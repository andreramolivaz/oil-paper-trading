"""Strategies agent G5: S13 (geopolitical premium), S14 (seasonality), S15 (COT positioning).

ALL DATA HERE IS SYNTHETIC: hand-built feature frames whose only purpose is to make each documented branch of
docs/STRATEGIES.md fire offline and deterministically. No network, no fixtures.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.regime.base import (
    LABEL_BACKWARDATION_HIGHVOL_GEO,
    LABEL_LOWVOL_RANGE,
    LABEL_SHOCK_CRASH,
    RegimeState,
)
from engine.strategies.base import Family, MarketContext
from engine.strategies.s13_geopolitical import S13GeopoliticalPremium
from engine.strategies.s14_seasonality import S14Seasonality
from engine.strategies.s15_cot import S15CotPositioning

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


def make_ctx(df: pd.DataFrame, *, label: str = LABEL_LOWVOL_RANGE, price: float = 100.0) -> MarketContext:
    ts = df.index[-1].to_pydatetime()
    return MarketContext(
        ts=ts,
        features=df,
        regime=RegimeState(ts=ts, regime_id=2, label=label, confidence=0.7),
        price=price,
        instrument="BZZ26",
        curve_codes={"M1": "BZZ26"},
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
# S13 - geopolitical premium
# ----------------------------------------------------------------------------------------------------------------
def test_s13_spike_goes_long_two_days() -> None:
    df = make_frame(120, **{cat.GEO_SPIKE: 0.0, cat.GEO_INDEX: 0.3})
    df.loc[df.index[-1], cat.GEO_SPIKE] = 1.0
    df.loc[df.index[-1], cat.GEO_INDEX] = 0.85
    sig = S13GeopoliticalPremium().generate(make_ctx(df, label=LABEL_BACKWARDATION_HIGHVOL_GEO))
    assert sig is not None and sig.direction is Direction.LONG
    assert sig.horizon_days == 2
    assert sig.meta["ttl_days"] == 2
    assert sig.meta["branch"] == "spike"
    assert sig.stop_pct == pytest.approx(1.5 * 0.02)  # 1.5 ATR
    assert sig.family == Family.EVENT
    assert_valid(sig)


def test_s13_deescalation_shorts_and_closes_longs() -> None:
    df = make_frame(120)
    df.loc[df.index[-1], cat.DEESCALATION_FLAG] = 1.0
    sig = S13GeopoliticalPremium().generate(make_ctx(df))
    assert sig is not None and sig.direction is Direction.SHORT
    assert sig.horizon_days == 3
    assert sig.meta["close_longs"] is True
    assert_valid(sig)


def test_s13_deescalation_overrides_a_same_day_spike() -> None:
    df = make_frame(120)
    df.loc[df.index[-1], cat.GEO_SPIKE] = 1.0
    df.loc[df.index[-1], cat.DEESCALATION_FLAG] = 1.0
    sig = S13GeopoliticalPremium().generate(make_ctx(df))
    assert sig is not None and sig.direction is Direction.SHORT and sig.meta["close_longs"] is True
    # with the override disabled the spike branch wins instead
    other = S13GeopoliticalPremium({"deescalation_overrides": False}).generate(make_ctx(df))
    assert other is not None and other.direction is Direction.LONG


def fade_frame(
    *,
    days_ago: int = 5,
    spread_chg: float = -0.01,
    premium_falling: bool = True,
    geo_above_mean: bool = True,
    curve_approx: float = 0.0,
    geo_falling: bool = False,
) -> pd.DataFrame:
    df = make_frame(120, **{cat.SPREAD_CHG_5: spread_chg, cat.SPOT_FRONT_PREMIUM: 0.01, cat.CURVE_APPROX: curve_approx})
    df.loc[df.index[-days_ago - 1], cat.GEO_SPIKE] = 1.0
    if premium_falling:
        df.loc[df.index[-1], cat.SPOT_FRONT_PREMIUM] = 0.004
    else:
        df.loc[df.index[-1], cat.SPOT_FRONT_PREMIUM] = 0.02
    if geo_falling:
        for k, val in enumerate([0.75, 0.70, 0.64, 0.58, 0.52, 0.45]):
            df.loc[df.index[-6 + k], cat.GEO_INDEX] = val
    elif geo_above_mean:
        df.loc[df.index[-1], cat.GEO_INDEX] = 0.6
    else:
        df.loc[df.index[-1], cat.GEO_INDEX] = 0.2  # premium already reabsorbed: below the 20d mean
    return df


def test_s13_fade_shorts_the_unconfirmed_premium() -> None:
    sig = S13GeopoliticalPremium().generate(make_ctx(fade_frame()))
    assert sig is not None and sig.direction is Direction.SHORT
    assert sig.meta["branch"] == "fade"
    assert sig.meta["spike_days_ago"] == 5
    assert sig.meta["curve_approx"] is False
    assert sig.horizon_days == 10
    assert_valid(sig)


def test_s13_fade_requires_spike_at_least_three_sessions_old() -> None:
    # a spike 2 sessions ago is still inside the momentum window: no fade
    assert S13GeopoliticalPremium().generate(make_ctx(fade_frame(days_ago=2))) is None


def test_s13_fade_none_when_curve_confirms() -> None:
    assert S13GeopoliticalPremium().generate(make_ctx(fade_frame(spread_chg=0.01))) is None


def test_s13_fade_none_when_dated_premium_is_rising() -> None:
    assert S13GeopoliticalPremium().generate(make_ctx(fade_frame(premium_falling=False))) is None


def test_s13_fade_none_when_geo_index_back_below_its_mean() -> None:
    assert S13GeopoliticalPremium().generate(make_ctx(fade_frame(geo_above_mean=False))) is None


def test_s13_fade_with_proxy_curve_needs_falling_geo_index_and_halves_size() -> None:
    strat = S13GeopoliticalPremium()
    real = strat.generate(make_ctx(fade_frame()))
    approx = strat.generate(make_ctx(fade_frame(curve_approx=1.0, geo_falling=True)))
    assert real is not None and approx is not None
    assert approx.direction is Direction.SHORT
    assert approx.meta["curve_approx"] is True
    assert approx.strength == pytest.approx(real.strength * 0.5)
    # proxy curve + geo index NOT falling -> no fade at all
    assert strat.generate(make_ctx(fade_frame(curve_approx=1.0, geo_falling=False))) is None


def test_s13_none_when_quiet() -> None:
    assert S13GeopoliticalPremium().generate(make_ctx(make_frame(120))) is None


def test_s13_none_when_required_feature_missing() -> None:
    df = make_frame(120)
    df.loc[df.index[-1], cat.GEO_SPIKE] = 1.0
    df[cat.RV_YZ_21] = float("nan")
    assert S13GeopoliticalPremium().generate(make_ctx(df)) is None


def test_s13_deterministic_and_params() -> None:
    strat = S13GeopoliticalPremium()
    ctx = make_ctx(fade_frame())
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()
    p = S13GeopoliticalPremium.default_params()
    for key in ("spike_horizon_days", "spike_stop_atr", "fade_min_days_ago", "fade_max_days_ago", "geo_mean_window"):
        assert key in p


# ----------------------------------------------------------------------------------------------------------------
# S14 - seasonality (Bonferroni over 52 weekly tests)
# ----------------------------------------------------------------------------------------------------------------
def seasonal_frame(
    *,
    effect: float = 0.0,
    years: int = 12,
    seed: int = 7,
    end: str = "2026-09-30",
    current_week_only: bool = False,
) -> pd.DataFrame:
    """Synthetic daily returns: iid noise plus, optionally, a planted effect on the CURRENT ISO week."""
    rows = years * 252
    df = make_frame(rows, end=end)
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.004, rows)
    iso = df.index.isocalendar()
    weeks = iso["week"].to_numpy().astype(int)
    iso_years = iso["year"].to_numpy().astype(int)
    cur = df.index[-1].isocalendar()
    cur_week, cur_year = int(cur.week), int(cur.year)
    if effect != 0.0:
        if current_week_only:
            mask = (weeks == cur_week) & (iso_years == cur_year)
        else:
            mask = weeks == cur_week
        rets = rets + mask * effect
    df[cat.RET_1] = rets
    return df


def test_s14_accepts_a_planted_significant_week() -> None:
    sig = S14Seasonality().generate(make_ctx(seasonal_frame(effect=0.006)))
    assert sig is not None and sig.direction is Direction.LONG
    assert sig.strength == pytest.approx(0.25)
    assert sig.prob == pytest.approx(0.55)
    assert sig.meta["tilt_only"] is True
    assert sig.meta["n_tests"] == 52
    assert sig.meta["p_bonferroni"] < 0.05
    assert abs(sig.meta["t_stat"]) > 2.0
    assert sig.family == Family.FUNDAMENTAL
    assert_valid(sig)


def test_s14_planted_negative_week_tilts_short() -> None:
    sig = S14Seasonality().generate(make_ctx(seasonal_frame(effect=-0.006)))
    assert sig is not None and sig.direction is Direction.SHORT
    assert sig.meta["mean_weekly_return"] < 0.0


def test_s14_rejects_pure_noise() -> None:
    for seed in (1, 2, 3, 7, 11):
        assert S14Seasonality().generate(make_ctx(seasonal_frame(seed=seed))) is None, seed


def test_s14_is_causal_current_week_cannot_justify_itself() -> None:
    # a huge effect present ONLY in the current (partial) ISO week must be ignored: that week is excluded
    df = seasonal_frame(effect=0.05, current_week_only=True)
    assert S14Seasonality().generate(make_ctx(df)) is None


def test_s14_disabled_in_shock_and_geopolitical_regimes() -> None:
    df = seasonal_frame(effect=0.006)
    strat = S14Seasonality()
    assert strat.generate(make_ctx(df, label=LABEL_SHOCK_CRASH)) is None
    assert strat.generate(make_ctx(df, label=LABEL_BACKWARDATION_HIGHVOL_GEO)) is None
    assert strat.generate(make_ctx(df, label=LABEL_LOWVOL_RANGE)) is not None


def test_s14_none_without_enough_years() -> None:
    assert S14Seasonality().generate(make_ctx(seasonal_frame(effect=0.006, years=3))) is None


def test_s14_deterministic_and_params() -> None:
    strat = S14Seasonality()
    ctx = make_ctx(seasonal_frame(effect=0.006))
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()
    p = S14Seasonality.default_params()
    for key in ("lookback_years", "alpha", "n_tests", "min_abs_t", "min_years", "tilt_strength", "tilt_prob"):
        assert key in p


# ----------------------------------------------------------------------------------------------------------------
# S15 - COT positioning
# ----------------------------------------------------------------------------------------------------------------
def cot_frame(
    *,
    brent_pctl: float | None = 0.95,
    wti_pctl: float | None = None,
    tsmom_path: tuple[float, ...] = (1.0, 0.9, 0.7, 0.5, 0.35, 0.2),
    crowding: float | None = None,
) -> pd.DataFrame:
    df = make_frame(320, **{cat.TSMOM_21: tsmom_path[0]})
    if brent_pctl is not None:
        df[cat.COT_MM_NET_BRENT_PCTL] = brent_pctl
    if wti_pctl is not None:
        df[cat.COT_MM_NET_WTI_PCTL] = wti_pctl
    if crowding is not None:
        df[cat.COT_CROWDING] = crowding
    for k, val in enumerate(tsmom_path):
        df.loc[df.index[-len(tsmom_path) + k], cat.TSMOM_21] = val
    return df


def test_s15_contrarian_short_at_the_top_of_the_distribution() -> None:
    sig = S15CotPositioning().generate(make_ctx(cot_frame(brent_pctl=0.95)))
    assert sig is not None and sig.direction is Direction.SHORT
    assert sig.horizon_days == 10
    assert sig.meta["pctl_source"] == cat.COT_MM_NET_BRENT_PCTL
    assert sig.meta["tsmom_21_change"] < 0.0
    assert_valid(sig)


def test_s15_contrarian_long_at_the_bottom_of_the_distribution() -> None:
    rising = (0.2, 0.35, 0.5, 0.7, 0.9, 1.0)
    sig = S15CotPositioning().generate(make_ctx(cot_frame(brent_pctl=0.05, tsmom_path=rising)))
    assert sig is not None and sig.direction is Direction.LONG
    assert sig.meta["tsmom_21_change"] > 0.0
    assert_valid(sig)


def test_s15_none_without_trend_exhaustion() -> None:
    rising = (0.2, 0.35, 0.5, 0.7, 0.9, 1.0)
    assert S15CotPositioning().generate(make_ctx(cot_frame(brent_pctl=0.95, tsmom_path=rising))) is None


def test_s15_none_inside_the_distribution() -> None:
    assert S15CotPositioning().generate(make_ctx(cot_frame(brent_pctl=0.5))) is None


def test_s15_falls_back_to_the_wti_percentile() -> None:
    sig = S15CotPositioning().generate(make_ctx(cot_frame(brent_pctl=None, wti_pctl=0.96)))
    assert sig is not None and sig.direction is Direction.SHORT
    assert sig.meta["pctl_source"] == cat.COT_MM_NET_WTI_PCTL


def test_s15_none_when_no_positioning_data() -> None:
    assert S15CotPositioning().generate(make_ctx(cot_frame(brent_pctl=None))) is None


@pytest.mark.parametrize(
    ("crowding", "pctl", "direction", "expected"),
    [
        (1.0, 0.95, Direction.LONG, 0.0),  # fully crowded long book, trade is long -> size zeroed
        (0.85, 0.95, Direction.LONG, 0.5),
        (0.7, 0.95, Direction.LONG, 1.0),  # at the floor: no reduction
        (0.6, 0.95, Direction.LONG, 1.0),
        (1.0, 0.95, Direction.SHORT, 1.0),  # crowd is long, trade is short -> untouched
        (1.0, 0.05, Direction.SHORT, 0.0),  # crowd is short, trade is short
        (1.0, 0.05, Direction.LONG, 1.0),
        (1.0, 0.95, Direction.FLAT, 1.0),
    ],
)
def test_s15_crowding_filter(crowding: float, pctl: float, direction: Direction, expected: float) -> None:
    ctx = make_ctx(cot_frame(brent_pctl=pctl, crowding=crowding))
    assert S15CotPositioning.crowding_filter(ctx, direction) == pytest.approx(expected)


def test_s15_crowding_filter_neutral_without_data() -> None:
    ctx = make_ctx(cot_frame(brent_pctl=0.95))  # COT_CROWDING is NaN
    assert S15CotPositioning.crowding_filter(ctx, Direction.LONG) == pytest.approx(1.0)
    ctx2 = make_ctx(cot_frame(brent_pctl=None, crowding=1.0))  # no percentile at all
    assert S15CotPositioning.crowding_filter(ctx2, Direction.LONG) == pytest.approx(1.0)


def test_s15_deterministic_and_params() -> None:
    strat = S15CotPositioning()
    ctx = make_ctx(cot_frame())
    a, b = strat.generate(ctx), strat.generate(ctx)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()
    p = S15CotPositioning.default_params()
    for key in ("upper_pctl", "lower_pctl", "tsmom_lookback", "horizon_days", "crowding_floor", "crowding_span"):
        assert key in p
