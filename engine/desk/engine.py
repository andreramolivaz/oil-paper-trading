"""``Desk``: the one loop that drives the books, in the backtest and live.

A trading day is the same three steps in both worlds:

1. **Bars.** Every bar of the vehicle goes to every book's broker. That is what fills the order decided at the
   previous step 3 - at the first price strictly after the decision - and what marks the account and enforces
   margin, the leverage cap, the daily-loss breaker and the account floor.
2. **Roll** (futures only). When the contract to hold tomorrow is not the one held today, the position moves
   to it at today's prices, paying the roll cost. Nothing is ever carried into an expiry.
3. **Decision.** Each book reads the same forecast and volatility and queues at most one order, which
   replaces any order still waiting from an earlier decision.

The backtest feeds one daily bar per day; the live tick feeds the intraday bars that arrived since the last
tick and takes the decision once per trading day, after the vehicle's decision time. Both call the methods
below and nothing else.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from engine.core.events import Bar, Fill
from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso
from engine.desk.book import Book, Decision

log = logging.getLogger(__name__)

DESK_STATE_FILE = "desk_state.json"
DECISIONS_LOG = "decisions"


@dataclass
class DeskState:
    """What the desk itself remembers between live ticks (each broker persists its own account)."""

    last_decision_day: dict[str, str] = field(default_factory=dict)  # book id -> ISO date of the last decision
    last_financing_day: dict[str, str] = field(default_factory=dict)
    last_roll: dict[str, str] = field(default_factory=dict)  # book id -> "OLD->NEW @ date"

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_decision_day": dict(self.last_decision_day),
            "last_financing_day": dict(self.last_financing_day),
            "last_roll": dict(self.last_roll),
        }

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> DeskState:
        d = d or {}
        return DeskState(
            last_decision_day={str(k): str(v) for k, v in (d.get("last_decision_day") or {}).items()},
            last_financing_day={str(k): str(v) for k, v in (d.get("last_financing_day") or {}).items()},
            last_roll={str(k): str(v) for k, v in (d.get("last_roll") or {}).items()},
        )


class Desk:
    def __init__(self, books: list[Book], store: StateStore | None = None) -> None:
        self.books: dict[str, Book] = {b.id: b for b in books}
        self.store = store
        self.state = DeskState.from_dict(store.read_json(DESK_STATE_FILE) if store is not None else None)

    # ------------------------------------------------------------------ helpers
    def books_on(self, vehicle: str) -> list[Book]:
        return [b for b in self.books.values() if b.cfg.vehicle == vehicle]

    def vehicles(self) -> list[str]:
        return sorted({b.cfg.vehicle for b in self.books.values()})

    def save(self) -> None:
        if self.store is not None:
            self.store.write_json(DESK_STATE_FILE, self.state.to_dict())

    # ------------------------------------------------------------------ step 1: bars
    def on_bar(self, vehicle: str, bar: Bar, vol_annual: float | None = None) -> list[Fill]:
        """Feed one bar to every book on ``vehicle``. Returns the fills it produced."""
        fills: list[Fill] = []
        for book in self.books_on(vehicle):
            fills += book.broker.on_bar(bar, vol_annual=vol_annual)
        return fills

    def accrue_financing(self, vehicle: str, day: date, ts: datetime) -> float:
        """Interest on the cash borrowed to hold a fund above 1x, once per calendar day elapsed."""
        total = 0.0
        for book in self.books_on(vehicle):
            rate = book.vehicle.financing_rate
            if rate <= 0:
                continue
            last = self.state.last_financing_day.get(book.id)
            days = (day - date.fromisoformat(last)).days if last else 1
            self.state.last_financing_day[book.id] = day.isoformat()
            if days > 0:
                total += book.broker.charge_financing(rate, float(min(days, 10)), ts)
        return total

    # ------------------------------------------------------------------ step 2: roll
    def roll(self, vehicle: str, to_symbol: str, prices: dict[str, float], ts: datetime) -> int:
        """Move every position that is not on ``to_symbol`` onto it, at ``prices``. Returns positions rolled.

        A book whose old contract has no price today cannot roll: it keeps the old contract for now and the
        roll is retried on the next call (the calendar leaves five business days of room before the expiry).
        """
        rolled = 0
        to_price = prices.get(to_symbol)
        for book in self.books_on(vehicle):
            for old in book.other_contract_positions(to_symbol):
                from_price = prices.get(old)
                if from_price is None or to_price is None or from_price <= 0 or to_price <= 0:
                    log.warning("book %s: cannot roll %s -> %s without both prices", book.id, old, to_symbol)
                    continue
                if book.broker.roll(old, to_symbol, from_price, to_price, ensure_utc(ts)):
                    rolled += 1
                    self.state.last_roll[book.id] = f"{old}->{to_symbol} @ {ensure_utc(ts).date().isoformat()}"
            # an order still queued on the old contract can never fill: drop it, the decision that follows
            # the roll re-issues whatever is needed on the new one
            stale = {o.order_id for o in book.broker.pending_orders() if o.instrument != to_symbol}
            if stale:
                book.broker.cancel_pending(stale, reason="contract rolled")
        return rolled

    # ------------------------------------------------------------------ step 3: decision
    def decide(
        self,
        vehicle: str,
        ts: datetime,
        day: date,
        symbol: str,
        price: float,
        forecast: dict[str, float | None],
        vol: float | None,
        force: bool = False,
    ) -> list[Decision]:
        """One decision per book per trading day (idempotent on ``day`` unless ``force``)."""
        out: list[Decision] = []
        ts = ensure_utc(ts)
        for book in self.books_on(vehicle):
            if not force and self.state.last_decision_day.get(book.id) == day.isoformat():
                continue
            if book.other_contract_positions(symbol):
                # The roll did not happen (a price was missing). Sizing on the new contract now would see no
                # position there and buy the whole target on top of what is still held on the old one. The
                # decision stays owed and is taken as soon as the roll is done.
                log.warning("book %s: still on another contract than %s, decision postponed", book.id, symbol)
                continue
            book.broker.note_price(symbol, price)
            # A decision states the position to HOLD. An order still queued from an earlier decision was the
            # answer to an older question, so it is replaced, never added to: two decisions less than a bar
            # apart (a book started just before its decision time, a feed that delivered no bar in between)
            # would otherwise buy the same target twice.
            replaced = book.broker.cancel_pending(reason=f"sostituito dalla decisione del {day.isoformat()}")
            decision, order = book.decide(ts, day, symbol, price, forecast, vol)
            if replaced:
                decision.rationale += " L'ordine della decisione precedente era ancora in coda: annullato" + (
                    ", lo sostituisce questo." if order is not None else "."
                )
                if order is not None:
                    order.rationale = decision.rationale
            if order is not None:
                accepted = book.broker.submit(order)
                decision.status = "ordine in coda" if accepted else f"rifiutato: {book.broker.state.last_reject_reason}"
            else:
                decision.status = decision.status or "nessun ordine"
            self.state.last_decision_day[book.id] = day.isoformat()
            if self.store is not None:
                self.store.append_jsonl(DECISIONS_LOG, _clean(decision.to_dict()), ts=ts)
            out.append(decision)
        return out


def _clean(obj: Any) -> Any:
    """Strict JSON: NaN and infinity become null."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    if isinstance(obj, datetime):
        return iso(obj)
    return obj
