"""Trend persistence features: Hurst exponent (DFA-1, trailing 100 returns) and Lo-MacKinlay variance ratios
(q = 5, 20) on the trailing 252 log returns. Nothing else lives here.

Both are computed on sliding windows that END at the row's date (strictly trailing); a window containing a NaN
return yields NaN. DFA-1 is preferred to raw R/S because the latter is biased upward in 100-point samples
(Anis-Lloyd); DFA on the integrated, demeaned returns gives alpha ~ 0.5 for a random walk, > 0.5 for persistent
and < 0.5 for anti-persistent increments.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.builder import log_series

COLUMNS: list[str] = [cat.HURST_100, cat.VR_5, cat.VR_20]
HURST_WINDOW = 100
HURST_SCALES: tuple[int, ...] = (8, 10, 13, 16, 20, 25, 33, 50)
VR_WINDOW = 252


def _detrend_projection(s: int) -> np.ndarray:
    """I - H for a linear fit on s equally spaced points (residual-maker matrix)."""
    t = np.arange(s, dtype="float64")
    x = np.column_stack([np.ones(s), t])
    hat = x @ np.linalg.inv(x.T @ x) @ x.T
    return np.eye(s) - hat


def hurst_dfa(logpx: pd.Series, window: int = HURST_WINDOW, scales: tuple[int, ...] = HURST_SCALES) -> pd.Series:
    """DFA-1 scaling exponent of the trailing `window` log returns ending at each date."""
    r = pd.to_numeric(logpx, errors="coerce").astype("float64").diff().to_numpy()
    n = len(r)
    out = np.full(n, np.nan, dtype="float64")
    if n < window + 1:
        return pd.Series(out, index=logpx.index)
    win = sliding_window_view(r, window)  # row i covers r[i .. i+window-1], ending at position i+window-1
    valid = ~np.isnan(win).any(axis=1)
    if not valid.any():
        return pd.Series(out, index=logpx.index)
    w = win[valid]
    profile = np.cumsum(w - w.mean(axis=1, keepdims=True), axis=1)
    log_f = np.empty((w.shape[0], len(scales)), dtype="float64")
    for j, s in enumerate(scales):
        nb = window // s
        resid_maker = _detrend_projection(s)
        fwd = profile[:, : nb * s].reshape(w.shape[0], nb, s)
        bwd = profile[:, window - nb * s :].reshape(w.shape[0], nb, s)
        boxes = np.concatenate([fwd, bwd], axis=1)  # (m, 2nb, s)
        resid = boxes @ resid_maker.T
        f2 = np.mean(resid * resid, axis=(1, 2))
        log_f[:, j] = 0.5 * np.log(np.maximum(f2, 1e-300))
    log_s = np.log(np.asarray(scales, dtype="float64"))
    xs = log_s - log_s.mean()
    slope = (log_f - log_f.mean(axis=1, keepdims=True)) @ xs / float(xs @ xs)
    slope[~np.isfinite(slope)] = np.nan
    out_valid = np.full(win.shape[0], np.nan, dtype="float64")
    out_valid[valid] = slope
    out[window - 1 :] = out_valid  # window i ends at position i+window-1 (the first return is NaN: diff)
    return pd.Series(out, index=logpx.index)


def variance_ratio(logpx: pd.Series, q: int, window: int = VR_WINDOW) -> pd.Series:
    """Lo-MacKinlay VR(q) with overlapping q-period returns and the unbiased variance estimators, trailing window."""
    r = pd.to_numeric(logpx, errors="coerce").astype("float64").diff().to_numpy()
    n = len(r)
    out = np.full(n, np.nan, dtype="float64")
    if n < window + 1 or q >= window:
        return pd.Series(out, index=logpx.index)
    win = sliding_window_view(r, window)
    valid = ~np.isnan(win).any(axis=1)
    if not valid.any():
        return pd.Series(out, index=logpx.index)
    w = win[valid]
    t_len = float(window)
    mu = w.mean(axis=1, keepdims=True)
    var_a = ((w - mu) ** 2).sum(axis=1) / (t_len - 1.0)
    cs = np.concatenate([np.zeros((w.shape[0], 1)), np.cumsum(w, axis=1)], axis=1)
    qsum = cs[:, q:] - cs[:, :-q]  # (m, window-q+1) overlapping q-period sums
    m = q * (t_len - q + 1.0) * (1.0 - q / t_len)
    var_c = ((qsum - q * mu) ** 2).sum(axis=1) / m
    vr = np.where(var_a > 0, var_c / np.where(var_a > 0, var_a, 1.0), np.nan)
    out_valid = np.full(win.shape[0], np.nan, dtype="float64")
    out_valid[valid] = vr
    out[window - 1 :] = out_valid
    return pd.Series(out, index=logpx.index)


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    px = base[cat.PX].astype("float64").where(base[cat.PX] > 0)
    logpx = log_series(px)
    out = {
        cat.HURST_100: hurst_dfa(logpx, HURST_WINDOW),
        cat.VR_5: variance_ratio(logpx, 5, VR_WINDOW),
        cat.VR_20: variance_ratio(logpx, 20, VR_WINDOW),
    }
    return pd.DataFrame(out, index=base.index).astype("float64")
