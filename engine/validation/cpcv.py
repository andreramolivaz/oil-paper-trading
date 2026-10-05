"""Combinatorial purged cross-validation (CPCV) and the Probability of Backtest Overfitting (PBO).

Two related but different procedures (López de Prado, *Advances in Financial ML*, ch. 11-12; Bailey, Borwein,
López de Prado & Zhu 2017, "The Probability of Backtest Overfitting", J. of Computational Finance 20(4)):

* **CPCV** (:func:`cpcv_plan`) is a cross-validation scheme for ONE strategy/model.  The ``n`` observations are
  cut into ``N`` contiguous groups; every one of the ``C(N, k)`` choices of ``k`` test groups is a split whose
  training set is the remaining groups, purged of label overlap and embargoed (:mod:`engine.validation.cv`).  The
  out-of-sample predictions of the splits are stitched into ``φ = k/N · C(N, k)`` complete backtest *paths*, so
  the strategy gets a distribution of OOS Sharpe ratios instead of a single number.
* **CSCV** (:func:`probability_of_backtest_overfitting`) is a model-selection diagnostic over MANY strategy
  variants.  The ``T×M`` return matrix is cut into ``S`` (even) contiguous submatrices; every choice of ``S/2``
  of them is an in-sample set and the complement the out-of-sample set.  In each of the ``C(S, S/2)`` combinations
  the variant that is best in-sample is ranked out-of-sample; the relative rank ``ω = rank/(M+1)`` gives the logit
  ``λ = ln(ω/(1-ω))`` and ``PBO = P[λ <= 0]``: the probability that the in-sample winner is below the OOS median.
  No purging is used: CSCV ranks pre-computed return streams rather than fitting models, the symmetric design keeps
  IS and OOS sample sizes equal (a requirement of the paper), and ``k = S/2`` is fixed by construction — this is
  why the PBO is computed with CSCV *partitions* and not with the ``C(N, k)`` CPCV splits.

Everything is vectorised on per-group sufficient statistics (sums, sums of squares, counts): ``T = 6000``,
``M = 200`` and ``S = 16`` (12 870 combinations) run in about a second.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from engine.validation.cv import purged_train_indices

__all__ = ["CPCVPlan", "PBOResult", "cpcv_plan", "n_backtest_paths", "probability_of_backtest_overfitting"]


# ---------------------------------------------------------------------------------------------------------------
# CPCV: splits and backtest paths
# ---------------------------------------------------------------------------------------------------------------
def n_backtest_paths(n_groups: int, k_test: int) -> int:
    """``φ(N, k) = k/N · C(N, k) = C(N-1, k-1)``: number of complete OOS paths CPCV produces."""
    return math.comb(n_groups - 1, k_test - 1)


@dataclass(frozen=True)
class CPCVPlan:
    """Groups, test-group combinations and path assignment of a CPCV run (indices computed lazily).

    ``paths[p, g]`` is the index of the split whose out-of-sample predictions fill group ``g`` in path ``p``.
    Every group appears in the test set of exactly ``φ`` splits, which are assigned to paths in enumeration order,
    so each path is a complete series built from ``N`` different splits (ch. 12, fig. 12.1).
    """

    n: int
    n_groups: int
    k_test: int
    label_horizon: int
    embargo: int
    groups: tuple[np.ndarray, ...]
    combos: np.ndarray  # (n_splits, k_test) sorted test-group indices
    paths: np.ndarray  # (n_paths, n_groups) split indices

    @property
    def n_splits(self) -> int:
        return int(self.combos.shape[0])

    @property
    def n_paths(self) -> int:
        return int(self.paths.shape[0])

    def test_indices(self, split: int) -> np.ndarray:
        return np.concatenate([self.groups[g] for g in self.combos[split]])

    def split(self, split: int) -> tuple[np.ndarray, np.ndarray]:
        """``(train_idx, test_idx)`` of one split, train purged and embargoed around every test group."""
        test_idx = self.test_indices(split)
        return purged_train_indices(self.n, test_idx, self.label_horizon, self.embargo), test_idx

    def iter_splits(self) -> Iterator[tuple[np.ndarray, np.ndarray, tuple[int, ...]]]:
        """Yield ``(train_idx, test_idx, test_groups)`` for every combination."""
        for s in range(self.n_splits):
            train_idx, test_idx = self.split(s)
            yield train_idx, test_idx, tuple(int(g) for g in self.combos[s])

    def stitch(self, oos: Mapping[int, np.ndarray]) -> np.ndarray:
        """Assemble the ``(n_paths, n)`` path matrix from per-split OOS values.

        ``oos[s]`` must be aligned with ``test_indices(s)`` (e.g. the OOS strategy returns of split ``s``).
        Splits missing from ``oos`` leave NaN in the paths that need them.
        """
        out = np.full((self.n_paths, self.n), np.nan)
        for p in range(self.n_paths):
            for g in range(self.n_groups):
                s = int(self.paths[p, g])
                vals = oos.get(s)
                if vals is None:
                    continue
                vals = np.asarray(vals, dtype=float)
                combo = self.combos[s]
                offset = int(sum(self.groups[h].size for h in combo if h < g))
                out[p, self.groups[g]] = vals[offset : offset + self.groups[g].size]
        return out


def cpcv_plan(n: int, n_groups: int = 6, k_test: int = 2, label_horizon: int = 0, embargo_pct: float = 0.0) -> CPCVPlan:
    """Enumerate the ``C(N, k)`` CPCV splits over ``n`` observations and assign them to backtest paths.

    ``label_horizon`` and ``embargo_pct`` have the same meaning as in :class:`engine.validation.cv.PurgedKFold`.
    Indices are not materialised here (``C(16, 8) = 12 870`` splits of 6 000 indices would be 600 MB); use
    :meth:`CPCVPlan.split` / :meth:`CPCVPlan.iter_splits`.
    """
    if n_groups < 2 or not 1 <= k_test < n_groups:
        raise ValueError("need n_groups >= 2 and 1 <= k_test < n_groups")
    if n < n_groups:
        raise ValueError("need at least one observation per group")
    if not 0.0 <= embargo_pct < 1.0:
        raise ValueError("embargo_pct must be in [0, 1)")
    groups = tuple(np.array_split(np.arange(n, dtype=np.int64), n_groups))
    combos = np.array(list(itertools.combinations(range(n_groups), k_test)), dtype=np.int64)
    n_splits = combos.shape[0]
    member = np.zeros((n_splits, n_groups), dtype=bool)
    member[np.arange(n_splits)[:, None], combos] = True
    phi = n_backtest_paths(n_groups, k_test)
    paths = np.empty((phi, n_groups), dtype=np.int64)
    for g in range(n_groups):
        paths[:, g] = np.flatnonzero(member[:, g])
    return CPCVPlan(
        n=n,
        n_groups=n_groups,
        k_test=k_test,
        label_horizon=int(label_horizon),
        embargo=math.ceil(embargo_pct * n),
        groups=groups,
        combos=combos,
        paths=paths,
    )


# ---------------------------------------------------------------------------------------------------------------
# CSCV: probability of backtest overfitting
# ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PBOResult:
    """Output of :func:`probability_of_backtest_overfitting`.

    * ``pbo``: fraction of combinations whose in-sample winner ranks in the lower OOS half (``logit <= 0``).
    * ``logits``: ``λ_c`` per combination (their distribution is the paper's main diagnostic).
    * ``slope``/``intercept``: OLS of the winner's OOS Sharpe on its IS Sharpe across combinations.  A negative
      slope is the "performance degradation" signature of overfitting.
    * ``prob_oos_loss``: fraction of combinations where the IS winner has a negative OOS Sharpe.
    * ``dominance``: does the OOS Sharpe distribution of the IS winner stochastically dominate (first / second
      order) the OOS distribution of all variants?  Dominance means selecting in-sample adds value OOS.
    Sharpe ratios are per-observation (not annualised; ranks and logits are invariant to the scale).
    """

    pbo: float
    logits: np.ndarray
    slope: float
    intercept: float
    prob_oos_loss: float
    is_sharpe_selected: np.ndarray
    oos_sharpe_selected: np.ndarray
    selected: np.ndarray
    dominance: dict[str, Any]
    n_combinations: int
    n_partitions: int
    n_variants: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly summary (arrays reduced to quantiles)."""
        q = [0.05, 0.25, 0.5, 0.75, 0.95]
        return {
            "pbo": self.pbo,
            "slope": self.slope,
            "intercept": self.intercept,
            "prob_oos_loss": self.prob_oos_loss,
            "logit_quantiles": dict(zip([str(x) for x in q], np.quantile(self.logits, q).tolist(), strict=True)),
            "logit_mean": float(np.mean(self.logits)),
            "oos_sharpe_selected_mean": float(np.mean(self.oos_sharpe_selected)),
            "dominance": self.dominance,
            "n_combinations": self.n_combinations,
            "n_partitions": self.n_partitions,
            "n_variants": self.n_variants,
        }


def _sharpe_from_moments(s1: np.ndarray, s2: np.ndarray, n: np.ndarray) -> np.ndarray:
    """Per-observation Sharpe from sums, sums of squares and counts (0 where the variance is zero)."""
    mean = s1 / n
    var = np.maximum(s2 - n * mean**2, 0.0) / np.maximum(n - 1.0, 1.0)
    sd = np.sqrt(var)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0.0, mean / sd, 0.0)


def _ecdf(sorted_vals: np.ndarray, grid: np.ndarray) -> np.ndarray:
    return np.searchsorted(sorted_vals, grid, side="right") / sorted_vals.size


def _stochastic_dominance(selected: np.ndarray, all_oos: np.ndarray) -> dict[str, Any]:
    """First- and second-order stochastic dominance of the IS winner's OOS Sharpe over all variants' OOS Sharpe."""
    sel = np.sort(selected)
    allv = np.sort(all_oos)
    grid = np.unique(
        np.concatenate((np.quantile(sel, np.linspace(0, 1, 101)), np.quantile(allv, np.linspace(0, 1, 101))))
    )
    cdf_sel, cdf_all = _ecdf(sel, grid), _ecdf(allv, grid)
    tol = 1e-12
    first = bool(np.all(cdf_sel <= cdf_all + tol))
    if grid.size > 1:
        dx = np.diff(grid)
        int_sel = np.concatenate(([0.0], np.cumsum(0.5 * (cdf_sel[1:] + cdf_sel[:-1]) * dx)))
        int_all = np.concatenate(([0.0], np.cumsum(0.5 * (cdf_all[1:] + cdf_all[:-1]) * dx)))
        second = bool(np.all(int_sel <= int_all + tol))
    else:
        second = first
    return {
        "first_order": first,
        "second_order": second,
        "mean_oos_selected": float(sel.mean()),
        "mean_oos_all": float(allv.mean()),
        "median_oos_selected": float(np.median(sel)),
        "median_oos_all": float(np.median(allv)),
        "max_cdf_gap": float(np.max(cdf_sel - cdf_all)),  # > 0 where the winner's CDF lies above (worse)
    }


def probability_of_backtest_overfitting(
    returns: np.ndarray, n_partitions: int = 16, chunk_size: int = 2048
) -> PBOResult:
    """CSCV estimate of the Probability of Backtest Overfitting (Bailey, Borwein, López de Prado & Zhu 2017).

    ``returns`` is the ``T×M`` matrix of per-period returns of ``M`` strategy variants (same instrument, same
    period, costs included); NaN are rejected (nothing is filled).  ``n_partitions`` (``S``) must be even and
    ``<= T``; ``C(S, S/2)`` combinations are evaluated (``S = 16`` → 12 870).  The selection criterion is the
    per-observation Sharpe ratio.  See the module docstring for the relation with CPCV.
    """
    r = np.asarray(returns, dtype=float)
    if r.ndim != 2:
        raise ValueError("returns must be a T x M matrix")
    if np.isnan(r).any():
        raise ValueError("returns contain NaN; align the variants before computing the PBO")
    t_obs, m_var = r.shape
    s = int(n_partitions)
    if s < 2 or s % 2:
        raise ValueError("n_partitions must be an even integer >= 2")
    if t_obs < s:
        raise ValueError("fewer observations than partitions")
    if m_var < 2:
        raise ValueError("need at least two strategy variants")

    groups = np.array_split(np.arange(t_obs), s)
    g_sum = np.stack([r[g].sum(axis=0) for g in groups])  # (S, M)
    g_sq = np.stack([np.square(r[g]).sum(axis=0) for g in groups])
    g_cnt = np.array([g.size for g in groups], dtype=float)
    tot_sum, tot_sq, tot_cnt = g_sum.sum(axis=0), g_sq.sum(axis=0), float(g_cnt.sum())

    combos = np.array(list(itertools.combinations(range(s), s // 2)), dtype=np.int64)
    n_comb = combos.shape[0]
    indicator = np.zeros((n_comb, s))
    indicator[np.arange(n_comb)[:, None], combos] = 1.0

    logits = np.empty(n_comb)
    is_sel = np.empty(n_comb)
    oos_sel = np.empty(n_comb)
    selected = np.empty(n_comb, dtype=np.int64)
    oos_all_parts: list[np.ndarray] = []
    for c0 in range(0, n_comb, max(1, int(chunk_size))):
        ind = indicator[c0 : c0 + chunk_size]
        is_n = (ind @ g_cnt)[:, None]
        is_s1, is_s2 = ind @ g_sum, ind @ g_sq
        is_sr = _sharpe_from_moments(is_s1, is_s2, is_n)
        oos_sr = _sharpe_from_moments(tot_sum - is_s1, tot_sq - is_s2, tot_cnt - is_n)
        best = np.argmax(is_sr, axis=1)
        rows = np.arange(best.size)
        sel_oos = oos_sr[rows, best]
        below = (oos_sr < sel_oos[:, None]).sum(axis=1)
        ties = (oos_sr == sel_oos[:, None]).sum(axis=1) - 1
        rank = 1.0 + below + 0.5 * ties  # average rank, 1 = worst OOS variant
        omega = rank / (m_var + 1.0)
        logits[c0 : c0 + best.size] = np.log(omega / (1.0 - omega))
        is_sel[c0 : c0 + best.size] = is_sr[rows, best]
        oos_sel[c0 : c0 + best.size] = sel_oos
        selected[c0 : c0 + best.size] = best
        oos_all_parts.append(oos_sr.ravel())

    pbo = float(np.mean(logits <= 0.0))
    if np.ptp(is_sel) > 0.0:
        slope, intercept = (float(v) for v in np.polyfit(is_sel, oos_sel, 1))
    else:
        slope, intercept = float("nan"), float(np.mean(oos_sel))
    return PBOResult(
        pbo=pbo,
        logits=logits,
        slope=slope,
        intercept=intercept,
        prob_oos_loss=float(np.mean(oos_sel < 0.0)),
        is_sharpe_selected=is_sel,
        oos_sharpe_selected=oos_sel,
        selected=selected,
        dominance=_stochastic_dominance(oos_sel, np.concatenate(oos_all_parts)),
        n_combinations=int(n_comb),
        n_partitions=s,
        n_variants=int(m_var),
    )
