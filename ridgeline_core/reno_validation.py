"""Validation of renovation estimates against completed projects (data/renovation_records.csv).

The ledger keeps predicted, quoted, verified and actual costs in separate columns. Only rows whose actual cost is
backed by documentary evidence, completed after the estimate was made, are scored. With fewer than `min_n` usable
rows the result is "insufficient data", never a made-up accuracy figure.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "renovation_records.csv"
COLUMNS = [
    "record_id", "pin", "address", "estimated_on", "model_version", "fin_sqft", "year_built", "cond_desc",
    "pred_low", "pred_expected", "pred_high", "pred_contingency", "confidence", "sources",
    "inspection_findings", "quoted_cost", "quote_date", "quote_source",
    "verified_cost", "verified_source",
    "actual_cost", "actual_completed_on", "actual_evidence", "discrepancy_reason",
]
EVIDENCE = {"invoice", "closing statement", "contractor final bill", "lender draw records", "receipts"}
SMALL_COST = 1_000          # percentage errors are not computed for actual costs below this (they blow up near zero)


def load_records(path: Path = LEDGER) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"renovation ledger is missing column(s): {', '.join(missing)}")
    return df


def usable_records(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(usable, excluded with a `why` column). A row is usable only if it has a prediction, a positive actual cost
    with documentary evidence, and the project was completed after the estimate (no hindsight)."""
    d = df.copy()
    for c in ("pred_low", "pred_expected", "pred_high", "actual_cost"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    for c in ("estimated_on", "actual_completed_on"):
        d[c] = pd.to_datetime(d[c], errors="coerce")
    why = pd.Series("", index=d.index)

    def flag(mask, text):
        nonlocal why
        why = why.where(~(mask & (why == "")), text)

    flag(d["pred_expected"].isna(), "no predicted expected cost")
    flag(~(d["actual_cost"] > 0), "no positive actual cost")
    flag(~d["actual_evidence"].str.strip().str.lower().isin(EVIDENCE), "actual cost not backed by an invoice or similar evidence")
    flag(d["estimated_on"].isna() | d["actual_completed_on"].isna(), "missing estimate or completion date")
    flag(d["actual_completed_on"] <= d["estimated_on"], "completed before the estimate was made (hindsight)")
    flag(d.duplicated(["pin", "estimated_on"], keep="first") & (d["pin"] != ""), "duplicate of an earlier row")
    return d[why == ""], d[why != ""].assign(why=why[why != ""])


def wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """90% Wilson interval for a proportion (honest about small samples)."""
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def metrics(df: pd.DataFrame, min_n: int = 10) -> dict:
    """MAE, median absolute error, MAPE, share within 10/15/20%, range coverage and width, with sample sizes."""
    use, excl = usable_records(df)
    n = len(use)
    out = {"n": n, "excluded": len(excl), "excluded_reasons": excl["why"].value_counts().to_dict() if len(excl) else {}}
    if n < min_n:
        out["status"] = f"insufficient data: {n} usable completed project(s), at least {min_n} needed"
        return out
    err = use["pred_expected"] - use["actual_cost"]
    pct_ok = use["actual_cost"] >= SMALL_COST
    ape = (err.abs() / use["actual_cost"])[pct_ok]
    out.update(status="ok", mae=float(err.abs().mean()), median_abs_error=float(err.abs().median()),
               mean_error=float(err.mean()), mape=float(ape.mean()) if len(ape) else None,
               mape_excluded_small=int((~pct_ok).sum()))
    for t in (0.10, 0.15, 0.20):
        k = int((ape <= t).sum())
        out[f"within_{int(t*100)}"] = {"share": k / len(ape) if len(ape) else None, "k": k, "n": len(ape), "ci90": wilson(k, len(ape))}
    has_range = use["pred_low"].notna() & use["pred_high"].notna()
    r = use[has_range]
    inside = int(((r["pred_low"] <= r["actual_cost"]) & (r["actual_cost"] <= r["pred_high"])).sum())
    out["range"] = {"n": len(r), "coverage": inside / len(r) if len(r) else None, "ci90": wilson(inside, len(r)),
                    "median_width_pct_of_expected": float(((r["pred_high"] - r["pred_low"]) / r["pred_expected"]).median()) if len(r) else None,
                    "share_low_below_actual": float((r["pred_low"] <= r["actual_cost"]).mean()) if len(r) else None,
                    "share_high_above_actual": float((r["pred_high"] >= r["actual_cost"]).mean()) if len(r) else None}
    return out


def time_split(df: pd.DataFrame, cutoff: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(calibration, test): projects completed before the cutoff may be used to adjust the model; only estimates made
    on or after the cutoff are scored, so nothing learned from them leaks into the test."""
    est = pd.to_datetime(df["estimated_on"], errors="coerce")
    done = pd.to_datetime(df["actual_completed_on"], errors="coerce")
    c = pd.Timestamp(cutoff)
    return df[done < c], df[est >= c]


def by_group(df: pd.DataFrame, column: str, min_n: int = 10) -> dict:
    """Metrics per group (for example age band or condition); groups below min_n report insufficient data."""
    return {str(k): metrics(g, min_n) for k, g in df.groupby(column)}


if __name__ == "__main__":                                  # python -m ridgeline_core.reno_validation
    import json
    print(json.dumps(metrics(load_records()), indent=2, default=str))
