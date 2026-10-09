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
from engine.core.store import StateStore
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
# a fund book that may be short: long in BNO, short by buying the -2x fund SCO
PAIR = BookConfig(
    id="coppia", name="Coppia", vehicle="BNO", vol_target=0.25, max_leverage=2.0, weekend_max_leverage=1.5,
    short_via="SCO",
)  # fmt: skip
STRONG = {"trend": 20.0, "accel": 10.0, "skew": 12.0, "carry": 10.0, "carry_momentum": 10.0, "copper": 10.0}
STRONG |= {"dollar": 6.0}  # +18.67
WEAK = {name: -value for name, value in STRONG.items()}


def _nothing_to_decide(desk, now: datetime) -> None:
    """Mark the decision of the session as taken: the ticks of a test about BARS must not also decide (a
    decision replaces whatever order is queued, and without a price for the inverse fund it replaces it with
    nothing)."""
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = decision_day(book.vehicle, now).isoformat()


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


# ----------------------------------------------------------------------------------------------- two legs
def _long_then_short(desk: Desk) -> tuple[Book, datetime, float]:
    """A PAIR book bought to the edge of 2x in the fund, then told to be as short: both orders queued."""
    book = desk.books["coppia"]
    t0 = datetime(2026, 10, 6, 19, 3, tzinfo=UTC)  # a Tuesday
    desk.decide("BNO", t0, date(2026, 10, 6), "BNO", 60.0, dict(STRONG), 0.10, leg_prices={"SCO": 20.0})
    desk.on_bar("BNO", _bar("BNO", t0 + timedelta(minutes=57), 60.0, 60.0), vol_annual=0.10)
    assert book.units("BNO") * 60.0 / 10_000 > 1.7 and book.units("SCO") == 0.0
    t1 = t0 + timedelta(days=1)
    decision = desk.decide("BNO", t1, date(2026, 10, 7), "BNO", 60.0, dict(WEAK), 0.10, leg_prices={"SCO": 20.0})[0]
    assert decision.n_orders == 2 and decision.status == "2 ordini in coda"
    assert [(o.instrument, o.qty_bbl < 0) for o in book.broker.pending_orders()] == [("BNO", True), ("SCO", False)]
    return book, t1, float(decision.legs[0]["target_units"])


def test_a_book_that_changes_side_is_fed_the_sale_before_the_purchase():
    """At 1.75x in the fund the account is near its buying power: the inverse fund can only be bought with
    what the sale of the fund frees. The bars of the same half hour arrive in no particular order."""
    desk = Desk([Book(PAIR, RISK)])
    book, t1, want = _long_then_short(desk)
    assert want * 20.0 > 0.8 * 10_000  # a purchase the account could not make on top of the fund
    end = t1 + timedelta(minutes=57)
    bars = [(_bar("SCO", end, 20.0, 20.0), 0.20), (_bar("BNO", end, 60.0, 60.0), 0.10)]  # the purchase first
    fills = desk.on_bars("BNO", bars)
    assert [(f.instrument, f.qty_bbl > 0) for f in fills] == [("BNO", False), ("SCO", True)]
    assert book.units("BNO") == 0.0 and book.units("SCO") == want  # nothing cut by the leverage cap
    equity = float(book.broker.snapshot(end).equity)
    assert book.net_exposure(equity) == pytest.approx(-2.0 * want * 20.0 / equity)
    assert -2.0 <= book.net_exposure(equity) < -1.6 and book.effective_leverage(equity) <= 2.0
    assert want * 20.0 <= equity  # the inverse fund is paid in cash
    assert desk.accrue_financing("BNO", date(2026, 10, 8), end + timedelta(days=1)) == 0.0  # nothing is borrowed
    # holding the inverse fund does not make the book "still on another contract": it decides again tomorrow
    t2 = t1 + timedelta(days=1)
    again = desk.decide("BNO", t2, date(2026, 10, 8), "BNO", 60.0, dict(WEAK), 0.10, leg_prices={"SCO": 20.0})
    assert len(again) == 1 and again[0].n_orders == 0 and again[0].legs[0]["target_units"] == want


def test_the_purchase_of_one_leg_waits_for_the_sale_of_the_other():
    """The two tables do not always deliver the same half hour on the same tick. With only the purchase leg's
    bar on file the purchase must not fill: the book would hold both sides, and at 1.75x in the fund the broker
    would cut the inverse fund to what is left of the buying power, for good."""
    desk = Desk([Book(PAIR, RISK)])
    book, t1, want = _long_then_short(desk)
    held = book.units("BNO")
    end = t1 + timedelta(minutes=57)
    assert desk.on_bars("BNO", [(_bar("SCO", end, 20.0, 20.0), 0.20)]) == []  # the inverse fund's bar alone
    assert book.units("BNO") == held and book.units("SCO") == 0.0 and len(book.broker.pending_orders()) == 2
    # the fund's bar of the same half hour arrives a tick later: the sale fills, the purchase has had its bar
    fills = desk.on_bars("BNO", [(_bar("BNO", end, 60.0, 60.0), 0.10)])
    assert [(f.instrument, f.qty_bbl) for f in fills] == [("BNO", -held)]
    assert book.broker.positions == {} and [o.instrument for o in book.broker.pending_orders()] == ["SCO"]
    # ... and fills whole on its next one
    later = end + timedelta(minutes=30)
    fills = desk.on_bars("BNO", [(_bar("BNO", later, 60.0, 60.0), 0.10), (_bar("SCO", later, 20.0, 20.0), 0.20)])
    assert [(f.instrument, f.qty_bbl) for f in fills] == [("SCO", want)]
    assert book.broker.pending_orders() == []


def test_a_gap_at_the_fill_cannot_push_the_short_side_over_its_ceiling_or_onto_margin():
    desk = Desk([Book(PAIR, RISK)])
    book = desk.books["coppia"]
    t0 = datetime(2026, 10, 6, 19, 3, tzinfo=UTC)
    decision = desk.decide("BNO", t0, date(2026, 10, 6), "BNO", 60.0, dict(WEAK), 0.23, leg_prices={"SCO": 20.0})[0]
    # -18.67 at 23 % volatility is 2.03x wanted: the ceiling binds, and the buffer (0.11x) leaves 473 shares
    want = decision.legs[0]["target_units"]
    assert decision.exposure == -2.0 and want == 473.0 and want * 20.0 > 0.94 * 10_000
    # the inverse fund opens 8 % above the price the decision saw: bought whole it would be 1.05x of the account
    fills = desk.on_bars("BNO", [(_bar("SCO", t0 + timedelta(minutes=57), 21.6, 21.6), 0.20)])
    assert len(fills) == 1 and fills[0].qty_bbl < want and fills[0].meta["qty_capped_by_leverage"]["from"] == want
    equity = float(book.broker.snapshot(t0 + timedelta(hours=1)).equity)
    assert book.units("SCO") * 21.6 <= equity  # in cash
    assert -2.0 <= book.net_exposure(equity) < -1.98 and book.effective_leverage(equity) <= 2.0
    assert desk.accrue_financing("BNO", date(2026, 10, 7), t0 + timedelta(days=1)) == 0.0


def test_an_inverse_fund_bar_reaches_only_the_books_that_use_that_fund():
    desk = Desk([Book(PRUDENTE, RISK), Book(PAIR, RISK), Book(SPINTO, RISK)])
    assert desk.short_legs("BNO") == ["SCO"] and desk.short_legs("MCL") == []
    end = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)
    desk.on_bars("BNO", [(_bar("BNO", end, 60.0, 61.0), 0.3), (_bar("SCO", end, 20.0, 19.4), 0.6)])
    assert desk.books["prudente"].broker.state.last_prices == {"BNO": 61.0}
    assert desk.books["coppia"].broker.state.last_prices == {"BNO": 61.0, "SCO": 19.4}
    assert desk.books["spinto"].broker.state.last_prices == {}


def test_the_replay_holds_the_short_side_in_the_inverse_fund_and_stays_under_its_ceiling():
    from engine.desk import signals as sg
    from tests.synthetic import inverse_fund_bars

    data = make_desk_data(n_days=560)
    data.series["BNO"] = make_vehicle_series("BNO", 560, seed=4, price0=60.0, drift=-0.002)  # a falling fund
    data.legs["SCO"] = inverse_fund_bars(data.series["BNO"].bars)
    twin = dataclasses.replace(DINAMICO, id="solo-long")
    # with the forecasts the series came with (a mix of signs): both sides are used, the ceiling holds on each
    mixed = run_backtest(data, [PAIR, twin], RISK, ruin=False)
    pair, long_only = mixed.books["coppia"], mixed.books["solo-long"]
    assert float(pair.exposure.min()) < -0.3 and float(pair.exposure.max()) > 0.3
    assert 0.2 < pair.stats["share_days_short"] < 0.9
    assert float(long_only.exposure.min()) >= 0.0 and long_only.stats["share_days_short"] == 0.0
    # in oil exposure, on both sides, on every day of the replay: never above the 2x ceiling
    assert float(pair.exposure.abs().max()) <= 2.0 + 1e-6 and float(pair.leverage.max()) <= 2.0 + 1e-6
    # the optimistic fill takes the same path through both legs
    close = run_backtest(data, [PAIR], RISK, ruin=False, fill=FILL_SAME_CLOSE).books["coppia"]
    assert float(close.exposure.min()) < -0.3 and float(close.exposure.abs().max()) <= 2.0 + 1e-6

    # the ceiling has to BIND to be tested: with the volatility at its floor the same forecasts ask for
    # several times the account on both sides, and the book still never holds more than 2x of oil, never more
    # than its own value in the inverse fund, and never pays interest on the short side
    calm = make_desk_data(n_days=560)
    calm.series["BNO"] = make_vehicle_series("BNO", 560, seed=4, price0=60.0, drift=-0.002)
    calm.series["BNO"].vol[:] = 0.10
    calm.legs["SCO"] = inverse_fund_bars(calm.series["BNO"].bars)
    capped = run_backtest(calm, [PAIR], RISK, ruin=False).books["coppia"]
    assert float(capped.exposure.min()) < -1.7 and float(capped.exposure.max()) > 1.7  # it does reach for it
    assert float(capped.exposure.abs().max()) <= 2.0 + 1e-6 and float(capped.leverage.max()) <= 2.0 + 1e-6

    # every sleeve short on every day, on a fund that loses more than half: the book that can be short is
    # short all the time and is paid for it, its long-only twin sits in cash and ends where it started
    series = data.series["BNO"]
    assert float(series.bars["close"].iloc[-1]) < 0.5 * float(series.bars["close"].iloc[300])
    series.forecasts[list(sg.SLEEVES)] = -10.0
    short = run_backtest(data, [PAIR, twin], RISK, ruin=False)
    pair, long_only = short.books["coppia"], short.books["solo-long"]
    assert float(pair.exposure.max()) <= 0.0 and pair.stats["share_days_short"] > 0.95
    assert pair.equity.iloc[-1] > 20_000 and long_only.equity.iloc[-1] == 10_000.0
    assert pair.costs["financing"] == 0.0  # the inverse fund is paid in cash: nothing is borrowed to be short
    assert float(pair.exposure.abs().max()) <= 2.0 + 1e-6
    # without the inverse fund's bars there is nothing to be short with: the same book stays in cash
    data.legs = {}
    bare = run_backtest(data, [PAIR], RISK, ruin=False).books["coppia"]
    assert float(bare.exposure.abs().max()) == 0.0 and bare.equity.iloc[-1] == 10_000.0


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


def test_a_bar_read_while_it_was_forming_is_not_complete_until_it_is_read_again():
    """A tick's download fails: the table on file is the one the PREVIOUS tick read, and its last row is the
    first minutes of a bar. Half an hour later the clock says that bar has ended - the table has not seen it
    end. Fed then, it is stamped as seen with a close that was a passing quote, and the breaker and the margin
    are judged on it; the real bar is never looked at again."""
    frame = half_hour_bars(date(2026, 10, 8), 60.0, n=2, first=(13, 30))  # 13:30-14:00 and 14:00-14:30
    read = datetime(2026, 10, 8, 14, 18, tzinfo=UTC)
    stale = frame.assign(observed_at=pd.Timestamp(read))  # read at 14:18, while the second bar was forming
    next_tick = datetime(2026, 10, 8, 14, 48, tzinfo=UTC)  # ... and nothing newer has arrived
    assert [b.ts.strftime("%H:%M") for b in completed_bars(stale, "BNO", next_tick, None)] == ["14:00"]
    price, seen = last_quote(stale, "BNO", next_tick)
    # the quote is still the newest price on file, and it says how old it really is: read at 14:18 from a feed
    # fifteen minutes behind, not "the 14:30 close"
    assert price == float(frame["close"].iloc[1]) and seen == datetime(2026, 10, 8, 14, 3, tzinfo=UTC)
    fresh = frame.assign(observed_at=pd.Timestamp(next_tick))  # the download works again
    assert [b.ts.strftime("%H:%M") for b in completed_bars(fresh, "BNO", next_tick, None)] == ["14:00", "14:30"]
    assert last_quote(fresh, "BNO", next_tick)[1] == datetime(2026, 10, 8, 14, 30, tzinfo=UTC)
    # a table read "after now" (a replay of an archive) is judged on the clock, and so is a row without a time
    later = frame.assign(observed_at=pd.Timestamp(next_tick) + pd.Timedelta(hours=5))
    assert len(completed_bars(later, "BNO", next_tick, None)) == 2
    holes = frame.assign(observed_at=pd.NaT)
    assert len(completed_bars(holes, "BNO", next_tick, None)) == 2
    long = stale.reset_index().assign(code="CLX26")  # the same on a table with several contracts
    assert [b.ts.strftime("%H:%M") for b in completed_bars(long, "CLX26", next_tick, None)] == ["14:00"]


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


# ----------------------------------------------------------------------------------------------- live, two legs
def _pair_live(tmp_path, forecast: float, with_fund: bool = True):
    """SYNTHETIC history ending on a Thursday, that day's half-hour bars for the fund and (optionally) for the
    inverse fund, and every sleeve of the day set to ``forecast``."""
    from engine.desk import signals as sg

    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    series = data.series["BNO"]
    day = series.bars.index[-1].date()
    series.forecasts.loc[series.forecasts.index[-1], list(sg.SLEEVES)] = forecast
    data.intraday["BNO"] = half_hour_bars(day, float(series.bars["close"].iloc[-1]), n=13, first=(13, 30))
    if with_fund:
        fund_close = float(data.legs["SCO"]["close"].iloc[-1])
        data.intraday["SCO"] = half_hour_bars(day, fund_close, n=13, first=(13, 30), step=-0.002)
    else:
        data.legs = {}
    return data, build_desk(tmp_path, RISK, [PAIR]), day


def test_live_the_short_side_is_bought_and_marked_on_the_inverse_fund_own_bars(tmp_path):
    data, desk, day = _pair_live(tmp_path, forecast=-12.0)
    t_decide = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)  # 15:18 New York
    first = live_tick(desk, data, t_decide)
    assert first.bars == 0 and len(first.decisions) == 1 and first.message().endswith("1 decisioni (1 ordini)")
    decision = first.decisions[0]
    fund_frame = data.intraday["SCO"]
    quote = float(fund_frame["close"].iloc[fund_frame.index.get_indexer([pd.Timestamp(t_decide)], method="ffill")[0]])
    assert decision["exposure"] < 0 and decision["order_units"] == 0.0 and decision["n_orders"] == 1
    assert decision["legs"][0]["symbol"] == "SCO" and decision["legs"][0]["order_units"] > 0
    assert decision["legs"][0]["price"] == pytest.approx(quote)  # the inverse fund's own quote, not the fund's
    assert abs(quote / float(data.intraday["BNO"]["close"].iloc[-1]) - 1.0) > 0.2  # ... and the two differ

    # 16:18: the 15:30-16:00 bar of the inverse fund fills the order at ITS open. Neither symbol had ever been
    # fed to this book: the fund's bars of this tick must not hide the inverse fund's.
    desk = build_desk(tmp_path, RISK, [PAIR])
    second = live_tick(desk, data, t_decide + timedelta(minutes=60))
    assert second.fills == 1 and second.decisions == []
    book = desk.books["coppia"]
    open_1530 = float(fund_frame.loc[pd.Timestamp(datetime(day.year, day.month, day.day, 19, 30, tzinfo=UTC)), "open"])
    position = book.broker.positions["SCO"]
    assert position.qty_bbl == decision["legs"][0]["target_units"] and "BNO" not in book.broker.positions
    assert position.avg_price == pytest.approx(open_1530, rel=5e-3) and position.avg_price > open_1530
    # marked on its own last quote: a position worth 0.7x of the account priced on the other table's numbers
    # would have moved the equity by thousands
    assert position.last_price == pytest.approx(float(fund_frame["close"].iloc[-1]))
    assert abs(second.books["coppia"]["equity"] - 10_000.0) < 60.0
    equity = second.books["coppia"]["equity"]
    assert second.books["coppia"]["leverage"] == pytest.approx(book.effective_leverage(equity), abs=1e-3)
    assert 1.0 < second.books["coppia"]["leverage"] <= 2.0  # in oil exposure: twice the dollars in the fund


def test_live_a_change_of_side_fills_both_legs_on_the_same_half_hour(tmp_path):
    data, desk, day = _pair_live(tmp_path, forecast=-12.0)
    book = desk.books["coppia"]
    price = float(data.series["BNO"].bars["close"].iloc[-2])
    # the day before: the book is bought to the edge of its ceiling in the fund
    eve = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC) - timedelta(days=1)
    desk.decide("BNO", eve, (eve - timedelta(hours=4)).date(), "BNO", price, dict(STRONG), 0.10)
    desk.on_bar("BNO", _bar("BNO", eve + timedelta(minutes=42), price, price), vol_annual=0.10)
    desk.save()
    held = book.units("BNO")
    assert held * price / 10_000 > 1.7

    t_decide = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)
    desk = build_desk(tmp_path, RISK, [PAIR])
    first = live_tick(desk, data, t_decide)
    decision = first.decisions[0]
    assert decision["n_orders"] == 2 and decision["order_units"] == -held and decision["legs"][0]["order_units"] > 0
    assert first.message().endswith("1 decisioni (2 ordini)")
    desk = build_desk(tmp_path, RISK, [PAIR])
    second = live_tick(desk, data, t_decide + timedelta(minutes=60))
    book = desk.books["coppia"]
    assert second.fills == 2 and book.units("BNO") == 0.0
    assert book.units("SCO") == decision["legs"][0]["target_units"]  # whole: the sale was fed first
    assert book.broker.pending_orders() == [] and second.books["coppia"]["status"] == "active"


def test_live_an_order_on_a_symbol_never_fed_fills_when_its_table_returns_after_the_close(tmp_path):
    """The book changes side at 15:18: it sells the fund and buys the inverse fund, which it has never held and
    whose 30-minute table has never arrived (the purchase is sized on the daily close). The table arrives at
    16:48, after the session's last bar. The purchase must still find the bar it was waiting for (15:30, the
    first that began after the decision): with "nothing older than what the book knows" as the only rule for
    a symbol never fed, that bar was skipped and the book stayed flat until the next morning's open."""
    data, desk, day = _pair_live(tmp_path, forecast=-12.0)
    price = float(data.series["BNO"].bars["close"].iloc[-2])
    eve = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC) - timedelta(days=1)
    desk.decide("BNO", eve, (eve - timedelta(hours=4)).date(), "BNO", price, dict(STRONG), 0.10)
    desk.on_bar("BNO", _bar("BNO", eve + timedelta(minutes=42), price, price), vol_annual=0.10)
    desk.save()
    inverse = data.intraday["SCO"]

    def tick(now: datetime, visible: bool):
        data.intraday["SCO"] = inverse if visible else inverse.iloc[:0]
        desk = build_desk(tmp_path, RISK, [PAIR])
        return live_tick(desk, data, now), desk

    t_decide = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)
    first, desk = tick(t_decide, visible=False)
    decision = first.decisions[0]
    assert decision["n_orders"] == 2 and decision["legs"][0]["order_units"] > 0
    assert decision["legs"][0]["price"] == pytest.approx(float(data.legs["SCO"]["close"].iloc[-1]))
    assert "SCO|30m" not in desk.books["coppia"].broker.state.last_bar_ts  # never fed a bar of it
    second, desk = tick(t_decide + timedelta(minutes=30), visible=False)
    assert second.fills == 0
    third, desk = tick(t_decide + timedelta(minutes=60), visible=False)  # 16:18: the fund's 15:30 bar
    book = desk.books["coppia"]
    assert third.fills == 1 and book.units("BNO") == 0.0 and book.units("SCO") == 0.0  # sold, and waiting
    assert any("nessuna barra intraday di SCO" in note for note in third.notes)
    fourth, desk = tick(t_decide + timedelta(minutes=90), visible=True)  # 16:48: the table is back
    book = desk.books["coppia"]
    assert fourth.fills == 1 and book.units("SCO") == decision["legs"][0]["target_units"]
    (bought,) = [f for f in StateStore(tmp_path / "desk" / "coppia").read_jsonl("trades") if f["instrument"] == "SCO"]
    opened = inverse.index[inverse.index > pd.Timestamp(t_decide)][0]  # the 15:30 New York bar
    assert bought["reference_price"] == pytest.approx(float(inverse.loc[opened, "open"]))
    assert fourth.books["coppia"]["status"] == "active" and book.broker.pending_orders() == []


def test_live_a_queued_purchase_of_the_inverse_fund_does_not_fill_once_the_book_has_no_short_side(tmp_path):
    """``short_via`` is removed from the configuration while the purchase decided an hour before is still
    queued. Filled, it would open a side the book may no longer have, and the next decision would sell it only
    a day later: the order is cancelled before any bar is fed."""
    data, desk, day = _pair_live(tmp_path, forecast=-12.0)
    t_decide = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)
    first = live_tick(desk, data, t_decide)
    assert first.decisions[0]["legs"][0]["order_units"] > 0 and first.decisions[0]["n_orders"] == 1
    desk = build_desk(tmp_path, RISK, [dataclasses.replace(PAIR, short_via=None)])
    second = live_tick(desk, data, t_decide + timedelta(minutes=60))  # the bar that would have filled it
    book = desk.books["coppia"]
    assert second.fills == 0 and book.broker.positions == {} and book.broker.pending_orders() == []
    assert second.books["coppia"]["leverage"] == 0.0
    assert any("annullato l'acquisto di un fondo inverso in coda per coppia" in note for note in second.notes)
    # with the configuration unchanged the same bar fills it (the control)
    other = tmp_path / "control"
    data, desk, day = _pair_live(other, forecast=-12.0)
    live_tick(desk, data, t_decide)
    desk = build_desk(other, RISK, [PAIR])
    assert live_tick(desk, data, t_decide + timedelta(minutes=60)).fills == 1


def test_an_order_the_broker_refuses_is_not_counted_as_queued_and_the_decision_says_so(tmp_path):
    """A book halted by its daily-loss breaker still takes its decision (the day's reading is on the page), and
    the broker refuses the order. The decision said "1 ordine" and "Compro 438 azioni" and nothing else."""
    from engine.core.events import AccountStatus

    data, desk, day = _pair_live(tmp_path, forecast=-12.0)
    state = desk.books["coppia"].broker.state
    state.status, state.breaker_until = AccountStatus.HALTED_BREAKER, day + timedelta(days=3)
    report = live_tick(desk, data, datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC))
    (decision,) = report.decisions
    assert decision["legs"][0]["order_units"] > 0  # what the book asked for is still on the record
    assert decision["n_orders"] == 0 and report.message().endswith("1 decisioni (0 ordini)")
    assert decision["status"].startswith("rifiutato (SCO)")
    assert "Il broker ha rifiutato l'ordine su SCO: non viene eseguito" in decision["rationale"]
    assert desk.books["coppia"].broker.pending_orders() == []


def test_live_without_the_inverse_fund_the_long_side_still_trades_and_the_short_one_says_why(tmp_path):
    data, desk, day = _pair_live(tmp_path, forecast=-12.0, with_fund=False)
    t_decide = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)
    report = live_tick(desk, data, t_decide)
    assert len(report.decisions) == 1 and report.decisions[0]["n_orders"] == 0
    assert report.decisions[0]["limited_by"] == "nessun prezzo per SCO"
    assert any("nessun prezzo recente per SCO" in note for note in report.notes)
    assert any("nessuna barra intraday di SCO" in note for note in report.notes)
    # a long day needs nothing of the inverse fund
    data, desk, day = _pair_live(tmp_path / "long", forecast=12.0, with_fund=False)
    report = live_tick(desk, data, datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC))
    assert report.decisions[0]["order_units"] > 0 and report.decisions[0]["n_orders"] == 1


# ----------------------------------------------------------------------------------------------- the report
def test_each_way_a_vehicle_is_traded_gets_its_own_attribution_table():
    from engine.desk.book import load_books
    from engine.desk.report import attribution_variants, sleeve_configs

    assert attribution_variants(load_books()) == [
        ("BNO", "BNO", True, None),  # the long-only fund book
        ("BNO + SCO", "BNO", False, "SCO"),  # the fund book whose short side is the inverse fund
        ("MCL", "MCL", False, None),  # the futures book sells the future itself
    ]
    both = [SPINTO, dataclasses.replace(SPINTO, id="solo-long", long_only=True)]
    assert [v[0] for v in attribution_variants(both)] == ["MCL", "MCL (long e short)"]
    rows = sleeve_configs("BNO", False, "SCO")
    assert len(rows) == 7 + 3 + 2 and all(c.short_via == "SCO" and not c.long_only for c in rows)
    assert [c.id.split(":")[1] for c in rows[-2:]] == ["prima", "tutte"]
    assert rows[-2].sleeves == {"trend": 1.0, "carry": 1.0, "carry_momentum": 1.0}  # what the desk read before
    assert len({c.id for c in rows}) == len(rows)


def test_the_backtest_fingerprint_changes_with_the_rules_and_only_with_them(monkeypatch):
    from engine.desk import backtest as replay
    from engine.desk import book as sizing
    from engine.desk import data as series
    from engine.desk import options as option_book
    from engine.desk import signals as sg
    from engine.desk import vehicles
    from engine.desk.book import load_books
    from engine.desk.options import load_options_config
    from engine.desk.report import canonical, model_signature

    books, options = load_books(), load_options_config()
    base = model_signature(books, options)
    assert base == model_signature(load_books(), load_options_config()) and len(base) == 12
    changed = [dataclasses.replace(books[0], vol_target=0.13), *books[1:]]
    assert model_signature(changed, options) != base  # a book's rule
    assert model_signature(books, None) != base  # the options book
    assert model_signature(books[:-1], options) != base
    # the account rules the replay runs with (capital, costs, margin) are part of it too
    with_rules = model_signature(books, options, RISK)
    assert with_rules != base and with_rules == model_signature(books, options, RiskConfig.load())
    assert model_signature(books, options, dataclasses.replace(RISK, initial_capital=20_000.0)) != with_rules
    dearer_costs = dataclasses.replace(RISK, costs={**(RISK.costs or {}), "base_spread_bps": 99.0})
    assert model_signature(books, options, dearer_costs) != with_rules
    for module, name, value in ((sizing, "HARD_CAP", 5.0), (option_book, "REPLAY_WIDTH", 0.08)):
        with monkeypatch.context() as patch:
            patch.setattr(module, name, value)
            assert model_signature(books, options) != base
    # everything else the replay's numbers depend on: a constant of the forecast, of the series (how old a
    # copper close may be), of the replay itself (its warm-up), the terms of a vehicle and of an inverse fund
    half_cash = dataclasses.replace(vehicles.SCO, max_weight=0.5)
    dearer = dataclasses.replace(vehicles.BNO, financing_rate=vehicles.BNO.financing_rate + 0.01)
    for change in (
        lambda patch: patch.setattr(sg, "COMBINED_FDM", 1.70),
        lambda patch: patch.setattr(series, "MACRO_MAX_AGE_DAYS", 3),
        lambda patch: patch.setattr(replay, "WARMUP_DAYS", 256),
        lambda patch: patch.setitem(vehicles.INVERSE_FUNDS, "SCO", half_cash),
        lambda patch: patch.setitem(vehicles.VEHICLES, "BNO", dearer),
    ):
        with monkeypatch.context() as patch:
            change(patch)
            assert model_signature(books, options) != base
        assert model_signature(books, options) == base
    # one spelling per value: a set is walked in an order that changes from one process to the next
    assert canonical(frozenset(["b", "a", "c"])) == canonical({"c", "b", "a"}) == "{'a', 'b', 'c'}"
    assert canonical({"k": (1, [2.5, "x"])}) == "{'k': (1, (2.5, 'x'))}"


# ----------------------------------------------------------------------------------------------- live, degraded
def _short_side_open(tmp_path, cfg: BookConfig = PAIR):
    """SYNTHETIC history ending today; the evening before, the book opened its short side: about 0.87x of the
    account in the inverse fund at 20.00. No daily table for the inverse fund: its marks come from the
    30-minute bars alone, as on the first days of a live book."""
    from engine.desk import signals as sg

    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    series = data.series["BNO"]
    day = series.bars.index[-1].date()
    fund_price = float(series.bars["close"].iloc[-2])
    series.forecasts.loc[series.forecasts.index[-1], list(sg.SLEEVES)] = -12.0
    data.legs = {}
    desk = build_desk(tmp_path, RISK, [cfg])
    eve = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC) - timedelta(days=1)
    desk.decide(
        "BNO", eve, (eve - timedelta(hours=4)).date(), "BNO", fund_price, dict(WEAK), 0.10, leg_prices={"SCO": 20.0}
    )
    end = eve + timedelta(minutes=42)
    desk.on_bars("BNO", [(_bar("BNO", end, fund_price, fund_price), 0.10), (_bar("SCO", end, 20.0, 20.0), 0.20)])
    desk.save()
    shares = desk.books[cfg.id].units("SCO")
    assert shares * 20.0 > 0.85 * 10_000
    return data, day, fund_price, shares


@pytest.mark.parametrize(("close", "tripped"), [(19.40, False), (17.20, True)])
def test_the_breaker_judges_a_short_side_on_its_own_completed_bar_not_on_an_old_quote(tmp_path, close, tripped):
    """At 10:18 the bar that is forming prints 17.80, 11 % under the open: a quote, not a close. At 10:48 the
    bar is complete. If it closed at 19.40 the day's loss is 2.6 % and nothing happens; if it closed at 17.20
    the breaker fires, and sells at 17.20. Judged on the FUND's bar with the inverse fund still at the 10:18
    quote, the book was halted in both cases and sold at 17.80, a price that was thirty minutes old."""
    data, day, fund_price, shares = _short_side_open(tmp_path)
    fund = half_hour_bars(day, fund_price, n=4, first=(13, 30), step=0.0)
    inverse = half_hour_bars(day, 20.0, n=4, first=(13, 30), step=0.0)
    forming = inverse.index[1]  # the 10:00-10:30 New York bar
    inverse.loc[forming, ["low", "close"]] = [17.80, close]
    inverse.loc[inverse.index[2:], ["open", "high", "low", "close"]] = close

    def tick(now: datetime, quote: float | None = None):
        data.intraday["BNO"] = fund[fund.index <= pd.Timestamp(now)]
        seen = inverse[inverse.index <= pd.Timestamp(now)].copy()
        if quote is not None:
            seen.loc[forming, ["low", "close"]] = quote  # what the feed shows of the bar still forming
        data.intraday["SCO"] = seen
        desk = build_desk(tmp_path, RISK, [PAIR])
        return live_tick(desk, data, now), desk

    first, _ = tick(datetime(day.year, day.month, day.day, 14, 18, tzinfo=UTC), quote=17.80)
    assert first.fills == 0 and first.books["coppia"]["status"] == "active"
    assert first.books["coppia"]["equity"] == pytest.approx(10_000 - shares * 2.20, abs=15.0)  # marked at the quote
    second, desk = tick(datetime(day.year, day.month, day.day, 14, 48, tzinfo=UTC))
    book = desk.books["coppia"]
    if not tripped:
        assert second.fills == 0 and second.books["coppia"]["status"] == "active" and book.units("SCO") == shares
        assert second.books["coppia"]["equity"] == pytest.approx(10_000 - shares * 0.60, abs=15.0)
    else:
        assert second.fills == 1 and second.books["coppia"]["status"] == "halted_breaker" and book.units("SCO") == 0
        (sale,) = [f for f in StateStore(tmp_path / "desk" / "coppia").read_jsonl("trades") if f["qty_bbl"] < 0]
        assert sale["reason"] == "circuit_breaker" and sale["reference_price"] == pytest.approx(17.20)


@pytest.mark.parametrize("restart", [(14, 31), (14, 44)])
def test_a_tick_between_a_bar_end_and_its_delivery_does_not_cost_the_bar_its_checks(tmp_path, restart):
    """Every restart of the runner ticks at once, at whatever minute it is. At 10:31 the feed, fifteen minutes
    behind, still shows the 10:00-10:30 bar as it was at 10:16; at 10:44 (a feed only ten minutes behind) it
    already shows the first minutes of the NEXT bar, dated 10:30. In neither case has the book been judged on
    the bar that ended at 10:30, 14 % down: the 10:48 tick must feed it as news and the breaker must fire, at
    that bar's close. Measured from the CLOCK of the last mark the bar was "old" and nothing happened until
    the bar after."""
    data, day, fund_price, shares = _short_side_open(tmp_path)
    fund = half_hour_bars(day, fund_price, n=4, first=(13, 30), step=0.0)
    inverse = half_hour_bars(day, 20.0, n=4, first=(13, 30), step=0.0)
    falling, after = inverse.index[1], inverse.index[2]  # the 10:00-10:30 New York bar and the one after it
    inverse.loc[falling, ["low", "close"]] = [17.20, 17.20]
    inverse.loc[inverse.index[2:], ["open", "high", "low", "close"]] = 17.20

    def read(table: pd.DataFrame, when: datetime, rows: int, quote: float | None = None) -> pd.DataFrame:
        seen = table.iloc[:rows].copy()
        if quote is not None:
            seen.loc[seen.index[-1], ["low", "close"]] = quote  # the last row is a bar still forming
        return seen.assign(observed_at=pd.Timestamp(when))

    def tick(now: datetime, rows: int, quote: float | None):
        data.intraday["BNO"] = read(fund, now, rows)
        data.intraday["SCO"] = read(inverse, now, rows, quote)
        desk = build_desk(tmp_path, RISK, [PAIR])
        return live_tick(desk, data, now), desk

    at = lambda h, m: datetime(day.year, day.month, day.day, h, m, tzinfo=UTC)  # noqa: E731
    first, _ = tick(at(14, 18), rows=2, quote=19.60)  # 10:18: the falling bar has just begun
    assert first.books["coppia"]["status"] == "active"
    # the restart: at 10:31 the falling bar as the late feed has it (19.00 at 10:16), at 10:44 the next bar too
    rows, quote = (2, 19.00) if restart == (14, 31) else (3, 17.20)
    off_slot, _ = tick(at(*restart), rows=rows, quote=quote)
    assert off_slot.bars == 0 and off_slot.fills == 0 and off_slot.books["coppia"]["status"] == "active"
    on_slot, desk = tick(at(14, 48), rows=3, quote=17.20)
    assert on_slot.fills == 1 and on_slot.books["coppia"]["status"] == "halted_breaker"
    (sale,) = [f for f in StateStore(tmp_path / "desk" / "coppia").read_jsonl("trades") if f["qty_bbl"] < 0]
    assert sale["reason"] == "circuit_breaker" and sale["reference_price"] == pytest.approx(17.20)
    assert sale["qty_bbl"] == -shares and desk.books["coppia"].units("SCO") == 0
    assert after > falling


def test_after_a_day_without_prices_yesterday_bars_are_old_and_the_book_is_judged_on_today(tmp_path):
    """No price at all for a session: the inverse fund's 30-minute table is missing and there is no daily one.
    The book stays marked where it was. The next morning the first tick still finds nothing - and opens the
    new accounting day on those old marks - then the table returns with yesterday's bars (12 % down at 10:00)
    and today's first (15 % down). Yesterday's are older than the day in progress: they fill what waited and
    judge nothing. Today's bar is the news, the breaker fires on it, and the sale is priced at ITS close - a
    price of now, not yesterday morning's."""
    from engine.desk import signals as sg

    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    series = data.series["BNO"]
    d1, d2, d3 = (series.bars.index[i].date() for i in (-3, -2, -1))
    series.forecasts[list(sg.SLEEVES)] = -12.0
    series.vol[:] = 0.10
    data.legs = {}
    desk = build_desk(tmp_path, RISK, [PAIR])
    t = datetime(d1.year, d1.month, d1.day, 19, 18, tzinfo=UTC)
    desk.decide("BNO", t, d1, "BNO", 60.0, dict(WEAK), 0.10, leg_prices={"SCO": 20.0})
    end = t + timedelta(minutes=42)
    desk.on_bars("BNO", [(_bar("BNO", end, 60.0, 60.0), 0.10), (_bar("SCO", end, 20.0, 20.0), 0.20)])
    desk.save()
    shares = desk.books["coppia"].units("SCO")
    fund = pd.concat([half_hour_bars(d, 60.0, n=13, first=(13, 30), step=0.0) for d in (d2, d3)])
    yesterday = half_hour_bars(d2, 20.0, n=13, first=(13, 30), step=0.0)
    yesterday.iloc[1, [2, 3]] = 17.60
    yesterday.iloc[2:, :4] = 17.60
    today = half_hour_bars(d3, 17.00, n=3, first=(13, 30), step=0.0)
    inverse = pd.concat([yesterday, today])

    def tick(now: datetime, visible: bool):
        data.intraday["BNO"] = fund[fund.index <= pd.Timestamp(now)]
        data.intraday["SCO"] = inverse[inverse.index <= pd.Timestamp(now)] if visible else inverse.iloc[:0]
        desk = build_desk(tmp_path, RISK, [PAIR])
        _nothing_to_decide(desk, now)
        return live_tick(desk, data, now), desk

    for hour, minute in ((14, 18), (17, 48), (21, 18)):
        report, _ = tick(datetime(d2.year, d2.month, d2.day, hour, minute, tzinfo=UTC), visible=False)
        assert report.books["coppia"]["equity"] == pytest.approx(10_000.0, abs=15.0)  # nothing new is known
    report, desk = tick(datetime(d3.year, d3.month, d3.day, 13, 48, tzinfo=UTC), visible=False)
    state = desk.books["coppia"].broker.state
    assert state.day_start_date == d3 and state.day_start_equity == pytest.approx(10_000.0, abs=15.0)
    assert state.last_price_asof is not None and state.last_price_asof.date() == d1  # ... and it says so
    report, desk = tick(datetime(d3.year, d3.month, d3.day, 14, 18, tzinfo=UTC), visible=True)
    assert report.bars >= 14 and report.fills == 1 and report.books["coppia"]["status"] == "halted_breaker"
    (sale,) = [f for f in StateStore(tmp_path / "desk" / "coppia").read_jsonl("trades") if f["qty_bbl"] < 0]
    assert sale["reason"] == "circuit_breaker" and sale["qty_bbl"] == -shares
    assert sale["reference_price"] == pytest.approx(17.00)  # today's bar, not 17.60 from yesterday morning
    assert pd.Timestamp(sale["ts"]).date() == d3


def test_the_bars_after_a_late_fill_are_news_about_the_position_it_opened(tmp_path):
    """A flat book with a purchase of the inverse fund still queued: that fund's 30-minute table has been
    missing since the decision. A flat book is marked on the fund, so "what it knows" is the fund's last quote.
    The table returns on a tick that runs three minutes after a regular one. The purchase fills on yesterday's
    bar, the first after its decision, at 20; today the inverse fund opened at 18. Today's bars of it all end
    before the FUND's last quote - and they are not old news about anything the book held: they are the first
    news there is about what it has just bought. The breaker must see them on this tick, not half an hour on."""
    from engine.desk import signals as sg

    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    series = data.series["BNO"]
    d1, d2 = series.bars.index[-2].date(), series.bars.index[-1].date()
    series.forecasts[list(sg.SLEEVES)] = -18.0
    series.vol[:] = 0.10
    data.legs = {}
    at = lambda d, h, m: datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)  # noqa: E731
    fund = pd.concat([half_hour_bars(d, 60.0, n=13, first=(13, 30), step=0.0) for d in (d1, d2)])
    inverse = pd.concat(
        [
            half_hour_bars(d1, 20.0, n=13, first=(13, 30), step=0.0),
            half_hour_bars(d2, 18.0, n=13, first=(13, 30), step=0.0),
        ]
    )
    desk = build_desk(tmp_path, RISK, [PAIR])
    desk.decide(
        "BNO", at(d1, 19, 18), d1, "BNO", 60.0, dict.fromkeys(sg.SLEEVES, -18.0), 0.10, leg_prices={"SCO": 20.0}
    )
    desk.books["coppia"].broker.mark({}, at(d1, 19, 18), "sintetico", at(d1, 19, 6))
    desk.save()
    (queued,) = desk.books["coppia"].broker.pending_orders()
    assert queued.instrument == "SCO" and queued.qty_bbl > 400

    def tick(now: datetime, visible: bool):
        delivered = pd.Timestamp(now) - pd.Timedelta(minutes=12)  # what a feed twelve minutes behind has
        data.intraday["BNO"] = fund[fund.index <= delivered].assign(observed_at=pd.Timestamp(now))
        if visible:
            data.intraday["SCO"] = inverse[inverse.index <= delivered].assign(observed_at=pd.Timestamp(now))
        else:
            data.intraday.pop("SCO", None)
        desk = build_desk(tmp_path, RISK, [PAIR])
        _nothing_to_decide(desk, now)
        return live_tick(desk, data, now), desk

    for when in (at(d1, 19, 48), at(d1, 20, 18), at(d2, 13, 48), at(d2, 14, 18), at(d2, 14, 48)):
        report, desk = tick(when, visible=False)
        assert report.fills == 0 and desk.books["coppia"].broker.positions == {}
    report, desk = tick(at(d2, 14, 51), visible=True)  # three minutes after the regular tick
    trades = StateStore(tmp_path / "desk" / "coppia").read_jsonl("trades")
    bought, sold = trades  # the purchase that was waiting, and the breaker's sale: both on this tick
    assert bought["qty_bbl"] == queued.qty_bbl and bought["reference_price"] == pytest.approx(20.0)
    assert pd.Timestamp(bought["ts"]).date() == d1  # on yesterday's bar, where the order belonged
    assert sold["reason"] == "circuit_breaker" and sold["reference_price"] == pytest.approx(18.0)
    assert pd.Timestamp(sold["ts"]).date() == d2 and report.books["coppia"]["status"] == "halted_breaker"


@pytest.mark.parametrize("failed", ["both", "inverse"])
def test_a_tick_without_a_download_judges_nothing_it_has_not_seen(tmp_path, failed):
    """10:18: the inverse fund's forming bar prints 17.80, 11 % down. 10:48: the download fails - for both
    tables, or only for the inverse fund's. All the book knows of what it HOLDS is still that 10:18 print. The
    bar closed at 19.40 (-3 %), as the 11:18 tick finds out. Nothing may be judged at 10:48: not on the table
    read at 10:18, whose last row now looks like a finished bar, and not on the fund's bar, which is not the
    position's. The breaker (8 %) fired in both cases and sold at 17.80."""
    data, day, fund_price, shares = _short_side_open(tmp_path)
    fund = half_hour_bars(day, fund_price, n=4, first=(13, 30), step=0.0)
    inverse = half_hour_bars(day, 20.0, n=4, first=(13, 30), step=0.0)
    forming = inverse.index[1]  # the 10:00-10:30 New York bar
    inverse.loc[forming, ["low", "close"]] = [17.80, 19.40]
    inverse.loc[inverse.index[2:], ["open", "high", "low", "close"]] = 19.40

    def read(table: pd.DataFrame, when: datetime, quote: float | None = None) -> pd.DataFrame:
        seen = table[table.index <= pd.Timestamp(when)].copy()
        if quote is not None:
            seen.loc[forming, ["low", "close"]] = quote
        return seen.assign(observed_at=pd.Timestamp(when))

    def tick(now: datetime):
        desk = build_desk(tmp_path, RISK, [PAIR])
        return live_tick(desk, data, now), desk

    at_1018, at_1048, at_1118 = (
        datetime(day.year, day.month, day.day, h, m, tzinfo=UTC) for h, m in ((14, 18), (14, 48), (15, 18))
    )
    data.intraday["BNO"], data.intraday["SCO"] = read(fund, at_1018), read(inverse, at_1018, quote=17.80)
    first, _ = tick(at_1018)
    assert first.books["coppia"]["status"] == "active"
    if failed == "inverse":
        data.intraday["BNO"] = read(fund, at_1048)  # the fund's table did arrive, with its finished bar
    second, desk = tick(at_1048)
    assert second.fills == 0 and second.books["coppia"]["status"] == "active"
    assert second.bars == (1 if failed == "inverse" else 0) and desk.books["coppia"].units("SCO") == shares
    data.intraday["BNO"], data.intraday["SCO"] = read(fund, at_1118), read(inverse, at_1118)
    third, desk = tick(at_1118)
    assert third.fills == 0 and third.books["coppia"]["status"] == "active"
    assert desk.books["coppia"].units("SCO") == shares
    assert third.books["coppia"]["equity"] == pytest.approx(10_000 - shares * 0.60, abs=15.0)


@pytest.mark.parametrize("short", [True, False])
def test_a_table_that_comes_back_fills_what_waited_and_trips_nothing(tmp_path, short):
    """The 30-minute table of what the book holds is missing for a session in which the price moves 12 % in
    the book's favour; the book is marked on the daily close. When the table returns, yesterday's bars are in
    it. Replayed as news they price the position 12 % lower than its mark against today's opening equity: the
    breaker sold a winning position at yesterday morning's price. (The long-only case is as old as the desk.)"""
    from engine.desk import signals as sg

    cfg = PAIR if short else dataclasses.replace(DINAMICO, id="coppia")
    held, px, other, other_px = ("SCO", 20.0, "BNO", 60.0) if short else ("BNO", 60.0, "SCO", 20.0)
    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    series = data.series["BNO"]
    d1, d2, d3 = (series.bars.index[i].date() for i in (-3, -2, -1))
    series.forecasts[list(sg.SLEEVES)] = -12.0 if short else 12.0
    series.vol[:] = 0.10
    data.legs = {}
    desk = build_desk(tmp_path, RISK, [cfg])
    t = datetime(d1.year, d1.month, d1.day, 19, 18, tzinfo=UTC)
    desk.decide("BNO", t, d1, "BNO", 60.0, dict(WEAK if short else STRONG), 0.10, leg_prices={"SCO": 20.0})
    end = t + timedelta(minutes=42)
    desk.on_bars("BNO", [(_bar("BNO", end, 60.0, 60.0), 0.10), (_bar("SCO", end, 20.0, 20.0), 0.20)])
    desk.save()
    shares = desk.books["coppia"].units(held)
    assert shares * px > 0.85 * 10_000

    # day 2: +12 % at 10:00 and flat after; day 3 flat. The other symbol does nothing.
    day2 = half_hour_bars(d2, px, n=13, first=(13, 30), step=0.0)
    day2.iloc[2:, :4] = px * 1.12
    day2.iloc[1, [1, 3]] = px * 1.12
    held_table = pd.concat([day2, half_hour_bars(d3, px * 1.12, n=3, first=(13, 30), step=0.0)])
    other_table = pd.concat(
        [
            half_hour_bars(d2, other_px, n=13, first=(13, 30), step=0.0),
            half_hour_bars(d3, other_px, n=3, first=(13, 30), step=0.0),
        ]
    )

    def tick(now: datetime, visible: bool):
        data.intraday[held] = held_table[held_table.index <= pd.Timestamp(now)] if visible else held_table.iloc[:0]
        data.intraday[other] = other_table[other_table.index <= pd.Timestamp(now)]
        if short:  # the daily table still works: it shows day 2's close from day 2 on
            closes = pd.DataFrame(
                {"open": [20.0, 22.4], "high": [20.0, 22.4], "low": [20.0, 20.0], "close": [20.0, 22.4]},
                index=pd.DatetimeIndex([pd.Timestamp(d1), pd.Timestamp(d2)]),
            )
            data.legs["SCO"] = closes[closes.index <= pd.Timestamp(now.date())]
        else:
            series.bars.loc[pd.Timestamp(d2), ["open", "high", "low", "close"]] = [60.0, 67.2, 60.0, 67.2]
            series.bars.loc[pd.Timestamp(d3), ["open", "high", "low", "close"]] = 67.2
        desk = build_desk(tmp_path, RISK, [cfg])
        _nothing_to_decide(desk, now)
        report = live_tick(desk, data, now)
        return report, desk

    for hour, minute in ((14, 18), (16, 18), (19, 48), (21, 18)):  # day 2: the table is missing all day
        report, _ = tick(datetime(d2.year, d2.month, d2.day, hour, minute, tzinfo=UTC), visible=False)
    # ... and at the first tick of day 3, which is what sets the day's opening equity: 12 % above the entry
    report, desk = tick(datetime(d3.year, d3.month, d3.day, 13, 48, tzinfo=UTC), visible=False)
    assert desk.books["coppia"].broker.state.day_start_date == d3
    marked = report.books["coppia"]["equity"]
    assert desk.books["coppia"].broker.state.day_start_equity == pytest.approx(marked, abs=1.0)
    assert marked == pytest.approx(10_000 + shares * px * 0.12, rel=0.02)  # +12 % on what it holds
    report, desk = tick(datetime(d3.year, d3.month, d3.day, 14, 18, tzinfo=UTC), visible=True)  # ... and is back
    book = desk.books["coppia"]
    assert report.bars >= 14 and report.fills == 0  # yesterday's bars were read, and sold nothing
    assert report.books["coppia"]["status"] == "active" and book.units(held) == shares
    assert report.books["coppia"]["equity"] == pytest.approx(marked, abs=5.0)
    assert book.broker.state.last_prices[held] == pytest.approx(px * 1.12)


def test_live_an_inverse_fund_left_without_its_configuration_keeps_its_own_price_and_is_sold(tmp_path):
    data, day, fund_price, shares = _short_side_open(tmp_path)
    data.intraday["BNO"] = half_hour_bars(day, fund_price, n=13, first=(13, 30), step=0.0)
    data.intraday["SCO"] = half_hour_bars(day, 20.0, n=13, first=(13, 30), step=0.0)
    # `short_via` is removed from the configuration while the book is short
    bare = dataclasses.replace(PAIR, short_via=None)
    assert latest_price(data, "BNO", "SCO", datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)) == (None, None)
    desk = build_desk(tmp_path, RISK, [bare])
    morning = live_tick(desk, data, datetime(day.year, day.month, day.day, 14, 48, tzinfo=UTC))
    # still marked at ITS price (the fund trades three times higher: on that table the book would be worth
    # tens of thousands), still fed its own bars, still counted as a short position
    assert abs(morning.books["coppia"]["equity"] - 10_000.0) < 30.0 and morning.bars >= 2
    assert desk.books["coppia"].broker.state.last_prices["SCO"] == pytest.approx(20.0)
    assert morning.books["coppia"]["leverage"] == pytest.approx(
        2 * shares * 20.0 / morning.books["coppia"]["equity"], abs=0.01
    )
    # the day's decision is not postponed "until the roll", and it sells the fund the book should not hold
    t_decide = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)
    desk = build_desk(tmp_path, RISK, [bare])
    decided = live_tick(desk, data, t_decide)
    (decision,) = decided.decisions
    assert decision["legs"][0]["order_units"] == -shares and "non ha più un lato short" in decision["rationale"]
    desk = build_desk(tmp_path, RISK, [bare])
    after = live_tick(desk, data, t_decide + timedelta(minutes=60))
    assert after.fills == 1 and desk.books["coppia"].broker.positions == {}


def test_the_order_that_follows_a_roll_fills_on_the_new_contract_the_same_session(tmp_path):
    """On the roll day a book is fed two contracts. The new one has never been fed: its bars must not be hidden
    behind the old one's (which, fed first, moved the book's last mark past them) until the next morning."""
    from engine.desk import signals as sg

    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    series = data.series["MCL"]
    day = series.bars.index[-1].date()
    new = held_contract("CL", add_business_days(day, 1, "US"))
    old = "CLF26" if new != "CLF26" else "CLG26"
    price = float(series.bars["close"].iloc[-1])
    cfg = dataclasses.replace(SPINTO, vol_target=2.0)
    desk = build_desk(tmp_path, RISK, [cfg])
    t0 = datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC)
    desk.decide("MCL", t0, add_business_days(day, -1, "US"), old, price, dict(LONG), 0.30)
    desk.on_bar("MCL", _bar(old, t0 + timedelta(minutes=60), price, price))
    desk.save()
    held = desk.books["spinto"].units(old)
    series.forecasts.loc[series.forecasts.index[-1], list(sg.SLEEVES)] = 2.0  # today wants much less
    bars = [half_hour_bars(day, price, n=14, first=(13, 30), step=0.0).reset_index().assign(code=c) for c in (old, new)]
    data.intraday["MCL"] = pd.concat(bars, ignore_index=True)
    now = datetime(day.year, day.month, day.day, 18, 48, tzinfo=UTC)  # 14:48 New York: roll, then the decision
    desk = build_desk(tmp_path, RISK, [cfg])
    report = live_tick(desk, data, now)
    (decision,) = report.decisions
    assert report.rolled == 1 and decision["symbol"] == new and decision["order_units"] < 0
    assert desk.books["spinto"].units(new) == held
    desk = build_desk(tmp_path, RISK, [cfg])
    assert live_tick(desk, data, now + timedelta(minutes=30)).fills == 0  # 15:18: no bar has started since
    desk = build_desk(tmp_path, RISK, [cfg])
    report = live_tick(desk, data, now + timedelta(minutes=60))  # 15:48: the 15:00 bar of the NEW contract
    assert report.fills == 1 and desk.books["spinto"].units(new) == decision["target_units"]
    assert desk.books["spinto"].broker.pending_orders() == []


def test_a_contract_rolled_into_is_fed_its_bars_without_waiting_for_an_order(tmp_path):
    """A position moved onto a contract the book was never fed must be watched from that moment: its bars are
    what margin and the breaker are judged on. Nothing to trade after the roll (the position is where the
    forecast wants it) must not mean nothing to look at."""
    from engine.desk import signals as sg

    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    series = data.series["MCL"]
    day = series.bars.index[-1].date()
    new = held_contract("CL", add_business_days(day, 1, "US"))
    old = "CLF26" if new != "CLF26" else "CLG26"
    price = float(series.bars["close"].iloc[-1])
    bought = dataclasses.replace(SPINTO, vol_target=2.0, weekend_max_leverage=None)  # the day is a Friday
    desk = build_desk(tmp_path, RISK, [bought])
    t0 = datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC)
    desk.decide("MCL", t0, add_business_days(day, -1, "US"), old, price, dict(LONG), 0.30)
    desk.on_bar("MCL", _bar(old, t0 + timedelta(minutes=60), price, price))
    desk.save()
    held = desk.books["spinto"].units(old)
    assert held > 0
    cfg = dataclasses.replace(bought, buffer=50.0)  # from here on a band so wide that nothing is traded
    series.forecasts.loc[series.forecasts.index[-1], list(sg.SLEEVES)] = 12.0
    bars = [half_hour_bars(day, price, n=14, first=(13, 30), step=0.0).reset_index().assign(code=c) for c in (old, new)]
    data.intraday["MCL"] = pd.concat(bars, ignore_index=True)
    now = datetime(day.year, day.month, day.day, 18, 48, tzinfo=UTC)  # 14:48 New York: the roll
    desk = build_desk(tmp_path, RISK, [cfg])
    report = live_tick(desk, data, now)
    (decision,) = report.decisions
    assert report.rolled == 1 and decision["order_units"] == 0.0 and desk.books["spinto"].units(new) == held
    assert desk.books["spinto"].broker.pending_orders() == []
    desk = build_desk(tmp_path, RISK, [cfg])
    live_tick(desk, data, now + timedelta(minutes=30))  # 15:18: the bar that ended at 15:00
    state = desk.books["spinto"].broker.state
    assert state.last_bar_ts[f"{new}|30m"] == datetime(day.year, day.month, day.day, 19, 0, tzinfo=UTC)
