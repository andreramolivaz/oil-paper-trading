"""A fund's cash distribution is income the raw bars show as a loss: the book must be credited it.

Found by review on 2026-10-13 (phase 12): the return series the forecast reads is Yahoo's adjusted close, the
fills and marks are the raw bars, so on an ex-date a book marked on the raw close lost ``shares x amount`` of cash
the real account receives - and could trip the daily-loss breaker on it (DBA pays 3.5-4.4 % every December). The
fix: ``fund_distributions`` reads the amounts off the table, the broker credits them once per (symbol, ex-date)
on the shares held when the ex-date opened, in the replay (before the day's bar) and on the live path (at the
tick whose download shows the ex-date).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.broker.paper import BrokerState
from engine.core.config import RiskConfig
from engine.core.events import Bar, Order
from engine.data.raw_store import RawStore
from engine.desk.backtest import run_backtest
from engine.desk.book import Book, BookConfig
from engine.desk.data import DeskData, build_desk_data, fund_distributions, price_only_series
from engine.desk.engine import Desk
from engine.desk.live import build_desk, live_tick
from tests.synthetic import make_desk_raw_frames, save_raw_frame

RISK = RiskConfig.load()
AGRI = BookConfig(
    id="agri",
    name="Agriculture",
    vehicle="DBA",
    vol_target=0.12,
    max_leverage=1.0,
    long_only=True,
    role="candidate",
    sleeves={"trend": 1.0, "accel": 1.0, "skew": 1.0},
)


def fund_table(ex_row: int | None, amount: float, n: int = 900, seed: int = 3, idx: pd.DatetimeIndex | None = None):
    """A calm uptrend (the book sits long near its 1x ceiling) with ONE distribution of ``amount`` per share on
    row ``ex_row``: the raw close falls by it that day, the adjusted close - what Yahoo gives for every earlier
    day - does not."""
    if idx is None:
        idx = pd.bdate_range("2023-01-02", periods=n, name="date")
    n = len(idx)
    rng = np.random.default_rng(seed)
    adj = 20.0 * np.exp(np.cumsum(rng.normal(0.0008, 0.006, size=n)))  # the total-return path
    raw = adj.copy()
    if ex_row is not None:
        # before the ex-date the raw price was higher by the amount: Yahoo scales those days by 1 - D / raw_prev
        prev = adj[ex_row - 1]
        raw[:ex_row] = adj[:ex_row] * (prev + amount) / prev
    return pd.DataFrame(
        {"open": raw * 0.9995, "high": raw * 1.003, "low": raw * 0.997, "close": raw, "adjclose": adj.round(6)},
        index=idx,
    )


# ------------------------------------------------------------------------------------------------- the broker
def _order(symbol: str, qty: float, ts: datetime, tag: str) -> Order:
    return Order(
        order_id=f"dist-{tag}",
        idempotency_key=f"dist-{tag}",
        ts=ts,
        account_id="desk-agri",
        instrument=symbol,
        qty_bbl=qty,
    )


def _bar(ts: datetime, open_: float, close: float) -> Bar:
    return Bar(
        symbol="DBA", ts=ts, open=open_, high=max(open_, close) * 1.001, low=min(open_, close) * 0.999, close=close,
        interval="30m", source="test",
    )  # fmt: skip


def test_the_broker_credits_a_distribution_once_on_the_shares_held_when_the_ex_date_opened():
    book = Book(AGRI, RISK)
    broker = book.broker
    friday = datetime(2025, 12, 19, 20, 30, tzinfo=UTC)
    ex_date = date(2025, 12, 22)
    # Friday: 100 shares bought at 20 on the 15:30 bar
    broker.note_price("DBA", 20.0)
    assert broker.submit(_order("DBA", 100.0, friday - timedelta(hours=1), "buy"))
    broker.on_bar(_bar(friday, 20.0, 20.0))
    assert broker.positions["DBA"].qty_bbl == 100.0
    # the ex-date turns: its first bar opens 0.912 lower; the holder of record is Friday's close
    first = datetime(2025, 12, 22, 15, 0, tzinfo=UTC)
    broker.on_bar(_bar(first, 19.09, 19.1))
    assert broker.state.day_start_date == ex_date and broker.state.day_start_positions == {"DBA": 100.0}
    # a sale during the day does not change what the ex-date pays
    assert broker.submit(_order("DBA", -60.0, first - timedelta(minutes=5), "sell"))
    broker.on_bar(_bar(first + timedelta(minutes=30), 19.1, 19.1))
    assert broker.positions["DBA"].qty_bbl == 40.0
    cash_before = broker.state.cash
    credited = broker.credit_distribution("DBA", 0.912, ex_date, first + timedelta(hours=1))
    assert credited == pytest.approx(91.2)
    assert broker.state.cash == pytest.approx(cash_before + 91.2)
    assert broker.state.total_distributions == pytest.approx(91.2)
    assert broker.state.distributions_seen["DBA"] == ex_date.isoformat()
    # the same ex-date is never credited twice, an older one never at all, a zero amount never
    assert broker.credit_distribution("DBA", 0.912, ex_date, first + timedelta(hours=2)) == 0.0
    assert broker.credit_distribution("DBA", 0.5, ex_date - timedelta(days=3), first) == 0.0
    assert broker.credit_distribution("DBA", 0.0, ex_date + timedelta(days=1), first) == 0.0
    assert broker.state.total_distributions == pytest.approx(91.2)
    # the state survives a round trip
    again = BrokerState.from_dict(broker.state.to_dict())
    assert again.total_distributions == pytest.approx(91.2)
    assert again.day_start_positions == {"DBA": 100.0} and again.distributions_seen == {"DBA": ex_date.isoformat()}
    # before the day has turned (the replay credits before the day's bar) the position as it stands pays;
    # a symbol not held pays nothing but is still marked as seen
    fresh = Book(AGRI, RISK).broker
    fresh.note_price("DBA", 20.0)
    assert fresh.submit(_order("DBA", 30.0, friday - timedelta(hours=1), "buy2"))
    fresh.on_bar(_bar(friday, 20.0, 20.0))
    assert fresh.credit_distribution("DBA", 1.0, ex_date, friday + timedelta(days=3)) == pytest.approx(30.0)
    empty = Book(AGRI, RISK).broker
    assert empty.credit_distribution("DBA", 1.0, ex_date, friday) == 0.0
    assert empty.state.distributions_seen["DBA"] == ex_date.isoformat()


# ------------------------------------------------------------------------------------------------- the desk step
def test_the_desk_credits_every_unseen_ex_date_up_to_today_and_starts_a_new_book_at_today():
    table = fund_table(None, 0.0, n=420)
    paid = pd.Series([0.5, 0.7], index=pd.DatetimeIndex([table.index[100], table.index[300]]), name="distribution")
    distributions = {"DBA": paid}
    book = Book(AGRI, RISK)
    desk = Desk([book])
    broker = book.broker
    t = datetime(2024, 6, 3, 20, 0, tzinfo=UTC)
    broker.note_price("DBA", 20.0)
    assert broker.submit(_order("DBA", 50.0, t - timedelta(hours=1), "buy"))
    broker.on_bar(_bar(t, 20.0, 20.0))
    assert broker.positions["DBA"].qty_bbl == 50.0
    # a book that has never seen the symbol starts today: the ex-date of row 100 (long before) is not its income
    today = table.index[200].date()
    assert desk.credit_distributions("DBA", distributions, today, t) == 0.0
    assert broker.state.distributions_seen == {"DBA": today.isoformat()}
    # the ex-date of row 300 arrives: credited once, whatever the number of ticks that see it
    ex_day = table.index[300].date()
    assert desk.credit_distributions("DBA", distributions, ex_day, t) == pytest.approx(35.0)
    assert desk.credit_distributions("DBA", distributions, ex_day, t) == 0.0
    assert desk.credit_distributions("DBA", distributions, ex_day + timedelta(days=1), t) == 0.0
    assert broker.state.total_distributions == pytest.approx(35.0)
    # learned late (a table missing for days): every ex-date since the last seen day is credited
    late = {"DBA": pd.Series([0.2, 0.3], index=pd.DatetimeIndex([table.index[310], table.index[315]]))}
    assert desk.credit_distributions("DBA", late, table.index[320].date(), t) == pytest.approx(25.0)
    # another vehicle's fund is not this book's income; a futures vehicle has no distributions at all
    assert desk.credit_distributions("DBA", {"GLD": paid}, table.index[400].date(), t) == 0.0
    assert desk.credit_distributions("DBA", {}, table.index[400].date(), t) == 0.0


# ------------------------------------------------------------------------------------------------- the replay
def test_the_replay_pays_the_distribution_and_the_ex_date_is_no_longer_a_loss():
    ex_row = 700
    amount = 0.06 * float(fund_table(None, 0.0)["adjclose"].iloc[ex_row - 1])  # 6 %: tripped the breaker before
    table = fund_table(ex_row, amount)
    price_prev = float(table["close"].iloc[ex_row - 1])
    series = price_only_series("DBA", table)
    assert series is not None
    ex_day = table.index[ex_row]
    paid = fund_distributions(table)
    assert list(paid.index) == [ex_day] and paid.iloc[0] == pytest.approx(amount, abs=1e-3)

    def replay(with_credit: bool):
        data = DeskData(meta={"missing": [], "notes": []})
        data.series["DBA"] = series
        if with_credit:
            data.distributions["DBA"] = paid
        return run_backtest(data, [AGRI], RISK, ruin=False).books["agri"]

    without, with_ = replay(False), replay(True)
    before = float(without.equity.loc[:ex_day].iloc[-2])
    lev = float(without.leverage.loc[:ex_day].iloc[-2])
    assert lev > 0.5
    loss = float(without.equity.loc[ex_day]) / before - 1.0
    gain = float(with_.equity.loc[ex_day]) / float(with_.equity.loc[:ex_day].iloc[-2]) - 1.0
    adjusted = float(series.returns.loc[ex_day])
    # without the credit the book lost about leverage x yield on a day the investable return was flat
    assert loss < -0.5 * lev * amount / price_prev and abs(adjusted) < 0.02
    # with it the day is the investable return times the leverage, within the spread
    assert gain == pytest.approx(lev * adjusted, abs=0.006)
    assert with_.costs["distributions"] == pytest.approx(amount * lev * before / price_prev, rel=0.05)
    assert without.costs["distributions"] == 0.0
    # before the ex-date the two replays are the same book
    pd.testing.assert_series_equal(
        without.equity.loc[: table.index[ex_row - 1]], with_.equity.loc[: table.index[ex_row - 1]]
    )
    # and after it the credited book is ahead by the distribution, compounded
    assert float(with_.equity.iloc[-1]) > float(without.equity.iloc[-1]) * (1.0 + 0.8 * lev * amount / price_prev)


# ------------------------------------------------------------------------------------------------- the live path
def _session(day: date, open_price: float, step: float = 0.0004) -> pd.DataFrame:
    """13 half-hour bars 09:30-16:00 New York (EDT), indexed by bar START in UTC."""
    start = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
    rows, last = [], open_price
    for _ in range(13):
        o = last
        c = o * (1.0 + step)
        rows.append({"open": o, "high": max(o, c) * 1.0002, "low": min(o, c) * 0.9998, "close": c, "volume": 500.0})
        last = c
    index = pd.DatetimeIndex([start + timedelta(minutes=30 * i) for i in range(13)], name="ts")
    return pd.DataFrame(rows, index=index)


def test_on_the_live_path_the_credit_lands_at_the_tick_whose_download_shows_the_ex_date(tmp_path):
    """Archive -> build_desk_data -> live_tick, two sessions: the book buys on Wednesday's decision and holds
    through Thursday's ex-date. Thursday morning the raw bars show the drop; the 15:18 tick downloads the daily
    table with the adjustment, the credit lands on the shares held at Thursday's open, once, and the breaker
    stays quiet. Before that download the loss is visible: that is the documented delay."""
    frames = make_desk_raw_frames(n=1100, end=date(2026, 10, 9))
    idx = frames["bno_daily"].index
    wed, thu = date(2026, 10, 7), date(2026, 10, 8)
    ex_row = int(np.flatnonzero(idx == pd.Timestamp(thu))[0])
    amount = 0.9
    full = fund_table(ex_row, amount, idx=idx)
    # Wednesday's download knows nothing of the distribution: the raw and adjusted closes still coincide
    wednesday_view = fund_table(None, 0.0, idx=idx).loc[: pd.Timestamp(wed)]
    raw_prev = float(full.loc[pd.Timestamp(wed), "close"])
    sessions = pd.concat([_session(wed, raw_prev / 1.0004**13), _session(thu, raw_prev - amount)])

    store = RawStore(tmp_path / "raw")
    for entry, frame in frames.items():
        save_raw_frame(store, entry, frame)

    def tick(now: datetime, daily: pd.DataFrame | None) -> tuple[object, Desk]:
        if daily is not None:
            save_raw_frame(store, "dba_daily", daily, fetched_at=now)
        save_raw_frame(store, "dba_intraday", sessions[sessions.index < pd.Timestamp(now)], fetched_at=now)
        data = build_desk_data(store)
        desk = build_desk(tmp_path / "state", RISK, [AGRI])
        return live_tick(desk, data, now), desk

    # Wednesday 15:18: the decision; 16:18: the 15:30 bar fills it
    _, desk = tick(datetime(2026, 10, 7, 19, 18, tzinfo=UTC), wednesday_view)
    _, desk = tick(datetime(2026, 10, 7, 20, 18, tzinfo=UTC), None)
    book = desk.books["agri"]
    shares = book.broker.positions["DBA"].qty_bbl
    assert shares > 0, "the uptrend must put the book long before the ex-date"
    equity_wed = book.broker.state.last_equity
    assert book.broker.state.total_distributions == 0.0

    # Thursday 10:18: the first bar of the ex-date, the raw price down by the distribution: the loss shows
    _, desk = tick(datetime(2026, 10, 8, 14, 18, tzinfo=UTC), None)
    book = desk.books["agri"]
    assert book.broker.state.total_distributions == 0.0
    assert book.broker.state.last_equity < equity_wed - 0.8 * shares * amount
    assert book.broker.state.status.value == "active"  # 4.5 % of a 20 $ fund at 1x: under the 5 % breaker

    # Thursday 15:18: the daily table of the ex-date arrives, adjusted; the credit lands once
    _, desk = tick(datetime(2026, 10, 8, 19, 18, tzinfo=UTC), full.loc[: pd.Timestamp(thu)])
    book = desk.books["agri"]
    assert book.broker.state.total_distributions == pytest.approx(shares * amount, abs=1e-6)
    assert book.broker.state.distributions_seen["DBA"] == thu.isoformat()
    assert abs(book.broker.state.last_equity - equity_wed) < 0.01 * equity_wed  # back to the investable return
    _, desk = tick(datetime(2026, 10, 8, 19, 48, tzinfo=UTC), None)
    assert desk.books["agri"].broker.state.total_distributions == pytest.approx(shares * amount, abs=1e-6)
    _, desk = tick(datetime(2026, 10, 8, 20, 18, tzinfo=UTC), full.loc[: pd.Timestamp(thu)])
    assert desk.books["agri"].broker.state.total_distributions == pytest.approx(shares * amount, abs=1e-6)
