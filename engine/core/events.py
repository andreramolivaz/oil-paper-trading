"""Event and state records shared by backtest and live. All timestamps are UTC-aware datetimes.

Serialisation: every record has to_dict()/from_dict() producing plain JSON-able dicts with ISO timestamps.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar

from engine.core.timeutil import iso, parse_iso

T = TypeVar("T", bound="Record")


class Record:
    """Mixin for dataclasses: JSON-able dict conversion with ISO timestamps."""

    def to_dict(self) -> dict[str, Any]:
        def conv(v: Any) -> Any:
            if isinstance(v, datetime):
                return iso(v)
            if isinstance(v, StrEnum):
                return str(v)
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list | tuple):
                return [conv(x) for x in v]
            return v

        return {k: conv(v) for k, v in asdict(self).items()}  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls: type[T], d: dict[str, Any]) -> T:
        kw: dict[str, Any] = {}
        for f in fields(cls):  # type: ignore[arg-type]
            if f.name not in d:
                continue
            v = d[f.name]
            ann = str(f.type)
            if v is not None and "datetime" in ann and isinstance(v, str):
                v = parse_iso(v)
            kw[f.name] = v
        return cls(**kw)


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"

    @property
    def sign(self) -> int:
        return {"long": 1, "short": -1, "flat": 0}[self.value]

    @staticmethod
    def from_sign(x: float) -> Direction:
        if x > 0:
            return Direction.LONG
        if x < 0:
            return Direction.SHORT
        return Direction.FLAT


class OrderType(StrEnum):
    MARKET = "market"
    STOP = "stop"
    LIMIT = "limit"


class OrderReason(StrEnum):
    SIGNAL = "signal"
    REBALANCE = "rebalance"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    ROLL = "roll"
    CIRCUIT_BREAKER = "circuit_breaker"
    MARGIN_CALL = "margin_call"
    LIQUIDATION = "liquidation"
    RESET = "reset"
    STALE_DATA_DELEVER = "stale_data_delever"
    EXPIRY = "expiry"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    HALTED_BREAKER = "halted_breaker"  # daily loss breaker: flat until next session
    HALTED_STALE = "halted_stale"  # data stale: no new risk
    DEAD = "dead"  # equity <= floor: trading stopped until reset


@dataclass
class Bar(Record):
    """One price bar. `ts` is the bar END (UTC). `asof` is when we observed it (publication/download time)."""

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    open_interest: float | None = None
    interval: str = "1d"  # "1d", "30m", "1h"
    source: str = ""
    asof: datetime | None = None
    is_settlement: bool = False

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass
class Signal(Record):
    """What a strategy emits for the next decision. `prob` is the calibrated probability the trade is profitable."""

    strategy_id: str
    ts: datetime
    instrument: str
    direction: Direction
    prob: float  # calibrated P(profitable) in [0,1]; 0.5 = no edge
    expected_return: float  # expected return over the horizon, fraction of notional
    expected_vol: float  # expected vol over the horizon, fraction of notional
    horizon_days: int
    stop_pct: float | None = None  # distance of the stop from entry, fraction (positive)
    target_pct: float | None = None
    trailing_pct: float | None = None
    strength: float = 1.0  # 0..1 size multiplier suggested by the strategy
    family: str = ""
    regime: str = ""
    rationale: str = ""  # human readable, Italian for the dashboard
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Signed conviction in [-1, 1]."""
        return self.direction.sign * max(0.0, min(1.0, 2.0 * abs(self.prob - 0.5))) * self.strength


@dataclass
class Order(Record):
    order_id: str
    idempotency_key: str
    ts: datetime  # decision time; fill happens strictly after
    account_id: str
    instrument: str
    qty_bbl: float  # signed barrels (+ buy, - sell)
    order_type: OrderType = OrderType.MARKET
    stop_price: float | None = None
    limit_price: float | None = None
    reason: OrderReason = OrderReason.SIGNAL
    rationale: str = ""
    strategies_for: list[str] = field(default_factory=list)
    strategies_against: list[str] = field(default_factory=list)
    regime: str = ""
    gate: dict[str, Any] = field(default_factory=dict)  # alpha-gate outcome and conditions
    leverage: dict[str, Any] = field(default_factory=dict)  # components: kelly, vol, drawdown, event, cap, chosen
    stop_pct: float | None = None
    target_pct: float | None = None
    trailing_pct: float | None = None
    status: str = "new"  # new | filled | rejected | cancelled


@dataclass
class Fill(Record):
    fill_id: str
    order_id: str
    ts: datetime  # execution time (bar timestamp of the price used)
    account_id: str
    instrument: str
    qty_bbl: float
    price: float  # executed price incl. slippage
    reference_price: float  # the market price before slippage
    slippage: float  # USD/bbl, positive = adverse
    commission: float  # USD total
    price_source: str = ""
    reason: OrderReason = OrderReason.SIGNAL
    realized_pnl: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position(Record):
    instrument: str
    qty_bbl: float
    avg_price: float
    opened_ts: datetime
    stop_price: float | None = None
    target_price: float | None = None
    trailing_pct: float | None = None
    high_water: float | None = None  # best price since entry (for trailing)
    low_water: float | None = None
    strategies: list[str] = field(default_factory=list)
    last_price: float | None = None
    last_ts: datetime | None = None

    @property
    def side(self) -> Direction:
        return Direction.from_sign(self.qty_bbl)

    def notional(self, price: float | None = None) -> float:
        p = price if price is not None else (self.last_price or self.avg_price)
        return abs(self.qty_bbl) * p

    def unrealized(self, price: float | None = None) -> float:
        p = price if price is not None else (self.last_price or self.avg_price)
        return self.qty_bbl * (p - self.avg_price)


@dataclass
class AccountSnapshot(Record):
    """Mark-to-market snapshot written on every tick to equity.jsonl."""

    ts: datetime
    account_id: str
    epoch: int
    cash: float
    equity: float
    unrealized: float
    margin_used: float
    margin_level: float | None  # equity / margin_used; None when flat
    gross_notional: float
    net_notional: float
    leverage: float  # gross_notional / equity
    peak_equity: float
    drawdown: float  # fraction, >= 0
    daily_pnl: float
    status: AccountStatus
    price_source: str = ""
    price_asof: datetime | None = None
    liquidation_price: float | None = None
    n_positions: int = 0


@dataclass
class Epoch(Record):
    """One 'life' of the account between resets."""

    epoch: int
    started_ts: datetime
    ended_ts: datetime | None = None
    start_equity: float = 10_000.0
    end_equity: float | None = None
    max_equity: float | None = None
    min_equity: float | None = None
    end_reason: str | None = None  # "reset_manual" | "dead" | None
    n_trades: int = 0
