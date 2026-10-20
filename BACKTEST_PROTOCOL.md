# Back-test protocol: resale value (ARV) against repeat sales

Written and committed **before** `backtest.py` existed or was run. Any change to the numbers below after the
graded run must be reported as a deviation, not silently applied.

## Question
When `estimate_arv` values a house as of the day it sold, how often does that value land within 15% of the price
the same house sold for next time?

## Fixed settings
| Setting | Value |
|---|---|
| Random seed | `20261020` |
| Generator | `numpy.random.default_rng(20261020).permutation(n)` |
| Database (graded run) | `data/gwinnett.db.gz` (decompressed), 246,806 parcels, 469,413 sale rows |
| Data cutoff | newest sale in the database: **2026-05-04**. No sale after it exists or is used. |
| Price index | stored `price_index` table, 2018-01-01 to 2026-05-05 (the day after the cutoff) |
| ARV parameters | the ARV keys in `assumptions.json` as committed: comp_floor 50,000; size_band 0.75 to 1.25; min_comps 3; window_months 12 then 24; arv_percentile 0.60; index_days 90; index_min_sales 30; index_min_sqft 300 |
| Success threshold | abs(ARV - second price) / second price <= 0.15 |

## Building the pairs
1. Read every row of `sales`. Collapse rows of the same house on the same day: identical amounts become one sale.
   If one house has **different** amounts on the same day, that sale is ambiguous and is marked unusable.
2. Order each house's distinct sale dates. A candidate pair is two **consecutive** sales of the same house
   (sale k and sale k+1). Pairs that skip a sale are not formed. A pair touching an ambiguous sale is dropped.
3. A pair qualifies only if **all** of these hold:
   - 30 <= days between the two sales <= 548
   - second price > first price
   - first price >= $50,000
   - the house was built before the first sale: `year_built` known and `year_built < year of first sale`
     (year built is a whole year, so a house built in the same year as the sale cannot be shown to predate it)
   - second price / first price <= 2.0
   - first sale on or after **2018-01-01** (amendment 1, below)
4. Sort qualifying pairs by (lrsnum, first sale date) so the order does not depend on how SQLite returns rows.

## Split
`perm = default_rng(20261020).permutation(n)`. Positions `perm[: n // 2]` are the **exploration half**;
positions `perm[n // 2 :]` are the **held-out half**. Only the held-out half is graded. The exploration half is
never valued by `backtest.py`. Pairs are split individually, as asked; one house can have pairs in both halves.

## Valuing each held-out pair
`estimate_arv(lrsnum, as_of=first sale date, params, data)`.

No future information: `estimate_arv` uses only comps sold strictly before `as_of`, only index values for days
up to `as_of` (each built from sales strictly before its day), and never the subject's own sales. `backtest.py`
also checks every returned comp is dated before `as_of` and stops if one is not. Tests prove that adding sales on
or after `as_of`, including the second sale of the pair, does not change the estimate.

## Metrics (fixed in advance; as amended 2026-10-20)
- **(a) Headline** = held-out pairs within 15% / held-out pairs the tool could value (status `ok`).
- **(b) Coverage** = held-out pairs the tool could value / all held-out pairs.
- **(c) All-in rate** = held-out pairs within 15% / **all** held-out pairs; a pair with no estimate is a miss.
- **(d)** Held-out sample size; median absolute percentage error and median signed percentage error among valued
  pairs. Counts by status are also reported.

## Known limits (stated before the run)
- Building size, year built and neighborhood come from the 2026 county snapshot, not the date of the first sale.
  A house enlarged between its two sales is valued at its later size. This cannot be fixed with the data we have.
- The index starts on 2018-01-01. Pairs whose first sale is earlier are excluded (amendment 1), so the result
  says nothing about valuations before 2018.
- A rising second price is required by the rules, so the sample leans toward houses that gained value.

## Amendment 1 (2026-10-20, before the graded run)
Decided by the project owner after the tiny development sample and **before** any accuracy on the full database
was computed.

- **Rule:** a pair is eligible only if its first sale is on or after 2018-01-01. The filter is applied with the
  other pair rules, before the seeded split, so the split is drawn from the eligible pairs only.
- **Reason:** 2018-01-01 is the first day the stored price index covers. An earlier first sale can never be
  valued (`index_unavailable`), so it measures the index's start date, not the estimate.
- **Counts on `data/gwinnett.db.gz`** (pair building only, no estimates made):

| | Pairs |
|---|---|
| Pass the original rules | 17,884 |
| Excluded: first sale before 2018-01-01 | 8,251 |
| **Eligible population** | **9,633** (first sales 2018-01-02 to 2026-03-17) |
| Exploration half | 4,816 |
| **Held-out half (graded)** | **4,817** |

- The headline metric changes from the all-in rate to the hit rate among valued pairs; the all-in rate and
  coverage are still reported (see Metrics).
- Seed, ARV parameters, data cutoff and every other rule are unchanged.

## Procedure
1. Commit this file.
2. Build `backtest.py` and its tests; run tests and a tiny sample on the 999-parcel development extract only.
3. The graded run (`python3 backtest.py --db <gwinnett.db> --full`) happens once, separately, and is not part
   of this change.

## Results of the graded run (2026-10-20)
Run once, at commit `f348222`, with `python3 backtest.py --db <decompressed data/gwinnett.db.gz> --full`.
Nothing was changed before or after the run. Files: `backtest_results/summary_full.json` (summary) and
`backtest_results/pairs_full.csv` (one row per held-out pair).

| Metric | Result |
|---|---|
| (a) Headline: within 15% among pairs the tool could value | **69.2%** (2,595 of 3,749) |
| (b) Coverage: share of the held-out sample valued | **77.8%** (3,749 of 4,817) |
| (c) All-in rate: within 15% over the whole held-out sample | **53.9%** (2,595 of 4,817) |
| (d) Held-out sample size | 4,817 |
| (d) Median absolute % error, valued pairs | 9.8% |
| (d) Median signed % error, valued pairs | -3.2% (estimates tend to sit below the next sale) |

All 1,068 unvalued pairs had fewer than 3 comps; none failed for lack of a price index.
