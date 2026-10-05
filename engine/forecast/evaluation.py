"""Forecast evaluation: pure metric functions and the aggregate report versus the random walk (brief §11).

Every metric is computed on PRICE levels. The benchmark is the no-change forecast ("random walk"):
the front settlement at the forecast time, `price_now`. Point and probabilistic metrics:

* Theil's U2 (ratio of RMSE to the no-change RMSE), Theil (1966), Bliemel (1973);
* Diebold-Mariano test of equal predictive accuracy with a Newey-West HAC long-run variance and the
  Harvey-Leybourne-Newbold (1997) small-sample correction, Diebold & Mariano (1995), Newey & West (1987);
* directional accuracy with an exact two-sided binomial test against p = 0.5 (Pesaran & Timmermann style
  hit rate; the binomial test is the simplest exact version);
* pinball (quantile) loss, Koenker & Bassett (1978);
* CRPS approximated from the available quantile levels via the quantile-score decomposition
  CRPS = 2 * integral_0^1 pinball_alpha d(alpha), Laio & Tamea (2007), Gneiting & Ranjan (2011);
* empirical coverage of the central 50% and 90% intervals.

Dates: the realised value of a forecast issued at trading date d with horizon h trading days is the
settlement at the ICE business date d + h (engine.core.calendar, UK bank holidays). If that exact date is
missing from the price series (calendar mismatch, missing settlement) the first settlement within
`tolerance_days` calendar days after it is used and the outcome is labelled approx=True. Nothing is ever
interpolated or invented: without a settlement the forecast stays pending.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from engine.core.calendar import add_business_days
from engine.core.timeutil import LONDON, ensure_utc, london_date
from engine.forecast.base import HORIZONS, ForecastQuantiles

ArrayLike = Sequence[float] | np.ndarray | pd.Series

# Quantile levels in the forecast contract -> field names.
QUANTILE_FIELDS: dict[float, str] = {0.05: "q05", 0.25: "q25", 0.5: "median", 0.75: "q75", 0.95: "q95"}

VERDICT_BEATS_RW = "batte il random walk"
VERDICT_NOT_BEATS_RW = "non batte il random walk"
VERDICT_INSUFFICIENT = "campione insufficiente"

# Names under which the random-walk forecaster may appear (walkforward's "rw", engine.forecast.benchmarks' name).
RW_MODEL_ALIASES: tuple[str, ...] = ("rw", "random_walk")

# Rule for `beats_random_walk`: Theil U < 1 AND one-sided DM p-value < 0.10 (model loss < RW loss).
BEATS_RW_THEIL_MAX = 1.0
BEATS_RW_DM_P_MAX = 0.10
MIN_N_FOR_TESTS = 3  # fewer resolved forecasts: tests are undefined (NaN) and the verdict is "insufficient"


# --------------------------------------------------------------------------------------
# pure metrics
# --------------------------------------------------------------------------------------
def _arr(x: ArrayLike | float) -> np.ndarray:
    return np.asarray(x, dtype=float).reshape(-1)


def theil_u(forecast: ArrayLike, actual: ArrayLike, naive: ArrayLike, scale: ArrayLike | None = None) -> float:
    """Theil's U2: RMSE of the forecast divided by the RMSE of the naive (no-change) forecast.

    U2 = sqrt(sum((f_t - a_t)^2)) / sqrt(sum((n_t - a_t)^2)).  U2 < 1 means the forecast beats the naive
    benchmark; U2 = 1 when forecast == naive. With `scale` (e.g. the price at forecast time) every error is
    divided elementwise before squaring, which gives Theil's U2 in Bliemel's relative-change form and makes the
    statistic comparable across price eras. Returns NaN when the naive error is identically zero or n == 0.

    References: Theil, H. (1966) Applied Economic Forecasting; Bliemel, F. (1973) J. Marketing Research 10.
    """
    f, a, n = _arr(forecast), _arr(actual), _arr(naive)
    if not (len(f) == len(a) == len(n)) or len(f) == 0:
        return float("nan")
    ef, en = f - a, n - a
    if scale is not None:
        s = _arr(scale)
        ef, en = ef / s, en / s
    den = float(np.sqrt(np.sum(en**2)))
    if den == 0.0 or not np.isfinite(den):
        return float("nan")
    return float(np.sqrt(np.sum(ef**2)) / den)


def newey_west_variance(d: ArrayLike, lag: int) -> float:
    """Newey-West (1987) HAC estimate of the long-run variance of the sample mean of `d`.

    var(mean d) = (gamma_0 + 2 * sum_{k=1}^{lag} w_k gamma_k) / n with Bartlett weights w_k = 1 - k/(lag+1).
    Autocovariances use the 1/n convention; a negative estimate (possible in tiny samples) is floored at 0.
    """
    x = _arr(d)
    n = len(x)
    if n == 0:
        return float("nan")
    xc = x - x.mean()
    gamma0 = float(np.dot(xc, xc) / n)
    s = gamma0
    for k in range(1, min(lag, n - 1) + 1):
        gk = float(np.dot(xc[k:], xc[:-k]) / n)
        s += 2.0 * (1.0 - k / (lag + 1.0)) * gk
    return max(s, 0.0) / n


def diebold_mariano(
    loss_a: ArrayLike,
    loss_b: ArrayLike,
    h: int,
    *,
    alternative: str = "two-sided",
    hln: bool = True,
) -> tuple[float, float]:
    """Diebold-Mariano (1995) test of equal predictive accuracy between two loss series.

    d_t = loss_a_t - loss_b_t; DM = mean(d) / sqrt(NW variance with lag h-1), where h is the forecast horizon
    (h-step-ahead errors are at most MA(h-1)). With `hln=True` the Harvey-Leybourne-Newbold (1997) small-sample
    correction is applied: DM* = DM * sqrt((n + 1 - 2h + h(h-1)/n) / n) compared with a Student-t(n-1);
    otherwise DM is compared with the standard normal.

    alternative: "two-sided" (losses differ), "less" (a is better: mean d < 0), "greater" (b is better).
    Returns (statistic, p_value). Identical losses -> (0.0, 1.0). If the losses differ by a constant (zero
    variance) the statistic is +-inf and the p-value is 0 or 1 according to the alternative. Fewer than 2
    observations -> (nan, nan).

    References: Diebold, F.X. & Mariano, R.S. (1995) J. Business & Economic Statistics 13(3);
    Harvey, D., Leybourne, S. & Newbold, P. (1997) Int. J. Forecasting 13(2); Newey & West (1987) Econometrica 55.
    """
    if alternative not in {"two-sided", "less", "greater"}:
        raise ValueError("alternative must be 'two-sided', 'less' or 'greater'")
    a, b = _arr(loss_a), _arr(loss_b)
    if len(a) != len(b):
        raise ValueError("loss_a and loss_b must have the same length")
    d = a - b
    d = d[np.isfinite(d)]
    n = len(d)
    if n < 2:
        return float("nan"), float("nan")
    mean = float(d.mean())
    var = newey_west_variance(d, max(int(h) - 1, 0))
    if var <= 0.0 or not np.isfinite(var):
        if abs(mean) < 1e-15:
            return 0.0, 1.0
        stat_inf = math.inf if mean > 0 else -math.inf
        if alternative == "two-sided":
            return stat_inf, 0.0
        if alternative == "less":
            return stat_inf, 0.0 if mean < 0 else 1.0
        return stat_inf, 0.0 if mean > 0 else 1.0
    stat = mean / math.sqrt(var)
    if hln:
        k = (n + 1 - 2 * h + h * (h - 1) / n) / n
        stat *= math.sqrt(max(k, 1e-12))
        dist: Any = stats.t(df=n - 1)
    else:
        dist = stats.norm()
    cdf = float(dist.cdf(stat))
    if alternative == "two-sided":
        p = 2.0 * min(cdf, 1.0 - cdf)
    elif alternative == "less":
        p = cdf
    else:
        p = 1.0 - cdf
    return float(stat), float(min(max(p, 0.0), 1.0))


def directional_accuracy(pred_up: ArrayLike, actual_up: ArrayLike) -> tuple[float, int, float]:
    """Hit rate of the predicted direction with an exact two-sided binomial test against p = 0.5.

    Returns (hit_rate, n, p_value). The p-value is scipy's exact two-sided binomial test (sum of the
    probabilities of outcomes at least as extreme as the observed count, Clopper-Pearson style); 60 hits out
    of 100 gives p = 0.0569. n == 0 -> (nan, 0, nan).
    """
    p_arr = np.asarray(pred_up, dtype=bool).reshape(-1)
    a_arr = np.asarray(actual_up, dtype=bool).reshape(-1)
    if len(p_arr) != len(a_arr):
        raise ValueError("pred_up and actual_up must have the same length")
    n = len(p_arr)
    if n == 0:
        return float("nan"), 0, float("nan")
    hits = int(np.sum(p_arr == a_arr))
    p_value = float(stats.binomtest(hits, n, 0.5, alternative="two-sided").pvalue)
    return hits / n, n, p_value


def _pinball_elementwise(q_pred: np.ndarray, actual: np.ndarray, alpha: float) -> np.ndarray:
    diff = actual - q_pred
    return np.maximum(alpha * diff, (alpha - 1.0) * diff)


def pinball_loss(q_pred: ArrayLike | float, actual: ArrayLike | float, alpha: float) -> float:
    """Mean pinball (quantile) loss of the alpha-quantile forecast q_pred against `actual`.

    rho_alpha(q, y) = alpha (y - q) if y >= q else (alpha - 1)(y - q) = (1 - alpha)(q - y).
    At alpha = 0.5 it equals half the absolute error. Koenker, R. & Bassett, G. (1978) Econometrica 46(1).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    q, y = _arr(q_pred), _arr(actual)
    if len(q) != len(y):
        raise ValueError("q_pred and actual must have the same length")
    if len(q) == 0:
        return float("nan")
    return float(np.mean(_pinball_elementwise(q, y, alpha)))


def crps_from_quantiles(quantiles: dict[float, float], actual: float) -> float:
    """Approximate CRPS from a finite set of quantile levels.

    Exact identity: CRPS(F, y) = 2 * integral_0^1 rho_alpha(F^{-1}(alpha), y) d(alpha), where rho_alpha is the
    pinball loss (Laio & Tamea 2007; Gneiting & Ranjan 2011). With only the levels in `quantiles` (here
    0.05/0.25/0.5/0.75/0.95) the integral is approximated by the trapezoidal rule over [alpha_min, alpha_max].

    Approximation (document for the user as approx=True): (i) the pinball loss is assumed piecewise linear in
    alpha between the available levels; (ii) the tails [0, alpha_min] and [alpha_max, 1] are NOT integrated, so
    the value is a lower bound of the true CRPS and under-penalises realisations far outside the 5-95 range.
    Models are only compared on the same set of levels, so the ranking is unaffected. Units: price (USD/bbl).
    Non-monotone quantiles are sorted before integration. Fewer than 2 finite levels -> NaN.
    """
    pts = sorted((float(a), float(q)) for a, q in quantiles.items() if np.isfinite(q) and 0.0 < float(a) < 1.0)
    if len(pts) < 2 or not np.isfinite(actual):
        return float("nan")
    alphas = np.array([a for a, _ in pts])
    qs = np.sort(np.array([q for _, q in pts]))  # enforce a monotone quantile function
    y = float(actual)
    losses = np.array([float(_pinball_elementwise(np.array([q]), np.array([y]), a)[0]) for a, q in zip(alphas, qs)])
    integral = float(np.trapezoid(losses, alphas))
    return 2.0 * integral


def coverage(q_low: ArrayLike, q_high: ArrayLike, actual: ArrayLike) -> float:
    """Empirical coverage: fraction of actuals inside the closed interval [q_low, q_high]. n == 0 -> NaN."""
    lo, hi, y = _arr(q_low), _arr(q_high), _arr(actual)
    if not (len(lo) == len(hi) == len(y)):
        raise ValueError("q_low, q_high and actual must have the same length")
    if len(y) == 0:
        return float("nan")
    inside = (y >= np.minimum(lo, hi)) & (y <= np.maximum(lo, hi))
    return float(np.mean(inside))


# --------------------------------------------------------------------------------------
# alignment of forecasts with realised settlements
# --------------------------------------------------------------------------------------
def as_trading_date(asof: datetime | date | pd.Timestamp | str) -> date:
    """Trading date (London) of a forecast timestamp. Naive datetimes are UTC; dates pass through."""
    if isinstance(asof, pd.Timestamp):
        if asof.tzinfo is None:
            return asof.date()
        return asof.tz_convert(LONDON).date()
    if isinstance(asof, datetime):
        return london_date(ensure_utc(asof))
    if isinstance(asof, date):
        return asof
    return as_trading_date(pd.Timestamp(asof))


def target_trading_date(asof: datetime | date | pd.Timestamp, horizon: str) -> date:
    """ICE business date `h` trading days after the forecast's trading date."""
    if horizon not in HORIZONS:
        raise ValueError(f"unknown horizon {horizon!r}; expected one of {sorted(HORIZONS)}")
    return add_business_days(as_trading_date(asof), HORIZONS[horizon], "ICE")


def normalize_price_index(prices: pd.Series) -> pd.Series:
    """Return `prices` indexed by tz-naive midnight Timestamps (London trading dates), sorted, NaN dropped."""
    s = prices.dropna()
    idx = pd.DatetimeIndex(pd.to_datetime(s.index))
    if idx.tz is not None:
        idx = idx.tz_convert(LONDON).tz_localize(None)
    out = pd.Series(s.to_numpy(dtype=float), index=idx.normalize(), name=prices.name)
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def realised_price(prices: pd.Series, target: date, tolerance_days: int = 3) -> tuple[float, date] | None:
    """First known settlement at or after `target` within `tolerance_days` calendar days, or None (pending).

    `prices` must be normalised (see normalize_price_index). Returns (price, realised_date).
    """
    if prices.empty:
        return None
    t = pd.Timestamp(target)
    pos = int(prices.index.searchsorted(t, side="left"))
    if pos >= len(prices):
        return None
    d = prices.index[pos]
    if (d - t).days > tolerance_days:
        return None
    return float(prices.iloc[pos]), pd.Timestamp(d).date()


FRAME_COLUMNS = [
    "model",
    "horizon",
    "asof",
    "asof_date",
    "target_date",
    "realised_date",
    "price_now",
    "median",
    "q05",
    "q25",
    "q75",
    "q95",
    "p_up",
    "expected_vol",
    "approx",
    "realised",
    "realised_approx",
]


def align_forecasts(
    forecasts: Iterable[ForecastQuantiles], actuals: pd.Series, tolerance_days: int = 3
) -> pd.DataFrame:
    """Tidy frame: one row per forecast with its target date and the realised settlement (NaN while pending)."""
    px = normalize_price_index(actuals) if len(actuals) else pd.Series(dtype=float)
    rows: list[dict[str, Any]] = []
    for f in forecasts:
        asof_date = as_trading_date(f.asof)
        target = target_trading_date(f.asof, f.horizon)
        hit = realised_price(px, target, tolerance_days) if len(px) else None
        rows.append(
            {
                "model": f.model,
                "horizon": f.horizon,
                "asof": ensure_utc(f.asof),
                "asof_date": pd.Timestamp(asof_date),
                "target_date": pd.Timestamp(target),
                "realised_date": pd.Timestamp(hit[1]) if hit else pd.NaT,
                "price_now": float(f.price_now),
                "median": float(f.median),
                "q05": float(f.q05),
                "q25": float(f.q25),
                "q75": float(f.q75),
                "q95": float(f.q95),
                "p_up": float(f.p_up) if f.p_up is not None else float("nan"),
                "expected_vol": float(f.expected_vol) if f.expected_vol is not None else float("nan"),
                "approx": bool(f.approx),
                "realised": hit[0] if hit else float("nan"),
                "realised_approx": bool(hit and hit[1] != target),
            }
        )
    df = pd.DataFrame(rows, columns=FRAME_COLUMNS)
    if df.empty:
        return df
    return score_frame(df)


def _pred_up(row: pd.Series) -> float:
    """1.0 up, 0.0 down, NaN undefined. p_up is the explicit view; the median is the fallback."""
    p = row["p_up"]
    if np.isfinite(p) and p != 0.5:
        return 1.0 if p > 0.5 else 0.0
    if row["median"] > row["price_now"]:
        return 1.0
    if row["median"] < row["price_now"]:
        return 0.0
    return float("nan")


def score_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Add per-forecast scores (errors vs realised and vs the no-change forecast, CRPS, interval hits)."""
    out = df.copy()
    y = out["realised"].to_numpy(dtype=float)
    p0 = out["price_now"].to_numpy(dtype=float)
    med = out["median"].to_numpy(dtype=float)
    out["err"] = med - y
    out["rel_err"] = (med - y) / p0
    out["rw_err"] = p0 - y
    out["rw_rel_err"] = (p0 - y) / p0
    out["abs_err"] = np.abs(out["err"])
    out["sq_rel_err"] = out["rel_err"] ** 2
    out["rw_sq_rel_err"] = out["rw_rel_err"] ** 2
    out["pred_up"] = out.apply(_pred_up, axis=1) if len(out) else pd.Series(dtype=float)
    actual_up = np.where(y > p0, 1.0, np.where(y < p0, 0.0, np.nan))
    out["actual_up"] = actual_up
    out["hit"] = np.where(
        np.isfinite(out["pred_up"].to_numpy(dtype=float)) & np.isfinite(actual_up),
        (out["pred_up"].to_numpy(dtype=float) == actual_up).astype(float),
        np.nan,
    )
    q05, q25, q75, q95 = (out[c].to_numpy(dtype=float) for c in ("q05", "q25", "q75", "q95"))
    out["in_50"] = np.where(np.isfinite(y), ((y >= q25) & (y <= q75)).astype(float), np.nan)
    out["in_90"] = np.where(np.isfinite(y), ((y >= q05) & (y <= q95)).astype(float), np.nan)
    out["crps"] = [
        crps_from_quantiles({a: float(r[c]) for a, c in QUANTILE_FIELDS.items()}, float(r["realised"]))
        for _, r in out.iterrows()
    ]
    out["crps_rel"] = out["crps"] / out["price_now"]
    return out


# --------------------------------------------------------------------------------------
# aggregate report
# --------------------------------------------------------------------------------------
def _nan_to_none(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x)


def _metrics_for_group(g: pd.DataFrame, horizon: str, rw_rows: pd.DataFrame | None) -> dict[str, Any]:
    g = g[np.isfinite(g["realised"].to_numpy(dtype=float))]
    n = len(g)
    h = HORIZONS.get(horizon, 1)
    y = g["realised"].to_numpy(dtype=float)
    p0 = g["price_now"].to_numpy(dtype=float)
    med = g["median"].to_numpy(dtype=float)
    out: dict[str, Any] = {"n": n, "horizon_days": h}
    if n == 0:
        out.update(
            {
                "theil_u": None,
                "dm_stat": None,
                "dm_p_one_sided": None,
                "dm_p_two_sided": None,
                "rmse": None,
                "rmse_rw": None,
                "mae": None,
                "mae_rw": None,
                "dir_hit_rate": None,
                "dir_n": 0,
                "dir_p_value": None,
                "crps": None,
                "crps_rel": None,
                "crps_approx": True,
                "crps_skill_vs_rw": None,
                "n_vs_rw": 0,
                "pinball": {str(a): None for a in QUANTILE_FIELDS},
                "coverage_50": None,
                "coverage_90": None,
                "approx": False,
                "realised_approx_n": 0,
                "beats_rw": False,
                "verdict": VERDICT_INSUFFICIENT,
            }
        )
        return out
    rmse = float(np.sqrt(np.mean((med - y) ** 2)))
    rmse_rw = float(np.sqrt(np.mean((p0 - y) ** 2)))
    out["rmse"], out["rmse_rw"] = rmse, rmse_rw
    out["mae"], out["mae_rw"] = float(np.mean(np.abs(med - y))), float(np.mean(np.abs(p0 - y)))
    out["theil_u"] = _nan_to_none(theil_u(med, y, p0, scale=p0))
    if n >= MIN_N_FOR_TESTS:
        la, lb = g["sq_rel_err"].to_numpy(dtype=float), g["rw_sq_rel_err"].to_numpy(dtype=float)
        stat, p2 = diebold_mariano(la, lb, h, alternative="two-sided")
        _, p1 = diebold_mariano(la, lb, h, alternative="less")
        out["dm_stat"], out["dm_p_one_sided"], out["dm_p_two_sided"] = (
            _nan_to_none(stat),
            _nan_to_none(p1),
            _nan_to_none(p2),
        )
    else:
        out["dm_stat"] = out["dm_p_one_sided"] = out["dm_p_two_sided"] = None
    mask = np.isfinite(g["pred_up"].to_numpy(dtype=float)) & np.isfinite(g["actual_up"].to_numpy(dtype=float))
    hit_rate, dir_n, dir_p = directional_accuracy(
        g["pred_up"].to_numpy(dtype=float)[mask] > 0.5, g["actual_up"].to_numpy(dtype=float)[mask] > 0.5
    )
    out["dir_hit_rate"], out["dir_n"], out["dir_p_value"] = _nan_to_none(hit_rate), dir_n, _nan_to_none(dir_p)
    out["crps"] = _nan_to_none(float(np.nanmean(g["crps"].to_numpy(dtype=float))))
    out["crps_rel"] = _nan_to_none(float(np.nanmean(g["crps_rel"].to_numpy(dtype=float))))
    out["crps_approx"] = True
    skill, n_vs = None, 0
    if rw_rows is not None and not rw_rows.empty:
        m = g[["asof_date", "crps"]].merge(rw_rows[["asof_date", "crps"]], on="asof_date", suffixes=("", "_rw"))
        m = m.dropna()
        n_vs = len(m)
        if n_vs and float(m["crps_rw"].mean()) > 0:
            skill = float(1.0 - m["crps"].mean() / m["crps_rw"].mean())
    out["crps_skill_vs_rw"], out["n_vs_rw"] = skill, n_vs
    out["pinball"] = {
        str(a): _nan_to_none(pinball_loss(g[c].to_numpy(dtype=float), y, a)) for a, c in QUANTILE_FIELDS.items()
    }
    out["coverage_50"] = _nan_to_none(coverage(g["q25"].to_numpy(dtype=float), g["q75"].to_numpy(dtype=float), y))
    out["coverage_90"] = _nan_to_none(coverage(g["q05"].to_numpy(dtype=float), g["q95"].to_numpy(dtype=float), y))
    out["approx"] = bool(g["approx"].any() or g["realised_approx"].any())
    out["realised_approx_n"] = int(g["realised_approx"].sum())
    out["beats_rw"] = _beats(out)
    out["verdict"] = _verdict(out)
    return out


def _beats(m: dict[str, Any]) -> bool:
    u, p = m.get("theil_u"), m.get("dm_p_one_sided")
    if u is None or p is None:
        return False
    return bool(u < BEATS_RW_THEIL_MAX and p < BEATS_RW_DM_P_MAX)


def _verdict(m: dict[str, Any]) -> str:
    if m.get("n", 0) < MIN_N_FOR_TESTS or m.get("dm_p_one_sided") is None:
        return VERDICT_INSUFFICIENT
    return VERDICT_BEATS_RW if m.get("beats_rw") else VERDICT_NOT_BEATS_RW


def resolve_rw_model(models: Iterable[str], rw_model: str = "rw") -> str | None:
    """Name of the random-walk forecaster present among `models`: `rw_model` first, then the known aliases."""
    present = set(models)
    for name in (rw_model, *RW_MODEL_ALIASES):
        if name in present:
            return name
    return None


def evaluate_frame(df: pd.DataFrame, rw_model: str = "rw") -> dict[str, dict[str, dict[str, Any]]]:
    """Report {model: {horizon: metrics}} from an aligned, scored frame (see align_forecasts)."""
    report: dict[str, dict[str, dict[str, Any]]] = {}
    if df.empty:
        return report
    resolved = df[np.isfinite(df["realised"].to_numpy(dtype=float))]
    rw_name = resolve_rw_model(resolved["model"].astype(str).unique(), rw_model)
    for (model, horizon), g in resolved.groupby(["model", "horizon"], sort=True):
        rw_rows = None
        if rw_name is not None and model != rw_name:
            rw_rows = resolved[(resolved["model"] == rw_name) & (resolved["horizon"] == horizon)]
        report.setdefault(str(model), {})[str(horizon)] = _metrics_for_group(g, str(horizon), rw_rows)
    return report


def evaluate(
    forecasts: list[ForecastQuantiles], actuals: pd.Series, tolerance_days: int = 3, rw_model: str = "rw"
) -> dict[str, dict[str, dict[str, Any]]]:
    """Aggregate out-of-sample report per model per horizon versus the random walk.

    `actuals`: realised front settlements indexed by trading date. For every model and horizon:
    n, Theil U (relative errors), DM statistic and p-values (one-sided "model better", two-sided) on squared
    relative errors vs the no-change forecast, directional hit rate with binomial p-value, mean CRPS
    (approximate, see crps_from_quantiles) with skill vs the `rw_model` forecasts on common dates, pinball
    loss by alpha, coverage of the 50% and 90% intervals, approx flags and the Italian verdict.
    """
    return evaluate_frame(align_forecasts(forecasts, actuals, tolerance_days), rw_model=rw_model)


def beats_random_walk(report: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, bool]]:
    """Per model and horizon: Theil U < 1 AND one-sided DM p < 0.10. Anything else (including too few
    observations) is False and the dashboard shows "non batte il random walk"."""
    return {model: {h: _beats(m) for h, m in by_h.items()} for model, by_h in report.items()}


def summary_table(report: dict[str, dict[str, dict[str, Any]]]) -> pd.DataFrame:
    """Flat table of the report (one row per model/horizon) for notebooks and the site export."""
    rows = []
    for model, by_h in report.items():
        for h, m in by_h.items():
            r = {"model": model, "horizon": h}
            r.update({k: v for k, v in m.items() if k != "pinball"})
            for a, v in m.get("pinball", {}).items():
                r[f"pinball_{a}"] = v
            rows.append(r)
    return pd.DataFrame(rows)
