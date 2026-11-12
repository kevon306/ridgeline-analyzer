"""Low-memory access to the county database.

The old approach read ALL 246,806 parcels and 469,405 sales into memory at startup (about 350 MB), which the shared
server's memory limit kills. This module keeps the data in the SQLite file on disk and loads only what a given
candidate list needs: the candidate parcels, and the sales in those parcels' neighborhoods. Results are identical.
"""
from __future__ import annotations
import sqlite3
import pandas as pd
from .arv import CountyData
import re
from .pipeline import normalize_street, zip5


def _norm_pin(raw) -> str:
    """County parcel number with spaces removed and upper-cased: 'R6226 166' -> 'R6226166'."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    return re.sub(r"\s+", "", str(raw)).upper()


def prepare(db_path) -> sqlite3.Connection:
    """Open the database and add lookup columns/indexes once (cheap; works on a temp copy)."""
    con = sqlite3.connect(db_path, check_same_thread=False)
    cols = {r[1] for r in con.execute("PRAGMA table_info(parcels)")}
    if "addr_key" not in cols:
        con.execute("ALTER TABLE parcels ADD COLUMN addr_key TEXT")
        con.execute("ALTER TABLE parcels ADD COLUMN pin_key TEXT")
        rows = ((f"{normalize_street(a)}|{zip5(z)}", _norm_pin(p), l) for l, p, a, z in
                con.execute("SELECT lrsnum, pin, address, zip FROM parcels").fetchall())
        con.executemany("UPDATE parcels SET addr_key=?, pin_key=? WHERE lrsnum=?", rows)
    con.execute("CREATE INDEX IF NOT EXISTS ix_parcels_addr ON parcels(addr_key)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_parcels_pin ON parcels(pin_key)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_parcels_nbhd ON parcels(neighborhood)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_sales_lrs ON sales(lrsnum)")
    con.commit()
    return con


def lookup_parcels(con, candidates: pd.DataFrame) -> dict:
    """Same dict shape as pipeline.load_parcel_index, but only for the rows in this candidate list."""
    keys_addr, keys_pin = set(), set()
    for c in candidates.to_dict("records"):
        keys_addr.add(f"{normalize_street(c.get('address'))}|{zip5(c.get('zip'), c.get('address'))}")
        p = _norm_pin(c.get("parcel"))
        if p: keys_pin.add(p)
    index: dict = {}
    q = ("SELECT p.lrsnum, p.pin, p.address, p.zip, p.neighborhood, b.year_built, b.fin_sqft, p.addr_key, p.pin_key "
         "FROM parcels p LEFT JOIN buildings b USING (lrsnum) WHERE ")
    for col, keys, prefix in (("addr_key", keys_addr, ""), ("pin_key", keys_pin, "PIN:")):
        keys = list(keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            for r in con.execute(q + f"p.{col} IN ({','.join('?' * len(chunk))})", chunk):
                rec = dict(lrsnum=r[0], pin=r[1], address=r[2], zip=r[3], neighborhood=r[4], year_built=r[5], fin_sqft=r[6])
                key = prefix + (r[7] if col == "addr_key" else r[8])
                index.setdefault(key, [])
                if rec not in index[key]: index[key].append(rec)
    return index


def load_county_subset(con, index: dict) -> CountyData:
    """Subjects = the matched candidate parcels; sales = sales in those parcels' neighborhoods; price_index = the stored table."""
    recs = {r["lrsnum"]: r for v in index.values() for r in v}
    subjects = pd.DataFrame([dict(lrsnum=l, fin_sqft=r["fin_sqft"], year_built=r["year_built"], neighborhood=r["neighborhood"])
                             for l, r in recs.items()], columns=["lrsnum", "fin_sqft", "year_built", "neighborhood"]).set_index("lrsnum")
    nbhds = sorted({int(r["neighborhood"]) for r in recs.values() if r["neighborhood"] is not None and not pd.isna(r["neighborhood"])})
    parts = []
    for i in range(0, len(nbhds), 500):
        chunk = nbhds[i:i + 500]
        parts.append(pd.read_sql(
            f"SELECT s.lrsnum, s.sale_date, s.amount, b.fin_sqft, p.neighborhood, p.address FROM sales s "
            f"JOIN buildings b USING (lrsnum) JOIN parcels p USING (lrsnum) WHERE p.neighborhood IN ({','.join('?' * len(chunk))})",
            con, params=chunk))
    sales = pd.concat(parts) if parts else pd.DataFrame(columns=["lrsnum", "sale_date", "amount", "fin_sqft", "neighborhood", "address"])
    pi = pd.read_sql("SELECT day, ppsf FROM price_index", con)
    return CountyData(sales=sales, subjects=subjects, price_index=pd.Series(pi["ppsf"].to_numpy(), index=pd.to_datetime(pi["day"])))
