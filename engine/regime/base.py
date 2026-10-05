"""Regime state record and the readable label vocabulary (brief §7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from engine.core.events import Record

# Canonical labels; the HMM states are mapped to these from their fitted moments (never hard-coded by date).
LABEL_BACKWARDATION_HIGHVOL_GEO = "Backwardation + vol alta + rischio geopolitico"
LABEL_CONTANGO_GLUT_DOWNTREND = "Contango + eccesso d'offerta + trend ribassista"
LABEL_LOWVOL_RANGE = "Range a bassa volatilità"
LABEL_SHOCK_CRASH = "Shock/crash"
LABEL_BULL_SQUEEZE = "Squeeze rialzista"
LABEL_TRANSITION = "Transizione"

ALL_LABELS = [
    LABEL_BACKWARDATION_HIGHVOL_GEO,
    LABEL_CONTANGO_GLUT_DOWNTREND,
    LABEL_LOWVOL_RANGE,
    LABEL_SHOCK_CRASH,
    LABEL_BULL_SQUEEZE,
    LABEL_TRANSITION,
]


@dataclass
class RegimeState(Record):
    ts: datetime
    regime_id: int  # -1 = transition
    label: str
    confidence: float  # max posterior probability
    probabilities: dict[str, float] = field(default_factory=dict)  # label -> prob
    change_point_prob: float = 0.0
    features_used: list[str] = field(default_factory=list)
    model: str = ""  # e.g. "hmm4-walkforward-2026-10-04"
    approx: bool = False

    @property
    def is_transition(self) -> bool:
        return self.regime_id < 0
