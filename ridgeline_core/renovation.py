"""Renovation budget: the approved band model made explicit, risk flags, an evidence-based confidence label,
an optional user-entered scope by category, and deal outcomes under low / expected / high renovation scenarios.

Nothing here invents a price. The only built-in prices are the approved per-square-foot bands in assumptions.json
(one contractor's ranges, not validated against completed projects). Everything else comes from county facts
or from numbers the user enters, and each figure carries its origin: "county record", "model assumption",
"your entry".

Scoring and ranking still use rules.renovation_estimate (likely band + contingency) unchanged.
"""
from __future__ import annotations

import math

from .rules import NOTICE, age_tier

MODEL_VERSION = "bands-v1"      # approved version one: age-tier $/sq ft bands, contingency on the likely figure only
MIN_VALIDATION_RECORDS = 30     # completed projects with verified actual cost needed before any rating above Low

# Repair categories a user can price from an inspection or contractor quotes. No default prices: blank = not estimated.
CATEGORIES = [
    "Interior paint", "Flooring and subfloor", "Kitchen (cabinets, counters, appliances)", "Bathrooms (fixtures, tile, plumbing)",
    "Roof", "HVAC", "Electrical", "Plumbing", "Windows and exterior doors", "Siding, exterior paint and repairs",
    "Drywall, trim and interior doors", "Structural and foundation", "Water damage and mold", "Demolition, hauling and disposal",
    "Permits and inspections", "Landscaping and exterior cleanup", "Contractor overhead and general labor", "Other",
]


def model_scenarios(fin_sqft: float, year_built: int, as_of_year: int, params: dict) -> dict:
    """Low / expected / high from the approved bands, with base and contingency separate.
    Under the approved rule the contingency applies to the likely (expected) figure only."""
    tier = age_tier(year_built, as_of_year, params["age_cutoff_years"])
    band = params["reno_per_sqft"][tier]
    out = {"tier": tier, "rates": dict(band), "fin_sqft": fin_sqft, "origin": "model assumption", "notice": NOTICE}
    for name, key, cont in (("low", "low", 0.0), ("expected", "likely", params["contingency"]), ("high", "high", 0.0)):
        base = band[key] * fin_sqft
        out[name] = {"base": base, "contingency": base * cont, "total": base * (1 + cont), "contingency_pct": cont}
    return out


def scope_budget(items: list[dict], contingency_pct: float) -> dict | None:
    """Sum a user-entered scope. Each category counts once; contingency is applied once, to the base of each scenario.
    items: {"category", "low", "expected", "high", "source"}; rows without an expected figure are ignored.
    Returns None when nothing usable was entered. Raises ValueError on inconsistent rows."""
    if contingency_pct is None or not (0 <= contingency_pct <= 1):
        raise ValueError("contingency must be between 0% and 100%")
    rows, seen = [], set()
    for it in items:
        exp = _num(it.get("expected"))
        if exp is None:
            continue
        cat = str(it.get("category") or "").strip() or "Other"
        if cat in seen:
            raise ValueError(f"'{cat}' is entered twice; combine it into one row so it is not counted twice")
        seen.add(cat)
        low, high = _num(it.get("low")), _num(it.get("high"))
        low = exp if low is None else low
        high = exp if high is None else high
        if min(low, exp, high) < 0 or not (low <= exp <= high):
            raise ValueError(f"'{cat}': figures must be 0 or more and low <= expected <= high")
        rows.append({"category": cat, "low": low, "expected": exp, "high": high, "source": str(it.get("source") or "").strip()})
    if not rows:
        return None
    out = {"rows": rows, "origin": "your entry", "contingency_pct": contingency_pct,
           "unsourced": [r["category"] for r in rows if not r["source"]]}
    for name in ("low", "expected", "high"):
        base = sum(r[name] for r in rows)
        out[name] = {"base": base, "contingency": base * contingency_pct, "total": base * (1 + contingency_pct),
                     "contingency_pct": contingency_pct}
    return out


def deal_outcomes(asking: float, arv: float, scenarios: dict, params: dict, closing: float = 0.0, financing: float = 0.0) -> list[dict]:
    """Profit and ROI under each renovation scenario. Same charter formula as rules.compute_roi; closing and
    financing costs (user-entered, default 0 = not included) reduce profit. ROI denominator stays asking + renovation."""
    selling = arv * params["selling_pct"]
    holding = asking * params["holding_pct_per_month"] * params["holding_months"]
    out = []
    for name in ("low", "expected", "high"):
        reno = scenarios[name]["total"]
        net = arv - selling - asking - reno - holding - closing - financing
        denom = asking + reno
        out.append({"scenario": name, "renovation": reno, "selling": selling, "holding": holding, "closing": closing,
                    "financing": financing, "net_profit": net, "roi_pct": net / denom * 100 if denom > 0 else None})
    return out


def outcome_warning(outcomes: list[dict]) -> str | None:
    """Plain-English warning when the verdict depends on which renovation scenario comes true."""
    by = {o["scenario"]: o["net_profit"] for o in outcomes}
    if by["expected"] > 0 and by["high"] <= 0:
        return "Profitable at the expected renovation cost, but a loss if renovation comes in at the high figure."
    if by["low"] > 0 and by["expected"] <= 0:
        return "A loss at the expected renovation cost; profitable only if renovation comes in at the low figure."
    if by["low"] <= 0:
        return "A loss even at the low renovation figure."
    return None


def risk_flags(year_built, cond_desc, as_of_year: int, params: dict) -> list[dict]:
    """Things to check, from county facts. Flags never add cost; they say where the estimate could be wrong."""
    flags = []
    age = None if year_built is None or (isinstance(year_built, float) and math.isnan(year_built)) else as_of_year - int(year_built)
    cutoff = params["age_cutoff_years"]
    flags.append({"item": "Roof, HVAC, water heater", "level": "check" if age is None or age < cutoff else "elevated",
                  "why": "No replacement dates are on record for any system."
                         + (f" The house is {age} years old (county record), at or past the {cutoff}-year age tier." if age is not None and age >= cutoff else ""),
                  "action": "Ask the seller for replacement dates and receipts; inspect before relying on the estimate."})
    if age is not None and age >= cutoff:
        flags.append({"item": "Electrical and plumbing", "level": "check",
                      "why": f"House built {int(year_built)} (county record); no documentation of updates on record.",
                      "action": "Have an inspector check the panel, wiring and supply/drain lines."})
    cond = (cond_desc or "").strip().lower()
    if cond in ("below normal for age", "poor for age", "very poor for age"):
        flags.append({"item": "Overall condition", "level": "elevated",
                      "why": f"County assessor rates the house '{cond}'. The per-square-foot bands assume typical condition, "
                             "so the scope may exceed even the high figure.",
                      "action": "Get an itemized contractor quote before making an offer."})
    flags.append({"item": "Water damage, structure", "level": "unknown",
                  "why": "No inspection findings are on record, so hidden damage cannot be ruled in or out.",
                  "action": "Budget for an inspection; water or structural problems are the most common large surprises."})
    return flags


def confidence(has_size_and_age: bool, scope: dict | None, validation: dict | None, inspected: bool = False) -> dict:
    """Evidence-based label for the renovation estimate (separate from ARV and ROI confidence). Not a percentage:
    no rating here has been statistically calibrated. Rules:
      Insufficient data : no living area or year built on record.
      Low               : approved bands only, or the bands have not been validated on enough completed projects.
      Moderate          : itemized scope you entered, every row with a source, OR bands validated on enough projects.
      High              : itemized, sourced scope based on an inspection AND validation on enough completed projects.
    """
    n_valid = (validation or {}).get("n", 0)
    validated = n_valid >= MIN_VALIDATION_RECORDS
    sourced_scope = bool(scope) and not scope["unsourced"]
    reasons = []
    if not has_size_and_age:
        return {"label": "Insufficient data", "reasons": ["No living area or year built on the county record."]}
    reasons.append(f"Validation: {n_valid} completed project(s) with a verified actual cost on record "
                   f"({MIN_VALIDATION_RECORDS} needed before the model's accuracy can be measured).")
    if scope:
        reasons.append("Itemized scope entered by you" + (", every row with a source." if sourced_scope
                                                         else f"; no source given for: {', '.join(scope['unsourced'])}."))
    else:
        reasons.append("No itemized scope: one per-square-foot band for the whole house, from one contractor's ranges.")
    reasons.append("Inspection: " + ("findings entered." if inspected else "none on record."))
    if sourced_scope and inspected and validated:
        label = "High"
    elif sourced_scope or validated:
        label = "Moderate"
    else:
        label = "Low"
    return {"label": label, "reasons": reasons}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v
