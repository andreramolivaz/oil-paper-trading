"""Forecast contract (brief §11): horizons, the ForecastQuantiles record and the Forecaster protocol.

Every forecaster in this package obeys the same point-in-time rules:
  * `fit(features, asof)` and `predict(features, asof, ...)` only read rows of `features` whose date is <= the
    trading date of `asof` (helpers `slice_asof`); appending future rows must never change a past forecast
    (tests/test_forecast_models.py::test_future_append_invariance);
  * parameters are re-estimated walk-forward at calendar anchors (monthly / quarterly, see `refit_anchor`) and
    then applied to the data available at `asof`; fitted states are cached by anchor date;
  * quantiles are PRICE levels (USD/bbl), not returns; `expected_vol` is annualised;
  * a model that cannot honestly produce a forecast raises `NotFitted` (never a made-up number).

Shared statistics helpers (Student-t quantiles, pinball loss, CDF interpolation) live here so that the
evaluation module and the ensemble use exactly the same definitions as the models.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal, Protocol

import numpy as np
import pandas as pd
from scipy import stats

from engine.core.errors import EngineError
from engine.core.events import Record
from engine.core.timeutil import ensure_utc, london_date
from engine.features import catalog as cat

HORIZONS: dict[str, int] = {"h1d": 1, "h1w": 5, "h1m": 21, "h3m": 63}  # trading days
QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95)
TRADING_DAYS = 252
MAX_HORIZON = max(HORIZONS.values())


class NotFitted(EngineError):
    """The forecaster has no honest forecast for this `asof` (missing data, too short history...)."""


# ---------------------------------------------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class ForecastQuantiles(Record):
    """Forecast for one horizon. Quantiles are price levels in USD/bbl; `expected_vol` is annualised."""

    horizon: str  # key of HORIZONS
    asof: datetime  # decision time (UTC)
    price_now: float  # front settlement at asof
    median: float
    q05: float
    q25: float
    q75: float
    q95: float
    p_up: float  # P(price at horizon > price_now)
    expected_vol: float  # annualised volatility of the log return over the horizon
    drivers: list[str] = field(default_factory=list)  # Italian, <= 4 short items
    model: str = ""
    approx: bool = False  # True when any input is a proxy/approximation (labelled "≈" in the UI)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def horizon_days(self) -> int:
        return HORIZONS[self.horizon]

    def quantiles(self) -> dict[float, float]:
        return {0.05: self.q05, 0.25: self.q25, 0.5: self.median, 0.75: self.q75, 0.95: self.q95}

    def is_monotone(self, strict: bool = True) -> bool:
        vals = [self.q05, self.q25, self.median, self.q75, self.q95]
        if strict:
            return all(a < b for a, b in pairwise(vals))
        return all(a <= b for a, b in pairwise(vals))

    def validate(self) -> None:
        """Raise ValueError when the record violates the contract."""
        if self.horizon not in HORIZONS:
            raise ValueError(f"unknown horizon {self.horizon!r}")
        vals = [self.price_now, self.median, self.q05, self.q25, self.q75, self.q95]
        if any(not math.isfinite(v) or v <= 0 for v in vals):
            raise ValueError(f"non-finite or non-positive price quantile in {self.model} {self.horizon}: {vals}")
        if not self.is_monotone(strict=False):
            raise ValueError(f"quantiles not monotone in {self.model} {self.horizon}: {vals[1:]}")
        if not (0.0 <= self.p_up <= 1.0):
            raise ValueError(f"p_up out of [0,1] in {self.model} {self.horizon}: {self.p_up}")
        if not math.isfinite(self.expected_vol) or self.expected_vol <= 0:
            raise ValueError(f"expected_vol must be > 0 in {self.model} {self.horizon}: {self.expected_vol}")
        if len(self.drivers) > 4:
            raise ValueError("drivers must have at most 4 items")


class Forecaster(Protocol):
    """Walk-forward forecaster. `features` is the daily catalog frame (engine.features.catalog names)."""

    name: str

    def fit(self, features: pd.DataFrame, asof: datetime) -> None: ...

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]: ...


# ---------------------------------------------------------------------------------------------------------------
# Point-in-time helpers
# ---------------------------------------------------------------------------------------------------------------
def asof_date(asof: datetime) -> pd.Timestamp:
    """London trading date of `asof` as a tz-naive midnight Timestamp (the features index convention)."""
    return pd.Timestamp(london_date(ensure_utc(asof)))


def naive_index(index: pd.Index) -> pd.DatetimeIndex:
    """Features index as tz-naive DatetimeIndex (tz-aware indexes are converted to UTC then stripped)."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx


def slice_asof(features: pd.DataFrame, asof: datetime) -> pd.DataFrame:
    """Point-in-time view: rows whose trading date is <= the trading date of `asof` (sorted ascending)."""
    if features.empty:
        return features
    idx = naive_index(features.index)
    keep = idx <= asof_date(asof)
    out = features.loc[keep]
    if not out.index.is_monotonic_increasing:
        out = out.sort_index()
    return out


def refit_anchor(index: pd.Index, asof: datetime, freq: Literal["M", "Q"] = "M") -> pd.Timestamp:
    """First trading row of the calendar month/quarter that contains the trading date of `asof`.

    Fitted parameters are re-estimated only at these anchors (walk-forward); the anchor depends on the
    calendar and on past rows only, so appending future rows never moves it.
    """
    idx = naive_index(index)
    idx = idx[idx <= asof_date(asof)]
    if len(idx) == 0:
        raise NotFitted("nessuna riga di feature disponibile alla data richiesta")
    years = np.asarray(idx.year, dtype=np.int64)
    months = np.asarray(idx.month, dtype=np.int64)
    key = years * 12 + months if freq == "M" else years * 4 + (months - 1) // 3
    first_pos = int(np.searchsorted(key, key[-1], side="left"))
    return pd.Timestamp(idx[first_pos])


def price_series(features: pd.DataFrame) -> pd.Series:
    """Continuous (roll-adjusted) Brent close used for return modelling; falls back to the front settlement."""
    for col in (cat.PX, cat.PX_FRONT, cat.M1):
        if col in features.columns:
            s = pd.to_numeric(features[col], errors="coerce").dropna()
            if len(s) > 1:
                return s.astype(float)
    raise NotFitted("serie dei prezzi non disponibile nelle feature")


def log_returns(features: pd.DataFrame) -> pd.Series:
    """Daily log returns of the continuous price (RET_1 when present, else diff of log PX)."""
    if cat.RET_1 in features.columns:
        r = pd.to_numeric(features[cat.RET_1], errors="coerce").dropna()
        if len(r) > 10:
            return r.astype(float)
    px = price_series(features)
    return np.log(px).diff().dropna()


def conservative_vol(features: pd.DataFrame) -> tuple[float, str]:
    """Annualised vol = max(RV_YZ_21, GARCH_VOL, OVX/100) at the last available row; (value, source label).

    When none of the catalog vol features is available the close-to-close 21d vol of the price series is used.
    """
    if features.empty:
        raise NotFitted("feature vuote")
    row = features.iloc[-1]
    cands: list[tuple[float, str]] = []
    for col, label, scale in (
        (cat.RV_YZ_21, "RV Yang-Zhang 21g", 1.0),
        (cat.GARCH_VOL, "GARCH", 1.0),
        (cat.OVX, "OVX", 0.01),
    ):
        if col in features.columns:
            v = row[col]
            try:
                fv = float(v) * scale
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv) and fv > 0:
                cands.append((fv, label))
    if cands:
        return max(cands, key=lambda x: x[0])
    r = log_returns(features)
    if len(r) < 21:
        raise NotFitted("storico insufficiente per stimare la volatilità")
    vol = float(r.iloc[-21:].std(ddof=1)) * math.sqrt(TRADING_DAYS)
    if not math.isfinite(vol) or vol <= 0:
        raise NotFitted("volatilità non stimabile")
    return vol, "RV close-close 21g"


# ---------------------------------------------------------------------------------------------------------------
# Distribution helpers
# ---------------------------------------------------------------------------------------------------------------
def horizon_sigma(annual_vol: float, horizon_days: int) -> float:
    """Standard deviation of the log return over `horizon_days` from an annualised vol."""
    return float(annual_vol) * math.sqrt(horizon_days / TRADING_DAYS)


def student_t_std_quantile(alpha: float, df: float) -> float:
    """Quantile of a Student-t rescaled to unit variance (so that `sigma` is the actual standard deviation)."""
    if df <= 2:
        raise ValueError("df must be > 2 for a finite variance")
    return float(stats.t.ppf(alpha, df) * math.sqrt((df - 2.0) / df))


def normal_quantile(alpha: float) -> float:
    return float(stats.norm.ppf(alpha))


def quantiles_from_sigma(
    center_log: float, sigma: float, df: float | None = None, levels: tuple[float, ...] = QUANTILE_LEVELS
) -> dict[float, float]:
    """Price-level quantiles exp(center_log + sigma * z_alpha); z from a unit-variance Student-t(df) or Normal."""
    out: dict[float, float] = {}
    for a in levels:
        z = normal_quantile(a) if df is None else student_t_std_quantile(a, df)
        out[a] = float(math.exp(center_log + sigma * z))
    return out


def sort_quantiles(qs: Mapping[float, float], scale: float = 1.0) -> dict[float, float]:
    """Monotone fix: sort the values across levels; ties are broken by 1e-9*scale to keep strict order."""
    levels = sorted(qs)
    vals = sorted(float(qs[a]) for a in levels)
    eps = 1e-9 * max(abs(scale), 1e-12)
    for i in range(1, len(vals)):
        if vals[i] <= vals[i - 1]:
            vals[i] = vals[i - 1] + eps
    return dict(zip(levels, vals))


def cdf_from_quantiles(qs: Mapping[float, float], x: float) -> float:
    """CDF at x implied by a quantile curve: piecewise linear between the levels, exponential tails outside.

    The tail scale is the distance between the outermost two quantiles so the CDF is continuous at the edges.
    """
    levels = sorted(qs)
    vals = [float(qs[a]) for a in levels]
    lo_a, hi_a = levels[0], levels[-1]
    lo_v, hi_v = vals[0], vals[-1]
    if hi_v <= lo_v:
        return 0.5 if x == lo_v else (0.0 if x < lo_v else 1.0)
    if x <= lo_v:
        s = max(vals[1] - lo_v, 1e-12) / math.log(levels[1] / lo_a)
        return float(lo_a * math.exp((x - lo_v) / s))
    if x >= hi_v:
        s = max(hi_v - vals[-2], 1e-12) / math.log((1 - levels[-2]) / (1 - hi_a))
        return float(1.0 - (1.0 - hi_a) * math.exp(-(x - hi_v) / s))
    return float(np.interp(x, vals, levels))


def p_up_from_quantiles(qs: Mapping[float, float], price_now: float) -> float:
    """P(price at horizon > price_now) from the quantile curve, clipped to [0.001, 0.999]."""
    p = 1.0 - cdf_from_quantiles(qs, price_now)
    return float(min(0.999, max(0.001, p)))


def pinball_loss(qs: Mapping[float, float], realized: float) -> float:
    """Mean pinball (quantile) loss over the levels: rho_a(u) = u * (a - 1{u<0}), u = realized - q_a."""
    tot = 0.0
    for a, q in qs.items():
        u = realized - float(q)
        tot += u * (a - (1.0 if u < 0 else 0.0))
    return tot / len(qs)


def make_forecast(
    horizon: str,
    asof: datetime,
    price_now: float,
    qs: Mapping[float, float],
    p_up: float,
    expected_vol: float,
    drivers: list[str],
    model: str,
    approx: bool = False,
    meta: dict[str, Any] | None = None,
) -> ForecastQuantiles:
    """Build a validated record from a quantile dict (levels 0.05..0.95), enforcing monotonicity."""
    if not math.isfinite(price_now) or price_now <= 0:
        raise ValueError(f"price_now must be a positive finite number, got {price_now}")
    q = sort_quantiles(qs, scale=price_now)
    fq = ForecastQuantiles(
        horizon=horizon,
        asof=ensure_utc(asof),
        price_now=float(price_now),
        median=q[0.5],
        q05=q[0.05],
        q25=q[0.25],
        q75=q[0.75],
        q95=q[0.95],
        p_up=float(min(1.0, max(0.0, p_up))),
        expected_vol=float(expected_vol),
        drivers=list(drivers)[:4],
        model=model,
        approx=bool(approx),
        meta=dict(meta or {}),
    )
    fq.validate()
    return fq


def implied_vol_from_quantiles(qs: Mapping[float, float], horizon_days: int) -> float:
    """Annualised vol implied by the 5%-95% and 25%-75% spreads of log price quantiles (average of the two)."""
    w90 = math.log(qs[0.95] / qs[0.05]) / (2.0 * normal_quantile(0.95))
    w50 = math.log(qs[0.75] / qs[0.25]) / (2.0 * normal_quantile(0.75))
    sd_h = 0.5 * (w90 + w50)
    return float(max(sd_h, 1e-8) / math.sqrt(horizon_days / TRADING_DAYS))
