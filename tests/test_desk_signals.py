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


def _closes(n: int = 900, seed: int = 11, vol: float = 0.012) -> pd.Series:
    """SYNTHETIC daily closes of another market (a seeded geometric random walk)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(4.0 * np.exp(np.cumsum(rng.normal(0.0, vol, size=n))), index=idx)


def _all_sleeves(r: pd.Series, slope: pd.Series, copper: pd.Series, dollar: pd.Series) -> dict[str, pd.Series]:
    return {
        "trend": sg.trend_forecast(r),
        "accel": sg.accel_forecast(r),
        "skew": sg.skew_forecast(r),
        "carry": sg.carry_forecast(slope),
        "carry_momentum": sg.carry_momentum_forecast(slope),
        "copper": sg.macro_trend_forecast(copper),
        "dollar": sg.macro_trend_forecast(dollar, inverse=True),
    }


def test_the_seven_sleeves_come_from_three_sources_that_weigh_the_same():
    assert sg.SLEEVES == ("trend", "accel", "skew", "carry", "carry_momentum", "copper", "dollar")
    assert {source: len(members) for source, members in sg.SOURCES.items()} == {"prezzo": 3, "curva": 2, "macro": 2}
    assert sum(sg.DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)
    for members in sg.SOURCES.values():
        assert sum(sg.DEFAULT_WEIGHTS[name] for name in members) == pytest.approx(1.0 / 3.0)
        assert len({sg.DEFAULT_WEIGHTS[name] for name in members}) == 1  # equal inside a source


@pytest.mark.parametrize("cut", [300, 450, 700, 899, 1100])
def test_no_lookahead_truncating_the_input_changes_nothing_before_the_cut(cut):
    r = _returns(n=1200)
    slope = pd.Series(np.sin(np.arange(len(r)) / 30.0) * 0.2, index=r.index)
    copper, dollar = _closes(1200, seed=11), _closes(1200, seed=12)
    full = _all_sleeves(r, slope, copper, dollar)
    assert all(series.notna().any() for series in full.values())  # every sleeve says something on this sample
    full["vol"] = sg.ew_vol(r)
    full["combined"] = sg.combine({k: full[k] for k in sg.SLEEVES})
    part = _all_sleeves(r.iloc[:cut], slope.iloc[:cut], copper.iloc[:cut], dollar.iloc[:cut])
    part["vol"] = sg.ew_vol(r.iloc[:cut])
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


def test_acceleration_is_the_change_of_the_trend_not_the_trend():
    idx = pd.bdate_range("2019-01-01", periods=900)
    # an alternating +-0.4 % in place of random noise: its mean is zero and its volatility constant over any
    # window, so what the forecast does below is the drift's doing and not a lucky or a quiet patch of a seed
    noise = np.where(np.arange(900) % 2 == 0, 0.004, -0.004)
    speeding = pd.Series(noise + np.where(np.arange(900) >= 840, 0.006, 0.0), index=idx)  # a trend that starts
    fading = pd.Series(noise + np.where(np.arange(900) >= 840, 0.0, 0.004), index=idx)  # a trend that stops
    up, down = sg.accel_forecast(speeding), sg.accel_forecast(fading)
    assert float(up.iloc[-1]) > 5.0
    # the trend itself is still up when it stops; what turns negative is its change
    assert float(sg.trend_forecast(fading).iloc[-1]) > 0 and float(down.iloc[-1]) < -5.0
    assert float(up.max()) <= sg.FORECAST_CAP and float(down.min()) >= -sg.FORECAST_CAP
    assert up.iloc[:319].isna().all() and up.iloc[320:].notna().all()  # the slowest speed needs 256 + 64 days
    # on a steady trend the change of the trend averages out near zero, whatever the sign of the trend
    steady = sg.accel_forecast(_returns(n=3000, drift=0.002, vol=0.01))
    assert abs(float(steady.mean())) < 2.0
    sizes = [float(sg.accel_forecast(_returns(n=3000, seed=s)).abs().mean()) for s in range(6)]
    assert 6.0 < float(np.mean(sizes)) < 14.0


def test_skew_is_long_a_market_whose_returns_turned_more_negatively_skewed_than_its_own_past():
    rng = np.random.default_rng(21)
    idx = pd.bdate_range("2015-01-01", periods=1500)
    base = rng.normal(0.0, 0.02, size=1500)  # symmetric for five years
    crashes = base.copy()
    crashes[1250:] = 0.004  # then a slow grind up ...
    crashes[1260::25] = -0.09  # ... broken by rare large falls: negative skew
    spikes = base.copy()
    spikes[1250:] = -0.004
    spikes[1260::25] = 0.09
    negative = sg.skew_forecast(pd.Series(crashes, index=idx))
    positive = sg.skew_forecast(pd.Series(spikes, index=idx))
    assert float(negative.iloc[-1]) > 10.0  # investors are paid to hold what can crash
    assert float(positive.iloc[-1]) < -10.0  # and pay for what can spike
    assert float(negative.max()) <= sg.FORECAST_CAP and float(positive.min()) >= -sg.FORECAST_CAP
    # nothing before the slower lookback has 500 days of its own history to be compared with
    first = 365 + sg.SKEW_MIN_HISTORY - 2  # position of the first day with both
    assert negative.iloc[:first].isna().all() and negative.iloc[first + 20 :].notna().all()


def test_a_macro_sleeve_is_the_trend_rule_on_another_market_and_the_dollar_is_inverted():
    closes = _closes()
    own_rule = sg.trend_forecast(closes.pct_change())
    pd.testing.assert_series_equal(sg.macro_trend_forecast(closes), own_rule)
    pd.testing.assert_series_equal(sg.macro_trend_forecast(closes, inverse=True), -own_rule)
    rising = pd.Series(np.exp(np.linspace(0.0, 1.2, 600)), index=pd.bdate_range("2021-01-01", periods=600))
    rising = rising * (1.0 + np.random.default_rng(3).normal(0.0, 0.004, size=600))
    assert float(sg.macro_trend_forecast(rising).iloc[-1]) > 10.0  # copper up: the cycle is a tailwind
    assert float(sg.macro_trend_forecast(rising, inverse=True).iloc[-1]) < -10.0  # dollar up: a headwind


def test_combine_matches_its_scalar_twin_row_by_row():
    r = _returns(n=1200)
    slope = pd.Series(np.cos(np.arange(len(r)) / 17.0) * 0.1, index=r.index)
    parts = _all_sleeves(r, slope, _closes(1200, seed=11), _closes(1200, seed=12))
    parts["carry"].iloc[900:920] = np.nan  # a hole in one sleeve
    parts["copper"].iloc[1000:1030] = np.nan  # one sleeve of a source missing
    parts["dollar"].iloc[1010:1040] = np.nan  # ... then the whole source
    for weights in (None, {"trend": 2.0, "carry": 1.0, "carry_momentum": 1.0, "copper": 0.5}):
        combined = sg.combine(parts, weights)
        for i in (10, 300, 700, 905, 1005, 1020, 1035, 1199):
            row = {k: (None if pd.isna(v.iloc[i]) else float(v.iloc[i])) for k, v in parts.items()}
            scalar = sg.combine_values(row, weights)
            if scalar is None:
                assert pd.isna(combined.iloc[i])
            else:
                assert combined.iloc[i] == pytest.approx(scalar)


def test_combine_weighs_the_sources_and_shrinks_the_multiplier_when_sleeves_are_missing():
    ten = dict.fromkeys(sg.SLEEVES, 10.0)
    assert sg.combine_values(ten) == pytest.approx(10.0 * sg.COMBINED_FDM)  # 17.5
    assert sg.combine_values(dict.fromkeys(sg.SLEEVES, 20.0)) == sg.FORECAST_CAP
    assert sg.combine_values(dict.fromkeys(sg.SLEEVES)) is None
    # a third each: (price 12 + curve 0 + macro -3) / 3 = 3, times the full multiplier
    mixed = {"trend": 14.0, "accel": 6.0, "skew": 16.0, "carry": 10.0, "carry_momentum": -10.0}
    mixed |= {"copper": 7.0, "dollar": -13.0}
    assert sg.source_values(mixed) == pytest.approx({"prezzo": 12.0, "curva": 0.0, "macro": -3.0})
    assert sg.combine_values(mixed) == pytest.approx(3.0 * 1.75)
    # one sleeve of a source missing: the source keeps its third and is read on the sleeve that is left
    no_copper = {**mixed, "copper": None}
    assert sg.source_values(no_copper)["macro"] == pytest.approx(-13.0)
    assert sg.combine_values(no_copper) == pytest.approx((12.0 + 0.0 - 13.0) / 3.0 * (1.0 + 0.75 * 5 / 6))
    # a whole source missing: the other two share the forecast, and the multiplier is smaller still
    no_macro = {**mixed, "copper": None, "dollar": None}
    assert sg.source_values(no_macro)["macro"] is None
    assert sg.combine_values(no_macro) == pytest.approx((12.0 + 0.0) / 2.0 * (1.0 + 0.75 * 4 / 6))
    # the three sleeves the desk read before the other four existed: the same number as then
    assert sg.combine_values({"trend": 10.0, "carry": 10.0, "carry_momentum": 10.0}) == pytest.approx(12.5)
    assert sg.combine_values({"trend": 10.0, "carry": 10.0, "carry_momentum": None}) == pytest.approx(11.25)
    assert sg.combine_values({"trend": 10.0}) == pytest.approx(10.0)  # one sleeve alone has no multiplier
    # a sleeve with zero weight is not in the book at all, and a book given a subset is not over-scaled
    assert sg.combine_values({"trend": 10.0, "carry": -10.0}, {"trend": 1.0, "carry": 0.0}) == pytest.approx(10.0)
    price_only = dict.fromkeys(sg.SOURCES["prezzo"], 1.0)
    assert sg.combine_values(ten, price_only) == pytest.approx(10.0 * (1.0 + 0.75 * 2 / 6))
    with pytest.raises(ValueError):
        sg.combine_values(ten, {"trend": 0.0})


def test_a_sleeve_that_was_not_given_counts_as_missing_on_every_day():
    r = _returns()
    given = {"trend": sg.trend_forecast(r), "accel": sg.accel_forecast(r)}
    explicit = {**given, **{k: pd.Series(np.nan, index=r.index) for k in sg.SLEEVES if k not in given}}
    pd.testing.assert_series_equal(sg.combine(given), sg.combine(explicit))
    with pytest.raises(ValueError):
        sg.combine({})


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
