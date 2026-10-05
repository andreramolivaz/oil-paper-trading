"""TradingSession: the ONE event loop that drives both the backtest and the live paper trading.

A trading day is processed in this order (`run_day`):

1. **Bars first.** The day's bar(s) are fed to every broker. This is what fills the orders decided at the
   PREVIOUS settlement, at the first price after the decision, and what evaluates stops, margin and breakers.
2. **Roll.** If the front contract changed (ICE expiry minus `roll_buffer_days` business days), every broker
   rolls its position from the old code to the new one, using the real M1/M2 settlements from the curve when
   available and the flat front price with a roll cost (flagged approximate) when it is not.
3. **Decision at the settlement.** Features are built point-in-time at the ICE settlement of the day, the regime
   is inferred, every ready strategy emits a signal, the allocator turns the signals into orders for the master
   account, and each strategy's own signal is sized at 1x for its shadow account. Orders are queued, never
   filled now: they fill on the next bar (step 1 of the following day).

The session is idempotent: `run_day` for a day already processed is a no-op, and every order carries an
idempotency key, so a re-run of a cron job never duplicates a fill. State (brokers, last processed day, weights)
round-trips through the `StateStore`, which is what the live runner reloads on every invocation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from engine.backtest.metrics import PerfSummary, summarise
from engine.broker.paper import PaperBroker
from engine.core.calendar import contract_code, front_month
from engine.core.config import RiskConfig
from engine.core.errors import DataUnavailable
from engine.core.eventcal import EventCalendar
from engine.core.events import AccountSnapshot, Bar, Order, Signal
from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso, settlement_ts
from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.portfolio.base import Allocator, CappedEqualWeightAllocator
from engine.regime.base import LABEL_TRANSITION, RegimeState
from engine.strategies.base import EventInfo, MarketContext, Strategy

log = logging.getLogger(__name__)

SESSION_FILE = "session.json"
ROLL_BUFFER_DAYS = 3


@dataclass
class SessionState:
    """What the session itself must remember between runs (the brokers persist their own state)."""

    last_day: str | None = None  # ISO London trading date of the last processed day
    processed_days: list[str] = field(default_factory=list)  # last N days, for idempotency checks
    front_symbol: str | None = None
    n_decisions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_day": self.last_day,
            "processed_days": self.processed_days[-400:],
            "front_symbol": self.front_symbol,
            "n_decisions": self.n_decisions,
        }

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> SessionState:
        if not d:
            return SessionState()
        return SessionState(
            last_day=d.get("last_day"),
            processed_days=list(d.get("processed_days") or []),
            front_symbol=d.get("front_symbol"),
            n_decisions=int(d.get("n_decisions") or 0),
        )


@dataclass
class DayResult:
    day: date
    bars: int = 0
    fills: int = 0
    orders: int = 0
    signals: int = 0
    rolled: tuple[str, str] | None = None
    regime: str = ""
    skipped: str | None = None


class TradingSession:
    def __init__(
        self,
        md: MarketData,
        strategies: list[Strategy],
        master: PaperBroker,
        risk: RiskConfig,
        allocator: Allocator | None = None,
        shadow_allocator: Allocator | None = None,
        shadows: dict[str, PaperBroker] | None = None,
        feature_builder: Any | None = None,
        regime_model: Any | None = None,
        store: StateStore | None = None,
        events: EventCalendar | None = None,
        roll_buffer_days: int = ROLL_BUFFER_DAYS,
        strict_pit: bool = False,
        shadow_accounts: bool = True,
        state: SessionState | None = None,
        strategy_params: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.md = md
        self.strategies = list(strategies)
        self.master = master
        self.risk = risk
        self.allocator: Allocator = allocator or CappedEqualWeightAllocator()
        self.shadow_allocator: Allocator = shadow_allocator or CappedEqualWeightAllocator()
        self.store = store
        self.events = events if events is not None else EventCalendar()
        self.roll_buffer_days = int(roll_buffer_days)
        self.strict_pit = bool(strict_pit)
        self.strategy_params = dict(strategy_params or {})
        self.state = state or SessionState.from_dict(store.read_json(SESSION_FILE) if store else None)
        self._features: pd.DataFrame | None = None
        self._features_asof: datetime | None = None
        self._regime_history: list[RegimeState] = []

        if feature_builder is None:
            from engine.features.builder import FullFeatureBuilder

            feature_builder = FullFeatureBuilder()
        self.feature_builder = feature_builder
        if regime_model is None:
            from engine.regime.model import make_regime_model

            regime_model = make_regime_model(None)
        self.regime_model = regime_model

        self.shadows: dict[str, PaperBroker] = dict(shadows or {})
        if shadow_accounts and not self.shadows:
            for s in self.strategies:
                self.shadows[s.id] = PaperBroker(f"shadow-{s.id}", risk, store=None)

    # ------------------------------------------------------------------ persistence
    def save(self) -> None:
        if self.store is None:
            return
        self.store.write_json(SESSION_FILE, self.state.to_dict())
        self.store.write_json("broker_master.json", self.master.state.to_dict())
        self.store.write_json(
            "broker_shadows.json", {sid: b.state.to_dict() for sid, b in sorted(self.shadows.items())}
        )

    def load(self) -> None:
        """Reload session + broker state from the store (live runner calls this on every invocation)."""
        if self.store is None:
            return
        self.state = SessionState.from_dict(self.store.read_json(SESSION_FILE))
        from engine.broker.paper import BrokerState

        raw_master = self.store.read_json("broker_master.json")
        if raw_master:
            self.master.state = BrokerState.from_dict(raw_master)
        raw_shadows = self.store.read_json("broker_shadows.json") or {}
        for sid, raw in raw_shadows.items():
            if sid in self.shadows and raw:
                self.shadows[sid].state = BrokerState.from_dict(raw)

    # ------------------------------------------------------------------ market helpers
    def front_code(self, day: date) -> str:
        y, m = front_month("BZ", day, self.roll_buffer_days)
        return contract_code("BZ", y, m)

    def _price_row(self, day: date) -> pd.Series | None:
        ts = pd.Timestamp(day)
        if self.md.prices.empty or ts not in self.md.prices.index:
            return None
        row = self.md.prices.loc[ts]
        return row.iloc[0] if isinstance(row, pd.DataFrame) else row

    @staticmethod
    def _f(row: pd.Series, name: str) -> float | None:
        if name not in row.index:
            return None
        v = row[name]
        try:
            return None if pd.isna(v) else float(v)
        except (TypeError, ValueError):
            return None

    def day_bar(self, day: date) -> Bar | None:
        """The daily bar of the front contract, stamped at the ICE settlement of that day."""
        row = self._price_row(day)
        if row is None:
            return None
        close = self._f(row, "brent_front_close")
        if close is None or close <= 0:
            return None
        o = self._f(row, "brent_front_open") or close
        hi = self._f(row, "brent_front_high") or max(o, close)
        lo = self._f(row, "brent_front_low") or min(o, close)
        hi = max(hi, o, close)
        lo = min(lo, o, close)
        asof = self.md.published_at.get("prices")
        asof_ts = None
        if asof is not None and pd.Timestamp(day) in asof.index:
            val = asof.loc[pd.Timestamp(day)]
            asof_ts = ensure_utc(pd.Timestamp(val).to_pydatetime()) if not pd.isna(val) else None
        return Bar(
            symbol=self.front_code(day),
            ts=settlement_ts(day),
            open=o,
            high=hi,
            low=lo,
            close=close,
            volume=self._f(row, "brent_front_volume"),
            open_interest=self._f(row, "brent_front_oi"),
            interval="1d",
            source=str(self.md.meta.get("prices_source", "md")),
            asof=asof_ts,
            is_settlement=True,
        )

    def curve_row(self, day: date) -> pd.Series | None:
        ts = pd.Timestamp(day)
        if self.md.curve.empty or ts not in self.md.curve.index:
            return None
        row = self.md.curve.loc[ts]
        return row.iloc[0] if isinstance(row, pd.DataFrame) else row

    def curve_codes(self, day: date) -> dict[str, str]:
        """Map 'M1'.. to contract codes. Uses the archived M1_code when present, else the calendar."""
        from engine.core.calendar import listed_months

        out: dict[str, str] = {}
        months = listed_months("BZ", day, 12, self.roll_buffer_days)
        for i, (y, m) in enumerate(months, start=1):
            out[f"M{i}"] = contract_code("BZ", y, m)
        row = self.curve_row(day)
        if row is not None and "M1_code" in row.index and isinstance(row["M1_code"], str):
            archived = str(row["M1_code"])
            if archived and archived != out.get("M1"):
                log.debug("curve M1_code %s differs from calendar front %s on %s", archived, out.get("M1"), day)
        return out

    # ------------------------------------------------------------------ features and regime
    def features_at(self, day: date) -> pd.DataFrame:
        asof = settlement_ts(day)
        if self._features is not None and self._features_asof == asof:
            return self._features
        md = self.md.truncate(asof) if self.strict_pit else self.md
        frame = self.feature_builder.build(md, asof)
        self._features, self._features_asof = frame, asof
        return frame

    def regime_at(self, features: pd.DataFrame, day: date) -> RegimeState:
        asof = settlement_ts(day)
        try:
            return self.regime_model.infer(features, asof)
        except Exception as exc:  # a regime failure must never stop trading: fall back to "transition"
            log.warning("regime inference failed on %s: %s", day, exc)
            return RegimeState(ts=asof, regime_id=-1, label=LABEL_TRANSITION, confidence=0.0, model="fallback")

    def context(self, day: date, features: pd.DataFrame, regime: RegimeState, price: float) -> MarketContext:
        asof = settlement_ts(day)
        upcoming = self.events.next_events(asof, horizon_days=14)
        recent = self.events.events_between(asof - pd.Timedelta(days=5), asof)
        infos = [EventInfo(e.event_id, e.name, e.ts, e.binary, e.hours_from(asof)) for e in [*recent, *upcoming]]
        cut = features.loc[: pd.Timestamp(day)] if not features.empty else features
        return MarketContext(
            ts=asof,
            features=cut,
            regime=regime,
            price=price,
            instrument=self.front_code(day),
            curve=self.curve_row(day),
            curve_codes=self.curve_codes(day),
            next_events=infos,
            intraday=self.md.intraday,
        )

    # ------------------------------------------------------------------ the day
    def _already_processed(self, day: date) -> bool:
        return day.isoformat() in set(self.state.processed_days)

    def _roll_if_needed(self, day: date, price: float) -> tuple[str, str] | None:
        """Roll every broker when the front code changes. Returns (old, new) when a roll happened."""
        new_symbol = self.front_code(day)
        old_symbol = self.state.front_symbol
        self.state.front_symbol = new_symbol
        if old_symbol is None or old_symbol == new_symbol:
            return None
        row = self.curve_row(day)
        from_price = to_price = price
        approx = True
        if row is not None:
            m1, m2 = self._f(row, "M1"), self._f(row, "M2")
            if m1 and m2 and m1 > 0 and m2 > 0:
                from_price, to_price, approx = m1, m2, False
        ts = settlement_ts(day)
        rolled = False
        for broker in [self.master, *self.shadows.values()]:
            if broker.positions.get(old_symbol) is not None:
                fills = broker.roll(old_symbol, new_symbol, from_price, to_price, ts)
                rolled = rolled or bool(fills)
        if rolled and self.store is not None:
            self.store.append_jsonl(
                "rolls",
                {
                    "ts": iso(ts),
                    "from": old_symbol,
                    "to": new_symbol,
                    "from_price": from_price,
                    "to_price": to_price,
                    "approx": approx,
                },
                ts=ts,
            )
        return (old_symbol, new_symbol) if rolled else None

    def on_bar(self, bar: Bar, vol_annual: float | None = None) -> int:
        """Feed one bar to every broker. Returns the number of fills produced."""
        event_window = self.events.in_event_window(bar.ts)
        fills = self.master.on_bar(bar, event_window=event_window, vol_annual=vol_annual)
        if self.store is not None:
            for f in fills:
                self.store.append_jsonl_unique("trades", f.to_dict(), "fill_id", ts=bar.ts)
        n = len(fills)
        for broker in self.shadows.values():
            n += len(broker.on_bar(bar, event_window=event_window, vol_annual=vol_annual))
        return n

    def on_settlement(self, day: date) -> tuple[list[Signal], list[Order], RegimeState]:
        """Build features, infer the regime, collect signals and queue orders for the next bar."""
        bar_close = self.day_bar(day)
        if bar_close is None:
            raise DataUnavailable(f"no front price on {day}")
        price = bar_close.close
        features = self.features_at(day)
        regime = self.regime_at(features, day)
        ctx = self.context(day, features, regime, price)
        asof = settlement_ts(day)

        vol = ctx.f(cat.RV_YZ_21)
        if pd.isna(vol):
            vol = ctx.f(cat.RV_CC_21)
        if not pd.isna(vol):
            self.master.set_market_vol(float(vol))
            for b in self.shadows.values():
                b.set_market_vol(float(vol))

        signals: list[Signal] = []
        for strat in self.strategies:
            params = self.strategy_params.get(strat.id)
            if params:
                strat.params.update(params)
            try:
                if not strat.ready(ctx):
                    continue
                sig = strat.generate(ctx)
            except Exception as exc:  # one broken strategy must not stop the others
                log.warning("strategy %s failed on %s: %s", strat.id, day, exc)
                continue
            if sig is not None:
                signals.append(sig)

        if self.store is not None:
            for sig in signals:
                self.store.append_jsonl("signals", sig.to_dict(), ts=asof)
            self.store.append_jsonl("regime", regime.to_dict(), ts=asof)
        self._regime_history.append(regime)

        # master account
        snapshot = self.master.snapshot(asof)
        orders = list(self.allocator.decide(signals, ctx, snapshot, list(self.master.positions.values()), self.risk))
        for order in orders:
            self.master.submit(order)

        # shadow accounts: one strategy each, 1x, no gate
        by_strategy: dict[str, list[Signal]] = {}
        for sig in signals:
            by_strategy.setdefault(sig.strategy_id, []).append(sig)
        for sid, broker in self.shadows.items():
            own = by_strategy.get(sid, [])
            snap = broker.snapshot(asof)
            for order in self.shadow_allocator.decide(own, ctx, snap, list(broker.positions.values()), self.risk):
                broker.submit(order)

        self.state.n_decisions += 1
        return signals, orders, regime

    def run_day(self, day: date, decide: bool = True) -> DayResult:
        """Process one trading day: bars (fills) -> roll -> decision. Idempotent per London trading date."""
        res = DayResult(day=day)
        if self._already_processed(day):
            res.skipped = "already processed"
            return res
        bar = self.day_bar(day)
        if bar is None:
            res.skipped = "no price"
            return res

        res.fills += self.on_bar(bar)
        res.bars += 1
        res.rolled = self._roll_if_needed(day, bar.close)

        if decide:
            try:
                signals, orders, regime = self.on_settlement(day)
                res.signals, res.orders, res.regime = len(signals), len(orders), regime.label
            except DataUnavailable as exc:
                log.warning("no decision on %s: %s", day, exc)
                res.skipped = str(exc)

        self.state.last_day = day.isoformat()
        self.state.processed_days = [*self.state.processed_days[-400:], day.isoformat()]
        self._record_equity(day)
        self.save()
        return res

    def _record_equity(self, day: date) -> None:
        if self.store is None:
            return
        asof = settlement_ts(day)
        snap = self.master.snapshot(asof)
        self.store.append_jsonl("equity", snap.to_dict(), ts=asof)
        shadows = {sid: b.snapshot(asof).equity for sid, b in sorted(self.shadows.items())}
        self.store.append_jsonl("equity_shadows", {"ts": iso(asof), "equity": shadows}, ts=asof)

    # ------------------------------------------------------------------ reporting helpers
    def snapshot(self, ts: datetime | None = None) -> AccountSnapshot:
        return self.master.snapshot(ensure_utc(ts or datetime.now(tz=UTC)))

    def shadow_summaries(self, equity: dict[str, pd.Series] | None = None) -> dict[str, PerfSummary]:
        """Per-strategy performance summary from the recorded shadow equity curves.

        `equity` is the {strategy_id: equity series} the runner collects (or the store replays); brokers hold
        only their current state, not the full history.
        """
        curves = equity if equity is not None else self.shadow_equity_from_store()
        return {sid: summarise(curve) for sid, curve in curves.items() if curve is not None and len(curve) > 1}

    def shadow_equity_from_store(self) -> dict[str, pd.Series]:
        """Replay `equity_shadows` from the store into one series per strategy."""
        if self.store is None:
            return {}
        rows = self.store.read_jsonl("equity_shadows")
        if not rows:
            return {}
        frame = pd.DataFrame([{"ts": pd.Timestamp(r["ts"]), **(r.get("equity") or {})} for r in rows]).set_index("ts")
        return {str(col): frame[col].dropna().astype(float) for col in frame.columns}
