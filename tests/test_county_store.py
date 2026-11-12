"""The SQLite-backed path (county_store) scores exactly like the old full-load path (load_county + load_parcel_index)."""
import shutil
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ridgeline_core.arv import load_county, load_params  # noqa: E402
from ridgeline_core.county_store import load_county_subset, lookup_parcels, prepare  # noqa: E402
from ridgeline_core.pipeline import load_candidates, load_parcel_index, normalize_pin, score_batch  # noqa: E402

P = load_params(ROOT / "assumptions.json")
FIXTURE = ROOT / "data" / "candidates_dev_fixture.csv"
COMPARED = ["row_id", "status", "reason", "rank", "arv", "comps_json", "comp_count", "comp_window_months", "spread",
            "net_profit", "roi_pct", "lrsnum", "matched_address", "explanation"]


@pytest.fixture
def extract(tmp_path):
    """A working copy of the 999-parcel extract (prepare writes to the file)."""
    db = tmp_path / "extract.db"
    shutil.copyfile(ROOT / "data" / "gwinnett_extract.db", db)
    prepare(db)
    return db


def old_path(db, c):
    return score_batch(c, None, P, None, data=load_county(db), index=load_parcel_index(db))


def new_path(db, c):
    con = sqlite3.connect(db)
    try:
        index = lookup_parcels(con, c)
        return score_batch(c, None, P, None, data=load_county_subset(con, index), index=index), index
    finally:
        con.close()


def match_method(scored: pd.DataFrame, pins: dict) -> list:
    """'parcel' when the row's parcel number is the matched record's pin, 'address' for any other match, else 'none'."""
    out = []
    for _, r in scored.iterrows():
        if pd.isna(r["lrsnum"]):
            out.append("none")
        elif "parcel" in scored.columns and normalize_pin(r.get("parcel")) and normalize_pin(r["parcel"]) == normalize_pin(pins.get(int(r["lrsnum"]))):
            out.append("parcel")
        else:
            out.append("address")
    return out


def assert_same(db, c):
    old = old_path(db, c)
    new, index = new_path(db, c)
    pd.testing.assert_frame_equal(old, new, check_exact=True)                      # every column, every row, exact
    pd.testing.assert_frame_equal(old[COMPARED], new[COMPARED], check_exact=True)  # the ones that matter, named
    full_pins = {int(p["lrsnum"]): p["pin"] for hits in load_parcel_index(db).values() for p in hits}
    sub_pins = {int(p["lrsnum"]): p["pin"] for hits in index.values() for p in hits}
    methods = match_method(new, sub_pins)
    assert match_method(old, full_pins) == methods
    return old, new, methods


def test_dev_fixture_scores_identically(extract):
    old, _, _ = assert_same(extract, load_candidates(FIXTURE))
    assert set(old["status"]) == {"ranked", "eliminated", "insufficient_comps", "unmatched"}   # every outcome exercised
    assert old["rank"].notna().sum() == 14


def test_dev_fixture_with_a_parcel_column_scores_identically(extract):
    c = load_candidates(FIXTURE)
    con = sqlite3.connect(extract)
    pin = {a: p for a, p in con.execute("SELECT address, pin FROM parcels")}
    other = con.execute("SELECT pin, address FROM parcels WHERE address NOT IN (%s) LIMIT 1"
                        % ",".join("?" * len(c)), [a.upper() for a in c["address"]]).fetchone()
    con.close()
    parcels = []
    for i, a in enumerate(c["address"]):
        p = pin.get(str(a).upper(), "")
        parcels.append([p, p.lower().replace(" ", ""), "R0000 000", "", other[0]][i % 5])   # right, reformatted, wrong, blank, another house
    c["parcel"] = parcels
    c.loc[len(c)] = [len(c) + 1, "1 Nowhere Rd", "30078", "250000", "TEST", other[0]]        # bad address, good parcel
    old, new, methods = assert_same(extract, c)
    assert {"parcel", "address", "none"} <= set(methods)                                        # every match method exercised
    assert (new["explanation"].str.contains("Matched by parcel")).any()


def test_unmatched_and_ambiguous_keys_stay_the_same(extract):
    con = sqlite3.connect(extract)
    dup = con.execute("SELECT address, zip FROM parcels GROUP BY address, zip HAVING count(*) > 1 LIMIT 1").fetchone()
    con.close()
    rows = [("1 Nowhere Rd", "30078", "250000"), ("", "", "250000"), ("2665 Cork St", "30517", "")]
    if dup:
        rows.append((dup[0], dup[1], "250000"))
    c = pd.DataFrame(rows, columns=["address", "zip", "asking_price"])
    c.insert(0, "row_id", range(1, len(c) + 1))
    assert_same(extract, c)


def test_prepare_is_repeatable_and_keeps_the_data(extract):
    con = sqlite3.connect(extract)
    before = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("parcels", "buildings", "sales", "price_index")}
    con.close()
    prepare(extract)
    con = sqlite3.connect(extract)
    after = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
    keys = con.execute("SELECT count(*) FROM parcels WHERE street_key IS NULL OR zip_key IS NULL OR pin_key IS NULL").fetchone()[0]
    con.close()
    assert before == after and keys == 0


def test_subset_needs_the_stored_price_index(extract):
    con = sqlite3.connect(extract)
    con.execute("DROP TABLE price_index")
    with pytest.raises(ValueError, match="price_index"):
        load_county_subset(con, {})
    con.close()
