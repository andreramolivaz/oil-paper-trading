"""The live tick of the desk: the same ``Desk`` as the backtest, fed what arrived since the last tick.

Every invocation rebuilds the books from the JSON on the data branch (GitHub Actions has no memory), then:

1. feeds each broker the intraday bars that COMPLETED since its last processed bar - a bar still forming, or
   one the late feed has not finished delivering, is left for the next tick, because feeding it would mark it
   as seen and the rest of it would never be checked against a stop;
2. charges a day's interest on any fund held above 1x;
3. for a futures vehicle, moves the position to the contract to hold tomorrow when that is not today's;
4. takes the daily decision once per trading day, at the first tick after the vehicle's decision time, and
   catches up the latest missed one if the scheduler was late - an order decided late simply fills later, at
   a real price, which is the honest version of a cron that fires when it wants to;
5. marks every book and appends an equity snapshot.

It is idempotent: a second tick in the same minute feeds no bar and takes no decision.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from engine.core.calendar import add_business_days, is_business_day
from engine.core.config import RiskConfig
from engine.core.events import Bar
from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso
from engine.desk.book import Book, BookConfig, load_books
from engine.desk.data import DeskData, VehicleSeries, held_contract
from engine.desk.engine import Desk
from engine.desk.vehicles import Vehicle, get_vehicle

log = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
DESK_DIR = "desk"
INTRADAY_INTERVAL = "30m"
INTRADAY_MINUTES = 30
# The feed is late (futures ten minutes, funds up to fifteen): a bar that ended three minutes ago is still
# missing its last quarter of an hour. A bar is treated as complete - and shown to a broker, which then never
# looks at it again - only once the feed has had time to finish it. The runner ticks at :18 and :48 for this.
FEED_DELAY = timedelta(minutes=15)
MAX_PRICE_AGE = timedelta(days=5)  # a mark older than this is not used to size a new order
# The two legs of a roll are priced at the same moment or the roll waits: the old contract at yesterday's close
# against the new one at today's price would book a day of market move as roll profit or loss.
ROLL_SYNC = timedelta(minutes=30)
ROOT_OF_VEHICLE = {"MCL": "CL"}


def desk_store(state_dir: Path) -> StateStore:
    return StateStore(Path(state_dir) / DESK_DIR)


def book_store(state_dir: Path, book_id: str) -> StateStore:
    return StateStore(Path(state_dir) / DESK_DIR / book_id)


def build_desk(state_dir: Path, risk: RiskConfig, configs: list[BookConfig] | None = None) -> Desk:
    configs = configs if configs is not None else load_books()
    books = [Book(cfg, risk, store=book_store(state_dir, cfg.id)) for cfg in configs]
    return Desk(books, store=desk_store(state_dir))


# ----------------------------------------------------------------------------------------------- calendar
def decision_day(vehicle: Vehicle, now: datetime) -> date:
    """Latest US business day whose decision time has already passed."""
    local = ensure_utc(now).astimezone(NEW_YORK)
    day = local.date()
    hour, minute = vehicle.decision_time_ny
    if is_business_day(day, "US") and local.time() >= time(hour, minute):
        return day
    return add_business_days(day, -1, "US")


def next_business_day(day: date) -> date:
    return add_business_days(day, 1, "US")


# ----------------------------------------------------------------------------------------------- bars
def _rows(frame: pd.DataFrame | None, symbol: str) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """The rows of ``symbol`` and their bar STARTS. ``frame`` is either a single-symbol table indexed by bar
    start, or a long table with ``ts`` and ``code``."""
    empty = pd.DatetimeIndex([], tz="UTC")
    if frame is None or frame.empty:
        return pd.DataFrame(), empty
    work = frame
    if "code" in work.columns:
        work = work[work["code"] == symbol]
        starts = pd.DatetimeIndex(pd.to_datetime(work["ts"], utc=True))
    else:
        starts = pd.DatetimeIndex(pd.to_datetime(work.index, utc=True))
    return (work, starts) if len(work) else (pd.DataFrame(), empty)


def last_quote(frame: pd.DataFrame | None, symbol: str, now: datetime) -> tuple[float | None, datetime | None]:
    """The newest price on file for ``symbol``, forming bar included, and the latest time it can be from.

    Marks and sizing want the freshest real price there is; they do not need the bar to be finished. The time
    returned is the bar's end, or ``now`` less the feed delay when the bar is still forming.
    """
    work, starts = _rows(frame, symbol)
    now_ts = pd.Timestamp(ensure_utc(now))
    for i in sorted(range(len(work)), key=lambda j: starts[j], reverse=True):
        if starts[i] > now_ts:
            continue
        close = float(work.iloc[i]["close"])
        if not math.isfinite(close) or close <= 0:
            continue
        end = starts[i] + pd.Timedelta(minutes=INTRADAY_MINUTES)
        seen = max(starts[i], min(end, now_ts - FEED_DELAY))
        return close, seen.to_pydatetime()
    return None, None


def completed_bars(
    frame: pd.DataFrame | None,
    symbol: str,
    now: datetime,
    after: datetime | None,
    delay: timedelta = FEED_DELAY,
) -> list[Bar]:
    """Intraday rows of ``symbol`` that ended at least ``delay`` before ``now`` and after ``after`` -> ``Bar``
    (ts = bar end). The delay is what makes "completed" true on a late feed (see ``FEED_DELAY``)."""
    work, starts = _rows(frame, symbol)
    if len(work) == 0:
        return []
    ends = starts + pd.Timedelta(minutes=INTRADAY_MINUTES)
    now_ts = pd.Timestamp(ensure_utc(now))
    keep = ends + pd.Timedelta(delay) <= now_ts
    if after is not None:
        keep &= ends > pd.Timestamp(ensure_utc(after))
    bars: list[Bar] = []
    order = sorted(range(len(work)), key=lambda i: ends[i])
    for i in order:
        if not keep[i]:
            continue
        row = work.iloc[i]
        close = float(row["close"])
        if not math.isfinite(close) or close <= 0:
            continue
        o = float(row["open"]) if pd.notna(row["open"]) and float(row["open"]) > 0 else close
        hi = float(row["high"]) if pd.notna(row["high"]) else max(o, close)
        lo = float(row["low"]) if pd.notna(row["low"]) and float(row["low"]) > 0 else min(o, close)
        end = ends[i].to_pydatetime()
        bars.append(
            Bar(
                symbol=symbol,
                ts=end,
                open=o,
                high=max(hi, o, close),
                low=min(lo, o, close),
                close=close,
                volume=None if pd.isna(row.get("volume")) else float(row["volume"]),
                interval=INTRADAY_INTERVAL,
                source="yahoo",
                asof=end,
            )
        )
    return bars


def _daily_close(data: DeskData, vehicle: str, symbol: str) -> tuple[float | None, date | None]:
    if vehicle in ROOT_OF_VEHICLE:
        wide = data.contracts.get(ROOT_OF_VEHICLE[vehicle])
        if wide is not None and symbol in wide.columns:
            col = wide[symbol].dropna()
            if len(col):
                return float(col.iloc[-1]), pd.Timestamp(col.index[-1]).date()
        return None, None
    series = data.series.get(vehicle)
    if series is not None and not series.bars.empty:
        return float(series.bars["close"].iloc[-1]), pd.Timestamp(series.bars.index[-1]).date()
    return None, None


def latest_price(data: DeskData, vehicle: str, symbol: str, now: datetime) -> tuple[float | None, datetime | None]:
    """Most recent real price of ``symbol``: the newest intraday quote, or the last daily close when that is
    from a LATER session (the intraday feed can die while the daily one lives, and a book must not be sized on
    a bar that is days old when a newer close is on file)."""
    now = ensure_utc(now)
    quote, quote_ts = last_quote(data.intraday.get(vehicle), symbol, now)
    close, day = _daily_close(data, vehicle, symbol)
    if quote is not None and quote_ts is not None and (day is None or quote_ts.astimezone(NEW_YORK).date() >= day):
        return quote, quote_ts
    if close is None or day is None:
        return quote, quote_ts
    session_close = datetime.combine(day, time(16, 0), tzinfo=NEW_YORK).astimezone(UTC)
    return close, min(now, session_close)


# ----------------------------------------------------------------------------------------------- the tick
@dataclass
class TickReport:
    now: datetime
    bars: int = 0
    fills: int = 0
    rolled: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    books: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "now": iso(self.now),
            "bars": self.bars,
            "fills": self.fills,
            "rolled": self.rolled,
            "decisions": self.decisions,
            "notes": self.notes,
            "books": self.books,
        }

    def message(self) -> str:
        orders = sum(1 for d in self.decisions if d.get("order_units"))
        text = f"{self.bars} barre, {self.fills} eseguiti, {len(self.decisions)} decisioni ({orders} ordini)"
        return text + (f", {self.rolled} roll" if self.rolled else "")


def _symbols_to_feed(desk: Desk, vehicle: Vehicle, today: date) -> list[str]:
    if vehicle.kind != "future":
        return [vehicle.id]
    root = ROOT_OF_VEHICLE[vehicle.id]
    wanted = {held_contract(root, today), held_contract(root, next_business_day(today))}
    for book in desk.books_on(vehicle.id):
        wanted |= {s for s, p in book.broker.positions.items() if p.qty_bbl != 0}
        wanted |= {o.instrument for o in book.broker.pending_orders()}
    return sorted(wanted)


def _forecast_row(series: VehicleSeries, day: date) -> tuple[dict[str, float | None], float | None] | None:
    ts = pd.Timestamp(day)
    if ts not in series.forecasts.index:
        return None
    row = series.forecasts.loc[ts]
    forecast = {k: (None if pd.isna(row[k]) else float(row[k])) for k in row.index}
    vol = series.vol.get(ts)
    return forecast, (None if vol is None or pd.isna(vol) else float(vol))


def live_tick(
    desk: Desk,
    data: DeskData,
    now: datetime | None = None,
    blocked: dict[str, str] | None = None,
) -> TickReport:
    """One pass over every vehicle that has a book. Never raises for one vehicle's missing data: the others
    still trade, and the reason is in ``report.notes``.

    ``blocked`` maps a vehicle to the reason its books may not take NEW decisions on this tick (a source they
    depend on is red). Bars, stops, margin, financing, rolls and marks are unaffected: a book is never left
    unattended because a download failed, it just does not add risk on data it cannot vouch for.
    """
    now = ensure_utc(now or datetime.now(tz=UTC))
    blocked = blocked or {}
    report = TickReport(now=now)
    today_ny = now.astimezone(NEW_YORK).date()
    for vehicle_id in desk.vehicles():
        vehicle = get_vehicle(vehicle_id)
        books = desk.books_on(vehicle_id)
        series = data.series.get(vehicle_id)
        frame = data.intraday.get(vehicle_id)
        if series is None:
            report.notes.append(f"{vehicle_id}: nessuna serie di prezzo, libri fermi")
            continue

        # 1. bars completed since the last tick, per book: a book that was just reset has seen none of them,
        #    the others must not be shown the same bar twice
        vol_last = float(series.vol.iloc[-1]) if len(series.vol) and pd.notna(series.vol.iloc[-1]) else None
        for symbol in _symbols_to_feed(desk, vehicle, today_ny):
            fed = 0
            for book in books:
                state = book.broker.state
                seen = state.last_bar_ts.get(f"{symbol}|{INTRADAY_INTERVAL}")
                if seen is None:
                    # A symbol this book was never fed: a new book, or the contract a position has just been
                    # rolled into. Nothing before the book's last mark can matter to it, and feeding the
                    # archive's older bars would mark a live position at prices that are days old - enough to
                    # trip a stop-out that never happened.
                    seen = state.last_mark_ts or now
                for bar in completed_bars(frame, symbol, now, seen):
                    report.fills += len(book.broker.on_bar(bar, vol_annual=vol_last))
                    fed += 1
            report.bars += fed
        if frame is None or frame.empty:
            report.notes.append(f"{vehicle_id}: nessuna barra intraday archiviata (ordini e stop in attesa)")

        # 2. interest on borrowed cash
        desk.accrue_financing(vehicle_id, today_ny, now)

        # 3-4. roll and decision, once per trading day
        day = decision_day(vehicle, now)
        pending = [b for b in books if desk.state.last_decision_day.get(b.id) != day.isoformat()]
        if pending and vehicle_id in blocked:
            report.notes.append(f"{vehicle_id}: {blocked[vehicle_id]}")
        elif pending:
            inputs = _forecast_row(series, day)
            if inputs is None:
                last = series.last_day()
                report.notes.append(
                    f"{vehicle_id}: decisione del {day.isoformat()} in attesa, dati fermi al "
                    f"{'n/d' if last is None else last.date().isoformat()}"
                )
            else:
                forecast, vol = inputs
                symbol = (
                    held_contract(ROOT_OF_VEHICLE[vehicle_id], next_business_day(day))
                    if vehicle.kind == "future"
                    else vehicle_id
                )
                price, price_ts = latest_price(data, vehicle_id, symbol, now)
                if price is None or price_ts is None or now - price_ts > MAX_PRICE_AGE:
                    report.notes.append(f"{vehicle_id}: nessun prezzo recente per {symbol}, decisione rimandata")
                else:
                    if vehicle.kind == "future":
                        prices = {symbol: price}
                        for book in books:
                            for old in book.other_contract_positions(symbol):
                                old_price, old_ts = latest_price(data, vehicle_id, old, now)
                                if old_price is not None and old_ts is not None and abs(old_ts - price_ts) <= ROLL_SYNC:
                                    prices[old] = old_price
                        report.rolled += desk.roll(vehicle_id, symbol, prices, now)
                        waiting = [b.id for b in pending if b.other_contract_positions(symbol)]
                        if waiting:
                            # these books keep the old contract and their decision stays owed: both are tried
                            # again on the next tick (the calendar leaves five sessions before the expiry)
                            report.notes.append(
                                f"{vehicle_id}: roll verso {symbol} rimandato per {', '.join(waiting)} (il vecchio "
                                "contratto non ha un prezzo dello stesso momento): decisione in attesa"
                            )
                    for decision in desk.decide(vehicle_id, now, day, symbol, price, forecast, vol):
                        report.decisions.append(decision.to_dict())

        # 5. mark
        for book in books:
            marks: dict[str, float] = {}
            asof: datetime | None = None
            symbols = {s for s, p in book.broker.positions.items() if p.qty_bbl != 0} or set(
                _symbols_to_feed(desk, vehicle, today_ny)[:1]
            )
            for symbol in symbols:
                price, price_ts = latest_price(data, vehicle_id, symbol, now)
                if price is not None:
                    marks[symbol] = price
                    asof = price_ts if asof is None or (price_ts is not None and price_ts > asof) else asof
            snap = book.broker.mark(marks, now, "yahoo (ritardo 10-15 min)", asof)
            report.books[book.id] = {
                "equity": round(float(snap.equity), 2),
                "leverage": round(float(snap.leverage), 3),
                "status": str(snap.status),
            }
    desk.save()
    return report
