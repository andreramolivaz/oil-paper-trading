"""The desk's forecasts: causal, on Carver's scale, and combined the same way as a series and as a scalar.

All inputs are SYNTHETIC (seeded random walks): these tests pin down the arithmetic, not a market fact.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.desk import signals as sg


def _returns(n: int = 900, seed: int = 5, drift: float = 0.0, vol: float = 0.02) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(rng.normal(drift, vol, size=n), index=idx)


def test_ew_vol_is_annualised_and_floored():
    r = _returns(vol=0.02)
    vol = sg.ew_vol(r)
    assert vol.iloc[:19].isna().all() and vol.iloc[19:].notna().all()
    assert 0.25 < float(vol.iloc[-1]) < 0.40  # 2 % a day is about 32 % a year
    quiet = sg.ew_vol(_returns(vol=0.0005))
    assert float(quiet.dropna().min()) == pytest.approx(sg.VOL_FLOOR)


@pytest.mark.parametrize("cut", [300, 450, 700, 899])
def test_no_lookahead_truncating_the_input_changes_nothing_before_the_cut(cut):
    r = _returns()
    slope = pd.Series(np.sin(np.arange(len(r)) / 30.0) * 0.2, index=r.index)
    full = {
        "trend": sg.trend_forecast(r),
        "carry": sg.carry_forecast(slope),
        "carry_momentum": sg.carry_momentum_forecast(slope),
        "vol": sg.ew_vol(r),
    }
    full["combined"] = sg.combine({k: full[k] for k in sg.SLEEVES})
    part = {
        "trend": sg.trend_forecast(r.iloc[:cut]),
        "carry": sg.carry_forecast(slope.iloc[:cut]),
        "carry_momentum": sg.carry_momentum_forecast(slope.iloc[:cut]),
        "vol": sg.ew_vol(r.iloc[:cut]),
    }
    part["combined"] = sg.combine({k: part[k] for k in sg.SLEEVES})
    for name, series in part.items():
        pd.testing.assert_series_equal(series, full[name].iloc[:cut], check_names=False, obj=name)


def test_trend_follows_the_sign_of_a_steady_trend_and_respects_the_cap():
    up = sg.trend_forecast(_returns(drift=0.004, vol=0.01))
    down = sg.trend_forecast(_returns(drift=-0.004, vol=0.01))
    assert float(up.iloc[-1]) > 10 and float(down.iloc[-1]) < -10
    assert float(up.max()) <= sg.FORECAST_CAP and float(down.min()) >= -sg.FORECAST_CAP
    assert up.iloc[:255].isna().all()  # the slowest filter needs 256 days: no forecast before all four exist


def test_trend_average_size_is_about_ten_on_a_random_walk():
    sizes = [float(sg.trend_forecast(_returns(n=3000, seed=s)).abs().mean()) for s in range(6)]
    assert 6.0 < float(np.mean(sizes)) < 14.0


def test_carry_is_a_sign_with_a_deadband():
    slope = pd.Series([0.30, 0.011, 0.009, 0.0, -0.009, -0.011, -0.40, np.nan])
    out = sg.carry_forecast(slope)
    assert out.iloc[:7].tolist() == [10.0, 10.0, 0.0, 0.0, 0.0, -10.0, -10.0]
    assert math.isnan(out.iloc[7])


def test_carry_momentum_compares_the_slope_with_its_own_average():
    slope = pd.Series(np.linspace(0.0, 0.5, 60))  # rising: always above its trailing mean
    out = sg.carry_momentum_forecast(slope)
    assert out.iloc[:19].isna().all()
    assert (out.iloc[19:] == 10.0).all()
    assert (sg.carry_momentum_forecast(-slope).iloc[19:] == -10.0).all()


def test_combine_matches_its_scalar_twin_row_by_row():
    r = _returns()
    slope = pd.Series(np.cos(np.arange(len(r)) / 17.0) * 0.1, index=r.index)
    parts = {
        "trend": sg.trend_forecast(r),
        "carry": sg.carry_forecast(slope),
        "carry_momentum": sg.carry_momentum_forecast(slope),
    }
    parts["carry"].iloc[400:420] = np.nan  # a hole in one sleeve
    weights = {"trend": 2.0, "carry": 1.0, "carry_momentum": 1.0}
    combined = sg.combine(parts, weights)
    for i in (10, 300, 410, 600, 899):
        row = {k: (None if pd.isna(v.iloc[i]) else float(v.iloc[i])) for k, v in parts.items()}
        scalar = sg.combine_values(row, weights)
        if scalar is None:
            assert pd.isna(combined.iloc[i])
        else:
            assert combined.iloc[i] == pytest.approx(scalar)


def test_combine_renormalises_and_shrinks_the_multiplier_when_a_sleeve_is_missing():
    assert sg.combine_values({"trend": 10.0, "carry": 10.0, "carry_momentum": 10.0}) == pytest.approx(12.5)
    assert sg.combine_values({"trend": 10.0, "carry": 10.0, "carry_momentum": None}) == pytest.approx(11.25)
    assert sg.combine_values({"trend": 10.0, "carry": None, "carry_momentum": None}) == pytest.approx(10.0)
    assert sg.combine_values({"trend": None, "carry": None, "carry_momentum": None}) is None
    assert sg.combine_values({"trend": 20.0, "carry": 20.0, "carry_momentum": 20.0}) == sg.FORECAST_CAP
    # a sleeve with zero weight is not in the book at all
    assert sg.combine_values({"trend": 10.0, "carry": -10.0}, {"trend": 1.0, "carry": 0.0}) == pytest.approx(10.0)


def test_exposure_is_forecast_over_ten_times_target_over_vol_then_capped():
    assert sg.exposure_fraction(10.0, 0.40, 0.20, 10.0) == pytest.approx(0.5)
    assert sg.exposure_fraction(20.0, 0.40, 0.20, 10.0) == pytest.approx(1.0)
    assert sg.exposure_fraction(-10.0, 0.25, 0.50, 10.0) == pytest.approx(-2.0)
    assert sg.exposure_fraction(20.0, 0.10, 0.50, 3.0) == 3.0  # the ceiling wins whatever the inputs say
    assert sg.exposure_fraction(-20.0, 0.10, 0.50, 3.0) == -3.0
    assert sg.exposure_fraction(-10.0, 0.25, 0.50, 10.0, long_only=True) == 0.0
    for bad in (float("nan"), float("inf")):
        assert sg.exposure_fraction(bad, 0.3, 0.2, 10.0) == 0.0
        assert sg.exposure_fraction(10.0, bad, 0.2, 10.0) == 0.0
    assert sg.exposure_fraction(10.0, 0.0, 0.2, 10.0) == 0.0
