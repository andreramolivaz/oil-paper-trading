"""Deflated Sharpe Ratio and the trials registry (brief §12: "registra ogni prova, niente cherry picking").

Bailey & López de Prado (2014), "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting
and Non-Normality", J. of Portfolio Management 40(5).  When the best of ``N`` trials is selected, the expected
maximum Sharpe under the null of zero true skill is

    SR0 = sqrt(V[SR_n]) · [ (1 - γ) · Z⁻¹(1 - 1/N) + γ · Z⁻¹(1 - 1/(N·e)) ],   γ = 0.5772… (Euler-Mascheroni)

and the DSR is the Probabilistic Sharpe Ratio of the selected estimate against ``SR0`` (instead of 0), taking the
track-record length, skewness and kurtosis into account.  All Sharpe ratios are per-observation unless
``periods_per_year`` is given, in which case annualised inputs are de-annualised by ``sqrt(periods_per_year)``.

``TrialLog`` appends every backtested parameter variant to the ``trials`` JSONL of the state store so that the
number of trials used as the DSR denominator is the real one, not the number of variants someone remembered.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
from scipy import stats

from engine.core.store import StateStore
from engine.validation.metrics import probabilistic_sharpe_ratio

__all__ = ["EULER_GAMMA", "Haircut", "TrialLog", "deflated_sharpe_ratio", "expected_max_sharpe", "haircut_sharpe"]

EULER_GAMMA = 0.5772156649015329


def _deannualise(sr: float, periods_per_year: float | None) -> float:
    return sr / math.sqrt(periods_per_year) if periods_per_year else sr


def expected_max_sharpe(n_trials: int, var_trials: float) -> float:
    """``E[max_n SR_n]`` under the null of zero true Sharpe (eq. above); 0 for a single trial or zero variance."""
    if n_trials <= 1 or not math.isfinite(var_trials) or var_trials <= 0.0:
        return 0.0
    z1 = float(stats.norm.ppf(1.0 - 1.0 / n_trials))
    z2 = float(stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
    return math.sqrt(var_trials) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe_ratio(
    sharpe_estimates: Sequence[float] | np.ndarray,
    selected_sharpe: float,
    n_obs: int,
    skew: float = 0.0,
    kurt: float = 3.0,
    n_trials: int | None = None,
    periods_per_year: float | None = None,
) -> tuple[float, float]:
    """``(dsr_prob, sr0)``: probability that the selected strategy's true Sharpe is positive once the selection
    among ``len(sharpe_estimates)`` trials is accounted for, and the expected maximum Sharpe ``SR0`` under the null.

    ``sharpe_estimates`` are the Sharpe ratios of ALL trials (the registry), ``selected_sharpe`` the one being
    promoted, ``n_obs`` its track-record length, ``skew``/``kurt`` (Pearson) the moments of its returns.
    ``n_trials`` overrides the trial count when the registry holds more trials than Sharpe values (never fewer).
    The variance across trials uses ``ddof=1`` (conservative).  A DSR probability above 0.95 is the promotion
    threshold used by :mod:`engine.validation.lifecycle`.
    """
    sr = np.asarray(sharpe_estimates, dtype=float).ravel()
    sr = sr[np.isfinite(sr)]
    trials = len(sr) if n_trials is None else max(int(n_trials), len(sr))
    if trials < 1:
        raise ValueError("at least one trial is required")
    scale = math.sqrt(periods_per_year) if periods_per_year else 1.0
    var_trials = float(np.var(sr / scale, ddof=1)) if sr.size > 1 else 0.0
    sr0 = expected_max_sharpe(trials, var_trials)
    prob = probabilistic_sharpe_ratio(_deannualise(selected_sharpe, periods_per_year), sr0, n_obs, skew, kurt)
    return float(prob), float(sr0)


@dataclass(frozen=True)
class Haircut:
    """Selection-bias haircut: ``adjusted = max(selected - sr0, 0)``, ``haircut`` the fraction removed."""

    selected: float
    sr0: float
    adjusted: float
    haircut: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def haircut_sharpe(selected_sharpe: float, sr0: float) -> Haircut:
    """Subtract the expected maximum under the null from the selected Sharpe (same units as the inputs)."""
    adjusted = max(selected_sharpe - sr0, 0.0)
    haircut = 1.0 - adjusted / selected_sharpe if selected_sharpe > 0 else 1.0
    return Haircut(float(selected_sharpe), float(sr0), float(adjusted), float(min(max(haircut, 0.0), 1.0)))


class TrialLog:
    """Append-only registry of every backtested parameter variant (``state/trials.jsonl``).

    One record per call: ``strategy``, ``params_hash`` (sha1 of the canonical JSON of ``params``), ``params``,
    ``sharpe``, ``n_obs``, ``skew``, ``kurt``, ``periods_per_year`` and ``asof`` (UTC).  Nothing is ever deleted
    or rewritten; re-running the same variant appends a new record (``distinct=True`` collapses them when counting).
    """

    NAME = "trials"

    def __init__(self, store: StateStore):
        self.store = store

    @staticmethod
    def params_hash(params: Mapping[str, Any]) -> str:
        canonical = json.dumps(dict(params), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()

    def log(
        self,
        strategy: str,
        params: Mapping[str, Any],
        sharpe: float,
        n_obs: int,
        skew: float,
        kurt: float,
        asof: datetime | None = None,
        periods_per_year: float | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Append one trial and return the stored record."""
        ts = (asof or datetime.now(tz=UTC)).astimezone(UTC)
        record: dict[str, Any] = {
            "strategy": str(strategy),
            "params_hash": self.params_hash(params),
            "params": dict(params),
            "sharpe": float(sharpe),
            "n_obs": int(n_obs),
            "skew": float(skew),
            "kurt": float(kurt),
            "periods_per_year": periods_per_year,
            "asof": ts.isoformat().replace("+00:00", "Z"),
        }
        record.update(extra)
        self.store.append_jsonl(self.NAME, record, rotate=False)
        return record

    def records(self, strategy: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.read_jsonl(self.NAME)
        return [r for r in rows if strategy is None or r.get("strategy") == strategy]

    def _latest_by_variant(self, strategy: str | None) -> dict[tuple[str, str], dict[str, Any]]:
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for r in self.records(strategy):
            latest[(str(r.get("strategy")), str(r.get("params_hash")))] = r  # file order = chronological
        return latest

    def n_trials(self, strategy: str | None = None, distinct: bool = True) -> int:
        """Number of trials for the DSR denominator (distinct parameter variants by default)."""
        if distinct:
            return len(self._latest_by_variant(strategy))
        return len(self.records(strategy))

    def sharpes(self, strategy: str | None = None, distinct: bool = True) -> np.ndarray:
        """Sharpe ratios of the trials (latest record per variant when ``distinct``)."""
        rows = list(self._latest_by_variant(strategy).values()) if distinct else self.records(strategy)
        return np.array([float(r["sharpe"]) for r in rows if "sharpe" in r], dtype=float)

    def deflate(
        self,
        strategy: str,
        selected_sharpe: float,
        n_obs: int,
        skew: float = 0.0,
        kurt: float = 3.0,
        periods_per_year: float | None = None,
    ) -> tuple[float, float]:
        """DSR of ``selected_sharpe`` against every registered trial of ``strategy``."""
        return deflated_sharpe_ratio(
            self.sharpes(strategy), selected_sharpe, n_obs, skew, kurt, periods_per_year=periods_per_year
        )
