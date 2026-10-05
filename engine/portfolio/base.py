"""Allocator protocol and the minimal capped allocator used as a baseline and in tests.

The production allocator is `engine.portfolio.allocator.MasterAllocator` (S20 weights + alpha gate + leverage).
Both implement `Allocator`, so the trading session is identical in backtest and live.
"""

from __future__ import annotations

import statistics
from typing import Protocol, runtime_checkable

from engine.core.config import RiskConfig
from engine.core.events import AccountSnapshot, Direction, Order, OrderReason, OrderType, Position, Signal
from engine.core.ids import idempotency_key, stable_join
from engine.strategies.base import MarketContext


@runtime_checkable
class Allocator(Protocol):
    """Turns strategy signals into orders for one account."""

    name: str

    def decide(
        self,
        signals: list[Signal],
        ctx: MarketContext,
        snapshot: AccountSnapshot,
        positions: list[Position],
        risk: RiskConfig,
    ) -> list[Order]: ...


def round_to_lots(qty_bbl: float, lot_bbl: float) -> float:
    """Round toward zero to a whole number of lots."""
    if lot_bbl <= 0:
        return float(qty_bbl)
    lots = int(abs(qty_bbl) / lot_bbl)
    return float(lots * lot_bbl) * (1.0 if qty_bbl >= 0 else -1.0)


def apply_hysteresis(target_qty: float, current_qty: float, fraction: float, lot_bbl: float) -> float:
    """Keep the current position when the change is smaller than `fraction` of the larger of the two."""
    delta = target_qty - current_qty
    if abs(delta) < lot_bbl:
        return current_qty
    scale = max(abs(current_qty), abs(target_qty), lot_bbl)
    if abs(delta) < fraction * scale and current_qty != 0.0 and (target_qty == 0.0 or target_qty * current_qty > 0):
        return current_qty
    return target_qty


class CappedEqualWeightAllocator:
    """Baseline: average the signal scores per instrument and never exceed 1x gross exposure.

    Used for the shadow accounts (one strategy each, no gate, no leverage) and as the reference the dashboard
    compares the master against. Leverage above 1x is only ever produced by `MasterAllocator`.
    """

    name = "capped_equal_weight"

    def __init__(self, max_leverage: float = 1.0):
        self.max_leverage = float(max_leverage)

    def decide(
        self,
        signals: list[Signal],
        ctx: MarketContext,
        snapshot: AccountSnapshot,
        positions: list[Position],
        risk: RiskConfig,
    ) -> list[Order]:
        cap = min(self.max_leverage, risk.default_max_leverage)
        equity = float(snapshot.equity)
        held = {p.instrument: p for p in positions}
        by_instrument: dict[str, list[Signal]] = {}
        for s in signals:
            if s.direction is Direction.FLAT or s.meta.get("tilt_only") or s.meta.get("approx"):
                continue
            by_instrument.setdefault(s.instrument, []).append(s)

        orders: list[Order] = []
        if equity <= 0:
            return orders
        for instrument, group in by_instrument.items():
            score = sum(s.score for s in group) / len(group)
            price = ctx.price if instrument == ctx.instrument else None
            if price is None or price <= 0:
                price = ctx.price
            target = round_to_lots(score * cap * equity / price, risk.lot_bbl)
            pos_held = held.get(instrument)
            current = pos_held.qty_bbl if pos_held is not None else 0.0
            target = apply_hysteresis(target, current, risk.hysteresis_bbl_fraction, risk.lot_bbl)
            if target == current:
                continue
            stops = [s.stop_pct for s in group if s.stop_pct]
            ids = [s.strategy_id for s in group]
            orders.append(
                Order(
                    order_id=idempotency_key(snapshot.epoch, ctx.ts, instrument, stable_join(ids), self.name)[:16],
                    idempotency_key=idempotency_key(snapshot.epoch, ctx.ts, instrument, stable_join(ids), self.name),
                    ts=ctx.ts,
                    account_id=snapshot.account_id,
                    instrument=instrument,
                    qty_bbl=target - current,
                    order_type=OrderType.MARKET,
                    reason=OrderReason.SIGNAL,
                    rationale="; ".join(s.rationale for s in group)[:800],
                    strategies_for=ids,
                    regime=ctx.regime.label,
                    stop_pct=statistics.median(stops) if stops else None,
                    target_pct=None,
                    leverage={"chosen": cap, "limited_by": f"tetto {cap:g}x (allocatore base)"},
                )
            )
        # close positions no longer supported by any signal
        for instrument, pos in held.items():
            if instrument not in by_instrument and pos.qty_bbl != 0.0:
                orders.append(
                    Order(
                        order_id=idempotency_key(snapshot.epoch, ctx.ts, instrument, "close", self.name)[:16],
                        idempotency_key=idempotency_key(snapshot.epoch, ctx.ts, instrument, "close", self.name),
                        ts=ctx.ts,
                        account_id=snapshot.account_id,
                        instrument=instrument,
                        qty_bbl=-pos.qty_bbl,
                        order_type=OrderType.MARKET,
                        reason=OrderReason.REBALANCE,
                        rationale="Nessun segnale attivo: posizione chiusa.",
                        regime=ctx.regime.label,
                    )
                )
        return orders
