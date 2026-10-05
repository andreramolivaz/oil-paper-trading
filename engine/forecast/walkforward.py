"""Walk-forward (rolling out-of-sample) evaluation of forecasters against the random-walk and curve benchmarks.

Point-in-time discipline: at every step t the model only receives `features.loc[:t]`, the settlement at t and the
curve at t. Realised values are looked up only AFTER the loop, to score the forecasts. Truncating the inputs at
any T > end therefore cannot change a single forecast (tests/test_forecast_eval.py, future-append invariance).

Refit policy (the model's own): a Forecaster may expose
  * `refit_every: int | None`  trading days (rows of the feature frame) between refits; None/0 = fit once;
  * `needs_refit(asof: datetime) -> bool`  evaluated at every step after the first fit.
Without either attribute the model is refit at every step (the safest choice, possibly slow).

Resumability: pass already computed forecasts as `done`; (model, horizon, trading date) triples found there
are skipped and included unchanged in the output. A resumed run refits at its first step.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from engine.core.errors import EngineError
from engine.core.timeutil import LONDON, settlement_ts
from engine.features import catalog
from engine.forecast.base import HORIZONS, Forecaster, ForecastQuantiles
from engine.forecast.evaluation import align_forecasts, as_trading_date, evaluate_frame, normalize_price_index

log = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252
TRADING_DAYS_PER_MONTH = 21
_Z = {0.05: float(stats.norm.ppf(0.05)), 0.25: float(stats.norm.ppf(0.25)), 0.75: float(stats.norm.ppf(0.75))}
_Z[0.95] = -_Z[0.05]
_CURVE_COL = re.compile(r"^M(\d+)$")
_MISSING = object()
# Names under which the benchmarks may appear (this module's lightweight ones and engine.forecast.benchmarks').
RW_ALIASES = frozenset({"rw", "random_walk"})
CURVE_ALIASES = frozenset({"curve", "futures_curve"})


# --------------------------------------------------------------------------------------
# benchmarks (brief §11: random walk and the futures curve, "the market's forecast")
# --------------------------------------------------------------------------------------
def trailing_vol(features: pd.DataFrame, window: int = 21) -> tuple[float, str]:
    """Annualised volatility known at the last row of `features`: catalog RV_YZ_21, else RV_CC_21, else the
    close-to-close vol of the last `window` log returns of PX_FRONT (or PX). Returns (vol, source); NaN if none."""
    for col in (catalog.RV_YZ_21, catalog.RV_CC_21):
        if col in features.columns and len(features):
            v = features[col].iloc[-1]
            if pd.notna(v) and float(v) > 0:
                return float(v), col
    for col in (catalog.PX_FRONT, catalog.PX, "close"):
        if col in features.columns:
            px = pd.to_numeric(features[col], errors="coerce").dropna()
            if len(px) >= 3:
                r = np.log(px.to_numpy(dtype=float)[-(window + 1) :])
                r = np.diff(r)
                if len(r) >= 2 and np.isfinite(r).all():
                    return float(np.std(r, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)), f"cc_{col}_{window}"
    return float("nan"), "none"


def lognormal_quantiles(center: float, sigma_h: float) -> dict[str, float]:
    """Quantile PRICE levels of a lognormal centred (median) at `center` with log-sd `sigma_h` over the horizon."""
    if not np.isfinite(sigma_h) or sigma_h <= 0:
        return {"median": center, "q05": center, "q25": center, "q75": center, "q95": center}
    return {
        "median": center,
        "q05": center * math.exp(_Z[0.05] * sigma_h),
        "q25": center * math.exp(_Z[0.25] * sigma_h),
        "q75": center * math.exp(_Z[0.75] * sigma_h),
        "q95": center * math.exp(_Z[0.95] * sigma_h),
    }


@dataclass
class RandomWalkBenchmark:
    """No-change forecast: median = last settlement, lognormal band from trailing realised vol, p_up = 0.5."""

    name: str = "rw"
    vol_window: int = 21
    refit_every: int | None = None  # nothing to fit

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        return None

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        vol, vol_src = trailing_vol(features, self.vol_window)
        out: dict[str, ForecastQuantiles] = {}
        for h, days in HORIZONS.items():
            sigma_h = vol * math.sqrt(days / TRADING_DAYS_PER_YEAR) if np.isfinite(vol) else float("nan")
            q = lognormal_quantiles(price, sigma_h)
            drivers = ["Nessuna deriva: mediana = ultimo settlement"]
            drivers.append(
                f"Ampiezza dalla volatilità realizzata {vol * 100:.0f}% ann."
                if np.isfinite(vol)
                else "Volatilità non disponibile: banda degenere"
            )
            out[h] = ForecastQuantiles(
                horizon=h,
                asof=asof,
                price_now=price,
                median=q["median"],
                q05=q["q05"],
                q25=q["q25"],
                q75=q["q75"],
                q95=q["q95"],
                p_up=0.5,
                expected_vol=vol,
                drivers=drivers,
                model=self.name,
                approx=not np.isfinite(vol),
                meta={"vol_source": vol_src, "benchmark": True},
            )
        return out


def curve_price_at(curve: pd.Series, days_ahead: int) -> tuple[float, float] | None:
    """Linear interpolation of the settlement curve at `days_ahead` trading days: rank = 1 + days/21 months.

    Returns (price, fractional_rank) or None when the needed ranks are unknown (NaN). Approximation: contract
    ranks are treated as equally spaced months and the then-front price at t+h is read off today's curve.
    """
    ranks: dict[int, float] = {}
    for k, v in curve.items():
        m = _CURVE_COL.match(str(k))
        if m and pd.notna(v):
            ranks[int(m.group(1))] = float(v)
    if 1 not in ranks:
        return None
    r = 1.0 + days_ahead / TRADING_DAYS_PER_MONTH
    lo, hi = math.floor(r), math.ceil(r)
    if lo == hi:
        return (ranks[lo], r) if lo in ranks else None
    if lo not in ranks or hi not in ranks:
        return None
    w = r - lo
    return ranks[lo] * (1 - w) + ranks[hi] * w, r


@dataclass
class CurveBenchmark:
    """The futures curve as forecast: median = curve interpolated at the horizon, lognormal band from realised vol."""

    name: str = "curve"
    vol_window: int = 21
    refit_every: int | None = None

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        return None

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        if curve is None:
            return {}
        vol, vol_src = trailing_vol(features, self.vol_window)
        out: dict[str, ForecastQuantiles] = {}
        for h, days in HORIZONS.items():
            hit = curve_price_at(curve, days)
            if hit is None or hit[0] <= 0 or price <= 0:
                continue
            center, rank = hit
            sigma_h = vol * math.sqrt(days / TRADING_DAYS_PER_YEAR) if np.isfinite(vol) else float("nan")
            q = lognormal_quantiles(center, sigma_h)
            if np.isfinite(sigma_h) and sigma_h > 0:
                p_up = float(stats.norm.cdf(math.log(center / price) / sigma_h))
            else:
                p_up = 0.5 if center == price else (1.0 if center > price else 0.0)
            slope = (center / price - 1.0) * 100
            out[h] = ForecastQuantiles(
                horizon=h,
                asof=asof,
                price_now=price,
                median=q["median"],
                q05=q["q05"],
                q25=q["q25"],
                q75=q["q75"],
                q95=q["q95"],
                p_up=p_up,
                expected_vol=vol,
                drivers=[
                    f"Curva futures interpolata al rango M{rank:.2f} (≈)",
                    f"Pendenza implicita {slope:+.1f}% rispetto al front",
                ],
                model=self.name,
                approx=True,
                meta={"vol_source": vol_src, "curve_rank": rank, "benchmark": True},
            )
        return out


# --------------------------------------------------------------------------------------
# walk-forward driver
# --------------------------------------------------------------------------------------
@dataclass
class WalkForwardResult:
    forecasts: list[ForecastQuantiles]
    report: dict[str, dict[str, dict[str, Any]]]
    frame: pd.DataFrame
    fits: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)  # steps where a model raised EngineError (no forecast)
    steps: int = 0

    def __iter__(self) -> Iterator[Any]:
        """Allows `forecasts, report = run_walkforward(...)`."""
        yield self.forecasts
        yield self.report


def _day_index(index: pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(index))
    if idx.tz is not None:
        idx = idx.tz_convert(LONDON).tz_localize(None)
    return idx.normalize()


def _as_day(x: datetime | date | pd.Timestamp | str) -> pd.Timestamp:
    return pd.Timestamp(as_trading_date(x))


def _curve_row(curve: pd.DataFrame | None, t: pd.Timestamp) -> pd.Series | None:
    if curve is None or t not in curve.index:
        return None
    row = curve.loc[t]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    cols = [c for c in row.index if _CURVE_COL.match(str(c))]
    vals = pd.to_numeric(row[cols], errors="coerce")
    return None if vals.dropna().empty else vals.astype(float)


def should_refit(model: Any, pos: int, last_fit_pos: int | None, asof: datetime) -> bool:
    if last_fit_pos is None:
        return True
    hook = getattr(model, "needs_refit", None)
    if callable(hook):
        return bool(hook(asof))
    every = getattr(model, "refit_every", _MISSING)
    if every is _MISSING:
        return True
    if not isinstance(every, int) or every <= 0:
        return False
    return pos - last_fit_pos >= every


def _conform(fq: ForecastQuantiles, model_name: str, horizon: str, asof: datetime) -> ForecastQuantiles:
    if fq.horizon != horizon:
        raise ValueError(f"{model_name}: forecast keyed {horizon!r} carries horizon {fq.horizon!r}")
    if as_trading_date(fq.asof) != as_trading_date(asof):
        raise ValueError(f"{model_name}: forecast asof {fq.asof} does not match step {asof}")
    return replace(fq, asof=asof, model=fq.model or model_name)


def run_walkforward(
    features: pd.DataFrame,
    prices: pd.Series,
    models: Sequence[Forecaster],
    start: datetime | date | str,
    end: datetime | date | str,
    step_days: int = 5,
    curve: pd.DataFrame | None = None,
    *,
    done: Sequence[ForecastQuantiles] | None = None,
    include_benchmarks: bool = True,
    tolerance_days: int = 3,
    progress: Callable[[pd.Timestamp, int, int], None] | None = None,
) -> WalkForwardResult:
    """Roll through [start, end] every `step_days` trading days, fit/predict point-in-time, score afterwards.

    features: daily catalog frame indexed by trading date; prices: front settlements by trading date (also the
    realised values); curve: M1..Mn settlements by trading date (optional). The RW and curve benchmarks are added
    unless a model with the same name is passed. Returns WalkForwardResult(forecasts, report, frame, fits).
    """
    if step_days < 1:
        raise ValueError("step_days must be >= 1")
    feats = features.copy()
    feats.index = _day_index(feats.index)
    feats = feats[~feats.index.duplicated(keep="last")].sort_index()
    px = normalize_price_index(prices)
    cv: pd.DataFrame | None = None
    if curve is not None and len(curve):
        cv = curve.copy()
        cv.index = _day_index(cv.index)
        cv = cv[~cv.index.duplicated(keep="last")].sort_index()

    all_models: list[Any] = list(models)
    names = {m.name for m in all_models}
    if include_benchmarks:
        for bench, aliases in ((RandomWalkBenchmark(), RW_ALIASES), (CurveBenchmark(), CURVE_ALIASES)):
            if not names & aliases:  # the caller may already pass engine.forecast.benchmarks' own versions
                all_models.append(bench)
                names.add(bench.name)
    if len(names) != len(all_models):
        raise ValueError("model names must be unique")

    start_ts, end_ts = _as_day(start), _as_day(end)
    grid = feats.index[(feats.index >= start_ts) & (feats.index <= end_ts)][::step_days]

    out: list[ForecastQuantiles] = list(done or [])
    done_keys = {(f.model, f.horizon, as_trading_date(f.asof)) for f in out}
    last_fit: dict[str, int | None] = dict.fromkeys(names)
    fits: dict[str, int] = dict.fromkeys(names, 0)
    skipped: dict[str, int] = dict.fromkeys(names, 0)
    steps = 0
    for i, t in enumerate(grid):
        if t not in px.index:
            log.debug("walkforward: no settlement on %s, step skipped", t.date())
            continue
        steps += 1
        day = t.date()
        asof = settlement_ts(day)
        price = float(px.loc[t])
        feats_t = feats.loc[:t]
        curve_t = _curve_row(cv, t)
        pos = int(np.searchsorted(feats.index.to_numpy(), t.to_datetime64()))
        for m in all_models:
            if all((m.name, h, day) in done_keys for h in HORIZONS):
                continue
            try:
                if should_refit(m, pos, last_fit[m.name], asof):
                    m.fit(feats_t, asof)
                    last_fit[m.name] = pos
                    fits[m.name] += 1
                preds = m.predict(feats_t, asof, price, curve_t)
            except EngineError as exc:  # e.g. NotFitted: no honest forecast today -> no forecast, never a guess
                log.warning("walkforward: %s skipped on %s: %s", m.name, day, exc)
                skipped[m.name] += 1
                continue
            for h, fq in preds.items():
                if h not in HORIZONS:
                    raise ValueError(f"{m.name}: unknown horizon {h!r}")
                if (m.name, h, day) in done_keys:
                    continue
                out.append(_conform(fq, m.name, h, asof))
                done_keys.add((m.name, h, day))
        if progress is not None:
            progress(t, i + 1, len(grid))

    frame = align_forecasts(out, px, tolerance_days) if out else pd.DataFrame()
    report = evaluate_frame(frame) if len(frame) else {}
    return WalkForwardResult(forecasts=out, report=report, frame=frame, fits=fits, skipped=skipped, steps=steps)
