"""Display helpers shared by the dashboard: the back-test reliability line and the printable top-5 summary.
Both only read what already exists (the graded back-test summary and the scored output); nothing is recalculated."""
from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

from ridgeline_core.reliability import arv_reliability
from ridgeline_core.rules import NOTICE

ROOT = Path(__file__).resolve().parent


def reliability_text() -> str | None:
    """One sentence from the graded back-test, recomputed from the per-pair file; None if it cannot be verified."""
    rl = arv_reliability()
    if not rl or not rl["verified"]:
        return None
    return (f"Resale value (ARV) back-test on {rl['tested']:,} past repeat sales: {rl['hit_rate_valued']*100:.0f}% of the "
            f"{rl['valued']:,} houses that could be valued landed within 15% of the next sale price; counting the "
            f"{rl['unvalued']:,} houses that could not be valued as misses, {rl['hit_rate_all']*100:.0f}% did. "
            "This measures the resale value only, not renovation costs. Details: BACKTEST_PROTOCOL.md.")


def _money(x):
    return "—" if pd.isna(x) else (f"−${abs(x):,.0f}" if x < 0 else f"${x:,.0f}")


def _pct(x):
    return "—" if pd.isna(x) else (f"▲ +{x:,.1f}%" if x > 0 else (f"▼ −{abs(x):,.1f}%" if x < 0 else "0.0%"))


def top5_html(scored: pd.DataFrame, params: dict, source_label: str, generated: str) -> str:
    """A one-page, print-ready HTML summary of the top five ranked properties."""
    top = scored[scored["top5"].fillna(False).astype(bool)].sort_values("rank")
    counts = scored["status"].value_counts()
    rows = "".join(
        f"<tr><td>#{int(r['rank'])}</td><td>{html.escape(str(r['matched_address']))}</td><td>{_money(r['asking_price_used'])}</td>"
        f"<td>{_money(r['arv'])}</td><td>{_money(r['reno_likely_with_contingency'])}</td><td>{_money(r['holding'])}</td>"
        f"<td>{_money(r['selling'])}</td><td>{_money(r['net_profit'])}</td><td><b>{_pct(r['roi_pct'])}</b></td>"
        f"<td>{int(r['comp_count'])}</td></tr>"
        for _, r in top.iterrows())
    body = (f"<table><thead><tr><th>Rank</th><th>Address</th><th>Asking</th><th>Resale value (ARV)</th>"
            f"<th>Renovation*</th><th>Holding</th><th>Selling</th><th>Projected profit</th><th>Projected ROI</th><th>Comps</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>") if len(top) else "<p>No property in this list was ranked.</p>"
    rel = reliability_text()
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Ridgeline: top 5 candidates</title>
<style>
body {{font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1B2430; margin: 24px;}}
h1 {{color: #1F3A5F; font-size: 20px; margin: 0;}} .sub {{color: #556; margin: 2px 0 14px 0; font-size: 13px;}}
table {{border-collapse: collapse; width: 100%; font-size: 12px;}} th, td {{border-bottom: 1px solid #DDE3EA; padding: 6px 8px; text-align: right;}}
th {{background: #F3F5F8; color: #1F3A5F;}} td:nth-child(2), th:nth-child(2) {{text-align: left;}}
.note {{font-size: 11px; color: #444; margin-top: 10px;}} @media print {{body {{margin: 0;}}}}
</style></head><body>
<h1>Ridgeline Property Analytics: top 5 candidates by projected ROI</h1>
<div class="sub">Gwinnett County, Georgia · generated {html.escape(generated)} · estimates as of {html.escape(str(params.get('as_of', '')))}
 · {len(scored)} properties screened: {int(counts.get('ranked', 0))} ranked, {int(counts.get('eliminated', 0))} eliminated,
 {int(counts.get('insufficient_comps', 0))} could not be valued, {int(counts.get('unmatched', 0))} could not be matched</div>
{body}
<p class="note">* Renovation = likely figure plus {params['contingency']*100:.0f}% contingency. {NOTICE}</p>
<p class="note">Projected ROI = projected net profit ÷ (asking price + renovation). Selling costs {params['selling_pct']*100:.0f}% of resale value;
holding {params['holding_pct_per_month']*100:.2f}% of asking per month for {params['holding_months']} months. Not included: acquisition-side closing costs
and renovation beyond the contingency. A screening estimate, not a guaranteed or annualized return.</p>
{f'<p class="note">{html.escape(rel)}</p>' if rel else ''}
<p class="note">Data: {html.escape(source_label)}.</p>
</body></html>"""


def _rate(x) -> str:
    """A parameter fraction as a plain percentage: 0.08 -> '8%', 0.0075 -> '0.75%'."""
    return f"{x * 100:g}%"


ROI_FORMULA = "ROI = projected profit ÷ (asking price + renovation)"


def how_calculated(params: dict, top: pd.Series | None = None) -> str:
    """Plain-English explanation of the figures on the results page, with every rate read from `params`
    (assumptions.json) and, when `top` (the number-one ranked row) is given, a worked example from its own figures."""
    bands = params["reno_per_sqft"]
    cutoff = params["age_cutoff_years"]
    hold_total = params["holding_pct_per_month"] * params["holding_months"]
    lines = [
        "- **Asking price** comes from your listing file. Everything else is an estimate.",
        f"- **Resale value (est.)** is estimated from recent sales of similar-sized homes in the same neighborhood "
        f"(at least {params['min_comps']} sales, within {params['window_months'][0]} months, or "
        f"{params['window_months'][-1]} if there are too few), each adjusted to today with the county price index.",
        f"- **Selling costs** are {_rate(params['selling_pct'])} of the resale value.",
        f"- **Holding costs** are {_rate(params['holding_pct_per_month'])} of the asking price per month for "
        f"{params['holding_months']} months ({_rate(hold_total)} of the asking price in total).",
        f"- **Renovation (est.)** is a per-square-foot band chosen by the home's age: likely "
        f"${bands['under20']['likely']:,.2f} per sq ft for homes under {cutoff} years old and "
        f"${bands['20plus']['likely']:,.2f} for {cutoff} years or older, plus {_rate(params['contingency'])} contingency "
        f"on that likely figure. It is a screening estimate, not a construction estimate.",
        "- **Projected profit (est.)** = resale value − selling costs − asking price − renovation − holding costs.",
        f"- **{ROI_FORMULA}.** It is an estimate, not a guaranteed or annualized return.",
    ]
    if top is not None:
        rate = bands[top["reno_tier"]]["likely"]
        sqft = top["reno_likely_base"] / rate
        age = f"under {cutoff} years old" if top["reno_tier"] == "under20" else f"{cutoff} years or older"
        lines += [
            "",
            f"**Worked example: #1, {top['matched_address']}**",
            f"- Asking price: {_money(top['asking_price_used'])}",
            f"- Resale value (est.): {_money(top['arv'])}, based on {int(top['comp_count'])} nearby sales",
            f"- Selling costs: {_rate(params['selling_pct'])} × {_money(top['arv'])} = {_money(top['selling'])}",
            f"- Holding costs: {_rate(params['holding_pct_per_month'])} × {_money(top['asking_price_used'])} × "
            f"{params['holding_months']} months = {_money(top['holding'])}",
            f"- Renovation (est.): {sqft:,.0f} sq ft × ${rate:,.2f} (likely, home {age}) = {_money(top['reno_likely_base'])}, "
            f"+ {_rate(params['contingency'])} contingency = {_money(top['reno_likely_with_contingency'])}",
            f"- Projected profit (est.): {_money(top['arv'])} − {_money(top['selling'])} − {_money(top['asking_price_used'])} − "
            f"{_money(top['reno_likely_with_contingency'])} − {_money(top['holding'])} = {_money(top['net_profit'])}",
            f"- Projected ROI (est.): {_money(top['net_profit'])} ÷ ({_money(top['asking_price_used'])} + "
            f"{_money(top['reno_likely_with_contingency'])}) = {top['roi_pct']:.1f}%",
            "",
            "Figures are rounded to the dollar, so a line can differ from its parts by $1.",
        ]
    return "\n".join(lines)


_COUNT_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def loss_note(top: pd.DataFrame) -> str | None:
    """Warning for the Top 5 when any of them has a negative projected ROI; None when none does."""
    n = int((top["roi_pct"] < 0).sum())
    if n == 0:
        return None
    these = f"these {_COUNT_WORD.get(len(top), len(top))}"
    if n == 1:
        return f"1 of {these} is projected to lose money. It ranks highest only because the others rank lower."
    return f"{n} of {these} are projected to lose money. They rank highest only because the others rank lower."
