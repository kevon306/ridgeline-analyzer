"""Business rules: renovation estimate and ROI (approved version one).

Everything here is a plain function of numbers, so each rule can be tested on its own.
Money stays unrounded inside the calculations; round only when displaying.
"""
from __future__ import annotations

NOTICE = "Pre-walkthrough screening estimate, not a construction estimate."


def age_tier(year_built: int, as_of_year: int, cutoff_years: int = 20) -> str:
    """'under20' if the house is younger than the cutoff, else '20plus'.
    A house exactly 20 years old counts as 20plus."""
    return "under20" if (as_of_year - year_built) < cutoff_years else "20plus"


def renovation_estimate(fin_sqft: float, year_built: int, as_of_year: int, params: dict) -> dict:
    """Low / likely / high renovation figures from the age-tier bands.

    The 15% contingency is applied to the LIKELY figure only (low and high stay as the
    bands give them). 'likely_with_contingency' is the number ROI is calculated on.
    Raises ValueError on invalid inputs; the pipeline checks inputs first and turns
    those cases into approved reason codes instead of fabricating an estimate.
    """
    if fin_sqft is None or not (fin_sqft > 0):
        raise ValueError("fin_sqft must be a positive number")
    if year_built is None or not (1700 <= year_built <= as_of_year):
        raise ValueError("year_built must be a plausible year not after the as-of year")
    tier = age_tier(year_built, as_of_year, params["age_cutoff_years"])
    band = params["reno_per_sqft"][tier]
    likely_base = band["likely"] * fin_sqft
    return {
        "tier": tier,
        "low": band["low"] * fin_sqft,
        "likely_base": likely_base,
        "likely_with_contingency": likely_base * (1 + params["contingency"]),
        "high": band["high"] * fin_sqft,
        "notice": NOTICE,
    }


def compute_roi(asking: float, arv: float, renovation: float, params: dict) -> dict:
    """Charter ROI, six-month base case.

    selling  = ARV x selling_pct
    holding  = asking x holding_pct_per_month x holding_months
    net      = ARV - selling - asking - renovation - holding
    roi (%)  = net / (asking + renovation) x 100     <- charter denominator
    roi is None if the denominator is not positive (it is never invented).
    """
    selling = arv * params["selling_pct"]
    holding = asking * params["holding_pct_per_month"] * params["holding_months"]
    net = arv - selling - asking - renovation - holding
    denom = asking + renovation
    roi = (net / denom * 100.0) if denom > 0 else None
    return {"selling": selling, "holding": holding, "net_profit": net, "denominator": denom, "roi_pct": roi}


def passes_price_screen(arv: float, asking: float, min_spread: float) -> tuple[bool, float]:
    """Pass when ARV - asking is at least min_spread (exactly the minimum passes)."""
    spread = arv - asking
    return spread >= min_spread, spread
