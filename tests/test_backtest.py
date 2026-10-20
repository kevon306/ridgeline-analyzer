"""Back-test: pair rules, no look-ahead, and a reproducible seeded split (BACKTEST_PROTOCOL.md)."""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest  # noqa: E402
from backtest import SEED, qualifying_pairs, run, split_pairs, value_pair  # noqa: E402
from helpers import base_parcels, build_db  # noqa: E402
from ridgeline_core.arv import estimate_arv, load_county, load_params  # noqa: E402

P = load_params(ROOT / "assumptions.json")
DEV_DB = ROOT / "data" / "gwinnett_extract.db"


def pairs_of(rows, year_built=None):
    sales = pd.DataFrame(rows, columns=["lrsnum", "sale_date", "amount"])
    yb = pd.Series(year_built if year_built is not None else {l: 1990 for l in sales["lrsnum"]})
    return qualifying_pairs(sales, yb)


# ---------------------------------------------------------------- pair rules
@pytest.mark.parametrize("second_date, second_price, kept", [
    ("2024-01-30", 220_000, False),   # 29 days
    ("2024-01-31", 220_000, True),    # 30 days
    ("2025-07-02", 220_000, True),    # 548 days
    ("2025-07-03", 220_000, False),   # 549 days
    ("2024-06-01", 200_000, False),   # same price: not higher
    ("2024-06-01", 199_000, False),   # lower
    ("2024-06-01", 400_000, True),    # ratio exactly 2.0
    ("2024-06-01", 400_001, False),   # ratio just over 2.0
])
def test_days_price_and_ratio_rules(second_date, second_price, kept):
    p = pairs_of([(1, "2024-01-01", 200_000), (1, second_date, second_price)])
    assert (len(p) == 1) is kept


def test_first_price_floor():
    assert len(pairs_of([(1, "2024-01-01", 49_999), (1, "2024-06-01", 60_000)])) == 0
    assert len(pairs_of([(1, "2024-01-01", 50_000), (1, "2024-06-01", 60_000)])) == 1


def test_house_must_be_built_before_the_first_sale():
    rows = [(1, "2024-01-01", 200_000), (1, "2024-06-01", 250_000)]
    assert len(pairs_of(rows, {1: 2023})) == 1
    assert len(pairs_of(rows, {1: 2024})) == 0          # built the year of the sale: cannot show it predates it
    assert len(pairs_of(rows, {1: float("nan")})) == 0  # unknown year built


def test_only_consecutive_sales_pair_and_order_is_fixed():
    rows = [(2, "2024-01-01", 100_000), (1, "2024-01-01", 100_000), (1, "2024-03-01", 120_000),
            (1, "2024-06-01", 130_000)]
    p = pairs_of(rows)
    assert list(zip(p["lrsnum"], p["first_date"].dt.date.astype(str), p["second_date"].dt.date.astype(str))) == [
        (1, "2024-01-01", "2024-03-01"), (1, "2024-03-01", "2024-06-01")]   # 1 Jan -> 1 Jun skips a sale


def test_same_day_duplicates_collapse_and_conflicts_are_dropped():
    dup = [(1, "2024-01-01", 200_000), (1, "2024-01-01", 200_000), (1, "2024-06-01", 250_000)]
    assert len(pairs_of(dup)) == 1
    conflict = [(1, "2024-01-01", 200_000), (1, "2024-01-01", 210_000), (1, "2024-06-01", 250_000)]
    assert len(pairs_of(conflict)) == 0


# ---------------------------------------------------------------- no future sale leaks in
AS_OF = "2026-01-15"                                         # the subject's first sale
PAST = [(2, "2025-09-01", 340_000), (3, "2025-10-01", 378_000), (4, "2025-12-01", 330_000)]
FUTURE = [
    (1, "2026-07-01", 900_000),                              # the pair's own second sale
    (1, AS_OF, 999_000),                                     # a sale on the as-of day itself
    (2, AS_OF, 50_000_000),                                  # a comp-sized house on the as-of day
    (3, "2026-02-01", 50_000_000), (4, "2026-03-01", 10_000_000),
]


def test_future_sales_do_not_change_the_estimate(tmp_path):
    before = load_county(build_db(tmp_path / "past.db", base_parcels(), PAST))
    build_db(tmp_path / "all.db", base_parcels(), PAST + FUTURE)
    # wild background sales after as_of would move the index if it looked ahead
    con = sqlite3.connect(tmp_path / "all.db")
    con.executemany("INSERT INTO sales VALUES (900,?,?,1)",
                    [(d.date().isoformat(), 5_000_000) for d in pd.date_range(AS_OF, "2026-05-04")] * 10)
    con.executemany("INSERT INTO sales VALUES (900,?,?,1)", [(AS_OF, 5_000_000)] * 500)   # swamps a 1-day peek
    con.commit(); con.close()
    after = load_county(tmp_path / "all.db")

    a = estimate_arv(1, AS_OF, P, before)
    b = estimate_arv(1, AS_OF, P, after)
    assert a[3] == "ok" and a == b
    assert all(c["sale_date"] < AS_OF for c in b[1])


def test_backtest_values_each_pair_as_of_its_first_sale(tmp_path):
    db = build_db(tmp_path / "g.db", base_parcels(), PAST + [(1, AS_OF, 300_000), (1, "2026-07-01", 330_000)])
    data = load_county(db)
    pair = qualifying_pairs(backtest.load_sales(db), data.subjects["year_built"]).query("lrsnum == 1").iloc[0]
    row = value_pair(pair, P, data)
    expected = estimate_arv(1, AS_OF, P, load_county(build_db(tmp_path / "past.db", base_parcels(), PAST)))[0]
    assert row["as_of"] == AS_OF and row["status"] == "ok" and row["arv"] == expected


def test_a_comp_dated_on_or_after_as_of_stops_the_run(tmp_path, monkeypatch):
    data = load_county(build_db(tmp_path / "g.db", base_parcels(), PAST))
    leaky = {"sale_date": AS_OF, "lrsnum": 2}
    monkeypatch.setattr(backtest, "estimate_arv", lambda *a, **k: (300_000, [leaky], 12, "ok"))
    pair = pd.Series({"lrsnum": 1, "first_date": pd.Timestamp(AS_OF), "first_price": 280_000,
                      "second_date": pd.Timestamp("2026-07-01"), "second_price": 330_000})
    with pytest.raises(RuntimeError, match="on or after as-of"):
        value_pair(pair, P, data)


# ---------------------------------------------------------------- the seeded split
def synthetic_pairs(n=200):
    rows = [(l, "2024-01-01", 100_000 + l) for l in range(n)] + [(l, "2024-06-01", 120_000 + l) for l in range(n)]
    return pairs_of(rows)


def test_same_seed_gives_the_same_sample():
    pairs = synthetic_pairs()
    e1, h1 = split_pairs(pairs, SEED)
    e2, h2 = split_pairs(pairs, SEED)
    pd.testing.assert_frame_equal(h1, h2)
    pd.testing.assert_frame_equal(e1, e2)


def test_same_seed_same_sample_whatever_order_the_sales_arrive_in():
    rows = [(l, "2024-01-01", 100_000 + l) for l in range(200)] + [(l, "2024-06-01", 120_000 + l) for l in range(200)]
    shuffled = pd.DataFrame(rows).sample(frac=1, random_state=7).values.tolist()
    _, h1 = split_pairs(pairs_of(rows), SEED)
    _, h2 = split_pairs(pairs_of(shuffled), SEED)
    assert h1["lrsnum"].tolist() == h2["lrsnum"].tolist()


def test_halves_are_disjoint_cover_everything_and_depend_on_the_seed():
    pairs = synthetic_pairs(201)
    explore, held = split_pairs(pairs, SEED)
    assert len(explore) == 100 and len(held) == 101
    assert set(explore.index).isdisjoint(held.index) and set(explore.index) | set(held.index) == set(pairs.index)
    _, other = split_pairs(pairs, SEED + 1)
    assert set(other.index) != set(held.index)


def test_tiny_sample_on_dev_extract_is_reproducible_and_held_out_only():
    data = load_county(DEV_DB)
    r1, s1 = run(DEV_DB, P, sample=5, data=data)
    r2, _ = run(DEV_DB, P, sample=5, data=data)
    pd.testing.assert_frame_equal(r1, r2)
    pairs = qualifying_pairs(backtest.load_sales(DEV_DB), data.subjects["year_built"])
    explore, held = split_pairs(pairs, SEED)
    key = lambda df: set(zip(df["lrsnum"], pd.to_datetime(df["first_date"] if "first_date" in df else df["as_of"])))
    assert key(r1) <= key(held) and key(r1).isdisjoint(key(explore))
    assert s1["pairs_evaluated"] == len(r1) == 5 and s1["data_cutoff"] == "2026-04-29"
