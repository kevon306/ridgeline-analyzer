"""R-04: after-repair value (ARV) from neighborhood comps.

Implements Decision D1, option A, from Design Document v1.0 (as amended 7 Oct 2026):
  1. Comp pool: same neighborhood, sale strictly before as_of, amount >= comp_floor,
     fin_sqft within size_band of the subject, one most-recent sale per house,
     subject excluded. Window 12 months; if < min_comps usable comps, widen once to 24.
  2. A comp is usable only if the price index exists for its sale date (it cannot be
     time-adjusted otherwise). Fewer than min_comps usable comps after widening ->
     status "insufficient_comps", no ARV.
  3. Each comp: amount / fin_sqft * index(as_of) / index(comp sale date).
     index(d) = median $/sq ft of county single-family sales of at least comp_floor on
     homes of more than index_min_sqft (300) sq ft, in the index_days strictly before d (so nothing from
     day d itself leaks in). If the database has a price_index table (day, ppsf) it is
     used as is; otherwise the index is computed from the sales table, and is missing
     (never guessed) when fewer than index_min_sales sales fall in the window.
  4. ARV = arv_percentile of the UNROUNDED adjusted $/sq ft * subject fin_sqft, whole dollars.

Contract: estimate_arv(lrsnum, as_of, params, data) -> arv, comps[], window_months, status

status is "ok" or one of the failure codes below. score_batch maps every failure to the
row status "insufficient_comps" and copies the code into the row's `reason`:
  unknown_parcel      lrsnum is not in the database
  no_neighborhood     parcel has no neighborhood code
  no_building_record  parcel has no building record (no year built, no size)
  no_size_on_record   building record exists but fin_sqft is missing or not positive
  index_unavailable   the price index is missing for as_of, so nothing can be adjusted
  insufficient_comps  fewer than min_comps usable comps after widening to 24 months
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULTS = {
    "comp_floor": 50_000,
    "size_band": [0.75, 1.25],
    "min_comps": 3,
    "window_months": [12, 24],
    "arv_percentile": 0.60,
    "index_days": 90,
    "index_min_sales": 30,
    "index_min_sqft": 300,
}


def load_params(path: str | Path = "assumptions.json") -> dict:
    """Merge assumptions.json over DEFAULTS (so R-05 stays one file)."""
    p = dict(DEFAULTS)
    path = Path(path)
    if path.exists():
        p.update(json.loads(path.read_text()))
    return p


@dataclass
class CountyData:
    """Sales joined to parcels/buildings, loaded once and reused for every call."""
    sales: pd.DataFrame                     # lrsnum, sale_date, amount, fin_sqft, neighborhood, address
    subjects: pd.DataFrame                  # indexed by lrsnum: fin_sqft, neighborhood, year_built
    price_index: pd.Series | None = None    # optional stored daily index, indexed by day
    _by_nbhd: dict = field(default_factory=dict)
    _arrays: dict = field(default_factory=dict)       # floor -> (dates, ppsf)
    _idx_cache: dict = field(default_factory=dict)

    def __post_init__(self):
        s = self.sales.copy()
        s["sale_date"] = pd.to_datetime(s["sale_date"])
        s = s[(s["amount"] > 0) & (s["fin_sqft"] > 0)].copy()
        s["ppsf"] = s["amount"] / s["fin_sqft"]
        self.sales = s.sort_values("sale_date").reset_index(drop=True)
        self._by_nbhd = {k: g for k, g in self.sales.groupby("neighborhood")}
        if self.price_index is not None:
            self.price_index = self.price_index.copy()
            self.price_index.index = pd.to_datetime(self.price_index.index).normalize()

    def _floored(self, floor: float, min_sqft: float):
        if (floor, min_sqft) not in self._arrays:
            s = self.sales[(self.sales["amount"] >= floor) & (self.sales["fin_sqft"] > min_sqft)]
            self._arrays[(floor, min_sqft)] = (s["sale_date"].to_numpy(), s["ppsf"].to_numpy())
        return self._arrays[(floor, min_sqft)]

    def index(self, d: pd.Timestamp, days: int, floor: float, min_sales: int, min_sqft: float = 300) -> float:
        """Median $/sq ft of county sales in [d - days, d); NaN when it cannot be trusted."""
        d = pd.Timestamp(d)
        key = (d, days, floor, min_sales, min_sqft, self.price_index is not None)
        if key in self._idx_cache:
            return self._idx_cache[key]
        if self.price_index is not None:
            v = self.price_index.get(d.normalize(), np.nan)
            val = float(v) if pd.notna(v) else np.nan
        else:
            dates, ppsf = self._floored(floor, min_sqft)
            lo = np.searchsorted(dates, np.datetime64(d - pd.Timedelta(days=days)), "left")
            hi = np.searchsorted(dates, np.datetime64(d), "left")
            val = float(np.median(ppsf[lo:hi])) if (hi - lo) >= min_sales else np.nan
        self._idx_cache[key] = val
        return val


def load_county(db_path: str | Path) -> CountyData:
    """Read gwinnett.db (contract 1) into memory, including price_index if present."""
    con = sqlite3.connect(db_path)
    try:
        sales = pd.read_sql(
            """SELECT s.lrsnum, s.sale_date, s.amount, b.fin_sqft, p.neighborhood, p.address
               FROM sales s JOIN buildings b USING (lrsnum) JOIN parcels p USING (lrsnum)""",
            con,
        )
        subjects = pd.read_sql(
            "SELECT p.lrsnum, b.fin_sqft, b.year_built, p.neighborhood FROM parcels p "
            "LEFT JOIN buildings b USING (lrsnum)", con
        ).set_index("lrsnum")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        price_index = None
        if "price_index" in tables:
            pi = pd.read_sql("SELECT day, ppsf FROM price_index", con)
            price_index = pd.Series(pi["ppsf"].to_numpy(), index=pd.to_datetime(pi["day"]))
    finally:
        con.close()
    return CountyData(sales=sales, subjects=subjects, price_index=price_index)


def _comp_pool(data: CountyData, lrsnum, nbhd, sqft, as_of, months, p) -> pd.DataFrame:
    g = data._by_nbhd.get(nbhd)
    if g is None:
        return data.sales.iloc[0:0]
    start = as_of - pd.DateOffset(months=months)
    lo_band, hi_band = p["size_band"]
    pool = g[
        (g["sale_date"] < as_of)
        & (g["sale_date"] >= start)
        & (g["amount"] >= p["comp_floor"])
        & (g["fin_sqft"] >= lo_band * sqft)
        & (g["fin_sqft"] <= hi_band * sqft)
        & (g["lrsnum"] != lrsnum)
    ]
    # one most-recent sale per house (a flipped house is one comp, not two)
    return pool.sort_values("sale_date").groupby("lrsnum", as_index=False).tail(1)


def estimate_arv(lrsnum, as_of, params: dict, data: CountyData):
    """Returns (arv, comps, window_months, status). arv is None unless status == 'ok'."""
    p = {**DEFAULTS, **(params or {})}
    as_of = pd.Timestamp(as_of)

    if lrsnum not in data.subjects.index:
        return None, [], None, "unknown_parcel"
    subj = data.subjects.loc[lrsnum]
    nbhd, sqft = subj["neighborhood"], subj["fin_sqft"]
    if pd.isna(nbhd):
        return None, [], None, "no_neighborhood"
    if pd.isna(sqft) or sqft <= 0:
        return None, [], None, ("no_building_record" if pd.isna(subj["year_built"]) else "no_size_on_record")

    def idx(d):
        return data.index(d, p["index_days"], p["comp_floor"], p["index_min_sales"], p["index_min_sqft"])

    idx_now = idx(as_of)
    if not (idx_now > 0):                       # NaN or non-positive
        return None, [], None, "index_unavailable"

    pool, used = None, None
    for months in p["window_months"]:
        cand = _comp_pool(data, lrsnum, nbhd, sqft, as_of, months, p).copy()
        cand["idx_then"] = np.array([idx(d) for d in cand["sale_date"]], dtype=float)   # also safe when the pool is empty
        pool, used = cand[cand["idx_then"] > 0], months       # NaN > 0 is False: unusable comps drop out
        if len(pool) >= p["min_comps"]:
            break
    if len(pool) < p["min_comps"]:
        return None, [], used, "insufficient_comps"

    adj_raw = (pool["ppsf"] * idx_now / pool["idx_then"]).to_numpy()
    comps = [
        {
            "lrsnum": r.lrsnum, "address": r.address,
            "sale_date": r.sale_date.date().isoformat(),
            "amount": int(r.amount), "fin_sqft": int(r.fin_sqft),
            "ppsf": round(float(r.ppsf), 2), "adj_ppsf": round(float(a), 2),
            "index_ratio": round(idx_now / r.idx_then, 4),
        }
        for r, a in zip(pool.itertuples(), adj_raw)
    ]
    arv = int(round(np.percentile(adj_raw, p["arv_percentile"] * 100) * sqft))   # unrounded values
    return arv, comps, used, "ok"
