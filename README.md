# Ridgeline Property Analytics: Property Investment ROI Analyzer (Gwinnett County, GA)

A screening tool, not a guarantee of profit, an inspection, or professional advice.

## Run it on your own computer (optional)
```
python3 -m venv .venv && source .venv/bin/activate
pip install pandas numpy openpyxl pytest
python3 -m pytest -q                      # expect: 46 passed
streamlit run app.py                      # the dashboard
python3 run_scoring.py --candidates data/candidates_dev_fixture.csv --out output/scored.csv
```

## What is here
| File | What it does |
|---|---|
| `ridgeline_core/arv.py` | Resale value (ARV) from neighborhood comps. Reference implementation, 17 tests. |
| `ridgeline_core/rules.py` | Renovation estimate (age tiers, 15% contingency on likely) and the charter ROI formula. |
| `ridgeline_core/pipeline.py` | Candidate file in, one scored row per candidate out: address join, status and reason, price screen, ROI, ranking. |
| `assumptions.json` | Every approved number (35,000 spread, 0.75%/month for 6 months, 8% selling, bands). Edited by hand. |
| `run_scoring.py` | Command-line runner. |
| `app.py` | The dashboard (Streamlit): upload a candidate CSV, see the ranking, top 5 and how each property was evaluated. |
| `build_county_db.py` | Rebuilds `data/gwinnett.db` from the two county spreadsheets. Run only when the county republishes. |
| `DEPLOY.md` | How to put the app on the web with no terminal. |
| `data/gwinnett.db.gz` | The full single-family county database (246,806 parcels), compressed. |
| `data/gwinnett_extract.db` | 999-parcel DEVELOPMENT extract used by the tests. |
| `data/candidates_dev_fixture.csv` | DEVELOPMENT candidates: real parcel addresses, SYNTHETIC asking prices. Not real listings. |

## ROI (charter formula)
`ROI % = (ARV - selling - asking - renovation - holding) / (asking + renovation) x 100`, with selling = 8% of ARV,
holding = 0.75% of asking x 6 months, renovation = likely band x sq ft x 1.15. The $35,000 price screen is a filter, not profit:
a property can pass it and still have a negative ROI. Excluded: acquisition-side closing costs and renovation beyond the contingency.

## Statuses and reasons
`ranked`, `eliminated`, `insufficient_comps`, `unmatched`; reasons `below_min_spread`, `fewer_than_3_comps`, `no_size_on_record`,
`no_building_record`, `no_neighborhood`, `index_unavailable`, `unmatched_address`, `ambiguous_address`.
PROPOSED, awaiting approval: `invalid_input` (blank or non-numeric asking price).

## Known limits
Renovation bands rest on one contractor and are not validated. The 65.9% back-test figure is exploratory, not the graded result.
Building size and year built come from the 2026 county snapshot.
