"""Tiny synthetic county database for pipeline tests (clearly NOT real data).
Flat market: every comp sells at exactly $200/sq ft, so the ARV of a 1,600 sq ft subject is exactly $320,000."""
import sqlite3
import pandas as pd

AS_OF = "2026-05-05"
BG_DAYS = [d.date().isoformat() for d in pd.date_range("2023-01-01", "2026-05-04", freq="D")]


def build_db(path, parcels, sales=()):
    """parcels: (lrsnum, address, zip, sqft, year_built, neighborhood); sqft/year None = no value;
    year_built == 'NOBUILD' = no building row at all. sales: (lrsnum, 'YYYY-MM-DD', amount)."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE parcels (lrsnum INTEGER PRIMARY KEY, pin TEXT, address TEXT, zip TEXT, neighborhood INTEGER);
        CREATE TABLE buildings (lrsnum INTEGER PRIMARY KEY, year_built INTEGER, fin_sqft INTEGER, cond_desc TEXT);
        CREATE TABLE sales (lrsnum INTEGER, sale_date DATE, amount INTEGER, slot INTEGER);""")
    allp = list(parcels) + [(900, "1 BACKGROUND WAY", "30000", 1600, 1990, 999)]
    for lrs, addr, z, sqft, yb, nb in allp:
        con.execute("INSERT INTO parcels VALUES (?,?,?,?,?)", (lrs, f"P{lrs}", addr, z, nb))
        if yb != "NOBUILD":
            con.execute("INSERT INTO buildings VALUES (?,?,?,?)", (lrs, yb, sqft, "NORMAL FOR AGE"))
    for d in BG_DAYS:                                   # daily background sales so the price index exists
        con.execute("INSERT INTO sales VALUES (900,?,320000,1)", (d,))
    for lrs, d, amt in sales:
        con.execute("INSERT INTO sales VALUES (?,?,?,1)", (lrs, d, amt))
    con.commit(); con.close()
    return path


def three_comps(nb_parcels=((2, 1700), (3, 1800), (4, 1500))):
    """Three comps in neighborhood 100 sold at exactly $200/sq ft in the last 12 months."""
    dates = ["2025-09-01", "2025-10-01", "2025-12-01"]
    return [(lrs, d, 200 * sq) for (lrs, sq), d in zip(nb_parcels, dates)]


def base_parcels():
    return [(1, "100 MAIN ST", "30078", 1600, 1990, 100),     # the subject
            (2, "2 COMP RD", "30078", 1700, 1990, 100),
            (3, "3 COMP RD", "30078", 1800, 1990, 100),
            (4, "4 COMP RD", "30078", 1500, 1990, 100)]
