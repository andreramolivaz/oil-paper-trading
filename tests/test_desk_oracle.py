"""The speculative Kalshi book (phase 14, ``engine/desk/oracle.py``): the de-escalation index read on the
prediction markets' archive, the week's change, the forecast, and the book that trades it on the micro future
beside the aggressive book - without ever touching the desk's forecast, the replay or the fingerprint.

The archive fixture is the runner's own: ``tests/fixtures/kalshi_log_2026-10.jsonl`` holds the two first days
of ``state/desk/kalshi-2026-10.jsonl`` as they were on the data branch on 2026-10-15 (28 records, 14 and 15
October, real prices). The numbers asserted below were worked out by hand from those records in
``docs/RESEARCH.md``, chapter 10 F.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from engine.core.calendar import add_business_days
from engine.core.config import RiskConfig
from engine.core.store import StateStore
from engine.desk import oracle
from engine.desk.book import Book, BookConfig
from engine.desk.data import held_contract
from engine.desk.engine import Desk
from engine.desk.live import build_desk, live_tick
from engine.desk.oracle import (
    COMPONENTS,
    IndexReading,
    forecast_on,
    horizon_probability,
    index_readings,
    lead_text,
    two_sided_mid,
)
from tests.synthetic import half_hour_bars, make_desk_data

FIXTURE = Path(__file__).parent / "fixtures" / "kalshi_log_2026-10.jsonl"
RISK = RiskConfig.load()
SPINTO = BookConfig(
    id="spinto", name="Aggressive", vehicle="MCL", vol_target=0.50, max_leverage=10.0, weekend_max_leverage=3.0
)
ORACLE = BookConfig(
    id="oracle", name="Oracle", vehicle="MCL", vol_target=0.50, max_leverage=10.0, weekend_max_leverage=3.0,
    role="candidate", forecast="kalshi", daily_loss_breaker=0.15,
)  # fmt: skip
DESK_LONG = {"trend": 15.0, "carry": 10.0, "carry_momentum": 10.0}
D14, D15 = date(2026, 10, 14), date(2026, 10, 15)


def _records() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------------------------- the ladder
def test_a_two_sided_quote_within_ten_cents_is_a_probability_and_nothing_else_is():
    assert two_sided_mid(0.36, 0.44) == pytest.approx(0.40)
    assert two_sided_mid(0.056, 0.063) == pytest.approx(0.0595)
    assert two_sided_mid(0.30, 0.41) is None  # eleven cents wide: an absence, not a probability
    assert two_sided_mid(0.0, 1.0) is None and two_sided_mid(None, 0.5) is None and two_sided_mid(0.5, None) is None
    assert two_sided_mid(0.6, 0.5) is None  # crossed
    # a bid of 0 is no bid and an ask of 1 no ask on this API (the fixture has dozens of each): one-sided is absent
    assert two_sided_mid(0.0, 0.10) is None and two_sided_mid(0.96, 1.0) is None and two_sided_mid(0.0, 0.0) is None
    assert two_sided_mid(0.01, 0.02) == pytest.approx(0.015) and two_sided_mid(0.9, 0.99) == pytest.approx(0.945)


def test_the_horizon_probability_interpolates_in_time_from_today_at_zero():
    asof = date(2026, 10, 15)
    ladder = [(date(2027, 1, 1), 0.06), (date(2027, 2, 1), 0.075), (date(2026, 11, 1), 0.015)]
    # 90 days from the 15th is the 13th of January: 12 of the 31 days between the two deadlines around it
    assert horizon_probability(ladder, asof, 90) == pytest.approx(0.06 + 0.015 * 12 / 31)
    # inside the first deadline the line starts at (today, 0): nothing has happened yet on an open market
    assert horizon_probability(ladder, asof, 17) == pytest.approx(0.015)
    assert horizon_probability(ladder, asof, 0) == 0.0
    # beyond the last deadline the last value holds; a deadline already passed is not read
    assert horizon_probability(ladder, asof, 400) == pytest.approx(0.075)
    assert horizon_probability([(date(2026, 10, 10), 0.9), *ladder], asof, 17) == pytest.approx(0.015)
    assert horizon_probability([(date(2026, 10, 10), 0.9)], asof) is None
    assert horizon_probability([], asof) is None
    # two markets on one date (two events of a series): the mean of what they quote
    assert horizon_probability([(date(2027, 1, 1), 0.1), (date(2027, 1, 1), 0.3)], asof, 78) == pytest.approx(0.2)


# --------------------------------------------------------------------------------------------- the archive
def test_the_index_on_the_runners_own_archive_matches_the_hand_calculation():
    readings = index_readings(_records())
    assert [r.day for r in readings] == [D14, D15]
    first, second = readings
    assert first.ts.startswith("2026-10-14T01:22") and second.ts.startswith("2026-10-15T00:48")
    assert set(first.components) == set(COMPONENTS) == set(second.components)
    # docs/RESEARCH.md chapter 10 F: the strait 6.6 % both days, the meeting 46.8 -> 45.6, the deal 11.0 -> 11.7
    assert first.components["KXHORMUZNORM"] == pytest.approx(0.066, abs=5e-4)
    assert first.components["KXUSIRANMEETING"] == pytest.approx(0.468, abs=5e-4)
    assert first.components["KXUSAIRANAGREEMENT"] == pytest.approx(0.110, abs=5e-4)
    assert second.components["KXHORMUZNORM"] == pytest.approx(0.0655, abs=5e-4)
    assert second.components["KXUSIRANMEETING"] == pytest.approx(0.456, abs=5e-4)
    assert second.components["KXUSAIRANAGREEMENT"] == pytest.approx(0.117, abs=5e-4)
    assert first.value == pytest.approx(0.2145, abs=5e-4) and second.value == pytest.approx(0.2130, abs=5e-4)
    # the strait is read on the markets quoted within ten cents: the deadlines of 2027 between January and
    # February bracket the horizon, the 2026 ones are inside it and the 2028-29 ones beyond it
    assert (
        index_readings([r for r in _records() if r["series"] == "KXBRENTD"]) == []
    )  # a price ladder is not a component


def test_a_series_with_two_open_events_on_one_day_counts_the_busier_one_and_junk_is_skipped():
    records = _records()
    meeting = next(r for r in records if r["series"] == "KXUSIRANMEETING" and r["day"] == "2026-10-15")
    twin = json.loads(json.dumps(meeting))
    twin["event"] = "KXUSIRANMEETING-99"
    cols = twin["columns"]
    for row in twin["markets"]:  # a second event with every quote at 0.90-0.95 and a tenth of the volume
        row[cols.index("yes_bid")], row[cols.index("yes_ask")] = 0.90, 0.95
        row[cols.index("volume")] = (row[cols.index("volume")] or 0.0) / 10.0
    with_twin = index_readings([*records, twin])
    assert with_twin[1].components["KXUSIRANMEETING"] == pytest.approx(0.456, abs=5e-4)  # the original, busier one
    loud = json.loads(json.dumps(twin))
    for row in loud["markets"]:
        row[cols.index("volume")] = 1e9
    assert index_readings([*records, loud])[1].components["KXUSIRANMEETING"] == pytest.approx(0.925, abs=1e-3)
    # a record without a day is skipped; a day whose markets cannot be read (a row shorter than its columns, no
    # date in the labels) is a reading with no component - a day with no view, never an error
    broken = [{"series": "KXHORMUZNORM", "day": "not a day"}, {**meeting, "markets": [["x"]], "title": "no date here"}]
    unreadable = index_readings(broken)
    assert len(unreadable) == 1 and unreadable[0].components == {} and unreadable[0].value is None


# --------------------------------------------------------------------------------------------- the forecast
def _reading(day: date, **components: float) -> IndexReading:
    return IndexReading(day=day, ts=f"{day.isoformat()}T00:20:00Z", components=dict(components))


BASE = {"KXHORMUZNORM": 0.066, "KXUSIRANMEETING": 0.456, "KXUSAIRANAGREEMENT": 0.117}


def test_the_forecast_is_minus_two_per_point_of_weekly_change_capped_with_a_deadband():
    ref = _reading(date(2026, 10, 14), **BASE)
    today = _reading(date(2026, 10, 21), **{k: v + 0.05 for k, v in BASE.items()})  # +5 points of de-escalation
    out = forecast_on([ref, today], date(2026, 10, 21))
    assert out.reading is today and out.reference is ref and out.common == tuple(COMPONENTS)
    assert out.change == pytest.approx(0.05) and out.forecast == pytest.approx(-10.0)  # a normal short
    assert "+5.0 points -> forecast -10.0" in out.note
    # escalation: long; a move of ten points saturates; half a point is inside the spread
    escal = _reading(date(2026, 10, 21), **{k: v - 0.025 for k, v in BASE.items()})
    assert forecast_on([ref, escal], date(2026, 10, 21)).forecast == pytest.approx(5.0)
    big = _reading(date(2026, 10, 21), **{k: v + 0.15 for k, v in BASE.items()})
    assert forecast_on([ref, big], date(2026, 10, 21)).forecast == -20.0
    small = _reading(date(2026, 10, 21), **{k: v + 0.005 for k, v in BASE.items()})
    flat = forecast_on([ref, small], date(2026, 10, 21))
    assert flat.forecast == 0.0 and "deadband" in flat.note
    # the forecast dict carries every input of the decision, as numbers, so that the rule can be replayed
    fc = out.as_forecast()
    assert fc["deescalation"] == pytest.approx(-10.0) and fc["kalshi_change"] == pytest.approx(0.05)
    assert fc["kalshi_index"] == pytest.approx(today.value) and fc["kalshi_components"] == 3.0
    assert fc["kalshi_age_days"] == 0.0 and fc["kalshi_ref_age_days"] == 7.0
    assert fc["kalshi:KXHORMUZNORM"] == pytest.approx(0.116) and fc["kalshi_ref:KXHORMUZNORM"] == pytest.approx(0.066)
    text = lead_text(fc)
    assert text.startswith("Kalshi, within 90 days: the strait back to normal 11.6 % (a week ago 6.6 %)")
    assert "+5.0 points in a week: forecast -10.0 out of 20." in text
    doc = out.to_dict()
    assert (
        doc["change"] == pytest.approx(0.05)
        and doc["reading_day"] == "2026-10-21"
        and doc["reference_day"] == "2026-10-14"
    )
    assert doc["index_reference"] == pytest.approx(ref.value, abs=1e-4) and doc["rule"]["forecast_per_point"] == 2.0
    assert doc["index_common"] == pytest.approx(doc["index_reference"] + doc["change"], abs=1e-4)
    assert doc["components_common"] == 3 and doc["components_read"] == 3
    assert [c["series"] for c in doc["components"]] == list(COMPONENTS)


def test_without_a_reading_a_reference_or_a_recent_reading_the_book_waits_or_goes_flat():
    readings = index_readings(_records())  # the 14th and the 15th of October: a two-day archive
    nothing = forecast_on([], D15)
    assert nothing.forecast is None and nothing.reading is None and "cannot decide" in nothing.note
    # the first week: a reading but no reference -> no decision (the book holds what it has: nothing)
    waits = forecast_on(readings, D15)
    assert waits.forecast is None and waits.reading is not None and waits.reference is None
    assert "waits for a week of archive" in waits.note
    assert waits.as_forecast()["deescalation"] is None and waits.as_forecast()["kalshi_index"] == pytest.approx(
        0.213, abs=5e-4
    )
    # the 21st reads the 14th as its reference, but the newest reading is six days old: no view, flat
    stale = forecast_on(readings, date(2026, 10, 21))
    assert stale.forecast == 0.0 and "6 days old" in stale.note
    # the 22nd would take the 15th (7 days) over the 14th (8 days) as reference: the newest within the window
    fresh = _reading(date(2026, 10, 22), **BASE)
    out = forecast_on([*readings, fresh], date(2026, 10, 22))
    assert out.reference is readings[1] and out.reading is fresh
    # a reading two days old is still today's view (the weekend)
    two_days = forecast_on([*readings, _reading(date(2026, 10, 20), **BASE)], date(2026, 10, 22))
    assert two_days.reading.day == date(2026, 10, 20) and two_days.forecast is not None
    # the reference window: 7 to 10 days before, nothing older
    old = _reading(date(2026, 10, 11), **BASE)
    assert forecast_on([old, fresh], date(2026, 10, 22)).reference is None  # 11 days: too old
    assert forecast_on([_reading(date(2026, 10, 12), **BASE), fresh], date(2026, 10, 22)).reference is not None


def test_a_ladder_that_appears_or_resolves_is_not_a_move():
    ref = _reading(date(2026, 10, 14), KXHORMUZNORM=0.066, KXUSAIRANAGREEMENT=0.117)  # no meeting market a week ago
    today = _reading(date(2026, 10, 21), KXHORMUZNORM=0.066, KXUSAIRANAGREEMENT=0.117, KXUSIRANMEETING=0.80)
    out = forecast_on([ref, today], date(2026, 10, 21))
    assert out.common == ("KXHORMUZNORM", "KXUSAIRANAGREEMENT") and out.change == 0.0 and out.forecast == 0.0
    assert "2 of 3 components in both" in out.note
    fc = out.as_forecast()
    assert fc["kalshi_components"] == 2.0 and "kalshi_ref:KXUSIRANMEETING" not in fc
    assert fc["kalshi:KXUSIRANMEETING"] == 0.80  # today's reading is still reported whole
    # the index shown is over what is present today; the change only over what both readings have
    assert fc["kalshi_index"] == pytest.approx((0.066 + 0.117 + 0.80) / 3)
    gone = forecast_on([ref, _reading(date(2026, 10, 21), KXUSIRANMEETING=0.5)], date(2026, 10, 21))
    assert gone.forecast == 0.0 and "no component present in both" in gone.note
    # a day whose markets could not be read at all is not a reading: the newest readable one within three days
    # is today's view, and in the reference window the newest readable one is the reference (found by review:
    # an empty day closed a leveraged position and reopened it the next day)
    start = date(2026, 10, 14)
    ramp = [_reading(start + timedelta(days=i), **{k: v + 0.01 * i for k, v in BASE.items()}) for i in range(15)]
    empty_27 = [r if r.day != date(2026, 10, 27) else IndexReading(r.day, r.ts, {}) for r in ramp]
    normal = forecast_on(ramp, date(2026, 10, 27))
    assert normal.forecast == pytest.approx(-14.0)
    fallback = forecast_on(empty_27, date(2026, 10, 27))  # the 26th against the 20th: six points, not seven
    assert fallback.reading.day == date(2026, 10, 26) and fallback.forecast == pytest.approx(-12.0)
    empty_21 = [r if r.day != date(2026, 10, 21) else IndexReading(r.day, r.ts, {}) for r in ramp]
    assert forecast_on(empty_21, date(2026, 10, 28)).reference.day == date(2026, 10, 20)
    only_empty = [IndexReading(date(2026, 10, 27), "", {})]
    assert forecast_on(only_empty, date(2026, 10, 27)).reading is None


# --------------------------------------------------------------------------------------------- the book
def test_the_oracle_book_trades_the_kalshi_sleeve_as_it_is_and_says_what_it_read():
    book = Book(ORACLE, RISK)
    ts = datetime(2026, 10, 21, 18, 48, tzinfo=UTC)
    ref = _reading(date(2026, 10, 14), **BASE)
    today = _reading(date(2026, 10, 21), **{k: v + 0.10 for k, v in BASE.items()})
    fc = forecast_on([ref, today], date(2026, 10, 21)).as_forecast()
    assert fc["deescalation"] == -20.0
    dec, order = book.decide(ts, date(2026, 10, 21), "CLZ26", 91.0, fc, 0.44)
    # exposure 2 x 0.50 / 0.44 = 2.27x of 10 000 $ at 91 $ a barrel: 2.5 lots wanted, two whole lots short
    assert dec.forecast["combined"] == -20.0 and dec.exposure_raw == pytest.approx(-2.2727, abs=1e-3)
    assert order is not None and order.qty_bbl == -200.0 and dec.target_units == -200.0
    assert dec.rationale.startswith("Kalshi, within 90 days: the strait back to normal 16.6 % (a week ago 6.6 %)")
    assert "forecast -20.0 out of 20. Volatility 44% against a target of 50%: exposure 2.27x" in dec.rationale
    assert "Sell 200 barrels of CLZ26: target 200 (short" in dec.rationale
    assert order.strategies_for == ["deescalation"] and order.strategies_against == []  # the sleeve wants the short
    # the sleeve is read as it is: no source average, no diversification multiplier
    assert (
        book.combined_forecast({"deescalation": 7.5}) == 7.5 and book.combined_forecast({"deescalation": None}) is None
    )
    assert book.combined_forecast({"deescalation": 35.0}) == 20.0 and book.combined_forecast({}) is None
    assert not book.replayable and book.sleeve_names == ("deescalation",)
    # the desk's forecast means nothing to it, and a book without a forecast holds what it has
    dec2, order2 = book.decide(ts, date(2026, 10, 22), "CLZ26", 91.0, dict(DESK_LONG), 0.44)
    assert order2 is None and dec2.limited_by == "missing data" and dec2.forecast["combined"] is None
    # flat on purpose (no view) is a forecast of zero, not missing data: the position is closed
    dec3, _ = book.decide(ts, date(2026, 10, 22), "CLZ26", 91.0, {"deescalation": 0.0, "kalshi_index": 0.21}, 0.44)
    assert dec3.exposure == 0.0 and dec3.limited_by == "no forecast" and "No position" in dec3.rationale


def test_on_one_desk_the_oracle_reads_its_own_forecast_and_the_aggressive_book_the_desks(tmp_path):
    desk = Desk([Book(SPINTO, RISK), Book(ORACLE, RISK)])
    ts = datetime(2026, 10, 21, 18, 48, tzinfo=UTC)
    own = {"oracle": {"deescalation": -20.0, "kalshi_index": 0.31, "kalshi_change": 0.1}}
    decided = desk.decide("MCL", ts, date(2026, 10, 21), "CLZ26", 91.0, dict(DESK_LONG), 0.30, book_forecasts=own)
    by_book = {d.book: d for d in decided}
    assert by_book["spinto"].forecast["combined"] > 0 and by_book["spinto"].rationale.startswith("Price ")
    assert by_book["oracle"].forecast["combined"] == -20.0 and by_book["oracle"].target_units < 0
    assert "trend" not in by_book["oracle"].forecast and "deescalation" not in by_book["spinto"].forecast
    # an entry for a book on the desk's forecast is ignored: those books read the desk's and nothing else
    desk2 = Desk([Book(SPINTO, RISK), Book(ORACLE, RISK)])
    swapped = {"spinto": {"deescalation": -20.0}}  # nonsense for it
    decided2 = desk2.decide("MCL", ts, date(2026, 10, 21), "CLZ26", 91.0, dict(DESK_LONG), 0.30, book_forecasts=swapped)
    by_book2 = {d.book: d for d in decided2}
    assert by_book2["spinto"].forecast["combined"] == by_book["spinto"].forecast["combined"]
    assert by_book2["oracle"].limited_by == "missing data" and by_book2["oracle"].n_orders == 0  # no entry: holds


def _archive_for(day: date, store: StateStore, bump: float = 0.0) -> None:
    """Write the fixture's two readings as the reference (a week before ``day``) and today's reading, the
    strait's quotes lifted by ``bump`` today."""
    for rec in _records():
        shifted = json.loads(json.dumps(rec))
        when = day - timedelta(days=7) if rec["day"] == "2026-10-14" else day
        shifted["day"] = when.isoformat()
        shifted["ts"] = f"{when.isoformat()}T00:20:00Z"
        shifted["key"] = f"{when.isoformat()}|{rec['event']}"
        if when == day and rec["series"] == "KXHORMUZNORM" and bump:
            cols = shifted["columns"]
            for row in shifted["markets"]:
                for name in ("yes_bid", "yes_ask"):
                    row[cols.index(name)] = min(1.0, row[cols.index(name)] + bump)
        store.append_jsonl(oracle.KALSHI_LOG, shifted, ts=datetime(when.year, when.month, when.day, 0, 20, tzinfo=UTC))


def test_on_the_live_path_the_oracle_decides_on_the_archive_and_the_other_books_do_not_notice(tmp_path):
    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    day = data.series["MCL"].bars.index[-1].date()
    symbol = held_contract("CL", add_business_days(day, 1, "US"))
    price = float(data.series["MCL"].bars["close"].iloc[-1])
    data.intraday["MCL"] = half_hour_bars(day, price, n=14, first=(13, 30), step=0.0).reset_index().assign(code=symbol)
    data.legs = {}
    now = datetime(day.year, day.month, day.day, 18, 48, tzinfo=UTC)  # 14:48 New York: the future's decision is due

    # 1. the archive is a week long and the strait's quotes jumped by 30 cents: de-escalation, the oracle is short
    desk = build_desk(tmp_path / "a", RISK, [SPINTO, ORACLE])
    _archive_for(day, desk.store, bump=0.30)
    report = live_tick(desk, data, now)
    by_book = {d["book"]: d for d in report.decisions}
    assert set(by_book) == {"spinto", "oracle"}
    assert by_book["oracle"]["forecast"]["combined"] == -20.0 and by_book["oracle"]["target_units"] < 0
    assert by_book["oracle"]["rationale"].startswith("Kalshi, within 90 days")
    assert (
        by_book["oracle"]["forecast"]["kalshi_age_days"] == 0.0
        and by_book["oracle"]["forecast"]["kalshi_ref_age_days"] == 7.0
    )
    assert any(n.startswith("oracle: de-escalation index") for n in report.notes)
    assert "deescalation" not in by_book["spinto"]["forecast"] and by_book["spinto"]["rationale"].startswith("Price ")
    # the same desk without the oracle takes exactly the same decision for the aggressive book
    alone = build_desk(tmp_path / "b", RISK, [SPINTO])
    only = live_tick(alone, data, now).decisions
    assert len(only) == 1 and only[0]["forecast"] == by_book["spinto"]["forecast"]
    assert only[0]["target_units"] == by_book["spinto"]["target_units"]

    # 2. a two-day archive: the oracle waits, with the reason in the notes, and takes no position
    desk2 = build_desk(tmp_path / "c", RISK, [SPINTO, ORACLE])
    for rec in _records():
        shifted = json.loads(json.dumps(rec))
        when = day - timedelta(days=1) if rec["day"] == "2026-10-14" else day
        shifted["day"], shifted["ts"] = when.isoformat(), f"{when.isoformat()}T00:20:00Z"
        desk2.store.append_jsonl(oracle.KALSHI_LOG, shifted, ts=now)
    report2 = live_tick(desk2, data, now)
    waiting = next(d for d in report2.decisions if d["book"] == "oracle")
    assert waiting["limited_by"] == "missing data" and waiting["n_orders"] == 0
    assert any("waits for a week of archive" in n for n in report2.notes)

    # 3. a line cut short in the archive (the runner cancelled while appending) is skipped, not fatal: the
    #    oracle decides on the lines that are whole, exactly as in 1, and the other book never notices
    desk3 = build_desk(tmp_path / "d", RISK, [SPINTO, ORACLE])
    path = desk3.store.jsonl_path(oracle.KALSHI_LOG, ts=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"key": "cut short", "day": "2026-10-1\n', encoding="utf-8")
    _archive_for(day, desk3.store, bump=0.30)
    report3 = live_tick(desk3, data, now)
    books3 = {d["book"]: d for d in report3.decisions}
    assert books3["spinto"]["target_units"] == by_book["spinto"]["target_units"]
    assert books3["oracle"]["target_units"] == by_book["oracle"]["target_units"] and report3.failed == []
    # and an archive made only of a bad line is no archive: no reading, the book cannot decide and holds
    desk4 = build_desk(tmp_path / "e", RISK, [SPINTO, ORACLE])
    path = desk4.store.jsonl_path(oracle.KALSHI_LOG, ts=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json\n", encoding="utf-8")
    report4 = live_tick(desk4, data, now)
    books4 = {d["book"]: d for d in report4.decisions}
    assert books4["oracle"]["limited_by"] == "missing data" and books4["oracle"]["n_orders"] == 0
    assert any("no Kalshi reading in the archive" in n for n in report4.notes) and report4.failed == []
    # a forecast that raises (anything but a bad line) costs the oracle its decision and nothing else
    desk5 = build_desk(tmp_path / "f", RISK, [SPINTO, ORACLE])
    _archive_for(day, desk5.store, bump=0.30)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(oracle, "archive_readings", lambda store: (_ for _ in ()).throw(RuntimeError("boom")))
        report5 = live_tick(desk5, data, now)
    books5 = {d["book"]: d for d in report5.decisions}
    assert books5["spinto"]["target_units"] == by_book["spinto"]["target_units"]
    assert books5["oracle"]["limited_by"] == "missing data"
    assert any("Kalshi forecast failed (RuntimeError: boom)" in n for n in report5.notes) and report5.failed == []


# --------------------------------------------------------------------------------------------- the replay
def test_the_replay_the_fingerprint_and_the_lifecycle_leave_the_oracle_out():
    from engine.desk.allocator import ROLE_CANDIDATE, ROLE_CORE, AllocatorConfig, LiveRecord, lifecycle_verdict
    from engine.desk.backtest import run_backtest
    from engine.desk.report import model_signature, replayed_books

    data = make_desk_data(n_days=400, start=date(2024, 1, 2))
    both = run_backtest(data, [SPINTO, ORACLE], RISK, ruin=False)
    alone = run_backtest(data, [SPINTO], RISK, ruin=False)
    assert set(both.books) == {"spinto"} and "oracle" not in both.books
    assert both.books["spinto"].equity.equals(alone.books["spinto"].equity)  # byte for byte the same replay
    assert replayed_books([SPINTO, ORACLE]) == [SPINTO]
    assert model_signature([SPINTO, ORACLE]) == model_signature([SPINTO])
    assert model_signature([SPINTO, dataclasses.replace(ORACLE, vol_target=0.2)]) == model_signature([SPINTO])
    # a book without a backtest drawdown to compare with is never promoted, however good its live record
    good = LiveRecord(days=270, sharpe=1.5, max_drawdown=-0.05, start=date(2025, 10, 8), end=date(2026, 10, 13))
    cfg = AllocatorConfig()
    assert lifecycle_verdict(ROLE_CANDIDATE, good, None, cfg) == ROLE_CANDIDATE
    assert lifecycle_verdict(ROLE_CANDIDATE, good, -0.25, cfg) == ROLE_CORE


def test_the_rule_on_file_is_the_registered_one():
    rule = oracle.describe()
    assert (oracle.HORIZON_DAYS, oracle.LOOKBACK_DAYS, oracle.REFERENCE_WINDOW_DAYS, oracle.STALE_DAYS) == (90, 7, 3, 3)
    assert (oracle.MAX_SPREAD, oracle.DEADBAND, oracle.FORECAST_PER_POINT, oracle.FORECAST_CAP) == (
        0.10,
        0.01,
        2.0,
        20.0,
    )
    assert list(COMPONENTS) == ["KXHORMUZNORM", "KXUSIRANMEETING", "KXUSAIRANAGREEMENT"]
    assert [c["series"] for c in rule["components"]] == list(COMPONENTS)
    assert "No backtest" in rule["text"] and "never promoted" in rule["text"]
    assert oracle.SLEEVES == ("deescalation",) and oracle.DEFAULT_WEIGHTS == {"deescalation": 1.0}
