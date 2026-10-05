"""Public entry point of the regime module: the factory and the degraded fallback model.

Other packages import from here (`from engine.regime.model import make_regime_model`) so that the choice of
implementation stays in one place and a missing or broken `hmmlearn` can never take the engine down: the
`eod` job must still produce a decision, it just produces a "Transizione" one, which the allocator reads as
"no regime information, keep risk at the floor".
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from engine.core.timeutil import ensure_utc
from engine.features import catalog as cat
from engine.regime.base import LABEL_TRANSITION, RegimeModel, RegimeState
from engine.regime.full import HISTORY_COLUMNS, FullRegimeModel
from engine.regime.labels import PROB_COLUMNS

log = logging.getLogger(__name__)

__all__ = ["ConstantRegimeModel", "FullRegimeModel", "make_regime_model"]


class ConstantRegimeModel:
    """Always "Transizione" with confidence 0.0.

    Two uses: a deterministic stand-in for tests of the strategies/portfolio layers, and the degraded
    fallback of `make_regime_model` when the HMM cannot be built. It never claims to know anything, so a
    consumer that honours the confidence threshold automatically keeps leverage at 1x.
    """

    name = "constant-transition"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})

    def fit(self, features: pd.DataFrame) -> None:
        """Nothing to estimate."""
        return None

    def infer(self, features: pd.DataFrame, ts: datetime) -> RegimeState:
        return RegimeState(
            ts=ensure_utc(ts),
            regime_id=-1,
            label=LABEL_TRANSITION,
            confidence=0.0,
            probabilities={},
            change_point_prob=0.0,
            features_used=[],
            model=self.name,
            approx=False,
        )

    def history(self, features: pd.DataFrame) -> pd.DataFrame:
        hist = pd.DataFrame(index=features.index)
        hist[cat.REGIME_ID] = -1
        hist[cat.REGIME_LABEL] = LABEL_TRANSITION
        hist[cat.REGIME_CONF] = 0.0
        for col in PROB_COLUMNS:
            hist[col] = 0.0
        hist[cat.BOCPD_CP_PROB] = 0.0
        return hist[HISTORY_COLUMNS]


def hmmlearn_available() -> bool:
    """True when the HMM backend can be imported. Patched in tests to exercise the fallback path."""
    try:
        import hmmlearn.hmm  # noqa: F401
    except Exception as exc:  # pragma: no cover - import failure is environment specific
        log.warning("regime: hmmlearn is not importable (%s)", exc)
        return False
    return True


def make_regime_model(config: dict[str, Any] | None = None) -> RegimeModel:
    """Build the production regime model, degrading to `ConstantRegimeModel` when the HMM is unavailable."""
    cfg = dict(config or {})
    if cfg.get("model") == ConstantRegimeModel.name:
        return ConstantRegimeModel(cfg)
    if not hmmlearn_available():
        log.warning("regime: falling back to %s (hmmlearn unavailable)", ConstantRegimeModel.name)
        return ConstantRegimeModel(cfg)
    try:
        return FullRegimeModel(cfg)
    except Exception as exc:
        log.warning("regime: FullRegimeModel could not be built (%s); falling back to %s", exc, ConstantRegimeModel.name)
        return ConstantRegimeModel(cfg)
