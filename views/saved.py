"""My saved runs ("/saved"). Logged-in users only. Every query is limited to the logged-in user's id (runs.py)."""
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from accounts import require_login

user = require_login()                            # first: a logged-out visitor is sent to /login

import results_view as rv  # noqa: E402
from accounts import app_engine  # noqa: E402
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.county_store import lookup_parcels  # noqa: E402
from runs import delete_run, list_runs, load_run, rebuild_scored  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
rv.apply_style()
st.title("My saved runs")

engine = app_engine()
if engine is None:
    st.error("Saved runs are unavailable: this server has no DATABASE_URL configured.")
    st.stop()
try:
    mine = list_runs(engine, user["id"])
except SQLAlchemyError:
    st.error("The saved-runs database could not be reached. Please try again shortly.")
    st.stop()
if mine.empty:
    st.info("No saved runs yet. Every list you score in the screener is saved here.")
    st.page_link("views/screener.py", label="Open the screener", icon=":material/search:")
    st.stop()

st.dataframe(pd.DataFrame({"Name": mine["name"], "Saved": pd.to_datetime(mine["created_at"]).dt.strftime("%Y-%m-%d %H:%M"),
                           "Rows": mine["n_rows"], "Ranked": mine["n_ranked"]}),
             hide_index=True, width="stretch")

ids = [int(i) for i in mine["id"]]
names = dict(zip(ids, mine["name"]))
if st.session_state.get("saved_run") not in ids:              # only this user's runs can be chosen
    st.session_state.pop("saved_run", None)
run_id = st.selectbox("Open a run", options=ids, format_func=names.get, key="saved_run")
loaded = load_run(engine, user["id"], run_id)                # filtered by user id again, not just by the menu
if loaded is None:
    st.error("That run was not found.")
    st.stop()
run, rows = loaded

if st.button("Delete this run", key="delete_run"):
    st.session_state["confirm_delete"] = run_id
if st.session_state.get("confirm_delete") == run_id:
    st.warning(f"Delete “{run['name']}” and its {run['n_rows']} rows? This cannot be undone.")
    yes, no = st.columns(2)
    if yes.button("Yes, delete it", key="confirm_delete_yes", type="primary"):
        delete_run(engine, user["id"], run_id)
        st.session_state.pop("confirm_delete", None)
        st.session_state.pop("saved_run", None)
        st.rerun()
    if no.button("Cancel", key="confirm_delete_no"):
        st.session_state.pop("confirm_delete", None)
        st.rerun()

params = load_params(ROOT / "assumptions.json")
db_path, source_label = rv.county_db()
con = sqlite3.connect(db_path)
try:
    index = lookup_parcels(con, rows[["address", "zip"]])
finally:
    con.close()
scored = rebuild_scored(run, rows, params, index)
ctx = rv.context(params, index, source_label, prefix=f"saved{run_id}_", saved=True)

st.caption(f"Saved {pd.Timestamp(run['created_at']):%Y-%m-%d %H:%M} · estimates as of {scored['as_of'].iloc[0]} · "
           f"{run['n_rows']} rows. The figures are the ones saved with the run.")
rv.render_counts(scored)
rv.render_top5(scored, ctx)
rv.render_all_results(scored, ctx)
rv.render_detail(scored, ctx)
