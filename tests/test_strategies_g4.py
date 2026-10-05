"""Strategies agent G4: S10 (macro fair value), S11 (EIA surprise), S12 (OPEC+ playbook).

ALL DATA HERE IS SYNTHETIC: hand-built feature frames (and hand-built EventInfo lists) whose only purpose is to
make each documented branch of docs/STRATEGIES.md fire offline and deterministically. No network, no fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.regime.base import (
    LABEL_BACKWARDATION_HIGHVOL_GEO,
    LABEL_CONTANGO_GLUT_DOWNTREND,
    LABEL_LOWVOL_RANGE,
    RegimeState,
)
from engine.strategies.base import EventInfo, Family, MarketContext
from engine.strategies.s10_macro_fv import S10MacroFairValue
from engine.strategies.s11_eia_surprise import S11EiaSurprise
from engine.strategies.s12_opec import S12OpecPlaybook

# ----------------------------------------------------------------------------------------------------------------
# synthetic context builders (shared shape across the G4/G5/G6 suites)
# ----------------------------------------------------------------------------------------------------------------
BASE_DEFAULTS: dict[str, float] = {
    cat.PX: 100.0,
    cat.PX_FRONT: 100.0,
    cat.RET_1: 0.0,
    cat.RET_5: 0.0,
    cat.RV_YZ_21: 0.15,
    cat.ATR_14: 0.02,
    cat.GEO_SPIKE: 0.0,
    cat.GEO_INDEX: 0.3,
    cat.CURVE_APPROX: 0.0,
    cat.HOURS_TO_EVENT: 500.0,
}


def make_frame(rows: int = 400, end: str = "2026-09-30", **overrides: float) -> pd.DataFrame:
    """All-NaN synthetic feature frame with a business-day UTC index and a few harmless defaults."""
    idx = pd.bdate_range(end=end, periods=rows, tz="UTC", name="date")
    df = pd.DataFrame(float("nan"), index=idx, columns=cat.ALL_NUMERIC_FEATURES, dtype=float)
    for col, val in {**BASE_DEFAULTS, **overrides}.items():
        df[col] = float(val)
    return df


def make_ctx(
    df: pd.DataFrame,
    *,
    label: str = LABEL_LOWVOL_RANGE,
    price: float = 100.0,
    events: list[EventInfo] | None = None,
    instrument: str = "BZZ26",
) -> MarketContext:
    ts = df.index[-1].to_pydatetime()
    regime = RegimeState(ts=ts, regime_id=2, label=label, confidence=0.7)
    return MarketContext(
        ts=ts,
        features=df,
        regime=regime,
        price=price,
        instrument=instrument,
        next_events=events or [],
        curve_codes={"M1": instrument},
    )


def assert_valid(sig: Signal) -> None:
    assert 0.0 < sig.prob < 1.0
    assert sig.expected_vol > 0.0
    assert sig.rationale.strip() != ""
    assert any(ch.isdigit() for ch in sig.rationale), sig.rationale
    assert 0.0 <= sig.strength <= 1.0
    assert sig.horizon_days >= 1
    assert sig.family in {
        Family.TREND,
        Family.CARRY,
        Family.RELATIVE_VALUE,
        Family.FUNDAMENTAL,
        Family.EVENT,
        Family.VOLATILITY,
        Family.ML,
    }
    if sig.stop_pct is not None:
        assert sig.stop_pct > 0.0


# ----------------------------------------------------------------------------------------------------------------
# S10 - macro fair value
# ----------------------------------------------------------------------------------------------------------------
def s10_frame(z: float, *, geo_spike: float = 0.0, stock_dev: float = 0.01) -> pd.DataFrame:
    return make_frame(
        320,
        **{
            cat.MACRO_FV_Z: z,
            cat.MACRO_FV_RESID: z * 0.01,
            cat.GEO_SPIKE: geo_spike,
            cat.CRUDE_STOCKS_VS_5Y: stock_dev,
        },
    )


def test_s10_short_when_price_above_fair_value() -> None:
    sig = S10MacroFairValue().generate(make_ctx(s10_frame(2.5)))
    assert sig is not None and sig.direction is Direction.SHORT
    assert sig.expected_return < 0.0
    assert sig.meta["macro_fv_z"] == pytest.approx(2.5)
    assert sig.horizon_days == 10
    assert_valid(sig)


def test_s10_long_when_price_below_fair_value() -> None:
    sig = S10MacroFairValue().generate(make_ctx(s10_frame(-2.5)))
    assert sig is not None and sig.direction is Direction.LONG
    assert sig.expected_return > 0.0
    assert sig.target_pct is not None and sig.target_pct > 0.0
    assert_valid(sig)


def test_s10_none_inside_the_band() -> None:
    assert S10MacroFairValue().generate(make_ctx(s10_frame(1.5))) is None


def test_s10_none_on_geopolitical_spike() -> None:
    assert S10MacroFairValue().generate(make_ctx(s10_frame(2.5, geo_spike=1.0))) is None


def test_s10_none_when_inventories_extreme() -> None:
    # |crude_stocks_vs_5y| = 12% > max_stock_dev (8%): the deviation is explained by fundamentals
    assert S10MacroFairValue().generate(make_ctx(s10_frame(2.5, stock_dev=-0.12))) is None


def test_s10_none_when_required_feature_missing() -> None:
    df = s10_frame(2.5)
    df[cat.MACRO_FV_Z] = float("nan")
    assert S10MacroFairValue().generate(make_ctx(df)) is None
    df2 = s10_frame(2.5)
    df2[cat.RV_YZ_21] = float("nan")
    assert S10MacroFairValue().generate(make_ctx(df2)) is None


def test_s10_strength_reduced_in_geopolitical_regime() -> None:
    strat = S10MacroFairValue()
    calm = strat.generate(make_ctx(s10_frame(2.5), label=LABEL_CONTANGO_GLUT_DOWNTREND))
    geo = strat.generate(make_ctx(s10_frame(2.5), label=LABEL_BACKWARDATION_HIGHVOL_GEO))
    assert calm is not None and geo is not None
    assert geo.strength == pytest.approx(calm.strength * 0.5)
    assert geo.meta["geo_regime"] is True


def test_s10_deterministic() -> None:
    strat = S10MacroFairValue()
    ctx = make_ctx(s10_frame(-2.8))
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None
    assert a.to_dict() == b.to_dict()


def test_s10_default_params_expose_documented_thresholds() -> None:
    p = S10MacroFairValue.default_params()
    for key in ("entry_z", "exit_z", "max_stock_dev", "geo_regime_strength", "horizon_days"):
        assert key in p
    assert p["entry_z"] == 2.0


# ----------------------------------------------------------------------------------------------------------------
# S11 - EIA surprise
# ----------------------------------------------------------------------------------------------------------------
def s11_frame(
    *,
    days_since: float = 0.0,
    stock_z: float = 0.0,
    cushing_z: float = 0.0,
    demand_z: float = 0.0,
    runs_z: float = 0.0,
) -> pd.DataFrame:
    return make_frame(
        320,
        **{
            cat.DAYS_SINCE_WPSR: days_since,
            cat.STOCK_SURPRISE_Z: stock_z,
            cat.CUSHING_SURPRISE_Z: cushing_z,
            cat.IMPLIED_DEMAND_Z: demand_z,
            cat.REFINERY_INPUTS_Z: runs_z,
        },
    )


def test_s11_release_day_gate() -> None:
    strat = S11EiaSurprise()
    assert strat.generate(make_ctx(s11_frame(days_since=1.0, stock_z=3.0))) is None
    assert strat.generate(make_ctx(s11_frame(days_since=5.0, stock_z=3.0))) is None
    fired = strat.generate(make_ctx(s11_frame(days_since=0.0, stock_z=3.0)))
    assert fired is not None


def test_s11_combined_z_arithmetic() -> None:
    # 0.40*2 - 0.25*4 = 0.8 - 1.0 = -0.2 ; +0.25*1 + 0.10*1 = 0.35 -> 0.15
    assert S11EiaSurprise.combined_z(2.0, 1.0, 4.0, 1.0) == pytest.approx(0.15)
    assert S11EiaSurprise.combined_z(1.0, 0.0, 0.0, 0.0) == pytest.approx(0.40)
    assert S11EiaSurprise.combined_z(0.0, 1.0, 0.0, 0.0) == pytest.approx(0.25)
    assert S11EiaSurprise.combined_z(0.0, 0.0, 1.0, 0.0) == pytest.approx(-0.25)
    assert S11EiaSurprise.combined_z(0.0, 0.0, 0.0, 1.0) == pytest.approx(0.10)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"stock_z": 3.0}, Direction.SHORT),  # crude build = bearish
        ({"stock_z": -3.0}, Direction.LONG),  # crude draw = bullish
        ({"cushing_z": 5.0}, Direction.SHORT),  # Cushing build = bearish
        ({"cushing_z": -5.0}, Direction.LONG),
        ({"demand_z": 5.0}, Direction.LONG),  # strong implied demand = bullish
        ({"demand_z": -5.0}, Direction.SHORT),
        ({"runs_z": 12.0}, Direction.SHORT),  # higher runs enter with the stock-build sign
        ({"runs_z": -12.0}, Direction.LONG),
    ],
)
def test_s11_sign_conventions(kwargs: dict[str, float], expected: Direction) -> None:
    sig = S11EiaSurprise().generate(make_ctx(s11_frame(**kwargs)))
    assert sig is not None, kwargs
    assert sig.direction is expected, (kwargs, sig.meta["combined_z"])
    assert_valid(sig)


def test_s11_horizon_and_ttl() -> None:
    sig = S11EiaSurprise().generate(make_ctx(s11_frame(stock_z=3.0)))
    assert sig is not None
    assert sig.horizon_days == 3
    assert sig.meta["ttl_days"] == 3
    assert sig.family == Family.FUNDAMENTAL


def test_s11_weight_halved_in_geopolitical_regime() -> None:
    strat = S11EiaSurprise()
    calm = strat.generate(make_ctx(s11_frame(stock_z=3.0), label=LABEL_LOWVOL_RANGE))
    geo = strat.generate(make_ctx(s11_frame(stock_z=3.0), label=LABEL_BACKWARDATION_HIGHVOL_GEO))
    assert calm is not None and geo is not None
    assert geo.strength == pytest.approx(calm.strength * 0.5)


def test_s11_none_below_min_z() -> None:
    # 0.40 * 2.0 = 0.8 < min_z 1.0
    assert S11EiaSurprise().generate(make_ctx(s11_frame(stock_z=2.0))) is None


def test_s11_none_when_a_component_is_missing() -> None:
    df = s11_frame(stock_z=3.0)
    df[cat.CUSHING_SURPRISE_Z] = float("nan")
    assert S11EiaSurprise().generate(make_ctx(df)) is None


def test_s11_deterministic() -> None:
    strat = S11EiaSurprise()
    ctx = make_ctx(s11_frame(stock_z=2.5, cushing_z=1.0, demand_z=-1.0, runs_z=0.5))
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()


# ----------------------------------------------------------------------------------------------------------------
# S12 - OPEC+ playbook
# ----------------------------------------------------------------------------------------------------------------
def s12_frame(*, tsmom: float = 0.5, slope_m1_m6: float = 0.02, slope_m1_m2: float = float("nan")) -> pd.DataFrame:
    return make_frame(
        200,
        **{cat.TSMOM_21: tsmom, cat.SLOPE_M1_M6: slope_m1_m6, cat.SLOPE_M1_M2: slope_m1_m2},
    )


def opec_event(ctx_ts: datetime, hours: float, event_id: str = "opec_2026_10") -> EventInfo:
    return EventInfo(
        event_id=event_id,
        name="Riunione OPEC+",
        ts=ctx_ts + timedelta(hours=hours),
        binary=True,
        hours_away=hours,
    )


def test_s12_pre_event_follows_trend_when_curve_agrees() -> None:
    df = s12_frame(tsmom=0.6, slope_m1_m6=0.03)
    ts = df.index[-1].to_pydatetime()
    sig = S12OpecPlaybook().generate(make_ctx(df, events=[opec_event(ts, 24.0)]))
    assert sig is not None and sig.direction is Direction.LONG
    assert sig.meta["pre_event"] is True
    assert sig.strength == pytest.approx(0.5)  # pre_strength 0.5 * vol_scale(0.15, 0.15) = 1.0
    assert sig.horizon_days == 2
    assert sig.meta["slope_used"] == cat.SLOPE_M1_M6
    assert_valid(sig)


def test_s12_pre_event_short_side() -> None:
    df = s12_frame(tsmom=-0.6, slope_m1_m6=-0.03)
    ts = df.index[-1].to_pydatetime()
    sig = S12OpecPlaybook().generate(make_ctx(df, events=[opec_event(ts, 10.0)]))
    assert sig is not None and sig.direction is Direction.SHORT


def test_s12_pre_event_none_when_trend_and_curve_disagree() -> None:
    df = s12_frame(tsmom=0.6, slope_m1_m6=-0.03)
    ts = df.index[-1].to_pydatetime()
    assert S12OpecPlaybook().generate(make_ctx(df, events=[opec_event(ts, 24.0)])) is None


def test_s12_pre_event_slope_fallback_to_m1_m2() -> None:
    df = s12_frame(tsmom=0.6, slope_m1_m6=float("nan"), slope_m1_m2=0.01)
    ts = df.index[-1].to_pydatetime()
    sig = S12OpecPlaybook().generate(make_ctx(df, events=[opec_event(ts, 30.0)]))
    assert sig is not None and sig.meta["slope_used"] == cat.SLOPE_M1_M2


def test_s12_pre_event_outside_48h_is_none() -> None:
    df = s12_frame()
    ts = df.index[-1].to_pydatetime()
    assert S12OpecPlaybook().generate(make_ctx(df, events=[opec_event(ts, 96.0)])) is None


def test_s12_post_event_follows_event_day_return() -> None:
    df = s12_frame()
    event_day = df.index[-3]
    df.loc[event_day, cat.RET_1] = 0.03  # ~3.2 sigma with rv = 0.15
    ts = df.index[-1].to_pydatetime()
    ev_ts = event_day.to_pydatetime() + timedelta(hours=16)
    hours = (ev_ts - ts).total_seconds() / 3600.0
    assert -72.0 <= hours < 0.0
    sig = S12OpecPlaybook().generate(make_ctx(df, events=[opec_event(ts, hours)]))
    assert sig is not None and sig.direction is Direction.LONG
    assert sig.meta["post_event"] is True
    assert sig.meta["event_day_ret_1"] == pytest.approx(0.03)
    assert sig.horizon_days == 3
    assert_valid(sig)


def test_s12_post_event_short_side() -> None:
    df = s12_frame()
    event_day = df.index[-2]
    df.loc[event_day, cat.RET_1] = -0.025
    ts = df.index[-1].to_pydatetime()
    ev_ts = event_day.to_pydatetime() + timedelta(hours=16)
    hours = (ev_ts - ts).total_seconds() / 3600.0
    sig = S12OpecPlaybook().generate(make_ctx(df, events=[opec_event(ts, hours)]))
    assert sig is not None and sig.direction is Direction.SHORT


def test_s12_post_event_none_when_reaction_below_one_sigma() -> None:
    df = s12_frame()
    event_day = df.index[-2]
    df.loc[event_day, cat.RET_1] = 0.0005  # far below 1 sigma
    ts = df.index[-1].to_pydatetime()
    ev_ts = event_day.to_pydatetime() + timedelta(hours=16)
    hours = (ev_ts - ts).total_seconds() / 3600.0
    assert S12OpecPlaybook().generate(make_ctx(df, events=[opec_event(ts, hours)])) is None


def test_s12_none_without_opec_events() -> None:
    df = s12_frame()
    ts = df.index[-1].to_pydatetime()
    assert S12OpecPlaybook().generate(make_ctx(df, events=[])) is None
    other = EventInfo("eia_wpsr", "EIA", ts + timedelta(hours=12), True, 12.0)
    assert S12OpecPlaybook().generate(make_ctx(df, events=[other])) is None


def test_s12_pre_event_wins_when_both_windows_overlap() -> None:
    df = s12_frame(tsmom=-0.6, slope_m1_m6=-0.03)
    event_day = df.index[-2]
    df.loc[event_day, cat.RET_1] = 0.03  # a post-event drift that would be LONG
    ts = df.index[-1].to_pydatetime()
    past_hours = (event_day.to_pydatetime() + timedelta(hours=16) - ts).total_seconds() / 3600.0
    events = [opec_event(ts, past_hours, "opec_past"), opec_event(ts, 20.0, "opec_next")]
    sig = S12OpecPlaybook().generate(make_ctx(df, events=events))
    assert sig is not None and sig.meta.get("pre_event") is True
    assert sig.direction is Direction.SHORT


def test_s12_deterministic_and_params() -> None:
    df = s12_frame()
    ts = df.index[-1].to_pydatetime()
    ctx = make_ctx(df, events=[opec_event(ts, 24.0)])
    strat = S12OpecPlaybook()
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()
    p = S12OpecPlaybook.default_params()
    for key in ("pre_hours", "post_hours", "pre_strength", "post_sigma_mult", "event_prefix"):
        assert key in p
