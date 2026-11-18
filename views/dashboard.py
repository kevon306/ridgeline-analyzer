"""Dashboard ("/dashboard"). Logged-in users only. Summarises the user's saved runs, read live from MySQL on every
load, and answers the charter question in dashboard_data.QUESTION."""
import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from accounts import require_login

user = require_login()                            # first: a logged-out visitor is sent to /login

from accounts import app_engine  # noqa: E402
from brand import COPPER, NAVY, masthead  # noqa: E402
from dashboard_data import OUTCOMES, QUESTION, summarize, user_rows  # noqa: E402
from results_view import REASON_LABEL, money  # noqa: E402
from runs import list_runs  # noqa: E402

masthead()
st.markdown("<div class='rl-kicker'>Dashboard · your saved screening runs</div>", unsafe_allow_html=True)
st.markdown(f"<div class='rl-q'>{QUESTION}</div>", unsafe_allow_html=True)

engine = app_engine()
if engine is None:
    st.error("The dashboard needs the accounts database, and this server has no DATABASE_URL configured.")
    st.stop()
try:
    mine = list_runs(engine, user["id"])
except SQLAlchemyError:
    st.error("The database could not be reached. Please try again shortly.")
    st.stop()
if mine.empty:
    st.info("No saved runs yet. Score a candidate list in the screener and it will be summarised here.")
    st.page_link("views/screener.py", label="Open the screener", icon=":material/search:")
    st.stop()

options = {0: f"All saved runs ({len(mine)})", **{int(r.id): f"{r.name} · {int(r.n_rows)} rows" for r in mine.itertuples()}}
pick = st.selectbox("Runs included", list(options), format_func=options.get, key="dash_run")
s = summarize(user_rows(engine, user["id"], pick or None), REASON_LABEL)

st.markdown(f"<div class='rl-outcome'>{s['answer']}</div>", unsafe_allow_html=True)
c = st.columns(4)
c[0].metric("Candidates screened", f"{s['n']:,}", help="Every row of every included run, each with a status and a reason.")
c[1].metric("Worth pursuing", f"{int(s['counts']['Worth pursuing']):,}", help="Ranked (passed the price screen, valued from "
            "3+ nearby sales) with a projected ROI above zero.")
c[2].metric("Screened out", f"{int(s['reasons'].sum()):,}", help="Eliminated on price, could not be valued, or not matched.")
c[3].metric("Median projected ROI, ranked", "—" if s["median_roi"] is None else f"{s['median_roi']:+.1f}%".replace("-", "−"))


def bars(df: pd.DataFrame, cat: str, val: str, title: str, order=None):
    """A single-series horizontal bar chart (no legend needed: the title names it), with a hover tooltip."""
    return (alt.Chart(df, title=title).mark_bar(color=NAVY, cornerRadiusEnd=4, height=16)
            .encode(y=alt.Y(f"{cat}:N", sort=order or "-x", title=None, axis=alt.Axis(labelLimit=260)),
                    x=alt.X(f"{val}:Q", title="Candidates", axis=alt.Axis(tickMinStep=1, grid=True, gridOpacity=0.35)),
                    tooltip=[alt.Tooltip(f"{cat}:N", title=title.split(":")[0]), alt.Tooltip(f"{val}:Q", title="Candidates")])
            .properties(height=alt.Step(30)))


left, right = st.columns(2)
with left:
    st.altair_chart(bars(s["counts"].rename_axis("Outcome").reset_index(name="n"), "Outcome", "n",
                         "Every candidate: what happened to it", OUTCOMES), width="stretch")
with right:
    if len(s["reasons"]):
        st.altair_chart(bars(s["reasons"].rename_axis("Reason").reset_index(name="n"), "Reason", "n",
                             "Why the rest were screened out"), width="stretch")
    else:
        st.info("Nothing was screened out in the runs included.")

ranked = s["ranked"].dropna(subset=["roi_pct"])
if len(ranked):
    hist = (alt.Chart(ranked, title="Projected ROI spread, ranked candidates")
            .mark_bar(color=NAVY, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, binSpacing=2)
            .encode(x=alt.X("roi_pct:Q", bin=alt.Bin(step=5), title="Projected ROI (%)"),
                    y=alt.Y("count():Q", title="Candidates", axis=alt.Axis(tickMinStep=1, gridOpacity=0.35)),
                    tooltip=[alt.Tooltip("roi_pct:Q", bin=alt.Bin(step=5), title="ROI band (%)"),
                             alt.Tooltip("count():Q", title="Candidates")]))
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=COPPER, strokeWidth=2).encode(x="x:Q")
    st.altair_chart((hist + zero).properties(height=240), width="stretch")
    st.caption("The copper line is break-even (0%). Bars left of it passed the screen but are projected to lose money.")

st.subheader("Worth pursuing, best projected return first")
w = s["worth"]
if len(w):
    st.dataframe(pd.DataFrame({"Property": w["address"], "Run": w["run_name"], "Asking price": w["asking_price"].map(money),
                               "Resale value (est.)": w["arv"].map(money), "Projected profit (est.)": w["net_profit"].map(money),
                               "Projected ROI (est.)": w["roi_pct"].map(lambda v: f"{v:+.1f}%")}),
                 hide_index=True, width="stretch")
else:
    st.info("No candidate in the runs included is ranked with a positive projected return.")
st.caption("Read live from your saved runs each time this page loads: score a new list in the screener and these numbers "
           "change. A house that appears in several runs is counted once per run. Every figure except the asking price "
           "is an estimate.")
