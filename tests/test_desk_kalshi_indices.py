"""The Kalshi indices on the other markets (phase 15, ``engine/desk/oracle.py``, docs/RESEARCH.md chapter 11): the
Fed path read off the FOMC meetings' outcome markets, the recession probability read off the yearly NBER markets,
a book that reads several sleeves with a sign, and the two books that start (equities, utilities) beside the oil
book - without touching the desk's forecast, the replay or the fingerprint.

The fixture is the runner's own download of 2026-10-15 23:40 UTC (``research-data``, ``kalshi-history``),
trimmed to the FOMC meetings of September 2026 to January 2027 and the two yearly recession markets, candles
from 1 June 2026 (``tests/fixtures/kalshi_history_2026-10-15_macro.json``). The numbers asserted below are worked
out by hand from the candles in the test itself.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from engine.core.config import RiskConfig
from engine.desk import oracle
from engine.desk.backfill import apply_backfill, records_from_history
from engine.desk.book import Book, BookConfig, load_books
from engine.desk.live import TickReport, build_desk, desk_store, oracle_forecasts
from engine.desk.report import replayed_books

FIXTURE = Path(__file__).parent / "fixtures" / "kalshi_history_2026-10-15_macro.json"
RISK = RiskConfig.load()
D15 = date(2026, 10, 15)
EQUITIES = BookConfig(
    id="oracle_equities", name="Oracle equities", vehicle="SPY", vol_target=0.50, max_leverage=2.0,
    weekend_max_leverage=1.0, long_only=True, role="candidate", forecast="kalshi",
    sleeves={"fedpath": 1.0, "recession": -1.0}, daily_loss_breaker=0.15,
)  # fmt: skip
UTILITIES = BookConfig(
    id="oracle_utilities", name="Oracle utilities", vehicle="XLU", vol_target=0.50, max_leverage=2.0,
    weekend_max_leverage=1.0, long_only=True, role="candidate", forecast="kalshi", sleeves={"fedpath": 1.0},
    daily_loss_breaker=0.15,
)  # fmt: skip


def history() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return records_from_history(history())


def _markets(records: list[dict], day: str, event: str) -> list[dict]:
    rec = next(r for r in records if r["day"] == day and r["event"] == event)
    return [dict(zip(rec["columns"], row, strict=False)) for row in rec["markets"]]


# ------------------------------------------------------------------------------------------- the Fed path
def test_a_meetings_expected_change_is_the_odds_weighted_outcome_over_the_two_sided_quotes(records):
    oct_ = _markets(records, "2026-10-15", "KXFEDDECISION-26OCT")
    quotes = {m["ticker"].rsplit("-", 1)[-1]: (m["yes_bid"], m["yes_ask"]) for m in oct_}
    # the candle of the evening before the 15th: hold 0.75/0.76, hike 25 at 0.24/0.25, the tails at 0.00/0.01
    assert quotes["H0"] == (0.75, 0.76) and quotes["H25"] == (0.24, 0.25) and quotes["C25"] == (0.0, 0.01)
    # the tails have no bid: they are absences, not probabilities; the rest is normalised to one
    assert oracle.meeting_expectation(oct_) == pytest.approx(0.245 * 25 / (0.755 + 0.245))
    dec = _markets(records, "2026-10-15", "KXFEDDECISION-26DEC")
    q = {m["ticker"].rsplit("-", 1)[-1]: (m["yes_bid"] + m["yes_ask"]) / 2 for m in dec if m["yes_bid"] > 0}
    assert set(q) == {"C25", "H0", "H25", "H26"}  # cut >25 at 0.00/0.01 is not quoted
    expected = (q["C25"] * -25 + q["H25"] * 25 + q["H26"] * 50) / sum(q.values())
    assert oracle.meeting_expectation(dec) == pytest.approx(expected) and expected == pytest.approx(20.98, abs=0.01)
    # a meeting is not read with one outcome quoted, or with half its distribution missing
    assert oracle.meeting_expectation([m for m in oct_ if m["ticker"].endswith("H0")]) is None
    thin = [{**m, "yes_bid": 0.30, "yes_ask": 0.31} if m["ticker"].endswith("H0") else m for m in oct_]
    assert oracle.meeting_expectation(thin) is None  # 0.305 + 0.245 = 0.55 of mass: unreadable
    # an unknown outcome code is ignored, "more than 25" counts as 50
    assert oracle.MEETING_OUTCOMES_BP["H26"] == 50.0 and oracle.MEETING_OUTCOMES_BP["C26"] == -50.0
    assert oracle.meeting_expectation([{**m, "ticker": "KXFEDDECISION-26OCT-X9"} for m in oct_]) is None


def test_the_fed_path_sums_the_meetings_within_ninety_days_and_a_meeting_day_drops_out(records):
    readings = {r.day: r for r in oracle.index_readings(records, oracle.FEDPATH)}
    # 18 June: the 16 September meeting is 90 days away, the first day it is in the window; October is not yet
    assert list(readings[date(2026, 6, 18)].components) == ["KXFEDDECISION-26SEP"]
    assert date(2026, 6, 17) in readings and readings[date(2026, 6, 17)].components == {}
    # 30 July: September and October (28 October is 90 days away)
    assert list(readings[date(2026, 7, 30)].components) == ["KXFEDDECISION-26SEP", "KXFEDDECISION-26OCT"]
    assert readings[date(2026, 7, 30)].dates["KXFEDDECISION-26OCT"] == date(2026, 10, 28)
    # the day of a meeting it is still within the horizon (the reading is the evening before, the markets are
    # open until the decision); the day after, it has no market
    sept16 = readings[date(2026, 9, 16)]
    assert list(sept16.components) == ["KXFEDDECISION-26SEP", "KXFEDDECISION-26OCT", "KXFEDDECISION-26DEC"]
    assert list(readings[date(2026, 9, 17)].components) == ["KXFEDDECISION-26OCT", "KXFEDDECISION-26DEC"]
    # 15 October: the sum of the two meetings read above, in basis points, by date
    r15 = readings[D15]
    assert list(r15.components) == ["KXFEDDECISION-26OCT", "KXFEDDECISION-26DEC"]
    assert r15.components["KXFEDDECISION-26OCT"] == pytest.approx(6.125)
    assert r15.value == pytest.approx(6.125 + 20.98, abs=0.01) and r15.spec is oracle.FEDPATH
    # the January 2027 meeting (27 January: 104 days) is in the fixture and out of the window
    assert "KXFEDDECISION-27JAN" not in r15.components


def test_the_fed_path_sleeve_is_minus_two_and_a_half_per_basis_point_over_the_common_meetings(records):
    readings = oracle.index_readings(records, oracle.FEDPATH)
    out = oracle.forecast_on(readings, D15, oracle.FEDPATH)
    assert out.reading.day == D15 and out.reference.day == date(2026, 10, 8)
    assert out.common == ("KXFEDDECISION-26OCT", "KXFEDDECISION-26DEC")
    change = sum(out.reading.components[c] - out.reference.components[c] for c in out.common)
    assert out.change == pytest.approx(change) and change == pytest.approx(5.35, abs=0.01)  # hawkish repricing
    assert out.forecast == pytest.approx(-2.5 * change) and "-2.5 per bp" in out.note and "+5.3 bp" in out.note
    fc = out.as_forecast()
    assert fc["fedpath"] == out.forecast and fc["fedpath:index"] == pytest.approx(out.reading.value)
    assert fc["fedpath:c:KXFEDDECISION-26OCT"] == pytest.approx(6.125) and fc["fedpath:common"] == 2.0
    assert fc["fedpath:r:KXFEDDECISION-26DEC"] == pytest.approx(out.reference.components["KXFEDDECISION-26DEC"])
    # the day after the September meeting: the reference (10 September) had three meetings, today has two, and
    # the change is over the two both have - the meeting's own surprise is not in the signal, by the rule
    after = oracle.forecast_on(readings, date(2026, 9, 17), oracle.FEDPATH)
    assert "KXFEDDECISION-26SEP" in after.reference.components and after.common == tuple(after.reading.components)
    assert after.forecast == -20.0 and "capped" in after.note  # +13 basis points in a week: more than the cap
    # the deadband is one basis point; the words name the meetings by their date
    text = oracle.lead_text(fc)
    assert text.startswith("Kalshi, the meetings within 90 days: the FOMC meeting of Oct 2026 +6 bp (a week ago +4 bp)")
    assert "Fed path +27 bp, +5.3 bp in a week: sleeve -13.4 out of 20." in text
    doc = out.to_dict()
    assert doc["id"] == "fedpath" and doc["unit"] == "bp" and doc["components"][0]["label"].endswith("28 Oct 2026")
    assert doc["index_common"] == pytest.approx(doc["index"]) and doc["rule"]["aggregate"] == "sum"


# ------------------------------------------------------------------------------------------- recession
def test_the_recession_probability_is_the_yearly_markets_as_one_ladder_at_a_year(records):
    assert oracle.year_of("KXRECSSNBER-26") == 2026 and oracle.year_of("KXRECSSNBER-2027") == 2027
    assert oracle.year_of("KXSOMETHING", "Will there be a recession in 2028?") == 2028 and oracle.year_of("X") is None
    # the ladder: P(by end 2026) = p26, P(by end 2027) = 1 - (1 - p26)(1 - p27); a past year is dropped
    ladder = oracle.year_ladder({2025: 0.9, 2026: 0.045, 2027: 0.195}, D15)
    assert ladder == [
        (date(2026, 12, 31), pytest.approx(0.045)),
        (date(2027, 12, 31), pytest.approx(1 - 0.955 * 0.805)),
    ]
    readings = {r.day: r for r in oracle.index_readings(records, oracle.RECESSION)}
    r15 = readings[D15]
    m26 = _markets(records, "2026-10-15", "KXRECSSNBER-26")[0]
    m27 = _markets(records, "2026-10-15", "KXRECSSNBER-27")[0]
    assert (m26["yes_bid"], m26["yes_ask"], m27["yes_bid"], m27["yes_ask"]) == (0.04, 0.05, 0.19, 0.20)
    p26, p27 = 0.045, 0.195
    by_2027 = 1 - (1 - p26) * (1 - p27)
    # 15 October 2027 lies 288 of the 365 days between the two year ends
    expected = p26 + (by_2027 - p26) * (date(2027, 10, 15) - date(2026, 12, 31)).days / 365
    assert r15.components == {"KXRECSSNBER": pytest.approx(expected)} and expected == pytest.approx(0.1919, abs=5e-4)
    out = oracle.forecast_on(list(readings.values()), D15, oracle.RECESSION)
    assert out.reference.day == date(2026, 10, 8) and out.reference.years == {"KXRECSSNBER": {2026: 0.045, 2027: 0.185}}
    # the change is read on the two years both readings quote, both ladders at today's date: only the 2027
    # quote moved (0.185 -> 0.195), and a week of the horizon sliding towards the end of 2027 is not a move
    by_2027_before = 1 - (1 - p26) * (1 - 0.185)
    expected_change = (by_2027 - by_2027_before) * (date(2027, 10, 15) - date(2026, 12, 31)).days / 365
    assert out.change == pytest.approx(expected_change) and expected_change == pytest.approx(0.0075, abs=2e-4)
    assert out.today == {"KXRECSSNBER": pytest.approx(expected)} and out.before["KXRECSSNBER"] < expected
    assert out.forecast == 0.0 and "deadband" in out.note  # three quarters of a point: no view
    fc = out.as_forecast()
    assert fc["recession:r:KXRECSSNBER"] == pytest.approx(out.before["KXRECSSNBER"])
    assert out.to_dict()["index_reference"] == pytest.approx(out.before["KXRECSSNBER"], abs=1e-4)
    assert oracle.RECESSION.horizon_days == 365 and oracle.RECESSION.deadband == 0.01


def test_a_year_market_that_lists_settles_or_ends_is_not_a_move():
    def reading(day: date, years: dict[int, float]) -> oracle.IndexReading:
        p = oracle.horizon_probability(oracle.year_ladder(years, day), day, 365)
        return oracle.IndexReading(day, "", {"KXRECSSNBER": p}, {}, oracle.RECESSION, {"KXRECSSNBER": years})

    quotes = {2026: 0.045, 2027: 0.195}
    # the turn of the year: the 2026 market's end passes and the index level falls with every quote unchanged
    before, after = reading(date(2026, 12, 30), quotes), reading(date(2027, 1, 6), quotes)
    assert after.value < before.value - 0.03
    out = oracle.forecast_on([before, after], date(2027, 1, 6), oracle.RECESSION)
    assert out.change == 0.0 and out.forecast == 0.0
    # a new year's market listing: the 2028 market appears, the others unchanged
    listed = reading(date(2027, 1, 13), {**quotes, 2028: 0.30})
    out2 = oracle.forecast_on([after, listed], date(2027, 1, 13), oracle.RECESSION)
    assert listed.value > after.value + 0.005 and out2.change == 0.0
    # a quote moving is a move: 2027 from 0.195 to 0.245
    moved = reading(date(2027, 1, 13), {**quotes, 2027: 0.245})
    out3 = oracle.forecast_on([after, moved], date(2027, 1, 13), oracle.RECESSION)
    # 2026 is over, so the ladder is the 2027 market alone and a year ahead lies beyond it: the last value
    assert out3.change == pytest.approx(0.245 - 0.195, abs=1e-6)
    assert out3.forecast == pytest.approx(4.0 * out3.change * 100)
    # a reading that quotes only a year the reference does not: no common year, no measurable move
    only_2028 = reading(date(2027, 1, 13), {2028: 0.30})
    assert oracle.forecast_on([after, only_2028], date(2027, 1, 13), oracle.RECESSION).forecast == 0.0


def test_a_near_certain_outcome_with_no_ask_is_read_at_its_bid():
    def market(code: str, bid: float, ask: float) -> dict:
        return {"ticker": f"KXFEDDECISION-26OCT-{code}", "yes_bid": bid, "yes_ask": ask}

    # the week before a meeting everybody expects to hold: 0.99 bid, nobody offers at 1.00, the rest at 0/0.01
    certain = [market("H0", 0.99, 1.0), market("H25", 0.0, 0.01), market("C25", 0.0, 0.01)]
    assert oracle.two_sided_mid(0.99, 1.0) is None  # the general rule: ask 1 is no ask
    assert oracle.meeting_expectation([*certain, market("H26", 0.0, 0.01)]) is None  # one outcome: not enough
    assert oracle.meeting_expectation([market("H0", 0.99, 1.0), market("H25", 0.01, 0.02)]) == pytest.approx(
        0.015 * 25 / (0.99 + 0.015)
    )
    assert oracle.meeting_expectation([market("H0", 0.90, 1.0), market("H25", 0.01, 0.02)]) is None  # under 0.95


# ------------------------------------------------------------------------------------------- the books
def test_a_book_averages_its_sleeves_with_its_signs_and_stays_in_cash_on_a_negative_mean():
    book = Book(EQUITIES, RISK)
    assert not book.replayable and book.cfg.sleeves == {"fedpath": 1.0, "recession": -1.0}
    # dovish Fed (+10) and rising recession odds (+6, read with a minus): (10 - 6) / 2
    assert book.combined_forecast({"fedpath": 10.0, "recession": 6.0}) == pytest.approx(2.0)
    # one sleeve without a value: the other alone; none: no forecast (the book holds what it has)
    assert book.combined_forecast({"fedpath": None, "recession": 6.0}) == pytest.approx(-6.0)
    assert book.combined_forecast({"fedpath": None, "recession": None}) is None and book.combined_forecast({}) is None
    assert book.combined_forecast({"fedpath": 20.0, "recession": -20.0}) == 20.0  # the mean is within the cap
    ts = datetime(2026, 10, 21, 19, 18, tzinfo=UTC)
    fc = {"fedpath": 16.0, "fedpath:index": 27.0, "fedpath:change": -6.4, "recession": -4.0, "recession:change": -0.01}
    dec, order = book.decide(ts, date(2026, 10, 21), "SPY", 670.0, fc, 0.15)
    # (16 + 4) / 2 = 10: a normal conviction; 10/10 x 0.50 / 0.15 = 3.3x wanted, the fund's margin caps at 2x
    assert dec.forecast["combined"] == pytest.approx(10.0) and dec.exposure == pytest.approx(2.0)
    assert dec.limited_by == "the book's leverage ceiling" and order is not None and order.qty_bbl > 0
    assert (
        "Forecast +10.0 out of 20: the mean of the sleeves with a value today, with the book's signs "
        "(fedpath +, recession -)." in dec.rationale
    )
    # one sleeve without a value: the words say which (found in review)
    dec_one, _ = book.decide(ts, date(2026, 10, 21), "SPY", 670.0, {"fedpath": 16.0, "recession": None}, 0.15)
    assert "with the book's signs (fedpath +); without a value: recession." in dec_one.rationale
    assert dec.rationale.startswith("Kalshi, the meetings within 90 days")
    assert order.strategies_for == ["fedpath", "recession"]  # both sleeves, with their signs, want the long
    # a negative mean: a long-only book is in cash, and says so
    dec2, _ = book.decide(ts, date(2026, 10, 22), "SPY", 670.0, {"fedpath": -8.0, "recession": 4.0}, 0.15)
    assert dec2.exposure == 0.0 and "does not go short" in dec2.rationale
    # the utilities book reads one sleeve as it is
    util = Book(UTILITIES, RISK)
    assert util.combined_forecast({"fedpath": 7.5}) == 7.5 and util.combined_forecast({"recession": 7.5}) is None


def test_on_the_live_path_each_sleeve_is_read_once_and_every_book_gets_its_own(tmp_path, records):
    desk = build_desk(tmp_path, RISK, [EQUITIES, UTILITIES])
    apply_backfill(desk_store(tmp_path), records)
    report = TickReport(datetime(2026, 10, 15, 19, 18, tzinfo=UTC))
    own = oracle_forecasts(desk, list(desk.books.values()), D15, report)
    assert set(own) == {"oracle_equities", "oracle_utilities"}
    assert own["oracle_equities"]["fedpath"] == pytest.approx(-13.37, abs=0.01)
    assert own["oracle_equities"]["recession"] == 0.0  # three quarters of a point: inside the deadband
    assert own["oracle_equities"]["recession:change"] == pytest.approx(0.0075, abs=2e-4)
    assert (
        "recession" not in own["oracle_utilities"]
        and own["oracle_utilities"]["fedpath"] == own["oracle_equities"]["fedpath"]
    )
    notes = [n for n in report.notes if n.startswith("oracle_")]
    assert (
        len(notes) == 3
        and sum("Fed path" in n for n in notes) == 2
        and sum("recession probability" in n for n in notes) == 1
    )
    # a day before the archive has a reference: the sleeves have no value and the books get nothing
    early = oracle_forecasts(
        desk, list(desk.books.values()), date(2026, 6, 20), TickReport(datetime(2026, 6, 20, tzinfo=UTC))
    )
    assert early["oracle_equities"]["fedpath"] is None and early["oracle_equities"]["recession"] is not None


def test_the_books_on_file_are_the_registered_ones_and_stay_out_of_the_replay():
    books = {b.id: b for b in load_books()}
    eq, ut = books["oracle_equities"], books["oracle_utilities"]
    assert (eq.vehicle, eq.sleeves, eq.long_only, eq.role, eq.forecast) == (
        "SPY", {"fedpath": 1.0, "recession": -1.0}, True, "candidate", "kalshi",
    )  # fmt: skip
    assert (ut.vehicle, ut.sleeves, ut.long_only) == ("XLU", {"fedpath": 1.0}, True)
    for b in (eq, ut):
        assert (b.vol_target, b.max_leverage, b.weekend_max_leverage, b.daily_loss_breaker) == (0.50, 2.0, 1.0, 0.15)
    # bonds and gold measured a negative after-cost Sharpe (chapter 11 E): they do not start
    assert "oracle_bonds" not in books and "oracle_gold" not in books
    # the oil book is unchanged: the de-escalation sleeve as it is
    assert books["oracle"].sleeves == {"deescalation": 1.0} and books["oracle"].vehicle == "MCL"
    # none of the three is replayed: no backtest, no fingerprint, no attribution row
    cfgs = replayed_books(list(books.values()))
    assert {b.id for b in cfgs} & {"oracle", "oracle_equities", "oracle_utilities"} == set()
    # the tick downloads and the backfill fills every series an index reads
    assert oracle.ALL_SERIES == (
        "KXHORMUZNORM",
        "KXUSIRANMEETING",
        "KXUSAIRANAGREEMENT",
        "KXFEDDECISION",
        "KXRECSSNBER",
    )
    with pytest.raises(ValueError, match="unknown sleeves"):
        BookConfig(**{**EQUITIES.__dict__, "sleeves": {"fedpath": 1.0, "weather": 1.0}})


def test_the_archives_days_are_counted_over_every_series_and_the_filled_ones_are_those_the_tick_never_read(tmp_path):
    from engine.desk.backfill import archive_days, backfilled_days

    store = desk_store(tmp_path)
    assert archive_days(store) == (0, None) and backfilled_days(store) == 0
    apply_backfill(store, records_from_history(history()))
    n, first = archive_days(store)
    assert (n, first) == (137, "2026-06-01") and backfilled_days(store) == 137  # June to 15 October, candles only
    # the tick reads one series on the 15th: that day is the tick's, even though the other series' records of
    # the day are still the candles'
    live = {
        "key": "2026-10-15|KXHORMUZNORM-X", "day": "2026-10-15", "ts": "2026-10-15T00:20:00Z", "series": "KXHORMUZNORM",
        "event": "KXHORMUZNORM-X", "title": "x", "columns": ["ticker", "label", "yes_bid", "yes_ask"],
        "markets": [["KXHORMUZNORM-X-1", "Before Jan 1, 2027", 0.05, 0.07]],
    }  # fmt: skip
    store.append_jsonl(oracle.KALSHI_LOG, live, ts=datetime(2026, 10, 15, 0, 20, tzinfo=UTC))
    assert archive_days(store) == (137, "2026-06-01") and backfilled_days(store) == 136
