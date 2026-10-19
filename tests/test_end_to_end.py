"""End to end on the 999-parcel development extract and the labelled development candidate file."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.pipeline import APPROVED_REASONS, APPROVED_STATUSES, PROPOSED_REASONS, load_candidates, score_batch  # noqa: E402
from run_scoring import main  # noqa: E402

DB = ROOT / "data" / "gwinnett_extract.db"
FIXTURE = ROOT / "data" / "candidates_dev_fixture.csv"
P = load_params(ROOT / "assumptions.json")


def test_2065_oak_rd_regression_at_the_configured_and_the_next_day():
    c = pd.DataFrame([{"row_id": 1, "address": "2065 Oak Rd", "zip": "30078", "asking_price": "325000"}])
    a = score_batch(c, DB, P).iloc[0]                       # configured as-of: 2026-05-05, the day after the newest county sale (4 May)
    b = score_batch(c, DB, P, "2026-05-06").iloc[0]         # one day later, to show the date matters by a few dollars
    assert P["as_of"] == "2026-05-05"
    assert (a["arv"], a["comp_count"], a["comp_window_months"]) == (323_216, 3, 24)
    assert (b["arv"], b["comp_count"], b["comp_window_months"]) == (323_265, 3, 24)
    assert a["status"] == b["status"] == "eliminated" and a["reason"] == "below_min_spread"


def test_full_run_on_the_dev_fixture_keeps_every_row_and_explains_it(tmp_path):
    out = tmp_path / "scored.csv"
    scored = main(["--candidates", str(FIXTURE), "--out", str(out)])
    n_in = len(load_candidates(FIXTURE))
    assert n_in == 20 and len(scored) == 20 and len(pd.read_csv(out)) == 20
    assert set(scored["status"]) <= set(APPROVED_STATUSES)
    assert set(scored["reason"].fillna("")) - {""} <= set(APPROVED_REASONS) | set(PROPOSED_REASONS)
    assert scored["explanation"].str.len().gt(10).all()
    ranked = scored[scored["status"] == "ranked"]
    assert len(ranked) >= 5
    assert ranked["rank"].astype(int).tolist() == list(range(1, len(ranked) + 1))      # 1..n, no gaps
    assert ranked["roi_pct"].is_monotonic_decreasing
    assert ranked["top5"].sum() == 5
    assert ranked[["reno_low", "reno_likely_with_contingency", "reno_high", "roi_pct", "arv"]].notna().all().all()
    assert (ranked["reno_notice"] == "Pre-walkthrough screening estimate, not a construction estimate.").all()
    others = scored[scored["status"] != "ranked"]
    assert others["roi_pct"].isna().all()                                              # no invented ROI


def test_same_input_gives_identical_output(tmp_path):
    a = main(["--candidates", str(FIXTURE), "--out", str(tmp_path / "a.csv")])
    b = main(["--candidates", str(FIXTURE), "--out", str(tmp_path / "b.csv")])
    pd.testing.assert_frame_equal(a, b)
