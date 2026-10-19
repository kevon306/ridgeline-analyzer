import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import AS_OF, base_parcels, build_db, three_comps  # noqa: E402
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.pipeline import (APPROVED_REASONS, APPROVED_STATUSES, PROPOSED_REASONS, assign_ranks,  # noqa: E402
                                     load_candidates, normalize_street, score_batch, zip5)
from ridgeline_core.rules import NOTICE  # noqa: E402

P = load_params(Path(__file__).resolve().parents[1] / "assumptions.json")


def cands(rows):
    return pd.DataFrame([{"row_id": i, "address": a, "zip": z, "asking_price": p} for i, (a, z, p) in enumerate(rows, 1)])


@pytest.fixture
def db(tmp_path):
    return build_db(tmp_path / "t.db", base_parcels(), three_comps())


def run(db, rows):
    return score_batch(cands(rows), db, P, AS_OF).set_index("row_id")


def test_address_normalization_is_exact_not_fuzzy():
    assert normalize_street("100 Main Street") == "100 MAIN ST"
    assert normalize_street("2065 oak rd., Snellville, GA 30078") == "2065 OAK RD"
    assert normalize_street("100 MAIN STT") == "100 MAIN STT"          # a typo stays a typo
    assert zip5("30078-1234") == "30078" and zip5("", "5 X RD, Y, GA 30043") == "30043"


def test_ranked_row_matches_hand_calculation(db):
    r = run(db, [("100 Main St", "30078", 250_000)]).loc[1]
    assert r["status"] == "ranked" and r["reason"] == ""
    assert r["arv"] == 320_000 and r["comp_count"] == 3 and r["comp_window_months"] == 12
    assert r["spread"] == 70_000
    assert r["reno_tier"] == "20plus"                                   # built 1990
    assert r["reno_likely_with_contingency"] == pytest.approx(41.70 * 1600 * 1.15)
    assert r["holding"] == pytest.approx(250_000 * 0.0075 * 6)
    assert r["selling"] == pytest.approx(320_000 * 0.08)
    net = 320_000 - r["selling"] - 250_000 - r["reno_likely_with_contingency"] - r["holding"]
    assert r["net_profit"] == pytest.approx(net)
    assert r["roi_pct"] == pytest.approx(net / (250_000 + r["reno_likely_with_contingency"]) * 100)
    assert r["reno_notice"] == NOTICE and r["rank"] == 1 and bool(r["top5"])


def test_price_screen_boundary_exactly_35000_passes_one_dollar_less_fails(db):
    out = run(db, [("100 Main St", "30078", 285_000), ("100 Main St", "30078", 285_001)])
    assert out.loc[1, "status"] == "ranked" and out.loc[1, "spread"] == 35_000
    assert out.loc[2, "status"] == "eliminated" and out.loc[2, "reason"] == "below_min_spread"
    assert pd.isna(out.loc[2, "roi_pct"]) and pd.isna(out.loc[2, "reno_low"])      # not calculated for eliminated


def test_unmatched_and_ambiguous_addresses(tmp_path):
    parcels = base_parcels() + [(5, "7 TWIN LN", "30078", 1600, 1990, 100), (6, "7 TWIN LN", "30078", 1600, 1990, 100)]
    db = build_db(tmp_path / "a.db", parcels, three_comps())
    out = run(db, [("999 Nowhere Rd", "30078", 200_000), ("7 Twin Lane", "30078", 200_000), ("100 Main St", "30043", 200_000)])
    assert (out.loc[1, "status"], out.loc[1, "reason"]) == ("unmatched", "unmatched_address")
    assert (out.loc[2, "status"], out.loc[2, "reason"]) == ("unmatched", "ambiguous_address")
    assert (out.loc[3, "status"], out.loc[3, "reason"]) == ("unmatched", "unmatched_address")   # wrong ZIP is not a match


def test_invalid_asking_price_keeps_the_row(db):
    out = run(db, [("100 Main St", "30078", ""), ("100 Main St", "30078", "abc"), ("100 Main St", "30078", "-5")])
    assert list(out["reason"]) == ["invalid_input"] * 3 and len(out) == 3


def test_fewer_than_three_comps(tmp_path):
    db = build_db(tmp_path / "c.db", base_parcels(), three_comps()[:2])
    r = run(db, [("100 Main St", "30078", 250_000)]).loc[1]
    assert (r["status"], r["reason"]) == ("insufficient_comps", "fewer_than_3_comps") and pd.isna(r["arv"])


def test_missing_building_size_neighborhood_follow_the_precedence(tmp_path):
    parcels = base_parcels() + [(5, "5 NOBUILD CT", "30078", None, "NOBUILD", 100),
                                (6, "6 NOSIZE CT", "30078", None, 1990, 100),
                                (7, "7 NONBHD CT", "30078", 1600, 1990, None),
                                (8, "8 BOTH CT", "30078", None, "NOBUILD", None)]
    db = build_db(tmp_path / "m.db", parcels, three_comps())
    out = run(db, [("5 Nobuild Court", "30078", 200_000), ("6 Nosize Ct", "30078", 200_000),
                   ("7 Nonbhd Ct", "30078", 200_000), ("8 Both Ct", "30078", 200_000)])
    assert [out.loc[i, "reason"] for i in (1, 2, 3, 4)] == ["no_building_record", "no_size_on_record", "no_neighborhood", "no_building_record"]
    assert set(out["status"]) == {"insufficient_comps"}


def test_every_row_has_an_approved_status_and_an_explanation(tmp_path):
    parcels = base_parcels() + [(5, "7 TWIN LN", "30078", 1600, 1990, 100), (6, "7 TWIN LN", "30078", 1600, 1990, 100)]
    db = build_db(tmp_path / "e.db", parcels, three_comps())
    rows = [("100 Main St", "30078", 250_000), ("100 Main St", "30078", 300_000), ("999 Nowhere Rd", "30078", 1),
            ("7 Twin Ln", "30078", 1), ("100 Main St", "30078", "")]
    out = score_batch(cands(rows), db, P, AS_OF)
    assert len(out) == len(rows)                                       # nothing dropped
    assert set(out["status"]) <= set(APPROVED_STATUSES)
    assert set(out["reason"]) - {""} <= set(APPROVED_REASONS) | set(PROPOSED_REASONS)
    assert out["explanation"].str.len().gt(10).all()
    ranked = out[out["status"] == "ranked"]
    assert ranked["roi_pct"].notna().all() and ranked["reno_low"].notna().all()


def test_ranking_orders_by_roi_then_spread_then_address():
    df = pd.DataFrame({"status": ["ranked"] * 4 + ["eliminated"],
                       "roi_pct": [10.0, 12.0, 10.0, 10.0, None],
                       "spread": [50_000, 40_000, 60_000, 60_000, 1_000],
                       "matched_address": ["B ST", "A ST", "C ST", "A AVE", "Z ST"]})
    out = assign_ranks(df)
    assert list(out["rank"][:4]) == [4, 1, 3, 2]       # 12% first; among the 10%: spread 60k (A AVE before C ST), then 50k
    assert pd.isna(out.loc[4, "rank"]) and list(out["top5"]) == [True] * 4 + [False]


def test_negative_roi_still_ranks_last_not_dropped(tmp_path):
    parcels = [(1, "100 MAIN ST", "30078", 1600, 1990, 100), (2, "2 COMP RD", "30078", 1700, 1990, 100),
               (3, "3 COMP RD", "30078", 1800, 1990, 100), (4, "4 COMP RD", "30078", 1500, 1990, 100)]
    db = build_db(tmp_path / "n.db", parcels, three_comps())
    out = score_batch(cands([("100 Main St", "30078", 285_000), ("100 Main St", "30078", 250_000)]), db, P, AS_OF)
    r = out[out["status"] == "ranked"]
    assert len(r) == 2 and r["roi_pct"].iloc[0] > r["roi_pct"].iloc[1] and r["rank"].tolist() == [1, 2]


def test_candidate_file_needs_required_columns(tmp_path):
    f = tmp_path / "bad.csv"; f.write_text("address,price\nA,1\n")
    with pytest.raises(ValueError, match="zip"):
        load_candidates(f)
