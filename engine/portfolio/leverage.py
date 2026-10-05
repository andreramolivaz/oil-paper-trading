"""Leverage decision (brief §9): how much, and what limited it.

    effective leverage = min(10, L_kelly, L_vol, L_drawdown, L_event)

and, crucially, **1x whenever the alpha gate has not passed**. Every component is recorded so the dashboard can
say "Leva 3,2x — limitata dalla volatilità" instead of showing an unexplained number.

L_vol caps the 1-day 99% expected shortfall at `sizing.es99_budget` of equity, using the MOST CONSERVATIVE
(largest) volatility among the GARCH forecast, the OVX-implied vol and the realized vol. Expected shortfall for
a Student-t with nu degrees of freedom, at level p:

    ES_p = sigma_scaled * pdf_t(q_p) / (1 - p) * (nu + q_p^2) / (nu - 1)

with q_p the t quantile and sigma_scaled = sigma_daily * sqrt((nu - 2) / nu) so that the distribution has the
intended standard deviation. Fat tails are the point: a Gaussian ES would understate a geopolitical gap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from scipy import stats

from engine.core.config import RiskConfig
from engine.portfolio.kelly import fractional_kelly_leverage

TRADING_DAYS = 252
ES_DF = 4.0  # Student-t degrees of freedom for the tail: fat, as Brent gaps demand

# Keys that name a *reason* rather than one of the numeric components ("the gate held it to 1x",
# "there was nothing to size"). The dashboard resolves these to a label only, not to a component value.
SYNTHETIC_LIMITS = frozenset({"gate", "no_signal"})

LIMIT_LABELS = {
    "gate": "gate non superato: 1x",
    "no_signal": "nessun segnale netto: nessuna esposizione",
    "kelly": "limitata dal Kelly frazionario",
    "vol": "limitata dalla volatilità",
    "drawdown": "limitata dal drawdown",
    "event": "limitata dall'evento in calendario",
    "cap": "limitata dal tetto di 10x",
    "default": "esposizione di default 1x",
}


@dataclass
class LeverageDecision:
    chosen: float
    components: dict[str, float] = field(default_factory=dict)
    limited_by: str = ""
    limited_by_key: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": round(float(self.chosen), 4),
            "components": {
                k: (None if v is None or not math.isfinite(v) else round(float(v), 4))
                for k, v in self.components.items()
            },
            "limited_by": self.limited_by,
            "limited_by_key": self.limited_by_key,
            "detail": self.detail,
        }


def student_t_es(level: float = 0.99, df: float = ES_DF) -> float:
    """Expected shortfall of a unit-variance Student-t at `level` (a positive multiple of sigma)."""
    q = float(stats.t.ppf(level, df))
    scale = math.sqrt((df - 2.0) / df)  # unit variance
    es_std = float(stats.t.pdf(q, df) / (1.0 - level) * (df + q * q) / (df - 1.0))
    return es_std * scale


def es99_one_day(vol_annual: float, level: float = 0.99, df: float = ES_DF) -> float:
    """1-day expected shortfall as a fraction of notional, from an annualised volatility."""
    if not math.isfinite(vol_annual) or vol_annual <= 0:
        return float("inf")
    daily = vol_annual / math.sqrt(TRADING_DAYS)
    return daily * student_t_es(level, df)


def conservative_vol(vols: dict[str, float | None]) -> tuple[float, str]:
    """The largest finite volatility among the candidates, with its name. (inf, '') when none is usable."""
    best, name = -1.0, ""
    for k, v in vols.items():
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f) and f > best:
            best, name = f, k
    return (best, name) if best > 0 else (float("inf"), "")


def drawdown_multiplier(drawdown: float, ladder: dict[str, float]) -> float:
    """Multiplier applied to the leverage ABOVE 1x, from the config ladder {threshold: multiplier}."""
    dd = abs(float(drawdown))
    worst = 1.0
    for threshold, mult in sorted(((float(k), float(v)) for k, v in ladder.items()), key=lambda kv: kv[0]):
        if dd >= threshold:
            worst = mult
    return float(worst)


def compute_leverage(
    gate_passed: bool,
    risk: RiskConfig,
    expected_return: float,
    expected_vol: float,
    horizon_days: int,
    vols: dict[str, float | None],
    drawdown: float,
    hours_to_event: float = float("inf"),
    is_pre_weekend: bool = False,
    geo_index_pctl: float | None = None,
) -> LeverageDecision:
    """The leverage the master may use now, and what limited it. Never above `risk.max_leverage` (hard 10x)."""
    hard_cap = min(10.0, float(risk.max_leverage))
    default_cap = float(risk.default_max_leverage)

    l_kelly = fractional_kelly_leverage(
        expected_return, expected_vol, risk.kelly_fraction, horizon_days=horizon_days, annualised=False
    )

    vol_used, vol_name = conservative_vol(vols)
    es = es99_one_day(vol_used)
    l_vol = float(risk.es99_budget / es) if es > 0 and math.isfinite(es) else 0.0

    mult = drawdown_multiplier(drawdown, risk.drawdown_delever)
    l_dd = 1.0 + (hard_cap - 1.0) * mult

    ev = risk.event_delever or {}
    l_event = hard_cap
    event_reason = None
    pre_event_hours = float(risk.gate.get("hours_before_binary_event", 24) or 24)
    if math.isfinite(hours_to_event) and 0.0 <= hours_to_event <= pre_event_hours:
        l_event = min(l_event, hard_cap * float(ev.get("pre_event_multiplier", 0.5)))
        event_reason = "evento binario entro le prossime ore"
    if (
        is_pre_weekend
        and geo_index_pctl is not None
        and float(geo_index_pctl) >= float(ev.get("geo_index_percentile_high", 0.9))
    ):
        l_event = min(l_event, hard_cap * float(ev.get("weekend_geo_risk_multiplier", 0.5)))
        event_reason = "weekend con rischio geopolitico alto"

    components = {
        "kelly": l_kelly,
        "vol": l_vol,
        "drawdown": l_dd,
        "event": l_event,
        "cap": hard_cap,
        "default": default_cap,
    }
    # Every limit always applies; failing the gate adds the 1x ceiling on top (brief §9). The minimum is the
    # cap on GROSS exposure, not a target: the sizing (volatility targeting x conviction) is usually well below.
    candidates = {k: components[k] for k in ("kelly", "vol", "drawdown", "event", "cap")}
    if not gate_passed:
        candidates["gate"] = min(default_cap, hard_cap)
    binding = min(candidates, key=lambda k: (candidates[k], k != "gate"))
    chosen = max(0.0, min(*candidates.values(), hard_cap))
    if chosen <= 0.0 and abs(expected_return) <= 0.0:
        # No conviction at all: the cap is zero because there is nothing to size, not because Kelly bit.
        binding = "no_signal"
    return LeverageDecision(
        chosen=float(chosen),
        components=components,
        limited_by=LIMIT_LABELS[binding],
        limited_by_key=binding,
        detail={
            "gate_passed": bool(gate_passed),
            "vol_used": None if not math.isfinite(vol_used) else round(vol_used, 4),
            "vol_source": vol_name,
            "es99_1d": None if not math.isfinite(es) else round(es, 5),
            "es99_budget": risk.es99_budget,
            "drawdown": round(float(drawdown), 4),
            "drawdown_multiplier": mult,
            "event_reason": event_reason,
            "hours_to_event": None if not math.isfinite(hours_to_event) else round(float(hours_to_event), 2),
        },
    )
