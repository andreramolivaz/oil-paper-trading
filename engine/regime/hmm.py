"""Walk-forward Gaussian HMM over the regime feature block, with a CAUSAL forward filter.

Why not `hmmlearn.predict_proba`
-------------------------------
`predict_proba` runs the forward-BACKWARD recursion, i.e. it smooths each day with the whole sample,
including the future. Used in a backtest it leaks information: the probability printed for 2020-03-05 would
already know about 2020-03-09. This module therefore only borrows the *estimation* step from hmmlearn and
implements the filtering recursion itself from the fitted parameters
(`startprob_`, `transmat_`, `means_`, `covars_`)::

    alpha_t(j) ∝ P(x_t | state=j) * sum_i transmat[i, j] * alpha_{t-1}(i)

renormalised at every step, with the emission log-densities shifted by their row maximum before they are
exponentiated, so nothing underflows even over 9 000 days. The posterior reported for day t therefore uses
rows <= t only.

Why `covariance_type="diag"`
----------------------------
A full covariance matrix costs k * d * (d + 1) / 2 parameters: with d = 7 features and k = 5 states that is
140 covariance parameters on a training window of ~750 observations, which overfits badly (and regularly
produces non positive definite matrices on the oil features, which are strongly collinear in a shock).
A diagonal covariance costs k * d = 35, keeps EM stable, and the information we need from the model is the
*location* of each state in feature space (its mean), not the shape of its correlation.

Walk-forward protocol
---------------------
* expanding training window, minimum `min_obs` observations (default 750 ≈ 3 years);
* refit every `refit_every` trading days (default 63 ≈ one quarter);
* between two refits only the forward filter runs, with frozen parameters;
* the number of states is re-selected by BIC over `n_states_range` (default 3..5) at every refit;
* winsorisation at the 1/99 percentiles and standardisation use the TRAINING window only, never the sample;
* across refits the new states are matched to the previous fit's states by nearest standardised mean
  (`scipy.optimize.linear_sum_assignment`) so that a canonical state id keeps its meaning through time.

Warm-up honesty: the rows of the very first training window are necessarily labelled by a model fitted on
them (there is no earlier fit), so they are in-sample. They are flagged in `HmmResult.warmup` and callers
should drop them when scoring. Every row after the first fit date is strictly out-of-sample.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.optimize import linear_sum_assignment

from engine.features import catalog as cat

log = logging.getLogger(__name__)


@contextlib.contextmanager
def _single_threaded() -> Iterator[None]:
    """Pin the BLAS/OpenMP pools to one thread while EM runs.

    Threaded reductions sum in a scheduling-dependent order, so the same training window can produce fitted
    means that differ in the last bits from one run to the next. Those last bits propagate into the
    posteriors, which would make the no-look-ahead test ("appending future rows changes nothing in the
    past") fail for a reason that has nothing to do with look-ahead. One thread makes EM bit-reproducible;
    the windows are small (<= a few thousand rows x 7 features) so nothing is lost in speed.
    """
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:  # pragma: no cover - threadpoolctl ships with scikit-learn
        yield
        return
    with threadpool_limits(limits=1):
        yield


F64 = npt.NDArray[np.float64]

# Feature block for the regime model, in this exact order (brief §7).
REGIME_FEATURES: list[str] = [
    cat.RET_21,
    cat.RV_YZ_21,
    cat.SLOPE_M1_M6,
    cat.VRP,
    cat.TSMOM_63,
    cat.GEO_INDEX,
    cat.CRUDE_STOCKS_VS_5Y,
]
# The curve slope has a documented fallback: M1-M6 is unavailable on the WTI proxy curve, M1-M2 is not.
SLOPE_PRIMARY = cat.SLOPE_M1_M6
SLOPE_FALLBACK = cat.SLOPE_M1_M2

DEFAULT_MIN_OBS = 750
DEFAULT_REFIT_EVERY = 63
DEFAULT_N_STATES = (3, 4, 5)
DEFAULT_RANDOM_STATE = 20260305
DEFAULT_N_ITER = 80
DEFAULT_TOL = 1e-3
WINSOR_LO = 1.0
WINSOR_HI = 99.0


@dataclass
class HmmFit:
    """Frozen parameters of one refit, plus everything needed to transform and label new rows."""

    fit_index: int  # positional index of the last training row
    fit_date: pd.Timestamp
    feature_names: list[str]
    median: F64  # training-window median, used to impute isolated NaNs
    lo: F64  # winsorisation bounds (training window)
    hi: F64
    center: F64  # standardisation moments (training window, post winsorisation)
    scale: F64
    startprob: F64
    transmat: F64
    means: F64  # (k, d) standardised means, rows in FITTED state order
    variances: F64  # (k, d) diagonal covariances
    loglik: float
    bic: float
    canonical_ids: list[int]  # fitted state i -> canonical (time stable) state id
    slope_missing: bool
    n_train: int

    @property
    def n_states(self) -> int:
        return int(self.means.shape[0])

    def means_by_canonical(self) -> dict[int, F64]:
        return {cid: self.means[i] for i, cid in enumerate(self.canonical_ids)}

    def transform(self, raw: F64) -> F64:
        """Impute -> winsorise -> standardise a (T, d) raw block with the FROZEN training moments."""
        x = np.array(raw, dtype="float64", copy=True)
        bad = ~np.isfinite(x)
        if bad.any():
            x[bad] = np.broadcast_to(self.median, x.shape)[bad]
        np.clip(x, self.lo, self.hi, out=x)
        return (x - self.center) / self.scale

    def log_emission(self, z: F64) -> F64:
        """Log N(z_t; mean_j, diag(var_j)) for a (T, d) standardised block -> (T, k)."""
        inv = 1.0 / self.variances  # (k, d)
        const = -0.5 * np.sum(np.log(2.0 * np.pi * self.variances), axis=1)  # (k,)
        diff = z[:, None, :] - self.means[None, :, :]  # (T, k, d)
        # Explicit reduction over the feature axis rather than `einsum`: einsum picks its blocking from the
        # array shape, so the same row would reduce in a different order in a longer block and the result
        # could differ in the last bits. Here every (t, k) cell is reduced on its own.
        quad = np.sum(diff * diff * inv[None, :, :], axis=2)  # (T, k)
        return const[None, :] - 0.5 * quad


@dataclass
class HmmResult:
    """Walk-forward output. `posteriors` columns are canonical state ids."""

    posteriors: pd.DataFrame
    fit_date: pd.Series  # per row: fit date of the parameters that produced it
    n_states: pd.Series  # per row: number of states of the active fit
    warmup: pd.Series  # per row: True while the row is inside the first training window
    fits: list[HmmFit] = field(default_factory=list)
    features_used: list[str] = field(default_factory=list)
    slope_missing: bool = False

    def fit_at(self, ts: pd.Timestamp) -> HmmFit | None:
        """The fit that was active at `ts` (the most recent one whose fit date is <= ts)."""
        active: HmmFit | None = None
        for f in self.fits:
            if f.fit_date <= ts:
                active = f
        return active or (self.fits[0] if self.fits else None)


class WalkForwardHmm:
    """Expanding-window Gaussian HMM with a causal forward filter and time-stable state ids."""

    def __init__(
        self,
        min_obs: int = DEFAULT_MIN_OBS,
        refit_every: int = DEFAULT_REFIT_EVERY,
        n_states_range: tuple[int, ...] = DEFAULT_N_STATES,
        random_state: int = DEFAULT_RANDOM_STATE,
        n_iter: int = DEFAULT_N_ITER,
        tol: float = DEFAULT_TOL,
    ) -> None:
        if min_obs < 50:
            raise ValueError("min_obs must be at least 50 observations")
        if refit_every < 1:
            raise ValueError("refit_every must be >= 1")
        self.min_obs = int(min_obs)
        self.refit_every = int(refit_every)
        self.n_states_range = tuple(int(k) for k in n_states_range)
        self.random_state = int(random_state)
        self.n_iter = int(n_iter)
        self.tol = float(tol)
        # Cache of fitted parameters keyed by (fit position, training-data digest): an expanding window
        # starting at row 0 never changes when rows are appended, so a cached fit stays exactly valid and
        # `update` only has to estimate the new refits.
        self.fit_cache: dict[tuple[int, str], HmmFit] = {}
        self._result: HmmResult | None = None

    # ---- public API ---------------------------------------------------------
    @property
    def result(self) -> HmmResult | None:
        return self._result

    def fit(self, features: pd.DataFrame) -> HmmResult:
        """Run (or re-run) the whole walk-forward and cache the result."""
        self._result = self._walk_forward(features)
        return self._result

    def update(self, features: pd.DataFrame) -> HmmResult:
        """Partial update: re-runs the walk-forward reusing every cached refit (same prefix => same fit)."""
        return self.fit(features)

    def posteriors(self, features: pd.DataFrame) -> pd.DataFrame:
        """Walk-forward filtered posteriors; index = dates, columns = canonical state ids."""
        return self.fit(features).posteriors

    # ---- internals ----------------------------------------------------------
    def _select_columns(self, features: pd.DataFrame) -> tuple[list[str], bool]:
        """Resolve the feature block (with the curve-slope fallback). Returns (columns, slope_missing)."""
        cols: list[str] = []
        slope_missing = False
        for name in REGIME_FEATURES:
            if name != SLOPE_PRIMARY:
                if name in features.columns:
                    cols.append(name)
                continue
            # curve slope with fallback
            if name in features.columns and features[name].notna().any():
                cols.append(name)
            elif SLOPE_FALLBACK in features.columns and features[SLOPE_FALLBACK].notna().any():
                log.info("regime: %s unavailable, falling back to %s", SLOPE_PRIMARY, SLOPE_FALLBACK)
                cols.append(SLOPE_FALLBACK)
            else:
                # Documented deviation from "use 0.0": a constant column carries no information and makes
                # the Gaussian emission degenerate, so the slope is dropped from the HMM input and the
                # state is flagged approximate. `labels.py` treats a missing feature as the neutral 0.0.
                log.warning("regime: no curve slope available (%s / %s); dropping it", SLOPE_PRIMARY, SLOPE_FALLBACK)
                slope_missing = True
        return cols, slope_missing

    @staticmethod
    def _digest(block: F64) -> str:
        return hashlib.sha1(np.ascontiguousarray(block).tobytes(), usedforsecurity=False).hexdigest()

    def _fit_once(
        self,
        raw: F64,
        columns: list[str],
        pos: int,
        fit_date: pd.Timestamp,
        slope_missing: bool,
        previous: HmmFit | None,
        next_canonical: int,
    ) -> tuple[HmmFit, int]:
        """Estimate one fit on rows[0..pos] (inclusive) and match its states to the previous fit."""
        train = raw[: pos + 1]
        digest = self._digest(train)
        key = (pos, digest)
        cached = self.fit_cache.get(key)
        if cached is not None:
            fit = self._relabel(cached, previous, next_canonical)
            return fit, max(next_canonical, max(fit.canonical_ids) + 1)

        # 1. drop columns that are entirely NaN inside the TRAINING window
        keep = [j for j in range(train.shape[1]) if np.isfinite(train[:, j]).any()]
        dropped = [columns[j] for j in range(train.shape[1]) if j not in keep]
        if dropped:
            log.debug("regime fit %s: dropping all-NaN features %s", fit_date.date(), dropped)
        names = [columns[j] for j in keep]
        tr = train[:, keep]
        # The slope can also disappear *per window*: the column exists in the frame (so `_select_columns`
        # kept it) but is empty in this training window, e.g. a curve history that only starts recently.
        # Such a fit is as approximate as one with no slope column at all.
        fit_slope_missing = slope_missing or not any(n in (SLOPE_PRIMARY, SLOPE_FALLBACK) for n in names)

        # 2. training moments (median for imputation, 1/99 winsorisation, mean/std standardisation)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            median = np.nanmedian(tr, axis=0)
        median = np.where(np.isfinite(median), median, 0.0)
        filled = np.where(np.isfinite(tr), tr, median)
        lo = np.percentile(filled, WINSOR_LO, axis=0)
        hi = np.percentile(filled, WINSOR_HI, axis=0)
        same = hi <= lo  # degenerate column: keep the raw range
        lo = np.where(same, filled.min(axis=0), lo)
        hi = np.where(same, filled.max(axis=0), hi)
        wins = np.clip(filled, lo, hi)
        center = wins.mean(axis=0)
        scale = wins.std(axis=0, ddof=0)
        scale = np.where(scale > 1e-12, scale, 1.0)
        z = (wins - center) / scale

        # 3. number of states by BIC on the training window
        best: tuple[float, int, Any] | None = None
        for k in self.n_states_range:
            model = self._estimate(z, k)
            if model is None:
                continue
            loglik = float(model.score(z))
            n_params = (k - 1) + k * (k - 1) + 2 * k * z.shape[1]
            bic = -2.0 * loglik + n_params * float(np.log(z.shape[0]))
            if best is None or bic < best[0]:
                best = (bic, k, model)
        if best is None:
            raise RuntimeError(f"no Gaussian HMM could be estimated on the window ending {fit_date.date()}")
        bic, k, model = best
        covars = np.asarray(model.covars_, dtype="float64")
        variances = np.array([np.diag(covars[i]) for i in range(k)]) if covars.ndim == 3 else covars
        variances = np.maximum(variances, 1e-6)

        fit = HmmFit(
            fit_index=pos,
            fit_date=fit_date,
            feature_names=names,
            median=median,
            lo=lo,
            hi=hi,
            center=center,
            scale=scale,
            startprob=np.asarray(model.startprob_, dtype="float64"),
            transmat=np.asarray(model.transmat_, dtype="float64"),
            means=np.asarray(model.means_, dtype="float64"),
            variances=variances,
            loglik=float(model.score(z)),
            bic=float(bic),
            canonical_ids=list(range(k)),
            slope_missing=fit_slope_missing,
            n_train=int(z.shape[0]),
        )
        self.fit_cache[key] = fit
        matched = self._relabel(fit, previous, next_canonical)
        return matched, max(next_canonical, max(matched.canonical_ids) + 1)

    def _estimate(self, z: F64, k: int) -> Any | None:
        from hmmlearn.hmm import GaussianHMM

        # hmmlearn logs convergence chatter and degenerate-transmat notices through its own logger; they are
        # expected while BIC probes k = 3..5 and would flood the GitHub Actions log.
        hl = logging.getLogger("hmmlearn")
        prev_level, prev_prop = hl.level, hl.propagate
        hl.setLevel(logging.CRITICAL)
        hl.propagate = False

        model = GaussianHMM(
            n_components=k,
            covariance_type="diag",
            n_iter=self.n_iter,
            tol=self.tol,
            random_state=self.random_state,
            init_params="stmc",
            params="stmc",
            min_covar=1e-3,
        )
        try:
            with warnings.catch_warnings(), _single_threaded():
                warnings.simplefilter("ignore")
                model.fit(z)
                score = float(model.score(z))
        except (ValueError, FloatingPointError) as exc:
            # A degenerate k (an empty state, a zero row in transmat_) is normal while probing by BIC.
            log.debug("regime: GaussianHMM(k=%d) failed to fit: %s", k, exc)
            return None
        finally:
            hl.setLevel(prev_level)
            hl.propagate = prev_prop
        if not np.isfinite(score):  # pragma: no cover - defensive
            return None
        return model

    @staticmethod
    def _relabel(fit: HmmFit, previous: HmmFit | None, next_canonical: int) -> HmmFit:
        """Assign time-stable canonical ids by matching standardised means to the previous fit."""
        import copy

        out = copy.copy(fit)
        if previous is None:
            out.canonical_ids = list(range(fit.n_states))
            return out

        shared = [n for n in fit.feature_names if n in previous.feature_names]
        new_idx = [fit.feature_names.index(n) for n in shared]
        old_idx = [previous.feature_names.index(n) for n in shared]
        prev_ids = list(previous.canonical_ids)
        if not shared:  # pragma: no cover - no overlap in features, cannot match
            out.canonical_ids = [next_canonical + i for i in range(fit.n_states)]
            return out

        a = fit.means[:, new_idx]  # (k_new, s)
        b = previous.means[:, old_idx]  # (k_old, s)
        cost = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
        rows, cols = linear_sum_assignment(cost)
        ids: list[int | None] = [None] * fit.n_states
        for r, c in zip(rows, cols):
            ids[int(r)] = prev_ids[int(c)]
        nxt = next_canonical
        for i in range(fit.n_states):
            if ids[i] is None:  # more states than the previous fit had: mint a new id
                ids[i] = nxt
                nxt += 1
        out.canonical_ids = [int(x) for x in ids]  # type: ignore[arg-type]
        return out

    @staticmethod
    def _forward_filter(fit: HmmFit, raw: F64, columns: list[str], stop: int) -> F64:
        """Causal forward recursion over rows[0..stop] -> (stop+1, k) filtered posteriors.

        Renormalised at each step; the emission log-densities are reduced by their row max before
        exponentiation so nothing underflows (the dropped scale is the usual log-likelihood scale).
        """
        idx = [columns.index(n) for n in fit.feature_names]
        z = fit.transform(raw[: stop + 1][:, idx])
        k = fit.n_states
        logb = fit.log_emission(z)  # (T, k)
        logb = logb - logb.max(axis=1, keepdims=True)  # row-wise shift: keeps the exponentials in range
        trans = fit.transmat
        out = np.empty((z.shape[0], k), dtype="float64")
        uniform = np.full(k, 1.0 / k)
        # `np.exp` is evaluated one row at a time into a fixed k-sized buffer. Vectorised over the whole
        # (T, k) block, numpy would send the elements through different SIMD lanes depending on the block
        # length, and exp is not bit-identical across those paths; a longer block (one extra refit at the
        # end of the frame) would then perturb earlier rows in the last bits, i.e. break the exact
        # no-look-ahead comparison. With k < 8 the scalar path is used and the result is reproducible.
        # +, -, * and / are exactly rounded by IEEE 754, so the rest of the recursion is already stable.
        buf = np.empty(k, dtype="float64")
        alpha = uniform
        for t in range(z.shape[0]):
            np.copyto(buf, logb[t])
            np.exp(buf, out=buf)
            prior = fit.startprob if t == 0 else trans.T @ alpha
            alpha = buf * prior
            total = alpha.sum()
            alpha = alpha / total if total > 0 else uniform
            out[t] = alpha
        return out

    def _walk_forward(self, features: pd.DataFrame) -> HmmResult:
        if not isinstance(features.index, pd.DatetimeIndex):
            raise TypeError("the feature frame must be indexed by trading dates")
        idx = features.index
        columns, slope_missing = self._select_columns(features)
        if not columns:
            raise ValueError("none of the regime features is present in the frame")

        # One causal forward-fill: a feature published before t stays valid at t; nothing is back-filled.
        raw = features[columns].astype("float64").ffill().to_numpy(dtype="float64", copy=True)
        n = raw.shape[0]

        empty = pd.DataFrame(index=idx, dtype="float64")
        if n < self.min_obs:
            log.info("regime: %d observations < min_obs=%d, no HMM fitted", n, self.min_obs)
            return HmmResult(
                posteriors=empty,
                fit_date=pd.Series(pd.NaT, index=idx, dtype="datetime64[ns]"),
                n_states=pd.Series(np.nan, index=idx, dtype="float64"),
                warmup=pd.Series(True, index=idx, dtype="bool"),
                fits=[],
                features_used=columns,
                slope_missing=slope_missing,
            )

        fit_positions = list(range(self.min_obs - 1, n, self.refit_every))
        fits: list[HmmFit] = []
        previous: HmmFit | None = None
        next_canonical = 0
        blocks: list[tuple[int, int, HmmFit]] = []  # (start, stop, fit) inclusive row range
        for i, pos in enumerate(fit_positions):
            fit, next_canonical = self._fit_once(
                raw, columns, pos, pd.Timestamp(idx[pos]), slope_missing, previous, next_canonical
            )
            fits.append(fit)
            previous = fit
            # Fit i is estimated on rows 0..pos_i, so it may only label rows pos_i+1 .. pos_{i+1}.
            # The first fit additionally covers its own (in-sample) training window: those rows are the
            # warm-up and are flagged as such.
            start = 0 if i == 0 else pos + 1
            stop = fit_positions[i + 1] if i + 1 < len(fit_positions) else n - 1
            blocks.append((start, stop, fit))

        all_ids = sorted({cid for f in fits for cid in f.canonical_ids})
        fit_date = pd.Series(pd.NaT, index=idx, dtype="datetime64[ns]")
        n_states = pd.Series(np.nan, index=idx, dtype="float64")
        values = np.zeros((n, len(all_ids)), dtype="float64")
        col_of = {cid: j for j, cid in enumerate(all_ids)}

        for start, stop, fit in blocks:
            # The filter is restarted at the first row of the (expanding) training window with startprob_
            # and run forward to `stop`; only rows in [start, stop] are reported for this fit, so row t is
            # always produced by the most recent fit whose fit date is <= t.
            alpha = self._forward_filter(fit, raw, columns, stop)
            for i, cid in enumerate(fit.canonical_ids):
                values[start : stop + 1, col_of[cid]] = alpha[start : stop + 1, i]
            fit_date.iloc[start : stop + 1] = fit.fit_date
            n_states.iloc[start : stop + 1] = float(fit.n_states)

        post = pd.DataFrame(values, index=idx, columns=pd.Index(all_ids, name="state"), dtype="float64")
        warm = pd.Series(False, index=idx, dtype="bool")
        warm.iloc[: fit_positions[0] + 1] = True
        return HmmResult(
            posteriors=post,
            fit_date=fit_date,
            n_states=n_states,
            warmup=warm,
            fits=fits,
            features_used=columns,
            slope_missing=slope_missing,
        )
