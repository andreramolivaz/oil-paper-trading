"""The desk loop, the replay and the live tick: fills after the decision, rolls, financing, idempotency.

Every price is SYNTHETIC and built so that the right fill price is known exactly (tests/synthetic.py).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from engine.core.calendar import add_business_days
from engine.core.config import RiskConfig
from engine.core.events import Bar
from engine.desk.backtest import FILL_SAME_CLOSE, performance, ruin_probabilities, run_backtest, yearly_returns
from engine.desk.book import Book, BookConfig
from engine.desk.data import held_contract
from engine.desk.engine import Desk
from engine.desk.live import (
    FEED_DELAY,
    build_desk,
    completed_bars,
    decision_day,
    last_quote,
    latest_price,
    live_tick,
)
from engine.desk.vehicles import BNO, MCL
from tests.synthetic import half_hour_bars, make_desk_data, make_vehicle_series

RISK = RiskConfig.load()
PRUDENTE = BookConfig(id="prudente", name="Prudente", vehicle="BNO", vol_target=0.12, max_leverage=1.0, long_only=True)
DINAMICO = BookConfig(id="dinamico", name="Dinamico", vehicle="BNO", vol_target=0.25, max_leverage=2.0, long_only=True)
SPINTO = BookConfig(
    id="spinto", name="Spinto", vehicle="MCL", vol_target=0.50, max_leverage=10.0, weekend_max_leverage=3.0
)
LONG = {"trend": 15.0, "carry": 10.0, "carry_momentum": 10.0}


def _bar(symbol: str, end: datetime, o: float, c: float, interval: str = "30m") -> Bar:
    return Bar(
        symbol=symbol,
        ts=end,
        open=o,
        high=max(o, c),
        low=min(o, c),
        close=c,
        interval=interval,
        source="sintetico",
        asof=end,
    )


# ----------------------------------------------------------------------------------------------- the loop
def test_an_order_fills_at_the_open_of_the_first_bar_that_starts_after_the_decision():
    desk = Desk([Book(PRUDENTE, RISK)])
    t0 = datetime(2026, 10, 8, 19, 3, tzinfo=UTC)
    decisions = desk.decide("BNO", t0, date(2026, 10, 8), "BNO", 60.0, dict(LONG), 0.30)
    assert len(decisions) == 1 and decisions[0].status == "ordine in coda"
    units = decisions[0].order_units
    # the bar that was already forming when the book decided cannot fill it
    assert desk.on_bar("BNO", _bar("BNO", datetime(2026, 10, 8, 19, 30, tzinfo=UTC), 60.0, 60.5)) == []
    fills = desk.on_bar("BNO", _bar("BNO", datetime(2026, 10, 8, 20, 0, tzinfo=UTC), 61.0, 61.2))
    assert len(fills) == 1 and fills[0].qty_bbl == units
    assert fills[0].reference_price == 61.0  # the open of the next bar, not the 60.0 the decision saw
    assert 61.0 < fills[0].price < 61.0 * 1.002  # plus half a spread, a fraction of a per cent
    assert desk.books["prudente"].units("BNO") == units


def test_one_decision_per_book_per_day():
    desk = Desk([Book(PRUDENTE, RISK), Book(DINAMICO, RISK)])
    ts = datetime(2026, 10, 8, 19, 3, tzinfo=UTC)
    assert len(desk.decide("BNO", ts, date(2026, 10, 8), "BNO", 60.0, dict(LONG), 0.30)) == 2
    assert desk.decide("BNO", ts + timedelta(minutes=30), date(2026, 10, 8), "BNO", 61.0, dict(LONG), 0.30) == []
    assert len(desk.decide("BNO", ts + timedelta(days=1), date(2026, 10, 9), "BNO", 61.0, dict(LONG), 0.30)) == 2


def test_a_new_decision_replaces_the_order_still_queued_and_never_adds_to_it():
    """Two decisions with no bar in between must leave ONE order. Found on real data: a book started at 14:48
    New York caught up the previous session's decision, took the day's own at 15:18 with the first order still
    queued, and ended the afternoon holding twice its target."""
    desk = Desk([Book(PRUDENTE, RISK)])
    book = desk.books["prudente"]
    ts = datetime(2026, 10, 8, 18, 48, tzinfo=UTC)
    first = desk.decide("BNO", ts, date(2026, 10, 7), "BNO", 60.0, dict(LONG), 0.30)[0]
    second = desk.decide("BNO", ts + timedelta(minutes=30), date(2026, 10, 8), "BNO", 60.0, dict(LONG), 0.30)[0]
    assert first.order_units == second.order_units > 0
    pending = book.broker.pending_orders()
    assert len(pending) == 1 and pending[0].qty_bbl == second.order_units
    assert "annullato, lo sostituisce questo" in second.rationale and pending[0].rationale == second.rationale
    assert "annullato" not in first.rationale
    # the first bar after the second decision fills once: the book holds its target, not twice its target
    fills = desk.on_bar("BNO", _bar("BNO", datetime(2026, 10, 8, 20, 0, tzinfo=UTC), 60.0, 60.0))
    assert len(fills) == 1 and book.units("BNO") == second.target_units

    # a decision that needs no order still withdraws the one that was waiting
    strong = {"trend": 20.0, "carry": 10.0, "carry_momentum": 10.0}
    assert desk.decide("BNO", ts + timedelta(days=1), date(2026, 10, 9), "BNO", 60.0, strong, 0.30)[0].order_units > 0
    held = desk.decide("BNO", ts + timedelta(days=4), date(2026, 10, 12), "BNO", 60.0, dict(LONG), 0.30)[0]
    assert held.order_units == 0 and book.broker.pending_orders() == []
    assert held.rationale.endswith("era ancora in coda: annullato.")
    assert book.units("BNO") == second.target_units


def test_the_roll_moves_the_position_and_drops_orders_left_on_the_old_contract():
    desk = Desk([Book(SPINTO, RISK)])
    book = desk.books["spinto"]
    ts = datetime(2026, 10, 12, 18, 35, tzinfo=UTC)
    desk.decide("MCL", ts, date(2026, 10, 12), "CLX26", 90.0, dict(LONG), 0.30)
    desk.on_bar("MCL", _bar("CLX26", ts + timedelta(minutes=55), 90.0, 90.0))
    held = book.units("CLX26")
    assert held > 0
    # an order is still queued on the old contract when the calendar says to leave it
    desk.decide(
        "MCL",
        ts + timedelta(days=1),
        date(2026, 10, 13),
        "CLX26",
        90.0,
        {"trend": 20.0, "carry": 10.0, "carry_momentum": 10.0},
        0.15,
    )
    assert any(o.instrument == "CLX26" for o in book.broker.pending_orders())
    rolled = desk.roll("MCL", "CLZ26", {"CLX26": 90.0, "CLZ26": 88.0}, ts + timedelta(days=1, minutes=5))
    assert rolled == 1 and book.units("CLX26") == 0 and book.units("CLZ26") == held
    assert book.broker.pending_orders() == []
    assert desk.state.last_roll["spinto"].startswith("CLX26->CLZ26")
    # The roll is priced per contract: the new one trading two dollars lower is not a 600 $ loss on 300
    # barrels. What the book did pay is commissions and spread on the entry and on both legs of the roll.
    equity = book.broker.snapshot(ts + timedelta(days=2)).equity
    assert 10_000 - 40 < equity < 10_000


def test_a_roll_without_both_prices_is_postponed_not_guessed():
    desk = Desk([Book(SPINTO, RISK)])
    ts = datetime(2026, 10, 12, 18, 35, tzinfo=UTC)
    desk.decide("MCL", ts, date(2026, 10, 12), "CLX26", 90.0, dict(LONG), 0.30)
    desk.on_bar("MCL", _bar("CLX26", ts + timedelta(minutes=55), 90.0, 90.0))
    assert desk.roll("MCL", "CLZ26", {"CLX26": 90.0}, ts + timedelta(days=1)) == 0
    assert desk.books["spinto"].units("CLX26") > 0


def test_interest_is_charged_only_on_what_is_borrowed():
    desk = Desk([Book(PRUDENTE, RISK), Book(dataclasses.replace(DINAMICO, vol_target=0.50), RISK)])
    ts = datetime(2026, 10, 8, 19, 3, tzinfo=UTC)
    strong = {"trend": 20.0, "carry": 10.0, "carry_momentum": 10.0}
    desk.decide("BNO", ts, date(2026, 10, 8), "BNO", 60.0, strong, 0.20)
    desk.on_bar("BNO", _bar("BNO", ts + timedelta(minutes=57), 60.0, 60.0))
    levered = desk.books["dinamico"].broker.snapshot(ts + timedelta(hours=1))
    assert levered.leverage > 1.5 and desk.books["prudente"].broker.snapshot(ts + timedelta(hours=1)).leverage <= 1.0
    charged = desk.accrue_financing("BNO", date(2026, 10, 9), ts + timedelta(days=1))
    borrowed = levered.gross_notional - levered.equity
    assert charged == pytest.approx(borrowed * BNO.financing_rate / 360.0, rel=1e-6)
    assert desk.books["prudente"].broker.state.total_financing == 0.0
    assert desk.accrue_financing("BNO", date(2026, 10, 9), ts + timedelta(days=1, hours=1)) == 0.0  # once a day
    assert MCL.financing_rate == 0.0  # a future's margin is collateral, not a loan


# ----------------------------------------------------------------------------------------------- the replay
def test_the_replay_fills_on_the_next_session_and_never_on_the_bar_it_decided_on():
    data = make_desk_data(n_days=420)
    series = data.series["BNO"]
    result = run_backtest(data, [PRUDENTE], RISK, ruin=False)
    book = result.books["prudente"]
    assert book.n_fills > 0 and book.equity.index[0] == series.bars.index[300]  # 300 days of warm-up
    assert float(book.leverage.max()) <= 1.0 + 1e-9 and float(book.exposure.min()) >= 0.0
    # same data, same rules, optimistic fill: a different path, the same ceilings
    close = run_backtest(data, [PRUDENTE], RISK, ruin=False, fill=FILL_SAME_CLOSE)
    assert close.meta["fill"] == FILL_SAME_CLOSE and float(close.books["prudente"].leverage.max()) <= 1.0 + 1e-9
    assert not close.books["prudente"].equity.equals(book.equity)


def test_the_replay_is_deterministic_and_costs_only_ever_hurt():
    data = make_desk_data(n_days=420)
    a = run_backtest(data, [PRUDENTE, SPINTO], RISK, ruin=False)
    b = run_backtest(data, [PRUDENTE, SPINTO], RISK, ruin=False)
    for book_id in ("prudente", "spinto"):
        pd.testing.assert_series_equal(a.books[book_id].equity, b.books[book_id].equity)
    doubled = run_backtest(data, [PRUDENTE, SPINTO], RISK, ruin=False, cost_multiplier=2.0)
    for book_id in ("prudente", "spinto"):
        assert doubled.books[book_id].equity.iloc[-1] <= a.books[book_id].equity.iloc[-1] + 1e-6
        assert float(a.books[book_id].leverage.max()) <= 10.0 + 1e-9
    assert set(a.benchmarks) == {"BNO", "MCL"} and a.books["spinto"].vehicle == "MCL"


def test_the_replay_holds_the_contract_the_calendar_says_and_rolls_before_the_expiry():
    data = make_desk_data(n_days=420)
    series = data.series["MCL"]
    books = [Book(dataclasses.replace(SPINTO, vol_target=0.5), RISK)]
    desk = Desk(books)
    from engine.desk.backtest import run_vehicle

    run_vehicle(desk, series, None, None)
    last_day = series.bars.index[-1].date()
    held = [s for s, p in desk.books["spinto"].broker.positions.items() if p.qty_bbl != 0]
    assert held in ([], [held_contract("CL", add_business_days(last_day, 1, "US"))])


def test_performance_numbers():
    idx = pd.bdate_range("2020-01-01", periods=505)
    equity = pd.Series(10_000.0 * 1.0004 ** pd.RangeIndex(505).to_numpy(), index=idx)
    stats = performance(equity)
    assert stats["years"] == 2.0 and stats["max_drawdown"] == 0.0 and stats["hit_rate"] == 1.0
    assert stats["cagr"] == pytest.approx(1.0004**252 - 1, abs=1e-4)
    assert performance(equity.iloc[:30]) == {}
    yearly = yearly_returns(equity)
    assert set(yearly) == {2020, 2021} and yearly[2021] == pytest.approx(
        1.0004 ** len(idx[idx.year == 2021]) - 1, abs=1e-4
    )


def test_ruin_probabilities_are_reproducible_and_grow_with_leverage():
    series = make_vehicle_series("BNO", n_days=900, vol=0.025, drift=0.0)
    calm = ruin_probabilities(series.returns)
    wild = ruin_probabilities(series.returns * 6.0)
    assert calm == ruin_probabilities(series.returns)
    assert wild["p_lose_half"] > calm["p_lose_half"] and wild["p_lose_quarter"] >= wild["p_lose_half"]
    assert 0.0 <= calm["p_dead"] <= wild["p_dead"] <= 1.0
    assert ruin_probabilities(series.returns.iloc[:100]) == {}


# ----------------------------------------------------------------------------------------------- the live tick
def _live_setup(tmp_path, n_days: int = 420):
    """Daily history that ends on a Thursday, plus that Thursday's half-hour bars for BNO."""
    data = make_desk_data(n_days=n_days, start=date(2025, 2, 24))
    last = data.series["BNO"].bars.index[-1].date()
    close = float(data.series["BNO"].bars["close"].iloc[-1])
    data.intraday["BNO"] = half_hour_bars(last, close, n=13, first=(13, 30))  # 09:30-16:00 New York (EDT)
    desk = build_desk(tmp_path, RISK, [PRUDENTE, DINAMICO])
    return data, desk, last


def test_completed_bars_leave_the_forming_bar_for_the_next_tick():
    frame = half_hour_bars(date(2026, 10, 8), 60.0, n=4, first=(13, 30))
    now = datetime(2026, 10, 8, 14, 45, tzinfo=UTC)  # the 14:30-15:00 bar is still forming
    bars = completed_bars(frame, "BNO", now, None)
    assert [b.ts.strftime("%H:%M") for b in bars] == ["14:00", "14:30"]
    assert completed_bars(frame, "BNO", now, bars[0].ts)[0].ts == bars[1].ts
    assert completed_bars(None, "BNO", now, None) == [] and completed_bars(frame.iloc[:0], "BNO", now, None) == []
    long = frame.reset_index().assign(code="CLX26")
    assert len(completed_bars(long, "CLX26", now, None)) == 2 and completed_bars(long, "CLZ26", now, None) == []


def test_a_bar_is_complete_only_once_the_late_feed_has_delivered_all_of_it():
    """Three minutes after a bar ends a feed that is fifteen minutes late has shown twelve minutes less than the
    whole bar. Feeding it then would stamp it as seen with half a high and half a low."""
    frame = half_hour_bars(date(2026, 10, 8), 60.0, n=4, first=(13, 30))
    just_after = datetime(2026, 10, 8, 14, 33, tzinfo=UTC)  # 14:00-14:30 ended three minutes ago
    assert [b.ts.strftime("%H:%M") for b in completed_bars(frame, "BNO", just_after, None)] == ["14:00"]
    on_the_slot = datetime(2026, 10, 8, 14, 48, tzinfo=UTC)  # the runner's slot: the feed has caught up
    assert [b.ts.strftime("%H:%M") for b in completed_bars(frame, "BNO", on_the_slot, None)] == ["14:00", "14:30"]
    assert FEED_DELAY.total_seconds() == 15 * 60
    assert len(completed_bars(frame, "BNO", just_after, None, delay=timedelta(0))) == 2


def test_last_quote_is_the_newest_price_on_file_and_says_how_old_it_can_be():
    frame = half_hour_bars(date(2026, 10, 8), 60.0, n=4, first=(13, 30))
    forming = datetime(2026, 10, 8, 14, 48, tzinfo=UTC)  # inside the 14:30-15:00 bar
    price, seen = last_quote(frame, "BNO", forming)
    assert price == float(frame["close"].iloc[2])  # the forming bar's last print, not the last finished bar
    assert seen == datetime(2026, 10, 8, 14, 33, tzinfo=UTC)  # the feed is fifteen minutes behind the clock
    early = datetime(2026, 10, 8, 14, 35, tzinfo=UTC)  # five minutes in: nothing of this bar can be older than
    assert last_quote(frame, "BNO", early)[1] == datetime(2026, 10, 8, 14, 30, tzinfo=UTC)  # its own start
    evening = datetime(2026, 10, 8, 23, 0, tzinfo=UTC)
    price, seen = last_quote(frame, "BNO", evening)
    assert price == float(frame["close"].iloc[-1]) and seen == datetime(2026, 10, 8, 15, 30, tzinfo=UTC)
    before = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)
    assert last_quote(frame, "BNO", before) == (None, None) and last_quote(None, "BNO", forming) == (None, None)
    bad = frame.copy()
    bad.iloc[2, bad.columns.get_loc("close")] = float("nan")  # a hole in the feed: fall back to the bar before
    assert last_quote(bad, "BNO", forming)[0] == float(frame["close"].iloc[1])


def test_decision_day_is_the_last_session_whose_decision_time_has_passed():
    thursday_before = datetime(2026, 10, 8, 18, 59, tzinfo=UTC)  # 14:59 New York
    thursday_after = datetime(2026, 10, 8, 19, 0, tzinfo=UTC)
    saturday = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    assert decision_day(BNO, thursday_before) == date(2026, 10, 7)
    assert decision_day(BNO, thursday_after) == date(2026, 10, 8)
    assert decision_day(MCL, thursday_before) == date(2026, 10, 8)  # the futures book decides at 14:35
    assert decision_day(BNO, saturday) == date(2026, 10, 9)  # a late scheduler catches up Friday's decision


def test_live_tick_decides_once_fills_on_the_next_bar_and_is_idempotent(tmp_path):
    data, desk, day = _live_setup(tmp_path)
    t_decide = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)  # 15:18 New York, a runner slot
    first = live_tick(desk, data, t_decide)
    assert first.bars == 0  # a new book is not fed the archive's older bars
    assert len(first.decisions) == 2 and all(d["order_units"] > 0 for d in first.decisions)
    assert first.fills == 0 and all(b["equity"] == 10_000.0 for b in first.books.values())

    # the same minute again, from the state on disk: nothing happens twice
    again = live_tick(build_desk(tmp_path, RISK, [PRUDENTE, DINAMICO]), data, t_decide)
    assert again.decisions == [] and again.fills == 0 and again.bars == 0

    # 15:48: the 15:00-15:30 bar is complete but it started BEFORE the decision: no fill yet
    desk = build_desk(tmp_path, RISK, [PRUDENTE, DINAMICO])
    second = live_tick(desk, data, t_decide + timedelta(minutes=30))
    assert second.bars == 2 and second.fills == 0
    # 16:18: the 15:30-16:00 bar started after the decision and fills both orders at its open
    desk = build_desk(tmp_path, RISK, [PRUDENTE, DINAMICO])
    third = live_tick(desk, data, t_decide + timedelta(minutes=60))
    assert third.fills == 2 and third.decisions == []
    frame = data.intraday["BNO"]
    open_1530 = float(frame.loc[pd.Timestamp(datetime(day.year, day.month, day.day, 19, 30, tzinfo=UTC)), "open"])
    for book in desk.books.values():
        position = book.broker.positions["BNO"]
        assert position.avg_price == pytest.approx(open_1530, rel=2e-3) and position.avg_price > open_1530
        assert book.broker.pending_orders() == []
    assert third.books["dinamico"]["leverage"] > third.books["prudente"]["leverage"] > 0


def test_a_book_started_just_before_its_decision_time_does_not_buy_twice(tmp_path):
    """The same case end to end, through the live tick and the state on disk."""
    data, desk, day = _live_setup(tmp_path)
    start = datetime(day.year, day.month, day.day, 18, 48, tzinfo=UTC)  # 14:48 New York: yesterday's is owed
    assert len(live_tick(desk, data, start).decisions) == 2
    desk = build_desk(tmp_path, RISK, [PRUDENTE, DINAMICO])
    second = live_tick(desk, data, start + timedelta(minutes=30))  # 15:18: the day's own decision
    assert len(second.decisions) == 2 and second.fills == 0
    assert all(len(book.broker.pending_orders()) == 1 for book in desk.books.values())
    fills = 0
    for minutes in (60, 90):  # 15:48 and 16:18: the 15:30 bar fills what is queued
        desk = build_desk(tmp_path, RISK, [PRUDENTE, DINAMICO])
        fills += live_tick(desk, data, start + timedelta(minutes=minutes)).fills
    targets = {d["book"]: d["target_units"] for d in second.decisions}
    assert fills == 2 and all(targets.values())
    for book in desk.books.values():
        assert book.units("BNO") == targets[book.id] and book.broker.pending_orders() == []


def test_a_contract_never_fed_before_is_not_marked_on_days_old_bars(tmp_path):
    """The regression that matters for a levered book: after a roll the new contract has no "last bar seen".
    Feeding it the archive from the start would mark the position at last week's price and could trip a
    stop-out that never happened."""
    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    day = data.series["MCL"].bars.index[-1].date()
    symbol = held_contract("CL", add_business_days(day, 1, "US"))
    price = float(data.series["MCL"].bars["close"].iloc[-1])
    old = half_hour_bars(
        add_business_days(day, -3, "US"), price * 0.5, n=20, first=(13, 30), step=0.0
    )  # a HALF price, days ago
    today = half_hour_bars(day, price, n=14, first=(13, 30), step=0.0)
    frame = pd.concat([old, today]).reset_index().assign(code=symbol)
    data.intraday["MCL"] = frame
    desk = build_desk(tmp_path, RISK, [dataclasses.replace(SPINTO, vol_target=0.5)])
    t_decide = datetime(day.year, day.month, day.day, 18, 40, tzinfo=UTC)  # 14:40 New York
    first = live_tick(desk, data, t_decide)
    assert first.bars == 0 and first.books["spinto"]["equity"] == 10_000.0
    desk = build_desk(tmp_path, RISK, [dataclasses.replace(SPINTO, vol_target=0.5)])
    second = live_tick(desk, data, t_decide + timedelta(minutes=80))  # 16:00: the 15:00-15:30 bar has filled it
    assert second.fills == 1 and second.books["spinto"]["status"] == "active"
    assert abs(second.books["spinto"]["equity"] - 10_000.0) < 50.0  # costs only: no bar at half price was ever seen
    seen = desk.books["spinto"].broker.state.last_bar_ts.get(f"{symbol}|30m")
    assert seen is None or seen > t_decide


def test_a_roll_waits_until_both_contracts_are_priced_at_the_same_moment(tmp_path):
    """A book still on the old contract takes no decision on the new one: it would see no position there and buy
    its whole target on top of what it holds. And the roll itself needs both prices from the same half hour."""
    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    day = data.series["MCL"].bars.index[-1].date()
    new = held_contract("CL", add_business_days(day, 1, "US"))
    old = "CLF26" if new != "CLF26" else "CLG26"
    price = float(data.series["MCL"].bars["close"].iloc[-1])
    cfg = dataclasses.replace(SPINTO, vol_target=2.0)
    desk = build_desk(tmp_path, RISK, [cfg])
    t0 = datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC)
    desk.decide("MCL", t0, add_business_days(day, -1, "US"), old, price, dict(LONG), 0.30)
    desk.on_bar("MCL", _bar(old, t0 + timedelta(minutes=60), price, price))
    desk.save()
    held = desk.books["spinto"].units(old)
    assert held > 0

    # today only the new contract has bars; the old one has a close from the session before
    today_new = half_hour_bars(day, price * 0.97, n=14, first=(13, 30), step=0.0).reset_index().assign(code=new)
    data.intraday["MCL"] = today_new
    data.contracts["CL"] = pd.DataFrame({old: [price]}, index=[pd.Timestamp(add_business_days(day, -1, "US"))])
    now = datetime(day.year, day.month, day.day, 18, 48, tzinfo=UTC)  # 14:48 New York
    desk = build_desk(tmp_path, RISK, [cfg])
    report = live_tick(desk, data, now)
    assert report.rolled == 0 and report.decisions == []
    assert any("roll verso" in note and "spinto" in note for note in report.notes)
    book = desk.books["spinto"]
    assert book.units(old) == held and book.units(new) == 0 and book.broker.pending_orders() == []

    # the old contract's bars arrive: the roll and the decision that was owed both happen on the next tick
    today_old = half_hour_bars(day, price, n=14, first=(13, 30), step=0.0).reset_index().assign(code=old)
    data.intraday["MCL"] = pd.concat([today_new, today_old], ignore_index=True)
    desk = build_desk(tmp_path, RISK, [cfg])
    report = live_tick(desk, data, now + timedelta(minutes=30))
    book = desk.books["spinto"]
    assert report.rolled == 1 and len(report.decisions) == 1
    assert book.units(old) == 0 and book.units(new) == held


def test_a_blocked_vehicle_takes_no_new_decision_but_is_still_marked(tmp_path):
    data, desk, day = _live_setup(tmp_path)
    now = datetime(day.year, day.month, day.day, 19, 3, tzinfo=UTC)
    report = live_tick(desk, data, now, blocked={"BNO": "fonte bno_daily non disponibile"})
    assert report.decisions == [] and any("bno_daily" in note for note in report.notes)
    assert set(report.books) == {"prudente", "dinamico"}
    # the decision is still owed: it is taken as soon as the source is back
    desk = build_desk(tmp_path, RISK, [PRUDENTE, DINAMICO])
    assert len(live_tick(desk, data, now + timedelta(minutes=30)).decisions) == 2


def test_a_decision_waits_for_the_day_it_is_about(tmp_path):
    data, desk, day = _live_setup(tmp_path)
    next_day = add_business_days(day, 1, "US")
    late = datetime(next_day.year, next_day.month, next_day.day, 19, 3, tzinfo=UTC)
    report = live_tick(desk, data, late)  # the daily table has no row for `next_day` yet
    assert report.decisions == [] and any("in attesa" in note for note in report.notes)


def test_latest_price_prefers_a_newer_daily_close_to_a_stale_intraday_bar():
    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    day = data.series["BNO"].bars.index[-1].date()
    close = float(data.series["BNO"].bars["close"].iloc[-1])
    stale_day = add_business_days(day, -2, "US")
    data.intraday["BNO"] = half_hour_bars(stale_day, 999.0, n=13, first=(13, 30), step=0.0)
    now = datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC)
    price, ts = latest_price(data, "BNO", "BNO", now)
    assert price == pytest.approx(close) and ts is not None and ts <= now
    data.intraday["BNO"] = half_hour_bars(day, 123.0, n=13, first=(13, 30), step=0.0)
    price, ts = latest_price(data, "BNO", "BNO", now)
    assert price == 123.0 and ts == datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC)
    assert latest_price(data, "MCL", "CLX99", now) == (None, None)
