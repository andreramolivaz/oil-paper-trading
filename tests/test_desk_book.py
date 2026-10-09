"""One book: the size it wants, the ceilings on it, the whole lots it ends up with, and the order that follows.

Inputs are SYNTHETIC. The invariants at the bottom are the desk's hard rules: never above the book's ceiling,
never above 10x, never short where shorting is not allowed, whatever the forecast and the volatility say.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.core.config import RiskConfig
from engine.core.events import Bar
from engine.desk.book import HARD_CAP, Book, BookConfig, book_risk, buffered_target, is_pre_closure, load_books
from engine.desk.vehicles import BNO, MCL, SCO, get_vehicle

RISK = RiskConfig.load()
THURSDAY = date(2026, 10, 8)
FRIDAY = date(2026, 10, 9)
TS = datetime(2026, 10, 8, 19, 3, tzinfo=UTC)
# The seven sleeves as they read on the Brent fund on 2026-10-08. By source: price (13.87 + 6.21 + 15.81) / 3 =
# +11.96, curve (10 - 10) / 2 = 0, other markets (7.38 - 12.57) / 2 = -2.60; a third each and the 1.75
# multiplier: +5.46.
LONG = {"trend": 13.87, "accel": 6.21, "skew": 15.81, "carry": 10.0, "carry_momentum": -10.0}
LONG |= {"copper": 7.38, "dollar": -12.57}


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
    assert dec.forecast["combined"] == pytest.approx(5.4649, abs=1e-3)
    assert dec.exposure == pytest.approx(0.54649 * 0.12 / 0.4354, rel=1e-4)
    # 23.6 shares wanted, buffer 4.3: a flat book buys to the lower edge, 19 shares
    assert dec.target_units == 19.0 and order is not None and order.qty_bbl == 19.0
    assert dec.exposure_actual == pytest.approx(19 * 63.94 / 10_000)
    assert "Compro 19 azioni di BNO" in dec.rationale and "previsione +5,5 su 20" in dec.rationale
    # the reader gets the three sources, not seven numbers: the sleeves are in the forecast table
    assert dec.rationale.startswith("Prezzo +12,0, curva +0,0, altri mercati -2,6: ")
    # 0.15x wanted, 0.12x bought: the reader is told it is the band, not left to wonder
    assert "esposizione 0,15x" in dec.rationale and "long, 0,12x; al bordo della fascia di inerzia" in dec.rationale
    assert order.instrument == "BNO" and order.idempotency_key and order.rationale == dec.rationale
    # what pushed for the trade and what pushed against it, sleeve by sleeve
    assert order.strategies_for == ["trend", "accel", "skew", "carry", "copper"]
    assert order.strategies_against == ["carry_momentum", "dollar"]


def test_one_micro_contract_is_most_of_a_small_account_and_the_book_says_so():
    spinto = _book("MCL", vol_target=0.50, max_leverage=10.0)
    # the WTI reading of 2026-10-08 (the fund's, with the future's own trend and acceleration): +4.07, which
    # is 0.48x wanted against a lot worth 0.91x - half a lot is not enough to buy one
    wti = {**LONG, "trend": 9.51, "accel": 3.42}
    dec, order = spinto.decide(TS, THURSDAY, "CLX26", 91.37, wti, 0.4207)
    assert dec.forecast["combined"] == pytest.approx(4.0746, abs=1e-3)
    assert dec.exposure == pytest.approx(0.4843, abs=1e-3)
    assert order is None and dec.target_units == 0.0 and dec.limited_by == "lotto minimo"
    assert "meno di quanto serve per un lotto intero" in dec.rationale
    # a stronger forecast (0.73x wanted) buys one lot and reports what that lot is really worth
    dec, order = spinto.decide(TS, THURSDAY, "CLX26", 91.37, {**wti, "trend": 20.0}, 0.4207)
    assert order is not None and order.qty_bbl == 100.0
    assert dec.exposure == pytest.approx(0.7267, abs=1e-3) and dec.exposure_actual == pytest.approx(0.9137)
    assert "un lotto da 100 barili vale 0,91x del conto" in dec.rationale
    assert order.leverage["value"] == pytest.approx(0.9137) and order.leverage["wanted"] == pytest.approx(
        0.7267, abs=1e-3
    )


def test_a_long_only_book_never_sells_short_and_a_futures_book_does():
    bearish = {"trend": -15.0, "carry": -10.0, "carry_momentum": -10.0}
    dec, order = _book().decide(TS, THURSDAY, "BNO", 60.0, bearish, 0.30)
    assert order is None and dec.target_units == 0.0 and dec.limited_by == "solo long"
    # the reader is told why there is no position, not that "0,00x" is the size wanted
    assert dec.rationale.endswith("Negativa, e questo libro non va short: resta in contanti. Nessuna posizione su BNO.")
    dec, order = _book("MCL", vol_target=0.5, max_leverage=10.0).decide(TS, THURSDAY, "CLX26", 90.0, bearish, 0.30)
    assert order is not None and order.qty_bbl < 0 and dec.exposure < 0


def test_the_ceiling_binds_and_is_lower_before_a_weekend():
    # price (20 + 10 + 12) / 3 = 14, curve 10, other markets (10 + 6) / 2 = 8: 10.67 x 1.75 = +18.67
    strong = {"trend": 20.0, "accel": 10.0, "skew": 12.0, "carry": 10.0, "carry_momentum": 10.0}
    strong |= {"copper": 10.0, "dollar": 6.0}
    book = _book("MCL", vol_target=0.50, max_leverage=10.0, weekend_max_leverage=3.0)
    thu, _ = book.decide(TS, THURSDAY, "CLX26", 90.0, strong, 0.10)  # 1.87 x 0.5 / 0.10 = 9.3x wanted
    fri, _ = book.decide(TS, FRIDAY, "CLX26", 90.0, strong, 0.10)
    assert thu.exposure == pytest.approx(9.333, abs=1e-3) and thu.limited_by == "obiettivo di volatilità"
    assert fri.exposure == 3.0 and fri.limited_by == "tetto prima della chiusura dei mercati"
    assert thu.target_units * 90.0 / 10_000 <= 10.0 and fri.target_units * 90.0 / 10_000 <= 3.0
    calm, _ = book.decide(TS, THURSDAY, "CLX26", 90.0, strong, 0.10 / 3)  # 28x wanted
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
    assert dec.forecast["combined"] == pytest.approx(13.87)  # one sleeve: no diversification multiplier
    assert dec.rationale.startswith("Prezzo +13,9: ")  # and only the source it reads is named
    # the default is a third per source, not a seventh per sleeve: the two macro sleeves weigh as much as
    # the three price ones
    third = dict.fromkeys(("trend", "accel", "skew"), 1 / 9)
    third |= dict.fromkeys(("carry", "carry_momentum", "copper", "dollar"), 1 / 6)
    assert _book().cfg.sleeves == pytest.approx(third)
    for cfg in load_books():
        assert cfg.sleeves == pytest.approx(third), cfg.id  # no book on file overrides the mix


# ----------------------------------------------------------------------------------------------- the short leg
SHORT = {name: -value for name, value in LONG.items()}  # the same reading with every sign turned: -5.46
MONDAY = date(2026, 10, 12)


def _pair(**over) -> Book:
    """A fund book that is allowed to be short: long in BNO up to 2x, short by BUYING the -2x fund SCO."""
    base = {"vol_target": 0.25, "max_leverage": 2.0, "weekend_max_leverage": 1.5, "long_only": False}
    return _book(**{**base, "short_via": "SCO", **over})


def _fill(book: Book, orders, prices: dict[str, float], end: datetime) -> None:
    """Submit and execute at ``prices`` on a bar that ends at ``end`` (sales first, as the desk feeds them)."""
    for symbol, px in prices.items():
        book.broker.note_price(symbol, px)  # what Desk.decide does: an order is measured against a price
    for order in orders:
        assert book.broker.submit(order)
    for order in orders:
        px = prices[order.instrument]
        bar = Bar(symbol=order.instrument, ts=end, open=px, high=px, low=px, close=px, interval="30m", source="test")
        book.broker.on_bar(bar)


def test_a_short_leg_is_for_fund_books_that_may_be_short_and_names_a_fund_that_exists():
    base = {"id": "x", "name": "x", "vol_target": 0.2, "max_leverage": 2.0}
    assert BookConfig(vehicle="BNO", short_via="SCO", **base).short_via == "SCO"
    for bad in (
        {"vehicle": "BNO", "short_via": "XYZ"},  # no such fund
        {"vehicle": "BNO", "short_via": "SCO", "long_only": True},  # a long-only book has no short side
        {"vehicle": "MCL", "short_via": "SCO"},  # a futures book sells the future
    ):
        with pytest.raises(ValueError):
            BookConfig(**base, **bad)
    on_file = {b.id: b for b in load_books()}
    assert on_file["dinamico"].short_via == "SCO" and not on_file["dinamico"].long_only
    assert on_file["prudente"].short_via is None and on_file["prudente"].long_only
    assert on_file["spinto"].short_via is None  # it sells the future itself
    # the fund is bought with cash: at most its own dollars, which is twice that in oil exposure
    assert SCO.multiplier == -2.0 and SCO.max_weight == 1.0 and SCO.max_exposure == 2.0
    with pytest.raises(ValueError):
        _pair().decide(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354)  # two legs: orders(), not decide()


def test_a_short_forecast_buys_the_inverse_fund_for_half_the_dollars():
    book = _pair()
    dec, orders = book.orders(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=18.61)
    assert dec.forecast["combined"] == pytest.approx(-5.4649, abs=1e-3)
    assert dec.exposure == pytest.approx(-0.54649 * 0.25 / 0.4354, rel=1e-4)  # -0.314x of oil
    # 1x of short exposure is 10 000 / 18.61 / 2 = 268.7 shares: 84.3 wanted, buffer 15.4, a flat book buys
    # to the lower edge of the band
    assert [(o.instrument, o.qty_bbl) for o in orders] == [("SCO", 69.0)]
    assert dec.order_units == 0.0 and dec.target_units == 0.0 and dec.n_orders == 1  # nothing on the fund itself
    assert dec.legs == [
        {"symbol": "SCO", "price": 18.61, "target_units": 69.0, "current_units": 0.0, "order_units": 69.0,
         "multiplier": -2.0}
    ]  # fmt: skip
    dollars = orders[0].qty_bbl * 18.61
    assert dollars / 10_000 == pytest.approx(0.1284, abs=1e-4)  # the dollars: 0.13x of the account ...
    assert dec.exposure_actual == pytest.approx(-2 * 69 * 18.61 / 10_000)  # ... the exposure: -0.26x
    assert "esposizione 0,31x, short (tramite SCO, che ogni giorno rende -2 volte il WTI" in dec.rationale
    assert "Compro 69 azioni di SCO: obiettivo 69 (short, 0,26x; al bordo della fascia di inerzia)." in dec.rationale
    # buying the inverse fund is the SHORT trade: the sleeves that are negative pushed for it
    assert orders[0].strategies_for == ["trend", "accel", "skew", "carry", "copper"]
    assert orders[0].strategies_against == ["carry_momentum", "dollar"]
    assert orders[0].rationale == dec.rationale and orders[0].leverage["value"] == pytest.approx(0.2568, abs=1e-4)


def test_changing_side_sells_one_leg_and_buys_the_other_in_the_same_decision():
    book = _pair()
    _, orders = book.orders(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=18.61)
    _fill(book, orders, {"SCO": 18.61}, TS + timedelta(minutes=57))
    assert book.units("SCO") == 69.0 and book.units("BNO") == 0.0
    equity = float(book.broker.snapshot(TS).equity)
    assert book.net_exposure(equity) == pytest.approx(-2 * 69 * 18.61 / equity)
    assert book.effective_leverage(equity) == pytest.approx(2 * 69 * 18.61 / equity)  # twice the dollars
    # the inverse fund is the book's other leg, not a contract waiting to be rolled
    assert book.other_contract_positions("BNO") == []

    # the forecast turns long: 49.1 fund shares wanted, buffer 9.0 -> 40; the short side is far outside its band
    later = TS + timedelta(days=4)
    dec, orders = book.orders(later, MONDAY, "BNO", 63.94, dict(LONG), 0.4354, leg_price=18.61)
    assert [(o.instrument, o.qty_bbl) for o in orders] == [("SCO", -69.0), ("BNO", 40.0)]  # the sale first
    assert dec.n_orders == 2 and dec.order_units == 40.0 and dec.legs[0]["order_units"] == -69.0
    assert dec.exposure_actual == pytest.approx(40 * 63.94 / dec.equity)
    assert "Compro 40 azioni di BNO: obiettivo 40 (long, 0,26x; al bordo della fascia di inerzia)." in dec.rationale
    assert dec.rationale.endswith("Vendo 69 azioni di SCO: il lato short si chiude.")
    assert orders[0].strategies_for == orders[1].strategies_for == ["trend", "accel", "skew", "carry", "copper"]
    _fill(book, orders, {"SCO": 18.61, "BNO": 63.94}, later + timedelta(minutes=57))
    assert book.units("SCO") == 0.0 and book.units("BNO") == 40.0

    # ... and back: the fund is sold, the inverse fund bought
    dec, orders = book.orders(later + timedelta(days=1), date(2026, 10, 13), "BNO", 63.94, dict(SHORT), 0.4354, 18.61)
    assert [(o.instrument, o.qty_bbl) for o in orders] == [("BNO", -40.0), ("SCO", 69.0)]
    assert dec.rationale.endswith("Vendo 40 azioni di BNO: il lato long si chiude.")


def test_the_short_leg_has_the_same_inertia_band_as_the_long_one():
    book = _pair()
    _, orders = book.orders(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=18.61)
    _fill(book, orders, {"SCO": 18.61}, TS + timedelta(minutes=57))
    later = TS + timedelta(days=4)
    # the same forecast again: 69 shares held, band 69-100 -> nothing to do
    dec, orders = book.orders(later, MONDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=18.61)
    assert orders == [] and dec.n_orders == 0 and dec.legs[0]["target_units"] == 69.0
    assert "Posizione invariata: 69 azioni su SCO (short, 0,26x; dentro la fascia di inerzia)." in dec.rationale
    # a forecast that is barely long (+0.3, which is 0.017x): the fund's band still contains zero, so nothing
    # is bought; the inverse fund is trimmed to the edge of ITS band (-4.6 shares wanted, buffer 15.4: 11)
    # instead of being sold whole. Price (5 - 5 - 1) / 3, curve 0, other markets (9.7 - 8) / 2.
    barely = {"trend": 5.0, "accel": -5.0, "skew": -1.0, "carry": 10.0, "carry_momentum": -10.0}
    barely |= {"copper": 9.7, "dollar": -8.0}
    dec, orders = book.orders(later, MONDAY, "BNO", 63.94, barely, 0.4354, leg_price=18.61)
    assert dec.forecast["combined"] == pytest.approx(0.3014, abs=1e-3) and 0 < dec.exposure < 0.02
    assert [(o.instrument, o.qty_bbl) for o in orders] == [("SCO", -58.0)]
    assert dec.legs[0]["target_units"] == 11.0 and dec.target_units == 0.0
    assert "Nessuna posizione su BNO. Vendo 58 azioni di SCO: del lato short ne restano 11." in dec.rationale
    # no view at all is flat on both legs, inertia or not
    flat = dict.fromkeys(LONG, 0.0)
    dec, orders = book.orders(later, MONDAY, "BNO", 63.94, flat, 0.4354, leg_price=18.61)
    assert [(o.instrument, o.qty_bbl) for o in orders] == [("SCO", -69.0)] and dec.exposure == 0.0


def test_the_ceilings_are_on_the_oil_exposure_and_the_inverse_fund_is_never_bought_on_margin():
    very_short = {"trend": -20.0, "accel": -10.0, "skew": -12.0, "carry": -10.0, "carry_momentum": -10.0}
    very_short |= {"copper": -10.0, "dollar": -6.0}  # -18.67
    book = _pair()
    thu, orders = book.orders(TS, THURSDAY, "BNO", 63.94, very_short, 0.10, leg_price=18.61)  # 4.7x wanted
    assert thu.exposure == -2.0 and thu.limited_by == "tetto di leva del libro"
    # 2x of exposure is 537 shares; buffer 67 -> a flat book buys 470: 0.87x of the account in dollars
    assert orders[0].instrument == "SCO" and orders[0].qty_bbl == 470.0
    assert 470 * 18.61 <= 10_000 and thu.exposure_actual == pytest.approx(-2 * 470 * 18.61 / 10_000)
    # the last decision before a weekend: 1.5x of exposure, which is 0.75x of the account in the fund
    fri, orders = book.orders(TS, FRIDAY, "BNO", 63.94, very_short, 0.10, leg_price=18.61)
    assert fri.exposure == -1.5 and fri.limited_by == "tetto prima della chiusura dei mercati"
    assert orders[0].qty_bbl == 336.0 and 336 * 18.61 / 10_000 < 0.75
    # a book that already holds more than the weekend allows is brought back UNDER the ceiling, not just to
    # the edge of its inertia band (470 shares held are inside the band 336-470, and above the 1.5x of 402)
    opening = book.orders(TS, THURSDAY, "BNO", 63.94, very_short, 0.10, leg_price=18.61)[1]
    _fill(book, opening, {"SCO": 18.61}, TS + timedelta(minutes=57))
    assert book.units("SCO") == 470.0
    fri, orders = book.orders(TS + timedelta(days=1), FRIDAY, "BNO", 63.94, very_short, 0.10, leg_price=18.61)
    assert [(o.instrument, o.qty_bbl) for o in orders] == [("SCO", -68.0)]
    assert 2 * fri.legs[0]["target_units"] * 18.61 <= 1.5 * fri.equity < 2 * 403 * 18.61
    # a book held to 1x is held to 1x on the short side too: half the account in the fund, never more
    small = _pair(max_leverage=1.0, weekend_max_leverage=None)
    dec, orders = small.orders(TS, THURSDAY, "BNO", 63.94, very_short, 0.10, leg_price=18.61)
    assert dec.exposure == -1.0 and orders[0].qty_bbl == 202.0 and 202 * 18.61 <= 0.5 * 10_000


def test_without_a_recent_price_the_short_side_is_never_opened_or_added_to_only_reduced():
    book = _pair()
    dec, orders = book.orders(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=None)
    assert orders == [] and dec.limited_by == "nessun prezzo per SCO" and dec.legs[0]["price"] is None
    assert "Nessun prezzo recente per SCO: il lato short non si apre." in dec.rationale
    # A book that was fed the inverse fund's bars has a "last price" for it even when it holds none. That is
    # not a price to open a position on: it can be days old (the table went missing), and the order would fill
    # wherever the fund has gone since.
    book.broker.note_price("SCO", 18.61)
    dec, orders = book.orders(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=None)
    assert orders == [] and dec.limited_by == "nessun prezzo per SCO" and dec.legs[0]["price"] is None
    # a long forecast does not need the inverse fund at all
    dec, orders = book.orders(TS, THURSDAY, "BNO", 63.94, dict(LONG), 0.4354, leg_price=None)
    assert [(o.instrument, o.qty_bbl) for o in orders] == [("BNO", 40.0)]

    # a short side that is already HELD has a last mark, and may be reduced on it ...
    held = _pair()
    _, opening = held.orders(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=18.61)
    _fill(held, opening, {"SCO": 18.61}, TS + timedelta(minutes=57))
    later = TS + timedelta(days=4)
    dec, orders = held.orders(later, MONDAY, "BNO", 63.94, dict(LONG), 0.4354, leg_price=None)
    assert [(o.instrument, o.qty_bbl) for o in orders] == [("SCO", -69.0), ("BNO", 40.0)]
    assert dec.legs[0]["price"] == 18.61
    # ... but not added to: three times the short forecast wants 253 shares, the book keeps its 69 and says why
    stronger = {name: 3 * value for name, value in SHORT.items()}
    dec, orders = held.orders(later, MONDAY, "BNO", 63.94, stronger, 0.4354, leg_price=None)
    assert orders == [] and dec.legs[0]["target_units"] == 69.0 and dec.limited_by == "nessun prezzo per SCO"
    assert "Nessun prezzo recente per SCO: il lato short non cresce. Posizione invariata: 69 azioni" in dec.rationale
    # The words say what happened and nothing else. With a quarter of the volatility the formula wants 3.8x and
    # the book's ceiling cuts it to 2x: THAT is what limited the exposure, and the 69 shares (0.25x) are still
    # 69 because there is no price - they are nowhere near the inertia band around 2x.
    dec, orders = held.orders(later, MONDAY, "BNO", 63.94, stronger, 0.4354 / 4, leg_price=None)
    assert orders == [] and dec.limited_by == "nessun prezzo per SCO" and dec.exposure == -2.0
    assert "limitata da: tetto di leva del libro" in dec.rationale and "limitata da: nessun prezzo" not in dec.rationale
    assert (
        "fascia di inerzia" not in dec.rationale and "Posizione invariata: 69 azioni su SCO (short, 0," in dec.rationale
    )
    # with a price it does grow
    _, orders = held.orders(later, MONDAY, "BNO", 63.94, stronger, 0.4354, leg_price=18.61)
    assert [(o.instrument, o.qty_bbl > 100) for o in orders] == [("SCO", True)]


def test_a_fund_is_sold_reduce_only_and_the_other_leg_is_bought_after_the_sale():
    book = _pair()
    _, opening = book.orders(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=18.61)
    assert [(o.reduce_only, o.after) for o in opening] == [(False, None)]  # a purchase on its own waits for nothing
    _fill(book, opening, {"SCO": 18.61}, TS + timedelta(minutes=57))
    _, flip = book.orders(TS + timedelta(days=4), MONDAY, "BNO", 63.94, dict(LONG), 0.4354, leg_price=18.61)
    sale, purchase = flip
    assert (sale.instrument, sale.reduce_only, sale.after) == ("SCO", True, None)
    assert (purchase.instrument, purchase.reduce_only, purchase.after) == ("BNO", False, sale.order_id)
    # a future can be short: its sale is an ordinary order, free to open the other side
    bearish = {"trend": -15.0, "carry": -10.0, "carry_momentum": -10.0}
    _, order = _book("MCL", vol_target=0.5, max_leverage=10.0).decide(TS, THURSDAY, "CLX26", 90.0, bearish, 0.30)
    assert order is not None and order.qty_bbl < 0 and not order.reduce_only
    # the fund of a long-only book is sold reduce-only too
    long_only = _book()
    _, buy = long_only.decide(TS, THURSDAY, "BNO", 63.94, dict(LONG), 0.4354)
    _fill(long_only, [buy], {"BNO": 63.94}, TS + timedelta(minutes=57))
    _, sell = long_only.decide(TS + timedelta(days=4), MONDAY, "BNO", 63.94, dict(SHORT), 0.4354)
    assert sell is not None and sell.qty_bbl == -19.0 and sell.reduce_only


def test_an_inverse_fund_held_after_its_configuration_is_gone_is_sold_not_forgotten():
    """`short_via` removed from the file while the book is short: the position is still an inverse fund (twice
    its dollars of short exposure, priced on its own table), it must not block the book's decisions as if it
    were an old contract, and the next decision sells it."""
    with_leg = _pair()
    _, opening = with_leg.orders(TS, THURSDAY, "BNO", 63.94, dict(SHORT), 0.4354, leg_price=18.61)
    _fill(with_leg, opening, {"SCO": 18.61}, TS + timedelta(minutes=57))
    without = _book(vol_target=0.25, max_leverage=2.0, long_only=False)  # the same book, the line removed
    without.broker.state = with_leg.broker.state
    assert without.short is None and without.leg() is SCO and without.uses("SCO")
    assert without.other_contract_positions("BNO") == [] and without.multiplier("SCO") == -2.0
    equity = float(without.broker.snapshot(TS).equity)
    assert without.net_exposure(equity) == pytest.approx(-2 * 69 * 18.61 / equity)
    later = TS + timedelta(days=4)
    for forecast, fund_units in ((SHORT, 0.0), (LONG, 40.0)):
        dec, orders = without.orders(later, MONDAY, "BNO", 63.94, dict(forecast), 0.4354, leg_price=18.61)
        assert (orders[0].instrument, orders[0].qty_bbl, orders[0].reduce_only) == ("SCO", -69.0, True)
        assert dec.legs[0]["target_units"] == 0.0 and dec.target_units == fund_units
        assert dec.rationale.endswith("Vendo 69 azioni di SCO: il libro non ha più un lato short.")
    assert dec.exposure > 0  # the long side is decided as usual
    # a book that neither is configured for the fund nor holds it has nothing to do with it
    assert _book().leg() is None and not _book().uses("SCO") and not _book("MCL", max_leverage=10.0).uses("SCO")


def test_the_inverse_fund_is_never_asked_to_carry_more_than_it_can_in_cash():
    """With the funds on file the book's own ceiling (2x) already equals what SCO can carry in cash (1x of the
    account at -2x), so the clamp never binds; a fund that may only be half the account shows that it works."""
    book = _pair()
    book.short = dataclasses.replace(SCO, max_weight=0.5)  # at most half the account: 1x of oil
    raw, capped, limited = book.exposure(-18.67, 0.10, THURSDAY)
    assert raw == pytest.approx(-4.67, abs=0.01) and capped == -1.0
    assert limited == "il fondo inverso si compra solo in contanti"
    assert book.exposure(18.67, 0.10, THURSDAY)[1] == 2.0  # the long side keeps the book's own ceiling
    very_short = dict.fromkeys(LONG, -11.0)
    dec, orders = book.orders(TS, THURSDAY, "BNO", 63.94, very_short, 0.10, leg_price=18.61)
    assert dec.exposure == -1.0 and orders[0].qty_bbl * 18.61 <= 0.5 * 10_000


# ----------------------------------------------------------------------------------------------- invariants
forecast_value = st.one_of(st.none(), st.floats(min_value=-20.0, max_value=20.0, allow_nan=False))


@settings(max_examples=300, deadline=None)
@given(
    level=st.floats(min_value=-20.0, max_value=20.0, allow_nan=False),
    vol=st.floats(min_value=0.005, max_value=3.0, allow_nan=False),
    price=st.floats(min_value=1.0, max_value=400.0, allow_nan=False),
    leg_price=st.one_of(st.none(), st.floats(min_value=0.5, max_value=400.0, allow_nan=False)),
    vol_target=st.sampled_from([0.12, 0.25, 0.50]),
    max_leverage=st.sampled_from([1.0, 2.0]),
    weekend=st.sampled_from([None, 0.5, 1.5]),
    day=st.sampled_from([THURSDAY, FRIDAY]),
)
def test_a_book_with_a_short_leg_never_exceeds_its_ceiling_on_either_side(
    level, vol, price, leg_price, vol_target, max_leverage, weekend, day
):
    if weekend is not None and weekend > max_leverage:
        weekend = max_leverage
    book = _pair(vol_target=vol_target, max_leverage=max_leverage, weekend_max_leverage=weekend)
    dec, orders = book.orders(TS, day, "BNO", price, dict.fromkeys(LONG, level), vol, leg_price=leg_price)
    ceiling = min(max_leverage, weekend) if weekend is not None and day == FRIDAY else max_leverage
    assert abs(dec.exposure) <= ceiling + 1e-12
    assert dec.target_units >= 0 and dec.target_units * price <= ceiling * dec.equity + 1e-6
    leg = dec.legs[0]
    assert leg["target_units"] >= 0
    if leg["price"] is not None:
        dollars = leg["target_units"] * leg["price"]
        assert dollars <= dec.equity + 1e-6  # never on margin
        assert 2.0 * dollars <= ceiling * dec.equity + 1e-6  # and never more oil exposure than the ceiling
    assert not (dec.target_units > 0 and leg["target_units"] > 0)  # a flat book never opens both sides
    assert len(orders) <= 1 and all(o.qty_bbl > 0 for o in orders)
    if orders:
        assert orders[0].instrument == ("BNO" if dec.exposure > 0 else "SCO")


@settings(max_examples=300, deadline=None)
@given(
    vehicle=st.sampled_from(["BNO", "MCL"]),
    trend=forecast_value,
    accel=forecast_value,
    skew=forecast_value,
    carry=st.sampled_from([None, -10.0, 0.0, 10.0]),
    momentum=st.sampled_from([None, -10.0, 10.0]),
    copper=forecast_value,
    dollar=forecast_value,
    vol=st.floats(min_value=0.005, max_value=3.0, allow_nan=False),
    price=st.floats(min_value=1.0, max_value=400.0, allow_nan=False),
    vol_target=st.sampled_from([0.12, 0.25, 0.50]),
    max_leverage=st.sampled_from([1.0, 2.0, 5.0, 10.0]),
    weekend=st.sampled_from([None, 0.5, 1.0]),
    day=st.sampled_from([THURSDAY, FRIDAY]),
)
def test_no_decision_ever_targets_more_than_the_ceiling(
    vehicle, trend, accel, skew, carry, momentum, copper, dollar, vol, price, vol_target, max_leverage, weekend, day
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
    forecast = {"trend": trend, "accel": accel, "skew": skew, "carry": carry, "carry_momentum": momentum}
    forecast |= {"copper": copper, "dollar": dollar}
    dec, order = book.decide(TS, day, "X", price, forecast, vol)
    combined = dec.forecast["combined"]
    assert combined is None or abs(combined) <= 20.0
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
