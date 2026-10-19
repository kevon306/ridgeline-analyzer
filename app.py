"""Ridgeline Property Analyzer: the dashboard.  Run locally with `streamlit run app.py`, or deploy on Streamlit Community Cloud.
It reads the real output of the scoring engine; nothing on screen is hard-coded."""
import gzip
import json
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from ridgeline_core.arv import load_county, load_params
from ridgeline_core.pipeline import REQUIRED_COLUMNS, load_candidates, load_parcel_index, score_batch

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Ridgeline Property Analyzer", page_icon="🏠", layout="wide")

STATUS_LABEL = {"ranked": "Ranked", "eliminated": "Eliminated", "insufficient_comps": "Could not value", "unmatched": "Could not match"}
NOTICE = "Pre-walkthrough screening estimate, not a construction estimate."


# ---------------------------------------------------------------- data and engine (loaded once, then cached)
@st.cache_resource(show_spinner="Loading Gwinnett County records (first load takes a moment)...")
def load_engine():
    full, gz, dev = ROOT / "data" / "gwinnett.db", ROOT / "data" / "gwinnett.db.gz", ROOT / "data" / "gwinnett_extract.db"
    if full.exists():
        path, label = full, "Gwinnett County single-family records, 2026 county files"
    elif gz.exists():
        path = Path(tempfile.gettempdir()) / "gwinnett.db"
        if not path.exists():
            with gzip.open(gz, "rb") as f_in, open(path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        label = "Gwinnett County single-family records, 2026 county files"
    else:
        path, label = dev, "DEVELOPMENT EXTRACT (999 parcels), not the full county"
    return load_county(path), load_parcel_index(path), label


def money(x):
    return "" if pd.isna(x) else (f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}")


def pct(x):
    return "" if pd.isna(x) else f"{x:,.1f}%"


params = load_params(ROOT / "assumptions.json")
data, index, source_label = load_engine()

# ---------------------------------------------------------------- header
st.markdown("<h1 style='margin-bottom:0'>Ridgeline Property Analyzer</h1>", unsafe_allow_html=True)
st.caption("Screens a list of candidate properties in Gwinnett County, GA and ranks them by projected ROI. "
           "A screening tool: projected ROI is an estimate, not a guarantee, and is not an annualized return.")
if "DEVELOPMENT" in source_label:
    st.warning("Development extract in use: only 999 parcels are loaded, so many properties will be 'could not match'.")

# ---------------------------------------------------------------- input
with st.sidebar:
    st.header("1. Your candidate list")
    up = st.file_uploader("Upload a CSV with columns: address, zip, asking_price", type="csv")
    use_example = st.checkbox("Use the development example instead", value=False,
                              help="Real parcel addresses with SYNTHETIC asking prices. For trying the screen only.")
    st.download_button("Download a blank template", "address,zip,asking_price\n123 Example Rd,30043,250000\n",
                       file_name="candidates_template.csv", mime="text/csv")
    st.divider()
    st.caption(f"**Data:** {source_label}. Estimates are as of {params.get('as_of', 'the newest sale loaded')}.")
    with st.expander("Approved assumptions (read only)"):
        st.write(f"- Price screen: resale value must exceed the asking price by at least ${params['min_spread']:,.0f}\n"
                 f"- Holding: {params['holding_pct_per_month']*100:.2f}% of the asking price per month for {params['holding_months']} months\n"
                 f"- Selling: {params['selling_pct']*100:.0f}% of resale value\n"
                 f"- Renovation: per-square-foot bands by age (under 20 years / 20 or more), plus {params['contingency']*100:.0f}% contingency on the likely figure")
    with st.expander("What this does not tell you"):
        st.write("- Renovation figures come from one contractor's ranges and have not been checked against independent cost data.\n"
                 "- Not included: acquisition-side closing costs, and renovation beyond the contingency.\n"
                 "- Building size and age come from the 2026 county records.\n"
                 "- A property can pass the price screen and still have a negative projected ROI.")

if use_example:
    candidates = load_candidates(ROOT / "data" / "candidates_dev_fixture.csv")
    st.info("Showing the DEVELOPMENT example: real parcel addresses with synthetic asking prices.")
elif up is not None:
    try:
        candidates = load_candidates(up)
    except ValueError as e:
        st.error(f"Your file could not be read: {e}. Download the blank template in the sidebar to see the format.")
        st.stop()
else:
    st.info("Upload your candidate list in the sidebar (or tick the development example) to see the ranking.")
    st.stop()

scored = score_batch(candidates, None, params, None, data=data, index=index)

# ---------------------------------------------------------------- counts
counts = scored["status"].value_counts()
c = st.columns(5)
for col, (label, key) in zip(c, [("Processed", None), ("Ranked", "ranked"), ("Eliminated", "eliminated"),
                                 ("Could not value", "insufficient_comps"), ("Could not match", "unmatched")]):
    col.metric(label, len(scored) if key is None else int(counts.get(key, 0)))

# ---------------------------------------------------------------- top 5
ranked = scored[scored["status"] == "ranked"]
st.subheader("Top 5 candidates by projected ROI")
if ranked.empty:
    st.info("No property in this list was ranked. See the reasons in the table below.")
else:
    top = ranked.head(5)
    cols = st.columns(len(top))
    for col, (_, r) in zip(cols, top.iterrows()):
        with col.container(border=True):
            st.markdown(f"**#{int(r['rank'])}**")
            st.markdown(f"{r['matched_address']}")
            st.metric("Projected ROI", pct(r["roi_pct"]))
            st.caption(f"Asking {money(r['asking_price_used'])}  \nResale value {money(r['arv'])}  \nProfit {money(r['net_profit'])}")

# ---------------------------------------------------------------- all results
st.subheader("All results")
chosen = st.multiselect("Show", options=list(STATUS_LABEL), default=list(STATUS_LABEL), format_func=STATUS_LABEL.get)
view = scored[scored["status"].isin(chosen)].copy()
table = pd.DataFrame({
    "Rank": view["rank"].map(lambda r: "" if pd.isna(r) else (f"★ {int(r)}" if int(r) <= 5 else str(int(r)))),
    "Address": view["matched_address"].fillna(view["address"]),
    "Result": view["status"].map(STATUS_LABEL),
    "Asking": view["asking_price_used"].map(money),
    "Resale value (ARV)": view["arv"].map(money),
    "Renovation (likely + contingency)": view["reno_likely_with_contingency"].map(money),
    "Projected profit": view["net_profit"].map(money),
    "Projected ROI": view["roi_pct"].map(pct),
    "Why": view["explanation"],
})
st.dataframe(table, hide_index=True, width="stretch")
st.caption(f"Renovation figures: {NOTICE}")
st.download_button("Download these results (CSV)", scored.to_csv(index=False), file_name="scored.csv", mime="text/csv")

# ---------------------------------------------------------------- detail panel
st.subheader("How a property was evaluated")
labels = {int(r["row_id"]): f"{('#' + str(int(r['rank'])) + '  ') if pd.notna(r['rank']) else ''}{r['matched_address'] if pd.notna(r['matched_address']) else r['address']}  ({STATUS_LABEL[r['status']]})"
          for _, r in scored.iterrows()}
pick = st.selectbox("Pick a property", options=list(labels), format_func=labels.get, key="pick")
r = scored[scored["row_id"] == pick].iloc[0]
st.info(r["explanation"])
if pd.notna(r["arv"]):
    st.markdown("**Resale value (ARV)**")
    st.write(f"Estimated from {int(r['comp_count'])} comparable sales in the same neighborhood within {int(r['comp_window_months'])} months. "
             f"Resale value minus asking price = **{money(r['spread'])}** (the screen needs at least ${params['min_spread']:,.0f}).")
    comps = pd.DataFrame(json.loads(r["comps_json"]))
    if not comps.empty:
        st.dataframe(pd.DataFrame({"Address": comps["address"], "Sale date": comps["sale_date"], "Sale price": comps["amount"].map(money),
                                   "Sq ft": comps["fin_sqft"], "$ per sq ft": comps["ppsf"].map(lambda v: f"${v:,.0f}"),
                                   "Adjusted to today": comps["adj_ppsf"].map(lambda v: f"${v:,.0f}")}), hide_index=True, width="stretch")
if r["status"] == "ranked":
    st.markdown("**Renovation and return**")
    st.warning(f"{NOTICE}  Low {money(r['reno_low'])} · Likely {money(r['reno_likely_base'])} · "
               f"Likely + {params['contingency']*100:.0f}% contingency {money(r['reno_likely_with_contingency'])} · High {money(r['reno_high'])}")
    steps = pd.DataFrame([
        ("Resale value (ARV)", "from the comparable sales above", money(r["arv"])),
        ("Selling costs", f"{params['selling_pct']*100:.0f}% of resale value", "-" + money(r["selling"])),
        ("Purchase (asking) price", "", "-" + money(r["asking_price_used"])),
        ("Renovation (likely + contingency)", f"{r['reno_tier']} band", "-" + money(r["reno_likely_with_contingency"])),
        ("Holding costs", f"{params['holding_pct_per_month']*100:.2f}% of asking x {params['holding_months']} months", "-" + money(r["holding"])),
        ("Projected profit", "", money(r["net_profit"])),
        ("Projected ROI", "profit / (asking + renovation)", pct(r["roi_pct"])),
    ], columns=["Step", "How", "Amount"])
    st.dataframe(steps, hide_index=True, width="stretch")
