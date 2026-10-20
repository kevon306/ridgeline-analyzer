"""Optional parcel-number matching (agreed 20 Oct): exact pin first, ignoring spaces and case; otherwise the exact
address + ZIP join. A parcel that matches nothing never hides an address match and never passes silently."""
import gzip
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import AS_OF, base_parcels, build_db, three_comps  # noqa: E402
from ridgeline_core.arv import load_county, load_params  # noqa: E402
from ridgeline_core.pipeline import load_parcel_index, normalize_pin, score_batch  # noqa: E402

P = load_params(ROOT / "assumptions.json")


def score(db, rows, parcel_column=True):
    cols = ["address", "zip", "asking_price"] + (["parcel"] if parcel_column else [])
    c = pd.DataFrame([r if parcel_column else r[:3] for r in rows], columns=cols).astype(str)
    c.insert(0, "row_id", range(1, len(c) + 1))
    return score_batch(c, db, P, AS_OF).set_index("row_id")


@pytest.fixture
def db(tmp_path):                          # helpers.build_db gives parcel n the pin "P<n>"; subject 1 is 100 MAIN ST 30078
    return build_db(tmp_path / "g.db", base_parcels(), three_comps())


# ---------------------------------------------------------------- synthetic county
def test_blank_parcel_falls_back_to_the_address_join(db):
    r = score(db, [("100 Main St", "30078", "285000", "")]).loc[1]
    assert r["lrsnum"] == 1 and r["status"] != "unmatched" and "parcel" not in r["explanation"].lower()


def test_wrong_parcel_with_a_good_address_still_matches_by_address(db):
    r = score(db, [("100 Main St", "30078", "285000", "P999999")]).loc[1]
    assert r["lrsnum"] == 1 and r["status"] != "unmatched" and "Matched by parcel" not in r["explanation"]


def test_wrong_parcel_and_wrong_address_is_unmatched_address(db):
    r = score(db, [("1 Nowhere Rd", "30078", "285000", "P999999")]).loc[1]
    assert (r["status"], r["reason"]) == ("unmatched", "unmatched_address") and pd.isna(r["lrsnum"])


def test_parcel_wins_over_an_address_that_matches_another_house(db):
    r = score(db, [("2 Comp Rd", "30078", "285000", "P1")]).loc[1]
    assert r["lrsnum"] == 1 and r["matched_address"] == "100 MAIN ST"
    assert "Matched by parcel P1; the county address is 100 MAIN ST, ZIP 30078." in r["explanation"]


@pytest.mark.parametrize("typed", ["P1", "p1", " p 1 ", "P\t1"])
def test_spaces_and_case_in_the_parcel_are_ignored(db, typed):
    r = score(db, [("1 Nowhere Rd", "30078", "285000", typed)]).loc[1]
    assert r["lrsnum"] == 1


def test_same_address_by_parcel_adds_no_note(db):
    r = score(db, [("100 Main Street", "30078", "285000", "P1")]).loc[1]
    assert r["lrsnum"] == 1 and "parcel" not in r["explanation"].lower()


def test_missing_parcel_column_still_works(db):
    out = score(db, [("100 Main St", "30078", "285000"), ("1 Nowhere Rd", "30078", "285000")], parcel_column=False)
    assert out.loc[1, "lrsnum"] == 1 and out.loc[2, "reason"] == "unmatched_address" and len(out) == 2


def test_normalize_pin():
    assert normalize_pin("R6226 166") == normalize_pin("r6226166") == "R6226166"
    assert normalize_pin(None) == normalize_pin(float("nan")) == normalize_pin("  ") == ""


# ---------------------------------------------------------------- the real county records
@pytest.fixture(scope="module")
def county(tmp_path_factory):
    path = tmp_path_factory.mktemp("county") / "gwinnett.db"
    with gzip.open(ROOT / "data" / "gwinnett.db.gz", "rb") as f_in, open(path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    return load_county(path), load_parcel_index(path)


def score_real(county, rows):
    data, index = county
    c = pd.DataFrame(rows, columns=["address", "zip", "asking_price", "parcel"]).astype(str)
    c.insert(0, "row_id", range(1, len(c) + 1))
    return score_batch(c, None, P, None, data=data, index=index).set_index("row_id")


def test_real_parcels_match_when_the_address_does_not(county):
    out = score_real(county, [
        ("2325 E Hill Way NW", "30071", "300000", "R6226 166"),
        ("865 Branford Ln NW", "30047", "300000", "R6157 253"),
        ("2325 E Hill Way NW", "30071", "300000", ""),           # the same rows without a parcel: the address join fails
        ("865 Branford Ln NW", "30047", "300000", ""),
    ])
    assert (out.loc[1, "lrsnum"], out.loc[1, "matched_address"]) == (1031036, "2325 EAST HILL WAY")
    assert "Matched by parcel R6226 166; the county address is 2325 EAST HILL WAY, ZIP 30071." in out.loc[1, "explanation"]
    assert (out.loc[2, "lrsnum"], out.loc[2, "matched_address"]) == (876992, "865 BRANFORD LN")
    assert "the county address is 865 BRANFORD LN, ZIP 30047" in out.loc[2, "explanation"]
    assert out.loc[1, "status"] != "unmatched" and out.loc[2, "status"] != "unmatched"
    assert out.loc[3, "reason"] == out.loc[4, "reason"] == "unmatched_address"
