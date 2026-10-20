"""Renovation evidence database (SQLite): property facts, inspections, estimates, cost records, price benchmarks.

Rules the schema enforces or the functions check:
  * Estimated, quoted and actual costs live apart: estimates/estimate_lines hold predictions; cost_records hold quotes,
    invoices and final costs, each with its own source, date and verification status.
  * Only cost_records with kind = 'final' AND verification = 'verified' count as actual completed-project outcomes.
  * Every fact, cost and benchmark carries a source and a date. Unknown component conditions stay 'unknown'.
  * Nothing here recalibrates the model. Feedback and bias reports only describe; they refuse to summarise groups that
    are too small.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "renovation_evidence.db"
SCHEMA_VERSION = 1
SMALL_COST = 1_000          # no percentage error below this actual cost (it blows up near zero)

CONDITIONS = ("good", "minor_repair", "major_repair", "replace", "unknown")
CONDITION_LABEL = {"good": "Good", "minor_repair": "Minor repair needed", "major_repair": "Major repair needed",
                   "replace": "Replacement needed", "unknown": "Unknown / not inspected"}
COMPONENTS = ("Roof", "HVAC", "Water heater", "Kitchen", "Bathrooms", "Flooring", "Plumbing", "Electrical",
              "Foundation and structure", "Windows and exterior doors", "Siding and exterior", "Interior walls and paint",
              "Moisture and mold")
FACT_FIELDS = ("living_area", "year_built", "bedrooms", "bathrooms", "property_type")
COST_KINDS = ("quote", "invoice", "final")
BENCHMARK_LEVELS = ("published_national", "published_regional", "local_quote", "verified_local")
COST_PARTS = ("labor", "materials", "installation", "demolition", "disposal", "permits", "overhead")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS properties (
  property_id INTEGER PRIMARY KEY, pin TEXT UNIQUE, address TEXT, zip TEXT, created_on TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS property_facts (
  fact_id INTEGER PRIMARY KEY, property_id INTEGER NOT NULL REFERENCES properties,
  field TEXT NOT NULL CHECK (field IN ('living_area','year_built','bedrooms','bathrooms','property_type')),
  value TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('verified','unverified')),
  source TEXT NOT NULL CHECK (length(trim(source)) > 0), recorded_on TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inspections (
  inspection_id INTEGER PRIMARY KEY, property_id INTEGER NOT NULL REFERENCES properties,
  inspected_on TEXT NOT NULL, inspector TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS component_findings (
  finding_id INTEGER PRIMARY KEY, inspection_id INTEGER NOT NULL REFERENCES inspections,
  component TEXT NOT NULL, condition TEXT NOT NULL CHECK (condition IN ('good','minor_repair','major_repair','replace','unknown')),
  verified_age_years REAL CHECK (verified_age_years IS NULL OR verified_age_years >= 0), age_source TEXT,
  notes TEXT, evidence_ref TEXT,
  CHECK (verified_age_years IS NULL OR length(trim(coalesce(age_source,''))) > 0));
CREATE TABLE IF NOT EXISTS estimates (
  estimate_id INTEGER PRIMARY KEY, property_id INTEGER NOT NULL REFERENCES properties, created_on TEXT NOT NULL,
  model_version TEXT NOT NULL, basis TEXT NOT NULL CHECK (basis IN ('model','scope')),
  contingency_pct REAL NOT NULL, low_total REAL NOT NULL, expected_total REAL NOT NULL, high_total REAL NOT NULL,
  confidence TEXT, notes TEXT, CHECK (low_total <= expected_total AND expected_total <= high_total));
CREATE TABLE IF NOT EXISTS estimate_lines (
  line_id INTEGER PRIMARY KEY, estimate_id INTEGER NOT NULL REFERENCES estimates, category TEXT NOT NULL,
  low REAL, expected REAL NOT NULL, high REAL, materials REAL, labor REAL, overhead REAL, source TEXT,
  UNIQUE (estimate_id, category));
CREATE TABLE IF NOT EXISTS cost_records (
  record_id INTEGER PRIMARY KEY, property_id INTEGER NOT NULL REFERENCES properties, category TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('quote','invoice','final')), amount REAL NOT NULL CHECK (amount >= 0),
  materials REAL, labor REAL, overhead REAL, source TEXT NOT NULL CHECK (length(trim(source)) > 0), evidence_ref TEXT,
  verification TEXT NOT NULL CHECK (verification IN ('unverified','verified')), record_date TEXT NOT NULL, notes TEXT);
CREATE TABLE IF NOT EXISTS price_benchmarks (
  benchmark_id INTEGER PRIMARY KEY, item TEXT NOT NULL, category TEXT NOT NULL, unit TEXT NOT NULL,
  low REAL, typical REAL NOT NULL, high REAL, includes TEXT NOT NULL, geography TEXT NOT NULL, price_date TEXT NOT NULL,
  source TEXT NOT NULL, source_ref TEXT NOT NULL, license_note TEXT NOT NULL, geo_adjustment TEXT,
  evidence_level TEXT NOT NULL CHECK (evidence_level IN ('published_national','published_regional','local_quote','verified_local')),
  CHECK ((low IS NULL OR low <= typical) AND (high IS NULL OR typical <= high)));
"""


# ---------------------------------------------------------------- connection and writes
def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    if con.execute("SELECT count(*) FROM schema_meta").fetchone()[0] == 0:
        con.execute("INSERT INTO schema_meta VALUES (?)", (SCHEMA_VERSION,))
    con.commit()
    return con


def find_property(con, pin: str) -> int | None:
    row = con.execute("SELECT property_id FROM properties WHERE pin = ?", (pin,)).fetchone()
    return row[0] if row else None


def property_id(con, pin: str, address: str = "", zip_code: str = "") -> int:
    """The property's id, creating it on first use. Properties are keyed by county parcel number."""
    row = con.execute("SELECT property_id FROM properties WHERE pin = ?", (pin,)).fetchone()
    if row:
        return row[0]
    cur = con.execute("INSERT INTO properties (pin, address, zip, created_on) VALUES (?,?,?,?)",
                      (pin, address, zip_code, date.today().isoformat()))
    con.commit()
    return cur.lastrowid


def add_fact(con, pid: int, field: str, value, status: str, source: str, recorded_on: str | None = None):
    con.execute("INSERT INTO property_facts (property_id, field, value, status, source, recorded_on) VALUES (?,?,?,?,?,?)",
                (pid, field, str(value), status, source, recorded_on or date.today().isoformat()))
    con.commit()


def add_inspection(con, pid: int, inspected_on: str, findings: list[dict], inspector: str = "", notes: str = "") -> int:
    """findings: {"component", "condition", "verified_age_years", "age_source", "notes", "evidence_ref"}.
    A missing or blank condition is stored as 'unknown', never guessed."""
    cur = con.execute("INSERT INTO inspections (property_id, inspected_on, inspector, notes) VALUES (?,?,?,?)",
                      (pid, inspected_on, inspector, notes))
    iid = cur.lastrowid
    for f in findings:
        cond = (f.get("condition") or "unknown").strip() or "unknown"
        age = _num(f.get("verified_age_years"))
        con.execute("INSERT INTO component_findings (inspection_id, component, condition, verified_age_years, age_source, notes, evidence_ref) "
                    "VALUES (?,?,?,?,?,?,?)", (iid, f["component"], cond, age, f.get("age_source") or None,
                                               f.get("notes") or "", f.get("evidence_ref") or ""))
    con.commit()
    return iid


def add_estimate(con, pid: int, basis: str, budget: dict, model_version: str, created_on: str | None = None,
                 confidence: str = "", notes: str = "") -> int:
    """budget: the output of renovation.scope_budget or a model_scenarios dict (low/expected/high totals, optional rows)."""
    cur = con.execute(
        "INSERT INTO estimates (property_id, created_on, model_version, basis, contingency_pct, low_total, expected_total, high_total, confidence, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, created_on or date.today().isoformat(), model_version, basis, budget["expected"].get("contingency_pct", 0.0),
         budget["low"]["total"], budget["expected"]["total"], budget["high"]["total"], confidence, notes))
    eid = cur.lastrowid
    for r in budget.get("rows", []):
        con.execute("INSERT INTO estimate_lines (estimate_id, category, low, expected, high, materials, labor, overhead, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (eid, r["category"], r["low"], r["expected"], r["high"],
                                                  r.get("materials"), r.get("labor"), r.get("overhead"), r.get("source", "")))
    con.commit()
    return eid


def add_cost_record(con, pid: int, category: str, kind: str, amount: float, source: str, verification: str,
                    record_date: str, materials=None, labor=None, overhead=None, evidence_ref: str = "", notes: str = "") -> int:
    """A quote, invoice or final cost. Parts (materials, labor, overhead), when given, must add up to the amount."""
    parts = [p for p in (_num(materials), _num(labor), _num(overhead)) if p is not None]
    if parts and abs(sum(parts) - float(amount)) > 1:
        raise ValueError(f"{category}: materials + labor + overhead = {sum(parts):,.0f}, but the amount is {float(amount):,.0f}")
    if verification == "verified" and not (evidence_ref or "").strip():
        raise ValueError(f"{category}: a verified cost needs an evidence reference (invoice number, file name, closing statement)")
    cur = con.execute("INSERT INTO cost_records (property_id, category, kind, amount, materials, labor, overhead, source, evidence_ref, verification, record_date, notes) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (pid, category, kind, float(amount), _num(materials), _num(labor),
                                                           _num(overhead), source, evidence_ref, verification, record_date, notes))
    con.commit()
    return cur.lastrowid


def add_benchmark(con, **b) -> int:
    """Item-level price benchmark. Refused unless it names its source, a reference (edition, URL, page), the license
    terms, the geography and the price date; 'verified_local' also requires an evidence-backed local source."""
    for req in ("item", "category", "unit", "typical", "includes", "geography", "price_date", "source", "source_ref", "license_note", "evidence_level"):
        if b.get(req) in (None, ""):
            raise ValueError(f"benchmark needs '{req}'")
    bad = set(str(b["includes"]).split(",")) - set(COST_PARTS)
    if bad:
        raise ValueError(f"unknown cost part(s) in 'includes': {', '.join(sorted(bad))}")
    cols = ("item", "category", "unit", "low", "typical", "high", "includes", "geography", "price_date", "source",
            "source_ref", "license_note", "geo_adjustment", "evidence_level")
    cur = con.execute(f"INSERT INTO price_benchmarks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      tuple(b.get(c) for c in cols))
    con.commit()
    return cur.lastrowid


# ---------------------------------------------------------------- reads
def latest_findings(con, pid: int) -> dict:
    """component -> most recent finding for this property (components never inspected are absent, i.e. unknown)."""
    rows = con.execute("""SELECT f.component, f.condition, f.verified_age_years, f.age_source, f.notes, f.evidence_ref, i.inspected_on
                          FROM component_findings f JOIN inspections i USING (inspection_id)
                          WHERE i.property_id = ? ORDER BY i.inspected_on, f.finding_id""", (pid,)).fetchall()
    keys = ("component", "condition", "verified_age_years", "age_source", "notes", "evidence_ref", "inspected_on")
    return {r[0]: dict(zip(keys, r)) for r in rows}


def facts_for(con, pid: int) -> dict:
    """field -> {"value", "status", "source", "conflict"}: newest verified value wins; two different verified values
    are a conflict; an unverified value is used only when nothing is verified."""
    out = {}
    for field in FACT_FIELDS:
        rows = con.execute("SELECT value, status, source, recorded_on FROM property_facts WHERE property_id = ? AND field = ? "
                           "ORDER BY recorded_on, fact_id", (pid, field)).fetchall()
        if not rows:
            continue
        verified = [r for r in rows if r[1] == "verified"]
        pick = (verified or rows)[-1]
        conflict = len({r[0] for r in verified}) > 1
        out[field] = {"value": pick[0], "status": pick[1], "source": pick[2], "recorded_on": pick[3], "conflict": conflict}
    return out


# ---------------------------------------------------------------- completeness
# Ordered by how much each item can move a renovation budget. This is a suggested collection order, not a measured ranking.
COMPLETENESS_ITEMS = [
    ("Foundation and structure", "high"), ("Moisture and mold", "high"), ("Roof", "high"), ("HVAC", "high"),
    ("Kitchen", "high"), ("Bathrooms", "high"), ("Electrical", "medium"), ("Plumbing", "medium"),
    ("Windows and exterior doors", "medium"), ("Flooring", "medium"), ("Siding and exterior", "medium"),
    ("Water heater", "low"), ("Interior walls and paint", "low"),
]


def completeness(county: dict, facts: dict, findings: dict) -> pd.DataFrame:
    """One row per item: status (verified / county record / unverified / conflicting / missing / unknown), source,
    effect on the renovation estimate and a collection priority (1 = collect first)."""
    rows = []
    for field, label, impact in (("living_area", "Living area", "high"), ("year_built", "Year built", "medium"),
                                 ("bedrooms", "Bedroom count", "medium"), ("bathrooms", "Bathroom count", "high"),
                                 ("property_type", "Property type", "medium")):
        f = facts.get(field)
        county_val = {"living_area": county.get("fin_sqft"), "year_built": county.get("year_built"),
                      "property_type": "single-family (county class 101)" if county else None}.get(field)
        if f and f["conflict"]:
            rows.append((label, "conflicting", f"{f['value']} (latest verified)", f["source"], impact))
        elif f and f["status"] == "verified":
            rows.append((label, "verified", f["value"], f["source"], impact))
        elif f:
            rows.append((label, "unverified", f["value"], f["source"], impact))
        elif county_val not in (None, "") and not (isinstance(county_val, float) and math.isnan(county_val)):
            rows.append((label, "county record", county_val, "Gwinnett County assessor, 2026 snapshot (not checked on site)", impact))
        else:
            rows.append((label, "missing", "", "", impact))
    for comp, impact in COMPLETENESS_ITEMS:
        f = findings.get(comp)
        if not f or f["condition"] == "unknown":
            rows.append((comp, "unknown", "not inspected", "", impact))
        else:
            age = f" · age {f['verified_age_years']:g} yrs ({f['age_source']})" if f["verified_age_years"] is not None else ""
            rows.append((comp, "inspected", CONDITION_LABEL[f["condition"]] + age, f"inspection {f['inspected_on']}"
                         + (f" · {f['evidence_ref']}" if f["evidence_ref"] else ""), impact))
    df = pd.DataFrame(rows, columns=["item", "status", "value", "source", "impact"])
    open_ = df["status"].isin(["missing", "unknown", "conflicting", "unverified"])
    order = {"high": 0, "medium": 1, "low": 2}
    df["priority"] = None
    pri = df[open_].assign(_o=df["impact"].map(order)).sort_values("_o", kind="mergesort").index
    df.loc[pri, "priority"] = range(1, len(pri) + 1)
    return df


# ---------------------------------------------------------------- feedback: estimate vs verified final cost
def actual_costs(con, pid: int) -> pd.DataFrame:
    """Verified final costs by category (the only costs that count as actual outcomes)."""
    return pd.read_sql("SELECT category, sum(amount) AS actual, max(record_date) AS last_date, count(*) AS records "
                       "FROM cost_records WHERE property_id = ? AND kind = 'final' AND verification = 'verified' "
                       "GROUP BY category", con, params=(pid,))


def baseline_estimate(con, pid: int) -> dict | None:
    """The estimate to judge: the newest one made BEFORE the first verified final cost (later ones could use hindsight)."""
    first = con.execute("SELECT min(record_date) FROM cost_records WHERE property_id = ? AND kind = 'final' AND verification = 'verified'",
                        (pid,)).fetchone()[0]
    q = "SELECT * FROM estimates WHERE property_id = ?" + (" AND created_on < ?" if first else "") + " ORDER BY created_on DESC, estimate_id DESC LIMIT 1"
    row = con.execute(q, (pid, first) if first else (pid,)).fetchone()
    if not row:
        return None
    cols = [d[0] for d in con.execute("SELECT * FROM estimates LIMIT 0").description]
    est = dict(zip(cols, row))
    est["lines"] = pd.read_sql("SELECT category, low, expected, high FROM estimate_lines WHERE estimate_id = ?", con, params=(est["estimate_id"],))
    return est


def compare(low, expected, high, actual) -> dict:
    err = expected - actual
    return {"low": low, "expected": expected, "high": high, "actual": actual, "error": err, "abs_error": abs(err),
            "pct_error": err / actual if actual >= SMALL_COST else None,
            "in_range": bool(low <= actual <= high) if low is not None and high is not None else None}


def feedback(con, pid: int) -> dict:
    """Total and per-category comparison of the baseline estimate with verified final costs. Category figures are the
    base cost (contingency is a project-level reserve and is not spread over categories); the total includes it."""
    est, act = baseline_estimate(con, pid), actual_costs(con, pid)
    if act.empty:
        return {"status": "no verified final costs yet"}
    if est is None:
        return {"status": "no estimate was recorded before the final costs, so there is nothing fair to compare"}
    total = compare(est["low_total"], est["expected_total"], est["high_total"], float(act["actual"].sum()))
    rows = []
    lines = est["lines"].set_index("category") if len(est["lines"]) else pd.DataFrame(columns=["low", "expected", "high"])
    for cat in sorted(set(lines.index) | set(act["category"])):
        a = act.loc[act["category"] == cat, "actual"]
        if cat in lines.index and len(a):
            l = lines.loc[cat]
            rows.append({"category": cat, "status": "compared", **compare(l["low"], l["expected"], l["high"], float(a.iloc[0]))})
        elif cat in lines.index:
            rows.append({"category": cat, "status": "estimated, no final cost recorded", "expected": lines.loc[cat, "expected"]})
        else:
            rows.append({"category": cat, "status": "not in the estimate (missed scope)", "actual": float(a.iloc[0])})
    return {"status": "ok", "estimate_id": est["estimate_id"], "basis": est["basis"], "estimated_on": est["created_on"],
            "total": total, "categories": pd.DataFrame(rows)}


def bias_report(con, min_n: int = 10) -> pd.DataFrame:
    """Recurring under- or overestimates by category across properties, from verified final costs only.
    Categories with fewer than min_n comparisons show 'insufficient data'. Nothing is recalibrated from this."""
    rows = []
    for (pid,) in con.execute("SELECT DISTINCT property_id FROM cost_records WHERE kind = 'final' AND verification = 'verified'"):
        fb = feedback(con, pid)
        if fb["status"] == "ok":
            rows.append({"category": "TOTAL", "error": fb["total"]["error"], "actual": fb["total"]["actual"]})
            c = fb["categories"]
            for _, r in c[c["status"] == "compared"].iterrows():
                rows.append({"category": r["category"], "error": r["error"], "actual": r["actual"]})
    if not rows:
        return pd.DataFrame(columns=["category", "n", "median_error", "share_under", "status"])
    df = pd.DataFrame(rows)
    out = []
    for cat, g in df.groupby("category"):
        enough = len(g) >= min_n
        out.append({"category": cat, "n": len(g), "median_error": float(g["error"].median()) if enough else None,
                    "share_under": float((g["error"] < 0).mean()) if enough else None,
                    "status": "ok" if enough else f"insufficient data ({len(g)} of {min_n})"})
    return pd.DataFrame(out)


def project_records(con) -> pd.DataFrame:
    """Project-level rows (baseline estimate vs verified final total) in reno_validation's column layout."""
    from .reno_validation import COLUMNS
    rows = []
    for pid, pin, address in con.execute("SELECT property_id, pin, address FROM properties"):
        est, act = baseline_estimate(con, pid), actual_costs(con, pid)
        if est is None:
            continue
        r = {c: "" for c in COLUMNS}
        r.update(record_id=str(est["estimate_id"]), pin=pin or "", address=address or "", estimated_on=est["created_on"],
                 model_version=est["model_version"], pred_low=str(est["low_total"]), pred_expected=str(est["expected_total"]),
                 pred_high=str(est["high_total"]), confidence=est["confidence"] or "")
        if not act.empty:
            r.update(actual_cost=str(float(act["actual"].sum())), actual_completed_on=str(act["last_date"].max()),
                     actual_evidence="verified final costs")
        rows.append(r)
    return pd.DataFrame(rows, columns=COLUMNS)


# ---------------------------------------------------------------- repair-need rates (only with enough data)
def need_rates(con, min_n: int = 30, cutoff: str | None = None) -> dict:
    """Share of inspected components found to need major repair or replacement, by component and house-age band, from
    inspections before `cutoff`; checked on inspections on or after it (Brier score). Below min_n inspected cases per
    component it reports insufficient data and the app keeps showing flags instead of probabilities."""
    df = pd.read_sql("""SELECT f.component, f.condition, i.inspected_on, pf.value AS year_built
                        FROM component_findings f JOIN inspections i USING (inspection_id)
                        LEFT JOIN property_facts pf ON pf.property_id = i.property_id AND pf.field = 'year_built' AND pf.status = 'verified'
                        WHERE f.condition != 'unknown'""", con)
    out = {}
    for comp, g in df.groupby("component"):
        if len(g) < min_n:
            out[comp] = {"status": f"insufficient data ({len(g)} inspected, {min_n} needed)"}
            continue
        g = g.assign(need=g["condition"].isin(["major_repair", "replace"]).astype(float))
        if cutoff is None:
            out[comp] = {"status": "ok (not yet validated: give a cutoff date to test on later inspections)", "rate": float(g["need"].mean()), "n": len(g)}
            continue
        cal, test = g[g["inspected_on"] < cutoff], g[g["inspected_on"] >= cutoff]
        if len(cal) < min_n or test.empty:
            out[comp] = {"status": f"insufficient data for a time split ({len(cal)} before, {len(test)} after the cutoff)"}
            continue
        rate = float(cal["need"].mean())
        out[comp] = {"status": "ok", "rate": rate, "n": len(cal), "test_n": len(test),
                     "brier": float(((test["need"] - rate) ** 2).mean())}
    return out


# ---------------------------------------------------------------- export / import (Streamlit Cloud does not keep files)
TABLES = ("properties", "property_facts", "inspections", "component_findings", "estimates", "estimate_lines",
          "cost_records", "price_benchmarks")


def export_json(con) -> str:
    return json.dumps({"schema_version": SCHEMA_VERSION, "exported_on": date.today().isoformat(),
                       **{t: pd.read_sql(f"SELECT * FROM {t}", con).to_dict("records") for t in TABLES}}, default=str, indent=1)


def import_json(con, text: str) -> dict:
    """Replace this database's contents with an export. Constraints are re-checked on insert, so a tampered file fails."""
    data = json.loads(text)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"export is schema version {data.get('schema_version')}, this app expects {SCHEMA_VERSION}")
    with con:
        for t in reversed(TABLES):
            con.execute(f"DELETE FROM {t}")
        for t in TABLES:
            for row in data.get(t, []):
                con.execute(f"INSERT INTO {t} ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                            tuple(None if (isinstance(v, float) and math.isnan(v)) else v for v in row.values()))
    return {t: len(data.get(t, [])) for t in TABLES}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v
