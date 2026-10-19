"""R-04 tests on a tiny synthetic gwinnett.db (contract 1 schema)."""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ridgeline_core.arv import estimate_arv, load_county  # noqa: E402

AS_OF = "2026-05-05"
SUBJ = 1  # 1,600 sq ft in neighborhood 100
BG_DAYS = [d.date().isoformat() for d in pd.date_range("2023-01-01", "2026-05-04", freq="D")]


def flat(i):                      # background market: $200/sq ft, never moves
    return 320_000


def rising(i):                    # background market: climbs about 0.0125% a day
    return 280_000 + 50 * i


def build_db(path, sales, extra_parcels=(), bg=flat, junk=(), price_index=None, no_building=()):
    """Subject 1 and comp houses 2-5 sit in neighborhood 100; house 6 (neighborhood 200) carries
    the daily background sales that make the county price index. price_index, if given, is a
    list of (day, ppsf) rows written to a price_index table."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE parcels (lrsnum INT PRIMARY KEY, pin TEXT, address TEXT, zip TEXT, neighborhood INT);
        CREATE TABLE buildings (lrsnum INT PRIMARY KEY, year_built INT, fin_sqft INT, cond_desc TEXT);
        CREATE TABLE sales (lrsnum INT, sale_date DATE, amount INT, slot INT);
    """)
    parcels = [(1, 1600, 100), (2, 1700, 100), (3, 1800, 100), (4, 1500, 100),
               (5, 3000, 100), (6, 1600, 200), *extra_parcels]
    for lrs, sqft, nb in parcels:
        con.execute("INSERT INTO parcels VALUES (?,?,?,?,?)", (lrs, f"P{lrs}", f"{lrs} TEST RD", "30078", nb))
        if lrs not in no_building:
            con.execute("INSERT INTO buildings VALUES (?,?,?,?)", (lrs, 1990, sqft, "NORMAL FOR AGE"))
    if bg is not None:
        for i, d in enumerate(BG_DAYS):
            con.execute("INSERT INTO sales VALUES (?,?,?,?)", (6, d, bg(i), 1))
    for d, amt in junk:
        con.execute("INSERT INTO sales VALUES (?,?,?,?)", (6, d, amt, 2))
    con.executemany("INSERT INTO sales VALUES (?,?,?,?)", sales)
    if price_index is not None:
        con.execute("CREATE TABLE price_index (day TEXT, ppsf REAL)")
        con.executemany("INSERT INTO price_index VALUES (?,?)", price_index)
    con.commit(); con.close()
    return load_county(path)


P = {}
THREE_COMPS = [
    (2, "2025-09-01", 340_000, 1),   # 200.00 / sq ft
    (3, "2025-12-01", 378_000, 1),   # 210.00
    (4, "2025-10-01", 330_000, 1),   # 220.00
]


# ---------------------------------------------------------------- the original five
def test_widens_once_to_24_months(tmp_path):
    data = build_db(tmp_path / "g.db", [
        (2, "2025-09-01", 340_000, 1),   # inside 12 mo
        (3, "2025-12-01", 360_000, 1),   # inside 12 mo
        (4, "2024-10-01", 300_000, 1),   # only inside 24 mo
    ])
    arv, comps, window, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "ok" and window == 24 and len(comps) == 3 and arv > 0


def test_insufficient_comps_has_no_arv(tmp_path):
    data = build_db(tmp_path / "g.db", [(2, "2025-09-01", 340_000, 1)])
    arv, comps, window, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "insufficient_comps" and arv is None and window == 24


def test_flipped_house_counts_once(tmp_path):
    data = build_db(tmp_path / "g.db", [
        (2, "2025-06-01", 250_000, 2), (2, "2025-11-01", 380_000, 1),  # same house twice
        (3, "2025-12-01", 360_000, 1), (4, "2025-10-01", 300_000, 1),
    ])
    _, comps, window, _ = estimate_arv(SUBJ, AS_OF, P, data)
    assert window == 12 and [c["amount"] for c in comps if c["lrsnum"] == 2] == [380_000]


def test_filters_size_floor_subject_and_lookahead(tmp_path):
    data = build_db(tmp_path / "g.db", [
        (5, "2025-09-01", 600_000, 1),   # 3,000 sq ft: outside 75-125%
        (4, "2025-09-01", 40_000, 1),    # under $50k floor
        (1, "2025-09-01", 330_000, 1),   # the subject itself
        (3, AS_OF, 360_000, 1),          # on as_of: not "before"
        (2, "2025-09-01", 340_000, 1),
    ])
    _, comps, _, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "insufficient_comps" and comps == []


def test_flat_market_arv_is_60th_percentile(tmp_path):
    data = build_db(tmp_path / "g.db", THREE_COMPS)
    arv, comps, _, _ = estimate_arv(SUBJ, AS_OF, P, data)
    assert all(c["adj_ppsf"] == pytest.approx(c["ppsf"]) for c in comps)
    assert arv == round(212.0 * 1600)    # 60th pct of 200/210/220 = 212


# ---------------------------------------------------------------- time adjustment
def test_rising_market_adjusts_comps_upward(tmp_path):
    flat_data = build_db(tmp_path / "flat.db", THREE_COMPS)
    data = build_db(tmp_path / "rise.db", THREE_COMPS, bg=rising)
    arv_flat, _, _, _ = estimate_arv(SUBJ, AS_OF, P, flat_data)
    arv, comps, _, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "ok" and arv > arv_flat
    now = data.index(pd.Timestamp(AS_OF), 90, 50_000, 30)
    for c in comps:
        then = data.index(pd.Timestamp(c["sale_date"]), 90, 50_000, 30)
        assert now > then and c["adj_ppsf"] > c["ppsf"]
        assert c["adj_ppsf"] == pytest.approx(c["ppsf"] * now / then, abs=0.01)
    older = {c["sale_date"]: c["index_ratio"] for c in comps}
    assert older["2025-09-01"] > older["2025-12-01"] > 1.0        # older sale, bigger uplift


def test_adjustment_uses_unrounded_values(tmp_path):
    sales = [(2, "2025-09-01", 343_001, 1), (3, "2025-12-01", 379_999, 1), (4, "2025-10-01", 331_777, 1)]
    data = build_db(tmp_path / "g.db", sales, bg=rising)
    arv, comps, _, _ = estimate_arv(SUBJ, AS_OF, P, data)
    now = data.index(pd.Timestamp(AS_OF), 90, 50_000, 30)
    sqft = {2: 1700, 3: 1800, 4: 1500}
    raw = [amt / sqft[l] * now / data.index(pd.Timestamp(d), 90, 50_000, 30) for l, d, amt, _ in sales]
    assert arv == int(round(np.percentile(raw, 60) * 1600))


# ---------------------------------------------------------------- the index itself
def test_index_ignores_sales_under_the_floor(tmp_path):
    junk = [(d, 1_000) for d in pd.date_range("2026-03-01", "2026-05-04", freq="D").date.astype(str)] * 3
    data = build_db(tmp_path / "g.db", THREE_COMPS, junk=junk)
    _, comps, _, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "ok"
    assert all(c["index_ratio"] == pytest.approx(1.0) for c in comps)   # junk transfers must not move the index


def test_stored_price_index_table_is_used(tmp_path):
    days = pd.date_range("2024-01-01", "2026-05-06", freq="D")
    table = [(d.date().isoformat(), 220.0 if d >= pd.Timestamp("2026-05-01") else 200.0) for d in days]
    data = build_db(tmp_path / "g.db", THREE_COMPS, price_index=table, bg=None)    # no background sales at all
    arv, comps, _, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "ok" and all(c["index_ratio"] == pytest.approx(1.1) for c in comps)
    assert arv == int(round(np.percentile([200 * 1.1, 210 * 1.1, 220 * 1.1], 60) * 1600))


def test_index_missing_for_as_of_is_flagged_not_guessed(tmp_path):
    data = build_db(tmp_path / "g.db", THREE_COMPS, bg=None)       # 3 sales in the county: below index_min_sales
    arv, comps, _, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "index_unavailable" and arv is None and comps == []


def test_comp_without_an_index_value_is_dropped_not_left_unadjusted(tmp_path):
    days = pd.date_range("2025-10-15", "2026-05-06", freq="D")      # table starts after the 2025-09-01 sale
    table = [(d.date().isoformat(), 200.0) for d in days]
    data = build_db(tmp_path / "g.db", THREE_COMPS, price_index=table, bg=None)
    arv, comps, window, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "insufficient_comps" and arv is None and window == 24     # only 2 usable comps


# ---------------------------------------------------------------- subjects that cannot be valued
def test_subject_with_no_square_footage(tmp_path):
    data = build_db(tmp_path / "g.db", THREE_COMPS, extra_parcels=[(7, None, 100)])
    assert estimate_arv(7, AS_OF, P, data) == (None, [], None, "no_size_on_record")


def test_subject_with_no_building_record(tmp_path):
    data = build_db(tmp_path / "g.db", THREE_COMPS, extra_parcels=[(7, 1600, 100)], no_building={7})
    assert estimate_arv(7, AS_OF, P, data) == (None, [], None, "no_building_record")


def test_subject_with_no_neighborhood(tmp_path):
    data = build_db(tmp_path / "g.db", THREE_COMPS, extra_parcels=[(7, 1600, None)])
    assert estimate_arv(7, AS_OF, P, data) == (None, [], None, "no_neighborhood")


def test_unknown_parcel(tmp_path):
    data = build_db(tmp_path / "g.db", THREE_COMPS)
    assert estimate_arv(999, AS_OF, P, data) == (None, [], None, "unknown_parcel")


def test_neighborhood_with_no_sales_at_all_is_insufficient_not_a_crash(tmp_path):
    data = build_db(tmp_path / "g.db", THREE_COMPS, extra_parcels=[(7, 1600, 300)])   # nobody ever sold in 300
    assert estimate_arv(7, AS_OF, P, data) == (None, [], 24, "insufficient_comps")


def test_index_ignores_tiny_homes(tmp_path):
    # house 8 is 200 sq ft and sells three times a day lately at $500/sq ft: bad data that would drag the median up
    junk = [(d, 100_000) for d in pd.date_range("2026-02-01", "2026-05-04", freq="D").date.astype(str)] * 3
    data = build_db(tmp_path / "g.db", THREE_COMPS, extra_parcels=[(8, 200, 200)], junk=[])
    con = sqlite3.connect(tmp_path / "g.db")
    con.executemany("INSERT INTO sales VALUES (8,?,?,1)", junk); con.commit(); con.close()
    data = load_county(tmp_path / "g.db")
    _, comps, _, status = estimate_arv(SUBJ, AS_OF, P, data)
    assert status == "ok" and all(c["index_ratio"] == pytest.approx(1.0) for c in comps)
