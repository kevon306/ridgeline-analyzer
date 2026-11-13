"""Printable top-5 summary and the back-test reliability line (display helpers; nothing recalculated)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from report import reliability_text, top5_html  # noqa: E402
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.pipeline import load_candidates, score_batch  # noqa: E402
from ridgeline_core.rules import NOTICE  # noqa: E402

P = load_params(ROOT / "assumptions.json")


def test_reliability_line_reports_both_verified_figures():
    text = reliability_text()
    assert "4,817 past repeat sales" in text and "69% of the 3,749 houses" in text and "1,068 houses" in text and "54% did" in text
    assert "not renovation costs" in text


def test_top5_summary_lists_the_ranked_five_with_notice():
    scored = score_batch(load_candidates(ROOT / "data" / "candidates_dev_fixture.csv"), ROOT / "data" / "gwinnett_extract.db", P)
    page = top5_html(scored, P, "test data", "2026-10-20")
    top = scored[scored["top5"]].sort_values("rank")
    assert len(top) == 5 and all(a in page for a in top["matched_address"])
    assert page.index(top["matched_address"].iloc[0]) < page.index(top["matched_address"].iloc[4])
    assert NOTICE in page and "not a guaranteed" in page and f"{len(scored)} properties screened" in page
    unranked = scored[scored["status"] != "ranked"]["address"]
    assert not any(f"<td>{a}</td>" in page for a in unranked)


def test_explanation_takes_every_rate_from_the_parameters():
    from report import ROI_FORMULA, how_calculated
    P2 = {**P, "selling_pct": 0.07, "holding_pct_per_month": 0.01, "holding_months": 4, "contingency": 0.2,
          "reno_per_sqft": {"under20": {"low": 10.0, "likely": 30.0, "high": 50.0}, "20plus": {"low": 12.0, "likely": 45.5, "high": 60.0}}}
    scored = score_batch(load_candidates(ROOT / "data" / "candidates_dev_fixture.csv"), ROOT / "data" / "gwinnett_extract.db", P2)
    top = scored[scored["rank"] == 1].iloc[0]
    text = how_calculated(P2, top)
    for needle in ("7% of the resale value", "1% of the asking price per month for 4 months", "(4% of the asking price in total)",
                   "$30.00 per sq ft", "$45.50 for 20 years", "plus 20% contingency", ROI_FORMULA):
        assert needle in text, needle
    assert "8%" not in text and "0.75%" not in text and "15%" not in text                   # nothing left over from the defaults
    money = lambda x: f"${x:,.0f}"
    assert f"7% × {money(top['arv'])} = {money(top['arv'] * 0.07)}" in text
    assert f"1% × {money(top['asking_price_used'])} × 4 months = {money(top['asking_price_used'] * 0.04)}" in text
    assert f"+ 20% contingency = {money(top['reno_likely_base'] * 1.2)}" in text
    net = top["arv"] * 0.93 - top["asking_price_used"] * 1.04 - top["reno_likely_base"] * 1.2
    assert f"= {money(net)}" in text and f"= {net / (top['asking_price_used'] + top['reno_likely_base'] * 1.2) * 100:.1f}%" in text
    assert f"based on {int(top['comp_count'])} nearby sales" in text


def test_explanation_with_the_approved_parameters():
    from report import how_calculated
    text = how_calculated(P)
    for needle in ("8% of the resale value", "0.75% of the asking price per month for 6 months", "plus 15% contingency",
                   "$23.60 per sq ft for homes under 20 years old", "$41.70 for 20 years or older",
                   "ROI = projected profit ÷ (asking price + renovation)"):
        assert needle in text, needle
    assert "Worked example" not in text                                                          # no row given, no example
