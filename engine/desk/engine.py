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

import itertools
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from engine.core.events import Bar, Fill
from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso, london_date
from engine.desk.allocator import ROLE_RETIRED, AllocatorConfig, Scale, drawdown_scale, hormuz_weekend_scale
from engine.desk.book import Book, Decision
from engine.desk.vehicles import INVERSE_FUNDS

log = logging.getLogger(__name__)

DESK_STATE_FILE = "desk_state.json"
DECISIONS_LOG = "decisions"


@dataclass
class DeskState:
    """What the desk itself remembers between live ticks (each broker persists its own account)."""

    last_decision_day: dict[str, str] = field(default_factory=dict)  # book id -> ISO date of the last decision
    last_financing_day: dict[str, str] = field(default_factory=dict)
    last_roll: dict[str, str] = field(default_factory=dict)  # book id -> "OLD->NEW @ date"
    # the allocator (phase 13): the scales in force per book, and the roles its lifecycle rule has changed
    allocator: dict[str, Any] = field(default_factory=dict)  # book id -> Scale.to_dict() + "day"
    roles: dict[str, dict[str, str]] = field(default_factory=dict)  # book id -> {"role", "since", "reason"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_decision_day": dict(self.last_decision_day),
            "last_financing_day": dict(self.last_financing_day),
            "last_roll": dict(self.last_roll),
            "allocator": dict(self.allocator),
            "roles": dict(self.roles),
        }

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> DeskState:
        d = d or {}
        return DeskState(
            last_decision_day={str(k): str(v) for k, v in (d.get("last_decision_day") or {}).items()},
            last_financing_day={str(k): str(v) for k, v in (d.get("last_financing_day") or {}).items()},
            last_roll={str(k): str(v) for k, v in (d.get("last_roll") or {}).items()},
            allocator={str(k): dict(v) for k, v in (d.get("allocator") or {}).items()},
            roles={str(k): {str(a): str(b) for a, b in dict(v).items()} for k, v in (d.get("roles") or {}).items()},
        )


class Desk:
    def __init__(
        self, books: list[Book], store: StateStore | None = None, allocator: AllocatorConfig | None = None
    ) -> None:
        self.books: dict[str, Book] = {b.id: b for b in books}
        self.store = store
        self.state = DeskState.from_dict(store.read_json(DESK_STATE_FILE) if store is not None else None)
        # None: the books' own rules, nothing scaled (the replay "without the allocator", and the old tests)
        self.allocator = allocator

    # ------------------------------------------------------------------ helpers
    def books_on(self, vehicle: str) -> list[Book]:
        return [b for b in self.books.values() if b.cfg.vehicle == vehicle]

    def short_legs(self, vehicle: str) -> list[str]:
        """The inverse funds the books on ``vehicle`` use for their short side (usually none or one): the ones
        they are configured for, and any they still hold or have an order on."""
        return sorted({fund for b in self.books_on(vehicle) for fund in INVERSE_FUNDS if b.uses(fund)})

    def vehicles(self) -> list[str]:
        return sorted({b.cfg.vehicle for b in self.books.values()})

    def role_of(self, book: Book) -> str:
        """The book's role today: the configured one unless the lifecycle rule changed it."""
        override = self.state.roles.get(book.id)
        return override["role"] if override else book.cfg.role

    # ------------------------------------------------------------------ the allocator
    def allocate(
        self,
        vehicle: str,
        day: date,
        runup: tuple[bool, date | None] = (False, None),
        hormuz_ratio: float | None = None,
    ) -> dict[str, Scale]:
        """Set the allocator's scales on every book of ``vehicle`` for a decision taken on ``day``.

        ``runup`` is the bubble switch's state for the vehicle (on, and the last day the run-up held);
        ``hormuz_ratio`` the latest transit ratio that can be acted on. The drawdown rule reads each book's own
        account; the lifecycle's retirement reads ``state.roles``. Without an allocator every scale is 1.
        """
        out: dict[str, Scale] = {}
        cfg = self.allocator
        for book in self.books_on(vehicle):
            scale = Scale()
            if cfg is not None:
                active, since = runup
                if active:
                    scale.risk *= cfg.runup_scale
                    scale.reasons.append(
                        f"price doubled in two years (last on {since.isoformat() if since else 'n/a'}): "
                        f"risk x {cfg.runup_scale:g} for a year"
                    )
                st = book.broker.state
                dd_scale = drawdown_scale(st.last_equity, st.peak_equity, cfg)
                if dd_scale < 1.0:
                    scale.risk *= dd_scale
                    scale.reasons.append(f"drawdown control: risk x {dd_scale:.2f}")
                weekend = hormuz_weekend_scale(hormuz_ratio, cfg)
                if weekend < 1.0:
                    scale.weekend = weekend
                    scale.reasons.append(
                        f"AIS-visible tanker transits through Hormuz at {hormuz_ratio:.0%} of normal: "
                        f"weekend ceiling x {weekend:g}"
                    )
                if self.role_of(book) == ROLE_RETIRED:
                    scale.risk = 0.0
                    scale.reasons.append("retired by the lifecycle rule: no new risk")
            book.set_scale(scale.risk, scale.weekend, scale.reasons)
            self.state.allocator[book.id] = {**scale.to_dict(), "day": day.isoformat()}
            out[book.id] = scale
        return out

    def save(self) -> None:
        if self.store is not None:
            self.store.write_json(DESK_STATE_FILE, self.state.to_dict())

    # ------------------------------------------------------------------ step 1: bars
    def on_bar(self, vehicle: str, bar: Bar, vol_annual: float | None = None) -> list[Fill]:
        """Feed one bar to every book on ``vehicle``. Returns the fills it produced."""
        return self.on_bars(vehicle, [(bar, vol_annual)])

    def on_bars(self, vehicle: str, bars: Sequence[tuple[Bar, float | None]]) -> list[Fill]:
        """Feed every book on ``vehicle`` the bars it trades, each with the volatility its spread is priced on:
        the vehicle's own bars and, for a book with a short leg, the inverse fund's. See :meth:`feed`."""
        fills: list[Fill] = []
        for book in self.books_on(vehicle):
            fills += self.feed(book, bars)
        return fills

    @staticmethod
    def feed(book: Book, bars: Sequence[tuple[Bar, float | None]], late_until: datetime | None = None) -> list[Fill]:
        """Feed ONE book its bars, one MOMENT at a time (the live tick calls this per book, because each book
        has its own "last bar seen").

        Bars are grouped by their timestamp. Inside a moment every bar first fills what is queued on it and
        marks its symbol; only then are margin, the account floor and the daily-loss breaker checked, ONCE, and
        only if the moment brought a bar for something the book holds (or held until this moment). Checked bar
        by bar, a book that holds one symbol was judged on another symbol's bar and its own half-hour-old
        quote: a breaker could trip, and sell, at a price that was already history.

        Among the bars of one moment the one that fills a SALE goes first: when a book changes side, the
        proceeds of the leg it sells are what pays for the leg it buys.

        ``late_until`` is the time of the newest PRICE the book's positions had been valued on when this tick
        began (the broker's ``last_price_asof`` - not the clock of the tick that marked them: a tick reads a
        feed that is fifteen minutes behind, and one that starts between a bar's end and its delivery has not
        seen that bar). A bar is LATE, and only fills the orders that were waiting for it at its open, when

        * it belongs to an accounting day the broker has already closed, or
        * it is a bar of something the book ALREADY HELD when the tick began and it ends before that time:
          its table was missing and has come back, and the position has a newer mark than this bar.

        A late bar does not re-mark and trips nothing: replayed as news it would price today's position at
        yesterday morning's level against today's opening equity. (Before, not "at or before": a quote from the
        first minutes of the NEXT bar is dated at that bar's start, which is this bar's end, and a bar the book
        has not been judged on is not old news for ending at the minute its successor began. And only for what
        was held: the bars that follow the late fill of a NEW position are the first news there is about it,
        however old the book's other prices are.) Without ``late_until`` (the replay, which feeds every bar
        once and in order) no bar is late.

        An inverse fund's bar is for the books that use that fund, and for nobody else.
        """
        broker = book.broker
        mine = sorted(
            ((bar, vol) for bar, vol in bars if bar.symbol not in INVERSE_FUNDS or book.uses(bar.symbol)),
            key=lambda item: ensure_utc(item[0].ts),
        )
        cutoff = None if late_until is None else ensure_utc(late_until)
        valued = {s for s, p in broker.positions.items() if p.qty_bbl != 0}  # what ``late_until`` is about
        fills: list[Fill] = []
        for moment, members in itertools.groupby(mine, key=lambda item: ensure_utc(item[0].ts)):
            group = list(members)
            opened = broker.state.day_start_date  # the accounting day in progress (None: a new account)
            closed_day = cutoff is not None and opened is not None and london_date(moment) < opened
            known = cutoff is not None and moment < cutoff
            selling = {o.instrument for o in broker.pending_orders() if o.qty_bbl < 0}
            group.sort(key=lambda item: (item[0].symbol not in selling, item[0].symbol))
            involved = {s for s, p in broker.positions.items() if p.qty_bbl != 0}
            news: list[tuple[Bar, float | None]] = []
            for bar, vol in group:
                late = closed_day or (known and bar.symbol in valued)
                fills += broker.on_bar(bar, vol_annual=vol, mark=not late, checks=False)
                if not late:
                    news.append((bar, vol))
            involved |= {s for s, p in broker.positions.items() if p.qty_bbl != 0}
            judged = [(bar, vol) for bar, vol in news if bar.symbol in involved]
            if judged:
                # forced sales are priced with the widest spread of the moment's bars (the inverse fund's)
                vols = [vol for _, vol in judged if vol is not None]
                fills += broker.check_risk(moment, judged[0][0].source, vol_annual=max(vols) if vols else None)
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

    def credit_distributions(self, vehicle: str, distributions: dict[str, pd.Series], day: date, ts: datetime) -> float:
        """Credit each book on ``vehicle`` the cash its funds paid, for every ex-date up to ``day`` not yet seen.

        ``distributions`` is ``DeskData.distributions`` (symbol -> per-share amount by ex-date). A book that has
        never seen a symbol starts at ``day`` itself: the history before a book existed is not its income. The
        broker credits each (symbol, ex-date) once, on the shares held when the ex-date opened, so this may run
        on every tick. A futures contract pays nothing and is never in ``distributions``.
        """
        total = 0.0
        if not distributions:
            return total
        for book in self.books_on(vehicle):
            for symbol, paid in distributions.items():
                if symbol != book.vehicle.id and not book.uses(symbol):
                    continue
                seen = book.broker.state.distributions_seen.get(symbol)
                since = date.fromisoformat(seen) if seen else day - timedelta(days=1)
                due = paid[(paid.index > pd.Timestamp(since)) & (paid.index <= pd.Timestamp(day))]
                for ex_ts, per_share in zip(pd.DatetimeIndex(due.index), due.to_numpy(dtype="float64"), strict=True):
                    total += book.broker.credit_distribution(symbol, float(per_share), ex_ts.date(), ts)
                book.broker.note_distributions_seen(symbol, day)
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
        leg_prices: Mapping[str, float] | None = None,
        book_forecasts: Mapping[str, Mapping[str, float | None]] | None = None,
    ) -> list[Decision]:
        """One decision per book per trading day (idempotent on ``day`` unless ``force``). ``leg_prices`` are
        today's prices of the inverse funds, for the books that hold their short side in one.

        ``forecast`` is the desk's (the seven sleeves of the vehicle's series), read by every book on the desk's
        forecast. A book on another forecast (``BookConfig.forecast``, the Kalshi index of phase 14) reads its
        own entry of ``book_forecasts`` and NEVER the desk's: without an entry it has no forecast, and a book
        without a forecast holds what it has and says so (``Book.orders``).
        """
        out: list[Decision] = []
        ts = ensure_utc(ts)
        for book in self.books_on(vehicle):
            if not force and self.state.last_decision_day.get(book.id) == day.isoformat():
                continue
            own: Mapping[str, float | None] | None = (book_forecasts or {}).get(book.id)
            reads = forecast if book.replayable else (dict(own) if own is not None else {})
            if book.other_contract_positions(symbol):
                # The roll did not happen (a price was missing). Sizing on the new contract now would see no
                # position there and buy the whole target on top of what is still held on the old one. The
                # decision stays owed and is taken as soon as the roll is done.
                log.warning("book %s: still on another contract than %s, decision postponed", book.id, symbol)
                continue
            book.broker.note_price(symbol, price)
            leg = book.leg()
            leg_price = None if leg is None else (leg_prices or {}).get(leg.id)
            if leg is not None and leg_price is not None:
                book.broker.note_price(leg.id, leg_price)
            # A decision states the position to HOLD. An order still queued from an earlier decision was the
            # answer to an older question, so it is replaced, never added to: two decisions less than a bar
            # apart (a book started just before its decision time, a feed that delivered no bar in between)
            # would otherwise buy the same target twice.
            replaced = book.broker.cancel_pending(reason=f"replaced by the decision of {day.isoformat()}")
            decision, orders = book.orders(ts, day, symbol, price, dict(reads), vol, leg_price)
            if replaced:
                decision.rationale += " The previous decision's order was still queued: cancelled" + (
                    ", this one replaces it." if orders else "."
                )
                for order in orders:
                    order.rationale = decision.rationale
            if orders:
                refused = [o.instrument for o in orders if not book.broker.submit(o)]
                queued = len(orders) - len(refused)
                decision.n_orders = queued  # what is really in the queue, not what the book asked for
                decision.status = "order queued" if queued == 1 else f"{queued} orders queued"
                if refused:
                    why = f"refused ({', '.join(refused)}): {book.broker.state.last_reject_reason}"
                    decision.status = why if queued == 0 else f"{decision.status}; {why}"
                    # the rationale says "buy" and "sell": when the broker said no, it must say that too
                    decision.rationale += (
                        f" The broker refused the order on {' and on '.join(refused)}: it is not executed "
                        "(the reason is in the decision's status)."
                    )
            else:
                decision.status = decision.status or "no order"
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
