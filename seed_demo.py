"""Create the demo account and a few saved runs to explore, using SYNTHETIC asking prices.

    DATABASE_URL=mysql://... DEMO_PASSWORD='...' python seed_demo.py

On iscs2, where students cannot run one-off commands, set DEMO_PASSWORD on the app instead
(`dokku config:set <app> DEMO_PASSWORD=...`): the app then seeds once when it starts (app.py ->
seed_from_environment). The demo user (demo@ridgeline.example) is created only if it does not exist; an existing one is reused unchanged.
The password comes from the DEMO_PASSWORD environment variable, never from this file. Each run is created only if
the demo user has no run with that name yet, so running the script twice adds nothing. Addresses are real parcels
from the development extract (data/gwinnett_extract.db); every asking price is made up (living area x a random
price per square foot) and the run names say so. No real listings are used.
"""
from __future__ import annotations

import os
import random
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import select, update

from accounts import normalize_email, register
from db import database_url, make_engine, runs, users
from ridgeline_core.arv import load_params
from ridgeline_core.pipeline import score_batch
from runs import save_run

ROOT = Path(__file__).resolve().parent
EXTRACT = ROOT / "data" / "gwinnett_extract.db"
DEMO_EMAIL = "demo@ridgeline.example"
SEED = 20261113
N_RUNS, ROWS_PER_RUN = 4, 10


def demo_user(engine, password: str) -> int:
    """The demo user's id, creating the account (flagged is_demo) if it does not exist."""
    with engine.connect() as con:
        row = con.execute(select(users.c.id).where(users.c.email == normalize_email(DEMO_EMAIL))).first()
    if row:
        return int(row.id)
    uid = register(engine, DEMO_EMAIL, password, "Demo investor")["id"]
    with engine.begin() as con:
        con.execute(update(users).where(users.c.id == uid).values(is_demo=1))
    return uid


def synthetic_lists(extract: Path = EXTRACT) -> list[tuple[str, pd.DataFrame]]:
    """N_RUNS candidate lists of ROWS_PER_RUN real extract addresses with SYNTHETIC asking prices (same every time)."""
    con = sqlite3.connect(extract)
    try:
        parcels = con.execute("SELECT p.address, p.zip, b.fin_sqft FROM parcels p JOIN buildings b USING (lrsnum) "
                              "WHERE b.fin_sqft > 0 ORDER BY p.lrsnum").fetchall()
    finally:
        con.close()
    rng = random.Random(SEED)
    picked = rng.sample(parcels, N_RUNS * ROWS_PER_RUN)
    out = []
    for k in range(N_RUNS):
        chunk = picked[k * ROWS_PER_RUN:(k + 1) * ROWS_PER_RUN]
        c = pd.DataFrame([{"address": a, "zip": str(z), "asking_price": str(int(round(sq * rng.uniform(110, 230), -3)))}
                          for a, z, sq in chunk])
        c.insert(0, "row_id", range(1, len(c) + 1))
        out.append((f"SYNTHETIC demo list {k + 1} of {N_RUNS} (synthetic asking prices, development extract)", c))
    return out


def seed(engine, password: str, extract: Path = EXTRACT) -> dict:
    """Create what is missing; return {"user_id", "created", "skipped"}."""
    uid = demo_user(engine, password)
    with engine.connect() as con:
        existing = {r.name for r in con.execute(select(runs.c.name).where(runs.c.user_id == uid))}
    params = load_params(ROOT / "assumptions.json")
    created, skipped = [], []
    for name, candidates in synthetic_lists(extract):
        if name in existing:
            skipped.append(name)
            continue
        save_run(engine, uid, name, score_batch(candidates, extract, params))
        created.append(name)
    return {"user_id": uid, "created": created, "skipped": skipped}


def seed_from_environment(engine) -> dict | None:
    """Seed when DEMO_PASSWORD is set (the app calls this once per server process, because students cannot run
    one-off commands on iscs2). Returns the result, or None when DEMO_PASSWORD is not set. Never raises: a problem is
    printed to the log (without the password) and the app carries on."""
    password = os.environ.get("DEMO_PASSWORD")
    if not password or engine is None:
        return None
    try:
        result = seed(engine, password)
    except Exception as e:                                   # the app must keep working whatever happens here
        print(f"Demo seed skipped: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    print(f"Demo seed: user id {result['user_id']}, created {len(result['created'])} run(s), "
          f"skipped {len(result['skipped'])} that already existed.")
    return result


def main(argv=None) -> int:
    password = os.environ.get("DEMO_PASSWORD")
    if not password:
        print("Set DEMO_PASSWORD in the environment first (it is never read from the code).", file=sys.stderr)
        return 2
    url = database_url()
    if not url:
        print("Set DATABASE_URL in the environment first.", file=sys.stderr)
        return 2
    result = seed(make_engine(url), password)
    print(f"Demo user id {result['user_id']}: created {len(result['created'])} run(s), "
          f"skipped {len(result['skipped'])} that already existed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
