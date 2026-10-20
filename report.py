"""Display helpers shared by the dashboard: the back-test reliability line and the printable top-5 summary.
Both only read what already exists (the graded back-test summary and the scored output); nothing is recalculated."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

from ridgeline_core.rules import NOTICE

ROOT = Path(__file__).resolve().parent
BACKTEST_SUMMARY = ROOT / "backtest_results" / "summary_full.json"


def reliability_text(path: Path = BACKTEST_SUMMARY) -> str | None:
    """One sentence from the graded back-test, or None if the summary file is not there."""
    try:
        s = json.loads(Path(path).read_text())
        return (f"How reliable is this? In a back-test on {s['pairs_evaluated']:,} past repeat sales (2018 onward), "
                f"{s['headline_hit_rate_when_valued']*100:.0f}% of estimates landed within {s['rules']['tolerance']*100:.0f}% "
                f"of the next sale price, and {s['coverage']*100:.0f}% of houses could be valued. "
                f"Estimates ran about {abs(s['median_pct_error'])*100:.0f}% {'below' if s['median_pct_error'] < 0 else 'above'} "
                f"the next sale on average. Details: BACKTEST_PROTOCOL.md.")
    except (OSError, KeyError, ValueError, TypeError):
        return None


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
