"""Ridgeline Property Analytics: the screening tool (page "/screener"; app.py is the router).
Logged-in users only: require_login() runs before anything else. It reads the real output of the scoring engine;
nothing on screen is hard-coded. The display itself lives in results_view.py (shared with /saved). Each list a
logged-in user scores is saved once to their saved runs."""
import datetime
import hashlib
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from accounts import require_login

user = require_login()                            # first: a logged-out visitor is sent to /login

import evidence_panel  # noqa: E402
import results_view as rv  # noqa: E402
from accounts import app_engine  # noqa: E402
from ridgeline_core import evidence as ev  # noqa: E402
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.county_store import load_county_subset, lookup_parcels  # noqa: E402
from ridgeline_core.pipeline import load_candidates, score_batch  # noqa: E402
from runs import run_name, save_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
rv.apply_style()


@st.cache_data(max_entries=4, show_spinner="Scoring your list...")
def score_list(candidates: pd.DataFrame, db_path: Path, params: dict):
    """Score one list from the parcels, neighborhood sales and price index it needs (same result as a full load)."""
    con = sqlite3.connect(db_path)
    try:
        index = lookup_parcels(con, candidates)
        data = load_county_subset(con, index)
        scored = score_batch(candidates, None, params, None, data=data, index=index)
    finally:
        con.close()
    return scored, index


params = load_params(ROOT / "assumptions.json")
db_path, source_label = rv.county_db()
contingency = f"{params['contingency']*100:.0f}%"

# ---------------------------------------------------------------- header
st.markdown(
    "<div class='rl-header'><h1>Ridgeline Property Analytics</h1>"
    "<div class='rl-sub'>Real Estate Investment Analytics · Gwinnett County, Georgia</div>"
    "<p>Upload a list of candidate fix-and-flip properties. The app estimates each one's resale value from recent nearby sales, "
    "screens out weak deals, and ranks the rest by projected return on investment (ROI). A screening tool: every figure is an estimate.</p></div>",
    unsafe_allow_html=True)
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
                 f"- Renovation: per-square-foot bands by age (under 20 years / 20 or more), plus {contingency} contingency on the likely figure")
    with st.expander("What this does not tell you"):
        st.write("- Renovation figures come from one contractor's ranges and have not been checked against independent cost data.\n"
                 "- Not included: acquisition-side closing costs, and renovation beyond the contingency.\n"
                 "- Building size and age come from the 2026 county records.\n"
                 "- A property can pass the price screen and still have a negative projected ROI.")
    evidence_db = evidence_panel.evidence_path()
    evidence_panel.sidebar_backup(ev.connect(evidence_db), evidence_db)

if use_example:
    candidates = load_candidates(ROOT / "data" / "candidates_dev_fixture.csv")
    source_name = "Development example (synthetic asking prices)"
    st.info("Showing the DEVELOPMENT example: real parcel addresses with synthetic asking prices.")
elif up is not None:
    try:
        candidates = load_candidates(up)
    except ValueError as e:
        st.error(f"Your file could not be read: {e}. Download the blank template in the sidebar to see the format.")
        st.stop()
    source_name = up.name
else:
    st.info("Upload your candidate list in the sidebar (or tick the development example) to see the ranking. "
            "On a phone, open the sidebar with the » button at the top left.  \n"
            "**How it works:** 1. upload a CSV of addresses and asking prices · 2. review the top five by projected ROI · "
            "3. pick any property to see how it was evaluated.")
    st.stop()

if candidates.empty:
    st.warning("Your file has the right columns but no property rows. Add at least one address and asking price.")
    st.stop()
if candidates.attrs.get("renamed"):
    st.info("Read your columns as: " + " · ".join(f"“{src}” → {dst}" for src, dst in candidates.attrs["renamed"].items()) + ".")
scored, index = score_list(candidates, db_path, params)
ctx = rv.context(params, index, source_label)

# ---------------------------------------------------------------- save the run (once per list, logged-in users only)
engine = app_engine()
if user and engine is not None:
    key = f"{user['id']}|{source_name}|{hashlib.sha256(candidates.to_csv(index=False).encode()).hexdigest()}"
    saved = st.session_state.setdefault("saved_run_ids", {})
    if key not in saved:
        try:
            saved[key] = save_run(engine, user["id"], run_name(source_name, datetime.datetime.now()), scored)
        except SQLAlchemyError:
            saved[key] = None
            st.warning("This run could not be saved to your saved runs. The results below are unaffected.")
    if saved[key]:
        st.caption("Saved to your saved runs.")
        st.page_link("views/saved.py", label="Open My saved runs", icon=":material/history:")

rv.render_counts(scored)
rv.render_top5(scored, ctx)
rv.render_all_results(scored, ctx)
rv.render_detail(scored, ctx)
