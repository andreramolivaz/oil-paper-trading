"""The two hard rules of the brief (§3): leverage is NEVER above 10x, and never above 1x without the gate.

Property tests with hypothesis over the whole input space of the leverage decision, plus allocator-level tests
that check the gross notional of the orders actually produced. Synthetic inputs, labelled as such.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.core.config import RiskConfig
from engine.core.events import AccountSnapshot, AccountStatus, Direction, Signal
from engine.features import catalog as cat
from engine.portfolio.allocator import MasterAllocator
from engine.portfolio.gate import AlphaGate
from engine.portfolio.leverage import compute_leverage, es99_one_day, student_t_es
from engine.regime.base import LABEL_BACKWARDATION_HIGHVOL_GEO, LABEL_LOWVOL_RANGE, RegimeState
from engine.strategies.base import Family, MarketContext

RISK = RiskConfig.load()
TS = datetime(2026, 10, 5, 18, 30, tzinfo=UTC)

finite = st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False)
positive = st.floats(min_value=1e-4, max_value=5.0, allow_nan=False, allow_infinity=False)
vol_or_none = st.one_of(st.none(), st.floats(min_value=0.01, max_value=4.0, allow_nan=False))


@settings(max_examples=400, deadline=None)
@given(
    gate_passed=st.booleans(),
    expected_return=finite,
    expected_vol=positive,
    horizon=st.integers(min_value=1, max_value=63),
    garch=vol_or_none,
    ovx=vol_or_none,
    realized=vol_or_none,
    drawdown=st.floats(min_value=0.0, max_value=0.95, allow_nan=False),
    hours=st.one_of(st.just(float("inf")), st.floats(min_value=0.0, max_value=500.0, allow_nan=False)),
    pre_weekend=st.booleans(),
    geo=st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
)
def test_leverage_never_breaches_the_hard_rules(
    gate_passed, expected_return, expected_vol, horizon, garch, ovx, realized, drawdown, hours, pre_weekend, geo
):
    d = compute_leverage(
        gate_passed=gate_passed,
        risk=RISK,
        expected_return=expected_return,
        expected_vol=expected_vol,
        horizon_days=horizon,
        vols={"garch": garch, "ovx": ovx, "realized": realized},
        drawdown=drawdown,
        hours_to_event=hours,
        is_pre_weekend=pre_weekend,
        geo_index_pctl=geo,
    )
    assert 0.0 <= d.chosen <= RISK.max_leverage <= 10.0
    if not gate_passed:
        assert d.chosen <= RISK.default_max_leverage + 1e-12, "leverage above 1x without the gate"
    assert d.limited_by, "the binding constraint must always be named"
    assert d.limited_by_key in d.components or d.limited_by_key == "gate"


def test_es_formula_matches_a_hand_computed_value():
    # Student-t(4), unit variance, 99%: quantile 3.7469, pdf 0.008752 -> ES/sigma = 3.6915 (see leverage.py)
    assert student_t_es(0.99, 4.0) == pytest.approx(3.6915, abs=1e-3)
    # at 40% annual vol the 1-day ES99 is 0.40/sqrt(252)*3.6915
    assert es99_one_day(0.40) == pytest.approx(0.40 / math.sqrt(252) * 3.6915, rel=1e-4)
    # a larger vol must never produce a smaller ES (monotonicity)
    assert es99_one_day(0.80) > es99_one_day(0.40) > es99_one_day(0.20)


def test_es_budget_binds_leverage_at_current_brent_volatility():
    """Documented calibration: ~1x at today's implied vol, ~2x at 25%, ~3.4x at 15%."""

    def cap(vol: float) -> float:
        return compute_leverage(True, RISK, 0.5, 0.02, 5, {"garch": vol}, 0.0).chosen

    assert cap(0.50) == pytest.approx(1.03, abs=0.1)
    assert cap(0.25) == pytest.approx(2.06, abs=0.15)
    assert cap(0.15) == pytest.approx(3.44, abs=0.25)
    assert cap(0.05) <= RISK.max_leverage


def _features(**over: float) -> pd.DataFrame:
    base = {
        cat.PX: 100.0,
        cat.PX_FRONT: 100.0,
        cat.RV_YZ_21: 0.25,
        cat.RV_CC_21: 0.25,
        cat.GARCH_VOL: 0.25,
        cat.OVX: 25.0,
        cat.ATR_14: 0.02,
        cat.GEO_SPIKE: 0.0,
        cat.GEO_INDEX: 0.3,
        cat.IS_PRE_WEEKEND: 0.0,
        cat.SPREAD_CHG_5: 0.01,
        cat.SPOT_FRONT_PREMIUM: 0.02,
        cat.CUSHING_VS_5Y: -0.05,
        cat.CURVE_APPROX: 0.0,
    }
    base.update(over)
    idx = pd.date_range("2026-01-01", periods=400, freq="B")
    return pd.DataFrame({k: [v] * len(idx) for k, v in base.items()}, index=idx)


def _ctx(regime_label: str = LABEL_LOWVOL_RANGE, confidence: float = 0.9, **over: float) -> MarketContext:
    return MarketContext(
        ts=TS,
        features=_features(**over),
        regime=RegimeState(ts=TS, regime_id=0, label=regime_label, confidence=confidence, model="test"),
        price=100.0,
        instrument="BZZ26",
    )


def _snapshot(equity: float = 10_000.0, drawdown: float = 0.0) -> AccountSnapshot:
    return AccountSnapshot(
        ts=TS,
        account_id="master",
        epoch=1,
        cash=equity,
        equity=equity,
        unrealized=0.0,
        margin_used=0.0,
        margin_level=None,
        gross_notional=0.0,
        net_notional=0.0,
        leverage=0.0,
        peak_equity=equity / max(1e-9, 1 - drawdown),
        drawdown=drawdown,
        daily_pnl=0.0,
        status=AccountStatus.ACTIVE,
    )


def _signal(sid: str, family: str, direction: Direction = Direction.LONG, prob: float = 0.8) -> Signal:
    return Signal(
        strategy_id=sid,
        ts=TS,
        instrument="BZZ26",
        direction=direction,
        prob=prob,
        expected_return=0.03,
        expected_vol=0.05,
        horizon_days=5,
        stop_pct=0.04,
        family=family,
        rationale=f"{sid}: test con numeri 1,23.",
    )


def test_allocator_stays_within_1x_when_the_gate_fails():
    alloc = MasterAllocator(RISK)
    alloc.set_context(health_overall="red", data_age_minutes=1000.0, lifecycles={})
    signals = [_signal("S1", Family.TREND), _signal("S4", Family.CARRY), _signal("S8", Family.RELATIVE_VALUE)]
    orders = alloc.decide(signals, _ctx(), _snapshot(), [], RISK)
    gross = sum(abs(o.qty_bbl) for o in orders) * 100.0
    assert alloc.last_debug.gate is not None and not alloc.last_debug.gate.passed
    assert gross <= 10_000.0 + 1e-6, "gross exposure above 1x with a failed gate"
    assert orders and "gate non superato" in orders[0].rationale


def test_allocator_can_exceed_1x_only_with_every_condition_met():
    alloc = MasterAllocator(RISK)
    # three decorrelated families agree, strong probabilities, solid out-of-sample stats, fresh green data,
    # calm regime with high confidence, no event within 24h (2026-10-05 is a Monday; WPSR is on Wednesday)
    alloc.set_context(
        health_overall="green",
        data_age_minutes=5.0,
        oos_stats={s: {"psr": 0.95} for s in ("S1", "S4", "S8")},
        lifecycles={"S1": "active", "S4": "active", "S8": "active"},
    )
    alloc.weights.current.weights = {"S1": 0.34, "S4": 0.33, "S8": 0.33}
    signals = [_signal("S1", Family.TREND), _signal("S4", Family.CARRY), _signal("S8", Family.RELATIVE_VALUE)]
    ctx = _ctx(**{cat.RV_YZ_21: 0.15, cat.GARCH_VOL: 0.15, cat.OVX: 15.0})
    orders = alloc.decide(signals, ctx, _snapshot(), [], RISK)
    gate = alloc.last_debug.gate
    lev = alloc.last_debug.leverage
    assert gate is not None and gate.passed, f"gate failed: {None if gate is None else gate.failed}"
    assert lev is not None and 1.0 < lev.chosen <= RISK.max_leverage
    gross = sum(abs(o.qty_bbl) for o in orders) * 100.0
    assert gross <= lev.chosen * 10_000.0 + 100.0 * 100.0  # one lot of rounding tolerance
    assert "Leva" in orders[0].rationale


@pytest.mark.parametrize(
    ("kwargs", "condition"),
    [
        ({"health_overall": "yellow"}, "data_fresh"),
        ({"data_age_minutes": 500.0}, "data_fresh"),
        ({"oos_stats": {}}, "regime_quality"),
    ],
)
def test_each_gate_condition_can_block_leverage(kwargs, condition):
    alloc = MasterAllocator(RISK)
    ctxargs = {
        "health_overall": "green",
        "data_age_minutes": 5.0,
        "oos_stats": {s: {"psr": 0.95} for s in ("S1", "S4", "S8")},
        "lifecycles": {"S1": "active", "S4": "active", "S8": "active"},
    }
    ctxargs.update(kwargs)
    alloc.set_context(**ctxargs)
    alloc.weights.current.weights = {"S1": 0.34, "S4": 0.33, "S8": 0.33}
    signals = [_signal("S1", Family.TREND), _signal("S4", Family.CARRY), _signal("S8", Family.RELATIVE_VALUE)]
    alloc.decide(signals, _ctx(), _snapshot(), [], RISK)
    gate = alloc.last_debug.gate
    assert gate is not None and not gate.passed and condition in gate.failed
    assert alloc.last_debug.leverage is not None
    assert alloc.last_debug.leverage.chosen <= RISK.default_max_leverage + 1e-12


def test_gate_needs_three_families_and_a_confident_regime():
    gate = AlphaGate(RISK)
    ctx = _ctx()
    two_families = [_signal("S1", Family.TREND), _signal("S2", Family.TREND), _signal("S4", Family.CARRY)]
    res = gate.evaluate(
        two_families,
        ctx,
        drawdown=0.0,
        weights={"S1": 1, "S2": 1, "S4": 1},
        oos_stats={s: {"psr": 0.95} for s in ("S1", "S2", "S4")},
        health_overall="green",
        data_age_minutes=1.0,
    )
    assert not res.passed and "families_agree" in res.failed
    assert res.conditions["families_agree"].value == 2

    low_conf = gate.evaluate(
        [_signal("S1", Family.TREND), _signal("S4", Family.CARRY), _signal("S8", Family.RELATIVE_VALUE)],
        _ctx(confidence=0.4),
        drawdown=0.0,
        oos_stats={s: {"psr": 0.95} for s in ("S1", "S4", "S8")},
        health_overall="green",
        data_age_minutes=1.0,
    )
    assert not low_conf.passed and "regime_quality" in low_conf.failed


def test_geopolitical_long_needs_physical_confirmation():
    gate = AlphaGate(RISK)
    kwargs = {
        "drawdown": 0.0,
        "oos_stats": {s: {"psr": 0.95} for s in ("S1", "S4", "S8")},
        "health_overall": "green",
        "data_age_minutes": 1.0,
    }
    signals = [_signal("S1", Family.TREND), _signal("S4", Family.CARRY), _signal("S8", Family.RELATIVE_VALUE)]
    # spike with spreads NOT confirming -> condition fails
    bad = gate.evaluate(
        signals,
        _ctx(regime_label=LABEL_BACKWARDATION_HIGHVOL_GEO, **{cat.GEO_SPIKE: 1.0, cat.SPREAD_CHG_5: -0.02}),
        **kwargs,
    )
    assert "physical_confirmation" in bad.failed
    # no spike, calm regime -> the condition is not required
    ok = gate.evaluate(signals, _ctx(), **kwargs)
    assert ok.conditions["physical_confirmation"].ok


def test_gate_fails_closed_on_missing_inputs():
    gate = AlphaGate(RISK)
    res = gate.evaluate(
        [_signal("S1", Family.TREND), _signal("S4", Family.CARRY), _signal("S8", Family.RELATIVE_VALUE)],
        _ctx(),
        drawdown=0.0,
        oos_stats=None,  # unknown out-of-sample quality
        health_overall="green",
        data_age_minutes=None,  # unknown data age
    )
    assert not res.passed
    assert "regime_quality" in res.failed and "data_fresh" in res.failed


def test_informational_and_approximate_signals_never_take_risk():
    alloc = MasterAllocator(RISK)
    alloc.set_context(health_overall="green", data_age_minutes=1.0, lifecycles={})
    flat = _signal("S16", Family.VOLATILITY, direction=Direction.FLAT)
    approx = _signal("S18", Family.VOLATILITY)
    approx.meta["approx"] = True
    orders = alloc.decide([flat, approx], _ctx(), _snapshot(), [], RISK)
    assert orders == []
