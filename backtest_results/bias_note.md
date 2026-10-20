# Why the estimates run low: exploratory follow-up (not graded)

Written 2026-10-20, after the graded run. **Exploratory only:** it re-reads `pairs_full.csv` (the 3,749 held-out pairs
the tool valued) and changes nothing in the model, the protocol or the graded result. Anything acted on from here
needs its own pre-registered test on data not used before.

Overall: median signed error −3.2%, median absolute error 9.8%, 69.2% within 15%.

| Split | Groups (median signed error · share within 15%) |
|---|---|
| Days between the two sales | 30–90: −0.2% · 78% — 91–180: −2.4% · 74% — 181–365: −4.6% · 63% — 366–548: −11.2% · 53% |
| Price gain between the sales | 0–5%: +2.9% · 79% — 5–15%: −1.5% · 79% — 15–30%: −3.2% · 66% — 30–50%: −7.0% · 61% — 50–100%: −9.2% · 57% |
| Year of the first sale | 2018: +0.5% · 2019: −1.9% · **2020: −8.2%** · **2021: −7.3%** · 2022: +1.0% · 2023: −2.6% · 2024: +2.0% · 2025: +3.3% |
| Second sale price (quartiles) | −2.7% · −4.1% · −3.2% · −3.0% (no pattern) |

## Reading
1. **Time, not price level.** The estimate is made as of the first sale and compared with a sale up to 18 months
   later. The longer the gap, the more market growth the estimate cannot see, so the error drifts negative.
   The 2020–2021 run-up shows the same effect: those years have the largest negative errors.
2. **Big jumps are the flips.** Pairs whose price rose 30% or more are most likely renovated in between. The ARV is
   meant to be an after-repair value, yet the error grows with the jump, so on the largest flips the estimate falls
   short of what renovated houses actually sold for.
3. **The test design adds some of it.** The rules keep only pairs where the price rose, which pulls the average
   error negative by construction.

## What this does not justify
No adjustment to the ARV (for example adding a market-growth factor or raising the percentile) follows from this note.
Tuning the model on the held-out half would make the 69.2% figure meaningless. A change would need a new protocol,
fixed in advance, and evaluation on sales that were not used here (for example sales after 2026-05-04 once the
county republishes).
