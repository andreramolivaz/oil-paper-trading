"""Offline deterministic tests for the carry/term-structure family (S4, S5, S6).

ALL DATA IN THIS MODULE IS SYNTHETIC: hand-built feature frames designed to trigger a documented branch.
The frame builders are shared with tests/test_strategies_g1.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.core.events import Direction
from engine.features.catalog import (
    BUTTERFLY_1_3_6,
    CRUDE_STOCKS_VS_5Y,
    CURVE_APPROX,
    GEO_SPIKE,
    ROLL_YIELD_ANN,
    ROLL_YIELD_PCTL,
    SLOPE_M1_M3,
    SPOT_FRONT_PREMIUM,
    SPREAD_CHG_5,
)
from engine.strategies.s04_carry import S4CarryRollYield
from engine.strategies.s05_calendar_spread import S5CalendarSpread
from engine.strategies.s06_butterfly import S6CurveButterfly
from tests.test_strategies_g1 import (
    ar1,
    assert_valid,
    base_frame,
    make_ctx,
    set_last,
    set_tail,
)


def _spike_last(df: pd.DataFrame, col: str, sigmas: float, window: int = 252) -> pd.DataFrame:
    """Put the last value `sigmas` standard deviations away from the mean of the trailing window."""
    out = df.copy()
    prior = out[col].iloc[-window:-1]
    out.iloc[-1, out.columns.get_loc(col)] = float(prior.mean()) + sigmas * float(prior.std())
    return out


def _flatten_last(df: pd.DataFrame, col: str, window: int = 252) -> pd.DataFrame:
    """Set the last value to the mean of the trailing window, so the rolling z-score is exactly 0."""
    return _spike_last(df, col, 0.0, window)


# =========================================================================== S4
def test_s4_long_above_60th_and_short_below_40th():
    df = _spike_last(base_frame(), ROLL_YIELD_ANN, 2.0)
    long_sig = S4CarryRollYield().generate(make_ctx(set_last(df, **{ROLL_YIELD_PCTL: 0.80})))
    assert long_sig is not None and long_sig.direction is Direction.LONG
    assert_valid(long_sig)
    # size proportional to |pctl - 0.5| (=0.6 after doubling), vol-scaled by vol_scale(0.15, 0.30) = 0.5
    assert abs(long_sig.strength - 0.5 * 0.6) < 1e-12
    assert long_sig.stop_pct == 3.0 * 0.02
    assert long_sig.meta["curve_approx"] is False

    short_sig = S4CarryRollYield().generate(make_ctx(set_last(df, **{ROLL_YIELD_PCTL: 0.20})))
    assert short_sig is not None and short_sig.direction is Direction.SHORT
    assert_valid(short_sig)
    assert abs(short_sig.strength - 0.5 * 0.6) < 1e-12


def test_s4_none_inside_the_percentile_band_and_on_missing_data():
    df = _spike_last(base_frame(), ROLL_YIELD_ANN, 2.0)
    for pctl in (0.45, 0.50, 0.55, 0.60, 0.40):
        assert S4CarryRollYield().generate(make_ctx(set_last(df, **{ROLL_YIELD_PCTL: pctl}))) is None
    missing = set_last(df, **{ROLL_YIELD_PCTL: float("nan")})
    assert S4CarryRollYield().generate(make_ctx(missing)) is None
    # thresholds are parameters
    tight = S4CarryRollYield(params={"long_pctl": 0.50})
    assert tight.generate(make_ctx(set_last(df, **{ROLL_YIELD_PCTL: 0.55}))) is not None


def test_s4_approx_curve_halves_the_size():
    df = _spike_last(base_frame(), ROLL_YIELD_ANN, 2.0)
    df = set_last(df, **{ROLL_YIELD_PCTL: 0.80})
    real = S4CarryRollYield().generate(make_ctx(df))
    proxy = S4CarryRollYield().generate(make_ctx(set_last(df, **{CURVE_APPROX: 1.0})))
    assert real is not None and proxy is not None
    assert abs(proxy.strength - real.strength / 2.0) < 1e-12
    assert proxy.meta["approx"] is True and "≈" in proxy.rationale


def test_s4_determinism_and_params_exposed():
    df = set_last(_spike_last(base_frame(), ROLL_YIELD_ANN, 2.0), **{ROLL_YIELD_PCTL: 0.80})
    ctx = make_ctx(df)
    strat = S4CarryRollYield()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None and first.to_dict() == second.to_dict()
    for key in ("long_pctl", "short_pctl", "approx_size_mult", "stop_atr_mult", "target_vol", "z_window"):
        assert key in S4CarryRollYield.default_params()


# =========================================================================== S5
def _squeeze_frame() -> pd.DataFrame:
    """Momentum branch: spread widening, stocks below the 5y mean, positive spot premium."""
    df = base_frame()
    return set_last(df, **{SPREAD_CHG_5: 0.004, CRUDE_STOCKS_VS_5Y: -0.03, SPOT_FRONT_PREMIUM: 0.01})


def _spike_frame() -> pd.DataFrame:
    """Mean-reversion branch: spread z far above the mean, geo spike, stocks not falling."""
    df = set_tail(base_frame(), 25, **{CRUDE_STOCKS_VS_5Y: 0.01})
    df = _spike_last(df, SLOPE_M1_M3, 8.0)
    return set_last(df, **{SPREAD_CHG_5: -0.001, GEO_SPIKE: 1.0})


def test_s5_momentum_branch_goes_long_the_spread():
    sig = S5CalendarSpread().generate(make_ctx(_squeeze_frame()))
    assert sig is not None and sig.direction is Direction.LONG
    assert_valid(sig)
    assert sig.instrument == "BZZ26-BZG27"
    assert sig.meta["branch"] == "momentum"
    assert sig.meta["multi_leg"] is True
    assert sig.meta["legs"] == [("BZZ26", 1.0), ("BZG27", -1.0)]
    assert "stretta fisica" in sig.rationale.lower()


def test_s5_mean_reversion_branch_shorts_the_spread():
    sig = S5CalendarSpread().generate(make_ctx(_spike_frame()))
    assert sig is not None and sig.direction is Direction.SHORT
    assert_valid(sig)
    assert sig.meta["branch"] == "mean_reversion"
    assert sig.meta["z"] > 2.5
    assert sig.instrument == "BZZ26-BZG27"


def test_s5_none_without_the_documented_conditions():
    # no squeeze, no spike
    assert S5CalendarSpread().generate(make_ctx(base_frame())) is None
    # spike without geo_spike
    no_geo = set_last(_spike_frame(), **{GEO_SPIKE: 0.0})
    assert S5CalendarSpread().generate(make_ctx(no_geo)) is None
    # squeeze with stocks ABOVE the 5y mean
    stocks_high = set_last(_squeeze_frame(), **{CRUDE_STOCKS_VS_5Y: 0.02})
    assert S5CalendarSpread().generate(make_ctx(stocks_high)) is None
    # squeeze without a spot premium
    no_premium = set_last(_squeeze_frame(), **{SPOT_FRONT_PREMIUM: -0.01})
    assert S5CalendarSpread().generate(make_ctx(no_premium)) is None
    # missing curve codes
    assert S5CalendarSpread().generate(make_ctx(_squeeze_frame(), curve_codes={"M1": "BZZ26"})) is None


def test_s5_approx_curve_suppression():
    df = _squeeze_frame()
    assert S5CalendarSpread().generate(make_ctx(set_last(df, **{CURVE_APPROX: 1.0}))) is None
    # real today, but fewer than min_real_curve_days real rows in the trailing window
    partial = df.copy()
    partial.iloc[-120:-60, partial.columns.get_loc(CURVE_APPROX)] = 1.0
    assert S5CalendarSpread().generate(make_ctx(partial)) is None
    # the requirement is a parameter (incubation until the real curve is long enough)
    lenient = S5CalendarSpread(params={"min_real_curve_days": 30})
    assert lenient.generate(make_ctx(partial)) is not None


def test_s5_determinism_and_params_exposed():
    ctx = make_ctx(_squeeze_frame())
    strat = S5CalendarSpread()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None and first.to_dict() == second.to_dict()
    for key in ("min_real_curve_days", "mr_entry_z", "z_window", "stocks_level_max", "spot_premium_min"):
        assert key in S5CalendarSpread.default_params()


# =========================================================================== S6
def test_s6_short_rich_curvature_and_long_cheap_curvature():
    rich = S6CurveButterfly().generate(make_ctx(_spike_last(base_frame(), BUTTERFLY_1_3_6, 6.0)))
    assert rich is not None and rich.direction is Direction.SHORT
    assert_valid(rich)
    assert rich.instrument == "FLY-BZZ26-BZG27-BZK27"
    assert rich.meta["multi_leg"] is True
    assert rich.meta["legs"] == [("BZZ26", 1.0), ("BZG27", -2.0), ("BZK27", 1.0)]
    assert rich.meta["z"] > 2.0 and rich.meta["half_life_days"] < 20.0

    cheap = S6CurveButterfly().generate(make_ctx(_spike_last(base_frame(), BUTTERFLY_1_3_6, -6.0)))
    assert cheap is not None and cheap.direction is Direction.LONG
    assert_valid(cheap)


def test_s6_exit_band_emits_a_flat_signal():
    df = _flatten_last(base_frame(), BUTTERFLY_1_3_6)
    sig = S6CurveButterfly().generate(make_ctx(df))
    assert sig is not None and sig.direction is Direction.FLAT
    assert sig.meta["exit_band"] is True
    assert sig.strength == 0.0
    assert_valid(sig)
    # opt-out for callers that prefer a bare None
    quiet = S6CurveButterfly(params={"emit_exit_signal": False})
    assert quiet.generate(make_ctx(df)) is None


def test_s6_no_trade_between_the_bands():
    df = _spike_last(base_frame(), BUTTERFLY_1_3_6, 1.2)
    sig = S6CurveButterfly().generate(make_ctx(df))
    assert sig is None


def test_s6_ou_half_life_gate():
    df = base_frame()
    # synthetic random walk: no mean reversion, the OU half-life gate must veto the trade
    df[BUTTERFLY_1_3_6] = ar1(len(df), 1.0, 0.001, 99)
    df = _spike_last(df, BUTTERFLY_1_3_6, 6.0)
    assert S6CurveButterfly().generate(make_ctx(df)) is None
    # mean-reverting series on the same spike does trade
    fast = _spike_last(base_frame(), BUTTERFLY_1_3_6, 6.0)
    traded = S6CurveButterfly().generate(make_ctx(fast))
    assert traded is not None
    # and the gate itself is a parameter
    blocked = S6CurveButterfly(params={"max_half_life_days": 1.0})
    assert blocked.generate(make_ctx(fast)) is None


def test_s6_approx_curve_suppression_and_missing_codes():
    df = _spike_last(base_frame(), BUTTERFLY_1_3_6, 6.0)
    assert S6CurveButterfly().generate(make_ctx(set_last(df, **{CURVE_APPROX: 1.0}))) is None
    partial = df.copy()
    partial.iloc[-120:-60, partial.columns.get_loc(CURVE_APPROX)] = 1.0
    assert S6CurveButterfly().generate(make_ctx(partial)) is None
    assert S6CurveButterfly().generate(make_ctx(df, curve_codes={"M1": "BZZ26", "M3": "BZG27"})) is None


def test_s6_determinism_and_params_exposed():
    ctx = make_ctx(_spike_last(base_frame(), BUTTERFLY_1_3_6, 6.0))
    strat = S6CurveButterfly()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None and first.to_dict() == second.to_dict()
    for key in ("entry_z", "exit_z", "max_half_life_days", "min_real_curve_days", "z_window", "ou_min_obs"):
        assert key in S6CurveButterfly.default_params()
    assert np.isclose(sum(r for _, r in first.meta["legs"]), 0.0)
