"""Macro features: dollar, equities, copper, rates passthroughs and a Kalman time-varying-beta fair value.

MACRO_FV_RESID / MACRO_FV_Z: weekly (5-day, overlapping, updated daily) log returns of PX are regressed on the
5-day log returns of DXY, SPX, copper and the 5-day change of the US 10y yield with random-walk coefficients
(Kalman filter, hand-rolled). The state is updated day by day with the one-step-ahead innovation
e_t = y_t - x_t' beta_{t|t-1}: strictly causal. The first `burn_in` valid observations only initialise the state
(OLS for beta_0, noise scales) and get NaN. Observation noise R_t is an EWMA of past squared innovations
(half-life `r_halflife` days) so the gain falls in turbulent markets; the state noise is Q = q_rel * sigma2_0 /
var(x_j) so every coefficient drifts at a comparable relative speed.

MACRO_FV_RESID = sum of the last 21 innovations / 5 (each daily return enters five overlapping weekly
residuals, so this approximates the 21-day cumulative log return not explained by the macro drivers).
MACRO_FV_Z = z-score of MACRO_FV_RESID against its trailing 252-day mean and std.
Regressors that are entirely missing are dropped (attrs['regressors_used']); rows with any missing input skip
the update and get NaN innovations (never filled).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.builder import log_series, prices_at

COLUMNS: list[str] = [
    cat.DXY,
    cat.DXY_RET_21,
    cat.VIX,
    cat.SPX_RET_21,
    cat.COPPER_RET_21,
    cat.US10Y,
    cat.BREAKEVEN,
    cat.MACRO_FV_RESID,
    cat.MACRO_FV_Z,
]
# regressor -> transform ("logret" = 5d log return, "diff" = 5d change)
REGRESSORS: dict[str, str] = {"dxy": "logret", "spx": "logret", "copper": "logret", "us10y": "diff"}
WEEK = 5
RESID_WINDOW = 21
Z_WINDOW = 252


def kalman_tvp_innovations(
    y: np.ndarray,
    x: np.ndarray,
    burn_in: int = 252,
    q_rel: float = 1e-4,
    r_halflife: int = 63,
    p0_scale: float = 10.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Random-walk-coefficient regression filtered forward. Returns (innovations, betas), NaN where not updated.

    x has shape (n, k) WITHOUT the intercept (added here). Rows with any non-finite input are skipped.
    """
    n, k = x.shape
    innov = np.full(n, np.nan, dtype="float64")
    betas = np.full((n, k + 1), np.nan, dtype="float64")
    xx = np.column_stack([np.ones(n), x])
    valid = np.isfinite(y) & np.all(np.isfinite(xx), axis=1)
    idx = np.flatnonzero(valid)
    if len(idx) <= burn_in + 1:
        return innov, betas
    init = idx[:burn_in]
    xb, yb = xx[init], y[init]
    xtx = xb.T @ xb
    try:
        xtx_inv = np.linalg.inv(xtx + 1e-12 * np.eye(k + 1))
    except np.linalg.LinAlgError:
        return innov, betas
    beta = xtx_inv @ xb.T @ yb
    resid = yb - xb @ beta
    sigma2 = float(np.var(resid, ddof=k + 1)) if burn_in > k + 1 else float(np.var(resid))
    sigma2 = max(sigma2, 1e-12)
    var_x = np.var(xb, axis=0)
    var_x[0] = 1.0
    var_x = np.where(var_x > 0, var_x, 1.0)
    q_mat = np.diag(q_rel * sigma2 / var_x)
    p_cov = p0_scale * sigma2 * xtx_inv
    lam = 0.5 ** (1.0 / max(1, r_halflife))
    r_obs = sigma2
    eye = np.eye(k + 1)
    for t in idx[burn_in:]:
        xt = xx[t]
        p_cov = p_cov + q_mat
        e = float(y[t] - xt @ beta)
        s = float(xt @ p_cov @ xt) + r_obs
        gain = (p_cov @ xt) / s
        beta = beta + gain * e
        p_cov = (eye - np.outer(gain, xt)) @ p_cov
        p_cov = 0.5 * (p_cov + p_cov.T)
        innov[t] = e
        betas[t] = beta
        r_obs = lam * r_obs + (1.0 - lam) * e * e
    return innov, betas


def _weekly(series: pd.Series, how: str) -> pd.Series:
    s = series.astype("float64")
    if how == "logret":
        return log_series(s).diff(WEEK)
    return s.diff(WEEK)


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    cfg: dict[str, Any] = dict(base.attrs.get("config", {}) or {}).get("macro", {}) or {}
    index = base.index
    px = base[cat.PX].astype("float64").where(base[cat.PX] > 0)
    dxy = prices_at(md, "dxy", index)
    spx = prices_at(md, "spx", index)
    copper = prices_at(md, "copper", index)
    us10y = prices_at(md, "us10y", index)
    out: dict[str, pd.Series] = {
        cat.DXY: dxy,
        cat.DXY_RET_21: log_series(dxy).diff(21),
        cat.VIX: prices_at(md, "vix", index),
        cat.SPX_RET_21: log_series(spx).diff(21),
        cat.COPPER_RET_21: log_series(copper).diff(21),
        cat.US10Y: us10y,
        cat.BREAKEVEN: prices_at(md, "breakeven10y", index),
    }
    raw = {"dxy": dxy, "spx": spx, "copper": copper, "us10y": us10y}
    used = [name for name, s in raw.items() if s.notna().sum() > 0]
    y = log_series(px).diff(WEEK).to_numpy(dtype="float64")
    resid = pd.Series(np.nan, index=index, dtype="float64")
    if used and len(index) > 0:
        x = np.column_stack([_weekly(raw[name], REGRESSORS[name]).to_numpy(dtype="float64") for name in used])
        innov, _ = kalman_tvp_innovations(
            y,
            x,
            burn_in=int(cfg.get("burn_in", 252)),
            q_rel=float(cfg.get("q_rel", 1e-4)),
            r_halflife=int(cfg.get("r_halflife", 63)),
        )
        e = pd.Series(innov, index=index)
        resid = e.rolling(RESID_WINDOW, min_periods=RESID_WINDOW - 6).sum() / WEEK
    out[cat.MACRO_FV_RESID] = resid
    mu = resid.rolling(Z_WINDOW, min_periods=126).mean()
    sd = resid.rolling(Z_WINDOW, min_periods=126).std()
    out[cat.MACRO_FV_Z] = (resid - mu) / sd.where(sd > 0)
    frame = pd.DataFrame(out, index=index).astype("float64")
    frame.attrs["regressors_used"] = used
    frame.attrs["approx_columns"] = []
    return frame
