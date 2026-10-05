"""Tests for engine.forecast.* (models, benchmarks, ensemble, implied range).

OFFLINE and deterministic. ALL market-like series in this file are SYNTHETIC (a random walk with GARCH(1,1)-like
variance, a hand-built backwardated curve, Brownian-bridge hourly bars): they exercise the code paths and the
point-in-time invariants and must never be mistaken for, or shipped as, real data.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from engine.core.calendar import add_business_days, contract_code, expiry_for, front_month
from engine.features import catalog as cat
from engine.forecast.arima import ArimaEtsForecaster
from engine.forecast.base import (
    HORIZONS,
    QUANTILE_LEVELS,
    ForecastQuantiles,
    NotFitted,
    asof_date,
    cdf_from_quantiles,
    make_forecast,
    p_up_from_quantiles,
    pinball_loss,
    refit_anchor,
    slice_asof,
    sort_quantiles,
)
from engine.forecast.benchmarks import FuturesCurveForecaster, RandomWalkForecaster
from engine.forecast.ensemble import StackingEnsemble
from engine.forecast.garch import GarchForecaster
from engine.forecast.harrv import HarRvForecaster
from engine.forecast.implied import ovx_implied_range, ovx_implied_range_record
from engine.forecast.quantile_gbm import FEATURE_LABELS_IT, QuantileGbmForecaster, feature_label_it

N_ROWS = 3000
# asof inside the synthetic range (rows exist after it) so that future-append invariance is a real test
ASOF = datetime(2021, 6, 15, 18, 30, tzinfo=UTC)
LEVELS = list(QUANTILE_LEVELS)


# ---------------------------------------------------------------------------------------------------------------
# SYNTHETIC data (labelled; never real)
# ---------------------------------------------------------------------------------------------------------------
def make_synthetic_features(n: int = N_ROWS, seed: int = 7, start: str = "2010-01-04") -> pd.DataFrame:
    """SYNTHETIC daily feature frame: log-price random walk with GARCH(1,1)-like variance and t(5) shocks, plus a
    very weak pull toward 70 $ so the path stays in a realistic range. Columns use the catalog names."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    omega, alpha, beta = 2.5e-6, 0.08, 0.90  # unconditional daily var 1.25e-4 -> ~18% annual vol
    sig2 = np.empty(n)
    r = np.empty(n)
    lp = np.empty(n)
    sig2[0] = omega / (1 - alpha - beta)
    lp_prev = math.log(70.0)
    for t in range(n):
        if t > 0:
            sig2[t] = omega + alpha * r[t - 1] ** 2 + beta * sig2[t - 1]
        shock = rng.standard_t(5) * math.sqrt(3.0 / 5.0)  # unit-variance t(5)
        r[t] = math.sqrt(sig2[t]) * shock - 0.002 * (lp_prev - math.log(70.0))
        lp[t] = lp_prev + r[t]
        lp_prev = lp[t]
    px = np.exp(lp)
    df = pd.DataFrame(index=idx)
    df[cat.PX] = px
    df[cat.PX_FRONT] = px
    df[cat.RET_1] = r
    ret = pd.Series(r, index=idx)
    rv = ret.rolling(21).std() * math.sqrt(252)
    df[cat.RV_CC_21] = rv
    df[cat.RV_YZ_21] = rv * 1.02
    df[cat.OVX] = rv * 100.0 * 1.1  # synthetic "implied" vol in percent
    df[cat.GARCH_VOL] = np.nan  # not available in the synthetic frame (the model must cope)
    df[cat.RET_5] = ret.rolling(5).sum()
    df[cat.RET_21] = ret.rolling(21).sum()
    df[cat.TSMOM_21] = df[cat.RET_21] / rv
    df[cat.TSMOM_63] = ret.rolling(63).sum() / rv
    ema_fast = pd.Series(px, index=idx).ewm(span=20).mean()
    ema_slow = pd.Series(px, index=idx).ewm(span=100).mean()
    df[cat.EMA_FAST_SLOW] = (ema_fast - ema_slow) / px
    df[cat.ATR_14] = ret.abs().rolling(14).mean()
    df[cat.DONCHIAN_POS_20] = (pd.Series(px, index=idx) - pd.Series(px, index=idx).rolling(20).min()) / (
        pd.Series(px, index=idx).rolling(20).max() - pd.Series(px, index=idx).rolling(20).min()
    )
    slope = 0.01 + 0.005 * np.sin(np.arange(n) / 60.0)
    df[cat.SLOPE_M1_M2] = slope
    df[cat.SLOPE_M1_M6] = slope * 4.5
    df[cat.DXY_RET_21] = rng.standard_normal(n) * 0.01
    df[cat.VR_20] = 1.0 + rng.standard_normal(n) * 0.1
    df[cat.COT_MM_NET_BRENT_PCTL] = rng.uniform(0, 1, n)
    df[cat.CRUDE_STOCKS_VS_5Y] = rng.standard_normal(n) * 0.03
    df[cat.GEO_INDEX] = rng.uniform(0, 1, n)
    df[cat.DAYS_TO_EXPIRY] = (np.arange(n) % 21).astype(float)
    df[cat.MONTH] = idx.month.astype(float)
    df[cat.CURVE_APPROX] = 0.0
    df[cat.REGIME_LABEL] = "A"
    return df


def make_synthetic_intraday(
    features: pd.DataFrame, days: int = 600, seed: int = 3, bars_per_day: int = 22
) -> pd.DataFrame:
    """SYNTHETIC hourly bars (UTC index) consistent with the daily closes: a Brownian bridge inside each day."""
    rng = np.random.default_rng(seed)
    sub = features.iloc[-days:]
    prev = float(features[cat.PX].iloc[-days - 1])
    rows: list[tuple[pd.Timestamp, float]] = []
    for d, px in zip(sub.index, sub[cat.PX].to_numpy(dtype=float)):
        times = pd.date_range(d + pd.Timedelta(hours=1), periods=bars_per_day, freq="h", tz="Europe/London")
        total = math.log(px) - math.log(prev)
        noise = rng.standard_normal(bars_per_day) * 0.004
        noise -= noise.mean()
        path = math.log(prev) + np.cumsum(np.full(bars_per_day, total / bars_per_day) + noise)
        path[-1] = math.log(px)
        rows.extend(zip(times.tz_convert("UTC"), np.exp(path)))
        prev = px
    out = pd.DataFrame(rows, columns=["ts", "close"]).set_index("ts")
    out["open"] = out["close"].shift(1).fillna(out["close"])
    out["high"] = out[["open", "close"]].max(axis=1) * 1.0005
    out["low"] = out[["open", "close"]].min(axis=1) * 0.9995
    return out


def make_curve(price: float, asof: datetime, slope_per_month: float = 0.01, n: int = 12) -> pd.Series:
    """SYNTHETIC backwardated curve: M_k = price * (1 - slope*(k-1)), with the real front code for the date."""
    y, m = front_month("BZ", asof_date(asof).date())
    s = pd.Series({f"M{k}": price * (1 - slope_per_month * (k - 1)) for k in range(1, n + 1)}, dtype=object)
    s["M1_code"] = contract_code("BZ", y, m)
    return s


# ---------------------------------------------------------------------------------------------------------------
# fixtures (module scope: the expensive fits run once)
# ---------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    df = make_synthetic_features()
    df.attrs["intraday"] = make_synthetic_intraday(df)
    return df


@pytest.fixture(scope="module")
def pit(features: pd.DataFrame) -> pd.DataFrame:
    p = slice_asof(features, ASOF).copy()
    p.attrs["intraday"] = features.attrs["intraday"]
    assert len(p) < len(features), "ASOF must leave future rows in the full frame"
    return p


@pytest.fixture(scope="module")
def price(pit: pd.DataFrame) -> float:
    return float(pit[cat.PX_FRONT].iloc[-1])


@pytest.fixture(scope="module")
def curve(price: float) -> pd.Series:
    return make_curve(price, ASOF)


FACTORIES: dict[str, Any] = {
    "random_walk": RandomWalkForecaster,
    "futures_curve": FuturesCurveForecaster,
    "arima_ets": ArimaEtsForecaster,
    "garch": GarchForecaster,
    "har_rv": HarRvForecaster,
    "lgbm_quantile": QuantileGbmForecaster,
}


def make_ensemble() -> StackingEnsemble:
    return StackingEnsemble([f() for f in FACTORIES.values()], regime_aware=True)


@pytest.fixture(scope="module")
def predictions(features: pd.DataFrame, price: float, curve: pd.Series) -> dict[str, dict[str, ForecastQuantiles]]:
    out: dict[str, dict[str, ForecastQuantiles]] = {}
    for name, factory in FACTORIES.items():
        out[name] = factory().predict(features, ASOF, price, curve)
    out["ensemble"] = make_ensemble().predict(features, ASOF, price, curve)
    return out


# ---------------------------------------------------------------------------------------------------------------
# contract and helpers
# ---------------------------------------------------------------------------------------------------------------
def test_horizons_contract() -> None:
    assert HORIZONS == {"h1d": 1, "h1w": 5, "h1m": 21, "h3m": 63}


def test_record_roundtrip() -> None:
    fq = make_forecast("h1w", ASOF, 100.0, {0.05: 90, 0.25: 96, 0.5: 100, 0.75: 104, 0.95: 110}, 0.5, 0.3, ["a"], "m")
    d = fq.to_dict()
    assert d["asof"] == "2021-06-15T18:30:00Z"
    back = ForecastQuantiles.from_dict(d)
    assert back == fq
    assert back.horizon_days == 5
    assert back.quantiles()[0.95] == 110.0


def test_make_forecast_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        make_forecast("h1w", ASOF, -1.0, {0.05: 90, 0.25: 96, 0.5: 100, 0.75: 104, 0.95: 110}, 0.5, 0.3, [], "m")
    with pytest.raises(ValueError):
        make_forecast("h9", ASOF, 100.0, {0.05: 90, 0.25: 96, 0.5: 100, 0.75: 104, 0.95: 110}, 0.5, 0.3, [], "m")


def test_sort_quantiles_fixes_crossing() -> None:
    q = sort_quantiles({0.05: 101.0, 0.25: 99.0, 0.5: 100.0, 0.75: 100.0, 0.95: 105.0}, scale=100.0)
    vals = [q[a] for a in LEVELS]
    assert all(a < b for a, b in zip(vals, vals[1:]))
    assert q[0.05] == 99.0 and q[0.95] == 105.0


def test_cdf_and_p_up_from_quantiles() -> None:
    qs = {0.05: 90.0, 0.25: 96.0, 0.5: 100.0, 0.75: 104.0, 0.95: 110.0}
    assert cdf_from_quantiles(qs, 100.0) == pytest.approx(0.5)
    assert cdf_from_quantiles(qs, 96.0) == pytest.approx(0.25)
    assert cdf_from_quantiles(qs, 98.0) == pytest.approx(0.375)
    assert cdf_from_quantiles(qs, 90.0) == pytest.approx(0.05)
    assert 0.0 < cdf_from_quantiles(qs, 80.0) < 0.05
    assert 0.95 < cdf_from_quantiles(qs, 120.0) < 1.0
    xs = np.linspace(70, 130, 200)
    cdf = [cdf_from_quantiles(qs, float(x)) for x in xs]
    assert all(a <= b + 1e-12 for a, b in zip(cdf, cdf[1:]))
    assert p_up_from_quantiles(qs, 100.0) == pytest.approx(0.5)
    assert p_up_from_quantiles(qs, 96.0) == pytest.approx(0.75)


def test_pinball_loss_by_hand() -> None:
    qs = {0.25: 100.0, 0.75: 110.0}
    # realized 104: u1 = +4 -> 4*0.25 = 1.0 ; u2 = -6 -> -6*(0.75-1) = 1.5 ; mean = 1.25
    assert pinball_loss(qs, 104.0) == pytest.approx(1.25)
    # a better-centred forecast scores lower
    assert pinball_loss({0.25: 103.0, 0.75: 105.0}, 104.0) < pinball_loss(qs, 104.0)


def test_refit_anchor_monthly_and_quarterly(features: pd.DataFrame) -> None:
    assert refit_anchor(features.index, ASOF, "M") == pd.Timestamp("2021-06-01")
    assert refit_anchor(features.index, ASOF, "Q") == pd.Timestamp("2021-04-01")
    # appending future rows never moves the anchor
    assert refit_anchor(slice_asof(features, ASOF).index, ASOF, "M") == refit_anchor(features.index, ASOF, "M")
    with pytest.raises(NotFitted):
        refit_anchor(features.index, datetime(2000, 1, 1, tzinfo=UTC), "M")


def test_slice_asof_is_inclusive_of_the_asof_date(features: pd.DataFrame) -> None:
    p = slice_asof(features, ASOF)
    assert p.index[-1] == pd.Timestamp("2021-06-15")
    assert (p.index <= pd.Timestamp("2021-06-15")).all()


# ---------------------------------------------------------------------------------------------------------------
# every model: shape of the output
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("model", [*FACTORIES.keys(), "ensemble"])
def test_quantiles_are_monotone_and_around_price(
    predictions: dict[str, dict[str, ForecastQuantiles]], price: float, model: str
) -> None:
    out = predictions[model]
    assert set(out) == set(HORIZONS)
    prev_width = 0.0
    for hname in HORIZONS:
        fq = out[hname]
        fq.validate()
        assert fq.model == model
        assert fq.horizon == hname
        assert fq.price_now == price
        assert fq.q05 < fq.q25 < fq.median < fq.q75 < fq.q95, (model, hname)
        assert 0.0 < fq.p_up < 1.0
        assert fq.expected_vol > 0.0
        assert fq.q05 < price < fq.q95, (model, hname)
        assert 0.7 < fq.median / price < 1.3, (model, hname)
        assert 1 <= len(fq.drivers) <= 4 and all(isinstance(d, str) and d for d in fq.drivers)
        width = math.log(fq.q95 / fq.q05)
        assert width > prev_width, (model, hname)  # bands widen with the horizon
        prev_width = width
        # JSON-able
        d = fq.to_dict()
        assert isinstance(d["meta"], dict) and isinstance(d["asof"], str)


def test_random_walk_median_is_price(predictions: dict[str, dict[str, ForecastQuantiles]], price: float) -> None:
    out = predictions["random_walk"]
    for fq in out.values():
        assert fq.median == price
        assert fq.p_up == 0.5
        assert not fq.approx
    # sqrt-of-time scaling of the band and Student-t(4) unit-variance quantile
    w1, w63 = math.log(out["h1d"].q95 / price), math.log(out["h3m"].q95 / price)
    assert w63 / w1 == pytest.approx(math.sqrt(63), rel=1e-9)
    vol = out["h1d"].expected_vol
    z95 = stats.t.ppf(0.95, 4) * math.sqrt(2.0 / 4.0)
    assert w1 == pytest.approx(vol * math.sqrt(1 / 252) * z95, rel=1e-9)
    # conservative vol = max(RV_YZ_21, OVX/100) on the synthetic frame (OVX is the largest by construction)
    assert out["h1d"].meta["vol_source"] == "OVX"


def test_curve_forecaster_uses_the_contract_at_the_horizon(
    predictions: dict[str, dict[str, ForecastQuantiles]], price: float, curve: pd.Series
) -> None:
    out = predictions["futures_curve"]
    m1 = float(curve["M1"])
    # 1 day ahead: still the front contract (M1 == price here)
    assert out["h1d"].median == pytest.approx(m1)
    # 3 months ahead in a backwardated curve: median below the front and inside the curve's range
    h3 = out["h3m"]
    assert h3.median < m1
    assert float(curve["M6"]) <= h3.median <= m1
    assert h3.meta["nearest_contract"] != curve["M1_code"]
    assert h3.p_up < 0.5  # the market expects a lower front price
    assert not h3.approx
    assert "backwardation" in h3.drivers[0]
    # reproduce the interpolation by hand from the calendar: target = asof + 63 ICE business days
    d0 = asof_date(ASOF).date()
    target = add_business_days(d0, 63, "ICE")
    x = (target - d0).days
    y, m = front_month("BZ", d0)
    xs, ys = [], []
    for k in range(1, 13):
        xs.append((expiry_for("BZ", y, m) - d0).days)
        ys.append(float(curve[f"M{k}"]))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    expected = float(np.interp(x, xs, ys))
    assert h3.median == pytest.approx(expected, rel=1e-12)
    assert h3.meta["target_date"] == target.isoformat()


def test_curve_forecaster_fallback_and_proxy_flag(pit: pd.DataFrame, price: float, curve: pd.Series) -> None:
    fc = FuturesCurveForecaster()
    rw = RandomWalkForecaster().predict(pit, ASOF, price)
    # no curve: random walk, approx, says so
    out = fc.predict(pit, ASOF, price, None)
    for hname, fq in out.items():
        assert fq.approx is True
        assert fq.model == "futures_curve"
        assert fq.median == price
        assert "random walk" in fq.drivers[0].lower()
        assert fq.q95 == rw[hname].q95
    # a single valid point is not a curve either
    out = fc.predict(pit, ASOF, price, pd.Series({"M1": price, "M2": float("nan")}))
    assert all(fq.approx for fq in out.values())
    # WTI proxy curve flagged by the feature frame
    proxy = pit.copy()
    proxy[cat.CURVE_APPROX] = 1.0
    out = fc.predict(proxy, ASOF, price, curve)
    assert all(fq.approx for fq in out.values())
    assert any("proxy" in d.lower() for d in out["h1m"].drivers)
    # ... or by the curve attrs
    c2 = curve.copy()
    c2.attrs["approx"] = True
    assert all(fq.approx for fq in fc.predict(pit, ASOF, price, c2).values())


# ---------------------------------------------------------------------------------------------------------------
# ARIMA/ETS
# ---------------------------------------------------------------------------------------------------------------
def test_arima_ets_structure(predictions: dict[str, dict[str, ForecastQuantiles]], price: float) -> None:
    out = predictions["arima_ets"]
    fq = out["h1m"]
    p, d, q = fq.meta["order"]
    assert d == 1 and p in (0, 1, 2) and q in (0, 1, 2)
    assert fq.meta["widen"] == 1.2
    assert fq.meta["anchor"] == "2021-06-01"
    assert fq.meta["ets_used"] is True
    assert abs(fq.median / price - 1) < 0.05
    # Gaussian band consistent with sd_h and the 1.2 widening already applied
    assert math.log(fq.q95 / fq.median) == pytest.approx(fq.meta["sd_h"] * stats.norm.ppf(0.95), rel=1e-9)
    # p_up consistent with the mixture mean and sd
    mu = 0.5 * (fq.meta["mu_arima"] + fq.meta["mu_ets"])
    assert fq.p_up == pytest.approx(stats.norm.cdf(mu / fq.meta["sd_h"]), rel=1e-9)


def test_arima_requires_history() -> None:
    short = make_synthetic_features(n=100, seed=1)
    with pytest.raises(NotFitted):
        ArimaEtsForecaster().predict(short, short.index[-1].to_pydatetime().replace(tzinfo=UTC), 70.0)


# ---------------------------------------------------------------------------------------------------------------
# GARCH
# ---------------------------------------------------------------------------------------------------------------
def test_garch_vol_increases_after_a_volatility_burst(features: pd.DataFrame, price: float) -> None:
    calm = slice_asof(features, ASOF).copy()
    burst = calm.copy()
    # plant a burst in the last 8 returns (all AFTER the June-1 refit anchor, so the fitted params are identical)
    r = burst[cat.RET_1].to_numpy(dtype=float).copy()
    r[-8:] = r[-8:] * 5.0 + np.array([0.04, -0.045, 0.05, -0.04, 0.045, -0.05, 0.04, -0.045])
    burst[cat.RET_1] = r
    lp = math.log(float(burst[cat.PX].iloc[0])) + np.cumsum(r)
    burst[cat.PX] = np.exp(lp)
    burst[cat.PX_FRONT] = burst[cat.PX]
    g_calm, g_burst = GarchForecaster(), GarchForecaster()
    out_calm = g_calm.predict(calm, ASOF, price)
    out_burst = g_burst.predict(burst, ASOF, float(burst[cat.PX_FRONT].iloc[-1]))
    assert out_calm["h1d"].meta["anchor"] == out_burst["h1d"].meta["anchor"] == "2021-06-01"
    for hname in ("h1d", "h1w", "h1m"):
        assert out_burst[hname].expected_vol > 1.5 * out_calm[hname].expected_vol, hname
    # no drift: p_up close to one half
    for fq in out_calm.values():
        assert 0.44 < fq.p_up < 0.56
        assert abs(fq.meta["sim_mean_return"]) < 0.02
    assert set(out_calm["h1d"].meta["specs"]) == {"GARCH(1,1)", "GJR-GARCH(1,1,1)", "EGARCH(1,1)"}
    assert out_calm["h1d"].meta["n_sims_total"] == 6000
    # vol path for the risk module: h values, positive, consistent with expected_vol
    path = g_calm.forecast_vol_path(5)
    assert path.shape == (5,) and (path > 0).all()
    assert math.sqrt(float(np.mean(path**2))) == pytest.approx(out_calm["h1w"].expected_vol, rel=1e-9)
    path_burst = g_burst.forecast_vol_path(21)
    assert path_burst[0] > path[0]
    with pytest.raises(ValueError):
        g_calm.forecast_vol_path(0)
    with pytest.raises(NotFitted):
        GarchForecaster().forecast_vol_path(5)


# ---------------------------------------------------------------------------------------------------------------
# HAR-RV
# ---------------------------------------------------------------------------------------------------------------
def test_har_rv_requires_intraday(pit: pd.DataFrame, price: float) -> None:
    bare = pit.copy()
    bare.attrs.clear()
    with pytest.raises(NotFitted):
        HarRvForecaster().fit(bare, ASOF)
    with pytest.raises(NotFitted):
        HarRvForecaster().predict(bare, ASOF, price)
    # too few days of bars is not enough either
    few = HarRvForecaster(intraday=pit.attrs["intraday"].iloc[-22 * 30 :])
    with pytest.raises(NotFitted):
        few.predict(bare, ASOF, price)


def test_har_rv_output(predictions: dict[str, dict[str, ForecastQuantiles]], pit: pd.DataFrame, price: float) -> None:
    out = predictions["har_rv"]
    fq = out["h1m"]
    assert fq.p_up == 0.5 and fq.median == price
    assert fq.meta["n_days"] >= 120
    assert fq.meta["n_obs_fit"] >= 50
    assert len(fq.meta["beta"]) == 4
    # HAR vol in the same ballpark as the daily close-to-close vol of the synthetic series
    rv_cc = float(pit[cat.RV_CC_21].iloc[-1])
    assert 0.4 * rv_cc < fq.expected_vol < 2.5 * rv_cc
    # explicit constructor argument works the same as features.attrs
    direct = HarRvForecaster(intraday=pit.attrs["intraday"])
    bare = pit.copy()
    bare.attrs.clear()
    out2 = direct.predict(bare, ASOF, price)
    assert out2["h1m"].expected_vol == pytest.approx(fq.expected_vol, rel=1e-12)
    # the last (settlement-time) day of bars is complete in the synthetic set: no scaling applied
    assert fq.meta["last_day_scaled"] is False


def test_har_rv_daily_rv_handles_partial_last_day(pit: pd.DataFrame) -> None:
    bars: pd.DataFrame = pit.attrs["intraday"]
    har = HarRvForecaster()
    full, scaled_full = har.daily_rv(bars, ASOF)
    # cut the last day at 14:00 UTC: ~14 of 22 bars -> kept and scaled up
    cut = datetime(2021, 6, 15, 14, 0, tzinfo=UTC)
    partial, scaled = har.daily_rv(bars, cut)
    assert scaled is True and scaled_full is False
    assert partial.index[-1] == full.index[-1]
    assert len(partial) == len(full)
    # cutting at 03:00 UTC leaves too few bars: the day is dropped
    early, _ = har.daily_rv(bars, datetime(2021, 6, 15, 3, 0, tzinfo=UTC))
    assert early.index[-1] < full.index[-1]


# ---------------------------------------------------------------------------------------------------------------
# LightGBM quantile
# ---------------------------------------------------------------------------------------------------------------
def test_lightgbm_runtime_on_5000_rows_and_embargo() -> None:
    df = make_synthetic_features(n=5000, seed=11, start="2005-01-03")
    asof = df.index[-1].to_pydatetime().replace(tzinfo=UTC)
    price = float(df[cat.PX_FRONT].iloc[-1])
    model = QuantileGbmForecaster()
    t0 = time.perf_counter()
    out = model.predict(df, asof, price)
    elapsed = time.perf_counter() - t0
    assert elapsed < 60.0, f"single predict took {elapsed:.1f}s"
    anchor = refit_anchor(df.index, asof, "Q")
    n_anchor = int((df.index <= anchor).sum())
    for hname, h in HORIZONS.items():
        fq = out[hname]
        fq.validate()
        assert fq.meta["anchor"] == anchor.date().isoformat()
        assert fq.meta["n_train"] == n_anchor - h  # embargo: the last h rows at the anchor have no label yet
        assert fq.meta["n_train"] >= 1500
        assert len(fq.drivers) == 4
        # the first three drivers are Italian labels of catalog features
        for drv, feat in zip(fq.drivers[:3], fq.meta["top_features"]):
            assert drv.startswith(feature_label_it(feat))
            assert feat not in (cat.PX, cat.PX_FRONT)
            assert feat in FEATURE_LABELS_IT  # synthetic columns are all catalog names
        assert "Rendimento mediano" in fq.drivers[3]
    # second predict at the same anchor reuses the cached boosters
    t0 = time.perf_counter()
    model.predict(df, asof, price)
    assert time.perf_counter() - t0 < 2.0


def test_lightgbm_needs_min_training_rows() -> None:
    df = make_synthetic_features(n=1200, seed=5)
    asof = df.index[-1].to_pydatetime().replace(tzinfo=UTC)
    with pytest.raises(NotFitted):
        QuantileGbmForecaster().predict(df, asof, float(df[cat.PX_FRONT].iloc[-1]))
    # relaxing the threshold makes it fit
    out = QuantileGbmForecaster(min_train_rows=500, num_boost_round=20).predict(
        df, asof, float(df[cat.PX_FRONT].iloc[-1])
    )
    assert out["h1d"].q05 < out["h1d"].q95


def test_feature_label_it_mapping() -> None:
    assert feature_label_it(cat.RV_YZ_21) == "Vol realizzata 21g"
    assert feature_label_it("regime_p_2") == "Prob. regime 2"
    assert feature_label_it("unknown_col") == "unknown_col"
    assert all(isinstance(v, str) and v for v in FEATURE_LABELS_IT.values())


# ---------------------------------------------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------------------------------------------
class _PlantedForecaster:
    """TEST DOUBLE (oracle): centres its quantiles on the realised future price (plus a bias) to plant a known
    ranking of pinball losses. Only for exercising the ensemble's scoring. Never a real model."""

    def __init__(self, name: str, future: pd.Series, bias: float, width: float):
        self.name = name
        self.future = future
        self.bias = bias
        self.width = width

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        return None

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        pos = int(self.future.index.searchsorted(asof_date(asof), side="right"))
        out: dict[str, ForecastQuantiles] = {}
        for hname, h in HORIZONS.items():
            target = float(self.future.iloc[min(pos + h - 1, len(self.future) - 1)])
            center = math.log(target * (1 + self.bias))
            sd = self.width * math.sqrt(h / 252)
            qs = {a: math.exp(center + sd * stats.norm.ppf(a)) for a in LEVELS}
            p_up = 1 - stats.norm.cdf((math.log(price) - center) / sd)
            out[hname] = make_forecast(
                hname, asof, price, qs, p_up, self.width, [f"{self.name} d1", f"{self.name} d2"], self.name
            )
        return out


def _ts(d: pd.Timestamp) -> datetime:
    return d.to_pydatetime().replace(hour=18, minute=30, tzinfo=UTC)


def test_ensemble_weights_sum_to_one_and_favour_lower_pinball_loss(features: pd.DataFrame) -> None:
    realized = features[cat.PX_FRONT]
    good = _PlantedForecaster("good", realized, bias=0.0, width=0.10)
    bad = _PlantedForecaster("bad", realized, bias=0.08, width=0.10)
    ens = StackingEnsemble([good, bad], regime_aware=True, shrink=0.5)
    dates = features.index[-260:-80:2]  # 90 decision dates, all with a realised 63-day outcome in the frame
    for d in dates:
        out = ens.predict(features, _ts(d), float(realized.loc[d]))
        assert out["h1m"].meta["weight_table"] == "equal"  # nothing scored yet
        for fq in out.values():
            assert sum(fq.meta["weights"].values()) == pytest.approx(1.0)
    assert ens.pending_count == len(dates) * len(HORIZONS) * 3  # good, bad and the ensemble itself
    resolved = ens.update(realized)
    assert resolved == ens.score_table().shape[0] == len(dates) * len(HORIZONS) * 3
    assert ens.pending_count == 0
    table = ens.score_table()
    assert (table.groupby("model")["loss"].mean()["good"] < table.groupby("model")["loss"].mean()["bad"]).all()
    last = features.index[-70]
    out = ens.predict(features, _ts(last), float(realized.loc[last]))
    for hname, fq in out.items():
        w = fq.meta["weights"]
        assert set(w) == {"good", "bad"}
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-12)
        assert w["good"] > w["bad"], hname
        assert w["bad"] >= 0.5 / 2 - 1e-12  # shrinkage toward equal weights with lambda 0.5
        assert w["good"] <= 0.5 + 0.5 * 1.0
        assert fq.meta["weight_table"] == "regime:A"  # >= 30 scored dates in the (constant) synthetic regime
        assert fq.meta["n_scores"] >= 30
        assert fq.model == "ensemble"
        assert fq.drivers == ["good d1", "bad d1", "good d2", "bad d2"]  # union of the two best models' drivers
        assert not fq.approx
    # the ensemble median sits between the components, closer to the better one
    comp = out["h1m"].meta["components"]
    assert (
        min(comp["good"]["median"], comp["bad"]["median"])
        <= out["h1m"].median
        <= max(comp["good"]["median"], comp["bad"]["median"])
    )
    assert abs(out["h1m"].median - comp["good"]["median"]) < abs(out["h1m"].median - comp["bad"]["median"])
    # point-in-time: at the first decision date no score has a realised target yet -> equal weights
    first = ens.predict(features, _ts(dates[0]), float(realized.loc[dates[0]]))
    assert first["h1m"].meta["weight_table"] == "equal"
    # regime fallback: an unseen label has < 30 observations -> global table
    other = features.copy()
    other.loc[other.index >= last, cat.REGIME_LABEL] = "B"
    out_b = ens.predict(other, _ts(last), float(realized.loc[last]))
    assert out_b["h1m"].meta["weight_table"] == "global"
    assert out_b["h1m"].meta["weights"]["good"] > out_b["h1m"].meta["weights"]["bad"]
    # regime_aware=False never uses regime tables
    plain = StackingEnsemble([good, bad], regime_aware=False)
    plain.from_state(ens.to_state())
    assert plain.predict(features, _ts(last), float(realized.loc[last]))["h1w"].meta["weight_table"] == "global"


def test_ensemble_state_roundtrip_and_idempotent_recording(features: pd.DataFrame) -> None:
    realized = features[cat.PX_FRONT]
    ens = StackingEnsemble(
        [_PlantedForecaster("good", realized, 0.0, 0.1), _PlantedForecaster("bad", realized, 0.08, 0.1)]
    )
    d = features.index[-200]
    ens.predict(features, _ts(d), float(realized.loc[d]))
    ens.predict(features, _ts(d), float(realized.loc[d]))  # re-running the same decision does not double count
    assert ens.pending_count == 3 * len(HORIZONS)
    state = ens.to_state()
    clone = StackingEnsemble(
        [_PlantedForecaster("good", realized, 0.0, 0.1), _PlantedForecaster("bad", realized, 0.08, 0.1)]
    )
    clone.from_state(state)
    assert clone.pending_count == ens.pending_count
    assert clone.update(realized) == ens.update(realized) == 3 * len(HORIZONS)
    pd.testing.assert_frame_equal(clone.score_table(), ens.score_table())
    state2 = clone.to_state()
    assert state2["pending"] == [] and len(state2["scores"]) == 3 * len(HORIZONS)
    # update with nothing new is a no-op
    assert clone.update(realized) == 0


def test_ensemble_skips_models_that_cannot_forecast(pit: pd.DataFrame, price: float) -> None:
    bare = pit.copy()
    bare.attrs.clear()
    ens = StackingEnsemble([RandomWalkForecaster(), HarRvForecaster()])
    out = ens.predict(bare, ASOF, price)
    fq = out["h1w"]
    assert set(fq.meta["weights"]) == {"random_walk"}
    assert "har_rv" in fq.meta["skipped"] and "intraday" in fq.meta["skipped"]["har_rv"]
    assert fq.median == price
    # nothing available at all -> NotFitted, never a made-up forecast
    with pytest.raises(NotFitted):
        StackingEnsemble([HarRvForecaster()]).predict(bare, ASOF, price)


def test_ensemble_with_real_models_reports_components(
    predictions: dict[str, dict[str, ForecastQuantiles]], price: float
) -> None:
    fq = predictions["ensemble"]["h1m"]
    assert set(fq.meta["weights"]) == set(FACTORIES)
    assert fq.meta["skipped"] == {}
    assert fq.meta["weight_table"] == "equal"
    assert sum(fq.meta["weights"].values()) == pytest.approx(1.0)
    lo = min(c["median"] for c in fq.meta["components"].values())
    hi = max(c["median"] for c in fq.meta["components"].values())
    assert lo <= fq.median <= hi


# ---------------------------------------------------------------------------------------------------------------
# point-in-time: appending future rows must not change a forecast at a fixed asof
# ---------------------------------------------------------------------------------------------------------------
def _as_floats(fq: ForecastQuantiles) -> dict[str, float]:
    return {k: getattr(fq, k) for k in ("median", "q05", "q25", "q75", "q95", "p_up", "expected_vol")}


@pytest.mark.parametrize("model", [*FACTORIES.keys(), "ensemble"])
def test_future_append_invariance(
    features: pd.DataFrame, pit: pd.DataFrame, price: float, curve: pd.Series, model: str
) -> None:
    factory = make_ensemble if model == "ensemble" else FACTORIES[model]
    out_full = factory().predict(features, ASOF, price, curve)  # frame WITH rows (and bars) after ASOF
    m2 = factory()
    m2.fit(pit, ASOF)  # explicit fit on the truncated frame, then predict
    out_pit = m2.predict(pit, ASOF, price, curve)
    for hname in HORIZONS:
        a, b = out_full[hname], out_pit[hname]
        assert _as_floats(a) == pytest.approx(_as_floats(b), rel=1e-9), (model, hname)
        assert a.drivers == b.drivers
        assert a.approx == b.approx
    # the record has the right asof and the model never read rows beyond it
    assert out_full["h1d"].asof == ASOF


def test_predict_before_data_starts_raises(features: pd.DataFrame, price: float) -> None:
    early = datetime(2009, 1, 5, tzinfo=UTC)
    for factory in FACTORIES.values():
        with pytest.raises(NotFitted):
            factory().predict(features, early, price)


# ---------------------------------------------------------------------------------------------------------------
# OVX implied range
# ---------------------------------------------------------------------------------------------------------------
def test_ovx_implied_range_arithmetic_by_hand() -> None:
    # width = 1.0 * 0.50 * sqrt(21/252) = 0.5 * 0.2886751 = 0.1443376
    low, high = ovx_implied_range(100.0, 50.0, days=21, z=1.0)
    assert low == pytest.approx(100.0 * math.exp(-0.1443376), rel=1e-6)
    assert high == pytest.approx(100.0 * math.exp(0.1443376), rel=1e-6)
    assert low == pytest.approx(86.5596, abs=1e-3)
    assert high == pytest.approx(115.5274, abs=1e-3)
    assert low * high == pytest.approx(100.0**2)  # symmetric in log space
    # z and days scale the width as expected
    l2, h2 = ovx_implied_range(100.0, 50.0, days=84, z=1.0)
    assert math.log(h2 / 100.0) == pytest.approx(2 * math.log(high / 100.0))
    l3, h3 = ovx_implied_range(100.0, 50.0, days=21, z=1.645)
    assert math.log(h3 / 100.0) == pytest.approx(1.645 * math.log(high / 100.0))
    assert ovx_implied_range(100.0, 0.0) == (100.0, 100.0)
    with pytest.raises(ValueError):
        ovx_implied_range(0.0, 50.0)
    with pytest.raises(ValueError):
        ovx_implied_range(100.0, -1.0)


def test_ovx_implied_range_record_is_labelled_approx() -> None:
    rec = ovx_implied_range_record(100.0, 50.0, asof="2026-09-29")
    assert rec["approx"] is True
    assert rec["low"] == pytest.approx(86.5596, abs=1e-3)
    assert "USO" in rec["note"] and "WTI" in rec["note"]
    missing = ovx_implied_range_record(100.0, None)
    assert missing["low"] is None and missing["high"] is None and missing["approx"] is True
