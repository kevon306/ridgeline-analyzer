"""Scoring pipeline: candidate list in, one scored row per candidate out.

Order of operations for every row (the first rule that applies decides the status):
  1. input checks        invalid asking price                       -> unmatched / invalid_input *
  2. address join        no match / more than one match             -> unmatched / unmatched_address, ambiguous_address
  3. county record       no building record, no size                -> insufficient_comps / no_building_record, no_size_on_record
  4. ARV (arv.py)        no neighborhood, no index, < 3 comps       -> insufficient_comps / no_neighborhood, index_unavailable, fewer_than_3_comps
  5. price screen        ARV - asking < min_spread                  -> eliminated / below_min_spread
  6. otherwise           renovation + ROI calculated                -> ranked

* invalid_input is PROPOSED, not yet approved: without it a blank price would have to be dropped or mislabelled.
Every input row is kept; nothing is dropped and nothing is invented.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

from .arv import estimate_arv, load_county, load_params
from .rules import compute_roi, passes_price_screen, renovation_estimate

APPROVED_STATUSES = ("ranked", "eliminated", "insufficient_comps", "unmatched")
APPROVED_REASONS = ("below_min_spread", "fewer_than_3_comps", "no_size_on_record", "no_building_record",
                    "no_neighborhood", "index_unavailable", "unmatched_address", "ambiguous_address")
PROPOSED_REASONS = ("invalid_input",)
REQUIRED_COLUMNS = ("address", "zip", "asking_price")

SUFFIXES = {"ROAD": "RD", "DRIVE": "DR", "STREET": "ST", "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR",
            "TRAIL": "TRL", "BOULEVARD": "BLVD", "AVENUE": "AVE", "PLACE": "PL", "TERRACE": "TER",
            "HIGHWAY": "HWY", "PARKWAY": "PKWY"}

EXPLAIN = {
    "ranked": "Passed the price screen and has enough comparable sales; projected ROI calculated.",
    "below_min_spread": "Estimated resale value is less than ${min_spread:,.0f} above the asking price, so the property was set aside before renovation and ROI were calculated.",
    "fewer_than_3_comps": "Fewer than 3 comparable sales in the same neighborhood within 24 months, so no resale value was estimated.",
    "no_size_on_record": "The county record has no usable living area for this property.",
    "no_building_record": "The county record has no usable building information (year built) for this property.",
    "no_neighborhood": "The county record has no neighborhood code, so comparable sales cannot be selected.",
    "index_unavailable": "The market price index is not available for the date needed, so comparable sales cannot be adjusted.",
    "unmatched_address": "No county parcel matched this street address and ZIP exactly.",
    "ambiguous_address": "More than one county parcel matched this street address and ZIP, so none was chosen.",
    "invalid_input": "The row has no usable asking price, so it could not be evaluated. (Proposed reason code, not yet approved.)",
}
ARV_CODE_MAP = {  # what estimate_arv returns -> (status, reason)
    "no_neighborhood": ("insufficient_comps", "no_neighborhood"),
    "no_building_record": ("insufficient_comps", "no_building_record"),
    "no_size_on_record": ("insufficient_comps", "no_size_on_record"),
    "index_unavailable": ("insufficient_comps", "index_unavailable"),
    "insufficient_comps": ("insufficient_comps", "fewer_than_3_comps"),
    "unknown_parcel": ("unmatched", "unmatched_address"),
}
RESULT_COLUMNS = ["row_id", "status", "reason", "explanation", "rank", "top5", "lrsnum", "matched_address",
                  "asking_price_used", "arv", "comp_count", "comp_window_months", "spread",
                  "reno_tier", "reno_low", "reno_likely_base", "reno_likely_with_contingency", "reno_high",
                  "reno_notice", "holding", "selling", "net_profit", "roi_pct", "comps_json", "as_of"]


def normalize_street(raw) -> str:
    """Upper-case, strip punctuation, spell the street suffix the way the county does (ROAD -> RD).
    Anything after the first comma (city, state, zip) is ignored. This is NOT fuzzy matching."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    s = str(raw).split(",")[0].upper()
    s = re.sub(r"[.#]", " ", s)
    tokens = s.split()
    if tokens and tokens[-1] in SUFFIXES:
        tokens[-1] = SUFFIXES[tokens[-1]]
    return " ".join(tokens)


def zip5(raw_zip, raw_address=None) -> str:
    for text in (raw_zip, raw_address):
        if text is not None and not (isinstance(text, float) and pd.isna(text)):
            m = re.search(r"(?<!\d)(\d{5})(?!\d)", str(text).replace(".0", ""))
            if m:
                return m.group(1)
    return ""


def load_candidates(path) -> pd.DataFrame:
    """Read candidates.csv. File-level problems raise; row-level problems become row statuses."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"candidates file is missing required column(s): {', '.join(missing)}")
    df.insert(0, "row_id", range(1, len(df) + 1))
    return df


def load_parcel_index(db_path) -> dict:
    """key 'STREET|ZIP' -> list of parcel dicts. More than one entry means ambiguous."""
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql("SELECT p.lrsnum, p.address, p.zip, p.neighborhood, b.year_built, b.fin_sqft "
                         "FROM parcels p LEFT JOIN buildings b USING (lrsnum)", con)
    finally:
        con.close()
    index: dict = {}
    for r in df.to_dict("records"):
        index.setdefault(f"{normalize_street(r['address'])}|{zip5(r['zip'])}", []).append(r)
    return index


def _row(row_id, status, reason, params, **extra) -> dict:
    out = {c: None for c in RESULT_COLUMNS}
    out.update(row_id=row_id, status=status, reason=reason or "", rank=None, top5=False, **extra)
    out["explanation"] = EXPLAIN[reason or "ranked"].format(min_spread=params["min_spread"])
    return out


def _score_one(cand: dict, index: dict, data, params: dict, as_of: pd.Timestamp) -> dict:
    rid = cand["row_id"]
    base = {"as_of": as_of.date().isoformat()}
    # 1. input checks
    try:
        asking = float(str(cand["asking_price"]).replace("$", "").replace(",", "").strip())
        if not asking > 0:
            raise ValueError
    except ValueError:
        return _row(rid, "unmatched", "invalid_input", params, **base)
    # 2. address join (exact on normalized street + zip)
    key = f"{normalize_street(cand['address'])}|{zip5(cand.get('zip'), cand['address'])}"
    hits = index.get(key, []) if key.split("|")[0] else []
    if len(hits) == 0:
        return _row(rid, "unmatched", "unmatched_address", params, asking_price_used=asking, **base)
    if len(hits) > 1:
        return _row(rid, "unmatched", "ambiguous_address", params, asking_price_used=asking, **base)
    parcel = hits[0]
    common = dict(lrsnum=int(parcel["lrsnum"]), matched_address=parcel["address"], asking_price_used=asking, **base)
    # 3. county record checks, before ARV so the documented precedence holds
    yb, sqft = parcel["year_built"], parcel["fin_sqft"]
    if pd.isna(yb) or not (1700 <= yb <= as_of.year):
        return _row(rid, "insufficient_comps", "no_building_record", params, **common)
    if pd.isna(sqft) or not sqft > 0:
        return _row(rid, "insufficient_comps", "no_size_on_record", params, **common)
    # 4. ARV
    arv, comps, window, code = estimate_arv(common["lrsnum"], as_of, params, data)
    if code != "ok":
        status, reason = ARV_CODE_MAP[code]
        return _row(rid, status, reason, params, comp_window_months=window, **common)
    spread_ok, spread = passes_price_screen(arv, asking, params["min_spread"])
    common.update(arv=arv, comp_count=len(comps), comp_window_months=window, spread=spread,
                  comps_json=json.dumps(comps))
    # 5. price screen
    if not spread_ok:
        return _row(rid, "eliminated", "below_min_spread", params, **common)
    # 6. renovation + ROI
    reno = renovation_estimate(float(sqft), int(yb), as_of.year, params)
    fin = compute_roi(asking, arv, reno["likely_with_contingency"], params)
    return _row(rid, "ranked", "", params, reno_tier=reno["tier"], reno_low=reno["low"],
                reno_likely_base=reno["likely_base"], reno_likely_with_contingency=reno["likely_with_contingency"],
                reno_high=reno["high"], reno_notice=reno["notice"], holding=fin["holding"], selling=fin["selling"],
                net_profit=fin["net_profit"], roi_pct=fin["roi_pct"], **common)


def assign_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Rank 'ranked' rows by ROI (high to low); ties go to the larger price spread, then to the address A-Z."""
    df = df.copy()
    df["rank"] = pd.array([pd.NA] * len(df), dtype="Int64")
    df["top5"] = False
    r = df[df["status"] == "ranked"].copy()
    r["_addr"] = r["matched_address"].fillna("").astype(str)
    r = r.sort_values(["roi_pct", "spread", "_addr"], ascending=[False, False, True], kind="mergesort")
    df.loc[r.index, "rank"] = range(1, len(r) + 1)
    df.loc[r.index, "top5"] = [i <= 5 for i in range(1, len(r) + 1)]
    return df


def score_batch(candidates: pd.DataFrame, db_path, params: dict | None = None, as_of=None, data=None, index=None) -> pd.DataFrame:
    """Return the candidates plus result columns, ranked rows first. Row count in == row count out."""
    params = params or load_params(Path(__file__).resolve().parents[1] / "assumptions.json")
    data = data if data is not None else load_county(db_path)             # the app passes these in so they load only once
    index = index if index is not None else load_parcel_index(db_path)
    # as-of date: explicit argument, else the dated setting in assumptions.json (the day after the newest sale in the
    # FULL county files), else derived from the loaded sales (only sensible for the full county, not a small extract).
    if as_of is not None:
        as_of = pd.Timestamp(as_of)
    elif params.get("as_of"):
        as_of = pd.Timestamp(params["as_of"])
    else:
        as_of = data.sales["sale_date"].max() + pd.Timedelta(days=1)
    results = [_score_one(c, index, data, params, as_of) for c in candidates.to_dict("records")]
    res = pd.DataFrame(results, columns=RESULT_COLUMNS)
    out = candidates.merge(res, on="row_id", how="left")
    out = assign_ranks(out)
    order = {"ranked": 0, "eliminated": 1, "insufficient_comps": 2, "unmatched": 3}
    out["_o"] = out["status"].map(order)
    out["_r"] = out["rank"].astype("float").fillna(1e9)
    out = out.sort_values(["_o", "_r", "row_id"], kind="mergesort").drop(columns=["_o", "_r"]).reset_index(drop=True)
    assert len(out) == len(candidates), "every input row must leave the pipeline"
    return out
