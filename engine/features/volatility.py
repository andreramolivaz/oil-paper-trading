"""Volatility features: Yang-Zhang realised vol, OVX, variance risk premium, vol-of-vol, 1y percentile and a
walk-forward GARCH(1,1)-t one-step-ahead forecast.

All statistics are trailing-only. The front-month OHLC bars are rescaled by PX/close before use so that the
overnight component of Yang-Zhang does not contain roll jumps (PX is the roll-adjusted continuous close; within a
bar the log ratios open/high/low/close are unchanged by the rescaling). When OHLC is missing (e.g. spot-only
history before 2007) the realised vol falls back to close-to-close on PX and the module flags it in
`attrs['yz_fallback_rows']` and `attrs['approx_columns']`.

GARCH: refit every `refit_every` trading days on an expanding window (at least `min_obs` returns), parameters
held fixed in between while the conditional variance is filtered forward day by day with the observed returns.
The value at date t is the annualised one-step-ahead vol for t+1 using returns <= t and a fit on returns <= t:
strictly causal. Fitted parameters are cached by (series start, refit date, length, checksum) so daily rebuilds
do not refit history.
"""

from __future__ import annotations

import logging
import math
import warnings
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.builder import log_series, prices_at

log = logging.getLogger(__name__)

TRADING_DAYS = 252
OHLC = ("open", "high", "low", "close")
COLUMNS: list[str] = [
    cat.RV_YZ_10,
    cat.RV_YZ_21,
    cat.RV_YZ_63,
    cat.RV_CC_21,
    cat.OVX,
    cat.VRP,
    cat.VOL_OF_VOL,
    cat.VOL_PCTL_1Y,
    cat.GARCH_VOL,
]

_GARCH_CACHE: dict[tuple[Any, ...], tuple[float, ...]] = {}


# ----------------------------------------------------------------------------------------------------------------
# realised volatility
# ----------------------------------------------------------------------------------------------------------------
def adjusted_ohlc(base: pd.DataFrame) -> pd.DataFrame | None:
    """Front OHLC rescaled by PX/close (roll-adjusted bars). None when OHLC is absent or empty."""
    if not all(c in base.columns for c in OHLC) or cat.PX not in base.columns:
        return None
    raw = base.loc[:, list(OHLC)].astype("float64")
    if any(raw[c].notna().sum() == 0 for c in ("open", "high", "low")):
        return None  # close alone is not a bar: callers fall back to close-to-close on PX
    close = raw["close"].where(raw["close"] > 0)
    factor = base[cat.PX].astype("float64") / close
    adj = raw.mul(factor, axis=0)
    adj = adj.where(adj > 0)
    return adj


def yang_zhang(adj: pd.DataFrame, window: int) -> pd.Series:
    """Yang-Zhang (2000) realised volatility, annualised, trailing `window` bars (NaN until the window is full)."""
    o = log_series(adj["open"] / adj["close"].shift(1))
    c = log_series(adj["close"] / adj["open"])
    u = log_series(adj["high"] / adj["open"])
    d = log_series(adj["low"] / adj["open"])
    rs = u * (u - c) + d * (d - c)
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    var_o = o.rolling(window, min_periods=window).var()
    var_c = c.rolling(window, min_periods=window).var()
    mean_rs = rs.rolling(window, min_periods=window).mean()
    var = var_o + k * var_c + (1.0 - k) * mean_rs
    return pd.Series(np.sqrt(var.clip(lower=0.0).to_numpy(dtype="float64") * TRADING_DAYS), index=adj.index)


def close_to_close(px: pd.Series, window: int) -> pd.Series:
    ret = log_series(px).diff()
    return ret.rolling(window, min_periods=window).std() * math.sqrt(TRADING_DAYS)


def realized_vol(base: pd.DataFrame, window: int) -> tuple[pd.Series, int]:
    """Yang-Zhang on adjusted OHLC, per-row fallback to close-to-close on PX. Returns (vol, n_fallback_rows)."""
    px = base[cat.PX].astype("float64")
    cc = close_to_close(px, window)
    adj = adjusted_ohlc(base)
    if adj is None:
        return cc, int(cc.notna().sum())
    yz = yang_zhang(adj, window)
    fallback = yz.isna() & cc.notna()
    out = yz.where(~fallback, cc)
    return out, int(fallback.sum())


# ----------------------------------------------------------------------------------------------------------------
# GARCH(1,1)-t walk-forward
# ----------------------------------------------------------------------------------------------------------------
def _fit_garch(r: np.ndarray, start: tuple[float, ...] | None) -> tuple[float, ...]:
    """Fit GARCH(1,1) with Student-t innovations on percent returns; returns (mu, omega, alpha, beta, nu)."""
    from arch.univariate import GARCH, ConstantMean, StudentsT

    am = ConstantMean(r, rescale=False)
    am.volatility = GARCH(p=1, q=1)
    am.distribution = StudentsT()
    sv = np.asarray(start, dtype="float64") if start is not None and len(start) == 5 else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # arch warns about scale / starting values; both are handled here
        res = am.fit(disp="off", show_warning=False, starting_values=sv)
    p = res.params
    out = (float(p["mu"]), float(p["omega"]), float(p["alpha[1]"]), float(p["beta[1]"]), float(p["nu"]))
    if not all(np.isfinite(out)) or out[2] < 0 or out[3] < 0 or out[1] <= 0:
        raise ValueError(f"degenerate GARCH fit: {out}")
    return out


def _filter_variance(eps2: np.ndarray, omega: float, alpha: float, beta: float, h0: float) -> np.ndarray:
    """h[t+1] = omega + alpha*eps2[t] + beta*h[t] for t = 0..n-1; returns the n one-step-ahead variances."""
    n = len(eps2)
    out = np.empty(n, dtype="float64")
    h = h0
    for t in range(n):
        h = omega + alpha * eps2[t] + beta * h
        out[t] = h
    return out


def garch_vol_walk_forward(
    ret: pd.Series,
    refit_every: int = 21,
    min_obs: int = 500,
    use_cache: bool = True,
) -> pd.Series:
    """Annualised one-step-ahead GARCH(1,1)-t vol at each date using only data up to that date (see module doc)."""
    r_all = pd.to_numeric(ret, errors="coerce").astype("float64")
    r = r_all.dropna() * 100.0  # percent returns: the scale arch expects
    n = len(r)
    out = np.full(n, np.nan, dtype="float64")
    if n < min_obs:
        return pd.Series(out, index=r.index).reindex(r_all.index)
    vals = r.to_numpy()
    sq_cum = np.cumsum(vals * vals)
    series_tag = (str(r.index[0]), round(float(vals[0]), 10))
    params: tuple[float, ...] | None = None
    t = min_obs - 1
    while t < n:
        window = vals[: t + 1]
        key = (*series_tag, str(r.index[t]), t + 1, round(float(sq_cum[t]), 6))
        cached = _GARCH_CACHE.get(key) if use_cache else None
        if cached is None:
            try:
                cached = _fit_garch(window, params)
            except Exception as e:
                log.warning("GARCH refit at %s failed (%s); keeping previous parameters", r.index[t], e)
                cached = params
            if cached is not None and use_cache:
                _GARCH_CACHE[key] = cached
        params = cached
        end = min(t + refit_every, n)
        if params is not None:
            mu, omega, alpha, beta = params[:4]
            eps2 = (vals[:end] - mu) ** 2
            h0 = float(np.mean(eps2[: t + 1]))  # backcast from the fit window (data <= t)
            h = _filter_variance(eps2, omega, alpha, beta, h0)
            out[t:end] = np.sqrt(np.maximum(h[t:end], 0.0) * TRADING_DAYS) / 100.0
        t = end
    return pd.Series(out, index=r.index).reindex(r_all.index)


# ----------------------------------------------------------------------------------------------------------------
# module entry point
# ----------------------------------------------------------------------------------------------------------------
def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    cfg: dict[str, Any] = dict(base.attrs.get("config", {}) or {}).get("garch", {}) or {}
    px = base[cat.PX].astype("float64")
    out: dict[str, pd.Series] = {}
    fallback_rows = 0
    for window, name in ((10, cat.RV_YZ_10), (21, cat.RV_YZ_21), (63, cat.RV_YZ_63)):
        vol, nfb = realized_vol(base, window)
        out[name] = vol
        fallback_rows = max(fallback_rows, nfb)
    out[cat.RV_CC_21] = close_to_close(px, 21)
    ovx = prices_at(md, "ovx", base.index)
    out[cat.OVX] = ovx
    out[cat.VRP] = ovx / 100.0 - out[cat.RV_YZ_21]
    out[cat.VOL_OF_VOL] = ovx.diff().rolling(21, min_periods=21).std()
    out[cat.VOL_PCTL_1Y] = out[cat.RV_YZ_21].rolling(TRADING_DAYS, min_periods=126).rank(pct=True)
    ret = log_series(px).diff()
    out[cat.GARCH_VOL] = garch_vol_walk_forward(
        ret,
        refit_every=int(cfg.get("refit_every", 21)),
        min_obs=int(cfg.get("min_obs", 500)),
        use_cache=bool(cfg.get("cache", True)),
    )
    frame = pd.DataFrame(out, index=base.index).astype("float64")
    frame.attrs["yz_fallback_rows"] = fallback_rows
    frame.attrs["approx_columns"] = (
        [cat.RV_YZ_10, cat.RV_YZ_21, cat.RV_YZ_63, cat.VRP, cat.VOL_PCTL_1Y] if fallback_rows else []
    )
    return frame
