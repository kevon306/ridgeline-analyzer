"""Ridgeline Property Analytics: the dashboard.  Run locally with `streamlit run app.py`, or deploy on Streamlit Community Cloud.
It reads the real output of the scoring engine; nothing on screen is hard-coded. Display only: no number is recalculated here
except the ROI denominator (asking + renovation, the charter formula) and the contingency amount, both shown for explanation."""
import datetime
import gzip
import json
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from ridgeline_core.arv import load_county, load_params
import evidence_panel
from report import top5_html
from ridgeline_core import evidence as ev
from ridgeline_core.pipeline import load_candidates, load_parcel_index, normalize_street, score_batch, zip5
from ridgeline_core.reliability import arv_reliability
from ridgeline_core.reno_validation import metrics as reno_metrics
from ridgeline_core.renovation import (CATEGORIES, MODEL_VERSION, confidence, deal_outcomes, outcome_warning,
                                       risk_flags, scope_budget)
from ridgeline_core.rules import NOTICE

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Ridgeline Property Analytics", page_icon="🏠", layout="wide")

# Display labels only: the scored output keeps the official status and reason codes unchanged.
STATUS_LABEL = {"ranked": "Ranked", "eliminated": "Eliminated", "insufficient_comps": "Could not value", "unmatched": "Could not match"}
REASON_LABEL = {
    "below_min_spread": "Below price screen",
    "fewer_than_3_comps": "Too few nearby sales",
    "no_size_on_record": "No living area on record",
    "no_building_record": "No building record",
    "no_neighborhood": "No neighborhood code",
    "index_unavailable": "Price index unavailable",
    "unmatched_address": "Not found in the county's single-family records",
    "ambiguous_address": "Matches several parcels",
    "invalid_input": "No usable asking price",
}
TIER_LABEL = {"under20": "under 20 years old", "20plus": "20 years or older"}
HELP = {
    "arv": "After-repair value: the estimated resale value after renovation, from recent comparable sales in the same neighborhood.",
    "reno": "Preliminary screening estimate from the approved per-square-foot bands by house age. " + NOTICE,
    "holding": "Estimated cost of holding the property during the assumed investment period.",
    "selling": "Estimated cost of selling the property, as a share of the resale value.",
    "profit": "Estimated profit after the costs included in the model: resale value minus selling, purchase, renovation and holding.",
    "roi": "Projected net profit divided by (asking price + renovation). An estimate, not a guaranteed or annualized return.",
    "invest": "The ROI denominator in the approved formula: asking price + renovation (likely, with contingency).",
}
POS, NEG, NAVY, TOP5_BG = "#1E7B4C", "#B42318", "#1F3A5F", "#E8EEF7"

st.markdown(f"""<style>
.block-container {{padding: 3.2rem 2rem 2rem 2rem; max-width: 1280px;}}
.rl-header {{background: {NAVY}; color: #fff; border-radius: 10px; padding: 0.9rem 1.25rem; margin-bottom: 0.9rem;}}
.rl-header h1 {{color: #fff; font-size: 1.55rem; font-weight: 700; margin: 0; padding: 0; line-height: 1.25;}}
.rl-header .rl-sub {{color: #C9D6E8; font-size: 0.9rem; margin-top: 0.1rem;}}
.rl-header p {{color: #E6ECF4; font-size: 0.9rem; margin: 0.45rem 0 0 0;}}
[data-testid="stMetric"] {{background: #F6F8FB; border: 1px solid #E3E8EF; border-radius: 8px; padding: 0.55rem 0.8rem;}}
[data-testid="stMetricValue"] {{font-size: 1.45rem;}}
h2, h3 {{color: {NAVY};}}
</style>""", unsafe_allow_html=True)


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
    index = load_parcel_index(path)
    # internal record id -> county parcel number; .get so a missing pin shows "not on record" instead of stopping the app
    pins = {int(p["lrsnum"]): p.get("pin") for hits in index.values() for p in hits if p.get("pin")}
    facts = {int(p["lrsnum"]): {"year_built": p.get("year_built"), "fin_sqft": p.get("fin_sqft"), "cond_desc": p.get("cond_desc")}
             for hits in index.values() for p in hits}            # county record facts for risk flags (display only)
    return load_county(path), index, pins, facts, label


def money(x):
    return "—" if pd.isna(x) else (f"−${abs(x):,.0f}" if x < 0 else f"${x:,.0f}")


def roi_text(x):
    """Sign symbol plus number, so a gain or loss never depends on color alone."""
    if pd.isna(x):
        return "—"
    return f"▲ +{x:,.1f}%" if x > 0 else (f"▼ −{abs(x):,.1f}%" if x < 0 else "0.0%")


def profit_text(x):
    if pd.isna(x):
        return "—"
    return f"▲ {money(x)}" if x > 0 else (f"▼ {money(x)}" if x < 0 else money(x))


def status_text(r):
    s = STATUS_LABEL[r["status"]]
    return f"{s} · {REASON_LABEL.get(r['reason'], r['reason'])}" if r["reason"] else s


def address_of(r):
    return r["matched_address"] if pd.notna(r["matched_address"]) else r["address"]


def results_table(view: pd.DataFrame):
    """Cells are pre-formatted text so a missing value reads "—" (Streamlit shows raw nulls as "None");
    the rows arrive in the engine's rank order. The Styler only highlights."""
    t = pd.DataFrame({
        "Rank": view["rank"].map(lambda r: "" if pd.isna(r) else (f"★ {int(r)}" if int(r) <= 5 else str(int(r)))),
        "Property address": view.apply(address_of, axis=1),
        "Asking price": view["asking_price_used"].map(money),
        "Resale value": view["arv"].map(money),
        "Renovation": view["reno_likely_with_contingency"].map(money),
        "Projected profit": view["net_profit"].map(profit_text),
        "Projected ROI": view["roi_pct"].map(roi_text),
        "Status": view.apply(status_text, axis=1),
    }, index=view.index)
    top = view["top5"].fillna(False).astype(bool)

    def row_style(row):
        return [f"background-color: {TOP5_BG}; font-weight: 600" if top[row.name] else ""] * len(row)

    def sign_color(v):
        return f"color: {POS}" if v.startswith("▲") else (f"color: {NEG}" if v.startswith("▼") else "")

    styler = t.style.apply(row_style, axis=1).map(sign_color, subset=["Projected profit", "Projected ROI"])
    config = {
        "Rank": st.column_config.Column(width=55, help="★ marks the top five by projected ROI. Only ranked properties get a rank."),
        "Property address": st.column_config.Column(width=185),
        "Asking price": st.column_config.Column(help="Asking price from your file."),
        "Resale value": st.column_config.Column(help="Estimated resale value (ARV). " + HELP["arv"]),
        "Renovation": st.column_config.Column(help=f"Likely renovation figure plus {contingency} contingency. " + HELP["reno"]),
        "Projected profit": st.column_config.Column(help=HELP["profit"]),
        "Projected ROI": st.column_config.Column(help=HELP["roi"]),
        "Status": st.column_config.Column(width=235, help="Why a property is or is not ranked. Pick it below for the full explanation."),
    }
    return styler, config


params = load_params(ROOT / "assumptions.json")
data, index, pins, facts, source_label = load_engine()
reliability = arv_reliability()
evidence_db = evidence_panel.evidence_path()
econ = ev.connect(evidence_db)
try:
    reno_validation = reno_metrics(ev.project_records(econ))
except (ValueError, Exception) as e:                        # unreadable evidence must not stop the app
    reno_validation = {"n": 0, "status": f"renovation evidence could not be read: {e}"}
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

with st.sidebar:
    evidence_panel.sidebar_backup(econ, evidence_db)

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
    st.info("Upload your candidate list in the sidebar (or tick the development example) to see the ranking. "
            "On a phone, open the sidebar with the » button at the top left.  \n"
            "**How it works:** 1. upload a CSV of addresses and asking prices · 2. review the top five by projected ROI · "
            "3. pick any property to see how it was evaluated.")
    st.stop()

if candidates.attrs.get("renamed"):
    st.info("Read your columns as: " + " · ".join(f"“{src}” → {dst}" for src, dst in candidates.attrs["renamed"].items()) + ".")
scored = score_batch(candidates, None, params, None, data=data, index=index)

# ---------------------------------------------------------------- counts
counts = scored["status"].value_counts()
c = st.columns(5)
for col, (label, key) in zip(c, [("Processed", None), ("Ranked", "ranked"), ("Eliminated", "eliminated"),
                                 ("Could not value", "insufficient_comps"), ("Could not match", "unmatched")]):
    col.metric(label, len(scored) if key is None else int(counts.get(key, 0)),
               help="Every row in your file." if key is None else f"Status code: {key}")
accounted = sum(int(counts.get(k, 0)) for k in STATUS_LABEL)
st.caption(f"All {len(scored)} properties in your file are accounted for below." if accounted == len(scored)
           else f"Warning: {len(scored) - accounted} properties have an unexpected status.")

# ---------------------------------------------------------------- top 5
ranked = scored[scored["status"] == "ranked"]
st.subheader("Top 5 candidates by projected ROI")
if ranked.empty:
    st.info("No property in this list was ranked. See the reasons in the table below.")
else:
    styler, config = results_table(ranked.head(5))
    st.dataframe(styler, hide_index=True, width="stretch", column_config=config,
                 column_order=["Rank", "Property address", "Asking price", "Resale value", "Projected profit", "Projected ROI"])
    st.caption("Ranked from highest to lowest projected ROI. Only properties that passed the price screen and could be valued are ranked. "
               "Projected ROI is an estimate, not a guaranteed return.")
    st.download_button("Download a printable top-5 summary", top5_html(scored, params, source_label, datetime.date.today().isoformat()),
                       file_name="ridgeline_top5.html", mime="text/html",
                       help="A one-page summary to open in your browser and print or save as PDF.")

# ---------------------------------------------------------------- all results
st.subheader("All results")
if "status_filter" not in st.session_state:
    st.session_state["status_filter"] = list(STATUS_LABEL)


def show_all_statuses():
    st.session_state["status_filter"] = list(STATUS_LABEL)


fcol, bcol = st.columns([4, 1], vertical_alignment="bottom")
chosen = fcol.multiselect("Filter by status", options=list(STATUS_LABEL), format_func=STATUS_LABEL.get, key="status_filter")
filtered = set(chosen) != set(STATUS_LABEL)
bcol.button("Show all statuses", on_click=show_all_statuses, disabled=not filtered, width="stretch")
view = scored[scored["status"].isin(chosen)]
if filtered:
    st.caption(f"**Filter on:** showing {len(view)} of {len(scored)} properties ({', '.join(STATUS_LABEL[s] for s in chosen) or 'no status selected'}).")


def pick_from_table():
    rows = st.session_state["results_table"].selection.rows
    if rows:
        st.session_state["pick"] = st.session_state["table_row_ids"][rows[0]]


if view.empty:
    st.info("No properties match this filter. Use **Show all statuses** to see every row.")
else:
    st.session_state["table_row_ids"] = [int(x) for x in view["row_id"]]
    styler, config = results_table(view)
    st.dataframe(styler, hide_index=True, width="stretch", column_config=config, key="results_table",
                 height=min(len(view), 25) * 35 + 42,
                 on_select=pick_from_table, selection_mode="single-row")
    st.caption(f"Money in US dollars; ROI = projected net profit ÷ (asking + renovation). ★ and shading mark the top five. Rows are already in rank order (clicking a column heading sorts it as text). Click a row to see how it was evaluated. Renovation figures: {NOTICE} "
               "Projected ROI is an estimate, not a guaranteed return.")
st.download_button("Download all results (CSV)", scored.to_csv(index=False), file_name="scored.csv", mime="text/csv")

# ---------------------------------------------------------------- detail panel
st.subheader("How a property was evaluated")
labels = {int(r["row_id"]): f"{('#' + str(int(r['rank'])) + '  ') if pd.notna(r['rank']) else ''}{address_of(r)}  ({STATUS_LABEL[r['status']]})"
          for _, r in scored.iterrows()}
pick = st.selectbox("Pick a property (or click a row in the table above)", options=list(labels), format_func=labels.get, key="pick")
r = scored[scored["row_id"] == pick].iloc[0]

with st.container(border=True):
    st.markdown(f"#### {address_of(r)}")
    (st.success if r["status"] == "ranked" else st.info)(f"**{status_text(r)}.** {r['explanation']}")

    st.markdown("**Property and status**")
    a, b, c3 = st.columns(3)
    a.metric("Asking price", money(r["asking_price_used"]))
    b.metric("Status", STATUS_LABEL[r["status"]], help=f"Status code: {r['status']}")
    c3.metric("Reason", REASON_LABEL.get(r["reason"], "—") if r["reason"] else "—",
              help=f"Reason code: {r['reason']}" if r["reason"] else "Ranked properties have no reason code.")
    entered = f"Entered as: {r['address']}"
    if str(r.get("parcel") or "").strip():
        entered += f", parcel {str(r['parcel']).strip()}"
    if pd.notna(r["matched_address"]):
        entered += f" · Matched county record: {r['matched_address']} (parcel {pins.get(int(r['lrsnum']), 'not on record')})"
    st.caption(f"{entered} · Status code: {r['status']} · Reason code: {r['reason'] or 'none'}")
    if r["reason"] in ("unmatched_address", "ambiguous_address"):
        street, z = normalize_street(r["address"]), zip5(r.get("zip"), r["address"])
        tip = (f"Looked up as street “{street or '(blank)'}” in ZIP “{z or '(none found)'}”. The match must be exact. "
               "Check for a unit or apartment number, a different spelling or street type, or a ZIP that differs from the county record.")
        if r["reason"] == "ambiguous_address":
            parcels = index.get(f"{street}|{z}", [])
            tip += " Parcels with this address: " + "; ".join(f"parcel {p['lrsnum']} ({p['address']} {p['zip']})" for p in parcels) + "."
        st.caption(tip)

    st.markdown("**Resale value (ARV) and comparable sales**")
    if pd.notna(r["arv"]):
        a, b = st.columns(2)
        a.metric("Estimated resale value (ARV)", money(r["arv"]), help=HELP["arv"])
        b.metric("Resale value − asking price", money(r["spread"]),
                 help=f"The price screen needs at least ${params['min_spread']:,.0f}.")
        if reliability:
            with st.expander("How reliable is this resale value estimate?"):
                rl = reliability
                st.markdown(
                    f"- **Within 15%, among houses the tool could value: {rl['hit_rate_valued']*100:.0f}%** "
                    f"({rl['hits']:,} of {rl['valued']:,})\n"
                    f"- **Within 15%, counting houses it could not value as misses: {rl['hit_rate_all']*100:.0f}%** "
                    f"({rl['hits']:,} of {rl['tested']:,})\n"
                    f"- Houses tested: {rl['tested']:,} · without a usable estimate: {rl['unvalued']:,} "
                    f"({rl['unvalued']/rl['tested']*100:.0f}%, all for too few comparable sales)\n"
                    f"- Typical miss: {rl['median_abs_error']*100:.1f}% (median); estimates ran {abs(rl['median_signed_error'])*100:.1f}% "
                    f"{'below' if rl['median_signed_error'] < 0 else 'above'} the next sale on average")
                st.caption(
                    "**What this measures:** the resale value (ARV) only, not renovation costs or ROI. "
                    "**Within 15%** means |estimate − actual next sale price| ÷ actual next sale price is 15% or less. "
                    "The two percentages share the same hits; the first divides by houses that got an estimate, the second by every "
                    "house tested. **Method:** Gwinnett County single-family houses that sold twice 30 to 548 days apart "
                    f"(first sales {rl['first_sales'][0]} to {rl['first_sales'][1]}), each valued as of its first sale using only "
                    "earlier data, then compared with its second sale; a seeded random half was held out and graded once "
                    "(BACKTEST_PROTOCOL.md). Only pairs whose price rose were tested, so the figures may flatter flat or falling markets.")
                if not rl["verified"]:
                    st.warning(f"These figures were recomputed from the per-house results but do not match the recorded summary: {rl['discrepancy']}")
        st.caption(f"Estimated from {int(r['comp_count'])} comparable sales in the same neighborhood within {int(r['comp_window_months'])} months, "
                   f"each adjusted to the as-of date ({r['as_of']}) with the county price index. "
                   f"The price screen needs at least ${params['min_spread']:,.0f}.")
        comps = pd.DataFrame(json.loads(r["comps_json"]))
        if not comps.empty:
            st.dataframe(pd.DataFrame({"Address": comps["address"], "Sale date": comps["sale_date"], "Sale price": comps["amount"].map(money),
                                       "Sq ft": comps["fin_sqft"].map(lambda v: f"{v:,}"), "$ per sq ft": comps["ppsf"].map(lambda v: f"${v:,.0f}"),
                                       "Adjusted to as-of date": comps["adj_ppsf"].map(lambda v: f"${v:,.0f}")}),
                         hide_index=True, width="stretch")
    else:
        st.caption("Not estimated: " + r["explanation"])

    st.markdown("**Renovation estimate**")
    fact = facts.get(int(r["lrsnum"]), {}) if pd.notna(r["lrsnum"]) else {}
    pin = pins.get(int(r["lrsnum"])) if pd.notna(r["lrsnum"]) else None
    epid = ev.find_property(econ, pin) if pin else None
    findings = ev.latest_findings(econ, epid) if epid else {}
    scope, closing, financing, model, conf = None, 0.0, 0.0, None, None
    if pd.notna(r["reno_likely_with_contingency"]):
        as_of_year = pd.Timestamp(r["as_of"]).year
        # the exact figures the ranking used (see renovation.model_scenarios for how they are built from the bands)
        rates = params["reno_per_sqft"][r["reno_tier"]]
        model = {"tier": r["reno_tier"], "rates": rates, "fin_sqft": r["reno_likely_base"] / rates["likely"],
                 "low": {"base": r["reno_low"], "contingency": 0.0, "total": r["reno_low"]},
                 "expected": {"base": r["reno_likely_base"], "contingency": r["reno_likely_with_contingency"] - r["reno_likely_base"],
                              "total": r["reno_likely_with_contingency"]},
                 "high": {"base": r["reno_high"], "contingency": 0.0, "total": r["reno_high"]}}
        st.warning(NOTICE)
        a, b, c3, d, e = st.columns(5)
        a.metric("Low", money(model["low"]["total"]), help=HELP["reno"])
        b.metric("Expected base", money(model["expected"]["base"]), help=HELP["reno"])
        c3.metric(f"Contingency ({contingency})", money(model["expected"]["contingency"]),
                  help=f"{contingency} added to the expected (likely) figure only, under the approved rule.")
        d.metric("Expected budget", money(model["expected"]["total"]), help="Base + contingency: the renovation figure used in the ranking. " + NOTICE)
        e.metric("High", money(model["high"]["total"]), help=HELP["reno"])
        st.caption(f"**What drives it:** {model['fin_sqft']:,.0f} sq ft [county record] × \\${rates['low']} / \\${rates['likely']} / \\${rates['high']} "
                   f"per sq ft (low / expected / high) for a house {TIER_LABEL.get(model['tier'], model['tier'])} [model assumption: one contractor's ranges, "
                   f"assumptions.json, model {MODEL_VERSION}]. One rate covers the whole house; there is no item-by-item scope, and "
                   "low and high come from the contractor's bands, not from a measured spread. "
                   f"County condition rating: {fact.get('cond_desc') or 'not on record'} [county record]. {NOTICE}")
        with st.expander("Risk flags: what could make this estimate wrong"):
            for fl in risk_flags(fact.get("year_built"), fact.get("cond_desc"), as_of_year, params, findings):
                st.markdown(f"- **{fl['item']}** ({fl['level']}): {fl['why']} *{fl['action']}*")
            st.caption("Flags never add cost. Age and condition say where to look, not what to replace.")
        with st.expander("Your scope and costs (optional): enter your inspection findings or contractor quotes"):
            st.caption("Blank rows are ignored. Each category is counted once; contingency is applied once. "
                       "These numbers are your entries: they change the scenarios below, not the ranking.")
            entered = st.data_editor(
                pd.DataFrame({"category": CATEGORIES, "low": [None] * len(CATEGORIES), "expected": [None] * len(CATEGORIES),
                              "high": [None] * len(CATEGORIES), "source": [""] * len(CATEGORIES)}),
                key=f"scope_{pick}", hide_index=True, width="stretch", num_rows="dynamic", height=(len(CATEGORIES) + 2) * 35 + 3,
                column_config={"category": st.column_config.TextColumn("Category"),
                               "low": st.column_config.NumberColumn("Low ($)", min_value=0, format="$%d"),
                               "expected": st.column_config.NumberColumn("Expected ($)", min_value=0, format="$%d"),
                               "high": st.column_config.NumberColumn("High ($)", min_value=0, format="$%d"),
                               "source": st.column_config.TextColumn("Source (quote, inspection, invoice)")})
            a, b, c3 = st.columns(3)
            cont_pct = a.number_input("Contingency on your scope (%)", 0.0, 100.0, 10.0, 1.0, key=f"cont_{pick}",
                                      help="Larger when condition is poor or the scope is incomplete.") / 100
            closing = b.number_input("Closing and transaction costs ($)", 0.0, step=500.0, key=f"closing_{pick}",
                                     help="Not in the approved model; 0 means not included.")
            financing = c3.number_input("Financing costs ($)", 0.0, step=500.0, key=f"fin_{pick}",
                                        help="Interest, points and fees for the hold period; 0 means not included.")
            inspected = st.checkbox("My scope is based on a completed inspection", key=f"insp_{pick}")
            try:
                scope = scope_budget(entered.to_dict("records"), cont_pct)
            except ValueError as e:
                st.error(str(e))
            if scope:
                st.caption(f"Your scope: base {money(scope['expected']['base'])} + contingency {money(scope['expected']['contingency'])} "
                           f"= {money(scope['expected']['total'])} expected (low {money(scope['low']['total'])}, "
                           f"high {money(scope['high']['total'])}).".replace("$", "\\$"))   # unescaped $...$ renders as math
                st.caption(f"Your scope covers only the {len(scope['rows'])} categor{'y' if len(scope['rows']) == 1 else 'ies'} you entered; "
                           "any work you did not enter counts as nothing. Compare it with the model's figures above before relying on it.")
        inspected = any(f["condition"] != "unknown" for f in findings.values()) or bool(st.session_state.get(f"insp_{pick}"))
        conf = confidence(True, scope, reno_validation, inspected=inspected and scope is not None)
        st.markdown(f"**Renovation confidence: {conf['label']}** (separate from the resale value and ROI)")
        st.caption(" ".join(conf["reasons"]) + f" Validation status: {reno_validation['status']}.")
    else:
        st.caption("Not calculated: renovation is estimated only for properties that pass the price screen.")

    st.markdown("**Projected return**")
    if r["status"] == "ranked":
        invest = r["asking_price_used"] + r["reno_likely_with_contingency"]
        a, b, c3 = st.columns(3)
        a.metric("Total projected investment", money(invest), help=HELP["invest"])
        b.metric("Projected net profit", profit_text(r["net_profit"]), help=HELP["profit"])
        c3.metric("Projected ROI", roi_text(r["roi_pct"]), help=HELP["roi"])
        steps = pd.DataFrame([
            ("Resale value (ARV)", "from the comparable sales above", money(r["arv"])),
            ("Selling costs", f"{params['selling_pct']*100:.0f}% of resale value", money(-r["selling"])),
            ("Purchase (asking) price", "", money(-r["asking_price_used"])),
            ("Renovation (likely + contingency)", f"likely + {contingency} contingency, screening estimate", money(-r["reno_likely_with_contingency"])),
            ("Holding costs", f"{params['holding_pct_per_month']*100:.2f}% of asking × {params['holding_months']} months", money(-r["holding"])),
            ("Projected net profit", "", profit_text(r["net_profit"])),
            ("Total projected investment", "asking + renovation", money(invest)),
            ("Projected ROI", "net profit ÷ total projected investment", roi_text(r["roi_pct"])),
        ], columns=["Step", "How", "Amount"])
        st.dataframe(steps, hide_index=True, width="stretch")
        st.markdown("**If renovation costs differ**")
        sets = [("Model", model, 0.0, 0.0)]
        if closing or financing:
            sets.append(("Model + your closing/financing", model, closing, financing))
        if scope:
            sets.append(("Your scope", scope, closing, financing))
        rows = []
        for label, sc, cl, fi in sets:
            outs = deal_outcomes(r["asking_price_used"], r["arv"], sc, params, cl, fi)
            warn = outcome_warning(outs)
            if warn:
                st.warning(f"{label}: {warn}")
            rows += [(label, o["scenario"].capitalize(), money(o["renovation"]), money(-(o["closing"] + o["financing"])) if (o["closing"] or o["financing"]) else "—",
                      profit_text(o["net_profit"]), roi_text(o["roi_pct"])) for o in outs]
        st.dataframe(pd.DataFrame(rows, columns=["Basis", "Renovation scenario", "Renovation budget", "Closing + financing",
                                                 "Projected profit", "Projected ROI"]), hide_index=True, width="stretch")
        st.caption("Same formula as above; only the renovation budget (and any closing or financing costs you enter) changes. "
                   "The ranking uses the model's expected budget.")
        st.caption(f"Renovation: {NOTICE} Projected ROI is an estimate for screening, not a guaranteed or annualized return. "
                   "Not included unless you enter them above: acquisition-side closing costs and financing costs.")
    else:
        st.caption("Not calculated: only ranked properties get a projected profit and ROI.")

    st.markdown("**Renovation evidence**")
    evidence_panel.render(econ, r, fact, pin, model, scope, conf["label"] if conf else "")
