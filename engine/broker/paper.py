"""Paper broker (brief §10): next-price fills, adverse costs, stops/targets/trailing on intrabar extremes,
10 % margin with stop-out, leverage cap as last line of defence, dead account, roll, reset, audit trail.

The same ``PaperBroker`` drives the backtest and the live session (``engine/backtest/session.py`` and
``engine/live``): the session feeds ``Bar`` events through ``on_bar`` and ``Order`` events through ``submit``.

Execution rules implemented here (tests in ``tests/test_broker.py`` and ``tests/test_invariants.py``):

* **Fills happen at the first price strictly after the decision.** A queued MARKET order fills at the OPEN of the
  first bar whose START is strictly after ``order.ts`` (never at the signal bar's close, never at a price of a bar
  that was already trading when the decision was taken). Daily bars: the bar start is the ICE session open
  (01:00 London) of the bar's London trading date (or ``bar.ts`` itself for midnight-labelled daily bars);
  intraday bars: ``bar.ts - interval``. The fill price is the open moved adversely by half-spread + slippage
  (``CostModel``), commission is debited from cash.
* **Stops / targets / trailing** are evaluated between executions on the bar's high/low. If stop and target are
  both inside the bar, the STOP wins. If the bar opens through the stop (gap) the fill is at the OPEN with extra
  adverse slippage (``costs.gap_extra_slippage_bps``). Trailing stops ratchet on ``high_water``/``low_water``,
  evaluated against the PRE-bar water mark (we never assume the favourable extreme came first).
* **Mark-to-market at the close**, peak equity / drawdown, daily P&L with ``day_start_equity`` reset on the first
  bar of a new London trading date.
* **Margin.** ``margin_used = rate * gross_notional``. If ``equity / margin_used < stop_out_level`` positions are
  force-reduced at the close with ``stop_out_extra_slippage_bps`` until the margin level is back to >= 1.0 (or
  flat). Independently, if gross leverage drifts above ``max_leverage`` (hard 10x) through price moves the
  position is reduced back to the cap (brief §3 "leva <= 10x sempre"). Reasons: MARGIN_CALL for partial
  reductions, LIQUIDATION for full closes; ``fill.meta['trigger']`` says ``stop_out``/``leverage_cap``/``dead``.
* **Dead account.** ``equity <= dead_equity_fraction * initial`` (or <= 0) closes everything, sets DEAD, archives
  the epoch (``epochs.json`` when a store is attached). Only ``reset`` revives the account.
* **Daily loss breaker.** ``daily_pnl / day_start_equity <= -daily_loss_breaker`` closes everything
  (CIRCUIT_BREAKER) and halts (HALTED_BREAKER) until the next London trading date (auto-clears on its first bar).
* **Idempotency.** ``submit`` rejects an idempotency key seen before; every fill id is
  ``idempotency_key(order_id, bar.ts, instrument, 'fill')`` and persisted with ``append_jsonl_unique``; a bar with
  a timestamp not after the last processed bar of the same symbol/interval is ignored (duplicate cron run).
* **Multi-leg instruments.** The price fed for a spread symbol is the spread price per barrel of the first leg.
  Margin notional, costs in bps and percentage stops all use the FIRST LEG's outright price as reference
  (``last_prices[first leg]`` when the session marks it, else any outright of the same root, else |spread price|
  as a last resort). This deliberately ignores exchange spread-margin credits: conservative by design.
* **Netting.** One net position per instrument symbol: adds recompute the average price, reductions realize P&L
  against the average (no FIFO lots), a flip opens a new position at the fill price.
* **Lots.** Quantities are rounded toward zero to multiples of ``risk.lot_bbl``; the rounding is recorded in
  ``fill.meta`` (``qty_requested`` / ``qty_filled``).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from engine.broker import margin as mg
from engine.broker.costs import CostModel
from engine.core.calendar import add_business_days
from engine.core.config import RiskConfig
from engine.core.errors import DataUnavailable
from engine.core.events import (
    AccountSnapshot,
    AccountStatus,
    Bar,
    Epoch,
    Fill,
    Order,
    OrderReason,
    OrderType,
    Position,
)
from engine.core.ids import idempotency_key, short_id
from engine.core.instruments import Instrument
from engine.core.store import StateStore
from engine.core.timeutil import ICE_OPEN_LONDON, LONDON, ensure_utc, iso, london_date, parse_iso

log = logging.getLogger(__name__)

ACCOUNT_FILE = "account.json"
POSITIONS_FILE = "positions.json"
EPOCHS_FILE = "epochs.json"
TRADES_LOG = "trades"
ORDERS_LOG = "orders"
EQUITY_LOG = "equity"
MAX_REJECTIONS_KEPT = 200
_EPS = 1e-9
_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*(m|min|h|d|w)\s*$", re.IGNORECASE)


def bar_start(bar: Bar) -> datetime:
    """UTC start of a bar whose ``ts`` is its END. See module docstring for the daily-bar convention."""
    ts = ensure_utc(bar.ts)
    m = _INTERVAL_RE.match(bar.interval or "1d")
    if m is None:
        return ts - timedelta(days=1)  # unknown interval: be conservative
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit in {"m", "min"}:
        return ts - timedelta(minutes=n)
    if unit == "h":
        return ts - timedelta(hours=n)
    if unit == "w":
        return ts - timedelta(weeks=n)
    # daily: session open of the bar's London trading date, never after the bar's own timestamp
    day = london_date(ts)
    if n > 1:
        day = day - timedelta(days=n - 1)
    session_open = datetime.combine(day, ICE_OPEN_LONDON, tzinfo=LONDON).astimezone(ensure_utc(ts).tzinfo)
    return min(session_open, ts)


def _finite(x: float, fallback: float = 1e9) -> float:
    return x if math.isfinite(x) else fallback


def _sgn(x: float) -> int:
    return (x > 0) - (x < 0)


# ----------------------------------------------------------------------------------------------- state
@dataclass
class BrokerState:
    """Everything the broker needs to resume; serialised to ``account.json``."""

    account_id: str
    epoch: int = 1
    cash: float = 10_000.0
    positions: dict[str, Position] = field(default_factory=dict)
    status: AccountStatus = AccountStatus.ACTIVE
    peak_equity: float = 10_000.0
    day_start_equity: float = 10_000.0
    day_start_date: date | None = None
    last_mark_ts: datetime | None = None
    last_prices: dict[str, float] = field(default_factory=dict)
    last_price_source: str = ""
    last_price_asof: datetime | None = None
    pending: list[Order] = field(default_factory=list)
    processed_keys: set[str] = field(default_factory=set)
    n_fills: int = 0
    breaker_until: date | None = None
    # epoch bookkeeping (archived into epochs.json on death / reset)
    epoch_started_ts: datetime | None = None
    epoch_start_equity: float = 10_000.0
    epoch_min_equity: float = 10_000.0
    epoch_n_fills: int = 0
    epoch_ended_ts: datetime | None = None
    epoch_end_reason: str | None = None
    epoch_archived: bool = False
    # audit
    realized_pnl: float = 0.0
    total_commission: float = 0.0
    total_slippage_usd: float = 0.0
    last_equity: float = 10_000.0
    last_bar_ts: dict[str, datetime] = field(default_factory=dict)  # "symbol|interval" -> last processed bar end
    rejections: list[dict[str, Any]] = field(default_factory=list)
    last_reject_reason: str = ""

    @staticmethod
    def fresh(account_id: str, initial_capital: float, epoch: int = 1, ts: datetime | None = None) -> BrokerState:
        return BrokerState(
            account_id=account_id,
            epoch=epoch,
            cash=initial_capital,
            peak_equity=initial_capital,
            day_start_equity=initial_capital,
            epoch_started_ts=ts,
            epoch_start_equity=initial_capital,
            epoch_min_equity=initial_capital,
            last_equity=initial_capital,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "epoch": self.epoch,
            "cash": self.cash,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "status": str(self.status),
            "peak_equity": self.peak_equity,
            "day_start_equity": self.day_start_equity,
            "day_start_date": iso(self.day_start_date),
            "last_mark_ts": iso(self.last_mark_ts),
            "last_prices": dict(self.last_prices),
            "last_price_source": self.last_price_source,
            "last_price_asof": iso(self.last_price_asof),
            "pending": [o.to_dict() for o in self.pending],
            "processed_keys": sorted(self.processed_keys),
            "n_fills": self.n_fills,
            "breaker_until": iso(self.breaker_until),
            "epoch_started_ts": iso(self.epoch_started_ts),
            "epoch_start_equity": self.epoch_start_equity,
            "epoch_min_equity": self.epoch_min_equity,
            "epoch_n_fills": self.epoch_n_fills,
            "epoch_ended_ts": iso(self.epoch_ended_ts),
            "epoch_end_reason": self.epoch_end_reason,
            "epoch_archived": self.epoch_archived,
            "realized_pnl": self.realized_pnl,
            "total_commission": self.total_commission,
            "total_slippage_usd": self.total_slippage_usd,
            "last_equity": self.last_equity,
            "last_bar_ts": {k: iso(v) for k, v in self.last_bar_ts.items()},
            "rejections": list(self.rejections),
            "last_reject_reason": self.last_reject_reason,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> BrokerState:
        def _date(s: str | None) -> date | None:
            return date.fromisoformat(s) if s else None

        st = BrokerState(account_id=str(d["account_id"]))
        st.epoch = int(d.get("epoch", 1))
        st.cash = float(d.get("cash", 0.0))
        st.positions = {k: Position.from_dict(v) for k, v in dict(d.get("positions", {})).items()}
        st.status = AccountStatus(d.get("status", "active"))
        st.peak_equity = float(d.get("peak_equity", st.cash))
        st.day_start_equity = float(d.get("day_start_equity", st.cash))
        st.day_start_date = _date(d.get("day_start_date"))
        st.last_mark_ts = parse_iso(d.get("last_mark_ts"))
        st.last_prices = {k: float(v) for k, v in dict(d.get("last_prices", {})).items()}
        st.last_price_source = str(d.get("last_price_source", ""))
        st.last_price_asof = parse_iso(d.get("last_price_asof"))
        st.pending = [Order.from_dict(o) for o in d.get("pending", [])]
        st.processed_keys = {str(k) for k in d.get("processed_keys", [])}
        st.n_fills = int(d.get("n_fills", 0))
        st.breaker_until = _date(d.get("breaker_until"))
        st.epoch_started_ts = parse_iso(d.get("epoch_started_ts"))
        st.epoch_start_equity = float(d.get("epoch_start_equity", st.cash))
        st.epoch_min_equity = float(d.get("epoch_min_equity", st.cash))
        st.epoch_n_fills = int(d.get("epoch_n_fills", 0))
        st.epoch_ended_ts = parse_iso(d.get("epoch_ended_ts"))
        st.epoch_end_reason = d.get("epoch_end_reason")
        st.epoch_archived = bool(d.get("epoch_archived", False))
        st.realized_pnl = float(d.get("realized_pnl", 0.0))
        st.total_commission = float(d.get("total_commission", 0.0))
        st.total_slippage_usd = float(d.get("total_slippage_usd", 0.0))
        st.last_equity = float(d.get("last_equity", st.cash))
        st.last_bar_ts = {k: t for k, v in dict(d.get("last_bar_ts", {})).items() if (t := parse_iso(v)) is not None}
        st.rejections = [dict(r) for r in d.get("rejections", [])]
        st.last_reject_reason = str(d.get("last_reject_reason", ""))
        return st


# ---------------------------------------------------------------------------------------------- broker
class PaperBroker:
    """See module docstring. Construct fresh, from a ``BrokerState`` or with ``PaperBroker.load(store)``."""

    def __init__(
        self,
        account_id: str,
        risk: RiskConfig,
        state: BrokerState | None = None,
        store: StateStore | None = None,
        instruments: dict[str, Instrument] | None = None,
        costs: CostModel | None = None,
    ) -> None:
        self.account_id = account_id
        self.risk = risk
        self.costs = costs or CostModel(risk)
        self.store = store
        self.instruments: dict[str, Instrument] = dict(instruments or {})
        self.state = state if state is not None else BrokerState.fresh(account_id, risk.initial_capital)
        if self.state.account_id != account_id:
            raise ValueError(f"state belongs to account {self.state.account_id!r}, not {account_id!r}")
        self.vol_annual: float | None = None  # realized vol used by the spread model; set_market_vol()

    @classmethod
    def load(
        cls,
        account_id: str,
        risk: RiskConfig,
        store: StateStore,
        instruments: dict[str, Instrument] | None = None,
        costs: CostModel | None = None,
    ) -> PaperBroker:
        """Resume from ``account.json`` in the store (fresh account when the file does not exist)."""
        raw = store.read_json(ACCOUNT_FILE)
        state = BrokerState.from_dict(raw) if raw else None
        return cls(account_id, risk, state=state, store=store, instruments=instruments, costs=costs)

    # ------------------------------------------------------------------ small helpers
    def set_market_vol(self, vol_annual: float | None) -> None:
        """Realized vol (annualised fraction) the spread model uses when ``on_bar`` is not given one."""
        self.vol_annual = vol_annual

    def set_status(self, status: AccountStatus) -> None:
        """External control for stale-data / breaker handling. DEAD cannot be cleared this way (use reset)."""
        if self.state.status == AccountStatus.DEAD and status != AccountStatus.DEAD:
            raise ValueError("a dead account can only be revived by reset()")
        self.state.status = status
        if status != AccountStatus.HALTED_BREAKER:
            self.state.breaker_until = None
        self._persist()

    @property
    def positions(self) -> dict[str, Position]:
        return self.state.positions

    @property
    def status(self) -> AccountStatus:
        return self.state.status

    def pending_orders(self) -> list[Order]:
        return list(self.state.pending)

    def cancel_pending(self, order_ids: set[str] | None = None, reason: str = "cancelled") -> int:
        """Cancel queued orders (all, or the given ids). Returns the number cancelled."""
        keep: list[Order] = []
        n = 0
        for o in self.state.pending:
            if order_ids is None or o.order_id in order_ids:
                self._cancel(o, reason)
                n += 1
            else:
                keep.append(o)
        self.state.pending = keep
        return n

    def _round_lots(self, qty: float) -> float:
        lot = self.risk.lot_bbl
        if lot <= 0:
            return qty
        n = int(abs(qty) / lot + 1e-9)
        return _sgn(qty) * n * lot

    def _ceil_lots(self, qty_abs: float) -> float:
        lot = self.risk.lot_bbl
        if lot <= 0:
            return qty_abs
        return math.ceil(qty_abs / lot - 1e-9) * lot

    # ------------------------------------------------------------------ prices & accounting
    def _price_for(self, symbol: str) -> float | None:
        p = self.state.last_prices.get(symbol)
        if p is not None:
            return p
        pos = self.state.positions.get(symbol)
        if pos is not None:
            return pos.last_price if pos.last_price is not None else pos.avg_price
        return None

    def _leg_reference_price(self, symbol: str, own_price: float | None = None) -> float:
        """Outright price per barrel used for notional, bps costs and percentage stops (module docstring)."""
        ins = self.instruments.get(symbol)
        if ins is not None and len(ins.legs) > 1:
            leg = ins.legs[0].future
            p = self.state.last_prices.get(leg.code)
            if p is None:
                for sym, px in self.state.last_prices.items():
                    other = self.instruments.get(sym)
                    outright = other is None or len(other.legs) <= 1
                    if sym != symbol and outright and sym.startswith(leg.root) and "-" not in sym and "/" not in sym:
                        p = px
                        break
            if p is not None:
                return abs(p)
        p = own_price if own_price is not None else self._price_for(symbol)
        if p is None:
            raise DataUnavailable(f"no reference price known for {symbol}")
        return abs(p)

    def _unrealized(self, overrides: dict[str, float] | None = None) -> float:
        tot = 0.0
        for sym, pos in self.state.positions.items():
            p = overrides.get(sym) if overrides else None
            if p is None:
                p = self._price_for(sym)
            tot += pos.unrealized(p)
        return tot

    def _equity(self, overrides: dict[str, float] | None = None) -> float:
        return self.state.cash + self._unrealized(overrides)

    def _gross_net(self, overrides: dict[str, float] | None = None) -> tuple[float, float]:
        gross = net = 0.0
        for sym, pos in self.state.positions.items():
            own = overrides.get(sym) if overrides else None
            ref = self._leg_reference_price(sym, own)
            gross += abs(pos.qty_bbl) * ref
            net += pos.qty_bbl * ref
        return gross, net

    def _is_risk_reducing(self, symbol: str, qty: float) -> bool:
        pos = self.state.positions.get(symbol)
        if pos is None or qty == 0:
            return False
        return _sgn(qty) == -_sgn(pos.qty_bbl) and abs(qty) <= abs(pos.qty_bbl) + _EPS

    def _update_equity_stats(self, ts: datetime) -> float:
        eq = self._equity()
        st = self.state
        st.last_equity = eq
        st.peak_equity = max(st.peak_equity, eq)
        st.epoch_min_equity = min(st.epoch_min_equity, eq)
        st.last_mark_ts = ts
        if st.epoch_started_ts is None:
            st.epoch_started_ts = ts
        return eq

    def _roll_day(self, ts: datetime) -> None:
        """First event of a new London trading date: reset the daily P&L base and auto-clear the breaker."""
        d = london_date(ts)
        st = self.state
        if st.day_start_date is None or d > st.day_start_date:
            st.day_start_equity = self._equity()
            st.day_start_date = d
        if st.status == AccountStatus.HALTED_BREAKER and st.breaker_until is not None and d >= st.breaker_until:
            st.status = AccountStatus.ACTIVE
            st.breaker_until = None
            log.info("daily-loss breaker cleared on %s", d)

    # ------------------------------------------------------------------ persistence
    def _persist(self) -> None:
        if self.store is None:
            return
        self.store.write_json(ACCOUNT_FILE, self.state.to_dict())
        self.store.write_json(POSITIONS_FILE, [p.to_dict() for p in self.state.positions.values()])

    def _persist_fill(self, fill: Fill) -> None:
        if self.store is not None:
            self.store.append_jsonl_unique(TRADES_LOG, fill, "fill_id", ts=fill.ts)

    def _persist_order(self, order: Order) -> None:
        if self.store is not None:
            self.store.append_jsonl_unique(ORDERS_LOG, order, "idempotency_key", ts=order.ts)

    def _epoch_record(self) -> Epoch:
        st = self.state
        return Epoch(
            epoch=st.epoch,
            started_ts=st.epoch_started_ts or st.last_mark_ts or ensure_utc(datetime(1970, 1, 1)),
            ended_ts=st.epoch_ended_ts,
            start_equity=st.epoch_start_equity,
            end_equity=st.last_equity if st.epoch_ended_ts is not None else None,
            max_equity=st.peak_equity,
            min_equity=st.epoch_min_equity,
            end_reason=st.epoch_end_reason,
            n_trades=st.epoch_n_fills,
        )

    def _archive_epoch(self, ts: datetime, reason: str) -> Epoch:
        st = self.state
        if not st.epoch_archived:
            st.epoch_ended_ts = ts
            st.epoch_end_reason = reason
            st.epoch_archived = True
            st.last_equity = self._equity()
        rec = self._epoch_record()
        if self.store is not None:
            rows: list[dict[str, Any]] = [dict(r) for r in (self.store.read_json(EPOCHS_FILE, []) or [])]
            rows = [r for r in rows if int(r.get("epoch", -1)) != rec.epoch]
            rows.append(rec.to_dict())
            rows.sort(key=lambda r: int(r["epoch"]))
            self.store.write_json(EPOCHS_FILE, rows)
        return rec

    # ------------------------------------------------------------------ order intake
    def _reject(self, order: Order, reason: str) -> bool:
        order.status = "rejected"
        st = self.state
        st.last_reject_reason = reason
        st.rejections.append(
            {
                "order_id": order.order_id,
                "idempotency_key": order.idempotency_key,
                "ts": iso(order.ts),
                "instrument": order.instrument,
                "qty_bbl": order.qty_bbl,
                "reason": reason,
            }
        )
        del st.rejections[:-MAX_REJECTIONS_KEPT]
        log.warning("order %s rejected: %s", order.order_id, reason)
        self._persist_order(order)
        self._persist()
        return False

    def _cancel(self, order: Order, reason: str) -> None:
        order.status = "cancelled"
        self.state.last_reject_reason = reason
        log.info("order %s cancelled: %s", order.order_id, reason)

    def submit(self, order: Order) -> bool:
        """Queue an order for execution on the next bar; False (and ``order.status == 'rejected'``) when refused.

        Rejections: dead or breaker-halted account; duplicate idempotency key (idempotent re-run); quantity below
        one lot; risk-increasing order while HALTED_STALE; post-trade gross leverage above ``risk.max_leverage``
        at last known prices (last line of defence; the portfolio gate enforces 1x/alpha rules upstream).
        """
        st = self.state
        if order.account_id and order.account_id != self.account_id:
            return self._reject(order, f"order for account {order.account_id!r}, broker is {self.account_id!r}")
        if st.status == AccountStatus.DEAD:
            return self._reject(order, "account dead: reset required")
        if st.status == AccountStatus.HALTED_BREAKER:
            return self._reject(order, "daily loss breaker active until next London trading date")
        if order.idempotency_key in st.processed_keys:
            return self._reject(order, "duplicate idempotency_key (already processed)")
        qty = self._round_lots(order.qty_bbl)
        if qty == 0:
            return self._reject(order, f"qty {order.qty_bbl} below one lot of {self.risk.lot_bbl:g} bbl")
        reducing = self._is_risk_reducing(order.instrument, qty)
        if st.status == AccountStatus.HALTED_STALE and not reducing:
            return self._reject(order, "data stale: only risk-reducing orders accepted")
        if not reducing:
            try:
                ref = self._leg_reference_price(order.instrument)
            except DataUnavailable:
                return self._reject(order, f"no reference price for {order.instrument}: cannot check leverage")
            equity = self._equity()
            if equity <= 0:
                return self._reject(order, "non-positive equity")
            gross, _ = self._gross_net()
            pos = st.positions.get(order.instrument)
            cur = pos.qty_bbl if pos else 0.0
            pend = sum(self._round_lots(o.qty_bbl) for o in st.pending if o.instrument == order.instrument)
            post = gross - abs(cur) * ref + abs(cur + pend + qty) * ref
            lev = mg.leverage(post, equity)
            if lev > self.risk.max_leverage + _EPS:
                return self._reject(
                    order, f"post-trade leverage {lev:.2f}x exceeds hard cap {self.risk.max_leverage:g}x"
                )
        st.processed_keys.add(order.idempotency_key)
        order.status = "new"
        st.pending.append(order)
        self._persist_order(order)
        self._persist()
        return True

    # ------------------------------------------------------------------ execution core
    def _book(self, symbol: str, qty: float, price: float, ts: datetime, strategies: list[str]) -> float:
        """Net ``qty`` at ``price`` into the position for ``symbol``. Returns the gross realized P&L (USD)."""
        st = self.state
        pos = st.positions.get(symbol)
        realized = 0.0
        if pos is None or pos.qty_bbl == 0:
            st.positions[symbol] = Position(
                symbol, qty, price, ts, strategies=list(strategies), last_price=price, last_ts=ts
            )
        elif _sgn(pos.qty_bbl) == _sgn(qty):  # add: weighted average price
            tot = pos.qty_bbl + qty
            pos.avg_price = (abs(pos.qty_bbl) * pos.avg_price + abs(qty) * price) / abs(tot)
            pos.qty_bbl = tot
            pos.last_price, pos.last_ts = price, ts
            for s in strategies:
                if s not in pos.strategies:
                    pos.strategies.append(s)
        else:  # reduce or flip
            closed = min(abs(qty), abs(pos.qty_bbl))
            realized = closed * (price - pos.avg_price) * _sgn(pos.qty_bbl)
            remaining = pos.qty_bbl + qty
            if abs(remaining) < _EPS:
                del st.positions[symbol]
            elif _sgn(remaining) == _sgn(pos.qty_bbl):
                pos.qty_bbl = remaining
                pos.last_price, pos.last_ts = price, ts
            else:  # flip: brand-new position at the fill price, protection levels dropped
                st.positions[symbol] = Position(
                    symbol, remaining, price, ts, strategies=list(strategies), last_price=price, last_ts=ts
                )
        return realized

    def _set_protection(self, symbol: str, order: Order, ref_price: float) -> None:
        """Stop/target from the order's percentages, as a distance of ``pct * reference leg price`` from avg."""
        pos = self.state.positions.get(symbol)
        if pos is None:
            return
        sign = _sgn(pos.qty_bbl)
        if order.stop_pct:
            pos.stop_price = pos.avg_price - sign * abs(order.stop_pct) * ref_price
        if order.target_pct:
            pos.target_price = pos.avg_price + sign * abs(order.target_pct) * ref_price
        if order.trailing_pct:
            pos.trailing_pct = abs(order.trailing_pct)
        if pos.high_water is None or pos.low_water is None:
            pos.high_water = pos.low_water = pos.avg_price

    def _execute(
        self,
        symbol: str,
        qty: float,
        reference_price: float,
        ts: datetime,
        reason: OrderReason,
        order_id: str,
        *,
        price_source: str,
        event_window: bool = False,
        vol: float | None = None,
        extra_bps: float = 0.0,
        impact: bool = True,
        cost_per_bbl_override: float | None = None,
        strategies: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Fill:
        """Execute ``qty`` against ``reference_price`` with adverse costs; book, record and persist the fill."""
        st = self.state
        ref_leg = self._leg_reference_price(symbol, reference_price)
        equity_before = self._equity({symbol: reference_price})
        if cost_per_bbl_override is not None:
            comps = {"half_spread_bps": 0.0, "slippage_bps": 0.0, "extra_bps": 0.0, "total_bps": 0.0}
            cost_per_bbl = abs(cost_per_bbl_override)
        else:
            comps = self.costs.execution_bps(qty, ref_leg, equity_before, vol, event_window, extra_bps, impact)
            cost_per_bbl = ref_leg * comps["total_bps"] * 1e-4
        price = CostModel.adverse_price(qty, reference_price, cost_per_bbl)
        commission = self.costs.commission(qty)
        realized = self._book(symbol, qty, price, ts, strategies or [])
        st.cash += realized - commission
        st.realized_pnl += realized
        st.total_commission += commission
        st.total_slippage_usd += cost_per_bbl * abs(qty)
        st.n_fills += 1
        st.epoch_n_fills += 1
        equity_after = self._equity({symbol: reference_price})
        gross_after, _ = self._gross_net({symbol: reference_price})
        margin_used = mg.margin_required(gross_after, self.risk.margin_rate)
        fill_meta: dict[str, Any] = {
            "epoch": st.epoch,
            "reference_leg_price": ref_leg,
            "half_spread_bps": comps["half_spread_bps"],
            "slippage_bps": comps["slippage_bps"],
            "extra_slippage_bps": comps["extra_bps"],
            "total_adverse_bps": comps["total_bps"],
            "event_window": event_window,
            "vol_annual": vol,
            "equity_after": equity_after,
            "leverage_after": _finite(mg.leverage(gross_after, equity_after)),
            "margin_level_after": mg.margin_level(equity_after, margin_used),
            "cash_after": st.cash,
        }
        if meta:
            fill_meta.update(meta)
        fill = Fill(
            fill_id=idempotency_key(order_id, iso(ts), symbol, "fill"),
            order_id=order_id,
            ts=ts,
            account_id=self.account_id,
            instrument=symbol,
            qty_bbl=qty,
            price=price,
            reference_price=reference_price,
            slippage=cost_per_bbl,
            commission=commission,
            price_source=price_source,
            reason=reason,
            realized_pnl=realized,
            meta=fill_meta,
        )
        self._persist_fill(fill)
        return fill

    def _forced_fill(
        self,
        symbol: str,
        qty: float,
        reference_price: float,
        ts: datetime,
        reason: OrderReason,
        *,
        price_source: str,
        extra_bps: float = 0.0,
        impact: bool = True,
        event_window: bool = False,
        vol: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Fill:
        order_id = short_id(str(reason), symbol, iso(ts), self.state.epoch, qty)
        return self._execute(
            symbol,
            qty,
            reference_price,
            ts,
            reason,
            order_id,
            price_source=price_source,
            event_window=event_window,
            vol=vol,
            extra_bps=extra_bps,
            impact=impact,
            meta=meta,
        )

    def _cap_qty_for_leverage(self, symbol: str, qty: float, open_price: float, vol: float | None, ev: bool) -> float:
        """Largest lot multiple of |qty| keeping post-trade gross leverage within the cap at fill price."""
        st = self.state
        pos = st.positions.get(symbol)
        cur = pos.qty_bbl if pos else 0.0
        ref_leg = self._leg_reference_price(symbol, open_price)
        overrides = {symbol: open_price}
        equity_pre = self._equity(overrides)
        gross_all, _ = self._gross_net(overrides)
        other_gross = gross_all - abs(cur) * ref_leg
        cap = self.risk.max_leverage
        q = qty

        def post_leverage(qq: float) -> float:
            comps = self.costs.execution_bps(qq, ref_leg, equity_pre, vol, ev)
            cost = abs(qq) * ref_leg * comps["total_bps"] * 1e-4 + self.costs.commission(qq)
            eq_post = equity_pre - cost
            return mg.leverage(other_gross + abs(cur + qq) * ref_leg, eq_post)

        for _ in range(4):
            comps = self.costs.execution_bps(q, ref_leg, equity_pre, vol, ev)
            cost = abs(q) * ref_leg * comps["total_bps"] * 1e-4 + self.costs.commission(q)
            eq_post = equity_pre - cost
            if eq_post <= 0:
                return 0.0
            max_total = max(0.0, cap * eq_post - other_gross) / ref_leg  # allowed |position| in this symbol
            bound = max_total - abs(cur) if _sgn(q) == _sgn(cur) or cur == 0 else max_total + abs(cur)
            new_q = _sgn(q) * self._round_lots(max(0.0, min(abs(q), bound)))
            if new_q == q:
                break
            q = new_q
            if q == 0:
                return 0.0
        lot = self.risk.lot_bbl if self.risk.lot_bbl > 0 else abs(q)
        while q != 0 and post_leverage(q) > cap + 1e-7:
            q = _sgn(q) * max(0.0, abs(q) - lot)
        return q

    # ------------------------------------------------------------------ on_bar steps
    def _fill_pending(self, bar: Bar, start: datetime, ev: bool, vol: float | None) -> list[Fill]:
        st = self.state
        ts = ensure_utc(bar.ts)
        fills: list[Fill] = []
        keep: list[Order] = []
        for o in st.pending:
            if o.instrument != bar.symbol or not (ensure_utc(o.ts) < start):
                keep.append(o)
                continue
            if st.status in {AccountStatus.DEAD, AccountStatus.HALTED_BREAKER}:
                self._cancel(o, f"account {st.status}")
                continue
            qty = self._round_lots(o.qty_bbl)
            reducing = self._is_risk_reducing(o.instrument, qty)
            if st.status == AccountStatus.HALTED_STALE and not reducing:
                self._cancel(o, "data stale: risk-increasing order dropped")
                continue
            # reference price per order type (STOP/LIMIT are optional extras; MARKET is the brief's path)
            if o.order_type == OrderType.MARKET:
                ref = bar.open
            elif o.order_type == OrderType.STOP and o.stop_price is not None:
                hit = bar.high >= o.stop_price if qty > 0 else bar.low <= o.stop_price
                if not hit:
                    keep.append(o)
                    continue
                ref = max(bar.open, o.stop_price) if qty > 0 else min(bar.open, o.stop_price)
            elif o.order_type == OrderType.LIMIT and o.limit_price is not None:
                hit = bar.low <= o.limit_price if qty > 0 else bar.high >= o.limit_price
                if not hit:
                    keep.append(o)
                    continue
                ref = min(bar.open, o.limit_price) if qty > 0 else max(bar.open, o.limit_price)
            else:
                self._cancel(o, f"unsupported order type {o.order_type} without trigger price")
                continue
            meta: dict[str, Any] = {
                "order_ts": iso(o.ts),
                "bar_start": iso(start),
                "bar_ts": iso(ts),
                "bar_interval": bar.interval,
                "qty_requested": o.qty_bbl,
                "qty_filled": qty,
                "lot_rounded": qty != o.qty_bbl,
                "order_type": str(o.order_type),
                "regime": o.regime,
                "strategies_for": list(o.strategies_for),
                "strategies_against": list(o.strategies_against),
                "gate": dict(o.gate),
                "leverage_components": dict(o.leverage),
            }
            if not reducing:
                capped = self._cap_qty_for_leverage(o.instrument, qty, ref, vol, ev)
                if capped == 0:
                    self._cancel(o, "leverage cap at fill price leaves no room")
                    continue
                if capped != qty:
                    meta["qty_capped_by_leverage"] = {"from": qty, "to": capped}
                    meta["qty_filled"] = capped
                    qty = capped
            fill = self._execute(
                o.instrument,
                qty,
                ref,
                ts,
                o.reason,
                o.order_id,
                price_source=bar.source,
                event_window=ev,
                vol=vol,
                strategies=o.strategies_for,
                meta=meta,
            )
            ref_leg = self._leg_reference_price(o.instrument, ref)
            self._set_protection(o.instrument, o, ref_leg)
            o.status = "filled"
            fills.append(fill)
        st.pending = keep
        return fills

    def _check_protection(self, bar: Bar, ev: bool, vol: float | None) -> Fill | None:
        pos = self.state.positions.get(bar.symbol)
        if pos is None or pos.qty_bbl == 0:
            return None
        ts = ensure_utc(bar.ts)
        long = pos.qty_bbl > 0
        ref_leg = self._leg_reference_price(bar.symbol, bar.open)
        stop = pos.stop_price
        trail_binding = False
        if pos.trailing_pct:
            water = pos.high_water if long else pos.low_water
            if water is not None:
                dist = pos.trailing_pct * ref_leg
                trail = water - dist if long else water + dist
                if stop is None or (long and trail > stop) or (not long and trail < stop):
                    stop, trail_binding = trail, True
        hit_stop = stop is not None and ((long and bar.low <= stop) or (not long and bar.high >= stop))
        tgt = pos.target_price
        hit_target = tgt is not None and ((long and bar.high >= tgt) or (not long and bar.low <= tgt))
        if hit_stop and stop is not None:
            gap = (long and bar.open <= stop) or (not long and bar.open >= stop)
            ref = bar.open if gap else stop
            return self._forced_fill(
                bar.symbol,
                -pos.qty_bbl,
                ref,
                ts,
                OrderReason.TRAILING_STOP if trail_binding else OrderReason.STOP_LOSS,
                price_source=bar.source,
                extra_bps=self.costs.gap_extra_slippage_bps if gap else 0.0,
                event_window=ev,
                vol=vol,
                meta={
                    "stop_level": stop,
                    "gap": gap,
                    "target_level": tgt,
                    "target_also_hit": bool(hit_target),
                    "bar_ts": iso(ts),
                    "bar_open": bar.open,
                    "bar_high": bar.high,
                    "bar_low": bar.low,
                },
            )
        if hit_target and tgt is not None:
            # resting limit at the target: filled at the target, or at a better open when the bar gaps through it
            ref = max(bar.open, tgt) if long else min(bar.open, tgt)
            return self._forced_fill(
                bar.symbol,
                -pos.qty_bbl,
                ref,
                ts,
                OrderReason.TAKE_PROFIT,
                price_source=bar.source,
                impact=False,
                event_window=ev,
                vol=vol,
                meta={"target_level": tgt, "bar_ts": iso(ts), "bar_open": bar.open},
            )
        pos.high_water = bar.high if pos.high_water is None else max(pos.high_water, bar.high)
        pos.low_water = bar.low if pos.low_water is None else min(pos.low_water, bar.low)
        return None

    def _mark_prices(self, prices: dict[str, float], ts: datetime, source: str, asof: datetime | None) -> None:
        st = self.state
        for sym, p in prices.items():
            if p is None or not math.isfinite(p):
                continue
            st.last_prices[sym] = float(p)
            pos = st.positions.get(sym)
            if pos is not None:
                pos.last_price, pos.last_ts = float(p), ts
        st.last_price_source = source
        st.last_price_asof = ensure_utc(asof) if asof is not None else ts
        self._update_equity_stats(ts)

    def _close_positions(
        self,
        ts: datetime,
        price_map: dict[str, float],
        reason: OrderReason,
        *,
        extra_bps: float,
        price_source: str,
        meta: dict[str, Any] | None,
        symbols: list[str] | None = None,
    ) -> list[Fill]:
        fills: list[Fill] = []
        for sym in list(symbols or self.state.positions.keys()):
            pos = self.state.positions.get(sym)
            if pos is None or pos.qty_bbl == 0:
                continue
            px = price_map.get(sym)
            m = dict(meta or {})
            if px is None:
                px = self._price_for(sym)
                m["price_fallback"] = "last_mark"
                m["price_asof"] = iso(self.state.last_price_asof)
            if px is None:
                raise DataUnavailable(f"no price to close {sym}")
            fills.append(
                self._forced_fill(
                    sym, -pos.qty_bbl, px, ts, reason, price_source=price_source, extra_bps=extra_bps, meta=m
                )
            )
        return fills

    def _enforce_margin(self, ts: datetime, price_source: str, ev: bool, vol: float | None) -> list[Fill]:
        """Stop-out below ``stop_out_margin_level`` and leverage-cap enforcement; reduces largest notional first."""
        st = self.state
        r = self.risk
        fills: list[Fill] = []
        for _ in range(200):  # bounded: every iteration closes at least one lot
            if not st.positions:
                break
            equity = self._equity()
            gross, _ = self._gross_net()
            level = mg.margin_level(equity, mg.margin_required(gross, r.margin_rate))
            lev = mg.leverage(gross, equity)
            stop_out = level is not None and level < r.stop_out_margin_level
            over_cap = lev > r.max_leverage + 1e-7
            if not stop_out and not over_cap:
                break
            trigger = "stop_out" if stop_out else "leverage_cap"
            extra = self.costs.stop_out_extra_slippage_bps if stop_out else 0.0
            base_meta = {"trigger": trigger, "margin_level": level, "leverage": _finite(lev), "equity": equity}
            if equity <= 0:
                fills += self._close_positions(
                    ts, {}, OrderReason.LIQUIDATION, extra_bps=extra, price_source=price_source, meta=base_meta
                )
                break
            target_gross = r.max_leverage * equity
            if stop_out and r.margin_rate > 0:
                target_gross = min(target_gross, equity / r.margin_rate)  # back to margin level >= 1.0
            excess = gross - target_gross
            sym, pos = max(st.positions.items(), key=lambda kv: abs(kv[1].qty_bbl) * self._leg_reference_price(kv[0]))
            ref_leg = self._leg_reference_price(sym)
            px = self._price_for(sym)
            if px is None:
                raise DataUnavailable(f"no price to liquidate {sym}")
            q_close = min(abs(pos.qty_bbl), max(r.lot_bbl, self._ceil_lots(excess / ref_leg)))
            full = q_close >= abs(pos.qty_bbl) - _EPS
            reason = OrderReason.LIQUIDATION if full else OrderReason.MARGIN_CALL
            fills.append(
                self._forced_fill(
                    sym,
                    -_sgn(pos.qty_bbl) * q_close,
                    px,
                    ts,
                    reason,
                    price_source=price_source,
                    extra_bps=extra,
                    event_window=ev,
                    vol=vol,
                    meta=base_meta,
                )
            )
        return fills

    def _check_dead(self, ts: datetime, price_source: str) -> list[Fill]:
        st = self.state
        equity = self._equity()
        if st.status == AccountStatus.DEAD or not mg.is_dead(
            equity, self.risk.initial_capital, self.risk.dead_equity_fraction
        ):
            return []
        fills = self._close_positions(
            ts,
            {},
            OrderReason.LIQUIDATION,
            extra_bps=self.costs.stop_out_extra_slippage_bps,
            price_source=price_source,
            meta={"trigger": "dead", "equity": equity},
        )
        st.status = AccountStatus.DEAD
        st.breaker_until = None
        self.cancel_pending(reason="account dead")
        self._update_equity_stats(ts)
        self._archive_epoch(ts, "dead")
        log.error("account %s DEAD at %s: equity %.2f", self.account_id, iso(ts), self._equity())
        return fills

    def _check_breaker(self, ts: datetime, price_source: str) -> list[Fill]:
        st = self.state
        if st.status != AccountStatus.ACTIVE or st.day_start_equity <= 0:
            return []
        equity = self._equity()
        loss = (equity - st.day_start_equity) / st.day_start_equity
        if loss > -self.risk.daily_loss_breaker:
            return []
        fills = self._close_positions(
            ts,
            {},
            OrderReason.CIRCUIT_BREAKER,
            extra_bps=0.0,
            price_source=price_source,
            meta={"trigger": "daily_loss_breaker", "daily_return": loss, "day_start_equity": st.day_start_equity},
        )
        st.status = AccountStatus.HALTED_BREAKER
        st.breaker_until = add_business_days(london_date(ts), 1, "ICE")
        self.cancel_pending(reason="daily loss breaker")
        log.warning("daily loss breaker: %.2f%% on %s; halted until %s", loss * 100, london_date(ts), st.breaker_until)
        return fills

    # ------------------------------------------------------------------ public event API
    def on_bar(self, bar: Bar, event_window: bool = False, vol_annual: float | None = None) -> list[Fill]:
        """Process one bar for ``bar.symbol``; returns the fills it produced (module docstring lists the steps)."""
        ts = ensure_utc(bar.ts)
        st = self.state
        key = f"{bar.symbol}|{bar.interval}"
        last = st.last_bar_ts.get(key)
        if last is not None and ts <= last:
            log.warning("bar %s @ %s ignored: not after last processed bar %s", bar.symbol, iso(ts), iso(last))
            return []
        st.last_bar_ts[key] = ts
        vol = vol_annual if vol_annual is not None else self.vol_annual
        start = bar_start(bar)
        fills: list[Fill] = []
        self._roll_day(ts)
        fills += self._fill_pending(bar, start, event_window, vol)  # 1. next-price fills
        f = self._check_protection(bar, event_window, vol)  # 2. stops / targets / trailing
        if f is not None:
            fills.append(f)
        self._mark_prices({bar.symbol: bar.close}, ts, bar.source, bar.asof)  # 3. mark to market
        fills += self._enforce_margin(ts, bar.source, event_window, vol)  # 4. stop-out / leverage cap
        fills += self._check_dead(ts, bar.source)  # 5. dead account
        fills += self._check_breaker(ts, bar.source)  # 6. daily loss breaker
        self._update_equity_stats(ts)
        self._persist()
        return fills

    def mark(self, prices: dict[str, float], ts: datetime, source: str, asof: datetime | None) -> AccountSnapshot:
        """Intraday mark-to-market without filling anything (30-min job). Status changes come via set_status."""
        ts = ensure_utc(ts)
        self._roll_day(ts)
        self._mark_prices(prices, ts, source, asof)
        snap = self.snapshot(ts)
        if self.store is not None:
            row = snap.to_dict()
            row["snapshot_id"] = idempotency_key(self.account_id, self.state.epoch, iso(ts), "snapshot")
            self.store.append_jsonl_unique(EQUITY_LOG, row, "snapshot_id", ts=ts)
        self._persist()
        return snap

    def roll(self, from_symbol: str, to_symbol: str, from_price: float, to_price: float, ts: datetime) -> list[Fill]:
        """Roll the whole position from one contract to the next: close old, open new with the same quantity.

        P&L is computed PER CONTRACT: the old contract's P&L is realized at ``from_price`` against its average
        price, and the new position starts with ``avg_price`` = its own fill price (not a blended average), so the
        equity curve shows the roll yield explicitly. Protection levels (stop, target, water marks) are shifted
        by ``to_price - from_price`` so the risk distance is preserved on the new contract. The roll cost
        (``costs.roll_cost``) is applied as adverse slippage split evenly across the two legs; commissions on
        each leg. Idempotent: the same (epoch, from, to, ts) never rolls twice.
        """
        st = self.state
        pos = st.positions.get(from_symbol)
        if pos is None or pos.qty_bbl == 0:
            return []
        ts = ensure_utc(ts)
        key = idempotency_key(self.account_id, st.epoch, "roll", from_symbol, to_symbol, iso(ts))
        if key in st.processed_keys:
            return []
        st.processed_keys.add(key)
        qty = pos.qty_bbl
        ref_leg = self._leg_reference_price(from_symbol, from_price)
        half_cost_per_bbl = self.costs.roll_cost(qty, ref_leg) / abs(qty) / 2.0
        shift = to_price - from_price
        old = Position.from_dict(pos.to_dict())
        order_id = short_id("roll", from_symbol, to_symbol, iso(ts), st.epoch)
        meta = {"roll_from": from_symbol, "roll_to": to_symbol, "roll_differential": shift, "idempotency_key": key}
        f_close = self._execute(
            from_symbol,
            -qty,
            from_price,
            ts,
            OrderReason.ROLL,
            order_id + ":close",
            price_source="roll",
            cost_per_bbl_override=half_cost_per_bbl,
            meta={**meta, "leg": "close"},
        )
        st.last_prices[from_symbol] = float(from_price)
        f_open = self._execute(
            to_symbol,
            qty,
            to_price,
            ts,
            OrderReason.ROLL,
            order_id + ":open",
            price_source="roll",
            cost_per_bbl_override=half_cost_per_bbl,
            strategies=old.strategies,
            meta={**meta, "leg": "open"},
        )
        st.last_prices[to_symbol] = float(to_price)
        new = st.positions.get(to_symbol)
        if new is not None:
            new.opened_ts = old.opened_ts
            new.stop_price = None if old.stop_price is None else old.stop_price + shift
            new.target_price = None if old.target_price is None else old.target_price + shift
            new.trailing_pct = old.trailing_pct
            new.high_water = None if old.high_water is None else old.high_water + shift
            new.low_water = None if old.low_water is None else old.low_water + shift
            new.strategies = list(old.strategies)
        self._update_equity_stats(ts)
        self._persist()
        return [f_close, f_open]

    def close_all(
        self, ts: datetime, price_map: dict[str, float], reason: OrderReason, extra_slippage_bps: float = 0.0
    ) -> list[Fill]:
        """Close every position at ``price_map[symbol]`` (last mark when missing, flagged in meta)."""
        ts = ensure_utc(ts)
        self._mark_prices({k: v for k, v in price_map.items() if k in self.state.positions}, ts, "close_all", ts)
        fills = self._close_positions(
            ts, price_map, reason, extra_bps=extra_slippage_bps, price_source="close_all", meta={"trigger": str(reason)}
        )
        self._update_equity_stats(ts)
        self._persist()
        return fills

    def reset(self, ts: datetime) -> Epoch:
        """Archive the current epoch (closing any position with reason RESET) and start a new one at the initial
        capital, flat and ACTIVE. Returns the ARCHIVED epoch record; the new epoch number is ``state.epoch``."""
        ts = ensure_utc(ts)
        st = self.state
        if st.positions:
            self._close_positions(
                ts, {}, OrderReason.RESET, extra_bps=0.0, price_source="reset", meta={"trigger": "reset"}
            )
        self.cancel_pending(reason="reset")
        self._update_equity_stats(ts)
        archived = self._archive_epoch(ts, "reset_manual")
        new = BrokerState.fresh(self.account_id, self.risk.initial_capital, epoch=st.epoch + 1, ts=ts)
        new.processed_keys = set(st.processed_keys)
        new.last_prices = dict(st.last_prices)
        new.last_price_source = st.last_price_source
        new.last_price_asof = st.last_price_asof
        new.last_mark_ts = ts
        new.n_fills = st.n_fills
        new.last_bar_ts = dict(st.last_bar_ts)
        new.rejections = list(st.rejections)
        self.state = new
        self._persist()
        log.info("account %s reset at %s: epoch %d -> %d", self.account_id, iso(ts), archived.epoch, new.epoch)
        return archived

    def snapshot(self, ts: datetime) -> AccountSnapshot:
        """Mark-to-market view at last known prices (equity = cash + unrealized; margin = rate * gross)."""
        st = self.state
        r = self.risk
        ts = ensure_utc(ts)
        unreal = self._unrealized()
        equity = st.cash + unreal
        gross, net = self._gross_net() if st.positions else (0.0, 0.0)
        margin_used = mg.margin_required(gross, r.margin_rate)
        level = mg.margin_level(equity, margin_used)
        lev = mg.leverage(gross, equity)
        dd = max(0.0, (st.peak_equity - equity) / st.peak_equity) if st.peak_equity > 0 else 0.0
        return AccountSnapshot(
            ts=ts,
            account_id=self.account_id,
            epoch=st.epoch,
            cash=st.cash,
            equity=equity,
            unrealized=unreal,
            margin_used=margin_used,
            margin_level=None if level is None else _finite(level),
            gross_notional=gross,
            net_notional=net,
            leverage=_finite(lev),
            peak_equity=st.peak_equity,
            drawdown=dd,
            daily_pnl=equity - st.day_start_equity,
            status=st.status,
            price_source=st.last_price_source,
            price_asof=st.last_price_asof,
            liquidation_price=self._liquidation_price(),
            n_positions=len(st.positions),
        )

    def _liquidation_price(self) -> float | None:
        """Stop-out price of the main (largest-notional) outright position, other positions held fixed."""
        st = self.state
        if not st.positions:
            return None
        items = sorted(st.positions.items(), key=lambda kv: -abs(kv[1].qty_bbl) * self._leg_reference_price(kv[0]))
        sym, pos = items[0]
        ins = self.instruments.get(sym)
        if ins is not None and len(ins.legs) > 1:
            return None  # spread margin depends on the leg price, not on the spread price: not a single-price problem
        other_unreal = sum(p.unrealized(self._price_for(s)) for s, p in items[1:])
        other_margin = sum(abs(p.qty_bbl) * self._leg_reference_price(s) for s, p in items[1:]) * self.risk.margin_rate
        return mg.liquidation_price(
            pos.qty_bbl,
            pos.avg_price,
            st.cash,
            self.risk.margin_rate,
            self.risk.stop_out_margin_level,
            other_unrealized=other_unreal,
            other_margin=other_margin,
        )
