"""Back-test of the ARV estimate against repeat sales. Method, seed and parameters: BACKTEST_PROTOCOL.md.

    python3 backtest.py --sample 10                       # tiny check on the development extract
    python3 backtest.py --db data/gwinnett.db --full      # the graded run: held-out half, once

Each qualifying pair is valued as of its first sale date; success = ARV within 15% of the second sale price.
Only the held-out half of the seeded split is ever valued.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from ridgeline_core.arv import CountyData, estimate_arv, load_county, load_params

ROOT = Path(__file__).resolve().parent
SEED = 20261020
RULES = {
    "min_days": 30,
    "max_days": 548,
    "min_first_price": 50_000,
    "max_ratio": 2.0,
    "min_first_date": "2018-01-01",     # first day the price index covers (protocol amendment 1)
    "tolerance": 0.15,
}
PAIR_COLUMNS = ["lrsnum", "first_date", "first_price", "second_date", "second_price", "days", "ratio", "year_built"]


def load_sales(db_path) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        return pd.read_sql("SELECT lrsnum, sale_date, amount FROM sales", con)
    finally:
        con.close()


def sale_events(sales: pd.DataFrame) -> pd.DataFrame:
    """One row per house per day. Same-day rows with identical amounts collapse to one sale;
    different amounts on the same day make that sale ambiguous (amount NaN)."""
    s = sales.dropna(subset=["sale_date", "amount"]).copy()
    s["sale_date"] = pd.to_datetime(s["sale_date"]).dt.normalize()
    g = s.groupby(["lrsnum", "sale_date"])["amount"]
    ev = g.agg(["min", "max"]).reset_index()
    ev["amount"] = ev["min"].where(ev["min"] == ev["max"])
    return ev[["lrsnum", "sale_date", "amount"]].sort_values(["lrsnum", "sale_date"]).reset_index(drop=True)


def qualifying_pairs(sales: pd.DataFrame, year_built: pd.Series, rules: dict = RULES) -> pd.DataFrame:
    """Consecutive sales of the same house that pass every rule in the protocol, sorted by (lrsnum, first_date)."""
    ev = sale_events(sales)
    nxt = ev.groupby("lrsnum").shift(-1)
    p = pd.DataFrame({
        "lrsnum": ev["lrsnum"],
        "first_date": ev["sale_date"], "first_price": ev["amount"],
        "second_date": nxt["sale_date"], "second_price": nxt["amount"],
    }).dropna(subset=["second_date"])
    p = p.dropna(subset=["first_price", "second_price"])          # a pair touching an ambiguous sale is dropped
    p["days"] = (p["second_date"] - p["first_date"]).dt.days
    p["ratio"] = p["second_price"] / p["first_price"]
    p["year_built"] = p["lrsnum"].map(year_built)
    keep = (
        p["days"].between(rules["min_days"], rules["max_days"])
        & (p["second_price"] > p["first_price"])
        & (p["first_price"] >= rules["min_first_price"])
        & p["year_built"].notna()
        & (p["year_built"] < p["first_date"].dt.year)
        & (p["ratio"] <= rules["max_ratio"])
        & (p["first_date"] >= pd.Timestamp(rules["min_first_date"]))
    )
    p = p[keep].sort_values(["lrsnum", "first_date"]).reset_index(drop=True)
    return p.astype({"first_price": "int64", "second_price": "int64", "year_built": "int64"})[PAIR_COLUMNS]


def split_pairs(pairs: pd.DataFrame, seed: int = SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(exploration half, held-out half). The held-out half keeps the permutation order."""
    perm = np.random.default_rng(seed).permutation(len(pairs))
    half = len(pairs) // 2
    return pairs.iloc[perm[:half]], pairs.iloc[perm[half:]]


def value_pair(row, params: dict, data: CountyData, tolerance: float = RULES["tolerance"]) -> dict:
    as_of = pd.Timestamp(row.first_date)
    arv, comps, window, status = estimate_arv(row.lrsnum, as_of, params, data)
    late = [c for c in comps if pd.Timestamp(c["sale_date"]) >= as_of]
    if late:
        raise RuntimeError(f"comp dated on or after as-of {as_of.date()} for {row.lrsnum}: {late[0]}")
    err = (arv - row.second_price) / row.second_price if arv is not None else np.nan
    return {
        "lrsnum": row.lrsnum, "as_of": as_of.date().isoformat(), "first_price": row.first_price,
        "second_date": pd.Timestamp(row.second_date).date().isoformat(), "second_price": row.second_price,
        "status": status, "arv": arv, "comp_count": len(comps), "window_months": window,
        "pct_error": err, "hit": bool(arv is not None and abs(err) <= tolerance),
    }


def summarize(results: pd.DataFrame) -> dict:
    n = len(results)
    ok = results[results["status"] == "ok"]
    return {
        "pairs_evaluated": n,
        "headline_hit_rate_when_valued": float(ok["hit"].mean()) if len(ok) else None,
        "coverage": len(ok) / n if n else None,
        "all_in_hit_rate": float(results["hit"].mean()) if n else None,     # no estimate = miss
        "median_abs_pct_error": float(ok["pct_error"].abs().median()) if len(ok) else None,
        "median_pct_error": float(ok["pct_error"].median()) if len(ok) else None,
        "status_counts": results["status"].value_counts().to_dict(),
    }


def run(db_path, params: dict, seed: int = SEED, sample: int | None = None, data: CountyData | None = None):
    """Values the held-out half (or its first `sample` pairs). Returns (results, summary)."""
    data = data or load_county(db_path)
    pairs = qualifying_pairs(load_sales(db_path), data.subjects["year_built"])
    explore, held = split_pairs(pairs, seed)
    if sample is not None:
        held = held.head(sample)
    results = pd.DataFrame([value_pair(r, params, data) for r in held.itertuples(index=False)],
                           columns=["lrsnum", "as_of", "first_price", "second_date", "second_price", "status",
                                    "arv", "comp_count", "window_months", "pct_error", "hit"])
    summary = {
        "seed": seed, "db": str(db_path), "data_cutoff": str(data.sales["sale_date"].max().date()),
        "qualifying_pairs": len(pairs), "exploration_half": len(explore), "held_out_half": len(pairs) - len(explore),
        "sample": sample, "rules": RULES, **summarize(results),
    }
    return results, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "gwinnett_extract.db"))
    ap.add_argument("--assumptions", default=str(ROOT / "assumptions.json"))
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default=str(ROOT / "output" / "backtest"))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sample", type=int, help="value only the first N held-out pairs (a quick check)")
    mode.add_argument("--full", action="store_true", help="the graded run: every held-out pair")
    a = ap.parse_args(argv)
    if a.seed != SEED:
        print(f"WARNING: seed {a.seed} differs from the protocol seed {SEED}; this is not the graded split.")
    results, summary = run(a.db, load_params(a.assumptions), a.seed, None if a.full else a.sample)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = "full" if a.full else f"sample{a.sample}"
    results.to_csv(out / f"pairs_{tag}.csv", index=False)
    (out / f"summary_{tag}.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    return results, summary


if __name__ == "__main__":
    main()
