"""One book: the size it wants, the ceilings on it, the whole lots it ends up with, and the order that follows.

Inputs are SYNTHETIC. The invariants at the bottom are the desk's hard rules: never above the book's ceiling,
never above 10x, never short where shorting is not allowed, whatever the forecast and the volatility say.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import UTC, date, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.core.config import RiskConfig
from engine.desk.book import HARD_CAP, Book, BookConfig, book_risk, buffered_target, is_pre_closure, load_books
from engine.desk.vehicles import BNO, MCL, get_vehicle

RISK = RiskConfig.load()
THURSDAY = date(2026, 10, 8)
FRIDAY = date(2026, 10, 9)
TS = datetime(2026, 10, 8, 19, 3, tzinfo=UTC)
LONG = {"trend": 13.77, "carry": 10.0, "carry_momentum": -10.0}  # combines to +5.74 with equal weights


def _book(vehicle: str = "BNO", **over) -> Book:
    base = {
        "id": "test",
        "name": "Test",
        "vehicle": vehicle,
        "vol_target": 0.12,
        "max_leverage": 1.0,
        "long_only": vehicle == "BNO",
    }
    return Book(BookConfig(**{**base, **over}), RISK)


# ----------------------------------------------------------------------------------------------- config
def test_the_books_on_file_are_the_three_documented_ones_and_none_exceeds_the_hard_cap():
    books = {b.id: b for b in load_books()}
    assert list(books) == ["prudente", "dinamico", "spinto"]
    assert books["prudente"].max_leverage == 1.0 and books["prudente"].long_only
    assert books["dinamico"].max_leverage == 2.0 and books["dinamico"].weekend_max_leverage == 1.5
    assert books["spinto"].vehicle == "MCL" and books["spinto"].max_leverage == 10.0 and not books["spinto"].long_only
    assert books["spinto"].weekend_max_leverage == 3.0
    for b in books.values():
        assert 0 < b.max_leverage <= HARD_CAP
        assert b.max_leverage <= get_vehicle(b.vehicle).max_leverage + 1e-9  # the broker's margin allows it
        assert 0 < b.vol_target <= 0.5  # nothing above full Kelly for a Sharpe of 0.5


@pytest.mark.parametrize(
    "bad",
    [
        {"max_leverage": 10.5},
        {"max_leverage": 0.0},
        {"weekend_max_leverage": 3.0, "max_leverage": 2.0},
        {"vol_target": 0.0},
        {"sleeves": {"trend": 1.0, "astrology": 1.0}},
    ],
)
def test_a_book_that_breaks_a_rule_cannot_be_configured(bad):
    with pytest.raises(ValueError):
        BookConfig(**{"id": "x", "name": "x", "vehicle": "MCL", "vol_target": 0.2, "max_leverage": 2.0, **bad})


def test_book_risk_takes_lot_margin_and_fees_from_the_vehicle():
    cfg = BookConfig(id="x", name="x", vehicle="MCL", vol_target=0.5, max_leverage=10.0)
    risk = book_risk(RISK, cfg, MCL)
    assert risk.lot_bbl == 100.0 and risk.margin_rate == 0.10 and risk.max_leverage == 10.0
    assert risk.costs["commission_per_lot_usd"] == 1.27
    fund = book_risk(RISK, dataclasses.replace(cfg, vehicle="BNO", max_leverage=5.0), BNO)
    assert fund.max_leverage == 2.0  # Regulation T: a fund cannot be held above 2x whatever the config asks
    assert fund.lot_bbl == 1.0 and fund.costs["commission_per_lot_usd"] == 0.0


def test_pre_closure_days_are_the_last_session_before_more_than_one_day_off():
    assert is_pre_closure(FRIDAY) and not is_pre_closure(THURSDAY)
    assert is_pre_closure(date(2026, 11, 25))  # the Wednesday before Thanksgiving
    assert not is_pre_closure(date(2026, 11, 24))


# ----------------------------------------------------------------------------------------------- the buffer
@pytest.mark.parametrize(
    ("exact", "buffer", "current", "cap", "want"),
    [
        (0.51, 0.13, 0, 10, 0),  # half a lot wanted, flat: not enough to buy a whole one
        (0.51, 0.13, 1, 10, 1),  # ... and not little enough to sell the one already held
        (0.70, 0.13, 0, 10, 1),
        (0.30, 0.13, 1, 10, 0),
        (-1.5, 0.2, 2, 10, -1),  # a flip goes to the nearer edge of the new band
        (0.0, 0.9, 3, 10, 0),  # no view at all is flat, inertia or not
        (7.6, 0.5, 2, 5.99, 5),  # the ceiling is rounded DOWN to whole lots
        (24.7, 4.3, 0, 156, 20),  # from below: to the lower edge
        (24.7, 4.3, 30, 156, 29),  # from above: to the upper edge
        (24.7, 4.3, 22, 156, 22),  # inside the band: left alone
        (float("nan"), 0.1, 2, 10, 0),
    ],
)
def test_buffered_target(exact, buffer, current, cap, want):
    assert buffered_target(exact, buffer, current, cap) == want


# ----------------------------------------------------------------------------------------------- decisions
def test_exposure_is_the_forecast_times_the_volatility_ratio_and_the_order_buys_to_the_edge_of_the_band():
    book = _book()
    dec, order = book.decide(TS, THURSDAY, "BNO", 63.94, dict(LONG), 0.4354)
    assert dec.forecast["combined"] == pytest.approx(5.7375, abs=1e-3)
    assert dec.exposure == pytest.approx(0.57375 * 0.12 / 0.4354, rel=1e-4)
    # 24.7 shares wanted, buffer 4.3: a flat book buys to the lower edge, 20 shares
    assert dec.target_units == 20.0 and order is not None and order.qty_bbl == 20.0
    assert dec.exposure_actual == pytest.approx(20 * 63.94 / 10_000)
    assert "Compro 20 azioni di BNO" in dec.rationale and "previsione +5,7 su 20" in dec.rationale
    # 0.16x wanted, 0.13x bought: the reader is told it is the band, not left to wonder
    assert "esposizione 0,16x" in dec.rationale and "long, 0,13x; al bordo della fascia di inerzia" in dec.rationale
    assert order.instrument == "BNO" and order.idempotency_key and order.rationale == dec.rationale


def test_one_micro_contract_is_most_of_a_small_account_and_the_book_says_so():
    spinto = _book("MCL", vol_target=0.50, max_leverage=10.0)
    # 0.47x wanted, one lot is 0.91x: half a lot is not enough to buy one
    dec, order = spinto.decide(
        TS, THURSDAY, "CLX26", 91.37, {"trend": 9.46, "carry": 10.0, "carry_momentum": -10.0}, 0.4207
    )
    assert order is None and dec.target_units == 0.0 and dec.limited_by == "lotto minimo"
    assert "meno di quanto serve per un lotto intero" in dec.rationale
    # a stronger forecast (0.74x wanted) buys one lot and reports what that lot is really worth
    dec, order = spinto.decide(
        TS, THURSDAY, "CLX26", 91.37, {"trend": 15.0, "carry": 10.0, "carry_momentum": -10.0}, 0.4207
    )
    assert order is not None and order.qty_bbl == 100.0
    assert dec.exposure == pytest.approx(0.7428, abs=1e-3) and dec.exposure_actual == pytest.approx(0.9137)
    assert "un lotto da 100 barili vale 0,91x del conto" in dec.rationale
    assert order.leverage["value"] == pytest.approx(0.9137) and order.leverage["wanted"] == pytest.approx(
        0.7428, abs=1e-3
    )


def test_a_long_only_book_never_sells_short_and_a_futures_book_does():
    bearish = {"trend": -15.0, "carry": -10.0, "carry_momentum": -10.0}
    dec, order = _book().decide(TS, THURSDAY, "BNO", 60.0, bearish, 0.30)
    assert order is None and dec.target_units == 0.0 and dec.limited_by == "solo long"
    dec, order = _book("MCL", vol_target=0.5, max_leverage=10.0).decide(TS, THURSDAY, "CLX26", 90.0, bearish, 0.30)
    assert order is not None and order.qty_bbl < 0 and dec.exposure < 0


def test_the_ceiling_binds_and_is_lower_before_a_weekend():
    strong = {"trend": 20.0, "carry": 10.0, "carry_momentum": 10.0}
    book = _book("MCL", vol_target=0.50, max_leverage=10.0, weekend_max_leverage=3.0)
    thu, _ = book.decide(TS, THURSDAY, "CLX26", 90.0, strong, 0.10)  # 1.67 x 0.5 / 0.10 = 8.3x wanted
    fri, _ = book.decide(TS, FRIDAY, "CLX26", 90.0, strong, 0.10)
    assert thu.exposure == pytest.approx(8.333, abs=1e-3) and thu.limited_by == "obiettivo di volatilità"
    assert fri.exposure == 3.0 and fri.limited_by == "tetto prima della chiusura dei mercati"
    assert thu.target_units * 90.0 / 10_000 <= 10.0 and fri.target_units * 90.0 / 10_000 <= 3.0
    calm, _ = book.decide(TS, THURSDAY, "CLX26", 90.0, strong, 0.10 / 3)  # 25x wanted
    assert calm.exposure == 10.0 and calm.limited_by == "tetto di leva del libro"
    # 11.1 lots are 10x; the buffer is 1.7 lots, so a flat book buys to the lower edge of the band: nine
    assert calm.target_units == 900.0
    # ... and a book already holding twelve is brought back under the ceiling: eleven, never twelve
    assert buffered_target(11.11, 1.667, 12, 11.11) == 11


def test_missing_inputs_hold_the_position_and_say_why():
    book = _book()
    for forecast, vol, price in (
        ({"trend": None, "carry": None, "carry_momentum": None}, 0.3, 60.0),
        (dict(LONG), None, 60.0),
        (dict(LONG), float("nan"), 60.0),
        (dict(LONG), 0.3, 0.0),
    ):
        dec, order = book.decide(TS, THURSDAY, "BNO", price, forecast, vol)
        assert order is None and dec.order_units == 0.0 and dec.limited_by == "dati mancanti"
        assert "Dati insufficienti" in dec.rationale


def test_the_same_day_always_produces_the_same_idempotency_key():
    a = _book().decide(TS, THURSDAY, "BNO", 63.94, dict(LONG), 0.4354)[1]
    b = _book().decide(TS.replace(hour=20), THURSDAY, "BNO", 64.50, dict(LONG), 0.4354)[1]
    c = _book().decide(TS, FRIDAY, "BNO", 63.94, dict(LONG), 0.4354)[1]
    assert a is not None and b is not None and c is not None
    assert a.idempotency_key == b.idempotency_key != c.idempotency_key


def test_a_book_uses_its_own_sleeve_weights():
    only_trend = _book(sleeves={"trend": 1.0})
    dec, _ = only_trend.decide(TS, THURSDAY, "BNO", 63.94, dict(LONG), 0.4354)
    assert dec.forecast["combined"] == pytest.approx(13.77)


# ----------------------------------------------------------------------------------------------- invariants
forecast_value = st.one_of(st.none(), st.floats(min_value=-20.0, max_value=20.0, allow_nan=False))


@settings(max_examples=300, deadline=None)
@given(
    vehicle=st.sampled_from(["BNO", "MCL"]),
    trend=forecast_value,
    carry=st.sampled_from([None, -10.0, 0.0, 10.0]),
    momentum=st.sampled_from([None, -10.0, 10.0]),
    vol=st.floats(min_value=0.005, max_value=3.0, allow_nan=False),
    price=st.floats(min_value=1.0, max_value=400.0, allow_nan=False),
    vol_target=st.sampled_from([0.12, 0.25, 0.50]),
    max_leverage=st.sampled_from([1.0, 2.0, 5.0, 10.0]),
    weekend=st.sampled_from([None, 0.5, 1.0]),
    day=st.sampled_from([THURSDAY, FRIDAY]),
)
def test_no_decision_ever_targets_more_than_the_ceiling(
    vehicle, trend, carry, momentum, vol, price, vol_target, max_leverage, weekend, day
):
    cfg = BookConfig(
        id="p",
        name="p",
        vehicle=vehicle,
        vol_target=vol_target,
        max_leverage=max_leverage,
        weekend_max_leverage=weekend,
        long_only=vehicle == "BNO",
    )
    book = Book(cfg, RISK)
    dec, order = book.decide(TS, day, "X", price, {"trend": trend, "carry": carry, "carry_momentum": momentum}, vol)
    v = get_vehicle(vehicle)
    ceiling = min(HARD_CAP, max_leverage, v.max_leverage)
    if weekend is not None and day == FRIDAY:
        ceiling = min(ceiling, weekend)
    notional = abs(dec.target_units) * price
    assert notional <= ceiling * dec.equity + 1e-6
    assert notional <= HARD_CAP * dec.equity + 1e-6
    assert abs(dec.exposure) <= ceiling + 1e-12
    if vehicle == "BNO":
        assert dec.target_units >= 0 and (order is None or order.qty_bbl > 0)
    assert math.isclose(dec.target_units % v.lot, 0.0, abs_tol=1e-9) or math.isclose(
        dec.target_units % v.lot, v.lot, abs_tol=1e-9
    )
    if order is not None:
        assert order.qty_bbl == dec.order_units != 0
