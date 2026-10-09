"""The paper broker, for accounts that trade more than one symbol on the same clock (the desk's fund book with a
short leg): orders that only reduce, orders that wait for another, a leverage cap that weighs an inverse fund
for its multiple, bars that arrive late, and risk checks run once per moment.

Every price is SYNTHETIC and chosen so that the right answer can be read off the numbers.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.broker.paper import PaperBroker
from engine.core.config import RiskConfig
from engine.core.events import AccountStatus, Bar, Order, OrderReason, OrderType
from engine.core.ids import idempotency_key
from engine.desk.book import BookConfig, book_risk
from engine.desk.vehicles import BNO

BASE = RiskConfig.load()
# a fund account exactly as the desk configures it: one-share lots, no commission, 2x in dollars, 50 % margin
FUND = dataclasses.replace(
    book_risk(BASE, BookConfig(id="t", name="t", vehicle="BNO", vol_target=0.25, max_leverage=2.0), BNO),
    daily_loss_breaker=0.08,
)
T0 = datetime(2026, 10, 6, 14, 3, tzinfo=UTC)


def _bar(symbol: str, end: datetime, o: float, c: float | None = None) -> Bar:
    c = o if c is None else c
    return Bar(symbol=symbol, ts=end, open=o, high=max(o, c), low=min(o, c), close=c, interval="30m", source="test")


def _order(symbol: str, qty: float, ts: datetime, tag: str = "", **extra) -> Order:
    key = idempotency_key("test", symbol, qty, ts.isoformat(), tag)
    return Order(
        order_id=key[:16], idempotency_key=key, ts=ts, account_id="t", instrument=symbol, qty_bbl=qty,
        order_type=OrderType.MARKET, reason=OrderReason.SIGNAL, **extra,
    )  # fmt: skip


def _broker(weights: dict[str, float] | None = None, risk: RiskConfig = FUND) -> PaperBroker:
    broker = PaperBroker("t", risk)
    broker.exposure_weights = dict(weights or {})
    broker.net_queued_sales = True  # as the desk's books set it
    return broker


def _buy(broker: PaperBroker, symbol: str, qty: float, price: float, ts: datetime) -> None:
    broker.note_price(symbol, price)
    assert broker.submit(_order(symbol, qty, ts))
    broker.on_bar(_bar(symbol, ts + timedelta(minutes=57), price))
    assert broker.positions[symbol].qty_bbl == qty


# ----------------------------------------------------------------------------------------------- reduce only
def test_a_reduce_only_sale_closes_what_is_there_and_never_opens_the_other_side():
    broker = _broker()
    _buy(broker, "BNO", 329.0, 60.0, T0)  # 1.97x of the account
    t1 = T0 + timedelta(days=1)
    assert broker.submit(_order("BNO", -329.0, t1, reduce_only=True))
    # before the sale fills, the fund falls and the leverage cap forces a few shares out
    forced = broker.on_bar(_bar("BNO", t1 + timedelta(minutes=27), 59.0, 58.6))  # started before the order
    assert len(forced) == 1 and forced[0].reason == OrderReason.MARGIN_CALL
    left = 329.0 + forced[0].qty_bbl
    assert 320.0 < left < 329.0 and broker.positions["BNO"].qty_bbl == left
    fills = broker.on_bar(_bar("BNO", t1 + timedelta(minutes=57), 58.6))
    assert [f.qty_bbl for f in fills] == [-left]  # what was left, not the 329 that were ordered
    assert fills[0].meta["qty_reduce_only"] == {"from": -329.0, "to": -left}
    assert "BNO" not in broker.positions and broker.pending_orders() == []
    # the same order without the flag is the oversell: a few shares short of a fund that cannot be shorted
    plain = _broker()
    _buy(plain, "BNO", 329.0, 60.0, T0)
    assert plain.submit(_order("BNO", -329.0, t1))
    plain.on_bar(_bar("BNO", t1 + timedelta(minutes=27), 59.0, 58.6))
    plain.on_bar(_bar("BNO", t1 + timedelta(minutes=57), 58.6))
    assert plain.positions["BNO"].qty_bbl == left - 329.0 < 0


def test_a_reduce_only_order_with_nothing_to_reduce_is_cancelled_not_filled():
    broker = _broker()
    broker.note_price("BNO", 60.0)
    assert broker.submit(_order("BNO", -10.0, T0, reduce_only=True))
    assert broker.on_bar(_bar("BNO", T0 + timedelta(minutes=57), 60.0)) == []
    assert broker.positions == {} and broker.pending_orders() == []
    # and one that would ADD to the position it finds is not a reduction either
    _buy(broker, "BNO", 10.0, 60.0, T0 + timedelta(hours=1))
    t2 = T0 + timedelta(hours=3)
    assert broker.submit(_order("BNO", 5.0, t2, reduce_only=True))
    assert broker.on_bar(_bar("BNO", t2 + timedelta(minutes=57), 60.0)) == []
    assert broker.positions["BNO"].qty_bbl == 10.0


# ----------------------------------------------------------------------------------------------- waits for
def test_an_order_waits_in_the_queue_while_the_order_it_names_is_still_there():
    broker = _broker({"SCO": 2.0})
    _buy(broker, "BNO", 290.0, 60.0, T0)  # 1.74x
    t1 = T0 + timedelta(days=1)
    broker.note_price("SCO", 20.0)
    sale = _order("BNO", -290.0, t1, reduce_only=True)
    purchase = _order("SCO", 430.0, t1, after=sale.order_id)  # 0.86x in dollars, 1.72x against the cap
    assert broker.submit(sale) and broker.submit(purchase)  # the queued sale frees the room for it
    end = t1 + timedelta(minutes=57)
    # the purchase leg's bar arrives alone: nothing fills, and the book never holds both sides
    assert broker.on_bar(_bar("SCO", end, 20.0)) == []
    assert set(broker.positions) == {"BNO"} and len(broker.pending_orders()) == 2
    # the sale's bar: the sale fills; the purchase had its bar already and waits for the next one
    assert [f.instrument for f in broker.on_bar(_bar("BNO", end, 60.0))] == ["BNO"]
    assert broker.positions == {} and [o.instrument for o in broker.pending_orders()] == ["SCO"]
    fills = broker.on_bar(_bar("SCO", end + timedelta(minutes=30), 20.2))
    assert [(f.instrument, f.qty_bbl, f.reference_price) for f in fills] == [("SCO", 430.0, 20.2)]
    # a cancelled order is out of the queue too: what waited for it is free to fill
    other = _broker()
    other.note_price("BNO", 60.0)
    first, second = _order("BNO", 5.0, T0, "a"), _order("BNO", 7.0, T0, "b")
    second.after = first.order_id
    assert other.submit(first) and other.submit(second)
    assert other.cancel_pending({first.order_id}) == 1
    assert [f.qty_bbl for f in other.on_bar(_bar("BNO", T0 + timedelta(minutes=57), 60.0))] == [7.0]


# ----------------------------------------------------------------------------------------------- cap weights
def test_an_inverse_fund_counts_twice_against_the_leverage_cap_at_submit_and_at_the_fill_price():
    broker = _broker({"SCO": 2.0})
    broker.note_price("SCO", 20.0)
    # 510 shares at 20 are 1.02x of the account in dollars and 2.04x against the cap: refused outright
    assert not broker.submit(_order("SCO", 510.0, T0, "big"))
    assert "post-trade leverage 2.04x exceeds hard cap 2x" in broker.state.last_reject_reason
    # 470 shares are 0.94x in dollars at the price the decision saw ...
    assert broker.submit(_order("SCO", 470.0, T0))
    # ... and the fund opens 8 % higher: the fill is cut so that the dollars never exceed the account
    (fill,) = broker.on_bar(_bar("SCO", T0 + timedelta(minutes=57), 21.6))
    assert fill.meta["qty_capped_by_leverage"]["from"] == 470.0 and fill.qty_bbl < 470.0
    snap = broker.snapshot(T0 + timedelta(hours=1))
    assert snap.gross_notional <= snap.equity  # paid in cash: nothing is borrowed
    assert broker.charge_financing(0.0525, 1.0, T0 + timedelta(days=1)) == 0.0
    assert 2.0 * snap.gross_notional / snap.equity == pytest.approx(2.0, abs=0.005)
    # an account without weights buys the same 470 shares whole: the weight is the only difference
    plain = _broker()
    plain.note_price("SCO", 20.0)
    assert plain.submit(_order("SCO", 470.0, T0))
    assert plain.on_bar(_bar("SCO", T0 + timedelta(minutes=57), 21.6))[0].qty_bbl == 470.0


def test_the_cap_is_enforced_after_a_mark_in_the_weighted_terms_and_margin_stays_in_dollars():
    broker = _broker({"SCO": 2.0})
    _buy(broker, "BNO", 100.0, 60.0, T0)  # 0.6x
    broker.note_price("SCO", 20.0)
    t1 = T0 + timedelta(hours=2)
    assert broker.submit(_order("SCO", 340.0, t1))  # 0.68x in dollars, 1.36x against the cap: 1.96x in all
    broker.on_bar(_bar("SCO", t1 + timedelta(minutes=57), 20.0))
    assert broker.positions["SCO"].qty_bbl == 340.0
    # the fund falls 10 %: equity 9 400, the fund 5 400 and the inverse fund 2 x 6 800: 2.02x against the cap,
    # while in plain dollars the account holds 1.30x. The cut is five shares of the inverse fund: it is the
    # position that weighs most, and each of its shares frees 40 $ of cap.
    fills = broker.on_bar(_bar("BNO", t1 + timedelta(hours=2), 54.0))
    assert [(f.instrument, f.reason, f.meta["trigger"]) for f in fills] == [
        ("SCO", OrderReason.MARGIN_CALL, "leverage_cap")
    ]
    assert fills[0].qty_bbl in (-5.0, -6.0)
    snap = broker.snapshot(t1 + timedelta(hours=3))
    weighted = 100.0 * 54.0 + 2.0 * broker.positions["SCO"].qty_bbl * 20.0
    assert weighted <= 2.0 * snap.equity + 1e-6 < weighted + 2.0 * 2.0 * 20.0  # under, by less than two shares
    assert snap.margin_level is not None and snap.margin_level > 1.5  # in dollars: nowhere near a stop-out
    # the same account without the weight sees 1.30x and cuts nothing
    plain = _broker()
    _buy(plain, "BNO", 100.0, 60.0, T0)
    plain.note_price("SCO", 20.0)
    assert plain.submit(_order("SCO", 340.0, t1))
    plain.on_bar(_bar("SCO", t1 + timedelta(minutes=57), 20.0))
    assert plain.on_bar(_bar("BNO", t1 + timedelta(hours=2), 54.0)) == []


# ----------------------------------------------------------------------------------------------- late bars
def test_a_late_bar_fills_what_waited_for_it_and_marks_nothing():
    broker = _broker()
    _buy(broker, "BNO", 100.0, 60.0, T0)
    now = T0 + timedelta(days=1)
    broker.mark({"BNO": 66.0}, now, "daily close", now)  # the account already knows a newer price
    equity = broker.snapshot(now).equity
    assert broker.submit(_order("BNO", -40.0, T0 + timedelta(hours=2), reduce_only=True))
    # yesterday's bar arrives now: it opens at 61, where the sale fills, and closes at 54 - ten per cent under
    # the mark the account has. As news it would be a loss of 720 $ on the day; it is not news.
    late = _bar("BNO", T0 + timedelta(hours=3), 61.0, 54.0)
    fills = broker.on_bar(late, mark=False, checks=False)
    assert [(f.qty_bbl, f.reference_price) for f in fills] == [(-40.0, 61.0)]
    assert broker.state.last_prices["BNO"] == 66.0 and broker.state.last_mark_ts == now
    assert broker.positions["BNO"].qty_bbl == 60.0 and broker.status == AccountStatus.ACTIVE
    # the sale at 61 instead of the 66 the account was marked at: 40 x 5 $ less, and nothing else moved
    assert broker.snapshot(now).equity == pytest.approx(equity - 40 * 5.0, abs=1.0)
    assert broker.on_bar(late, mark=False, checks=False) == []  # and it is not looked at twice
    # the same bar run as a normal one prices the position at 54 and trips the 8 % daily breaker
    naive = _broker()
    _buy(naive, "BNO", 150.0, 60.0, T0)
    naive.mark({"BNO": 66.0}, now, "daily close", now)
    naive.mark({"BNO": 66.0}, now + timedelta(hours=14), "open", now + timedelta(hours=14))  # a new day begins
    naive.on_bar(_bar("BNO", now + timedelta(hours=14, minutes=1), 61.0, 59.0))
    assert naive.status == AccountStatus.HALTED_BREAKER


# ----------------------------------------------------------------------------------------------- one check
def test_a_bar_without_checks_followed_by_check_risk_is_the_same_as_a_bar():
    def run(split: bool) -> tuple[float, str, dict[str, float], int]:
        broker = _broker()
        _buy(broker, "BNO", 320.0, 60.0, T0)
        t = T0 + timedelta(hours=2)
        fills = 0
        for i, close in enumerate((59.0, 57.5, 55.0, 53.0)):  # a slide through the leverage cap and the breaker
            bar = _bar("BNO", t + timedelta(minutes=30 * (i + 1)), 60.0 if i == 0 else (59.0, 57.5, 55.0)[i - 1], close)
            if split:
                fills += len(broker.on_bar(bar, vol_annual=0.4, checks=False))
                fills += len(broker.check_risk(bar.ts, bar.source, vol_annual=0.4))
            else:
                fills += len(broker.on_bar(bar, vol_annual=0.4))
        snap = broker.snapshot(t + timedelta(hours=3))
        return round(snap.equity, 6), str(snap.status), {s: p.qty_bbl for s, p in broker.positions.items()}, fills

    assert run(split=True) == run(split=False)
    assert run(split=False)[1] == "halted_breaker" and run(split=False)[3] >= 2


def test_two_symbols_of_one_moment_are_judged_together_not_one_on_the_other_s_old_quote():
    broker = _broker({"SCO": 2.0})
    _buy(broker, "SCO", 430.0, 20.0, T0)  # the short side: 0.86x in dollars
    t1 = T0 + timedelta(days=1)
    broker.mark({"SCO": 17.8, "BNO": 60.0}, t1, "quote", t1)  # the bar that is forming prints its low: -9.5 %
    end = t1 + timedelta(minutes=12)
    fund, inverse = _bar("BNO", end, 60.0, 60.5), _bar("SCO", end, 19.9, 19.4)  # ... and closes at -3 %
    # fed the old way, the fund's bar is judged with the inverse fund still at 17.80: breaker, sold at 17.80
    old = PaperBroker("t", FUND, state=type(broker.state).from_dict(broker.state.to_dict()))
    forced = old.on_bar(fund)
    assert [(f.reason, round(f.reference_price, 2)) for f in forced] == [(OrderReason.CIRCUIT_BREAKER, 17.8)]
    # fed moment by moment: both marks first, one check after
    assert broker.on_bar(inverse, checks=False) == [] and broker.on_bar(fund, checks=False) == []
    assert broker.check_risk(end, "test") == []
    assert broker.status == AccountStatus.ACTIVE and broker.positions["SCO"].qty_bbl == 430.0


def test_a_queued_sale_frees_room_for_another_purchase_only_for_an_account_that_asks():
    """The older accounts (the first system's master and shadows) use this broker with its defaults, and for
    them ``submit`` must be what it was: a purchase that needs the room a sale is about to free is refused
    while that sale is only queued. A desk book changes side in one decision and turns the allowance on."""
    for netting, accepted in ((False, False), (True, True)):
        broker = PaperBroker("t", FUND)
        broker.net_queued_sales = netting
        _buy(broker, "BNO", 300.0, 60.0, T0)  # 1.8x of a 2x account
        later = T0 + timedelta(hours=2)
        broker.note_price("SCO", 20.0)
        assert broker.submit(_order("BNO", -300.0, later, "sale"))
        assert broker.submit(_order("SCO", 400.0, later, "buy")) is accepted
        if not accepted:
            assert "exceeds hard cap" in broker.state.last_reject_reason
    assert PaperBroker("t", FUND).net_queued_sales is False and PaperBroker("t", FUND).exposure_weights == {}


# ----------------------------------------------------------------------------------------------- invariant
@settings(max_examples=150, deadline=None)
@given(
    steps=st.lists(
        st.tuples(
            st.sampled_from(["BNO", "SCO"]),
            st.floats(min_value=-600.0, max_value=600.0, allow_nan=False),
            st.floats(min_value=0.80, max_value=1.25, allow_nan=False),
            st.booleans(),
        ),
        min_size=1,
        max_size=14,
    )
)
def test_no_fill_and_no_bar_leaves_the_weighted_leverage_above_the_cap(steps):
    """Random orders on a fund and an inverse fund, random price jumps between the order and its bar: after
    every bar's own checks the account is within its cap counted with the weights, and it never borrows to hold
    the inverse fund alone."""
    broker = _broker({"SCO": 2.0})
    prices = {"BNO": 60.0, "SCO": 20.0}
    ts = T0
    for n, (symbol, qty, jump, reduce) in enumerate(steps):
        ts += timedelta(hours=1)
        for sym, px in prices.items():
            broker.note_price(sym, px)
        qty = float(round(qty))
        if qty:
            broker.submit(_order(symbol, qty, ts, str(n), reduce_only=reduce and qty < 0))
        prices[symbol] *= jump
        position = broker.positions.get(symbol)
        before = 0.0 if position is None else position.qty_bbl
        fills = broker.on_bar(_bar(symbol, ts + timedelta(minutes=57), prices[symbol]))
        if broker.status == AccountStatus.DEAD:
            break
        equity = broker._equity()
        weighted = sum(abs(p.qty_bbl) * prices[s] * (2.0 if s == "SCO" else 1.0) for s, p in broker.positions.items())
        assert equity <= 0 or weighted <= 2.0 * equity + 2.0 * 60.0, (n, weighted, equity, [f.reason for f in fills])
        for fill in fills:
            # An order that only shrinks the position it finds adds no risk: after it the account can still be
            # over its cap because the PRICE moved (a short bought back by one share into a 25 % jump), and the
            # bar's own checks deal with that. Every other order was sized, at the fill price, to leave the
            # account within the cap - in dollars too, since no weight is below one.
            shrinks = before * fill.qty_bbl < 0 and abs(fill.qty_bbl) <= abs(before)
            if fill.reason == OrderReason.SIGNAL and not shrinks:
                assert fill.meta["leverage_after"] <= 2.0 + 1e-6, (n, before, fill.qty_bbl)
        held = broker.positions.get("SCO")
        if held is not None and set(broker.positions) == {"SCO"} and held.qty_bbl > 0 and equity > 0:
            assert held.qty_bbl * prices["SCO"] <= equity + 20.0  # the inverse fund alone is never on margin
