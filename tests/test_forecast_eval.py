"""Forecast evaluation, archive and walk-forward tests. Offline and deterministic.

All price series here are SYNTHETIC (seeded random walks or hand-written numbers) and exist only to exercise the
metric arithmetic, the archive logic and the point-in-time discipline. They are never shipped as real data.
"""

from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from engine.core.calendar import add_business_days, is_business_day
from engine.core.timeutil import settlement_ts
from engine.features import catalog
from engine.forecast.archive import FORECASTS_LOG, OUTCOMES_LOG, ForecastArchive, forecast_key
from engine.forecast.base import HORIZONS, ForecastQuantiles
from engine.forecast.evaluation import (
    VERDICT_BEATS_RW,
    VERDICT_INSUFFICIENT,
    VERDICT_NOT_BEATS_RW,
    align_forecasts,
    as_trading_date,
    beats_random_walk,
    coverage,
    crps_from_quantiles,
    diebold_mariano,
    directional_accuracy,
    evaluate,
    newey_west_variance,
    pinball_loss,
    summary_table,
    target_trading_date,
    theil_u,
)
from engine.forecast.walkforward import (
    CurveBenchmark,
    RandomWalkBenchmark,
    curve_price_at,
    run_walkforward,
    should_refit,
)

# --------------------------------------------------------------------------------------------------------------
# synthetic fixtures (labelled synthetic; deterministic)
# --------------------------------------------------------------------------------------------------------------


def ice_days(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if is_business_day(d, "ICE"):
            out.append(d)
        d = add_business_days(d, 1, "ICE")
    return out


def synthetic_prices(n: int = 320, start: date = date(2026, 1, 5), seed: int = 7) -> pd.Series:
    """SYNTHETIC seeded lognormal random walk around 100 $/bbl on ICE business days. Not market data."""
    rng = np.random.default_rng(seed)
    days = ice_days(start, n)
    rets = rng.normal(0.0, 0.02, size=n)
    px = 100.0 * np.exp(np.cumsum(rets))
    return pd.Series(px, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]), name="synthetic_front")


def synthetic_features(prices: pd.Series) -> pd.DataFrame:
    """Minimal catalog-shaped frame: PX_FRONT, PX and a noise feature. SYNTHETIC."""
    rng = np.random.default_rng(11)
    df = pd.DataFrame(index=prices.index)
    df[catalog.PX_FRONT] = prices.to_numpy()
    df[catalog.PX] = prices.to_numpy()
    df[catalog.RET_1] = np.log(prices).diff().to_numpy()
    df["noise"] = rng.normal(size=len(prices))
    return df


def synthetic_curve(prices: pd.Series, slope: float = -0.01) -> pd.DataFrame:
    """SYNTHETIC backwardated curve: M_k = M1 * (1 + slope)^(k-1)."""
    cv = pd.DataFrame(index=prices.index)
    for k in range(1, 7):
        cv[f"M{k}"] = prices.to_numpy() * (1 + slope) ** (k - 1)
    cv["M1_code"] = "SYN"
    return cv


def fq(
    model: str,
    horizon: str,
    asof: datetime,
    price: float,
    median: float,
    width: float = 0.05,
    p_up: float | None = None,
    approx: bool = False,
) -> ForecastQuantiles:
    return ForecastQuantiles(
        horizon=horizon,
        asof=asof,
        price_now=price,
        median=median,
        q05=median * (1 - 2 * width),
        q25=median * (1 - width),
        q75=median * (1 + width),
        q95=median * (1 + 2 * width),
        p_up=p_up if p_up is not None else (0.5 if median == price else (0.7 if median > price else 0.3)),
        expected_vol=0.3,
        drivers=["test sintetico"],
        model=model,
        approx=approx,
    )


# --------------------------------------------------------------------------------------------------------------
# pure metrics: hand-computed values
# --------------------------------------------------------------------------------------------------------------


def test_theil_u_equals_one_when_forecast_is_naive():
    a = np.array([10.0, 11.0, 9.0, 12.0])
    naive = np.array([9.5, 10.0, 11.0, 9.0])
    assert theil_u(naive, a, naive) == pytest.approx(1.0)
    assert theil_u(a, a, naive) == 0.0
    # hand: errors f-a = [1, 2], naive-a = [2, 4] -> sqrt(5)/sqrt(20) = 0.5
    assert theil_u([11, 12], [10, 10], [12, 14]) == pytest.approx(0.5)
    # scaled form divides both error series by the same scale -> unchanged when scale is constant
    assert theil_u([11, 12], [10, 10], [12, 14], scale=[10, 10]) == pytest.approx(0.5)
    assert math.isnan(theil_u([1, 2], [1, 2], [1, 2]))  # naive error identically zero


def test_newey_west_variance_hand():
    d = np.array([1.0, -1.0, 2.0, 0.0, -2.0, 1.0])
    n = len(d)
    xc = d - d.mean()
    gammas = [float(np.dot(xc[k:], xc[: n - k]) / n) if k else float(np.dot(xc, xc) / n) for k in range(3)]
    lag = 2
    expected = (gammas[0] + 2 * sum((1 - k / (lag + 1)) * gammas[k] for k in (1, 2))) / n
    assert newey_west_variance(d, lag) == pytest.approx(expected)
    assert newey_west_variance(d, 0) == pytest.approx(gammas[0] / n)


def test_dm_identical_losses_p_one():
    loss = np.array([0.1, 0.3, 0.2, 0.5, 0.4])
    stat, p = diebold_mariano(loss, loss, 1)
    assert stat == 0.0 and p == 1.0
    stat, p = diebold_mariano(loss, loss, 5, alternative="less")
    assert stat == 0.0 and p == 1.0


def test_dm_hand_computed_asymptotic_and_hln():
    # d = a - b = [-2, 0, -1, 1, -3]; mean -1; deviations [-1, 1, 0, 2, -2]; gamma0 = 10/5 = 2
    # var(mean) = 2/5 = 0.4; DM = -1/sqrt(0.4) = -1.58114
    loss_b = np.array([3.0] * 5)
    loss_a = loss_b + np.array([-2.0, 0.0, -1.0, 1.0, -3.0])
    stat, p2 = diebold_mariano(loss_a, loss_b, 1, hln=False)
    assert stat == pytest.approx(-1.5811388, abs=1e-6)
    assert p2 == pytest.approx(2 * (1 - 0.943070), abs=5e-4)  # Phi(1.5811) = 0.94307 -> p = 0.1139
    _, p_less = diebold_mariano(loss_a, loss_b, 1, hln=False, alternative="less")
    _, p_greater = diebold_mariano(loss_a, loss_b, 1, hln=False, alternative="greater")
    assert p_less == pytest.approx(p2 / 2, abs=1e-9)
    assert p_greater == pytest.approx(1 - p2 / 2, abs=1e-9)
    # HLN: factor sqrt((n+1-2h+h(h-1)/n)/n) = sqrt(4/5); Student-t(n-1 = 4)
    stat_h, p_h = diebold_mariano(loss_a, loss_b, 1, hln=True)
    assert stat_h == pytest.approx(-1.5811388 * math.sqrt(0.8), abs=1e-6)
    assert p_h == pytest.approx(2 * stats.t.sf(1.5811388 * math.sqrt(0.8), 4), abs=1e-6)
    assert p_h > p2  # the small-sample correction is more conservative


def test_dm_degenerate_and_short_inputs():
    a = np.array([1.0, 2.0, 3.0])
    stat, p = diebold_mariano(a - 1.0, a, 1, alternative="less")  # constant difference, zero variance
    assert stat == -math.inf and p == 0.0
    _, p = diebold_mariano(a - 1.0, a, 1, alternative="greater")
    assert p == 1.0
    stat, p = diebold_mariano([1.0], [2.0], 1)
    assert math.isnan(stat) and math.isnan(p)
    with pytest.raises(ValueError):
        diebold_mariano(a, a, 1, alternative="bogus")


def test_directional_accuracy_60_of_100():
    pred = np.ones(100, dtype=bool)
    actual = np.array([True] * 60 + [False] * 40)
    hit, n, p = directional_accuracy(pred, actual)
    assert hit == pytest.approx(0.6) and n == 100
    assert round(p, 3) == 0.057
    hit, n, p = directional_accuracy([], [])
    assert math.isnan(hit) and n == 0 and math.isnan(p)
    hit, n, p = directional_accuracy([True, False, True, True], [True, True, True, True])
    assert hit == 0.75 and n == 4 and p == pytest.approx(0.625)  # 2*P(X>=3 | n=4, p=.5) = 2*5/16


def test_pinball_alpha_half_is_half_abs_error():
    q = np.array([100.0, 90.0, 120.0])
    y = np.array([110.0, 100.0, 100.0])
    assert pinball_loss(q, y, 0.5) == pytest.approx(np.mean(np.abs(q - y)) / 2)
    assert pinball_loss(100.0, 110.0, 0.9) == pytest.approx(9.0)  # under-forecast at a high quantile is costly
    assert pinball_loss(100.0, 90.0, 0.9) == pytest.approx(1.0)
    assert pinball_loss(100.0, 100.0, 0.25) == 0.0
    with pytest.raises(ValueError):
        pinball_loss(1.0, 1.0, 1.0)


def test_coverage_counts():
    lo = [0.0, 0.0, 0.0, 0.0]
    hi = [10.0, 10.0, 10.0, 10.0]
    assert coverage(lo, hi, [5.0, 10.0, 11.0, -1.0]) == 0.5  # closed interval: 10 is inside
    assert coverage(hi, lo, [5.0, 10.0, 11.0, -1.0]) == 0.5  # order of bounds does not matter
    assert math.isnan(coverage([], [], []))


def test_crps_from_quantiles_hand_computed():
    q = {0.05: 90.0, 0.25: 95.0, 0.5: 100.0, 0.75: 105.0, 0.95: 110.0}
    # pinball at the five levels for y=100: [0.5, 1.25, 0, 1.25, 0.5]; trapezoid over alpha = 0.6625; x2
    assert crps_from_quantiles(q, 100.0) == pytest.approx(1.325)
    # point mass at 100 with y=110: pinball = 10*alpha, trapezoid 10*(0.95^2-0.05^2)/2 = 4.5 -> 9.0
    # (the true CRPS of a point mass is |error| = 10: the missing 5% tails are the documented approximation)
    assert crps_from_quantiles(dict.fromkeys(q, 100.0), 110.0) == pytest.approx(9.0)
    assert crps_from_quantiles(q, 100.0) < crps_from_quantiles(q, 104.0) < crps_from_quantiles(q, 120.0)
    assert crps_from_quantiles(q, 96.0) == pytest.approx(crps_from_quantiles(q, 104.0))  # symmetric here
    assert math.isnan(crps_from_quantiles({0.5: 100.0}, 100.0))
    # non-monotone quantiles are sorted before integrating
    assert crps_from_quantiles({0.05: 110.0, 0.5: 100.0, 0.95: 90.0}, 100.0) == pytest.approx(
        crps_from_quantiles({0.05: 90.0, 0.5: 100.0, 0.95: 110.0}, 100.0)
    )


# --------------------------------------------------------------------------------------------------------------
# calendar mapping of target dates
# --------------------------------------------------------------------------------------------------------------


def test_target_trading_date_uses_ice_calendar():
    asof = settlement_ts(date(2026, 10, 5))  # Monday
    assert as_trading_date(asof) == date(2026, 10, 5)
    assert target_trading_date(asof, "h1d") == date(2026, 10, 6)
    assert target_trading_date(asof, "h1w") == date(2026, 10, 12)
    # Christmas 2026: Fri 25 Dec holiday, Mon 28 Dec Boxing Day substitute -> Thu 24 Dec + 1 bd = Tue 29 Dec
    assert date(2026, 12, 25).weekday() == 4
    assert target_trading_date(settlement_ts(date(2026, 12, 24)), "h1d") == date(2026, 12, 29)
    # a naive datetime is treated as UTC; a date passes through
    assert target_trading_date(datetime(2026, 10, 5, 18, 30), "h1d") == date(2026, 10, 6)
    assert target_trading_date(date(2026, 10, 9), "h1d") == date(2026, 10, 12)  # Friday -> Monday
    with pytest.raises(ValueError):
        target_trading_date(asof, "h2w")


# --------------------------------------------------------------------------------------------------------------
# aggregate evaluation
# --------------------------------------------------------------------------------------------------------------


def _evaluation_forecasts(prices: pd.Series, n_asof: int = 30) -> list[ForecastQuantiles]:
    """Naive (no-change) model, an oracle (median = realised) and a wide-band model, on synthetic prices."""
    out: list[ForecastQuantiles] = []
    for t in prices.index[:n_asof]:
        asof = settlement_ts(t.date())
        p = float(prices.loc[t])
        for h in ("h1d", "h1w"):
            target = pd.Timestamp(target_trading_date(asof, h))
            realised = float(prices.loc[target])
            out.append(fq("naive", h, asof, p, p, width=0.0))
            out.append(fq("oracle", h, asof, p, realised, width=0.02, p_up=0.9 if realised > p else 0.1))
            out.append(fq("wide", h, asof, p, p * 1.001, width=0.5, approx=True))
    return out


def test_evaluate_report_structure_and_random_walk_verdict():
    px = synthetic_prices()
    forecasts = _evaluation_forecasts(px)
    report = evaluate(forecasts, px, rw_model="naive")
    assert set(report) == {"naive", "oracle", "wide"}
    assert set(report["naive"]) == {"h1d", "h1w"}
    m = report["naive"]["h1d"]
    assert m["n"] == 30 and m["horizon_days"] == 1
    assert m["theil_u"] == pytest.approx(1.0)
    assert m["dm_stat"] == 0.0 and m["dm_p_one_sided"] == 1.0 and m["dm_p_two_sided"] == 1.0
    assert m["beats_rw"] is False and m["verdict"] == VERDICT_NOT_BEATS_RW
    # pinball at alpha=0.5 of the no-change forecast is half its MAE, which equals the RW MAE
    assert m["pinball"]["0.5"] == pytest.approx(m["mae"] / 2) and m["mae"] == pytest.approx(m["mae_rw"])
    assert m["coverage_50"] == 0.0 and m["coverage_90"] == 0.0  # degenerate band never covers
    assert m["dir_n"] == 0 and m["dir_hit_rate"] is None  # p_up = 0.5 and median == price: no direction
    assert m["approx"] is False and m["crps_approx"] is True
    beats = beats_random_walk(report)
    assert beats["naive"] == {"h1d": False, "h1w": False}


def test_evaluate_oracle_beats_random_walk_and_wide_model_covers():
    px = synthetic_prices()
    report = evaluate(_evaluation_forecasts(px), px, rw_model="naive")
    o = report["oracle"]["h1d"]
    assert o["theil_u"] == 0.0 and o["rmse"] == 0.0
    assert o["dm_stat"] < 0 and o["dm_p_one_sided"] < 0.10 and o["dm_p_two_sided"] < 0.20
    assert o["beats_rw"] is True and o["verdict"] == VERDICT_BEATS_RW
    assert o["dir_hit_rate"] == 1.0 and o["dir_n"] == 30 and o["dir_p_value"] < 1e-6
    assert o["coverage_50"] == 1.0 and o["coverage_90"] == 1.0
    assert o["crps"] > 0 and o["crps_skill_vs_rw"] > 0 and o["n_vs_rw"] == 30
    assert beats_random_walk(report)["oracle"]["h1d"] is True
    w = report["wide"]["h1w"]
    assert w["approx"] is True  # approx forecasts propagate the flag
    assert w["coverage_90"] == 1.0  # +-100% band covers everything on a 2% daily vol synthetic walk
    assert w["pinball"]["0.05"] > 0 and w["pinball"]["0.95"] > 0
    # the skill versus the random walk is negative for a band that is far too wide
    assert w["crps_skill_vs_rw"] < 0
    table = summary_table(report)
    assert len(table) == 6 and "pinball_0.5" in table.columns and "theil_u" in table.columns


def test_evaluate_pending_and_small_samples():
    px = synthetic_prices(n=40)
    asof = settlement_ts(px.index[-2].date())
    p = float(px.iloc[-2])
    pending = fq("m", "h1m", asof, p, p * 1.02)  # target far beyond the data: stays pending
    small = [fq("m", "h1d", settlement_ts(t.date()), float(px.loc[t]), float(px.loc[t]) * 1.01) for t in px.index[:2]]
    frame = align_forecasts([pending, *small], px)
    assert len(frame) == 3 and frame["realised"].isna().sum() == 1
    report = evaluate([pending, *small], px)
    assert "h1m" not in report.get("m", {})  # pending forecasts are not scored
    m = report["m"]["h1d"]
    assert m["n"] == 2 and m["dm_p_one_sided"] is None and m["verdict"] == VERDICT_INSUFFICIENT
    assert beats_random_walk(report)["m"]["h1d"] is False
    assert evaluate([], px) == {}


def test_align_uses_next_settlement_within_tolerance_and_flags_approx():
    px = synthetic_prices(n=30)
    asof = settlement_ts(px.index[5].date())
    target = pd.Timestamp(target_trading_date(asof, "h1d"))
    f = fq("m", "h1d", asof, float(px.iloc[5]), float(px.iloc[5]))
    gap = px.drop(index=target)
    frame = align_forecasts([f], gap)
    assert frame.loc[0, "realised_approx"] and frame.loc[0, "realised_date"] > target
    assert frame.loc[0, "realised"] == pytest.approx(float(gap[gap.index > target].iloc[0]))
    assert math.isnan(align_forecasts([f], gap, tolerance_days=0).loc[0, "realised"])
    assert evaluate([f], gap)["m"]["h1d"]["approx"] is True


# --------------------------------------------------------------------------------------------------------------
# archive
# --------------------------------------------------------------------------------------------------------------


def _live_forecasts(asof_day: date, price: float) -> list[ForecastQuantiles]:
    asof = settlement_ts(asof_day)
    out = []
    for h in HORIZONS:
        out.append(fq("rw", h, asof, price, price, width=0.03))
        out.append(fq("ets", h, asof, price, price * 1.01, width=0.03))
    return out


def test_archive_record_is_idempotent(tmp_store):
    arc = ForecastArchive(tmp_store)
    fcs = _live_forecasts(date(2026, 10, 5), 100.0)
    assert arc.record(fcs, run_id="eod-1") == 8
    assert arc.record(fcs, run_id="eod-2") == 0  # same (model, horizon, asof): nothing appended
    revised = [fq("ets", "h1d", settlement_ts(date(2026, 10, 5)), 100.0, 103.0)]
    assert arc.record(revised, run_id="eod-3") == 0  # a revision never rewrites the original
    recs = arc.forecasts()
    assert len(recs) == 8
    first = next(r for r in recs if r["model"] == "ets" and r["horizon"] == "h1d")
    assert first["median"] == pytest.approx(101.0) and first["run_id"] == "eod-1"
    assert first["idempotency_key"] == forecast_key("ets", "h1d", settlement_ts(date(2026, 10, 5)))
    assert first["target_date"] == "2026-10-06" and first["asof_date"] == "2026-10-05"
    assert first["asof"] == "2026-10-05T18:30:00Z"  # London 19:30 BST = 18:30 UTC
    files = tmp_store.jsonl_files(FORECASTS_LOG)
    assert len(files) == 1 and files[0].name == "forecasts-2026-10.jsonl"
    assert len(arc.latest()) == 8 and all(f.asof == settlement_ts(date(2026, 10, 5)) for f in arc.latest())


def test_archive_resolve_appends_outcomes_without_rewriting(tmp_store):
    arc = ForecastArchive(tmp_store)
    arc.record(_live_forecasts(date(2026, 10, 5), 100.0), run_id="eod-1")  # Monday
    fpath = tmp_store.jsonl_files(FORECASTS_LOG)[0]
    before = fpath.read_bytes()
    # SYNTHETIC settlements on ICE business days 5..8 Oct 2026
    days = [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)]
    px = pd.Series([100.0, 105.0, 101.0, 103.0], index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]))
    assert arc.resolve(px) == 2  # h1d (target Tue 6 Oct) for both models
    assert arc.resolve(px) == 0  # idempotent
    assert fpath.read_bytes() == before  # the forecast log is untouched
    outs = arc.outcomes()
    assert len(outs) == 2 and {o["horizon"] for o in outs} == {"h1d"}
    o = next(o for o in outs if o["model"] == "ets")
    assert o["realised"] == 105.0 and o["target_date"] == "2026-10-06" and o["realised_date"] == "2026-10-06"
    assert o["error"] == pytest.approx(101.0 - 105.0) and o["pred_up"] is True and o["actual_up"] is True
    # ets band: q25/q75 = 97.97/104.03, q05/q95 = 95.06/107.06 -> 105 is outside the 50% and inside the 90%
    assert o["hit"] is True and o["in_50"] is False and o["in_90"] is True and o["realised_approx"] is False
    assert o["approx"] is False and o["crps_approx"] is True and o["crps"] > 0
    rw = next(o for o in outs if o["model"] == "rw")
    assert rw["pred_up"] is None and rw["hit"] is None  # p_up = 0.5 and median == price: no direction
    assert len(arc.pending()) == 6
    # extend the series past 12 Oct (5 ICE business days after 5 Oct) -> h1w resolves, h1m/h3m still pending
    more = ice_days(date(2026, 10, 9), 3)  # 9, 12, 13 Oct
    px2 = pd.concat([px, pd.Series([104.0, 99.0, 98.0], index=pd.DatetimeIndex([pd.Timestamp(d) for d in more]))])
    assert arc.resolve(px2) == 2
    o = next(o for o in arc.outcomes() if o["model"] == "ets" and o["horizon"] == "h1w")
    assert o["target_date"] == "2026-10-12" and o["realised"] == 99.0 and o["hit"] is False
    assert len(arc.pending()) == 4 and len(tmp_store.jsonl_files(OUTCOMES_LOG)) == 1


def test_archive_resolve_tolerance_and_track_record(tmp_store):
    arc = ForecastArchive(tmp_store)
    arc.record(_live_forecasts(date(2026, 10, 5), 100.0), run_id="r1")
    arc.record(_live_forecasts(date(2026, 10, 6), 102.0), run_id="r2")
    arc.record(_live_forecasts(date(2026, 10, 7), 101.0), run_id="r3")
    # SYNTHETIC: the 7 Oct settlement is missing -> the h1d forecast of 6 Oct resolves on 8 Oct, labelled approx
    days = [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 8), date(2026, 10, 9)]
    px = pd.Series([100.0, 102.0, 103.0, 101.5], index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]))
    assert arc.resolve(px) == 6  # h1d of 5, 6 and 7 Oct for two models
    o = next(o for o in arc.outcomes() if o["model"] == "ets" and o["asof"].startswith("2026-10-06"))
    assert o["target_date"] == "2026-10-07" and o["realised_date"] == "2026-10-08" and o["realised_approx"] is True
    assert o["approx"] is True
    # strict tolerance: the gap stays pending
    arc2 = ForecastArchive(type(tmp_store)(tmp_store.root / "strict"))
    arc2.record(_live_forecasts(date(2026, 10, 6), 102.0), run_id="r")
    assert arc2.resolve(px, tolerance_days=0) == 0
    tr = arc.track_record()
    assert tr["source"] == "live" and tr["n_forecasts"] == 24 and tr["n_resolved"] == 6 and tr["n_pending"] == 18
    assert set(tr["models"]) == {"rw", "ets"} and set(tr["models"]["ets"]) == {"h1d"}
    m = tr["models"]["ets"]["h1d"]
    assert m["n"] == 3 and m["approx"] is True and m["realised_approx_n"] == 1
    assert tr["models"]["rw"]["h1d"]["theil_u"] == pytest.approx(1.0)
    assert tr["models"]["rw"]["h1d"]["verdict"] == VERDICT_NOT_BEATS_RW
    frame = arc.frame()
    assert len(frame) == 24 and frame["realised"].notna().sum() == 6
    empty = ForecastArchive(type(tmp_store)(tmp_store.root / "empty")).track_record()
    assert empty["n_forecasts"] == 0 and empty["models"] == {}


# --------------------------------------------------------------------------------------------------------------
# walk-forward
# --------------------------------------------------------------------------------------------------------------


class SpyForecaster:
    """Records the newest feature row it is shown. Forecast = deterministic function of the price only."""

    name = "spy"
    refit_every: int | None = 10

    def __init__(self, name: str = "spy", refit_every: int | None = 10, fail_on: set[date] | None = None):
        self.name = name
        self.refit_every = refit_every
        self.fail_on = fail_on or set()
        self.fit_calls: list[tuple[date, pd.Timestamp]] = []
        self.predict_calls: list[tuple[date, pd.Timestamp, float]] = []

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        self.fit_calls.append((as_trading_date(asof), features.index.max()))

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        from engine.forecast.base import NotFitted

        self.predict_calls.append((as_trading_date(asof), features.index.max(), price))
        if as_trading_date(asof) in self.fail_on:
            raise NotFitted("sintetico: nessuna previsione onesta oggi")
        return {h: fq(self.name, h, asof, price, price * (1 + 0.002 * HORIZONS[h]), width=0.04) for h in HORIZONS}


class NoPolicyForecaster:
    """A model that declares no refit policy at all: the evaluator must refit at every step."""

    name = "nopolicy"

    def __init__(self) -> None:
        self.fit_calls: list[date] = []

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        self.fit_calls.append(as_trading_date(asof))

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        return {h: fq(self.name, h, asof, price, price, width=0.04) for h in HORIZONS}


def test_walkforward_never_sees_future_rows():
    px = synthetic_prices(n=200)
    feats = synthetic_features(px)
    spy = SpyForecaster()
    start, end = px.index[30].date(), px.index[120].date()
    res = run_walkforward(feats, px, [spy], start, end, step_days=5, curve=synthetic_curve(px))
    assert res.steps == 19 and len(spy.predict_calls) == 19
    for day, max_seen, price in spy.predict_calls:
        assert max_seen.date() <= day  # point-in-time: newest row is the step date
        assert price == float(px.loc[pd.Timestamp(day)])  # the settlement at t, not a later one
    for day, max_seen in spy.fit_calls:
        assert max_seen.date() <= day
    assert {d for d, _, _ in spy.predict_calls} == {t.date() for t in px.index[30:121:5]}
    assert set(res.report) == {"spy", "rw", "curve"}
    assert all(f.asof == settlement_ts(as_trading_date(f.asof)) for f in res.forecasts)


def test_walkforward_future_append_invariance():
    px = synthetic_prices(n=260)
    feats = synthetic_features(px)
    cv = synthetic_curve(px)
    start, end = px.index[40].date(), px.index[100].date()
    cut = px.index[100]
    a = run_walkforward(feats.loc[:cut], px.loc[:cut], [SpyForecaster()], start, end, step_days=5, curve=cv.loc[:cut])
    b = run_walkforward(feats, px, [SpyForecaster()], start, end, step_days=5, curve=cv)
    assert len(a.forecasts) == len(b.forecasts) > 0
    assert [f.to_dict() for f in a.forecasts] == [f.to_dict() for f in b.forecasts]
    # the truncated run has fewer realised outcomes (nothing after the cut), the full run scores more
    assert a.frame["realised"].notna().sum() < b.frame["realised"].notna().sum()
    # forecasts, report = run_walkforward(...) still works
    forecasts, report = b
    assert forecasts is b.forecasts and report is b.report


def test_walkforward_benchmarks_and_report():
    px = synthetic_prices(n=300)
    feats = synthetic_features(px)
    res = run_walkforward(feats, px, [SpyForecaster()], px.index[30].date(), px.index[220].date(), step_days=5)
    assert set(res.report) == {"spy", "rw"}  # no curve passed -> curve benchmark yields nothing
    rw = res.report["rw"]
    assert set(rw) == set(HORIZONS)
    for h in HORIZONS:
        assert rw[h]["theil_u"] == pytest.approx(1.0) and rw[h]["beats_rw"] is False
        assert rw[h]["verdict"] == VERDICT_NOT_BEATS_RW
        assert rw[h]["dir_n"] == 0  # p_up = 0.5 and median == price: the RW has no direction
        assert 0.0 <= rw[h]["coverage_90"] <= 1.0
    assert res.steps == 39  # positions 30, 35, ..., 220
    assert res.fits["rw"] == 1 and res.fits["spy"] == 20  # refit_every=10 with step 5 -> every other step
    with_curve = run_walkforward(
        feats, px, [SpyForecaster()], px.index[30].date(), px.index[220].date(), step_days=5, curve=synthetic_curve(px)
    )
    assert "curve" in with_curve.report
    c = with_curve.report["curve"]["h1m"]
    assert c["approx"] is True and c["n"] > 0
    curve_fc = [f for f in with_curve.forecasts if f.model == "curve" and f.horizon == "h1m"][0]
    assert curve_fc.median < curve_fc.price_now and curve_fc.p_up < 0.5  # backwardated synthetic curve
    tidy = with_curve.frame
    assert {"model", "horizon", "asof", "target_date", "realised", "crps", "in_90"} <= set(tidy.columns)


def test_walkforward_resumable_and_skips_done():
    px = synthetic_prices(n=200)
    feats = synthetic_features(px)
    start, mid, end = px.index[30].date(), px.index[80].date(), px.index[130].date()
    first = run_walkforward(feats, px, [SpyForecaster()], start, mid, step_days=5, include_benchmarks=False)
    spy = SpyForecaster()
    second = run_walkforward(feats, px, [spy], start, end, step_days=5, done=first.forecasts, include_benchmarks=False)
    keys = [(f.model, f.horizon, as_trading_date(f.asof)) for f in second.forecasts]
    assert len(keys) == len(set(keys))  # no duplicates
    assert set(keys) >= {(f.model, f.horizon, as_trading_date(f.asof)) for f in first.forecasts}
    assert all(d > mid for d, _, _ in spy.predict_calls)  # nothing recomputed for already covered dates
    full = run_walkforward(feats, px, [SpyForecaster()], start, end, step_days=5, include_benchmarks=False)
    key = lambda f: (f.model, f.horizon, f.asof)  # noqa: E731
    got = [f.to_dict() for f in sorted(second.forecasts, key=key)]
    assert got == [f.to_dict() for f in sorted(full.forecasts, key=key)]


def test_walkforward_refit_policies_and_not_fitted():
    px = synthetic_prices(n=120)
    feats = synthetic_features(px)
    start, end = px.index[20].date(), px.index[70].date()
    once = SpyForecaster(name="once", refit_every=None)
    every = NoPolicyForecaster()
    fail_day = px.index[25].date()
    flaky = SpyForecaster(name="flaky", fail_on={fail_day})
    res = run_walkforward(feats, px, [once, every, flaky], start, end, step_days=5, include_benchmarks=False)
    assert res.steps == 11
    assert res.fits["once"] == 1 and len(once.predict_calls) == 11
    assert res.fits["nopolicy"] == 11  # unknown policy -> refit at every step
    assert res.skipped["flaky"] == 1 and "flaky" in res.report
    assert all(as_trading_date(f.asof) != fail_day for f in res.forecasts if f.model == "flaky")
    with pytest.raises(ValueError):
        run_walkforward(feats, px, [SpyForecaster(), SpyForecaster()], start, end)  # duplicate names
    with pytest.raises(ValueError):
        run_walkforward(feats, px, [], start, end, step_days=0)


def test_walkforward_dedupes_benchmark_aliases_and_skill_uses_alias():
    px = synthetic_prices(n=120)
    feats = synthetic_features(px)
    theirs = SpyForecaster(name="random_walk", refit_every=None)  # stands in for engine.forecast.benchmarks' RW
    res = run_walkforward(feats, px, [theirs, SpyForecaster()], px.index[20].date(), px.index[60].date(), step_days=5)
    assert "rw" not in res.report and "random_walk" in res.report  # no second random walk added
    assert "curve" not in res.report  # no curve data -> the curve benchmark yields nothing
    m = res.report["spy"]["h1d"]
    assert m["n_vs_rw"] == m["n"] > 0 and m["crps_skill_vs_rw"] is not None  # skill found via the alias


def test_should_refit_rules():
    asof = settlement_ts(date(2026, 10, 5))
    m = SpyForecaster(refit_every=10)
    assert should_refit(m, 0, None, asof) is True
    assert should_refit(m, 9, 0, asof) is False and should_refit(m, 10, 0, asof) is True
    assert should_refit(SpyForecaster(refit_every=None), 50, 0, asof) is False

    class Hooked:
        def needs_refit(self, ts: datetime) -> bool:
            return ts.day == 5

    assert should_refit(Hooked(), 3, 0, asof) is True
    assert should_refit(Hooked(), 3, 0, settlement_ts(date(2026, 10, 6))) is False


def test_benchmarks_direct():
    px = synthetic_prices(n=60)
    feats = synthetic_features(px)
    t = px.index[-1]
    asof = settlement_ts(t.date())
    price = float(px.iloc[-1])
    rw = RandomWalkBenchmark().predict(feats, asof, price)
    assert set(rw) == set(HORIZONS)
    for f in rw.values():
        assert f.median == price and f.p_up == 0.5 and f.q05 < f.q25 < f.median < f.q75 < f.q95
        assert f.expected_vol > 0 and f.approx is False and f.model == "rw" and len(f.drivers) <= 4
    assert rw["h3m"].q95 - rw["h3m"].q05 > rw["h1d"].q95 - rw["h1d"].q05  # band widens with the horizon
    # with no price history at all the band is degenerate and labelled approx
    bare = RandomWalkBenchmark().predict(pd.DataFrame({"noise": [1.0, 2.0]}), asof, price)
    assert bare["h1d"].approx is True and bare["h1d"].q05 == bare["h1d"].q95 == price
    curve = pd.Series({"M1": 100.0, "M2": 98.0, "M3": 96.0, "M1_code": "SYN"})
    assert curve_price_at(curve, 0) == (100.0, 1.0)
    assert curve_price_at(curve, 21)[0] == pytest.approx(98.0)
    assert curve_price_at(curve, 63) is None  # M4 unknown
    p, rank = curve_price_at(curve, 5)
    assert rank == pytest.approx(1 + 5 / 21) and p == pytest.approx(100 - 2 * 5 / 21)
    cb = CurveBenchmark().predict(feats, asof, 100.0, curve)
    assert set(cb) == {"h1d", "h1w", "h1m"} and cb["h1m"].median == pytest.approx(98.0) and cb["h1m"].approx is True
    assert 0.0 < cb["h1m"].p_up < 0.5
    assert CurveBenchmark().predict(feats, asof, 100.0, None) == {}
