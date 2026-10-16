"""The prediction markets' archive filled from Kalshi's daily candlesticks (phase 14, ``engine/desk/backfill.py``),
the adapter's candlestick rows, the job, and what the speculative book reads once the archive has a past.

The fixture is the runner's own download of 2026-10-15 17:59 UTC (``research-data``, ``kalshi-history``): every
market of the three series the de-escalation index is made of, with their daily candlesticks since each opened
(``tests/fixtures/kalshi_history_2026-10-15.json``, the fields the backfill reads). The live archive of the 14th
and 15th (``kalshi_log_2026-10.jsonl``) is the one the tick wrote.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from engine.core.store import StateStore
from engine.data.adapters.kalshi import CANDLE_COLUMNS, parse_candlesticks
from engine.desk import oracle
from engine.desk.backfill import SOURCE, apply_backfill, records_from_history
from engine.desk.prediction import KALSHI_LOG, LOG_COLUMNS

FIXTURES = Path(__file__).parent / "fixtures"
HISTORY = FIXTURES / "kalshi_history_2026-10-15.json"
LIVE_LOG = FIXTURES / "kalshi_log_2026-10.jsonl"


def history() -> dict:
    return json.loads(HISTORY.read_text(encoding="utf-8"))


def live_records() -> list[dict]:
    return [json.loads(line) for line in LIVE_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------------------------- the adapter
def test_the_adapter_reads_both_spellings_of_a_candlestick_and_sorts_them():
    live = [
        {
            "end_period_ts": 1792036800,
            "yes_bid": {"open_dollars": "0.062", "close_dollars": "0.061"},
            "yes_ask": {"open_dollars": "0.067", "close_dollars": "0.072"},
            "price": {"close_dollars": "0.071", "previous_dollars": "0.069"},
            "volume_fp": "42620.93",
            "open_interest_fp": "1265568.38",
        },
        {"end_period_ts": 1791950400, "yes_bid": {"close_dollars": "0.062"}, "yes_ask": {"close_dollars": "0.067"}},
        {"no_end": True},
    ]
    rows = parse_candlesticks(live, "T", "live")
    assert [r["end_ts"] for r in rows] == [1791950400, 1792036800] and list(rows[0]) == CANDLE_COLUMNS
    assert rows[1]["yes_bid_close"] == 0.061 and rows[1]["yes_ask_close"] == 0.072 and rows[1]["price_close"] == 0.071
    assert rows[1]["volume"] == 42620.93 and rows[1]["open_interest"] == 1265568.38 and rows[1]["source"] == "live"
    assert rows[0]["price_close"] is None and rows[0]["volume"] is None
    # the historical endpoint spells the same fields without "_dollars" and "_fp"
    old = [
        {
            "end_period_ts": 1,
            "yes_bid": {"close": "0.5"},
            "yes_ask": {"close": "0.6"},
            "price": {"close": None},
            "volume": "3",
            "open_interest": "9",
        }
    ]
    row = parse_candlesticks(old, "T", "historical")[0]
    assert (row["yes_bid_close"], row["yes_ask_close"], row["price_close"], row["volume"], row["open_interest"]) == (
        0.5,
        0.6,
        None,
        3.0,
        9.0,
    )


# ---------------------------------------------------------------------------------------------- the records
def test_the_history_becomes_one_record_per_event_and_day_in_the_archives_format():
    recs = records_from_history(history())
    assert recs and all(r["columns"] == list(LOG_COLUMNS) and r["backfilled"] and r["source"] == SOURCE for r in recs)
    days = sorted({r["day"] for r in recs})
    assert days[0] == "2026-01-09" and days[-1] == "2026-10-15"  # the download was taken on the 15th at 17:59 UTC
    assert all(r["key"] == f"{r['day']}|{r['event']}" for r in recs)
    assert len({r["key"] for r in recs}) == len(recs)  # one record per event and day
    # the candle in progress (ending 2026-10-16 04:00 UTC) is not a close and is left out
    assert not any(r["day"] == "2026-10-16" for r in recs)
    # a daily candle ends at midnight New York: the record of UTC day D holds the state at the start of D
    strait = [r for r in recs if r["series"] == "KXHORMUZNORM"]
    assert strait[0]["day"] == "2026-03-18" and strait[0]["ts"] == "2026-03-18T04:00:00Z"
    cols = strait[0]["columns"]
    by_ticker = {row[cols.index("ticker")]: row for row in strait[0]["markets"]}
    first = by_ticker["KXHORMUZNORM-26MAR17-B270101"]  # the one market open on the strait's first day
    assert first[cols.index("label")] == "Before Jan 1, 2027"
    assert (first[cols.index("yes_bid")], first[cols.index("yes_ask")], first[cols.index("last")]) == (0.86, 0.95, 0.96)
    assert (
        first[cols.index("volume_24h")] == 2.0 and first[cols.index("volume")] == 2.0 and first[cols.index("oi")] == 2.0
    )
    assert first[cols.index("prev_mid")] is None  # no candle before the first
    assert first[cols.index("close_time")] == "2027-01-01T14:59:00Z"
    # a market is in a day's record only while it is open: "Before Sep 1, 2026" settled on 1 September 13:10 UTC
    sep = {r["day"]: r for r in strait}
    tickers = lambda rec: {row[cols.index("ticker")] for row in rec["markets"]}  # noqa: E731
    assert "KXHORMUZNORM-26MAR17-B260901" in tickers(sep["2026-09-01"])
    assert "KXHORMUZNORM-26MAR17-B260901" not in tickers(sep["2026-09-02"])
    assert "KXHORMUZNORM-26MAR17-B270201" not in tickers(sep["2026-10-08"])  # opened the 8th at 17:30 UTC
    assert "KXHORMUZNORM-26MAR17-B270201" in tickers(sep["2026-10-09"])
    # the cumulative volume runs from the first candle; the markets are in the archive's order (by close time)
    jan = [row for row in sep["2026-10-13"]["markets"] if row[cols.index("ticker")].endswith("B270101")][0]
    assert jan[cols.index("volume")] > jan[cols.index("volume_24h")] > 0
    closes = [row[cols.index("close_time")] for row in sep["2026-10-13"]["markets"]]
    assert closes == sorted(closes)
    # the record of the 15th from the candle against the tick's reading of the 15th: the same ladder, read
    # three hours apart (00:48 against 04:00 UTC): the strait within a cent, the thin meeting market wider
    live = {r["key"]: r for r in live_records()}
    candle = {r["key"]: r for r in recs}
    key = "2026-10-15|KXHORMUZNORM-26MAR17"
    live_jan = {row[0]: row for row in live[key]["markets"]}["KXHORMUZNORM-26MAR17-B270101"]
    cand_jan = {row[0]: row for row in candle[key]["markets"]}["KXHORMUZNORM-26MAR17-B270101"]
    assert abs(live_jan[cols.index("yes_bid")] - cand_jan[cols.index("yes_bid")]) <= 0.01
    assert abs(live_jan[cols.index("yes_ask")] - cand_jan[cols.index("yes_ask")]) <= 0.01


def test_an_empty_or_odd_history_gives_no_record_and_no_error():
    assert records_from_history({}) == []
    assert records_from_history({"series": {"X": {"markets": [], "candles": {"T": [{"end_ts": 1}]}}}}) == []
    odd = {
        "fetched_at": "2026-10-15T17:59:19Z",
        "series": {
            "KXHORMUZNORM": {
                "markets": [
                    {"ticker": "T", "event_ticker": "E", "open_time": "2026-10-01T00:00:00Z", "close_time": "bad"}
                ],
                "candles": {
                    "T": [{"end_ts": 1791950400, "yes_bid_close": 0.1, "yes_ask_close": 0.2}, {"junk": 1}, "x"]
                },
            }
        },
    }
    recs = records_from_history(odd)
    assert len(recs) == 1 and recs[0]["day"] == "2026-10-14" and recs[0]["markets"][0][4:6] == [0.1, 0.2]


# ---------------------------------------------------------------------------------------------- the archive
def test_the_backfill_fills_only_the_days_the_tick_never_read_and_is_idempotent(tmp_path):
    store = StateStore(tmp_path / "desk")
    for rec in live_records():
        store.append_jsonl(KALSHI_LOG, rec, ts=datetime(2026, 10, 14, tzinfo=UTC))
    before = oracle.archive_readings(store)
    assert [r.day for r in before] == [date(2026, 10, 14), date(2026, 10, 15)]
    recs = [r for r in records_from_history(history()) if r["series"] in oracle.COMPONENTS]
    done = apply_backfill(store, recs)
    live_keys = {r["key"] for r in live_records()}
    assert done["written"] == len([r for r in recs if r["key"] not in live_keys])
    assert done["skipped"] == len([r for r in recs if r["key"] in live_keys]) == 6  # three events on two live days
    assert done["first_day"] == "2026-01-09" and done["last_day"] == "2026-10-13"  # the 14th and 15th are the tick's
    after = oracle.archive_readings(store)
    assert after[-1].day == date(2026, 10, 15) and after[0].day == date(2026, 1, 9)
    live_15 = next(r for r in after if r.day == date(2026, 10, 15))
    assert live_15.components["KXUSIRANMEETING"] == pytest.approx(0.456, abs=5e-4)  # the tick's reading, kept
    # the archive's own records are untouched and still first in the file; the backfill says what it is
    lines = [
        json.loads(line)
        for line in store.jsonl_path(KALSHI_LOG, ts=datetime(2026, 10, 1, tzinfo=UTC)).read_text().splitlines()
    ]
    assert lines[0]["key"] == live_records()[0]["key"] and not lines[0].get("backfilled")
    assert all(line.get("backfilled") for line in lines[len(live_records()) :])
    # run again: nothing to add
    again = apply_backfill(store, recs)
    assert again["written"] == 0 and again["skipped"] == len(recs)
    # the months land in their own files
    assert store.jsonl_path(KALSHI_LOG, ts=datetime(2026, 3, 1, tzinfo=UTC)).exists()
    assert store.jsonl_path(KALSHI_LOG, ts=datetime(2025, 12, 1, tzinfo=UTC)).exists() is False


def test_with_a_past_the_oracle_has_a_reference_and_the_index_history_reads_as_registered(tmp_path):
    store = StateStore(tmp_path / "desk")
    for rec in live_records():
        store.append_jsonl(KALSHI_LOG, rec, ts=datetime(2026, 10, 14, tzinfo=UTC))
    apply_backfill(store, [r for r in records_from_history(history()) if r["series"] in oracle.COMPONENTS])
    readings = oracle.archive_readings(store)
    by_day = {r.day: r for r in readings}
    # the deal's ladder alone until the strait's markets open on 17 March; the meeting's from 24 September
    assert set(by_day[date(2026, 3, 10)].components) == {"KXUSAIRANAGREEMENT"}
    assert set(by_day[date(2026, 3, 25)].components) == {"KXHORMUZNORM", "KXUSAIRANAGREEMENT"}
    assert set(by_day[date(2026, 10, 6)].components) == set(oracle.COMPONENTS)
    # docs/RESEARCH.md chapter 10 G: the index peaked in June and bottomed in September
    values = {d: r.value for d, r in by_day.items() if r.value is not None and d >= date(2026, 3, 18)}
    assert max(values, key=values.get) == date(2026, 6, 15) and values[date(2026, 6, 15)] == pytest.approx(
        0.575, abs=2e-3
    )
    assert min(values, key=values.get) == date(2026, 9, 11) and values[date(2026, 9, 11)] == pytest.approx(
        0.081, abs=2e-3
    )
    # the decision of 15 October: the tick's reading against the candle of the 8th, three components in both
    out = oracle.forecast_on(readings, date(2026, 10, 15))
    assert out.reading.day == date(2026, 10, 15) and out.reference.day == date(2026, 10, 8)
    assert out.common == tuple(oracle.COMPONENTS) and out.change == pytest.approx(-0.0376, abs=1e-3)
    assert out.forecast == pytest.approx(7.52, abs=0.05)  # escalation: long oil, under a normal conviction
    # a ladder that appears between the two readings is not a move: on 1 October the meeting's markets (open
    # since the 24th) are in today's reading and not in the reference's; the change is over the other two
    first_week = oracle.forecast_on(readings, date(2026, 10, 1))
    assert first_week.common == ("KXHORMUZNORM", "KXUSAIRANAGREEMENT") and first_week.reference.day == date(2026, 9, 24)


def test_the_job_fills_the_archive_from_a_saved_history_and_runs_once_per_id(tmp_path):
    from engine.live import jobs
    from engine.live.runner import LiveRunner
    from tests.test_tick_job import _settings

    runner = LiveRunner(_settings(tmp_path))
    desk_dir = runner.settings.state_dir / "desk"
    desk_dir.mkdir(parents=True, exist_ok=True)
    (desk_dir / "kalshi-2026-10.jsonl").write_text(LIVE_LOG.read_text(encoding="utf-8"), encoding="utf-8")
    out = jobs.kalshi_backfill(runner, "bf-1", date(2026, 1, 1), HISTORY)
    assert out.status == "ok" and out.detail["applied"]["written"] == 508 and out.detail["applied"]["skipped"] == 6
    archive = out.detail["archive"]
    assert archive["first_day"] == "2026-01-09" and archive["days"] == 280  # every series, every day
    assert archive["indices"]["deescalation"] == {"readings": 276, "first_day": "2026-01-13", "last_day": "2026-10-15"}
    assert archive["indices"]["fedpath"]["readings"] == 0  # the history fixture has no FOMC series
    assert "the archive now has 280 days" in out.message
    assert "records written" in out.message and str(out.detail["source"]).endswith("kalshi_history_2026-10-15.json")
    assert jobs.kalshi_backfill(runner, "bf-1", date(2026, 1, 1), HISTORY).status == "skipped"
    again = jobs.kalshi_backfill(runner, "bf-2", date(2026, 1, 1), HISTORY)
    assert again.status == "ok" and again.detail["applied"]["written"] == 0 and "nothing to add" in again.message
    # the run is on the record like any other job
    runs = [r for r in runner.store.read_jsonl("runs") if r.get("job") == "kalshi_backfill"]
    assert [r["status"] for r in runs] == ["ok", "ok"]
