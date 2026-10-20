"""Renovation budget, scenarios, risk flags, confidence, validation metrics and the verified reliability figures.
Every cost below is a hand-made TEST value, not a real price or a real project."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.pipeline import load_candidates, score_batch  # noqa: E402
from ridgeline_core.reliability import arv_reliability  # noqa: E402
from ridgeline_core.reno_validation import COLUMNS, metrics, time_split, usable_records, wilson  # noqa: E402
from ridgeline_core.renovation import (MIN_VALIDATION_RECORDS, confidence, deal_outcomes, model_scenarios,  # noqa: E402
                                       outcome_warning, risk_flags, scope_budget)
from ridgeline_core.rules import compute_roi, renovation_estimate  # noqa: E402

P = load_params(ROOT / "assumptions.json")


# ---------------------------------------------------------------- the 69% / 54% figures
def test_reliability_figures_are_recomputed_and_match_the_record():
    rl = arv_reliability()
    assert (rl["tested"], rl["valued"], rl["unvalued"], rl["hits"]) == (4817, 3749, 1068, 2595)
    assert round(rl["hit_rate_valued"] * 100) == 69 and round(rl["hit_rate_all"] * 100) == 54
    assert rl["verified"] and rl["discrepancy"] is None


def test_reliability_flags_a_summary_that_disagrees(tmp_path):
    s = json.loads((ROOT / "backtest_results" / "summary_full.json").read_text())
    s["all_in_hit_rate"] = 0.60
    (tmp_path / "s.json").write_text(json.dumps(s))
    rl = arv_reliability(summary_path=tmp_path / "s.json")
    assert not rl["verified"] and "all-in hit rate" in rl["discrepancy"] and round(rl["hit_rate_all"] * 100) == 54
    assert arv_reliability(pairs_path=tmp_path / "missing.csv") is None


# ---------------------------------------------------------------- model scenarios = the approved numbers
@pytest.mark.parametrize("year", [1965, 2015])
def test_model_scenarios_match_the_approved_estimate(year):
    m, r = model_scenarios(1800, year, 2026, P), renovation_estimate(1800, year, 2026, P)
    assert m["expected"]["base"] == r["likely_base"] and m["expected"]["total"] == pytest.approx(r["likely_with_contingency"])
    assert m["low"]["total"] == r["low"] and m["high"]["total"] == r["high"]
    assert m["low"]["contingency"] == m["high"]["contingency"] == 0                    # approved: contingency on likely only
    assert m["expected"]["contingency"] == pytest.approx(m["expected"]["total"] - m["expected"]["base"])


def test_expected_outcome_equals_the_charter_roi():
    m = model_scenarios(1600, 1990, 2026, P)
    out = {o["scenario"]: o for o in deal_outcomes(300_000, 420_000, m, P)}
    ref = compute_roi(300_000, 420_000, m["expected"]["total"], P)
    assert out["expected"]["net_profit"] == pytest.approx(ref["net_profit"]) and out["expected"]["roi_pct"] == pytest.approx(ref["roi_pct"])
    assert out["low"]["net_profit"] > out["expected"]["net_profit"] > out["high"]["net_profit"]


def test_closing_and_financing_reduce_profit_once():
    m = model_scenarios(1600, 1990, 2026, P)
    base = deal_outcomes(300_000, 420_000, m, P)[1]["net_profit"]
    withc = deal_outcomes(300_000, 420_000, m, P, closing=6_000, financing=9_000)[1]["net_profit"]
    assert base - withc == pytest.approx(15_000)


# ---------------------------------------------------------------- user scope: no double counting
def test_scope_counts_each_category_once_and_contingency_once():
    s = scope_budget([{"category": "Interior paint", "low": 4_000, "expected": 5_000, "high": 7_000, "source": "quote A"},
                      {"category": "Contractor overhead and general labor", "expected": 3_000, "source": "quote A"},
                      {"category": "Roof", "expected": None}], 0.10)                # blank row ignored
    assert s["expected"]["base"] == 8_000 and s["expected"]["contingency"] == pytest.approx(800)
    assert s["expected"]["total"] == pytest.approx(8_800)
    assert s["low"]["base"] == 7_000 and s["high"]["base"] == 10_000           # missing low/high fall back to expected
    assert [r["category"] for r in s["rows"]] == ["Interior paint", "Contractor overhead and general labor"]


def test_duplicate_category_and_bad_ranges_are_refused():
    with pytest.raises(ValueError, match="twice"):
        scope_budget([{"category": "Roof", "expected": 9_000}, {"category": "Roof", "expected": 9_000}], 0.1)
    with pytest.raises(ValueError, match="low <= expected <= high"):
        scope_budget([{"category": "HVAC", "low": 8_000, "expected": 6_000}], 0.1)
    with pytest.raises(ValueError, match="contingency"):
        scope_budget([{"category": "HVAC", "expected": 6_000}], 1.5)
    assert scope_budget([{"category": "HVAC", "expected": None}], 0.1) is None


def test_cosmetic_scope_is_narrow_and_major_scope_is_wide():
    cosmetic = scope_budget([{"category": "Interior paint", "low": 4_500, "expected": 5_000, "high": 5_500, "source": "q"},
                             {"category": "Flooring and subfloor", "low": 7_000, "expected": 8_000, "high": 9_000, "source": "q"}], 0.05)
    major = scope_budget([{"category": "Kitchen (cabinets, counters, appliances)", "low": 18_000, "expected": 28_000, "high": 45_000, "source": "q"},
                          {"category": "Bathrooms (fixtures, tile, plumbing)", "low": 9_000, "expected": 15_000, "high": 26_000, "source": "q"},
                          {"category": "Water damage and mold", "low": 0, "expected": 4_000, "high": 20_000, "source": "inspection"}], 0.15)
    width = lambda s: (s["high"]["total"] - s["low"]["total"]) / s["expected"]["total"]
    assert width(cosmetic) < 0.3 < width(major)


def test_scope_turning_a_deal_into_a_loss_is_flagged():
    big = scope_budget([{"category": "Structural and foundation", "low": 10_000, "expected": 30_000, "high": 90_000, "source": "engineer"}], 0.15)
    warn = outcome_warning(deal_outcomes(300_000, 420_000, big, P))
    assert warn == "Profitable at the expected renovation cost, but a loss if renovation comes in at the high figure."


# ---------------------------------------------------------------- risk flags and confidence
def test_age_and_condition_raise_flags_but_never_cost():
    old = risk_flags(1975, "poor for age", 2026, P)
    new = risk_flags(2018, "normal for age", 2026, P)
    items = lambda fl: {f["item"]: f["level"] for f in fl}
    assert items(old)["Roof, HVAC, water heater"] == "elevated" and "Electrical and plumbing" in items(old)
    assert items(old)["Overall condition"] == "elevated"
    assert items(new)["Roof, HVAC, water heater"] == "check" and "Electrical and plumbing" not in items(new)
    assert all("cost" not in f for f in old + new)                    # flags carry no dollar amount
    assert items(risk_flags(None, None, 2026, P))["Water damage, structure"] == "unknown"


def test_confidence_labels_follow_the_evidence():
    sourced = scope_budget([{"category": "Roof", "expected": 9_000, "source": "quote"}], 0.1)
    unsourced = scope_budget([{"category": "Roof", "expected": 9_000}], 0.1)
    enough = {"n": MIN_VALIDATION_RECORDS}
    assert confidence(False, None, None)["label"] == "Insufficient data"
    assert confidence(True, None, {"n": 0})["label"] == "Low"
    assert confidence(True, unsourced, {"n": 0})["label"] == "Low"
    assert confidence(True, sourced, {"n": 0})["label"] == "Moderate"
    assert confidence(True, sourced, enough, inspected=False)["label"] == "Moderate"
    assert confidence(True, sourced, enough, inspected=True)["label"] == "High"


# ---------------------------------------------------------------- validation ledger and metrics
def ledger(rows):
    df = pd.DataFrame([{c: "" for c in COLUMNS} | r for r in rows], columns=COLUMNS)
    return df.astype(str)


def rec(i, expected, actual, low=None, high=None, evidence="invoice", est="2025-01-01", done="2025-06-01", pin=None):
    return {"record_id": str(i), "pin": pin or f"T{i}", "estimated_on": est, "actual_completed_on": done,
            "pred_expected": str(expected), "pred_low": "" if low is None else str(low), "pred_high": "" if high is None else str(high),
            "actual_cost": "" if actual is None else str(actual), "actual_evidence": evidence}


def test_empty_ledger_is_insufficient_data_not_a_score():
    m = metrics(ledger([]))
    assert m["n"] == 0 and m["status"].startswith("insufficient data")


def test_unverified_hindsight_and_duplicate_rows_are_excluded():
    df = ledger([rec(1, 50_000, 52_000),
                 rec(2, 50_000, 60_000, evidence="seller said"),                       # not documented
                 rec(3, 50_000, None),                                                  # quote only, no actual
                 rec(4, 50_000, 55_000, est="2025-07-01", done="2025-06-01"),           # finished before the estimate
                 rec(5, 50_000, 52_000, pin="T1")])                                     # same house, same estimate date
    use, excl = usable_records(df)
    assert use["record_id"].tolist() == ["1"]
    assert sorted(excl["why"]) == sorted(["actual cost not backed by an invoice or similar evidence", "no positive actual cost",
                                          "completed before the estimate was made (hindsight)", "duplicate of an earlier row"])


def test_metrics_by_hand():
    rows = [rec(i, 100_000, a, low=90_000, high=120_000) for i, a in
            enumerate([100_000, 110_000, 125_000, 80_000, 95_000, 100_000, 130_000, 104_000, 99_000, 500], start=1)]
    m = metrics(ledger(rows), min_n=10)
    errs = [0, 10_000, 25_000, 20_000, 5_000, 0, 30_000, 4_000, 1_000, 99_500]
    assert m["n"] == 10 and m["mae"] == pytest.approx(sum(errs) / 10)
    assert m["mape_excluded_small"] == 1                                       # the $500 actual is too small for a % error
    apes = [0, 10_000/110_000, 25_000/125_000, 20_000/80_000, 5_000/95_000, 0, 30_000/130_000, 4_000/104_000, 1_000/99_000]
    assert m["mape"] == pytest.approx(sum(apes) / 9)
    assert m["within_10"]["k"] == 6 and m["within_15"]["k"] == 6 and m["within_20"]["k"] == 7   # 25k/125k is exactly 20%
    assert m["range"]["coverage"] == pytest.approx(6 / 10) and m["range"]["median_width_pct_of_expected"] == pytest.approx(0.30)
    lo, hi = m["within_15"]["ci90"]
    assert lo < 6 / 9 < hi


def test_time_split_keeps_test_estimates_out_of_calibration():
    df = ledger([rec(1, 1, 1, est="2024-01-01", done="2024-06-01"), rec(2, 1, 1, est="2024-05-01", done="2025-03-01"),
                 rec(3, 1, 1, est="2025-02-01", done="2025-08-01")])
    cal, test = time_split(df, "2025-01-01")
    assert cal["record_id"].tolist() == ["1"] and test["record_id"].tolist() == ["3"]


def test_wilson_interval_is_wide_for_small_samples():
    lo, hi = wilson(3, 4)
    assert lo < 0.4 and hi > 0.9
    lo2, hi2 = wilson(300, 400)
    assert hi2 - lo2 < 0.1


# ---------------------------------------------------------------- full flow: candidate -> budget -> profit -> ROI
def test_full_flow_from_candidate_to_roi_uses_one_renovation_budget():
    scored = score_batch(load_candidates(ROOT / "data" / "candidates_dev_fixture.csv"), ROOT / "data" / "gwinnett_extract.db", P)
    ranked = scored[scored["status"] == "ranked"]
    assert len(ranked)
    for _, r in ranked.iterrows():
        sqft = r["reno_likely_base"] / P["reno_per_sqft"][r["reno_tier"]]["likely"]
        exp = {"expected": {"total": r["reno_likely_with_contingency"]}, "low": {"total": r["reno_low"]}, "high": {"total": r["reno_high"]}}
        out = {o["scenario"]: o for o in deal_outcomes(r["asking_price_used"], r["arv"], exp, P)}
        assert out["expected"]["net_profit"] == pytest.approx(r["net_profit"]) and out["expected"]["roi_pct"] == pytest.approx(r["roi_pct"])
        assert r["reno_likely_with_contingency"] == pytest.approx(r["reno_likely_base"] * (1 + P["contingency"]))   # contingency once
        assert sqft > 0
