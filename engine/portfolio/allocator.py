"""MasterAllocator: signals -> orders for the master account (brief §9).

Pipeline, in order:

1. **Filter.** Informational signals (direction FLAT, e.g. S16's breakout hint), tilt-only signals (S14
   seasonality) and approximations (S18 synthetic options) never take risk. Tilt-only signals shift the size of
   the others by at most +-25%. Strategies that are not "active" in the lifecycle carry weight zero.
2. **Combine.** Net conviction per instrument = sum of w_i * score_i with the S20 weights.
3. **Size.** Volatility targeting: the gross exposure that would make the position's volatility equal
   `sizing.vol_target_annual`, times the conviction, clipped to +-1 of equity before leverage.
4. **Gate + leverage.** The alpha gate decides whether more than 1x is allowed at all; the leverage components
   decide how much. The resulting cap is applied to the gross notional.
5. **Hysteresis and lots.** Small changes are ignored, quantities are rounded to whole lots.
6. **Explain.** Every order carries the strategies for and against, the gate outcome, the leverage components
   and an Italian rationale ("Leva 3,2x — limitata dalla volatilità").

Positions whose supporting strategies stopped signalling are closed with reason REBALANCE.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from engine.core.config import RiskConfig
from engine.core.eventcal import EventCalendar
from engine.core.events import (
    AccountSnapshot,
    Direction,
    Order,
    OrderReason,
    OrderType,
    Position,
    Signal,
)
from engine.core.ids import idempotency_key, stable_join
from engine.features import catalog as cat
from engine.portfolio.base import apply_hysteresis, round_to_lots
from engine.portfolio.gate import AlphaGate, GateResult
from engine.portfolio.leverage import LeverageDecision, compute_leverage
from engine.portfolio.weights import RegimeConditionedWeights
from engine.strategies.base import MarketContext

log = logging.getLogger(__name__)

MAX_TILT = 0.25


@dataclass
class AllocationDebug:
    """Everything the dashboard and the audit log need about one allocation round."""

    gate: GateResult | None = None
    leverage: LeverageDecision | None = None
    net_score: dict[str, float] = field(default_factory=dict)
    target_qty: dict[str, float] = field(default_factory=dict)
    tilt: float = 0.0
    vol_used: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": None if self.gate is None else self.gate.to_dict(),
            "leverage": None if self.leverage is None else self.leverage.to_dict(),
            "net_score": {k: round(v, 4) for k, v in self.net_score.items()},
            "target_qty": {k: round(v, 2) for k, v in self.target_qty.items()},
            "tilt": round(self.tilt, 3),
            "vol_used": self.vol_used,
        }


class MasterAllocator:
    name = "master"

    def __init__(
        self,
        risk: RiskConfig,
        weights: RegimeConditionedWeights | None = None,
        gate: AlphaGate | None = None,
        events: EventCalendar | None = None,
        lifecycles: dict[str, str] | None = None,
    ):
        self.risk = risk
        self.events = events if events is not None else EventCalendar()
        self.weights = weights or RegimeConditionedWeights()
        self.gate = gate or AlphaGate(risk, self.events)
        self.lifecycles: dict[str, str] = dict(lifecycles or {})
        self.oos_stats: dict[str, dict[str, float]] = {}
        self.family_returns: pd.DataFrame | None = None
        self.health_overall: str = "red"
        self.data_age_minutes: float | None = None
        self.last_debug = AllocationDebug()

    # ------------------------------------------------------------------ inputs the live job refreshes
    def set_context(
        self,
        health_overall: str | None = None,
        data_age_minutes: float | None = None,
        oos_stats: dict[str, dict[str, float]] | None = None,
        family_returns: pd.DataFrame | None = None,
        lifecycles: dict[str, str] | None = None,
    ) -> None:
        if health_overall is not None:
            self.health_overall = health_overall
        if data_age_minutes is not None:
            self.data_age_minutes = data_age_minutes
        if oos_stats is not None:
            self.oos_stats = dict(oos_stats)
        if family_returns is not None:
            self.family_returns = family_returns
        if lifecycles is not None:
            self.lifecycles = dict(lifecycles)

    # ------------------------------------------------------------------
    def _weight(self, strategy_id: str) -> float:
        lc = self.lifecycles.get(strategy_id)
        if lc is not None and lc != "active":
            return 0.0
        w = self.weights.current.weights
        if not w:
            # no weights yet (first run / incubation): equal weight over the strategies that signalled
            return 1.0
        return float(w.get(strategy_id, 0.0))

    @staticmethod
    def _split(signals: list[Signal]) -> tuple[list[Signal], list[Signal], list[Signal]]:
        live, tilts, info = [], [], []
        for s in signals:
            if s.meta.get("tilt_only"):
                tilts.append(s)
            elif s.direction is Direction.FLAT or s.meta.get("approx"):
                info.append(s)
            else:
                live.append(s)
        return live, tilts, info

    def _portfolio_vol(self, ctx: MarketContext) -> float:
        """Annualised volatility estimate used for volatility targeting: the most conservative available."""
        candidates = [ctx.f(cat.GARCH_VOL), ctx.f(cat.RV_YZ_21), ctx.f(cat.RV_CC_21)]
        ovx = ctx.f(cat.OVX)
        if not math.isnan(ovx) and ovx > 0:
            candidates.append(ovx / 100.0)
        usable = [c for c in candidates if isinstance(c, float) and math.isfinite(c) and c > 0]
        return max(usable) if usable else 0.40  # a deliberately high default when nothing is known

    # ------------------------------------------------------------------
    def decide(
        self,
        signals: list[Signal],
        ctx: MarketContext,
        snapshot: AccountSnapshot,
        positions: list[Position],
        risk: RiskConfig,
    ) -> list[Order]:
        equity = float(snapshot.equity)
        held = {p.instrument: p for p in positions}
        orders: list[Order] = []
        if equity <= 0:
            return orders

        live, tilts, _info = self._split(signals)
        tilt = 0.0
        if tilts:
            tilt = max(-MAX_TILT, min(MAX_TILT, sum(t.score for t in tilts) / len(tilts) * MAX_TILT * 4))

        vol = self._portfolio_vol(ctx)
        vol_target = float(risk.vol_target_annual)

        # gate and leverage are account-level decisions (one per round)
        weight_map = {s.strategy_id: self._weight(s.strategy_id) for s in live}
        gate = self.gate.evaluate(
            live,
            ctx,
            drawdown=float(snapshot.drawdown),
            weights=weight_map,
            oos_stats=self.oos_stats,
            family_returns=self.family_returns,
            health_overall=self.health_overall,
            data_age_minutes=self.data_age_minutes,
        )
        agreeing = [s for s in live if s.direction is gate.direction and weight_map.get(s.strategy_id, 0.0) > 0]
        exp_ret = (
            sum(abs(s.expected_return) * weight_map[s.strategy_id] for s in agreeing)
            / max(1e-9, sum(weight_map[s.strategy_id] for s in agreeing))
            if agreeing
            else 0.0
        )
        exp_vol = max(s.expected_vol for s in agreeing) if agreeing else max(1e-6, vol * math.sqrt(5 / 252))
        horizon = max((s.horizon_days for s in agreeing), default=5)
        hours_to_event, _ = self.events.hours_to_next_binary(ctx.ts)
        geo_pctl = ctx.f(cat.GEO_INDEX)
        lev = compute_leverage(
            gate_passed=gate.passed,
            risk=risk,
            expected_return=exp_ret,
            expected_vol=exp_vol,
            horizon_days=horizon,
            vols={
                "garch": ctx.f(cat.GARCH_VOL),
                "ovx": (ctx.f(cat.OVX) / 100.0 if math.isfinite(ctx.f(cat.OVX)) else None),
                "realized": ctx.f(cat.RV_YZ_21),
            },
            drawdown=float(snapshot.drawdown),
            hours_to_event=hours_to_event,
            is_pre_weekend=bool(ctx.f(cat.IS_PRE_WEEKEND, 0.0) >= 1.0),
            geo_index_pctl=None if math.isnan(geo_pctl) else geo_pctl,
        )
        max_gross = lev.chosen * equity

        # net conviction per instrument
        by_instrument: dict[str, list[Signal]] = {}
        for s in live:
            if weight_map.get(s.strategy_id, 0.0) <= 0 and self.weights.current.weights:
                continue
            by_instrument.setdefault(s.instrument, []).append(s)

        net_scores: dict[str, float] = {}
        for instrument, group in by_instrument.items():
            total_w = sum(max(0.0, weight_map.get(s.strategy_id, 1.0)) for s in group)
            if total_w <= 0:
                continue
            net_scores[instrument] = (
                sum(s.score * max(0.0, weight_map.get(s.strategy_id, 1.0)) for s in group) / total_w
            )

        # volatility targeting: the exposure (as a fraction of equity) that hits the vol target at full conviction
        vol_scalar = min(1.0, vol_target / max(vol, 1e-6))
        targets: dict[str, float] = {}
        gross_used = 0.0
        for instrument, score in sorted(net_scores.items(), key=lambda kv: -abs(kv[1])):
            fraction = max(-1.0, min(1.0, score)) * vol_scalar * (1.0 + tilt)
            notional = fraction * equity * lev.chosen
            room = max_gross - gross_used
            if abs(notional) > room:
                notional = math.copysign(room, notional)
            price = ctx.price
            if abs(notional) < 1e-9 or price <= 0:
                targets[instrument] = 0.0
                continue
            qty = round_to_lots(notional / price, risk.lot_bbl)
            targets[instrument] = qty
            gross_used += abs(qty) * price

        self.last_debug = AllocationDebug(
            gate=gate, leverage=lev, net_score=net_scores, target_qty=targets, tilt=tilt, vol_used=vol
        )

        for instrument, target in targets.items():
            pos = held.get(instrument)
            current = pos.qty_bbl if pos is not None else 0.0
            final = apply_hysteresis(target, current, risk.hysteresis_bbl_fraction, risk.lot_bbl)
            if final == current:
                continue
            group = by_instrument.get(instrument, [])
            ids = [s.strategy_id for s in group]
            against = [
                s.strategy_id
                for s in live
                if s.instrument == instrument and s.direction.sign * (1 if final >= 0 else -1) < 0
            ]
            stops = [s.stop_pct for s in group if s.stop_pct]
            targets_pct = [s.target_pct for s in group if s.target_pct]
            trailing = [s.trailing_pct for s in group if s.trailing_pct]
            key = idempotency_key(snapshot.epoch, ctx.ts, instrument, stable_join(ids), self.name)
            orders.append(
                Order(
                    order_id=key[:16],
                    idempotency_key=key,
                    ts=ctx.ts,
                    account_id=snapshot.account_id,
                    instrument=instrument,
                    qty_bbl=final - current,
                    order_type=OrderType.MARKET,
                    reason=OrderReason.SIGNAL if current == 0.0 else OrderReason.REBALANCE,
                    rationale=self._rationale(group, against, gate, lev, final, ctx),
                    strategies_for=ids,
                    strategies_against=against,
                    regime=ctx.regime.label,
                    gate=gate.to_dict(),
                    leverage=lev.to_dict(),
                    stop_pct=statistics.median(stops) if stops else None,
                    target_pct=statistics.median(targets_pct) if targets_pct else None,
                    trailing_pct=statistics.median(trailing) if trailing else None,
                )
            )

        # close what no longer has support
        for instrument, pos in held.items():
            if instrument in targets or pos.qty_bbl == 0.0:
                continue
            key = idempotency_key(snapshot.epoch, ctx.ts, instrument, "close", self.name)
            orders.append(
                Order(
                    order_id=key[:16],
                    idempotency_key=key,
                    ts=ctx.ts,
                    account_id=snapshot.account_id,
                    instrument=instrument,
                    qty_bbl=-pos.qty_bbl,
                    order_type=OrderType.MARKET,
                    reason=OrderReason.REBALANCE,
                    rationale="Nessun segnale attivo su questo strumento: posizione chiusa.",
                    strategies_for=[],
                    strategies_against=[],
                    regime=ctx.regime.label,
                    gate=gate.to_dict(),
                    leverage=lev.to_dict(),
                )
            )
        return orders

    # ------------------------------------------------------------------
    def _rationale(
        self,
        group: list[Signal],
        against: list[str],
        gate: GateResult,
        lev: LeverageDecision,
        qty: float,
        ctx: MarketContext,
    ) -> str:
        side = "long" if qty > 0 else ("short" if qty < 0 else "flat")
        pro = ", ".join(f"{s.strategy_id}" for s in group) or "nessuna"
        lev_txt = f"Leva {lev.chosen:.2f}x — {lev.limited_by}".replace(".", ",", 1)
        first = group[0].rationale if group else ""
        contro = f" Contro: {', '.join(against)}." if against else ""
        gate_txt = "gate superato" if gate.passed else f"gate non superato ({len(gate.failed)} condizioni)"
        return (
            f"Posizione {side} su {ctx.instrument} in regime «{ctx.regime.label}». "
            f"Pro: {pro}.{contro} {first} {lev_txt} ({gate_txt})."
        ).strip()
