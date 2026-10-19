"""Builds data/candidates_dev_fixture.csv for DEVELOPMENT ONLY.
Addresses are real parcels from the 999-parcel extract; asking prices are SYNTHETIC (a seeded fraction of the
estimated resale value). Never present this file as real listings or as evidence of how the model performs."""
import random
import pandas as pd
from pathlib import Path
from ridgeline_core.arv import estimate_arv, load_county, load_params

root = Path(__file__).resolve().parent
db = root / "data" / "gwinnett_extract.db"
params = load_params(root / "assumptions.json")
data = load_county(db)
as_of = pd.Timestamp(params["as_of"])
import sqlite3
con = sqlite3.connect(db)
parcels = pd.read_sql("SELECT p.lrsnum, p.address, p.zip FROM parcels p", con)
rng = random.Random(20261019)
valued, no_comps = [], []
for r in parcels.itertuples():
    arv, _, _, st = estimate_arv(r.lrsnum, as_of, params, data)
    (valued if st == "ok" else no_comps).append((r, arv))
rng.shuffle(valued); rng.shuffle(no_comps)
rows = []
for (r, arv), f in zip(valued[:15], [0.55, 0.60, 0.65, 0.68, 0.70, 0.72, 0.75, 0.78, 0.80, 0.82, 0.85, 0.88, 0.90, 0.95, 1.02]):
    rows.append((r.address, r.zip, round(arv * f, -3)))
for r, _ in no_comps[:2]:                                    # likely 'insufficient comps' rows
    rows.append((r.address, r.zip, 300_000))
rows.append((valued[15][0].address.replace(" ", "  ", 1).title() + ", Lawrenceville, GA", valued[15][0].zip, round(valued[15][1] * 0.7, -3)))  # city text + odd casing still match exactly
rows.append(("1 Nonexistent Rd", "30043", 250_000))          # unmatched
rows.append((valued[16][0].address, valued[16][0].zip, ""))  # blank price
df = pd.DataFrame(rows, columns=["address", "zip", "asking_price"])
df["note"] = "DEV FIXTURE: real parcel address, SYNTHETIC asking price"
df.to_csv(root / "data" / "candidates_dev_fixture.csv", index=False)
print(len(df), "rows written")
