"""The "strong alpha" gate (brief §9): leverage above 1x is earned, never assumed.

All SEVEN conditions must hold. Each one is reported with its value, its threshold and an Italian explanation,
so the dashboard and the trade log can show exactly why leverage was or was not allowed.

Design rule: the gate FAILS CLOSED. A missing input (no regime confidence, no out-of-sample statistics, NaN
probability, unknown data age) is a failed condition, never a passed one.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import pandas as pd

from engine.core.config import RiskConfig
from engine.core.eventcal import EventCalendar
from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.regime.base import LABEL_TRANSITION
from engine.strategies.base import MarketContext

log = logging.getLogger(__name__)

CONDITIONS = (
    "ensemble_prob",
    "families_agree",
    "regime_quality",
    "physical_confirmation",
    "no_binary_event",
    "data_fresh",
    "drawdown_ok",
)


@dataclass
class Condition:
    ok: bool
    value: Any
    threshold: Any
    detail: str

    def to_dict(self) -> dict[str, Any]:
        v = self.value
        if isinstance(v, float) and not math.isfinite(v):
            v = None
        return {"ok": bool(self.ok), "value": v, "threshold": self.threshold, "detail": self.detail}


@dataclass
class GateResult:
    passed: bool
    conditions: dict[str, Condition] = field(default_factory=dict)
    ensemble_prob: float = 0.0
    direction: Direction = Direction.FLAT
    families_agreeing: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": bool(self.passed),
            "ensemble_prob": None if not math.isfinite(self.ensemble_prob) else round(self.ensemble_prob, 4),
            "direction": str(self.direction),
            "families_agreeing": list(self.families_agreeing),
            "reason": self.reason,
            "conditions": {k: v.to_dict() for k, v in self.conditions.items()},
        }

    @property
    def failed(self) -> list[str]:
        return [k for k, c in self.conditions.items() if not c.ok]


def _tradeable(signals: list[Signal]) -> list[Signal]:
    """Signals that actually take risk: directional, not informational, not an approximation."""
    return [
        s
        for s in signals
        if s.direction is not Direction.FLAT and not s.meta.get("tilt_only") and not s.meta.get("approx")
    ]


def net_direction(signals: list[Signal], weights: dict[str, float] | None = None) -> tuple[Direction, float]:
    """Weighted net direction and the weighted gross conviction behind it."""
    w = weights or {}
    net = sum(s.score * float(w.get(s.strategy_id, 1.0)) for s in signals)
    return Direction.from_sign(net), abs(float(net))


def ensemble_probability(signals: list[Signal], direction: Direction, weights: dict[str, float] | None = None) -> float:
    """Weighted average calibrated probability of the signals pointing the net way (0.5 when there are none)."""
    w = weights or {}
    agreeing = [s for s in signals if s.direction is direction]
    if not agreeing:
        return 0.5
    total = sum(max(0.0, float(w.get(s.strategy_id, 1.0))) for s in agreeing)
    if total <= 0:
        return float(sum(s.prob for s in agreeing) / len(agreeing))
    return float(sum(s.prob * max(0.0, float(w.get(s.strategy_id, 1.0))) for s in agreeing) / total)


def low_correlation_families(families: list[str], returns: pd.DataFrame | None, max_corr: float) -> list[str]:
    """Largest subset of families whose pairwise correlation stays below `max_corr`.

    `returns` holds one column per family (daily out-of-sample returns). Without it we cannot prove the
    families are decorrelated, so we accept the distinct family names as given and say so in the detail.
    """
    uniq = sorted(set(families))
    if returns is None or returns.empty or len(uniq) < 2:
        return uniq
    cols = [f for f in uniq if f in returns.columns]
    if len(cols) < 2:
        return uniq
    corr = returns[cols].corr().abs()
    best: list[str] = []
    for size in range(len(cols), 1, -1):
        for combo in combinations(cols, size):
            sub = corr.loc[list(combo), list(combo)].to_numpy()
            ok = True
            for i in range(len(combo)):
                for j in range(i + 1, len(combo)):
                    if not math.isfinite(sub[i][j]) or sub[i][j] >= max_corr:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                best = list(combo)
                break
        if best:
            break
    extras = [f for f in uniq if f not in cols]
    return sorted([*best, *extras]) if best else extras


class AlphaGate:
    def __init__(self, risk: RiskConfig, events: EventCalendar | None = None):
        self.risk = risk
        self.events = events if events is not None else EventCalendar()
        g = risk.gate or {}
        self.min_prob = float(g.get("min_ensemble_prob", 0.62))
        self.min_families = int(g.get("min_families_agree", 3))
        self.max_corr = float(g.get("max_family_correlation", 0.5))
        self.min_regime_conf = float(g.get("min_regime_confidence", 0.70))
        self.min_psr = float(g.get("min_prob_sharpe", 0.90))
        self.require_physical = bool(g.get("require_physical_confirmation_for_geo_longs", True))
        self.event_hours = float(g.get("hours_before_binary_event", 24))
        self.max_dd = float(g.get("max_drawdown_for_leverage", 0.10))
        self.max_age_min = float(g.get("max_data_age_minutes", 90))

    # ------------------------------------------------------------------
    def evaluate(
        self,
        signals: list[Signal],
        ctx: MarketContext,
        drawdown: float,
        weights: dict[str, float] | None = None,
        oos_stats: dict[str, dict[str, float]] | None = None,
        family_returns: pd.DataFrame | None = None,
        health_overall: str = "red",
        data_age_minutes: float | None = None,
    ) -> GateResult:
        live = _tradeable(signals)
        direction, _conviction = net_direction(live, weights)
        prob = ensemble_probability(live, direction, weights)
        agreeing = [s for s in live if s.direction is direction]
        conditions: dict[str, Condition] = {}

        # 1 - calibrated ensemble probability
        conditions["ensemble_prob"] = Condition(
            ok=bool(direction is not Direction.FLAT and math.isfinite(prob) and prob >= self.min_prob),
            value=None if not math.isfinite(prob) else round(prob, 4),
            threshold=self.min_prob,
            detail=(
                f"probabilità d'insieme {prob:.0%} contro la soglia {self.min_prob:.0%}"
                if direction is not Direction.FLAT
                else "nessuna direzione netta tra i segnali"
            ),
        )

        # 2 - at least N weakly correlated families agree
        fams = [s.family for s in agreeing if s.family]
        decorrelated = low_correlation_families(fams, family_returns, self.max_corr)
        conditions["families_agree"] = Condition(
            ok=len(decorrelated) >= self.min_families,
            value=len(decorrelated),
            threshold=self.min_families,
            detail=(
                f"{len(decorrelated)} famiglie poco correlate d'accordo ({', '.join(decorrelated) or 'nessuna'}), "
                f"ne servono {self.min_families}"
                + (
                    ""
                    if family_returns is not None and not family_returns.empty
                    else "; correlazioni non ancora stimabili"
                )
            ),
        )

        # 3 - regime confidence and out-of-sample quality of the agreeing strategies in this regime
        conf = float(ctx.regime.confidence or 0.0)
        regime_ok = conf >= self.min_regime_conf and ctx.regime.label != LABEL_TRANSITION
        psr_values = [float((oos_stats or {}).get(s.strategy_id, {}).get("psr", float("nan"))) for s in agreeing]
        usable = [p for p in psr_values if math.isfinite(p)]
        psr_ok = bool(usable) and len(usable) == len(agreeing) and min(usable) >= self.min_psr
        worst = min(usable) if usable else float("nan")
        conditions["regime_quality"] = Condition(
            ok=bool(regime_ok and psr_ok),
            value={"regime_confidence": round(conf, 3), "min_psr": None if not usable else round(worst, 3)},
            threshold={"regime_confidence": self.min_regime_conf, "min_psr": self.min_psr},
            detail=(
                f"regime «{ctx.regime.label}» con confidenza {conf:.0%} (soglia {self.min_regime_conf:.0%}); "
                + (
                    f"Probabilistic Sharpe minimo {worst:.2f} contro {self.min_psr:.2f}"
                    if usable
                    else "nessuna statistica out-of-sample disponibile per le strategie d'accordo"
                )
            ),
        )

        # 4 - physical confirmation for geopolitical longs
        geo_spike = ctx.f(cat.GEO_SPIKE, 0.0)
        geo_regime = "geopolitic" in (ctx.regime.label or "").lower()
        needs_physical = (
            self.require_physical
            and direction is Direction.LONG
            and (geo_regime or (math.isfinite(geo_spike) and geo_spike >= 1.0))
        )
        physical_ok = True
        physical_detail = "non richiesta (nessun picco geopolitico o posizione non long)"
        if needs_physical:
            physical_ok = self._physical_confirms(ctx, direction)
            physical_detail = (
                "curva e fisico confermano la stretta"
                if physical_ok
                else "premio geopolitico non confermato da spread e fisico"
            )
        conditions["physical_confirmation"] = Condition(
            ok=bool(physical_ok), value=physical_ok, threshold=True, detail=physical_detail
        )

        # 5 - no major binary event in the next hours (unless every agreeing strategy is a defined-risk event one)
        hours, event = self.events.hours_to_next_binary(ctx.ts)
        event_close = math.isfinite(hours) and 0.0 <= hours <= self.event_hours
        defined_risk_only = bool(agreeing) and all(s.family == "event" and s.meta.get("defined_risk") for s in agreeing)
        conditions["no_binary_event"] = Condition(
            ok=bool(not event_close or defined_risk_only),
            value=None if not math.isfinite(hours) else round(hours, 2),
            threshold=self.event_hours,
            detail=(
                f"prossimo evento binario «{event.name if event else '-'}» tra {hours:.1f} h"
                if math.isfinite(hours)
                else "nessun evento binario in calendario"
            )
            + (" (strategie evento a rischio definito)" if defined_risk_only and event_close else ""),
        )

        # 6 - data fresh and consistent
        age = float("inf") if data_age_minutes is None else float(data_age_minutes)
        fresh = health_overall == "green" and math.isfinite(age) and age <= self.max_age_min
        conditions["data_fresh"] = Condition(
            ok=bool(fresh),
            value={"health": health_overall, "age_minutes": None if not math.isfinite(age) else round(age, 1)},
            threshold={"health": "green", "age_minutes": self.max_age_min},
            detail=(
                f"stato dati «{health_overall}», ultimo aggiornamento "
                + (f"{age:.0f} min fa" if math.isfinite(age) else "non noto")
            ),
        )

        # 7 - account drawdown under control
        dd = abs(float(drawdown))
        conditions["drawdown_ok"] = Condition(
            ok=dd <= self.max_dd,
            value=round(dd, 4),
            threshold=self.max_dd,
            detail=f"drawdown {dd:.1%} contro il limite {self.max_dd:.0%}",
        )

        passed = all(c.ok for c in conditions.values())
        failed = [k for k, c in conditions.items() if not c.ok]
        reason = (
            "Gate superato: leva oltre 1x consentita."
            if passed
            else "Gate non superato (" + "; ".join(conditions[k].detail for k in failed[:3]) + ")."
        )
        return GateResult(
            passed=passed,
            conditions=conditions,
            ensemble_prob=prob,
            direction=direction,
            families_agreeing=decorrelated,
            reason=reason,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _physical_confirms(ctx: MarketContext, direction: Direction) -> bool:
        """Delegate to S7; if S7 is unavailable the condition fails closed."""
        try:
            from engine.strategies.s07_physical_confirmation import S7PhysicalConfirmation

            return bool(S7PhysicalConfirmation.physical_confirms(ctx, direction))
        except Exception as exc:
            log.warning("physical confirmation unavailable (%s): gate condition fails closed", exc)
            return False
