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
