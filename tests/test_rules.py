import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ridgeline_core.arv import load_params  # noqa: E402
from ridgeline_core.rules import (age_tier, renovation_estimate, compute_roi,  # noqa: E402
                                  passes_price_screen, NOTICE)

P = load_params(Path(__file__).resolve().parents[1] / "assumptions.json")


def test_age_tier_boundary_exactly_20_years_is_20plus():
    assert age_tier(2007, 2026) == "under20"   # 19 years
    assert age_tier(2006, 2026) == "20plus"    # exactly 20 years


def test_renovation_under20_bands_and_contingency_on_likely_only():
    r = renovation_estimate(1500, 2011, 2026, P)
    assert r["tier"] == "under20"
    assert r["low"] == pytest.approx(19.40 * 1500)
    assert r["likely_base"] == pytest.approx(35_400)            # the example in the spec
    assert r["likely_with_contingency"] == pytest.approx(35_400 * 1.15)
    assert r["high"] == pytest.approx(41.70 * 1500)             # no contingency on high


def test_renovation_20plus_bands():
    r = renovation_estimate(1500, 1990, 2026, P)
    assert r["tier"] == "20plus"
    assert r["likely_base"] == pytest.approx(41.70 * 1500)
    assert r["high"] == pytest.approx(55.60 * 1500)


def test_renovation_always_carries_the_notice():
    assert renovation_estimate(1500, 1990, 2026, P)["notice"] == NOTICE


@pytest.mark.parametrize("sqft,year", [(None, 1990), (0, 1990), (-5, 1990), (1500, None), (1500, 1500), (1500, 2030)])
def test_renovation_rejects_invalid_inputs(sqft, year):
    with pytest.raises(ValueError):
        renovation_estimate(sqft, year, 2026, P)


def test_roi_worked_example_1_positive():
    reno = renovation_estimate(1500, 1990, 2026, P)["likely_with_contingency"]
    r = compute_roi(300_000, 450_000, reno, P)
    assert reno == pytest.approx(71_932.50)
    assert r["holding"] == pytest.approx(13_500)
    assert r["selling"] == pytest.approx(36_000)
    assert r["net_profit"] == pytest.approx(28_567.50)
    assert r["roi_pct"] == pytest.approx(7.68, abs=0.005)      # charter denominator: asking + renovation


def test_roi_worked_example_2_passes_screen_but_negative():
    ok, spread = passes_price_screen(380_000, 330_000, P["min_spread"])
    assert ok and spread == 50_000
    reno = renovation_estimate(1800, 2011, 2026, P)["likely_with_contingency"]
    r = compute_roi(330_000, 380_000, reno, P)
    assert r["net_profit"] == pytest.approx(-44_102)
    assert r["roi_pct"] == pytest.approx(-11.64, abs=0.005)


def test_roi_zero_denominator_is_none_not_invented():
    assert compute_roi(0, 100_000, 0, P)["roi_pct"] is None


def test_price_screen_boundary():
    assert passes_price_screen(335_000, 300_000, 35_000) == (True, 35_000)     # exactly the minimum passes
    assert passes_price_screen(334_999, 300_000, 35_000)[0] is False
