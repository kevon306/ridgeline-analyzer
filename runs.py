"""Saved screening runs: save, list, open and delete, always for one user.

Every read and delete names the owner: the query filters runs.user_id = :user_id (run_rows are reached only through a
run that passes that filter), so a guessed run id belonging to someone else matches nothing. The users / runs /
run_rows tables are the ones in db.py; nothing here creates or alters tables.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd
from sqlalchemy import and_, delete, func, insert, select
from sqlalchemy.engine import Engine

from db import run_rows, runs
from ridgeline_core.pipeline import EXPLAIN, normalize_street, zip5
from ridgeline_core.rules import compute_roi, renovation_estimate

_LIMITS = {"address": 200, "zip": 10, "status": 30, "reason": 40, "matched_address": 200}
_MONEY = ("asking_price", "arv", "spread", "reno_likely_with_contingency", "net_profit")


def _num(x, places):
    if x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x):
        return None
    return round(float(x), places)


def _text(x, field):
    if x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x):
        return None
    return str(x)[:_LIMITS[field]]


def run_name(source: str, when: dt.datetime | None = None) -> str:
    """The uploaded file's name plus the date and time, e.g. 'candidates.csv · 2026-11-13 08:15'."""
    when = when or dt.datetime.now()
    return f"{source} · {when:%Y-%m-%d %H:%M}"[:150]


def save_run(engine: Engine, user_id: int, name: str, scored: pd.DataFrame) -> int:
    """Store one scored list: a runs row and one run_rows row per scored row. Returns the new run id."""
    as_of = dt.date.fromisoformat(str(scored["as_of"].dropna().iloc[0])) if scored["as_of"].notna().any() else None
    rows = []
    for _, r in scored.iterrows():
        rank = r.get("rank")
        rows.append({
            "row_id": int(r["row_id"]), "address": _text(r.get("address"), "address"), "zip": _text(r.get("zip"), "zip"),
            "asking_price": _num(r.get("asking_price_used"), 2), "status": _text(r.get("status"), "status"),
            "reason": _text(r.get("reason"), "reason"), "matched_address": _text(r.get("matched_address"), "matched_address"),
            "arv": _num(r.get("arv"), 2), "comp_count": None if pd.isna(r.get("comp_count")) else int(r["comp_count"]),
            "spread": _num(r.get("spread"), 2), "reno_likely_with_contingency": _num(r.get("reno_likely_with_contingency"), 2),
            "net_profit": _num(r.get("net_profit"), 2), "roi_pct": _num(r.get("roi_pct"), 3),
            "rank_in_run": None if rank is None or pd.isna(rank) else int(rank),
            "comps_json": r.get("comps_json") if isinstance(r.get("comps_json"), str) else None,
        })
    with engine.begin() as con:
        run_id = con.execute(insert(runs).values(user_id=int(user_id), name=name[:150], as_of=as_of,
                                                 n_rows=len(scored))).inserted_primary_key[0]
        if rows:
            con.execute(insert(run_rows), [{**row, "run_id": run_id} for row in rows])
    return int(run_id)


def list_runs(engine: Engine, user_id: int) -> pd.DataFrame:
    """This user's runs, newest first: id, name, created_at, as_of, n_rows, n_ranked."""
    ranked = (select(run_rows.c.run_id, func.count().label("n_ranked")).select_from(run_rows.join(runs, run_rows.c.run_id == runs.c.id))
              .where(runs.c.user_id == user_id, run_rows.c.status == "ranked").group_by(run_rows.c.run_id).subquery())
    q = (select(runs.c.id, runs.c.name, runs.c.created_at, runs.c.as_of, runs.c.n_rows,
                func.coalesce(ranked.c.n_ranked, 0).label("n_ranked"))
         .select_from(runs.outerjoin(ranked, ranked.c.run_id == runs.c.id))
         .where(runs.c.user_id == user_id).order_by(runs.c.created_at.desc(), runs.c.id.desc()))
    with engine.connect() as con:
        return pd.DataFrame(con.execute(q).mappings().all(), columns=["id", "name", "created_at", "as_of", "n_rows", "n_ranked"])


def load_run(engine: Engine, user_id: int, run_id: int) -> tuple[dict, pd.DataFrame] | None:
    """(run, rows) when run_id belongs to user_id, else None."""
    owned = and_(runs.c.id == run_id, runs.c.user_id == user_id)
    with engine.connect() as con:
        run = con.execute(select(runs).where(owned)).mappings().first()
        if run is None:
            return None
        rows = con.execute(select(run_rows).select_from(run_rows.join(runs, run_rows.c.run_id == runs.c.id))
                           .where(owned).order_by(run_rows.c.id)).mappings().all()
    df = pd.DataFrame(rows, columns=[c.name for c in run_rows.c])
    for c in (*_MONEY, "roi_pct"):
        df[c] = pd.to_numeric(df[c].astype(object), errors="coerce").astype(float)
    return dict(run), df


def delete_run(engine: Engine, user_id: int, run_id: int) -> bool:
    """Delete a run and its rows if it belongs to user_id. False (and nothing deleted) otherwise."""
    with engine.begin() as con:
        mine = select(runs.c.id).where(runs.c.id == run_id, runs.c.user_id == user_id)
        if con.execute(mine).first() is None:
            return False
        con.execute(delete(run_rows).where(run_rows.c.run_id.in_(mine)))
        con.execute(delete(runs).where(runs.c.id == run_id, runs.c.user_id == user_id))
    return True


def rebuild_scored(run: dict, rows: pd.DataFrame, params: dict, index: dict) -> pd.DataFrame:
    """Turn saved rows back into the scored-table shape the display uses.

    Saved: every field in run_rows. Filled from the county record (`index`, looked up for these addresses) and the
    current assumptions, ONLY when they reproduce the saved renovation budget and profit to the cent: the parcel,
    the renovation tier and low / high band, selling and holding costs. Otherwise those stay empty and the detail
    panel says the breakdown is not available. Explanations come from the reason code.
    """
    as_of = pd.Timestamp(run["as_of"]) if run.get("as_of") else pd.Timestamp(params["as_of"])
    out = pd.DataFrame({
        "row_id": rows["row_id"].astype(int), "address": rows["address"], "zip": rows["zip"],
        "status": rows["status"], "reason": rows["reason"].fillna(""), "matched_address": rows["matched_address"],
        "asking_price_used": rows["asking_price"], "arv": rows["arv"], "comp_count": rows["comp_count"],
        "spread": rows["spread"], "reno_likely_with_contingency": rows["reno_likely_with_contingency"],
        "net_profit": rows["net_profit"], "roi_pct": rows["roi_pct"], "comps_json": rows["comps_json"],
        "rank": pd.array(rows["rank_in_run"], dtype="Int64"), "as_of": as_of.date().isoformat(),
    })
    out["top5"] = out["rank"].fillna(99).astype(int) <= 5
    out["explanation"] = out["reason"].map(lambda c: EXPLAIN[c or "ranked"].format(min_spread=params["min_spread"]))
    for c in ("lrsnum", "comp_window_months", "reno_low", "reno_likely_base", "reno_high", "selling", "holding"):
        out[c] = float("nan")
    out["reno_tier"] = None
    for i, r in out.iterrows():
        if pd.isna(r["matched_address"]):
            continue
        hits = [h for h in index.get(f"{normalize_street(r['address'])}|{zip5(r['zip'], r['address'])}", [])
                if h["address"] == r["matched_address"]]
        if len(hits) != 1:
            continue
        h = hits[0]
        out.at[i, "lrsnum"] = h["lrsnum"]
        if pd.isna(r["reno_likely_with_contingency"]) or h.get("fin_sqft") is None or h.get("year_built") is None:
            continue
        reno = renovation_estimate(float(h["fin_sqft"]), int(h["year_built"]), as_of.year, params)
        fin = compute_roi(r["asking_price_used"], r["arv"], reno["likely_with_contingency"], params)
        if abs(round(reno["likely_with_contingency"], 2) - r["reno_likely_with_contingency"]) <= 0.01 and \
                abs(round(fin["net_profit"], 2) - r["net_profit"]) <= 0.01:
            out.at[i, "reno_tier"] = reno["tier"]
            for c, v in (("reno_low", reno["low"]), ("reno_likely_base", reno["likely_base"]), ("reno_high", reno["high"]),
                         ("selling", fin["selling"]), ("holding", fin["holding"])):
                out.at[i, c] = v
    order = {"ranked": 0, "eliminated": 1, "insufficient_comps": 2, "unmatched": 3}
    out["_o"] = out["status"].map(order)
    out["_r"] = out["rank"].astype("float").fillna(1e9)
    return out.sort_values(["_o", "_r", "row_id"], kind="mergesort").drop(columns=["_o", "_r"]).reset_index(drop=True)
