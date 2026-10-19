"""Score a candidate list.  Usage:
    python3 run_scoring.py --candidates data/candidates_dev_fixture.csv --out output/scored.csv
"""
import argparse
from pathlib import Path

from ridgeline_core.arv import load_params
from ridgeline_core.pipeline import load_candidates, score_batch

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--db", default=str(ROOT / "data" / "gwinnett_extract.db"))
    ap.add_argument("--assumptions", default=str(ROOT / "assumptions.json"))
    ap.add_argument("--out", default=str(ROOT / "output" / "scored.csv"))
    ap.add_argument("--as-of", default=None, help="override the as-of date (default: day after the newest sale loaded)")
    a = ap.parse_args(argv)
    scored = score_batch(load_candidates(a.candidates), a.db, load_params(a.assumptions), a.as_of)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(a.out, index=False)
    print(f"{len(scored)} rows scored -> {a.out}")
    print(scored["status"].value_counts().to_string())
    return scored


if __name__ == "__main__":
    main()
