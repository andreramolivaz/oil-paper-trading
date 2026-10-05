"""Bayesian online change-point detection (Adams & MacKay, 2007) for structural breaks in the Brent series.

Model
-----
Run-length posterior P(r_t | x_1..x_t) with a constant hazard H = 1/250 (one structural break per trading
year, a priori) and a **Normal-Gamma** conjugate prior, i.e. BOTH the mean and the variance of the segment
are unknown. That matters here: the 2026-02-28 shock changed the mean AND multiplied the variance, and a
known-variance detector either misses the first days or screams for weeks.

Per run length r the sufficient statistics are (mu_r, kappa_r, alpha_r, beta_r) and the one-step predictive
is a Student-t with 2*alpha_r degrees of freedom::

    x | r ~ t_{2 alpha_r}(mu_r, beta_r (kappa_r + 1) / (alpha_r kappa_r))

Because kappa_r = kappa0 + r and alpha_r = alpha0 + r/2 depend only on the run length, the gamma-function
part of the log predictive is a constant vector computed once, which is what makes the filter fast
(10 000 days in well under a second: one vector operation of length <= 500 per day).

Recursion::

    growth[r+1] = P(r)  * pred[r] * (1 - H)
    cp          = sum_r P(r) * pred[r] * H
    P_new       = normalise([cp, growth...])

truncated at `run_length_max` run lengths (default 500): the tail mass is folded into the last bucket, which
keeps the distribution proper and is indistinguishable from the untruncated filter for a hazard of 1/250.

Output
------
``BOCPD_CP_PROB = P(run length < 5)`` per day -- "the current segment started within the last 5 days".
Causal by construction: the value for day t is a filtering quantity using x_1..x_t only. Standardisation of
the input is **expanding** (trailing mean/std), never full sample, for the same reason.

Two channels
------------
A break in Brent shows up either as a level shift of the daily return (a price shock) or as a level shift of
the realised-volatility *changes* (a volatility regime change with no clear direction, e.g. the slide into
the 2014-2015 glut). The two channels are run separately and combined with ``max``: the consumer of this
number uses it to cut risk and to declare "transizione", and for that purpose either kind of break must
fire. ``max`` (rather than a mean) keeps the detector sensitive; the cost is a slightly higher false-positive
rate on quiet stretches, which the 0.5 threshold absorbs.

Warm-up: at t = 0 the run length IS 0, so the first few values are mechanically close to 1. Callers should
ignore the first ~`warmup` observations (the models in this package do).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.special import gammaln

from engine.features import catalog as cat

F64 = npt.NDArray[np.float64]

DEFAULT_HAZARD = 1.0 / 250.0
DEFAULT_RUN_LENGTH_MAX = 500
DEFAULT_SHORT_RUN = 5  # P(run length < 5)
# Normal-Gamma prior on standardised data: mean 0, weak confidence, precision centred on 1.
PRIOR_MU = 0.0
PRIOR_KAPPA = 1.0
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0
STD_MIN_PERIODS = 20


def expanding_standardise(s: pd.Series, min_periods: int = STD_MIN_PERIODS) -> pd.Series:
    """Causal standardisation: trailing expanding mean/std, zero where not yet defined."""
    x = pd.to_numeric(s, errors="coerce").astype("float64")
    mu = x.expanding(min_periods=min_periods).mean()
    sd = x.expanding(min_periods=min_periods).std(ddof=0)
    z = (x - mu) / sd.where(sd > 1e-12)
    return z.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def bocpd_run_length(
    x: F64,
    hazard: float = DEFAULT_HAZARD,
    run_length_max: int = DEFAULT_RUN_LENGTH_MAX,
    short_run: int = DEFAULT_SHORT_RUN,
) -> F64:
    """P(run length < `short_run`) per observation of the already standardised 1-d array `x`."""
    n = x.shape[0]
    out = np.zeros(n, dtype="float64")
    if n == 0:
        return out
    r_max = max(int(run_length_max), short_run + 1)
    k = min(r_max, n + 1)

    # Constant part of the Student-t log predictive, indexed by run length r = 0..k-1.
    r = np.arange(k, dtype="float64")
    kappa = PRIOR_KAPPA + r
    alpha = PRIOR_ALPHA + 0.5 * r
    const = gammaln(alpha + 0.5) - gammaln(alpha) - 0.5 * np.log(2.0 * np.pi * alpha)

    mu = np.full(k, PRIOR_MU, dtype="float64")
    beta = np.full(k, PRIOR_BETA, dtype="float64")
    probs = np.zeros(k, dtype="float64")
    probs[0] = 1.0
    h = float(hazard)
    one_minus_h = 1.0 - h

    for t in range(n):
        xt = float(x[t])
        var = beta * (kappa + 1.0) / (alpha * kappa)
        z2 = (xt - mu) ** 2 / var
        logpred = const - 0.5 * np.log(var) - (alpha + 0.5) * np.log1p(z2 / (2.0 * alpha))

        with np.errstate(divide="ignore"):
            logp = np.log(probs)
        joint = logp + logpred
        m = float(np.max(joint[np.isfinite(joint)], initial=-np.inf))
        if not np.isfinite(m):  # pragma: no cover - defensive
            probs[:] = 0.0
            probs[0] = 1.0
            out[t] = 1.0
            continue
        w = np.exp(joint - m)  # unnormalised P(r) * pred(r)

        new = np.zeros(k, dtype="float64")
        new[0] = h * w.sum()
        new[1:] = one_minus_h * w[:-1]
        # fold the truncated tail back into the last bucket so the distribution stays proper
        new[-1] += one_minus_h * w[-1]
        total = new.sum()
        if total > 0:
            probs = new / total
        else:  # pragma: no cover - defensive
            probs = np.zeros(k, dtype="float64")
            probs[0] = 1.0
        out[t] = float(probs[:short_run].sum())

        # Normal-Gamma sufficient statistics shifted by one run length (prior re-seeded at r = 0).
        mu_new = np.empty(k, dtype="float64")
        beta_new = np.empty(k, dtype="float64")
        mu_new[0] = PRIOR_MU
        beta_new[0] = PRIOR_BETA
        mu_new[1:] = (kappa[:-1] * mu[:-1] + xt) / (kappa[:-1] + 1.0)
        beta_new[1:] = beta[:-1] + kappa[:-1] * (xt - mu[:-1]) ** 2 / (2.0 * (kappa[:-1] + 1.0))
        mu, beta = mu_new, beta_new

    return out


def bocpd_series(
    values: pd.Series,
    hazard: float = DEFAULT_HAZARD,
    run_length_max: int = DEFAULT_RUN_LENGTH_MAX,
    short_run: int = DEFAULT_SHORT_RUN,
    standardise: bool = True,
) -> pd.Series:
    """Run the detector on one channel and return the change-point probability per date."""
    s = expanding_standardise(values) if standardise else pd.to_numeric(values, errors="coerce").fillna(0.0)
    arr = bocpd_run_length(s.to_numpy(dtype="float64"), hazard, run_length_max, short_run)
    return pd.Series(arr, index=values.index, name=cat.BOCPD_CP_PROB, dtype="float64")


def _return_channel(features: pd.DataFrame) -> pd.Series | None:
    if cat.RET_1 in features.columns and features[cat.RET_1].notna().any():
        return features[cat.RET_1]
    if cat.PX in features.columns and features[cat.PX].notna().any():
        px = pd.to_numeric(features[cat.PX], errors="coerce").astype("float64")
        return np.log(px.where(px > 0)).diff()
    if cat.RET_21 in features.columns and features[cat.RET_21].notna().any():
        return features[cat.RET_21].diff()
    return None


def _vol_channel(features: pd.DataFrame) -> pd.Series | None:
    if cat.RV_YZ_21 in features.columns and features[cat.RV_YZ_21].notna().any():
        rv = pd.to_numeric(features[cat.RV_YZ_21], errors="coerce").astype("float64")
        return np.log(rv.where(rv > 0)).diff()
    return None


def change_point_probability(
    features: pd.DataFrame,
    hazard: float = DEFAULT_HAZARD,
    run_length_max: int = DEFAULT_RUN_LENGTH_MAX,
    short_run: int = DEFAULT_SHORT_RUN,
    use_vol_channel: bool = True,
) -> pd.Series:
    """`BOCPD_CP_PROB` per date: max over the return channel and (optionally) the RV-change channel."""
    out = pd.Series(0.0, index=features.index, name=cat.BOCPD_CP_PROB, dtype="float64")
    channels: list[pd.Series] = []
    ret = _return_channel(features)
    if ret is not None:
        channels.append(bocpd_series(ret.fillna(0.0), hazard, run_length_max, short_run))
    if use_vol_channel:
        vol = _vol_channel(features)
        if vol is not None:
            channels.append(bocpd_series(vol.fillna(0.0), hazard, run_length_max, short_run))
    for ch in channels:
        out = out.combine(ch, max)
    return out
