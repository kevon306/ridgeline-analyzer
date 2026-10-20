"""Reliability of the resale-value (ARV) estimate, computed from the graded back-test's per-pair file.

The figures are recomputed from backtest_results/pairs_full.csv every time (nothing is typed in) and checked
against the recorded summary_full.json. They describe the ARV estimate only, never the renovation estimate.

  within 15%  :  |ARV - next sale price| / next sale price <= 0.15
  hit rate among valued pairs  = hits / pairs the tool could value
  all-in hit rate              = hits / all pairs tested (pairs with no estimate count as misses)
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PAIRS = ROOT / "backtest_results" / "pairs_full.csv"
SUMMARY = ROOT / "backtest_results" / "summary_full.json"


def arv_reliability(pairs_path: Path = PAIRS, summary_path: Path = SUMMARY) -> dict | None:
    """Returns the recomputed figures plus `verified` (they match the recorded summary) and any `discrepancy`.
    None when the per-pair file is missing or unreadable."""
    try:
        r = pd.read_csv(pairs_path)
        tol = 0.15
        valued = r[r["status"] == "ok"]
        err = (valued["arv"] - valued["second_price"]) / valued["second_price"]
        hits = int((err.abs() <= tol).sum())
        tested = len(r)
    except (OSError, KeyError, ValueError):
        return None
    if tested == 0 or valued.empty:
        return None
    out = {
        "tested": tested, "valued": len(valued), "unvalued": tested - len(valued), "hits": hits, "tolerance": tol,
        "hit_rate_valued": hits / len(valued), "hit_rate_all": hits / tested,
        "median_abs_error": float(err.abs().median()), "median_signed_error": float(err.median()),
        "first_sales": (str(r["as_of"].min()), str(r["as_of"].max())), "last_second_sale": str(r["second_date"].max()),
        "unvalued_reasons": r.loc[r["status"] != "ok", "status"].value_counts().to_dict(),
        "verified": False, "discrepancy": None,
    }
    try:
        s = json.loads(Path(summary_path).read_text())
        checks = {"hit rate among valued pairs": (out["hit_rate_valued"], s["headline_hit_rate_when_valued"]),
                  "all-in hit rate": (out["hit_rate_all"], s["all_in_hit_rate"]),
                  "pairs tested": (tested, s["pairs_evaluated"]), "tolerance": (tol, s["rules"]["tolerance"])}
        bad = [f"{k}: recomputed {a:.4g}, recorded {b:.4g}" for k, (a, b) in checks.items() if abs(a - b) > 1e-9]
        out["verified"], out["discrepancy"] = (not bad), ("; ".join(bad) or None)
    except (OSError, KeyError, ValueError, TypeError):
        out["discrepancy"] = "recorded summary missing or unreadable; figures are recomputed from the per-pair file only"
    return out
