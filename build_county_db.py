"""Builds data/gwinnett.db (the full single-family county database) from the two county spreadsheets.
Run once when the county republishes its files; the app only reads the finished database.
Owner names and mailing addresses are deliberately NOT loaded."""
import sqlite3, sys
from pathlib import Path
import numpy as np
import pandas as pd

OWN, IMP = sys.argv[1], sys.argv[2]
OUT = Path(sys.argv[3] if len(sys.argv) > 3 else "data/gwinnett.db")
FLOOR, MIN_SQFT, DAYS, MIN_SALES = 50_000, 300, 90, 30

read = lambda p: pd.read_pickle(p) if str(p).endswith('.pkl') else pd.read_excel(p)
own = read(OWN)
imp = read(IMP)
sfr = own[own["PROPCLAS"] == 101].copy()                                    # single-family residence
b = imp.sort_values("AreaNum").groupby("LRSNum").first().reset_index()[["LRSNum", "YrBuilt", "FinSize", "CondDesc"]]

parcels = pd.DataFrame({"lrsnum": sfr["LRSNum"].astype("int64"), "pin": sfr["PIN"].astype(str),
                        "address": sfr["PROPERTYSTREET"].astype(str), "zip": sfr["PROPERTYZip"].astype(str).str.replace(r"\.0$", "", regex=True),
                        "neighborhood": sfr["Public_NeiNum"].astype("Int64")})
bsel = b[b["LRSNum"].isin(sfr["LRSNum"])].dropna(subset=["YrBuilt"])
buildings = pd.DataFrame({"lrsnum": bsel["LRSNum"].astype("int64"), "year_built": bsel["YrBuilt"].astype("Int64"),
                          "fin_sqft": bsel["FinSize"].round().astype("Int64"), "cond_desc": bsel["CondDesc"]})
excluded = pd.DataFrame({"lrsnum": sfr.loc[~sfr["LRSNum"].isin(buildings["lrsnum"]), "LRSNum"].astype("int64"), "reason": "no building record"})
rows = []
for k in (1, 2, 3):
    x = sfr[["LRSNum", f"SALE{k}D", f"SALE{k}AMT"]].copy(); x.columns = ["lrsnum", "sale_date", "amount"]; x["slot"] = k; rows.append(x)
sales = pd.concat(rows).dropna(subset=["sale_date"]); sales = sales[sales["amount"] > 0]
sales["sale_date"] = pd.to_datetime(sales["sale_date"]).dt.strftime("%Y-%m-%d")
sales = sales.astype({"lrsnum": "int64", "amount": "int64"})

# price_index(day, ppsf): median $/sq ft of sales >= floor on homes > MIN_SQFT in the 90 days strictly before each day
sz = buildings.set_index("lrsnum")["fin_sqft"]
s = sales.copy(); s["sf"] = s["lrsnum"].map(sz); s = s[(s["amount"] >= FLOOR) & (s["sf"] > MIN_SQFT)]
s["date"] = pd.to_datetime(s["sale_date"]); s = s.sort_values("date")
dates = s["date"].values.astype("datetime64[D]"); pp = (s["amount"] / s["sf"]).values
days = pd.date_range("2018-01-01", s["date"].max() + pd.Timedelta(days=1), freq="D"); idx = []
for d in days:
    d64 = np.datetime64(d.date()); lo = np.searchsorted(dates, d64 - np.timedelta64(DAYS, "D"), "left"); hi = np.searchsorted(dates, d64, "left")
    idx.append(float(np.median(pp[lo:hi])) if hi - lo >= MIN_SALES else None)
price_index = pd.DataFrame({"day": days.strftime("%Y-%m-%d"), "ppsf": idx})

OUT.parent.mkdir(parents=True, exist_ok=True)
if OUT.exists(): OUT.unlink()
con = sqlite3.connect(OUT)
con.executescript("""
CREATE TABLE parcels (lrsnum INTEGER PRIMARY KEY, pin TEXT, address TEXT, zip TEXT, neighborhood INTEGER);
CREATE TABLE buildings (lrsnum INTEGER PRIMARY KEY, year_built INTEGER, fin_sqft INTEGER, cond_desc TEXT);
CREATE TABLE sales (lrsnum INTEGER, sale_date DATE, amount INTEGER, slot INTEGER);
CREATE TABLE excluded (lrsnum INTEGER, reason TEXT);
CREATE TABLE price_index (day TEXT PRIMARY KEY, ppsf REAL);
CREATE INDEX idx_sales_lrs ON sales(lrsnum);""")
def put(name, df):
    df = df.astype(object).where(df.notna(), None)
    con.executemany(f"INSERT INTO {name} VALUES ({','.join('?'*df.shape[1])})", df.values.tolist())
for n, d in (("parcels", parcels), ("buildings", buildings), ("sales", sales[["lrsnum", "sale_date", "amount", "slot"]]), ("excluded", excluded), ("price_index", price_index)): put(n, d)
con.commit()
for n in ("parcels", "buildings", "sales", "excluded", "price_index"): print(n, con.execute(f"select count(*) from {n}").fetchone()[0])
print("newest sale:", con.execute("select max(sale_date) from sales").fetchone()[0], "| null index days:", con.execute("select count(*) from price_index where ppsf is null").fetchone()[0])
con.execute("VACUUM"); con.close()
print("size MB:", round(OUT.stat().st_size / 1e6, 1))
