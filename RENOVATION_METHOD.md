# Renovation estimate: method, evidence and limits

Written 2026-10-20. Model version **bands-v1** (approved version one). This file says what the renovation estimate is,
what it rests on, and what it cannot do yet.

## 1. Audit of the estimator as it stood

| Finding | Effect |
|---|---|
| One dollar-per-square-foot rate for the whole house, chosen only by age tier (under 20 years / 20 or more). | A cosmetic refresh and a gut job on the same-size, same-age house get the same budget. |
| Rates come from **one contractor's ranges** (`assumptions.json` → `reno_per_sqft`), with no source date or geography recorded. | No way to tell how current or local they are. |
| The low rate is identical for both age tiers ($19.4/sq ft). | The low figure ignores age entirely. |
| Contingency is a flat 15%, on the likely figure only. | It does not change with condition or with how much is known. |
| Low and high are separate rates, not derived from a scope. | The range is the contractor's, not a measured spread. |
| The county condition rating (`cond_desc`) was loaded but not used. | Poor-condition houses got the same budget as typical ones. |
| **Never validated:** no completed renovation cost exists anywhere in the project. | Nobody knows how accurate it is. The "69% within 15%" figure is about the **resale value**, not renovation. |

What was right: it never added costs it could not justify, applied the contingency once, kept the money unrounded,
and showed the "Pre-walkthrough screening estimate, not a construction estimate" notice.

## 2. What changed (this version)

- **Ranking unchanged.** Scoring still uses the approved band model (likely + 15%). Without completed-project data
  there is no evidence-based replacement, and changing the approved numbers would be a guess.
- **Base, contingency and total are shown separately** for the low, expected and high scenarios
  (`ridgeline_core/renovation.py: model_scenarios`). Under the approved rule, contingency applies to the expected
  figure only.
- **What drives the cost** is spelled out: square feet [county record] × rate [model assumption], age tier, and the
  county condition rating [county record].
- **Risk flags** (`risk_flags`) come from county facts and never add cost:
  - Roof, HVAC and water heater: always "check", because no replacement dates exist; "elevated" at or past the
    approved 20-year age tier.
  - Electrical and plumbing: "check" at or past 20 years.
  - Overall condition: "elevated" when the assessor rates it below normal, poor or very poor.
  - Water damage and structure: "unknown" without an inspection.

  No new age thresholds were invented; the only cutoff used is the approved 20-year tier.
- **Your scope (optional):** 18 repair categories with **no default prices**. You enter low / expected / high and a
  source from your inspection or quotes:
  - each category counts once (duplicates are refused), and blank rows are ignored
  - your contingency % is applied once to each scenario's base
  - categories you leave blank count as $0, and the app says so
- **Deal outcomes for each scenario** (`deal_outcomes`) use the charter formula. Closing and financing costs are
  your entries (default 0 = not included) and reduce profit; the ROI denominator stays asking + renovation. The app
  warns when the verdict flips: for example, profitable at expected but a loss at high.
- **Renovation confidence** (`confidence`): a label, not a percentage, because nothing has been calibrated. It is
  separate from the resale value and ROI.
  - **Insufficient data:** no living area or year built on record.
  - **Low:** the bands only, or your scope without sources.
  - **Moderate:** an itemized scope where every row has a source, OR the bands validated on at least 30 completed
    projects.
  - **High:** a sourced scope based on an inspection AND validation on at least 30 completed projects.

  Today every property is **Low** unless you enter a sourced scope, because the validation count is 0.
- **Validation metrics** (`ridgeline_core/reno_validation.py`, fed from the evidence database below; run
  `python -m ridgeline_core.reno_validation`):
  - Predicted, quoted, verified and actual costs are kept in separate columns.
  - A row is scored only if the actual cost has documentary evidence (invoice, closing statement, contractor final
    bill, lender draw records, receipts) and the project finished after the estimate was made.
  - Duplicates and hindsight rows are excluded, with the reason recorded.
  - Metrics: MAE, median absolute error, mean error, MAPE (skipping actual costs under $1,000), share within 10/15/20%
    with 90% Wilson intervals, range coverage alongside range width, results by group, and a time-based split.
  - With fewer than 10 usable projects it reports "insufficient data". **It currently holds 0 records.**
- **Reliability box (resale value):** recomputed from `backtest_results/pairs_full.csv` on every load and checked
  against the recorded summary:
  - 69.2% = 2,595 / 3,749 valued houses
  - 53.9% = 2,595 / 4,817 houses tested, counting the 1,068 unvalued ones as misses

  Both are verified. The box says plainly that they measure the resale value, not renovation.

## 3. Data that actually powers the estimate

| Input | Source | Date | Geography | Limits |
|---|---|---|---|---|
| Living area, year built, condition rating | Gwinnett County assessor files, loaded by `build_county_db.py` | 2026 snapshot | Gwinnett County | Snapshot, not the condition at purchase; no bedrooms, bathrooms or system ages |
| $/sq ft bands (low / likely / high by age tier) | One contractor's ranges, `assumptions.json` | Not recorded | Not recorded | Unvalidated; one source |
| Contingency 15% | Approved assumption | — | — | Not tied to condition or evidence |
| Your scope, closing and financing costs | Your entries | When entered | The property | Only as good as the quote or inspection behind them |

No external price feed, cost guide or contractor database is used. No local price in this app has been verified
locally.

## 4. What still needs better data

1. **Completed-project costs.** Your own or a partner's past flips (invoices, draw records, closing statements) entered
   as verified final costs in the evidence database (see section 6). This is the only way to measure accuracy, calibrate the bands and earn a rating
   above Low. About 30 projects are needed before the rating can rise.
2. **Item-level local prices.** Candidate providers (check cost and terms before relying on them):
   - **RSMeans (Gordian):** itemized construction costs with city cost indexes that include Atlanta; paid subscription.
   - **Craftsman National Construction Estimator:** item costs with area modification factors; paid book/software.
   - **Contractor quotes for the property:** the best evidence for a single deal; enter them in "Your scope".
   - Free national cost guides (for example homeowner cost-guide websites) are self-reported and national; they would
     at most be a last-resort benchmark.
3. **System ages and permits.** Gwinnett County building permits (public search) could show roof, HVAC or remodel
   permits and dates, which would raise or lower the risk flags. A permit is not proof of cost.
4. **Bedrooms, bathrooms, layout.** Not in the current database. The county's improvement file may carry more fields
   than `build_county_db.py` keeps; that is worth checking before buying data.
5. **Inspection findings.** No structured inspection input exists beyond "My scope is based on a completed inspection".

## 5. Model versions

| Version | Date | Change | Evidence |
|---|---|---|---|
| bands-v1 | approved version one | Age-tier $/sq ft bands, 15% contingency on likely | One contractor's ranges; not validated |

Any change to the bands or contingency should get a new version here, be tested on completed projects that were not
used to choose it (`time_split`), and be reversible by restoring the previous `assumptions.json`.

## 6. Renovation evidence database (added 2026-10-20)

`ridgeline_core/evidence.py` creates `data/renovation_evidence.db` (SQLite) on first use. The app writes to it from the
"Renovation evidence" part of each property's detail panel.

| Table | Holds | Rules |
|---|---|---|
| `properties` | One row per county parcel number (pin) | Created only when something is saved |
| `property_facts` | Living area, year built, bedrooms, bathrooms, property type | Every value has a source and date, and is `verified` or `unverified`; two different verified values are flagged as a conflict |
| `inspections`, `component_findings` | Condition of 13 components (roof, HVAC, water heater, kitchen, bathrooms, flooring, plumbing, electrical, foundation, windows and doors, siding, interior walls, moisture) | Good / minor repair / major repair / replacement / **unknown** (the default, never guessed); a verified age requires its source |
| `estimates`, `estimate_lines` | Predictions: model or your scope, low / expected / high totals, per-category lines with optional materials, labor and overhead | Predictions only; never mixed with quotes or actual costs |
| `cost_records` | Quotes, invoices and final costs by category, with optional materials / labor / overhead | Parts must add up to the amount; `verified` needs an evidence reference; **only `final` + `verified` rows count as actual outcomes** |
| `price_benchmarks` | Item-level unit prices (low / typical / high), what they include (labor, materials, installation, demolition, disposal, permits, overhead), geography, price date, source, reference, license note, geographic adjustment, evidence level | A row is refused unless every provenance field is filled; evidence level is one of published national / published regional / local quote / verified local. **Currently empty.** |

**Feedback.**
- The baseline is the newest estimate recorded **before** the first verified final cost; later estimates could use
  hindsight and are ignored.
- Per category and in total it reports the dollar error, absolute error, percentage error (only when the actual cost is
  at least $1,000) and whether the actual fell inside the predicted range.
- Category figures are base costs; the total includes the contingency.
- Categories that cost money but were not estimated are listed as missed scope.

**Recurring bias** (`bias_report`): the median error and the share underestimated by category, across properties, from
verified final costs only. Categories with fewer than 10 comparisons show "insufficient data".

**Nothing recalibrates automatically.** Any change to the bands needs a new model version (section 5) and a test on
projects not used to choose it.

**Repair-need probabilities** (`need_rates`): the share of inspected components needing major repair or replacement.
It is reported only once a component has at least 30 inspected cases, and checked on later inspections (Brier score)
with a cutoff date. Until then the app shows risk flags, not probabilities. Today every component is "insufficient data".

**Completeness report.** For each property it lists every fact and component as one of: verified, county record,
unverified, conflicting, missing, or unknown (not inspected). The open items are ranked by how much they can move a
renovation budget: structure, moisture, roof, HVAC, kitchen and bathrooms first. That ranking is a suggested collection
order, not a measured one.

**Persistence.** On Streamlit Community Cloud the app's files are erased when it restarts, so the database does too.
Use **Renovation evidence backup** in the sidebar: download the JSON after each session and upload it to restore. When
running locally, the database file stays in `data/`. It is excluded from git; keep the JSON backup somewhere safe, or
commit a copy deliberately.

**Pricing sources: research status.** RSMeans (Gordian) and Craftsman's National Construction Estimator were
checked on 2026-10-20. Neither site could be reached from the build environment (both hosts failed to resolve), so
their data access, price and license terms are **not verified**. Before loading any of their figures:
1. confirm the current price and format (book, software, online)
2. confirm whether the license allows storing unit costs in your own database and showing them in an app
3. record the edition and the license note on every benchmark row

No benchmark has been loaded, and no price in the app comes from either source.

## 7. How to start collecting completed-project data

1. **Before each project:** open the property, enter your scope from the contractor's quote, and click
   **Record your scope estimate now**. Do this before work starts; that recording is what later gets graded.
2. **Save the inspection.** Set each component you actually saw, leaving the rest unknown, and add receipt- or
   label-backed ages.
3. **As bills arrive:** add them as `invoice` rows.
4. **When the job is done:** add one `final` row per category, tick **verified**, and give the invoice number or
   file name as evidence.
5. **Download the evidence backup** after each session.
6. **Past flips are worth entering too, with one exception:** their final costs are real data, but an estimate
   recorded today for a finished job is hindsight and is not graded. Only estimates recorded before the final costs
   count.
7. **After about 10 graded projects,** feedback by category becomes meaningful; **after about 30**, the overall
   metrics and the renovation confidence rating can move above Low.
