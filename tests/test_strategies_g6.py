"""Strategies agent G6: S16 (vol regime), S17 (short reversal), Black-76 and S18 (synthetic options).

ALL DATA HERE IS SYNTHETIC: hand-built feature frames whose only purpose is to make each documented branch of
docs/STRATEGIES.md fire offline and deterministically. No network, no fixtures.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.regime.base import LABEL_LOWVOL_RANGE, LABEL_SHOCK_CRASH, RegimeState
from engine.strategies import black76
from engine.strategies.base import Family, MarketContext
from engine.strategies.common import TRADING_DAYS, expected_move, ou_half_life
from engine.strategies.s16_vol_regime import S16VolRegime
from engine.strategies.s17_reversal import S17ShortReversal
from engine.strategies.s18_synthetic_options import SYNTHETIC_PREFIX, S18SyntheticOptions

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
    idx = pd.bdate_range(end=end, periods=rows, tz="UTC", name="date")
    df = pd.DataFrame(float("nan"), index=idx, columns=cat.ALL_NUMERIC_FEATURES, dtype=float)
    for col, val in {**BASE_DEFAULTS, **overrides}.items():
        df[col] = float(val)
    return df


def make_ctx(
    df: pd.DataFrame, *, label: str = LABEL_LOWVOL_RANGE, price: float = 100.0, instrument: str = "BZZ26"
) -> MarketContext:
    ts = df.index[-1].to_pydatetime()
    return MarketContext(
        ts=ts,
        features=df,
        regime=RegimeState(ts=ts, regime_id=2, label=label, confidence=0.7),
        price=price,
        instrument=instrument,
        curve_codes={"M1": instrument},
    )


def assert_valid(sig: Signal) -> None:
    assert 0.0 < sig.prob < 1.0
    assert sig.expected_vol > 0.0
    assert sig.rationale.strip() != ""
    assert any(ch.isdigit() for ch in sig.rationale), sig.rationale
    assert 0.0 <= sig.strength <= 1.0
    assert sig.horizon_days >= 1
    if sig.stop_pct is not None:
        assert sig.stop_pct > 0.0


# ----------------------------------------------------------------------------------------------------------------
# S16 - volatility regime
# ----------------------------------------------------------------------------------------------------------------
def s16_frame(
    *,
    vrp_mode: str = "high",
    ret_1: float = 0.03,
    hours: float = 500.0,
    vol_pctl: float = 0.5,
    ovx: float = 40.0,
    rows: int = 320,
) -> pd.DataFrame:
    df = make_frame(rows, **{cat.OVX: ovx, cat.HOURS_TO_EVENT: hours, cat.VOL_PCTL_1Y: vol_pctl})
    ramp = np.linspace(0.0, 0.2, rows)
    if vrp_mode == "high":
        vrp = ramp  # the last value is the maximum -> percentile 1.0
    elif vrp_mode == "low":
        vrp = ramp[::-1]  # the last value is the minimum -> percentile 0.0
    else:
        vrp = ramp.copy()
        vrp[-1] = float(np.median(ramp))  # mid distribution
    df[cat.VRP] = vrp
    df.loc[df.index[-1], cat.RET_1] = ret_1
    return df


def test_s16_mean_reversion_fades_an_oversized_move() -> None:
    sig = S16VolRegime().generate(make_ctx(s16_frame(ret_1=0.03)))
    assert sig is not None and sig.direction is Direction.SHORT
    assert sig.meta["branch"] == "mean_reversion"
    assert sig.meta["vrp_pctl"] > 0.7
    assert 1 <= sig.horizon_days <= 5
    assert sig.family == Family.VOLATILITY
    expected_sigmas = 0.03 / (0.15 / math.sqrt(TRADING_DAYS))
    assert sig.meta["ret_1_sigmas"] == pytest.approx(expected_sigmas)
    assert_valid(sig)


def test_s16_mean_reversion_mirrors_on_a_down_move() -> None:
    sig = S16VolRegime().generate(make_ctx(s16_frame(ret_1=-0.03)))
    assert sig is not None and sig.direction is Direction.LONG
    assert sig.expected_return > 0.0


def test_s16_mean_reversion_blocked_by_an_imminent_event() -> None:
    assert S16VolRegime().generate(make_ctx(s16_frame(ret_1=0.03, hours=10.0))) is None


def test_s16_mean_reversion_needs_a_move_above_1_5_sigma() -> None:
    # 1.0 sigma move: 0.15/sqrt(252) ~ 0.00945
    assert S16VolRegime().generate(make_ctx(s16_frame(ret_1=0.0094))) is None


def test_s16_informational_branch_enables_the_breakout_family() -> None:
    sig = S16VolRegime().generate(make_ctx(s16_frame(vrp_mode="low", ret_1=0.0, vol_pctl=0.1)))
    assert sig is not None
    assert sig.direction is Direction.FLAT
    assert sig.prob == pytest.approx(0.5)
    assert sig.strength == pytest.approx(0.0)
    assert sig.meta["enable_breakout"] is True
    assert sig.expected_return == pytest.approx(0.0)
    assert_valid(sig)


def test_s16_informational_branch_needs_low_realized_vol_too() -> None:
    assert S16VolRegime().generate(make_ctx(s16_frame(vrp_mode="low", ret_1=0.0, vol_pctl=0.5))) is None


def test_s16_none_in_the_middle_of_the_distribution() -> None:
    assert S16VolRegime().generate(make_ctx(s16_frame(vrp_mode="mid", ret_1=0.0, vol_pctl=0.5))) is None


def test_s16_none_without_ovx_or_vrp() -> None:
    df = s16_frame()
    df[cat.OVX] = float("nan")
    assert S16VolRegime().generate(make_ctx(df)) is None
    df2 = s16_frame()
    df2[cat.VRP] = float("nan")
    assert S16VolRegime().generate(make_ctx(df2)) is None


def test_s16_deterministic_and_params() -> None:
    strat = S16VolRegime()
    ctx = make_ctx(s16_frame())
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()
    p = S16VolRegime.default_params()
    for key in ("vrp_pctl_high", "vrp_pctl_low", "vol_pctl_low", "min_hours_to_event", "ret_sigma_mult"):
        assert key in p
    assert 1 <= int(p["horizon_days"]) <= 5


# ----------------------------------------------------------------------------------------------------------------
# S17 - short-term reversal
# ----------------------------------------------------------------------------------------------------------------
def s17_frame(
    *,
    hurst: float = 0.4,
    ret_5: float = 0.08,
    geo_spike: float = 0.0,
    surprise: float | None = None,
    ou_phi: float = 0.5,
    rows: int = 320,
    seed: int = 3,
) -> pd.DataFrame:
    df = make_frame(rows, **{cat.HURST_100: hurst, cat.GEO_SPIKE: geo_spike})
    rng = np.random.default_rng(seed)
    # synthetic AR(1) path for RET_5 so that ou_half_life() has a mean-reverting series to fit
    path = np.zeros(rows)
    for i in range(1, rows):
        path[i] = ou_phi * path[i - 1] + rng.normal(0.0, 0.01)
    df[cat.RET_5] = path
    df.loc[df.index[-1], cat.RET_5] = ret_5
    if surprise is not None:
        df[cat.STOCK_SURPRISE_Z] = surprise
    return df


def test_s17_fades_an_oversized_five_day_move() -> None:
    df = s17_frame(ret_5=0.08)
    sig = S17ShortReversal().generate(make_ctx(df))
    assert sig is not None and sig.direction is Direction.SHORT
    sigma_5 = expected_move(0.15, 5)
    assert sig.meta["ret_5_sigmas"] == pytest.approx(0.08 / sigma_5)
    assert sig.stop_pct == pytest.approx(1.5 * expected_move(0.15, sig.horizon_days))
    assert_valid(sig)


def test_s17_mirrors_on_a_down_move() -> None:
    sig = S17ShortReversal().generate(make_ctx(s17_frame(ret_5=-0.08)))
    assert sig is not None and sig.direction is Direction.LONG


def test_s17_horizon_comes_from_the_ou_half_life() -> None:
    df = s17_frame(ret_5=0.08, ou_phi=0.5)
    ctx = make_ctx(df)
    hl = ou_half_life(ctx.hist(cat.RET_5, 252))
    assert hl is not None
    expected = min(5, max(1, int(round(hl))))
    sig = S17ShortReversal().generate(ctx)
    assert sig is not None
    assert sig.horizon_days == expected
    assert sig.meta["ou_half_life"] == pytest.approx(hl)


def test_s17_horizon_falls_back_to_three_without_an_ou_fit() -> None:
    df = s17_frame(ret_5=0.08)
    df[cat.RET_5] = float("nan")  # not enough observations for the OU fit
    df.loc[df.index[-1], cat.RET_5] = 0.08
    sig = S17ShortReversal().generate(make_ctx(df))
    assert sig is not None
    assert sig.horizon_days == 3
    assert sig.meta["ou_half_life"] is None


def test_s17_requires_antipersistence() -> None:
    assert S17ShortReversal().generate(make_ctx(s17_frame(hurst=0.62))) is None


def test_s17_requires_a_move_above_two_sigma() -> None:
    assert S17ShortReversal().generate(make_ctx(s17_frame(ret_5=0.02))) is None


def test_s17_blocked_by_a_geopolitical_spike() -> None:
    assert S17ShortReversal().generate(make_ctx(s17_frame(geo_spike=1.0))) is None


def test_s17_blocked_by_an_eia_surprise_but_fires_when_neutral() -> None:
    strat = S17ShortReversal()
    assert strat.generate(make_ctx(s17_frame(surprise=1.5))) is None
    neutral = strat.generate(make_ctx(s17_frame(surprise=0.4)))
    assert neutral is not None
    missing = strat.generate(make_ctx(s17_frame(surprise=None)))  # NaN surprise is allowed
    assert missing is not None


def test_s17_strength_halved_in_shock_regime() -> None:
    strat = S17ShortReversal()
    calm = strat.generate(make_ctx(s17_frame(), label=LABEL_LOWVOL_RANGE))
    shock = strat.generate(make_ctx(s17_frame(), label=LABEL_SHOCK_CRASH))
    assert calm is not None and shock is not None
    assert shock.strength == pytest.approx(calm.strength * 0.5)
    assert shock.meta["shock_regime"] is True


def test_s17_none_when_required_feature_missing() -> None:
    df = s17_frame()
    df[cat.HURST_100] = float("nan")
    assert S17ShortReversal().generate(make_ctx(df)) is None


def test_s17_deterministic_and_params() -> None:
    strat = S17ShortReversal()
    ctx = make_ctx(s17_frame())
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()
    p = S17ShortReversal.default_params()
    for key in ("max_hurst", "ret_sigma_mult", "max_stock_surprise_z", "stop_sigma", "max_horizon_days"):
        assert key in p


# ----------------------------------------------------------------------------------------------------------------
# Black-76
# ----------------------------------------------------------------------------------------------------------------
def test_black76_known_value() -> None:
    assert black76.price(100.0, 100.0, 0.25, 0.3, 0.0, True) == pytest.approx(5.98, abs=0.01)


@pytest.mark.parametrize(
    ("f", "k", "t", "vol", "r"),
    [
        (100.0, 100.0, 0.25, 0.30, 0.0),
        (113.96, 95.0, 0.5, 0.45, 0.03),
        (82.0, 101.0, 1.0, 0.25, 0.05),
        (60.0, 60.0, 0.08, 0.6, 0.01),
    ],
)
def test_black76_put_call_parity(f: float, k: float, t: float, vol: float, r: float) -> None:
    call = black76.price(f, k, t, vol, r, True)
    put = black76.price(f, k, t, vol, r, False)
    assert call - put == pytest.approx(math.exp(-r * t) * (f - k), abs=1e-9)


def test_black76_intrinsic_at_expiry() -> None:
    assert black76.price(110.0, 100.0, 0.0, 0.3, 0.0, True) == pytest.approx(10.0)
    assert black76.price(90.0, 100.0, 0.0, 0.3, 0.0, False) == pytest.approx(10.0)
    assert black76.price(90.0, 100.0, 0.0, 0.3, 0.0, True) == pytest.approx(0.0)


def test_black76_delta_and_vega() -> None:
    d_call = black76.delta(100.0, 100.0, 0.25, 0.3, 0.0, True)
    d_put = black76.delta(100.0, 100.0, 0.25, 0.3, 0.0, False)
    assert 0.0 < d_call < 1.0
    assert -1.0 < d_put < 0.0
    assert d_call - d_put == pytest.approx(1.0, abs=1e-9)  # r = 0
    v = black76.vega(100.0, 100.0, 0.25, 0.3)
    assert v > 0.0
    bumped = black76.price(100.0, 100.0, 0.25, 0.31, 0.0, True)
    base = black76.price(100.0, 100.0, 0.25, 0.30, 0.0, True)
    assert (bumped - base) == pytest.approx(v * 0.01, rel=0.02)


def test_black76_implied_vol_round_trip() -> None:
    px = black76.price(101.0, 95.0, 0.4, 0.37, 0.02, True)
    iv = black76.implied_vol(px, 101.0, 95.0, 0.4, 0.02, True)
    assert iv is not None and iv == pytest.approx(0.37, abs=1e-6)
    assert black76.implied_vol(1e9, 101.0, 95.0, 0.4, 0.02, True) is None


def test_black76_put_skew_is_monotone_and_documented() -> None:
    f, atm = 100.0, 0.40
    strikes = [60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0]
    vols = [black76.skew_vol(f, k, atm) for k in strikes]
    assert all(a >= b for a, b in zip(vols, vols[1:])), vols  # non-increasing in strike
    assert vols[strikes.index(100.0)] == pytest.approx(atm)
    assert vols[strikes.index(110.0)] == pytest.approx(atm)  # no call-wing skew modelled
    # documented slope: +2 vol points per 10% out-of-the-money
    assert black76.skew_vol(f, 90.0, atm) == pytest.approx(atm + 0.02)
    assert black76.skew_vol(f, 80.0, atm) == pytest.approx(atm + 0.04)


def test_black76_rejects_nonpositive_inputs() -> None:
    with pytest.raises(ValueError):
        black76.price(0.0, 100.0, 0.25, 0.3)
    with pytest.raises(ValueError):
        black76.skew_vol(0.0, 100.0, 0.3)


# ----------------------------------------------------------------------------------------------------------------
# S18 - synthetic options (approximation, always weight 0)
# ----------------------------------------------------------------------------------------------------------------
def s18_frame(*, ovx: float, garch: float, hours: float) -> pd.DataFrame:
    return make_frame(320, **{cat.OVX: ovx, cat.GARCH_VOL: garch, cat.HOURS_TO_EVENT: hours})


def test_s18_long_straddle_before_an_event_with_cheap_implied_vol() -> None:
    sig = S18SyntheticOptions().generate(make_ctx(s18_frame(ovx=30.0, garch=0.45, hours=24.0)))
    assert sig is not None
    assert sig.meta["structure"] == "long_straddle"
    assert len(sig.meta["legs"]) == 2
    assert {leg["type"] for leg in sig.meta["legs"]} == {"call", "put"}
    assert all(leg["qty"] == 1.0 for leg in sig.meta["legs"])
    assert sig.meta["premium_pct"] > 0.0
    assert sig.meta["vol_gap"] == pytest.approx(0.45 - 0.30)
    assert sig.expected_return > 0.0  # GARCH move above the premium paid
    assert_valid(sig)


def test_s18_short_defined_risk_strangle_when_premium_is_rich() -> None:
    sig = S18SyntheticOptions().generate(make_ctx(s18_frame(ovx=60.0, garch=0.30, hours=500.0)))
    assert sig is not None
    assert sig.meta["structure"] == "short_strangle_defined_risk"
    legs = sig.meta["legs"]
    assert len(legs) == 4
    assert sum(1 for leg in legs if leg["qty"] < 0) == 2  # two short bodies
    assert sum(1 for leg in legs if leg["qty"] > 0) == 2  # two long wings: risk is defined
    assert sig.meta["premium_pct"] > 0.0
    assert sig.meta["vol_gap"] == pytest.approx(0.60 - 0.30)
    assert_valid(sig)


def test_s18_signals_are_flat_approx_and_on_a_synthetic_instrument() -> None:
    for ovx, garch, hours in ((30.0, 0.45, 24.0), (60.0, 0.30, 500.0)):
        sig = S18SyntheticOptions().generate(make_ctx(s18_frame(ovx=ovx, garch=garch, hours=hours)))
        assert sig is not None
        assert sig.direction is Direction.FLAT  # delta neutral: never a futures position
        assert sig.meta["approx"] is True
        assert sig.meta["multi_leg"] is True
        assert sig.meta["master_weight"] == 0.0
        assert sig.instrument.startswith(SYNTHETIC_PREFIX)
        assert sig.instrument == f"{SYNTHETIC_PREFIX}BZZ26"
        assert "approssimazione" in sig.rationale.lower()
        assert all(leg["approx"] is True for leg in sig.meta["legs"])


def test_s18_none_when_no_structure_applies() -> None:
    strat = S18SyntheticOptions()
    # implied vol not rich enough and no event in sight
    assert strat.generate(make_ctx(s18_frame(ovx=30.0, garch=0.30, hours=500.0))) is None
    # event close but implied vol already above the forecast
    assert strat.generate(make_ctx(s18_frame(ovx=60.0, garch=0.30, hours=24.0))) is None


def test_s18_none_without_implied_or_forecast_vol() -> None:
    df = s18_frame(ovx=30.0, garch=0.45, hours=24.0)
    df[cat.OVX] = float("nan")
    assert S18SyntheticOptions().generate(make_ctx(df)) is None
    df2 = s18_frame(ovx=30.0, garch=0.45, hours=24.0)
    df2[cat.GARCH_VOL] = float("nan")
    assert S18SyntheticOptions().generate(make_ctx(df2)) is None


def test_s18_deterministic_and_params() -> None:
    strat = S18SyntheticOptions()
    ctx = make_ctx(s18_frame(ovx=30.0, garch=0.45, hours=24.0))
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()
    p = S18SyntheticOptions.default_params()
    for key in ("max_hours_straddle", "min_hours_strangle", "rich_vol_mult", "strangle_width", "wing_width"):
        assert key in p
