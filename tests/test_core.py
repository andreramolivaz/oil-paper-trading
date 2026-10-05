from datetime import UTC, datetime

from engine.core.events import AccountStatus, Bar, Direction, Order, OrderReason, OrderType, Signal
from engine.core.ids import idempotency_key
from engine.core.instruments import Future, Instrument
from engine.core.timeutil import (
    is_after_settlement,
    is_ice_session_open,
    next_weekend_gap_hours,
    settlement_ts,
)


def test_event_roundtrip():
    ts = datetime(2026, 10, 5, 18, 30, tzinfo=UTC)
    o = Order(
        order_id="o1",
        idempotency_key="k",
        ts=ts,
        account_id="master",
        instrument="BZZ26",
        qty_bbl=-300,
        order_type=OrderType.MARKET,
        reason=OrderReason.SIGNAL,
        strategies_for=["S1", "S4"],
        leverage={"kelly": 2.1, "vol": 1.4, "chosen": 1.4},
    )
    d = o.to_dict()
    assert d["ts"] == "2026-10-05T18:30:00Z" and d["order_type"] == "market"
    o2 = Order.from_dict(d)
    assert o2 == o


def test_signal_score_sign():
    ts = datetime(2026, 10, 5, 18, 30, tzinfo=UTC)
    s = Signal("S1", ts, "BZZ26", Direction.SHORT, 0.7, -0.02, 0.05, 5)
    assert s.score < 0 and abs(s.score - (-0.4)) < 1e-9
    assert Signal.from_dict(s.to_dict()) == s


def test_bar_and_status_enums():
    b = Bar("BZ=F", datetime(2026, 10, 5, tzinfo=UTC), 100, 101, 99, 100.5, source="yahoo")
    assert Bar.from_dict(b.to_dict()) == b
    assert AccountStatus("dead") == AccountStatus.DEAD


def test_idempotency_key_stable():
    assert idempotency_key(1, "a", 2.5) == idempotency_key(1, "a", 2.5)
    assert idempotency_key(1, "a") != idempotency_key("1a")


def test_instruments():
    f = Future.from_code("BZZ26")
    ins = Instrument.future(f)
    assert ins.symbol == "BZZ26" and ins.expiry == f.expiry and ins.root == "BZ"
    sp = Instrument.calendar_spread(Future.from_code("BZZ26"), Future.from_code("BZF27"))
    assert sp.symbol == "BZZ26-BZF27" and sp.legs[1].ratio == -1.0


def test_session_clock():
    # Monday 2026-10-05 12:00 UTC = 13:00 London (BST) -> open
    assert is_ice_session_open(datetime(2026, 10, 5, 12, 0, tzinfo=UTC))
    # 22:30 UTC = 23:30 London -> closed
    assert not is_ice_session_open(datetime(2026, 10, 5, 22, 30, tzinfo=UTC))
    # Saturday -> closed
    assert not is_ice_session_open(datetime(2026, 10, 10, 12, 0, tzinfo=UTC))
    # settlement 19:30 London BST = 18:30 UTC on 2026-10-05
    assert settlement_ts(datetime(2026, 10, 5, tzinfo=UTC).date()) == datetime(2026, 10, 5, 18, 30, tzinfo=UTC)
    assert is_after_settlement(datetime(2026, 10, 5, 18, 31, tzinfo=UTC))
    assert not is_after_settlement(datetime(2026, 10, 5, 18, 29, tzinfo=UTC))
    # GMT in December: settlement 19:30 UTC
    assert settlement_ts(datetime(2026, 12, 7, tzinfo=UTC).date()) == datetime(2026, 12, 7, 19, 30, tzinfo=UTC)
    assert next_weekend_gap_hours(datetime(2026, 10, 9, 21, 0, tzinfo=UTC)) == 1.0  # Fri 22:00 London -> 1h to close
    assert next_weekend_gap_hours(datetime(2026, 10, 10, 12, 0, tzinfo=UTC)) == 0.0
