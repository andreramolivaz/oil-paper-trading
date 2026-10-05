"""Offline deterministic tests for the trend family (S1, S2, S3).

ALL DATA IN THIS MODULE IS SYNTHETIC: hand-built feature frames designed to trigger a documented branch of a
strategy. No real market value appears here; the real-snapshot fixtures live under tests/fixtures/.

`base_frame` / `make_ctx` are shared with tests/test_strategies_g2.py and tests/test_strategies_g3.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.core.events import Direction, Signal
from engine.features.catalog import (
    ATR_14,
    BRENT_WTI,
    BRENT_WTI_Z,
    BUTTERFLY_1_3_6,
    COT_CROWDING,
    COT_MM_NET_BRENT_PCTL,
    CRACK_321,
    CRACK_321_Z,
    CRUDE_STOCKS_VS_5Y,
    CURVE_APPROX,
    CUSHING_VS_5Y,
    DONCHIAN_POS_55,
    GEO_SPIKE,
    PRODUCT_LEAD,
    PX,
    PX_FRONT,
    RET_1,
    RET_5,
    ROLL_YIELD_ANN,
    ROLL_YIELD_PCTL,
    RV_YZ_21,
    SLOPE_M1_M3,
    SLOPE_M1_M6,
    SPOT_FRONT_PREMIUM,
    SPREAD_CHG_5,
    TSMOM_10,
    TSMOM_21,
    TSMOM_63,
    TSMOM_126,
    TSMOM_252,
    VOL_PCTL_1Y,
)
from engine.regime.base import LABEL_LOWVOL_RANGE, LABEL_TRANSITION, RegimeState
from engine.strategies.base import MarketContext
from engine.strategies.s01_momentum import S1MomentumCarry
from engine.strategies.s02_breakout import S2CompressionBreakout
from engine.strategies.s03_kalman_trend import S3KalmanTrend

N_ROWS = 320
CURVE_CODES = {"M1": "BZZ26", "M2": "BZF27", "M3": "BZG27", "M6": "BZK27"}
TSMOM_COLS = (TSMOM_10, TSMOM_21, TSMOM_63, TSMOM_126, TSMOM_252)


# --------------------------------------------------------------------------- synthetic frame builders
def ar1(n: int, phi: float, sd: float, seed: int, mean: float = 0.0) -> np.ndarray:
    """Deterministic AR(1) path (synthetic): x[t] = phi*x[t-1] + eps, eps ~ N(0, sd)."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n, dtype=float)
    eps = rng.normal(0.0, sd, n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + eps[i]
    return x + mean


def base_frame(n: int = N_ROWS, drift: float = 0.0, seed: int = 11) -> pd.DataFrame:
    """A complete, fully synthetic feature frame: every strategy is flat on it unless a test moves a value."""
    idx = pd.bdate_range("2025-01-02", periods=n, tz=UTC)
    t = np.arange(n, dtype=float)
    log_px = np.log(90.0) + drift * t + ar1(n, 0.0, 0.01, seed + 1)
    px = np.exp(log_px)
    df = pd.DataFrame(index=idx)
    df[PX] = px
    df[PX_FRONT] = px
    df[RET_1] = pd.Series(np.log(px), index=idx).diff().fillna(0.0)
    df[RET_5] = pd.Series(np.log(px), index=idx).diff(5).fillna(0.0)
    df[RV_YZ_21] = 0.30
    df[ATR_14] = 0.02
    df[VOL_PCTL_1Y] = 0.50
    df[DONCHIAN_POS_55] = 0.50
    for col in TSMOM_COLS:
        df[col] = 0.0
    df[SLOPE_M1_M6] = 0.0
    df[COT_CROWDING] = 0.30
    df[COT_MM_NET_BRENT_PCTL] = 0.50
    df[CURVE_APPROX] = 0.0
    df[ROLL_YIELD_ANN] = ar1(n, 0.95, 0.01, seed + 2)
    df[ROLL_YIELD_PCTL] = 0.50
    df[SLOPE_M1_M3] = ar1(n, 0.97, 0.002, seed + 3)
    df[SPREAD_CHG_5] = ar1(n, 0.80, 0.002, seed + 4)
    df[SPOT_FRONT_PREMIUM] = ar1(n, 0.90, 0.003, seed + 5)
    df[CRUDE_STOCKS_VS_5Y] = ar1(n, 0.98, 0.005, seed + 6)
    df[CUSHING_VS_5Y] = ar1(n, 0.98, 0.005, seed + 7)
    df[GEO_SPIKE] = 0.0
    df[BUTTERFLY_1_3_6] = ar1(n, 0.85, 0.001, seed + 8)
    df[BRENT_WTI] = ar1(n, 0.99, 0.30, seed + 9, mean=5.0)
    df[BRENT_WTI_Z] = 0.0
    df[CRACK_321] = ar1(n, 0.95, 0.50, seed + 10, mean=20.0)
    df[CRACK_321_Z] = 0.0
    df[PRODUCT_LEAD] = ar1(n, 0.50, 0.01, seed + 11)
    return df


def set_last(df: pd.DataFrame, **values: float) -> pd.DataFrame:
    out = df.copy()
    for col, value in values.items():
        out.iloc[-1, out.columns.get_loc(col)] = value
    return out


def set_tail(df: pd.DataFrame, rows: int, **values: float) -> pd.DataFrame:
    out = df.copy()
    for col, value in values.items():
        out.iloc[-rows:, out.columns.get_loc(col)] = value
    return out


def make_ctx(
    df: pd.DataFrame,
    *,
    label: str = LABEL_LOWVOL_RANGE,
    confidence: float = 0.85,
    price: float = 90.0,
    instrument: str = "BZZ26",
    curve_codes: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> MarketContext:
    ts = df.index[-1]
    assert isinstance(ts, pd.Timestamp)
    regime = RegimeState(ts=ts.to_pydatetime(), regime_id=0, label=label, confidence=confidence)
    return MarketContext(
        ts=ts.to_pydatetime(),
        features=df,
        regime=regime,
        price=price,
        instrument=instrument,
        curve_codes=CURVE_CODES.copy() if curve_codes is None else curve_codes,
        params=params or {},
    )


def assert_valid(sig: Signal) -> None:
    """Every signal the engine accepts must satisfy these invariants."""
    assert 0.0 < sig.prob < 1.0
    assert sig.expected_vol > 0.0
    assert sig.horizon_days >= 1
    assert 0.0 <= sig.strength <= 1.0
    assert sig.rationale and any(ch.isdigit() for ch in sig.rationale)
    assert sig.family and sig.regime
    if sig.stop_pct is not None:
        assert sig.stop_pct > 0.0
    if sig.direction is Direction.FLAT:
        assert sig.expected_return == 0.0
    else:
        assert sig.direction.sign * sig.expected_return > 0.0


# =========================================================================== S1
def _trend_frame(sign: float, slope: float) -> pd.DataFrame:
    df = base_frame()
    df = set_last(df, **dict.fromkeys(TSMOM_COLS, sign))
    return set_last(df, **{SLOPE_M1_M6: slope})


def test_s1_long_with_carry_agreement():
    sig = S1MomentumCarry().generate(make_ctx(_trend_frame(1.0, 0.04)))
    assert sig is not None and sig.direction is Direction.LONG
    assert_valid(sig)
    assert sig.meta["carry_agrees"] is True
    assert sig.meta["trend_score"] == 1.0
    # vol_scale(0.15, 0.30) = 0.5 and full size because trend and slope agree
    assert sig.strength == 0.5
    assert sig.stop_pct == 2.0 * 0.02
    assert "backwardation" in sig.rationale and "concorde" in sig.rationale
    assert "," in sig.rationale  # Italian decimal comma from fmt_pct / fmt_num


def test_s1_short_and_carry_divergence_halves_size():
    short = S1MomentumCarry().generate(make_ctx(_trend_frame(-1.0, -0.04)))
    assert short is not None and short.direction is Direction.SHORT
    assert_valid(short)
    assert short.strength == 0.5 and short.meta["carry_agrees"] is True

    diverging = S1MomentumCarry().generate(make_ctx(_trend_frame(-1.0, 0.04)))
    assert diverging is not None and diverging.direction is Direction.SHORT
    assert diverging.strength == 0.25  # halved
    assert "discorde" in diverging.rationale


def test_s1_none_when_horizons_disagree_or_feature_missing():
    df = base_frame()
    # weights 1,2,3,4,5 -> score = (1-2+3+4-5)/15 = 0.067, below min_trend_score
    df = set_last(df, **{TSMOM_10: 1.0, TSMOM_21: -1.0, TSMOM_63: 1.0, TSMOM_126: 1.0, TSMOM_252: -1.0})
    assert S1MomentumCarry().generate(make_ctx(df)) is None

    missing = set_last(_trend_frame(1.0, 0.04), **{TSMOM_63: float("nan")})
    assert S1MomentumCarry().generate(make_ctx(missing)) is None

    short_history = _trend_frame(1.0, 0.04).iloc[-50:]
    assert S1MomentumCarry().generate(make_ctx(short_history)) is None


def test_s1_crowding_skip_only_on_the_crowded_side():
    crowded_long = set_last(_trend_frame(1.0, 0.04), **{COT_CROWDING: 0.95, COT_MM_NET_BRENT_PCTL: 0.98})
    assert S1MomentumCarry().generate(make_ctx(crowded_long)) is None

    crowded_short = set_last(_trend_frame(1.0, 0.04), **{COT_CROWDING: 0.95, COT_MM_NET_BRENT_PCTL: 0.02})
    sig = S1MomentumCarry().generate(make_ctx(crowded_short))
    assert sig is not None and sig.direction is Direction.LONG  # crowding is on the other side

    # crowded but the side is unknown: stay out rather than guess
    unknown = set_last(_trend_frame(1.0, 0.04), **{COT_CROWDING: 0.95, COT_MM_NET_BRENT_PCTL: float("nan")})
    assert S1MomentumCarry().generate(make_ctx(unknown)) is None

    # the threshold is a parameter and must be honoured
    relaxed = S1MomentumCarry(params={"cot_crowding_max": 0.99})
    assert relaxed.generate(make_ctx(crowded_long)) is not None


def test_s1_determinism_and_params_exposed():
    ctx = make_ctx(_trend_frame(1.0, 0.04))
    strat = S1MomentumCarry()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None
    assert first.to_dict() == second.to_dict()
    for key in ("horizon_weights", "cot_crowding_max", "divergence_size_mult", "stop_atr_mult", "target_vol"):
        assert key in S1MomentumCarry.default_params()


# =========================================================================== S2
def _compression_frame(donchian: float, ret1: float) -> pd.DataFrame:
    df = set_tail(base_frame(), 15, **{VOL_PCTL_1Y: 0.10})
    return set_last(df, **{DONCHIAN_POS_55: donchian, RET_1: ret1})


def test_s2_long_and_short_breakout():
    long_sig = S2CompressionBreakout().generate(make_ctx(_compression_frame(1.05, 0.03)))
    assert long_sig is not None and long_sig.direction is Direction.LONG
    assert_valid(long_sig)
    assert long_sig.stop_pct == 2.0 * 0.02 and long_sig.trailing_pct == 3.0 * 0.02
    assert long_sig.meta["volume_confirmed"] is None  # no volume column in the frame
    assert abs(long_sig.meta["breakout_atr"] - 1.5) < 1e-12

    short_sig = S2CompressionBreakout().generate(make_ctx(_compression_frame(-0.02, -0.03)))
    assert short_sig is not None and short_sig.direction is Direction.SHORT
    assert_valid(short_sig)


def test_s2_compression_gate_and_channel_gate():
    # inside the channel: no breakout
    assert S2CompressionBreakout().generate(make_ctx(_compression_frame(0.50, 0.03))) is None
    # vol not compressed in the 10 preceding days
    not_compressed = set_tail(base_frame(), 15, **{VOL_PCTL_1Y: 0.60})
    not_compressed = set_last(not_compressed, **{DONCHIAN_POS_55: 1.05, RET_1: 0.03})
    assert S2CompressionBreakout().generate(make_ctx(not_compressed)) is None
    # threshold is a parameter
    relaxed = S2CompressionBreakout(params={"vol_pctl_max": 0.70})
    assert relaxed.generate(make_ctx(not_compressed)) is not None


def test_s2_optional_volume_confirmation():
    df = _compression_frame(1.05, 0.03)
    confirmed = df.copy()
    confirmed["brent_front_volume"] = 1000.0
    confirmed.iloc[-1, confirmed.columns.get_loc("brent_front_volume")] = 2000.0
    sig = S2CompressionBreakout().generate(make_ctx(confirmed))
    assert sig is not None and sig.meta["volume_confirmed"] is True
    assert "volume confermato" in sig.rationale

    rejected = df.copy()
    rejected["brent_front_volume"] = 1000.0
    rejected.iloc[-1, rejected.columns.get_loc("brent_front_volume")] = 100.0
    assert S2CompressionBreakout().generate(make_ctx(rejected)) is None


def test_s2_determinism_and_params_exposed():
    ctx = make_ctx(_compression_frame(1.05, 0.03))
    strat = S2CompressionBreakout()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None and first.to_dict() == second.to_dict()
    for key in ("vol_pctl_max", "compression_lookback_days", "stop_atr_mult", "trailing_atr_mult", "volume_window"):
        assert key in S2CompressionBreakout.default_params()


# =========================================================================== S3
def test_s3_long_and_short_trend():
    up = S3KalmanTrend().generate(make_ctx(base_frame(drift=0.002)))
    assert up is not None and up.direction is Direction.LONG
    assert_valid(up)
    assert up.meta["slope_z"] > 1.5 and up.meta["slope_log_per_day"] > 0
    assert up.stop_pct == 2.5 * 0.02

    down = S3KalmanTrend().generate(make_ctx(base_frame(drift=-0.002)))
    assert down is not None and down.direction is Direction.SHORT
    assert_valid(down)


def test_s3_none_without_trend_and_without_history():
    flat = base_frame()
    flat[PX] = 90.0  # perfectly flat synthetic price: filtered slope is zero
    assert S3KalmanTrend().generate(make_ctx(flat)) is None
    # noisy random walk with no drift stays below the threshold
    assert S3KalmanTrend().generate(make_ctx(base_frame(drift=0.0))) is None
    assert S3KalmanTrend().generate(make_ctx(base_frame(drift=0.002).iloc[-100:])) is None
    missing = base_frame(drift=0.002)
    missing.iloc[-1, missing.columns.get_loc(RV_YZ_21)] = float("nan")
    assert S3KalmanTrend().generate(make_ctx(missing)) is None


def test_s3_regime_transition_inflates_observation_noise():
    df = base_frame(drift=0.002)
    normal = S3KalmanTrend().generate(make_ctx(df, label=LABEL_LOWVOL_RANGE, confidence=0.9))
    transition = S3KalmanTrend().generate(make_ctx(df, label=LABEL_TRANSITION, confidence=0.4))
    assert normal is not None and transition is not None
    assert normal.meta["noise_inflated"] is False
    assert transition.meta["noise_inflated"] is True
    assert transition.meta["obs_noise"] > normal.meta["obs_noise"]
    # more observation noise -> less confident slope -> smaller |z| and smaller size
    assert abs(transition.meta["slope_z"]) < abs(normal.meta["slope_z"])
    assert transition.strength <= normal.strength


def test_s3_higher_vol_reduces_conviction_and_determinism():
    df = base_frame(drift=0.002)
    calm = S3KalmanTrend().generate(make_ctx(df))
    loud = df.copy()
    loud[RV_YZ_21] = 0.60
    noisy = S3KalmanTrend().generate(make_ctx(loud))
    assert calm is not None and noisy is not None
    assert abs(noisy.meta["slope_z"]) < abs(calm.meta["slope_z"])

    ctx = make_ctx(df)
    strat = S3KalmanTrend()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None and first.to_dict() == second.to_dict()
    for key in ("window", "q_level", "q_slope", "obs_noise_mult", "transition_inflation", "slope_z_threshold"):
        assert key in S3KalmanTrend.default_params()


def test_s3_uses_fixed_timestamp_context():
    df = base_frame(drift=0.002)
    ctx = make_ctx(df)
    assert ctx.ts.tzinfo is not None
    assert isinstance(ctx.ts, datetime)
