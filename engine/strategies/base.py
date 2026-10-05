"""Strategy contract. A strategy sees a point-in-time MarketContext and emits at most one Signal per instrument.

Rules for implementers (tests enforce the first two):
  * read only `ctx.features.loc[:ctx.ts]`; never index beyond ctx.ts (no look-ahead);
  * be deterministic for a given context;
  * fill `rationale` in Italian, one or two sentences, with the numbers that drove the decision;
  * `prob` is your honest, calibrated P(trade is profitable over horizon_days); emit 0.5 (or None) when no edge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from engine.core.events import Direction, Signal
from engine.regime.base import RegimeState


class Family:
    TREND = "trend"
    CARRY = "carry"
    RELATIVE_VALUE = "relative_value"
    FUNDAMENTAL = "fundamental"
    EVENT = "event"
    VOLATILITY = "volatility"
    ML = "ml"


@dataclass
class EventInfo:
    event_id: str
    name: str
    ts: datetime
    binary: bool
    hours_away: float


@dataclass
class MarketContext:
    """Everything a strategy may look at for the decision at `ts` (ICE settlement of a trading date)."""

    ts: datetime
    features: pd.DataFrame  # daily feature frame up to and including the row for ts (catalog names)
    regime: RegimeState
    price: float  # front settlement at ts (P&L reference)
    instrument: str  # default tradeable instrument symbol (front Brent future code)
    curve: pd.Series | None = None  # M1..Mn settlements at ts (NaN where unknown)
    curve_codes: dict[str, str] = field(default_factory=dict)  # 'M1' -> 'BZZ26'
    next_events: list[EventInfo] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)  # strategy params from config/strategies.yaml
    intraday: pd.DataFrame | None = None

    @property
    def row(self) -> pd.Series:
        return self.features.iloc[-1]

    def f(self, name: str, default: float = float("nan")) -> float:
        """Latest value of a feature, or default if missing/NaN."""
        if name not in self.features.columns or self.features.empty:
            return default
        v = self.features[name].iloc[-1]
        try:
            return default if pd.isna(v) else float(v)
        except (TypeError, ValueError):
            return default

    def hist(self, name: str, n: int) -> pd.Series:
        if name not in self.features.columns:
            return pd.Series(dtype=float)
        return self.features[name].iloc[-n:]

    def hours_to_next_binary_event(self) -> float:
        hrs = [e.hours_away for e in self.next_events if e.binary and e.hours_away >= 0]
        return min(hrs) if hrs else float("inf")


class Strategy(ABC):
    id: str = "S0"
    name: str = ""
    family: str = Family.TREND
    horizon_days: int = 5
    warmup_days: int = 300
    requires: tuple[str, ...] = ()  # feature names that must be non-NaN to emit a signal

    def __init__(self, params: dict[str, Any] | None = None):
        self.params: dict[str, Any] = dict(self.default_params())
        if params:
            self.params.update(params)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {}

    def ready(self, ctx: MarketContext) -> bool:
        if len(ctx.features) < self.warmup_days:
            return False
        return all(not pd.isna(ctx.f(r)) for r in self.requires)

    @abstractmethod
    def generate(self, ctx: MarketContext) -> Signal | None:
        """Return a Signal for the next bar, or None when flat/no opinion."""

    # helper for implementers
    def make_signal(
        self,
        ctx: MarketContext,
        direction: Direction,
        prob: float,
        expected_return: float,
        expected_vol: float,
        rationale: str,
        *,
        instrument: str | None = None,
        stop_pct: float | None = None,
        target_pct: float | None = None,
        trailing_pct: float | None = None,
        strength: float = 1.0,
        horizon_days: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Signal:
        return Signal(
            strategy_id=self.id,
            ts=ctx.ts,
            instrument=instrument or ctx.instrument,
            direction=direction,
            prob=float(min(0.99, max(0.01, prob))),
            expected_return=float(expected_return),
            expected_vol=float(max(1e-6, expected_vol)),
            horizon_days=horizon_days or self.horizon_days,
            stop_pct=stop_pct,
            target_pct=target_pct,
            trailing_pct=trailing_pct,
            strength=float(min(1.0, max(0.0, strength))),
            family=self.family,
            regime=ctx.regime.label,
            rationale=rationale,
            meta=meta or {},
        )
