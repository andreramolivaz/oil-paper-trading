"""Features agent A: builder, price, volatility, trend, curve, macro.

All data here is SYNTHETIC (random walks seeded for determinism) and exists only to exercise the logic offline.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.core.calendar import contract_code, listed_months
from engine.core.timeutil import LONDON, settlement_ts
from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features import curve, macro, price, trend, volatility
from engine.features.builder import (
    BASE_COLUMNS,
    FullFeatureBuilder,
    align_released,
    catalog_order,
    compute_all,
    describe,
    settlement_index,
)

MODULES_A = ["price", "volatility", "trend", "curve", "macro"]


# ----------------------------------------------------------------------------------------------------------------
# synthetic market data
# ----------------------------------------------------------------------------------------------------------------
def make_md(
    n: int = 1500,
    seed: int = 0,
    start: str = "2015-01-05",
    with_curve: bool = True,
    proxy_rows: int = 300,
    with_ovx: bool = True,
    with_ohlc: bool = True,
) -> MarketData:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, name="date")
    r = rng.normal(0.0002, 0.02, n)
    close = 60.0 * np.exp(np.cumsum(r))
    prev = np.r_[close[0], close[:-1]]
    opn = prev * np.exp(rng.normal(0, 0.004, n))
    hi = np.maximum(opn, close) * np.exp(np.abs(rng.normal(0, 0.006, n)))
    lo = np.minimum(opn, close) * np.exp(-np.abs(rng.normal(0, 0.006, n)))
    prices = pd.DataFrame(index=idx)
    if with_ohlc:
        prices["brent_front_open"] = opn
        prices["brent_front_high"] = hi
        prices["brent_front_low"] = lo
    prices["brent_front_close"] = close
    prices["brent_cont"] = close * 1.02  # a constant roll-adjustment offset
    prices["brent_spot"] = close * (1 + rng.normal(0.01, 0.005, n))
    prices["wti_front_close"] = close - 4
    prices["ovx"] = (30 + np.cumsum(rng.normal(0, 0.8, n))).clip(10, 120) if with_ovx else np.nan
    prices["vix"] = (18 + np.cumsum(rng.normal(0, 0.5, n))).clip(9, 80)
    prices["dxy"] = 95 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    prices["spx"] = 2000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    prices["copper"] = 3 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    prices["us10y"] = (2.0 + np.cumsum(rng.normal(0, 0.03, n))).clip(0.3, 6)
    prices["breakeven10y"] = (2.0 + np.cumsum(rng.normal(0, 0.01, n))).clip(0.3, 4)
    # daily bars are final at 23:00 London (Yahoo adapter convention)
    pub = pd.Series((idx + pd.Timedelta(hours=23)).tz_localize(LONDON).tz_convert(UTC), index=idx)
    md = MarketData(prices=prices, published_at={"prices": pub})
    if with_curve:
        slope = 0.01 + 0.3 * np.cumsum(rng.normal(0, 0.001, n))
        cv = pd.DataFrame(index=idx)
        for k in range(1, 25):
            cv[f"M{k}"] = close * (1 - slope * (k - 1) / 12)
        codes = [contract_code("BZ", *listed_months("BZ", d.date(), 1)[0]) for d in idx]
        cv["M1_code"] = pd.Series(codes, index=idx, dtype="string")
        wti = close - 4
        for k in range(1, 5):
            cv[f"WTI_C{k}"] = wti * (1 - 0.8 * slope * (k - 1) / 12)
        if proxy_rows:
            cv.iloc[:proxy_rows, :24] = np.nan
            cv.iloc[:proxy_rows, cv.columns.get_loc("M1_code")] = pd.NA
        md.curve = cv
    return md


def eod_asof(day: pd.Timestamp) -> pd.Timestamp:
    """A decision time after every daily table of `day` is published (23:00 London bars, 23:00 UTC fallback of
    MarketData.truncate for tables without published_at) and before the next session's settlement."""
    return pd.Timestamp(settlement_ts(day.date()) + timedelta(hours=6))


@pytest.fixture(scope="module")
def md() -> MarketData:
    return make_md()


@pytest.fixture(scope="module")
def frame(md: MarketData) -> pd.DataFrame:
    return FullFeatureBuilder(modules=MODULES_A).build(md, eod_asof(md.prices.index[-1]))


# ----------------------------------------------------------------------------------------------------------------
# builder
# ----------------------------------------------------------------------------------------------------------------
def test_shape_columns_and_dtypes(md: MarketData, frame: pd.DataFrame) -> None:
    assert list(frame.index) == list(md.prices.index)
    assert frame.index.is_monotonic_increasing and frame.index.tz is None
    assert list(frame.columns[: len(BASE_COLUMNS)]) == BASE_COLUMNS
    for col in catalog_order():
        assert col in frame.columns, col
    for col in cat.ALL_NUMERIC_FEATURES:
        assert frame[col].dtype == "float64", col
    assert frame.attrs["px_source"] == "brent_cont"
    assert frame.attrs["modules_ok"] == MODULES_A and frame.attrs["modules_failed"] == {}
    # the regime columns are placeholders
    assert frame[cat.REGIME_ID].isna().all() and frame[cat.REGIME_LABEL].isna().all()
    assert frame["regime_p_0"].isna().all()
    # base aliases are the front OHLC
    assert np.allclose(frame["close"], md.prices["brent_front_close"])
    assert np.allclose(frame[cat.PX], md.prices["brent_cont"])
    # the main features are populated after warm-up
    tail = frame.iloc[-1]
    for col in (
        cat.RET_21,
        cat.RV_YZ_21,
        cat.GARCH_VOL,
        cat.HURST_100,
        cat.VR_20,
        cat.TSMOM_63,
        cat.ROLL_YIELD_ANN,
        cat.SPOT_FRONT_PREMIUM,
        cat.MACRO_FV_Z,
        cat.DEC_DEC_SPREAD,
    ):
        assert np.isfinite(tail[col]), col


def test_truncate_equivalence_and_future_invariance(md: MarketData) -> None:
    """build(full md, asof T) == build(md.truncate(asof T), asof T), and appending 50 future rows changes nothing."""
    b = FullFeatureBuilder(modules=MODULES_A)
    t_idx = md.prices.index[-51]
    asof = eod_asof(t_idx)
    full = b.build(md, asof)
    assert full.index[-1] == t_idx
    trunc = b.build(md.truncate(asof), asof)
    pd.testing.assert_frame_equal(full, trunc)
    later = b.build(md, eod_asof(md.prices.index[-1]))
    assert len(later) == len(full) + 50
    pd.testing.assert_frame_equal(later.loc[:t_idx], full)


def test_asof_respects_published_at_and_settlement(md: MarketData) -> None:
    b = FullFeatureBuilder(modules=["price"])
    t_idx = md.prices.index[-100]
    # exactly at settlement the 23:00-London bar of t is not yet published -> last row is t-1
    strict = b.build(md, settlement_ts(t_idx.date()))
    assert strict.index[-1] == md.prices.index[-101]
    # without a published_at table the settlement rule alone applies -> last row is t
    md2 = MarketData(prices=md.prices.copy())
    assert b.build(md2, settlement_ts(t_idx.date())).index[-1] == t_idx
    assert b.build(md2, settlement_ts(t_idx.date()) - timedelta(minutes=1)).index[-1] == md.prices.index[-101]
    # settlement_index: 19:30 London -> 18:30 UTC in BST, 19:30 UTC in GMT
    si = settlement_index(pd.DatetimeIndex(["2026-10-05", "2026-12-07"]))
    assert si[0] == pd.Timestamp("2026-10-05 18:30", tz="UTC") and si[1] == pd.Timestamp("2026-12-07 19:30", tz="UTC")


def test_missing_tables_and_failing_module_are_graceful(md: MarketData) -> None:
    md2 = make_md(400, seed=5, with_curve=False, with_ovx=False)
    b = FullFeatureBuilder(modules=[*MODULES_A, "does_not_exist"])
    f = b.build(md2, eod_asof(md2.prices.index[-1]))
    assert len(f) == 400
    assert f[cat.M1].isna().all() and f[cat.CURVE_APPROX].isna().all() and f[cat.OVX].isna().all()
    assert f[cat.VRP].isna().all() and f[cat.GARCH_VOL].isna().all()  # < 500 obs
    assert np.isfinite(f[cat.RV_YZ_21].iloc[-1]) and np.isfinite(f[cat.RET_5].iloc[-1])
    assert "does_not_exist" in f.attrs["modules_failed"]
    assert f.attrs["module_attrs"]["curve"]["curve_available"] is False
    # empty MarketData
    empty = FullFeatureBuilder(modules=MODULES_A).build(MarketData(), eod_asof(pd.Timestamp("2026-10-01")))
    assert empty.empty and cat.GARCH_VOL in empty.columns
    # compute_all shortcut
    f2 = compute_all(md2, eod_asof(md2.prices.index[-1]), {"modules": ["price"]})
    assert f2.attrs["modules_ok"] == ["price"]


def test_px_fallback_and_spot_backfill() -> None:
    md2 = make_md(300, seed=7, with_curve=False)
    md2.prices = md2.prices.drop(columns=["brent_cont"])
    f = FullFeatureBuilder(modules=["price"]).build(md2, eod_asof(md2.prices.index[-1]))
    assert f.attrs["px_source"] == "brent_front_close"
    # futures start late: the spot history is spliced in before the first futures print, scaled at the junction
    md3 = make_md(300, seed=7, with_curve=False)
    md3.prices.loc[md3.prices.index[:100], ["brent_cont", "brent_front_close"]] = np.nan
    f3 = FullFeatureBuilder(modules=["price"]).build(md3, eod_asof(md3.prices.index[-1]))
    info = f3.attrs["px_backfill"]
    assert info and info["column"] == "brent_spot"
    ratio = md3.prices["brent_cont"].iloc[100] / md3.prices["brent_spot"].iloc[100]
    assert np.allclose(f3[cat.PX].iloc[:100], md3.prices["brent_spot"].iloc[:100] * ratio)
    assert np.allclose(f3[cat.PX].iloc[100:], md3.prices["brent_cont"].iloc[100:])
    f4 = FullFeatureBuilder({"px_backfill_spot": False}, modules=["price"]).build(md3, eod_asof(md3.prices.index[-1]))
    assert f4[cat.PX].iloc[:100].isna().all() and f4.attrs["px_backfill"] is None


def test_describe_is_json_able(frame: pd.DataFrame) -> None:
    d = describe(frame)
    json.dumps(d)
    assert d["rows"] == len(frame) and d["px_source"] == "brent_cont"
    assert 0.0 <= d["columns"][cat.GARCH_VOL]["nan_share"] < 0.5
    assert d["columns"][cat.REGIME_ID]["nan_share"] == 1.0 and d["columns"][cat.REGIME_ID]["last"] is None
    assert d["columns"][cat.RET_1]["last"] == pytest.approx(float(frame[cat.RET_1].iloc[-1]))


def test_align_released_uses_publication_not_period() -> None:
    dates = pd.bdate_range("2026-09-01", periods=10)
    # weekly value for period 2026-09-04 published Wednesday 2026-09-09 14:30 UTC (before settlement 18:30 UTC)
    values = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-08-28", "2026-09-04"]))
    published = pd.Series(pd.to_datetime(["2026-09-02 14:30", "2026-09-09 14:30"], utc=True), index=values.index)
    out = align_released(values, published, dates)
    assert out.loc["2026-09-01"] != out.loc["2026-09-01"]  # NaN: nothing published yet
    assert out.loc["2026-09-02"] == 1.0 and out.loc["2026-09-08"] == 1.0
    assert out.loc["2026-09-09"] == 2.0 and out.loc["2026-09-10"] == 2.0
    # a publication after settlement is only usable the next day
    published.iloc[1] = pd.Timestamp("2026-09-09 19:00", tz="UTC")
    out2 = align_released(values, published, dates)
    assert out2.loc["2026-09-09"] == 1.0 and out2.loc["2026-09-10"] == 2.0


# ----------------------------------------------------------------------------------------------------------------
# price / volatility
# ----------------------------------------------------------------------------------------------------------------
def _base_from_prices(prices: pd.DataFrame) -> pd.DataFrame:
    md2 = MarketData(prices=prices)
    return FullFeatureBuilder(modules=[]).base_frame(md2, eod_asof(prices.index[-1]))


def test_yang_zhang_constant_prices_is_zero() -> None:
    idx = pd.bdate_range("2020-01-01", periods=80)
    prices = pd.DataFrame(
        {
            "brent_front_open": 50.0,
            "brent_front_high": 50.0,
            "brent_front_low": 50.0,
            "brent_front_close": 50.0,
            "brent_cont": 50.0,
        },
        index=idx,
    )
    base = _base_from_prices(prices)
    out = volatility.compute(MarketData(prices=prices), eod_asof(idx[-1]), base)
    assert np.allclose(out[cat.RV_YZ_21].dropna(), 0.0) and out[cat.RV_YZ_21].notna().sum() == 80 - 21
    assert np.allclose(out[cat.RV_CC_21].dropna(), 0.0)
    assert out.attrs["yz_fallback_rows"] == 0


def test_yang_zhang_matches_close_to_close_scale_and_fallback(md: MarketData) -> None:
    base = FullFeatureBuilder(modules=[]).base_frame(md, eod_asof(md.prices.index[-1]))
    out = volatility.compute(md, eod_asof(md.prices.index[-1]), base)
    yz, cc = out[cat.RV_YZ_63].dropna(), out[cat.RV_CC_21].dropna()
    # synthetic daily vol 2% -> ~32% annualised: both estimators agree on the scale
    assert 0.25 < yz.mean() < 0.40 and 0.25 < cc.mean() < 0.40
    assert out.attrs["yz_fallback_rows"] == 0 and out.attrs["approx_columns"] == []
    # roll-adjusted bars: the constant 2% offset between brent_cont and the front does not create a gap
    gap = price.compute(md, eod_asof(md.prices.index[-1]), base)[cat.GAP_1]
    assert gap.abs().max() < 0.05
    # without OHLC the module falls back to close-to-close and flags it
    md_cc = make_md(200, seed=3, with_curve=False, with_ohlc=False)
    base_cc = FullFeatureBuilder(modules=[]).base_frame(md_cc, eod_asof(md_cc.prices.index[-1]))
    out_cc = volatility.compute(md_cc, eod_asof(md_cc.prices.index[-1]), base_cc)
    assert out_cc.attrs["yz_fallback_rows"] > 0 and cat.RV_YZ_21 in out_cc.attrs["approx_columns"]
    pd.testing.assert_series_equal(out_cc[cat.RV_YZ_21], out_cc[cat.RV_CC_21], check_names=False)
    p_cc = price.compute(md_cc, eod_asof(md_cc.prices.index[-1]), base_cc)
    assert p_cc.attrs["ohlc_fallback"] is True and p_cc[cat.GAP_1].isna().all()
    assert np.isfinite(p_cc[cat.DONCHIAN_POS_20].iloc[-1]) and np.isfinite(p_cc[cat.ATR_14].iloc[-1])


def test_tsmom_sign_and_returns() -> None:
    idx = pd.bdate_range("2020-01-01", periods=400)
    rng = np.random.default_rng(11)
    up = 50 * np.exp(np.cumsum(0.003 + rng.normal(0, 0.005, 400)))
    for sign, path in ((1, up), (-1, up[::-1])):
        prices = pd.DataFrame({"brent_cont": path, "brent_front_close": path}, index=idx)
        prices["brent_front_open"] = prices["brent_cont"].shift(1).fillna(path[0])
        prices["brent_front_high"] = prices[["brent_front_open", "brent_front_close"]].max(axis=1) * 1.002
        prices["brent_front_low"] = prices[["brent_front_open", "brent_front_close"]].min(axis=1) * 0.998
        base = _base_from_prices(prices)
        out = price.compute(MarketData(prices=prices), eod_asof(idx[-1]), base)
        for col in (cat.TSMOM_10, cat.TSMOM_21, cat.TSMOM_63, cat.TSMOM_126, cat.TSMOM_252):
            assert np.sign(out[col].iloc[-1]) == sign, col
        assert np.sign(out[cat.RET_63].iloc[-1]) == sign and np.sign(out[cat.EMA_FAST_SLOW].iloc[-1]) == sign
        assert out[cat.RET_1].iloc[-1] == pytest.approx(np.log(path[-1] / path[-2]))
        assert out[cat.RET_252].iloc[-1] == pytest.approx(np.log(path[-1] / path[-253]))
    assert out[cat.MONTH].iloc[0] == 1.0 and abs(out[cat.DOY_SIN] ** 2 + out[cat.DOY_COS] ** 2 - 1).max() < 1e-12


def test_donchian_breakout_reads_outside_unit_interval() -> None:
    idx = pd.bdate_range("2021-01-01", periods=120)
    close = np.full(120, 50.0) + np.tile([0.0, 0.5, -0.5, 0.2], 30)
    close[-1] = 60.0  # breakout above every previous high
    prices = pd.DataFrame({"brent_cont": close, "brent_front_close": close}, index=idx)
    prices["brent_front_open"] = close
    prices["brent_front_high"] = close + 0.1
    prices["brent_front_low"] = close - 0.1
    base = _base_from_prices(prices)
    out = price.compute(MarketData(prices=prices), eod_asof(idx[-1]), base)
    assert out[cat.DONCHIAN_POS_20].iloc[-1] > 1.0 and out[cat.DONCHIAN_POS_55].iloc[-1] > 1.0
    inside = out[cat.DONCHIAN_POS_55].iloc[60:-1]
    assert ((inside >= 0) & (inside <= 1)).all()
    assert out[cat.ATR_14].iloc[-2] == pytest.approx(0.2 / 50.0 + 0.0, abs=0.02)  # range ~0.2-1.2 of 50


def test_garch_is_causal_and_fast() -> None:
    rng = np.random.default_rng(21)
    n = 3000
    h, r = 1.0, np.empty(n)
    for t in range(n):  # a stationary GARCH(1,1) path with normal innovations (0.05 + 0.08 + 0.9 < 1 persistence)
        e = rng.standard_normal() * np.sqrt(h)
        r[t] = 0.012 * e
        h = 0.05 + 0.08 * e * e + 0.90 * h
    ret = pd.Series(r, index=pd.bdate_range("2010-01-01", periods=n))
    volatility._GARCH_CACHE.clear()
    t0 = time.perf_counter()
    full = volatility.garch_vol_walk_forward(ret)
    elapsed = time.perf_counter() - t0
    print(f"\nGARCH walk-forward on {n} rows: {elapsed:.1f}s")
    assert elapsed < 60.0
    assert full.iloc[:499].isna().all() and full.iloc[499:].notna().all()
    assert 0.05 < full.dropna().median() < 0.60  # annualised, around the unconditional 0.012*sqrt(50)*sqrt(252)
    truncated = volatility.garch_vol_walk_forward(ret.iloc[:2200])
    pd.testing.assert_series_equal(full.iloc[:2200], truncated)
    # the forecast at t reacts only to returns <= t: perturbing the LAST return leaves everything before unchanged
    shocked = ret.copy()
    shocked.iloc[-1] = 0.25
    out_shocked = volatility.garch_vol_walk_forward(shocked, use_cache=False)
    pd.testing.assert_series_equal(out_shocked.iloc[:-1], full.iloc[:-1])
    assert out_shocked.iloc[-1] > full.iloc[-1]


# ----------------------------------------------------------------------------------------------------------------
# trend
# ----------------------------------------------------------------------------------------------------------------
def _ar1_logprice(phi: float, n: int, seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    e = rng.normal(0, 0.01, n)
    r = np.empty(n)
    r[0] = e[0]
    for t in range(1, n):
        r[t] = phi * r[t - 1] + e[t]
    return pd.Series(np.cumsum(r), index=pd.bdate_range("2000-01-03", periods=n))


def test_hurst_and_variance_ratio_calibration() -> None:
    iid, pers, anti = (_ar1_logprice(phi, 3000, 1) for phi in (0.0, 0.5, -0.5))
    h_iid, h_pers, h_anti = (trend.hurst_dfa(s).mean() for s in (iid, pers, anti))
    assert 0.40 < h_iid < 0.60
    assert h_pers > h_iid + 0.1 and h_anti < h_iid - 0.1
    vr_iid, vr_pers, vr_anti = (trend.variance_ratio(s, 5).mean() for s in (iid, pers, anti))
    assert 0.85 < vr_iid < 1.15 and vr_pers > 1.5 and vr_anti < 0.6
    h = trend.hurst_dfa(iid)
    assert h.iloc[:100].isna().all() and h.iloc[100:].notna().all()
    v = trend.variance_ratio(iid, 20)
    assert v.iloc[:252].isna().all() and v.iloc[252:].notna().all()
    # trailing windows only: the value at t is unchanged when later rows are appended
    pd.testing.assert_series_equal(trend.hurst_dfa(iid.iloc[:1500]), h.iloc[:1500])
    pd.testing.assert_series_equal(trend.variance_ratio(iid.iloc[:1500], 20), v.iloc[:1500])
    # a NaN inside the window propagates (never filled)
    holed = iid.copy()
    holed.iloc[1000] = np.nan
    assert trend.hurst_dfa(holed).iloc[1000:1100].isna().all() and trend.hurst_dfa(holed).iloc[1101:].notna().all()


# ----------------------------------------------------------------------------------------------------------------
# curve
# ----------------------------------------------------------------------------------------------------------------
def test_roll_yield_annualisation_on_known_curve() -> None:
    idx = pd.bdate_range("2026-09-01", periods=30)
    cv = pd.DataFrame({"M1": 100.0, "M2": 99.0, "M3": 98.0, "M6": 95.0, "M12": 90.0}, index=idx)
    cv["M1_code"] = "BZX26"  # Nov-26 front -> M2 = Dec-26, one month apart
    prices = pd.DataFrame({"brent_cont": 100.0, "brent_front_close": 100.0, "brent_spot": 101.0}, index=idx)
    md2 = MarketData(prices=prices, curve=cv)
    base = _base_from_prices(prices)
    out = curve.compute(md2, eod_asof(idx[-1]), base)
    assert out[cat.ROLL_YIELD_ANN].iloc[-1] == pytest.approx((100 / 99 - 1) * 12, rel=1e-9)  # ~12.1%
    assert out[cat.ROLL_YIELD_ANN].iloc[-1] == pytest.approx(0.1212, abs=0.001)
    assert out[cat.SLOPE_M1_M2].iloc[-1] == pytest.approx(0.01) and out[cat.SLOPE_M1_M6].iloc[-1] == pytest.approx(0.05)
    assert out[cat.BUTTERFLY_1_3_6].iloc[-1] == pytest.approx((100 - 2 * 98 + 95) / 100)
    assert out[cat.CURVE_APPROX].iloc[-1] == 0.0 and out[cat.SPREAD_CHG_5].iloc[-1] == 0.0
    # spot premium: no spot-specific published_at -> one trading day of lag assumed
    assert out[cat.SPOT_FRONT_PREMIUM].iloc[-1] == pytest.approx(0.01) and np.isnan(out[cat.SPOT_FRONT_PREMIUM].iloc[0])
    assert out.attrs["spot_alignment"] == "lag_1_trading_day_assumed"
    # with M2_code two months out the annualisation halves
    cv2 = cv.copy()
    cv2["M2_code"] = "BZF27"
    out2 = curve.compute(MarketData(prices=prices, curve=cv2), eod_asof(idx[-1]), base)
    assert out2[cat.ROLL_YIELD_ANN].iloc[-1] == pytest.approx((100 / 99 - 1) * 6, rel=1e-9)


def test_dec_dec_spread_uses_contract_ranks() -> None:
    idx = pd.bdate_range("2026-10-01", periods=5)
    cv = pd.DataFrame(index=idx)
    for k in range(1, 37):
        cv[f"M{k}"] = 100.0 - k  # M1=99 ... M36=64
    cv["M1_code"] = "BZZ26"  # the front IS a December: nearest Dec = M1, following Dec = M13
    prices = pd.DataFrame({"brent_cont": 99.0, "brent_front_close": 99.0}, index=idx)
    out = curve.compute(MarketData(prices=prices, curve=cv), eod_asof(idx[-1]), _base_from_prices(prices))
    assert out[cat.DEC_DEC_SPREAD].iloc[-1] == pytest.approx((99.0 - 87.0) / 99.0)
    cv["M1_code"] = "BZF27"  # Jan-27 front: nearest Dec = M12 (Dec-27), following = M24
    out = curve.compute(MarketData(prices=prices, curve=cv), eod_asof(idx[-1]), _base_from_prices(prices))
    assert out[cat.DEC_DEC_SPREAD].iloc[-1] == pytest.approx((88.0 - 76.0) / 88.0)


def test_curve_approx_flagging_with_wti_proxy(md: MarketData, frame: pd.DataFrame) -> None:
    proxy = frame.iloc[:300]
    real = frame.iloc[300:]
    assert (proxy[cat.CURVE_APPROX] == 1.0).all() and (real[cat.CURVE_APPROX] == 0.0).all()
    assert proxy[cat.M1].isna().all() and proxy[cat.SLOPE_M1_M6].isna().all()  # levels never from the proxy
    c1, c2 = md.curve["WTI_C1"].iloc[:300], md.curve["WTI_C2"].iloc[:300]
    assert np.allclose(proxy[cat.SLOPE_M1_M2], (c1 - c2) / c1)
    assert np.allclose(proxy[cat.ROLL_YIELD_ANN], (c1 / c2 - 1) * 12)
    m1, m2 = md.curve["M1"].iloc[300:], md.curve["M2"].iloc[300:]
    assert np.allclose(real[cat.SLOPE_M1_M2], (m1 - m2) / m1)
    assert set(frame.attrs["approx_columns"]) >= {cat.SLOPE_M1_M2, cat.ROLL_YIELD_ANN, cat.ROLL_YIELD_PCTL}
    # rows with neither real nor proxy curve stay NaN
    md2 = make_md(100, seed=9, proxy_rows=0)
    md2.curve.iloc[:20] = np.nan
    f2 = FullFeatureBuilder(modules=["curve"]).build(md2, eod_asof(md2.prices.index[-1]))
    assert f2[cat.CURVE_APPROX].iloc[:20].isna().all() and (f2[cat.CURVE_APPROX].iloc[20:] == 0.0).all()
    # an assembler-level curve_approx flag marks real-looking rows as approximate
    md2.curve_approx = pd.Series(False, index=md2.curve.index)
    md2.curve_approx.iloc[50:60] = True
    f3 = FullFeatureBuilder(modules=["curve"]).build(md2, eod_asof(md2.prices.index[-1]))
    assert (f3[cat.CURVE_APPROX].iloc[50:60] == 1.0).all() and (f3[cat.CURVE_APPROX].iloc[60:] == 0.0).all()


def test_spot_premium_aligned_on_spot_publication() -> None:
    idx = pd.bdate_range("2026-09-01", periods=12)
    cv = pd.DataFrame({"M1": 100.0}, index=idx)
    cv["M1_code"] = "BZX26"
    spot = pd.Series(np.arange(12, dtype="float64") + 100.0, index=idx)  # premium (spot-100)/100 = 0, 0.01, ...
    prices = pd.DataFrame({"brent_cont": 100.0, "brent_front_close": 100.0, "brent_spot": spot}, index=idx)
    # EIA publishes the spot of day s three days later at 17:00 UTC (before the 18:30 UTC settlement)
    pub = pd.Series((idx + pd.Timedelta(days=3, hours=17)).tz_localize(UTC), index=idx)
    md2 = MarketData(prices=prices, curve=cv, published_at={"brent_spot": pub})
    out = curve.compute(md2, eod_asof(idx[-1]), _base_from_prices(prices))
    assert out.attrs["spot_alignment"] == "published_at:brent_spot"
    prem = out[cat.SPOT_FRONT_PREMIUM]
    assert prem.iloc[:3].isna().all()
    # Thursday 2026-09-03 + 3d = Sunday 06 17:00 -> usable Monday 07; Friday 04 -> Monday 07 17:00 -> usable Monday 07
    assert prem.loc["2026-09-07"] == pytest.approx(0.03)  # latest published: Friday 04 (index 3)
    assert prem.loc["2026-09-10"] == pytest.approx(0.04)  # Monday 07 (index 4) published Thursday 10 17:00
    assert prem.loc["2026-09-14"] == pytest.approx(0.08)  # Fri 11 (index 8) published Mon 14 17:00 -> usable Mon 14


# ----------------------------------------------------------------------------------------------------------------
# macro
# ----------------------------------------------------------------------------------------------------------------
def test_kalman_recovers_constant_betas_and_is_causal() -> None:
    rng = np.random.default_rng(2)
    n = 2000
    x = np.column_stack([rng.normal(0, 0.01, n), rng.normal(0, 0.02, n), rng.normal(0, 0.1, n)])
    beta = np.array([-1.5, 0.8, 0.05])
    y = x @ beta + rng.normal(0, 0.01, n)
    innov, betas = macro.kalman_tvp_innovations(y, x, burn_in=252)
    assert np.isnan(innov[:252]).all() and np.isfinite(innov[252:]).all()
    assert np.allclose(betas[-1][1:], beta, atol=0.15) and abs(betas[-1][0]) < 0.01
    assert abs(np.nanmean(innov)) < 1e-3 and 0.008 < np.nanstd(innov) < 0.013
    innov2, _ = macro.kalman_tvp_innovations(y[:1500], x[:1500], burn_in=252)
    assert np.allclose(innov[:1500], innov2, equal_nan=True)
    # a regime shift in beta is tracked
    y2 = y.copy()
    y2[1000:] = x[1000:] @ np.array([1.5, 0.8, 0.05]) + rng.normal(0, 0.01, 1000)
    _, betas2 = macro.kalman_tvp_innovations(y2, x, burn_in=252, q_rel=1e-3)
    assert betas2[999][1] < -0.5 and betas2[-1][1] > 0.5


def test_macro_module_outputs(md: MarketData, frame: pd.DataFrame) -> None:
    assert np.allclose(frame[cat.DXY], md.prices["dxy"]) and np.allclose(frame[cat.VIX], md.prices["vix"])
    assert frame[cat.DXY_RET_21].iloc[-1] == pytest.approx(
        np.log(md.prices["dxy"].iloc[-1] / md.prices["dxy"].iloc[-22])
    )
    assert frame.attrs["module_attrs"]["macro"]["regressors_used"] == ["dxy", "spx", "copper", "us10y"]
    z = frame[cat.MACRO_FV_Z].dropna()
    assert len(z) > 500 and abs(z.mean()) < 0.5 and 0.5 < z.std() < 1.5
    # a missing regressor table is dropped, not invented
    md2 = make_md(600, seed=4, with_curve=False)
    md2.prices["copper"] = np.nan
    f2 = FullFeatureBuilder(modules=["macro"]).build(md2, eod_asof(md2.prices.index[-1]))
    assert f2.attrs["module_attrs"]["macro"]["regressors_used"] == ["dxy", "spx", "us10y"]
    assert f2[cat.COPPER_RET_21].isna().all() and np.isfinite(f2[cat.MACRO_FV_RESID].iloc[-1])


# ----------------------------------------------------------------------------------------------------------------
# runtime
# ----------------------------------------------------------------------------------------------------------------
def test_full_build_runtime_3000_rows() -> None:
    md3 = make_md(3000, seed=3)
    volatility._GARCH_CACHE.clear()
    t0 = time.perf_counter()
    f = FullFeatureBuilder(modules=MODULES_A).build(md3, eod_asof(md3.prices.index[-1]))
    elapsed = time.perf_counter() - t0
    print(f"\nFullFeatureBuilder (modules A) on 3000 rows: {elapsed:.1f}s")
    assert len(f) == 3000 and elapsed < 60.0
    # daily rebuild hits the GARCH parameter cache
    t0 = time.perf_counter()
    FullFeatureBuilder(modules=MODULES_A).build(md3, eod_asof(md3.prices.index[-1]))
    assert time.perf_counter() - t0 < elapsed


def test_smoke_on_real_fixture_market_data() -> None:
    """Optional smoke test on the real captured snapshots (skipped until another agent ships the loader)."""
    from pathlib import Path

    fixtures_mod = pytest.importorskip("engine.data.fixtures")
    folder = Path(__file__).parent / "fixtures" / "market_data"
    if not folder.exists():
        pytest.skip("tests/fixtures/market_data missing")
    md_real = fixtures_mod.load_fixture_market_data()
    f = FullFeatureBuilder(modules=MODULES_A).build(md_real, eod_asof(md_real.prices.index[-1]))
    assert len(f) > 0 and f.attrs["modules_failed"] == {}
