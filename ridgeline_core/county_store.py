"""County data kept in the SQLite file; only what an uploaded candidate list needs is loaded into memory.

Loading the whole county (load_county + load_parcel_index: 246,806 parcels, 469,405 sales) costs about 420 MB. This
module gives score_batch the same inputs restricted to one list:

  prepare(db_path)               once per database file: adds normalized lookup columns (street, ZIP, pin) to parcels
                                 and the indexes the lookups use. Writes to the file, so pass a working copy.
  lookup_parcels(con, cands)     the parcel lookup (same shape as pipeline.load_parcel_index) for every address key and
                                 parcel number in the list, including every parcel that shares a key, so ambiguous
                                 addresses stay ambiguous.
  load_county_subset(con, idx)   a CountyData holding the looked-up parcels as subjects, all sales in their
                                 neighborhoods (comps never come from another neighborhood) and the stored price_index.

The result scores exactly like the full load provided the database has a price_index table (otherwise the index is
computed from every county sale, which a subset cannot reproduce, so load_county_subset refuses) and score_batch gets
an as-of date (the app passes params["as_of"]; without one the date is taken from the newest sale loaded).
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from .arv import CountyData
from .pipeline import normalize_pin, normalize_street, zip5

STORE_VERSION = 1
_CHUNK = 500                      # keep IN (...) lists well under SQLite's variable limit
_PARCEL_COLS = ("p.lrsnum, p.pin, p.address, p.zip, p.neighborhood, b.year_built, b.fin_sqft, b.cond_desc, "
                "p.street_key, p.zip_key")


def prepare(db_path) -> None:
    """Add street_key / zip_key / pin_key to parcels and index them. Safe to call again: it does nothing once done."""
    con = sqlite3.connect(db_path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS county_store_meta (version INTEGER NOT NULL)")
        row = con.execute("SELECT max(version) FROM county_store_meta").fetchone()
        if row and row[0] == STORE_VERSION:
            return
        cols = {r[1] for r in con.execute("PRAGMA table_info(parcels)")}
        for c in ("street_key", "zip_key", "pin_key"):
            if c not in cols:
                con.execute(f"ALTER TABLE parcels ADD COLUMN {c} TEXT")
        # the pipeline's own normalizers, run row by row inside SQLite (nothing county-wide is held in Python)
        con.create_function("street_key", 1, normalize_street, deterministic=True)
        con.create_function("zip_key", 1, lambda z: zip5(z), deterministic=True)
        con.create_function("pin_key", 1, normalize_pin, deterministic=True)
        con.execute("UPDATE parcels SET street_key = street_key(address), zip_key = zip_key(zip), pin_key = pin_key(pin)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_parcels_key ON parcels (street_key, zip_key)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_parcels_pin ON parcels (pin_key)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_parcels_nbhd ON parcels (neighborhood)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_sales_lrs ON sales (lrsnum)")
        con.execute("DELETE FROM county_store_meta")
        con.execute("INSERT INTO county_store_meta VALUES (?)", (STORE_VERSION,))
        con.commit()
    finally:
        con.close()


def _chunks(seq, n=_CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def lookup_parcels(con: sqlite3.Connection, candidates: pd.DataFrame) -> dict:
    """key 'STREET|ZIP' -> list of parcel dicts, for the candidates' address keys and parcel numbers only.
    Every parcel sharing a returned key is included, exactly as in the full lookup."""
    keys = set()
    for c in candidates.to_dict("records"):
        street = normalize_street(c.get("address"))
        if street:
            keys.add((street, zip5(c.get("zip"), c.get("address"))))
    if "parcel" in candidates.columns:
        pins = {normalize_pin(p) for p in candidates["parcel"]} - {""}
        for part in _chunks(pins):
            keys |= set(con.execute(f"SELECT street_key, zip_key FROM parcels WHERE pin_key IN ({','.join('?' * len(part))})",
                                    part).fetchall())
    index: dict = {}
    names = ("lrsnum", "pin", "address", "zip", "neighborhood", "year_built", "fin_sqft", "cond_desc")
    for part in _chunks(sorted(keys)):
        where = " OR ".join(["(p.street_key = ? AND p.zip_key = ?)"] * len(part))
        args = [v for k in part for v in k]
        for r in con.execute(f"SELECT {_PARCEL_COLS} FROM parcels p LEFT JOIN buildings b USING (lrsnum) WHERE {where} "
                             "ORDER BY p.rowid", args):
            index.setdefault(f"{r[8]}|{r[9]}", []).append(dict(zip(names, r[:8])))
    return index


def load_county_subset(con: sqlite3.Connection, index: dict) -> CountyData:
    """CountyData for the parcels in `index`: those parcels as subjects, every sale in their neighborhoods, and the
    stored price_index. Raises ValueError when the database has no price_index table (see the module note)."""
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "price_index" not in tables:
        raise ValueError("database has no price_index table; a subset cannot reproduce the county-wide index, use load_county")
    parcels = [p for hits in index.values() for p in hits]
    lrs = sorted({int(p["lrsnum"]) for p in parcels})
    nbhds = sorted({int(p["neighborhood"]) for p in parcels if p["neighborhood"] is not None and not pd.isna(p["neighborhood"])})
    sales = [pd.DataFrame(columns=["lrsnum", "sale_date", "amount", "fin_sqft", "neighborhood", "address"])]
    for part in _chunks(nbhds):
        sales.append(pd.read_sql(
            f"""SELECT s.lrsnum, s.sale_date, s.amount, b.fin_sqft, p.neighborhood, p.address
                FROM sales s JOIN buildings b USING (lrsnum) JOIN parcels p USING (lrsnum)
                WHERE p.neighborhood IN ({','.join('?' * len(part))}) ORDER BY s.rowid""", con, params=part))
    subjects = [pd.DataFrame(columns=["lrsnum", "fin_sqft", "year_built", "neighborhood"])]
    for part in _chunks(lrs):
        subjects.append(pd.read_sql(
            f"SELECT p.lrsnum, b.fin_sqft, b.year_built, p.neighborhood FROM parcels p LEFT JOIN buildings b USING (lrsnum) "
            f"WHERE p.lrsnum IN ({','.join('?' * len(part))})", con, params=part))
    pi = pd.read_sql("SELECT day, ppsf FROM price_index", con)
    frames = [f for f in sales if len(f)] or sales[:1]
    subj = [f for f in subjects if len(f)] or subjects[:1]
    return CountyData(sales=pd.concat(frames, ignore_index=True),
                      subjects=pd.concat(subj, ignore_index=True).set_index("lrsnum"),
                      price_index=pd.Series(pi["ppsf"].to_numpy(), index=pd.to_datetime(pi["day"])))
