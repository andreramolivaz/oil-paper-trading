"""The weekend watch (phase 22, ``engine/desk/weekend.py``) and the radar that shows it
(``engine/desk/radar.py``), ``docs/RESEARCH.md`` 17 E and J.

Kalshi trades while crude is closed. The watch marks the de-escalation index at the first reading after the
futures close on Friday, follows it until Sunday 17:00 New York, and logs the weekend. It is a FACT on the page:
the one measurement (17 M, 34 weekends) found the sign right and the size unreadable, so nothing trades on it and
the sentence says so.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from engine.core.store import StateStore
from engine.desk import prediction, radar, weekend

FRIDAY_CLOSE = datetime(2026, 11, 6, 22, 0, tzinfo=UTC)  # 17:00 New York
SERIES = ("KXHORMUZNORM", "KXUSIRANMEETING", "KXUSAIRANAGREEMENT")
# every market's deadline is the index's own horizon, so that a component reads exactly its quote and the
# arithmetic of these tests is the plain one: the ladder's interpolation has its own tests (test_desk_oracle.py)
DEADLINE = (FRIDAY_CLOSE + timedelta(days=90)).date()


def _reading(when: datetime, probabilities: dict[str, float]) -> prediction.Reading:
    """A download of the markets at ``when``: one event per series, one market each, priced at ``p``."""
    rows = [
        {
            "series_ticker": series,
            "event_ticker": f"{series}-26",
            "ticker": f"{series}-26-A",
            "title": f"the strait back to normal before {DEADLINE:%b %-d, %Y}",
            "yes_sub_title": f"before {DEADLINE:%b %-d, %Y}",
            "yes_bid": round(p - 0.01, 4),
            "yes_ask": round(p + 0.01, 4),
            "volume": 10.0,
            "open_interest": 100.0,
            "status": "active",
            "close_time": pd.Timestamp(DEADLINE, tz="UTC"),
            "seen": pd.Timestamp(when),
        }
        for series, p in probabilities.items()
    ]
    return prediction.Reading(pd.DataFrame(rows), pd.Timestamp(when))


@pytest.fixture
def quotes(monkeypatch):
    """The newest download the watch sees, settable per call."""
    box: dict[str, prediction.Reading | None] = {"reading": None}
    monkeypatch.setattr(prediction, "latest_reading", lambda raw: box["reading"])
    monkeypatch.setattr(weekend.prediction, "latest_reading", lambda raw: box["reading"])
    return box


# ----------------------------------------------------------------------------------------------- the calendar
def test_the_weekend_runs_from_fridays_close_to_sunday_five_in_new_york():
    # any instant of the weekend belongs to the Friday that opened it
    for when in (FRIDAY_CLOSE, FRIDAY_CLOSE + timedelta(hours=30), FRIDAY_CLOSE + timedelta(days=1, hours=23)):
        assert weekend.friday_close(when) == FRIDAY_CLOSE
    # an hour before the close still belongs to the PREVIOUS weekend: the futures were trading. That Friday was
    # still on summer time in New York, so its close is an hour earlier in UTC - the mark is a CLOCK time there
    assert weekend.friday_close(FRIDAY_CLOSE - timedelta(hours=1)) == datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
    assert weekend.sunday_mark(FRIDAY_CLOSE) == FRIDAY_CLOSE + timedelta(days=2)


# ----------------------------------------------------------------------------------------------- the watch
def test_the_watch_marks_the_index_after_the_close_follows_it_and_logs_the_weekend(tmp_path, quotes):
    store = StateStore(tmp_path)
    base = dict.fromkeys(SERIES, 0.10)
    quotes["reading"] = _reading(FRIDAY_CLOSE + timedelta(minutes=20), base)
    first = weekend.update(None, store, FRIDAY_CLOSE + timedelta(minutes=25))
    assert first["weekend"] == "2026-11-06" and first["mark"] == "reading" and first["change"] == 0.0

    # Saturday: the odds move five points on one market, so the index moves a third of that
    quotes["reading"] = _reading(FRIDAY_CLOSE + timedelta(days=1), {**base, "KXHORMUZNORM": 0.25})
    during = weekend.update(None, store, FRIDAY_CLOSE + timedelta(days=1, minutes=5))
    assert during["change"] == pytest.approx(0.05, abs=1e-9)
    assert store.tail_jsonl(weekend.WEEKEND_LOG, 5) == []  # not over, nothing logged

    # the same download again changes nothing
    assert weekend.update(None, store, FRIDAY_CLOSE + timedelta(days=1, minutes=35))["change"] == pytest.approx(0.05)
    assert len(store.tail_jsonl(weekend.WEEKEND_LOG, 5)) == 0

    # after Sunday 17:00 the weekend is logged, once
    quotes["reading"] = _reading(FRIDAY_CLOSE + timedelta(days=2, hours=3), {**base, "KXHORMUZNORM": 0.25})
    over = weekend.update(None, store, FRIDAY_CLOSE + timedelta(days=2, hours=3))
    assert over["logged"] == "2026-11-06"
    rows = store.tail_jsonl(weekend.WEEKEND_LOG, 5)
    assert len(rows) == 1 and rows[0]["w"] == pytest.approx(0.05) and rows[0]["components"] == sorted(SERIES)
    weekend.update(None, store, FRIDAY_CLOSE + timedelta(days=2, hours=4))
    assert len(store.tail_jsonl(weekend.WEEKEND_LOG, 5)) == 1


def test_a_reading_after_sunday_five_does_not_enter_the_weekends_move(tmp_path, quotes):
    store = StateStore(tmp_path)
    quotes["reading"] = _reading(FRIDAY_CLOSE + timedelta(minutes=20), dict.fromkeys(SERIES, 0.10))
    weekend.update(None, store, FRIDAY_CLOSE + timedelta(minutes=25))
    late = FRIDAY_CLOSE + timedelta(days=2, hours=2)  # 19:00 New York Sunday: the futures trade again
    quotes["reading"] = _reading(late, dict.fromkeys(SERIES, 0.40))
    out = weekend.update(None, store, late)
    assert out["change"] == 0.0  # the move after the reopening is the market's, not the weekend's


def test_without_a_reading_near_the_close_the_mark_comes_from_the_archive_and_says_how_late_it_is(tmp_path, quotes):
    store = StateStore(tmp_path)
    saturday = FRIDAY_CLOSE + timedelta(hours=5)
    for series in SERIES:
        row = dict.fromkeys(prediction.LOG_COLUMNS)
        row |= {
            "ticker": f"{series}-26-A",
            "label": f"before {DEADLINE:%b %-d, %Y}",
            "yes_bid": 0.09,
            "yes_ask": 0.11,
            "volume": 10.0,
            "close_time": f"{DEADLINE}T00:00:00Z",
        }
        store.append_jsonl(
            prediction.KALSHI_LOG,
            {
                "day": saturday.date().isoformat(),
                "ts": saturday.isoformat().replace("+00:00", "Z"),
                "series": series,
                "event": f"{series}-26",
                "title": f"the strait back to normal before {DEADLINE:%b %-d, %Y}",
                "columns": list(prediction.LOG_COLUMNS),
                "markets": [[row[c] for c in prediction.LOG_COLUMNS]],
            },
            saturday,
        )
    quotes["reading"] = _reading(FRIDAY_CLOSE + timedelta(hours=14), dict.fromkeys(SERIES, 0.20))
    out = weekend.update(None, store, FRIDAY_CLOSE + timedelta(hours=14))
    assert out["mark"] == "archive"
    state = store.read_json(weekend.WEEKEND_FILE)
    assert state["mark"]["late_hours"] == pytest.approx(5.0, abs=0.1)
    block = weekend.block(store, FRIDAY_CLOSE + timedelta(hours=14))
    assert "5 hours after the futures closed" in (block["mark_note"] or "")
    # and the move is measured from that archived mark (0.10) to the live reading (0.20), not from zero
    assert block["change"] == pytest.approx(0.10, abs=1e-9)


def test_the_page_says_the_move_and_what_the_measurement_found_and_flags_five_points(tmp_path, quotes):
    store = StateStore(tmp_path)
    base = dict.fromkeys(SERIES, 0.10)
    quotes["reading"] = _reading(FRIDAY_CLOSE + timedelta(minutes=20), base)
    weekend.update(None, store, FRIDAY_CLOSE + timedelta(minutes=25))
    quiet = weekend.block(store, FRIDAY_CLOSE + timedelta(hours=2))
    assert quiet["phase"] == "in progress" and quiet["flag"] is False
    assert "could not be told from the size of the move" in quiet["text"]
    assert quiet["measured"]["weekends"] == 34 and quiet["measured"]["page_shows_gap"] is False

    quotes["reading"] = _reading(FRIDAY_CLOSE + timedelta(days=1), dict.fromkeys(SERIES, 0.30))
    weekend.update(None, store, FRIDAY_CLOSE + timedelta(days=1))
    loud = weekend.block(store, FRIDAY_CLOSE + timedelta(days=1))
    assert loud["flag"] is True and "flagged" in loud["text"]
    assert loud["change"] == pytest.approx(0.20)


def test_with_fewer_than_two_components_in_common_there_is_no_move_to_show(tmp_path, quotes):
    store = StateStore(tmp_path)
    quotes["reading"] = _reading(FRIDAY_CLOSE + timedelta(minutes=20), {"KXHORMUZNORM": 0.10})
    weekend.update(None, store, FRIDAY_CLOSE + timedelta(minutes=25))
    block = weekend.block(store, FRIDAY_CLOSE + timedelta(hours=2))
    assert block["change"] is None and "fewer than two components" in block["text"]
    assert block["flag"] is False


# ----------------------------------------------------------------------------------------------- the radar
def test_the_curve_half_life_finds_the_month_where_half_the_premium_is_gone():
    closes = {"BZF27": 96.0, "BZG27": 93.0, "BZJ27": 90.0, "BZN27": 86.0, "BZZ27": 80.0}
    out = radar.curve_half_life(closes, date(2026, 11, 13))
    assert out["backwardation"] is True and out["half_gone_by"] == "2027-07-01"
    assert out["premium"] == pytest.approx(16.0)
    # a curve in contango prices no shortage to end
    rising = {"BZF27": 80.0, "BZG27": 83.0, "BZJ27": 86.0, "BZN27": 90.0, "BZZ27": 96.0}
    assert radar.curve_half_life(rising, date(2026, 11, 13))["backwardation"] is False
    assert radar.curve_half_life({"BZF27": 96.0}, date(2026, 11, 13)) is None  # too few contracts


def test_a_futures_code_gives_its_delivery_month_and_anything_else_gives_nothing():
    assert radar.contract_month("BZM27") == date(2027, 6, 1)
    assert radar.contract_month("CLZ26") == date(2026, 12, 1)
    assert radar.contract_month("BNO") is None and radar.contract_month("") is None


def test_the_median_date_of_a_ladder_is_where_it_crosses_a_half():
    today = date(2026, 11, 15)
    ladder = [(date(2027, 1, 1), 0.10), (date(2027, 7, 1), 0.40), (date(2028, 1, 1), 0.60)]
    median, crosses = radar.ladder_median(ladder, today)
    assert crosses is True and date(2027, 7, 1) < median < date(2028, 1, 1)
    # a ladder that never reaches a half has no median inside its deadlines
    low = [(date(2027, 1, 1), 0.05), (date(2028, 1, 1), 0.30)]
    assert radar.ladder_median(low, today) == (None, False)


def test_a_tile_that_fails_costs_its_own_tile_and_never_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr(radar, "kalshi_tile", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    block = radar.radar_block(StateStore(tmp_path), None, _empty_data(), {}, {}, [], FRIDAY_CLOSE)
    ids = [t["id"] for t in block["tiles"]]
    assert ids == ["kalshi", "options", "weekend", "reopening", "term", "spread", "system"]
    broken = block["tiles"][0]
    assert broken["error"].startswith("RuntimeError: boom") and broken["flag"] is False
    assert "could not be computed" in broken["text"]


def test_the_system_tile_is_quiet_on_a_weekend_and_speaks_when_a_tick_is_missing(tmp_path):
    asleep = radar.system_tile({"last_ok_tick": None, "overall": "green"}, [], datetime(2026, 11, 14, 18, tzinfo=UTC))
    assert asleep["flag"] is False and asleep["asleep"] is True and "Weekend" in asleep["text"]
    # a Wednesday afternoon with no tick for hours is something to look at
    wednesday = datetime(2026, 11, 11, 19, tzinfo=UTC)
    late = radar.system_tile(
        {"last_ok_tick": (wednesday - timedelta(hours=6)).isoformat(), "overall": "green"}, [], wednesday
    )
    assert late["flag"] is True and "6.0 hours old" in late["text"]
    # a red critical source, and a halted book, are both said
    red = radar.system_tile(
        {"last_ok_tick": wednesday.isoformat(), "overall": "red", "not_green": [{"source": "x", "status": "red"}]},
        [{"id": "spinto", "name": "Spinto", "status": "dead"}],
        wednesday,
    )
    assert red["flag"] is True and "critical data source is red" in red["text"] and "Spinto" in red["text"]
    # a secondary source that is not green is said, never flagged: it silences only what reads it
    yellow = radar.system_tile(
        {"last_ok_tick": wednesday.isoformat(), "overall": "yellow", "not_green": [{"source": "gpr", "status": "red"}]},
        [],
        wednesday,
    )
    assert yellow["flag"] is False and "1 secondary source not green" in yellow["text"]


def _empty_data():
    """A desk-data stand-in with nothing in it: every tile must cope with an empty archive."""

    class Empty:
        series: dict = {}
        contracts: dict = {}
        intraday: dict = {}

    return Empty()
