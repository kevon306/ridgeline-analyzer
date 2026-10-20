"""Renovation evidence database: separation of estimated / quoted / actual costs, provenance rules, completeness,
estimate-vs-actual feedback, bias reporting, repair-need rates, benchmarks, backup. All amounts are TEST values."""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ridgeline_core import evidence as ev  # noqa: E402
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.reno_validation import metrics  # noqa: E402
from ridgeline_core.renovation import deal_outcomes, risk_flags, scope_budget  # noqa: E402

P = load_params(ROOT / "assumptions.json")


@pytest.fixture
def con(tmp_path):
    c = ev.connect(tmp_path / "e.db")
    yield c
    c.close()


def scope(rows, cont=0.10):
    return scope_budget(rows, cont)


# ---------------------------------------------------------------- category totals, parts, contingency
def test_parts_fill_in_expected_and_must_agree():
    s = scope([{"category": "Roof", "materials": 6_000, "labor": 4_000, "overhead": 1_000, "source": "quote"}])
    assert s["expected"]["base"] == 11_000 and s["expected"]["total"] == pytest.approx(12_100)
    with pytest.raises(ValueError, match="materials \\+ labor \\+ overhead"):
        scope([{"category": "Roof", "expected": 12_000, "materials": 6_000, "labor": 4_000}])


def test_overhead_inside_rows_and_as_its_own_row_is_refused():
    with pytest.raises(ValueError, match="counted twice"):
        scope([{"category": "Roof", "materials": 6_000, "labor": 4_000, "overhead": 1_000},
               {"category": "Contractor overhead and general labor", "expected": 3_000}])
    ok = scope([{"category": "Roof", "materials": 6_000, "labor": 4_000},
                {"category": "Contractor overhead and general labor", "expected": 3_000}])
    assert ok["expected"]["base"] == 13_000


def test_contingency_applies_once_per_scenario():
    s = scope([{"category": "Kitchen (cabinets, counters, appliances)", "low": 20_000, "expected": 25_000, "high": 35_000}], 0.2)
    assert [s[k]["contingency"] for k in ("low", "expected", "high")] == pytest.approx([4_000, 5_000, 7_000])
    assert s["expected"]["total"] == pytest.approx(s["expected"]["base"] + s["expected"]["contingency"])


# ---------------------------------------------------------------- provenance and separation
def test_cost_records_need_sources_parts_that_add_up_and_evidence_when_verified(con):
    pid = ev.property_id(con, "R1 001", "1 TEST RD", "30000")
    with pytest.raises(ValueError, match="amount is"):
        ev.add_cost_record(con, pid, "Roof", "final", 10_000, "invoice", "verified", "2026-01-01", materials=5_000, labor=4_000, evidence_ref="inv 1")
    with pytest.raises(ValueError, match="evidence reference"):
        ev.add_cost_record(con, pid, "Roof", "final", 10_000, "invoice", "verified", "2026-01-01")
    with pytest.raises(sqlite3.IntegrityError):
        ev.add_cost_record(con, pid, "Roof", "quote", 10_000, "  ", "unverified", "2026-01-01")
    with pytest.raises(sqlite3.IntegrityError):
        ev.add_cost_record(con, pid, "Roof", "estimate", 10_000, "x", "unverified", "2026-01-01")   # not a cost kind


def test_only_verified_final_costs_count_as_actual(con):
    pid = ev.property_id(con, "R1 001")
    ev.add_cost_record(con, pid, "Roof", "quote", 9_000, "ABC Roofing quote", "unverified", "2025-12-01")
    ev.add_cost_record(con, pid, "Roof", "invoice", 9_500, "ABC Roofing", "unverified", "2026-01-10")
    ev.add_cost_record(con, pid, "Roof", "final", 9_800, "ABC Roofing", "unverified", "2026-01-20")     # not verified
    assert ev.actual_costs(con, pid).empty
    ev.add_cost_record(con, pid, "Roof", "final", 9_800, "ABC Roofing", "verified", "2026-01-20", evidence_ref="inv 77")
    a = ev.actual_costs(con, pid)
    assert a["actual"].tolist() == [9_800]


def test_unknown_condition_stays_unknown_and_age_needs_a_source(con):
    pid = ev.property_id(con, "R1 001")
    ev.add_inspection(con, pid, "2026-02-01", [{"component": "Roof", "condition": ""}, {"component": "HVAC", "condition": "good",
                                                                                           "verified_age_years": 3, "age_source": "install label"}])
    f = ev.latest_findings(con, pid)
    assert f["Roof"]["condition"] == "unknown" and f["HVAC"]["verified_age_years"] == 3
    with pytest.raises(sqlite3.IntegrityError):
        ev.add_inspection(con, pid, "2026-02-02", [{"component": "Roof", "condition": "good", "verified_age_years": 5}])


def test_facts_prefer_verified_and_flag_conflicts(con):
    pid = ev.property_id(con, "R1 001")
    ev.add_fact(con, pid, "bedrooms", 3, "unverified", "listing", "2026-01-01")
    assert ev.facts_for(con, pid)["bedrooms"]["status"] == "unverified"
    ev.add_fact(con, pid, "bedrooms", 4, "verified", "walkthrough", "2026-01-05")
    f = ev.facts_for(con, pid)["bedrooms"]
    assert (f["value"], f["status"], f["conflict"]) == ("4", "verified", False)
    ev.add_fact(con, pid, "bedrooms", 3, "verified", "appraisal", "2026-01-09")
    assert ev.facts_for(con, pid)["bedrooms"]["conflict"] is True
    with pytest.raises(sqlite3.IntegrityError):
        ev.add_fact(con, pid, "garage_size", 2, "verified", "walkthrough")                     # not a tracked field


def test_completeness_marks_county_missing_unknown_and_priorities(con):
    pid = ev.property_id(con, "R1 001")
    ev.add_inspection(con, pid, "2026-02-01", [{"component": "Roof", "condition": "replace", "evidence_ref": "photo 4"}])
    comp = ev.completeness({"fin_sqft": 1600, "year_built": 1985}, ev.facts_for(con, pid), ev.latest_findings(con, pid)).set_index("item")
    assert comp.loc["Living area", "status"] == "county record" and comp.loc["Bathroom count", "status"] == "missing"
    assert comp.loc["Roof", "status"] == "inspected" and comp.loc["HVAC", "status"] == "unknown"
    assert pd.isna(comp.loc["Roof", "priority"]) and comp.loc["Bathroom count", "priority"] < comp.loc["Water heater", "priority"]


def test_inspection_findings_replace_age_guesses_in_flags():
    findings = {"Roof": {"condition": "good", "verified_age_years": 2, "age_source": "permit", "inspected_on": "2026-02-01"},
                "HVAC": {"condition": "replace", "verified_age_years": None, "inspected_on": "2026-02-01"}}
    fl = {f["item"]: f for f in risk_flags(1975, "normal for age", 2026, P, findings)}
    assert fl["Roof"]["level"] == "documented" and fl["HVAC"]["level"] == "elevated"
    assert fl["Water heater"]["level"] == "elevated"                                            # still not inspected
    assert all("cost" not in f for f in fl.values())


# ---------------------------------------------------------------- feedback: predicted vs actual
def record_project(con, pin, est_rows, finals, est_date="2025-11-01", final_date="2026-03-01", cont=0.10):
    pid = ev.property_id(con, pin)
    ev.add_estimate(con, pid, "scope", scope(est_rows, cont), "bands-v1+scope", created_on=est_date)
    for cat, amt in finals:
        ev.add_cost_record(con, pid, cat, "final", amt, "contractor", "verified", final_date, evidence_ref=f"inv-{pin}-{cat}")
    return pid


def test_feedback_by_category_and_total(con):
    pid = record_project(con, "R1 001",
                         [{"category": "Roof", "low": 8_000, "expected": 10_000, "high": 12_000},
                          {"category": "Interior paint", "low": 4_000, "expected": 5_000, "high": 6_000},
                          {"category": "HVAC", "expected": 7_000}],
                         [("Roof", 13_000), ("Interior paint", 4_500), ("Water damage and mold", 800)])
    fb = ev.feedback(con, pid)
    t = fb["total"]
    assert t["expected"] == pytest.approx(24_200) and t["actual"] == 18_300 and t["error"] == pytest.approx(5_900)
    assert t["pct_error"] == pytest.approx(5_900 / 18_300) and t["in_range"] is False     # 18,300 < low 20,900
    c = fb["categories"].set_index("category")
    assert c.loc["Roof", "error"] == -3_000 and c.loc["Roof", "in_range"] is False and c.loc["Roof", "pct_error"] == pytest.approx(-3_000 / 13_000)
    assert c.loc["Interior paint", "in_range"] is True
    assert c.loc["HVAC", "status"] == "estimated, no final cost recorded"
    assert c.loc["Water damage and mold", "status"] == "not in the estimate (missed scope)"


def test_percentage_error_is_skipped_for_tiny_actual_costs():
    assert ev.compare(500, 900, 1_200, 400)["pct_error"] is None
    assert ev.compare(500, 900, 1_200, 1_000)["pct_error"] == pytest.approx(-0.1)


def test_an_estimate_made_after_the_final_cost_is_not_used(con):
    pid = record_project(con, "R1 001", [{"category": "Roof", "expected": 10_000}], [("Roof", 13_000)])
    ev.add_estimate(con, pid, "scope", scope([{"category": "Roof", "expected": 13_000}], 0), "x", created_on="2026-04-01")
    assert ev.feedback(con, pid)["total"]["expected"] == pytest.approx(11_000)              # the pre-work estimate
    pid2 = ev.property_id(con, "R1 002")
    ev.add_cost_record(con, pid2, "Roof", "final", 9_000, "x", "verified", "2026-01-01", evidence_ref="inv")
    ev.add_estimate(con, pid2, "scope", scope([{"category": "Roof", "expected": 9_000}], 0), "x", created_on="2026-02-01")
    assert ev.feedback(con, pid2)["status"].startswith("no estimate was recorded before")


def test_bias_report_needs_enough_projects_and_only_describes(con):
    for i in range(9):
        record_project(con, f"R2 {i:03d}", [{"category": "Bathrooms (fixtures, tile, plumbing)", "expected": 10_000}],
                       [("Bathrooms (fixtures, tile, plumbing)", 13_000)])
    b = ev.bias_report(con, min_n=10).set_index("category")
    assert b.loc["Bathrooms (fixtures, tile, plumbing)", "status"] == "insufficient data (9 of 10)"
    record_project(con, "R2 999", [{"category": "Bathrooms (fixtures, tile, plumbing)", "expected": 10_000}],
                   [("Bathrooms (fixtures, tile, plumbing)", 12_000)])
    b = ev.bias_report(con, min_n=10).set_index("category")
    row = b.loc["Bathrooms (fixtures, tile, plumbing)"]
    assert row["status"] == "ok" and row["share_under"] == 1.0 and row["median_error"] == pytest.approx(-3_000)   # category base 10,000 vs 13,000


def test_project_records_feed_the_validation_metrics(con):
    for i, actual in enumerate([10_000, 12_000, 9_000, 15_000, 11_000, 10_500, 9_800, 30_000, 10_200, 11_500]):
        record_project(con, f"R3 {i:03d}", [{"category": "Roof", "low": 9_000, "expected": 10_000, "high": 12_000}],
                       [("Roof", actual)], cont=0.0)
    m = metrics(ev.project_records(con), min_n=10)
    assert m["n"] == 10 and m["status"] == "ok" and m["within_15"]["k"] == 7
    assert m["range"]["coverage"] == pytest.approx(0.8)


# ---------------------------------------------------------------- repair-need rates only with enough data
def test_need_rates_report_insufficient_data_then_validate_on_later_inspections(con):
    pid = ev.property_id(con, "R4 001")
    ev.add_inspection(con, pid, "2025-01-01", [{"component": "HVAC", "condition": "replace"}])
    assert ev.need_rates(con)["HVAC"]["status"].startswith("insufficient data")
    for i in range(40):
        p = ev.property_id(con, f"R4 {i + 100}")
        ev.add_inspection(con, p, f"2025-{(i % 12) + 1:02d}-15" if i < 30 else "2026-02-01",
                          [{"component": "HVAC", "condition": "replace" if i % 4 == 0 else "good"}])
    r = ev.need_rates(con, min_n=30, cutoff="2026-01-01")["HVAC"]
    assert r["status"] == "ok" and r["n"] == 31 and r["test_n"] == 10 and 0 < r["rate"] < 1 and r["brier"] >= 0


# ---------------------------------------------------------------- price benchmarks
def test_benchmarks_require_full_provenance(con):
    good = dict(item="Interior paint, walls", category="Interior paint", unit="sq ft of wall", low=1.0, typical=2.0, high=3.0,
                includes="labor,materials", geography="TEST region", price_date="2026-01-01", source="TEST source",
                source_ref="TEST edition p.1", license_note="TEST terms", evidence_level="published_regional")
    assert ev.add_benchmark(con, **good)
    with pytest.raises(ValueError, match="source_ref"):
        ev.add_benchmark(con, **{**good, "source_ref": ""})
    with pytest.raises(ValueError, match="unknown cost part"):
        ev.add_benchmark(con, **{**good, "includes": "labor,magic"})
    with pytest.raises(sqlite3.IntegrityError):
        ev.add_benchmark(con, **{**good, "low": 5.0})                                          # low above typical


# ---------------------------------------------------------------- backup and downstream ROI
def test_export_import_round_trip_and_version_check(con, tmp_path):
    pid = record_project(con, "R5 001", [{"category": "Roof", "expected": 10_000}], [("Roof", 11_000)])
    ev.add_fact(con, pid, "bathrooms", 2, "verified", "walkthrough")
    dump = ev.export_json(con)
    other = ev.connect(tmp_path / "other.db")
    counts = ev.import_json(other, dump)
    assert counts["cost_records"] == 1 and ev.feedback(other, ev.find_property(other, "R5 001"))["total"]["actual"] == 11_000
    with pytest.raises(ValueError, match="schema version"):
        ev.import_json(other, dump.replace('"schema_version": 1', '"schema_version": 99'))


def test_recorded_scope_flows_into_profit_and_roi():
    s = scope([{"category": "Roof", "low": 8_000, "expected": 10_000, "high": 14_000, "source": "quote"},
               {"category": "Interior paint", "materials": 1_500, "labor": 3_500, "source": "quote"}], 0.10)
    out = {o["scenario"]: o for o in deal_outcomes(250_000, 340_000, s, P, closing=5_000)}
    selling, holding = 340_000 * P["selling_pct"], 250_000 * P["holding_pct_per_month"] * P["holding_months"]
    exp_reno = (10_000 + 5_000) * 1.10
    assert out["expected"]["renovation"] == pytest.approx(exp_reno)
    assert out["expected"]["net_profit"] == pytest.approx(340_000 - selling - 250_000 - exp_reno - holding - 5_000)
    assert out["expected"]["roi_pct"] == pytest.approx(out["expected"]["net_profit"] / (250_000 + exp_reno) * 100)
