"""What the dashboard reads and how it summarises it. Everything comes live from the saved runs in MySQL (tables runs
and run_rows), filtered to the logged-in user; nothing here is hard-coded or cached.

The charter question it answers: "Across a batch of candidates, which are worth pursuing by projected return, and why
were the rest screened out?" A candidate is worth pursuing when it is ranked (passed the price screen, valued from 3+
nearby sales) AND its projected ROI is above zero."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from db import run_rows, runs

QUESTION = ("Across a batch of candidates, which are worth pursuing by projected return, and why were the rest "
            "screened out?")
OUTCOMES = ["Worth pursuing", "Ranked, projected loss", "Eliminated", "Could not value", "Could not match"]
_NUM = ["asking_price", "arv", "spread", "reno_likely_with_contingency", "net_profit", "roi_pct"]


def user_rows(engine: Engine, user_id: int, run_id: int | None = None) -> pd.DataFrame:
    """Every saved row of this user's runs (or of one of their runs), with the run's name and date."""
    q = (select(run_rows.c.run_id, runs.c.name.label("run_name"), runs.c.created_at, run_rows.c.row_id,
                run_rows.c.address, run_rows.c.matched_address, run_rows.c.status, run_rows.c.reason,
                *[run_rows.c[c] for c in _NUM], run_rows.c.comp_count)
         .select_from(run_rows.join(runs, run_rows.c.run_id == runs.c.id))
         .where(runs.c.user_id == user_id))
    if run_id is not None:
        q = q.where(runs.c.id == run_id)
    with engine.connect() as con:
        df = pd.DataFrame(con.execute(q.order_by(runs.c.created_at, run_rows.c.id)).mappings().all(),
                          columns=["run_id", "run_name", "created_at", "row_id", "address", "matched_address", "status",
                                   "reason", *_NUM, "comp_count"])
    for c in _NUM:
        df[c] = pd.to_numeric(df[c].astype(object), errors="coerce").astype(float)
    return df


def outcome(row) -> str:
    if row["status"] == "ranked":
        return "Worth pursuing" if pd.notna(row["roi_pct"]) and row["roi_pct"] > 0 else "Ranked, projected loss"
    return {"eliminated": "Eliminated", "insufficient_comps": "Could not value", "unmatched": "Could not match"}[row["status"]]


def summarize(df: pd.DataFrame, reason_label: dict) -> dict:
    """Counts, the ROI spread of ranked candidates, the reasons the rest were screened out, and a one-sentence answer."""
    if df.empty:
        return {"n": 0}
    df = df.assign(outcome=df.apply(outcome, axis=1))
    counts = df["outcome"].value_counts().reindex(OUTCOMES, fill_value=0)
    ranked = df[df["status"] == "ranked"]
    out = df[df["status"] != "ranked"].assign(why=lambda d: d["reason"].map(lambda r: reason_label.get(r, r or "No reason")))
    reasons = out["why"].value_counts()
    worth = (df[df["outcome"] == "Worth pursuing"]
             .assign(address=lambda d: d["matched_address"].fillna(d["address"]))
             .sort_values(["roi_pct", "net_profit"], ascending=False))
    n, n_worth = len(df), int(counts["Worth pursuing"])
    answer = (f"Of {n:,} candidates in {df['run_id'].nunique()} saved run{'s' if df['run_id'].nunique() != 1 else ''}, "
              f"{n_worth:,} ({n_worth / n:.0%}) are worth pursuing: ranked with a positive projected return")
    answer += (f", led by {worth.iloc[0]['address']} at {worth.iloc[0]['roi_pct']:+.1f}%." if n_worth else ".")
    if len(out):
        answer += (f" {len(out):,} were screened out, most often for: {reasons.index[0].lower()} "
                   f"({int(reasons.iloc[0]):,}).")
    if counts["Ranked, projected loss"]:
        answer += f" {int(counts['Ranked, projected loss']):,} passed the screen but are projected to lose money."
    return {"n": n, "runs": int(df["run_id"].nunique()), "counts": counts, "reasons": reasons, "ranked": ranked,
            "worth": worth, "answer": answer,
            "median_roi": float(ranked["roi_pct"].median()) if len(ranked) else None}
