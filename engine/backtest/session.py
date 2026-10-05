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
from engine.core.instruments import GALLONS_PER_BARREL, Future, Instrument, Kind, Leg
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
        precompute_regime: bool = True,
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
        self.precompute_regime = bool(precompute_regime)
        self.strategy_params = dict(strategy_params or {})
        self.state = state or SessionState.from_dict(store.read_json(SESSION_FILE) if store else None)
        self._features: pd.DataFrame | None = None
        self._features_asof: datetime | None = None
        self._features_full: pd.DataFrame | None = None
        self._regime_rows: pd.DataFrame | None = None
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
        if self.master.store is None:
            # the master does not persist itself (backtest): keep a copy here so a reload can resume
            self.store.write_json("broker_master.json", self.master.state.to_dict())
        self.store.write_json(
            "broker_shadows.json", {sid: b.state.to_dict() for sid, b in sorted(self.shadows.items())}
        )

    def load(self, session_only: bool = False) -> None:
        """Reload session + broker state from the store (live runner calls this on every invocation).

        With `session_only=True` only the session bookkeeping is reloaded: the live runner builds its brokers
        with `PaperBroker.load`, whose `account.json` is the canonical account state, so reloading the session's
        own copy would risk two sources of truth.
        """
        if self.store is None:
            return
        self.state = SessionState.from_dict(self.store.read_json(SESSION_FILE))
        if session_only:
            return
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

    # ------------------------------------------------------------------ multi-leg instruments
    def price_instrument(self, symbol: str, day: date) -> float | None:
        """Daily price of a symbol a strategy may trade, in USD per barrel of its first leg.

        The strategies emit four shapes besides the outright front:

        * ``BZZ26-BZG27``   calendar spread   -> M(near) - M(far) from the archived Brent curve
        * ``BZZ26/CLK26``   Brent-WTI spread  -> Brent front close - WTI front close
        * ``CRACK321-CLK26`` 3-2-1 crack      -> (2*RBOB + HO) * 42 / 3 - WTI front close
        * ``FLY-a-b-c``     curve butterfly   -> M(a) - 2*M(b) + M(c) from the curve

        Returns None when any leg is unknown: an instrument we cannot price is simply not tradeable that day,
        which is why the calendar spread and the butterfly stay silent until the curve archive has history.
        """
        row = self._price_row(day)
        curve = self.curve_row(day)

        def curve_price(code: str) -> float | None:
            if curve is None:
                return None
            codes = self.curve_codes(day)
            for rank, rank_code in codes.items():
                if rank_code == code and rank in curve.index:
                    return self._f(curve, rank)
            return None

        if symbol.startswith("FLY-"):
            parts = symbol.split("-")[1:]
            if len(parts) != 3:
                return None
            legs = [curve_price(c) for c in parts]
            if any(v is None for v in legs):
                return None
            near, mid, far = (float(v) for v in legs)  # type: ignore[arg-type]
            return near - 2.0 * mid + far
        if symbol.startswith("CRACK321-"):
            if row is None:
                return None
            rbob, ho, wti = (self._f(row, "rbob_close"), self._f(row, "ho_close"), self._f(row, "wti_front_close"))
            if rbob is None or ho is None or wti is None:
                return None
            return (2.0 * rbob * GALLONS_PER_BARREL + ho * GALLONS_PER_BARREL) / 3.0 - wti
        if "/" in symbol:
            if row is None:
                return None
            brent, wti = self._f(row, "brent_front_close"), self._f(row, "wti_front_close")
            if brent is None or wti is None:
                return None
            return brent - wti
        if "-" in symbol:
            near_code, far_code = symbol.split("-", 1)
            near_leg, far_leg = curve_price(near_code), curve_price(far_code)
            if near_leg is None or far_leg is None:
                return None
            return near_leg - far_leg
        return None

    def register_instrument(self, symbol: str) -> Instrument | None:
        """Teach every broker the legs of a multi-leg symbol.

        The broker needs them to resolve the FIRST LEG's outright price, which is what the notional, the
        bps costs and the percentage stops are measured against (see the broker's module docstring). Without
        the registration a spread order is rejected because the leverage check has no reference price.
        """
        existing = self.master.instruments.get(symbol)
        if existing is not None:
            return existing
        if symbol.startswith("SYNOPT-"):
            # S18's synthetic option structures are an explicit approximation: the paper broker holds futures,
            # not options, so they are informational only and carry zero weight in the master by design.
            log.debug("%s is a synthetic option structure: informational only, never traded", symbol)
            return None
        instrument: Instrument | None = None
        try:
            if symbol.startswith("FLY-"):
                codes = symbol.split("-")[1:]
                if len(codes) == 3:
                    legs = (
                        Leg(Future.from_code(codes[0]), 1.0),
                        Leg(Future.from_code(codes[1]), -2.0),
                        Leg(Future.from_code(codes[2]), 1.0),
                    )
                    instrument = Instrument(symbol, Kind.CALENDAR_SPREAD, legs, "Butterfly di curva")
            elif symbol.startswith("CRACK321-"):
                crude = Future.from_code(symbol.split("-", 1)[1])
                rbob = Future("RB", crude.year, crude.month)
                ho = Future("HO", crude.year, crude.month)
                instrument = Instrument.crack_321(crude, rbob, ho)
            elif "/" in symbol:
                left, right = symbol.split("/", 1)
                instrument = Instrument.brent_wti(Future.from_code(left), Future.from_code(right))
            elif "-" in symbol:
                near, far = symbol.split("-", 1)
                instrument = Instrument.calendar_spread(Future.from_code(near), Future.from_code(far))
            else:
                instrument = Instrument.future(Future.from_code(symbol))
        except (ValueError, KeyError, IndexError) as exc:
            log.warning("cannot build an instrument for %s: %s", symbol, exc)
            return None
        if instrument is None:
            return None
        for broker in [self.master, *self.shadows.values()]:
            broker.instruments[symbol] = instrument
        return instrument

    def leg_prices(self, day: date) -> dict[str, float]:
        """Outright prices of the legs a multi-leg instrument may reference (WTI, RBOB, heating oil fronts).

        The broker measures a spread's notional, its bps costs and its percentage stops against the FIRST
        LEG's outright price. For the 3-2-1 crack that leg is a product, and nothing else in the session
        quotes products, so without these marks every crack order is refused for want of a reference price.
        Products are quoted in USD per gallon and converted to the barrel equivalent, which is the unit the
        crack is expressed in.
        """
        row = self._price_row(day)
        if row is None:
            return {}
        out: dict[str, float] = {}
        wti = self._f(row, "wti_front_close")
        if wti is not None and wti > 0:
            y, m = front_month("CL", day, self.roll_buffer_days)
            out[contract_code("CL", y, m)] = wti
        for column, root in (("rbob_close", "RB"), ("ho_close", "HO")):
            value = self._f(row, column)
            if value is None or value <= 0:
                continue
            y, m = front_month(root, day, self.roll_buffer_days)
            out[contract_code(root, y, m)] = value * GALLONS_PER_BARREL
        return out

    def mark_legs(self, day: date) -> None:
        """Feed the leg prices to every broker so multi-leg references resolve."""
        prices = self.leg_prices(day)
        if not prices:
            return
        ts = settlement_ts(day)
        source = str(self.md.meta.get("prices_source", "md"))
        for symbol in prices:
            self.register_instrument(symbol)
        for broker in [self.master, *self.shadows.values()]:
            broker.mark(prices, ts, source, None)

    def auxiliary_bars(self, day: date) -> list[Bar]:
        """Bars for the non-front instruments that have a pending order or an open position.

        Spreads are built from leg CLOSES, so open = high = low = close: the intrabar extremes of a synthetic
        spread are not observable from daily leg data, and inventing them would make stops fire on moves that
        may never have happened. Protective stops on spreads are therefore evaluated at the close only, which
        is the conservative reading.
        """
        wanted: set[str] = set()
        for broker in [self.master, *self.shadows.values()]:
            wanted |= {o.instrument for o in broker.pending_orders()}
            wanted |= {sym for sym, pos in broker.positions.items() if pos.qty_bbl != 0}
        wanted.discard(self.front_code(day))
        bars: list[Bar] = []
        for symbol in sorted(wanted):
            price = self.price_instrument(symbol, day)
            if price is None:
                continue
            bars.append(
                Bar(
                    symbol=symbol,
                    ts=settlement_ts(day),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    interval="1d",
                    source=f"{self.md.meta.get('prices_source', 'md')} (gambe)",
                    is_settlement=True,
                )
            )
        return bars

    # ------------------------------------------------------------------ features and regime
    def features_at(self, day: date) -> pd.DataFrame:
        """The feature frame as it was knowable at the settlement of `day`.

        Two paths, and they must agree:

        * `strict_pit=True` rebuilds from `md.truncate(settlement)` for every single day. It is the slow,
          obviously-correct path, used by the no-look-ahead tests.
        * the default path builds the whole history ONCE and slices it at `day`. This is equivalent only
          because every feature is causal (trailing windows, expanding fits, publication-filtered releases),
          which is what `tests/test_no_lookahead.py` pins down: rows up to T are identical whether or not
          later data exists, and both paths give the same signals and fills. Without this a multi-year
          backtest would rebuild a 9 000-row frame on each of its ~3 000 days.
        """
        asof = settlement_ts(day)
        if self.strict_pit:
            if self._features is not None and self._features_asof == asof:
                return self._features
            frame = self.feature_builder.build(self.md.truncate(asof), asof)
            self._features, self._features_asof = frame, asof
            return frame
        return self._full_features().loc[: pd.Timestamp(day)]

    def _full_features(self) -> pd.DataFrame:
        """Build (once) the feature frame over the whole available history."""
        if self._features_full is None:
            last = pd.Timestamp(self.md.prices.index.max()).date() if not self.md.prices.empty else None
            asof = settlement_ts(last) if last is not None else datetime.now(tz=UTC)
            self._features_full = self.feature_builder.build(self.md, asof)
        return self._features_full

    def regime_at(self, features: pd.DataFrame, day: date) -> RegimeState:
        """The regime inferred at the settlement of `day`.

        On the fast path the walk-forward history is computed once over the whole frame and the row for `day`
        is read back: the regime model's own tests prove `history()` is byte-identical on rows up to T when
        later rows are appended, so this is the same number at a fraction of the cost. `infer` stays the path
        used live (one day at a time) and under `strict_pit`.
        """
        asof = settlement_ts(day)
        try:
            if self.strict_pit or not self.precompute_regime:
                return self.regime_model.infer(features, asof)
            row = self._regime_row(day)
            return self.regime_model.infer(features, asof) if row is None else self._state_from_row(row, asof)
        except Exception as exc:  # a regime failure must never stop trading: fall back to "transition"
            log.warning("regime inference failed on %s: %s", day, exc)
            return RegimeState(ts=asof, regime_id=-1, label=LABEL_TRANSITION, confidence=0.0, model="fallback")

    def _regime_row(self, day: date) -> pd.Series | None:
        if self._regime_rows is None:
            history = self.regime_model.history(self._full_features())
            self._regime_rows = history if isinstance(history, pd.DataFrame) else pd.DataFrame()
        ts = pd.Timestamp(day)
        if self._regime_rows.empty or ts not in self._regime_rows.index:
            return None
        row = self._regime_rows.loc[ts]
        return row.iloc[0] if isinstance(row, pd.DataFrame) else row

    @staticmethod
    def _state_from_row(row: pd.Series, asof: datetime) -> RegimeState:
        probabilities = {
            str(col)[len("regime_p_") :]: float(row[col])
            for col in row.index
            if str(col).startswith("regime_p_") and not pd.isna(row[col])
        }
        regime_id = row.get("regime_id")
        conf = row.get("regime_conf")
        cp = row.get("bocpd_cp_prob")
        return RegimeState(
            ts=asof,
            regime_id=int(regime_id) if regime_id is not None and not pd.isna(regime_id) else -1,
            label=str(row.get("regime_label") or LABEL_TRANSITION),
            confidence=float(conf) if conf is not None and not pd.isna(conf) else 0.0,
            probabilities=probabilities,
            change_point_prob=float(cp) if cp is not None and not pd.isna(cp) else 0.0,
            model="history",
        )

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
                self.register_instrument(sig.instrument)
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
        self._record_decision(asof, signals, orders)

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

    def _record_decision(self, asof: datetime, signals: list[Signal], orders: list[Order]) -> None:
        """Persist the gate and leverage outcome even when no order follows: being flat has a reason too."""
        if self.store is None:
            return
        debug = getattr(self.allocator, "last_debug", None)
        record: dict[str, Any] = {
            "ts": iso(asof),
            "n_signals": len(signals),
            "n_orders": len(orders),
            "instruments": sorted({o.instrument for o in orders}),
            "strategies": sorted({s.strategy_id for s in signals}),
        }
        if debug is not None:
            as_dict = debug.to_dict() if hasattr(debug, "to_dict") else {}
            record["gate"] = as_dict.get("gate")
            record["leverage"] = as_dict.get("leverage")
            record["net_score"] = as_dict.get("net_score")
            record["target_qty"] = as_dict.get("target_qty")
            record["vol_used"] = as_dict.get("vol_used")
        self.store.append_jsonl("decisions", record, ts=asof)

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

        self.register_instrument(bar.symbol)
        res.fills += self.on_bar(bar)
        res.bars += 1
        self.mark_legs(day)
        for aux in self.auxiliary_bars(day):
            res.fills += self.on_bar(aux)
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
