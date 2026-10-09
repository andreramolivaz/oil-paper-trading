"""What the scheduler's tick decides to do, and the four causes of the flat account that it must not repeat.

1. a guard that says "too early" is not a failure (it used to paint the run red);
2. the end of day targets the last date that HAS settled, whatever time GitHub starts the job;
3. only a critical source can stop new risk (one optional table going red had halted the whole account);
4. the Form 4 table is read back after being fetched (it was archived and never loaded).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from engine.core.config import Settings
from engine.core.store import StateStore
from engine.data.adapters.insider import load_fixture
from engine.data.assemble import Loader, _insider_table, build_market_data
from engine.data.base import FetchResult
from engine.data.fetch import DEFAULT_CRITICAL, Fetcher, front_contract_symbol, overall_from_sources, quality_issues
from engine.data.raw_store import RawStore
from engine.live import jobs

REPO = Path(__file__).resolve().parent.parent
INSIDER_FIXTURE = Path(__file__).parent / "fixtures" / "insider" / "alphavantage_form4_COP_OXY.json"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path / "state",
        config_dir=REPO / "config",
        eia_api_key=None,
        fred_api_key=None,
        github_repo="test/test",
        offline=True,
    )


def _runner(tmp_path: Path, sources: list[dict]) -> SimpleNamespace:
    settings = _settings(tmp_path)
    return SimpleNamespace(
        settings=settings,
        raw=RawStore(settings.state_dir),
        store=StateStore(settings.state_dir),
        health=lambda: {"sources": sources},
    )


# ----------------------------------------------------------------------------------------------- 1. exit codes
def test_nothing_to_do_is_not_a_failure():
    assert jobs.JobOutcome("skipped", "prima del settlement", {}).exit_code == 0
    assert jobs.JobOutcome("ok", "", {}).exit_code == 0
    assert jobs.JobOutcome("failed", "boom", {}).exit_code == 1


# ----------------------------------------------------------------------------------------------- 2. which date
@pytest.mark.parametrize(
    ("now", "want"),
    [
        (datetime(2026, 10, 8, 18, 40, tzinfo=UTC), date(2026, 10, 8)),  # 19:40 London: today has settled
        (datetime(2026, 10, 8, 17, 0, tzinfo=UTC), date(2026, 10, 7)),  # 18:00 London: not yet, yesterday has
        (datetime(2026, 10, 8, 0, 58, tzinfo=UTC), date(2026, 10, 7)),  # 01:58 London: the run that was failing
        (datetime(2026, 10, 12, 6, 0, tzinfo=UTC), date(2026, 10, 9)),  # Monday morning: Friday
        (datetime(2026, 10, 10, 12, 0, tzinfo=UTC), date(2026, 10, 9)),  # Saturday: Friday
    ],
)
def test_the_end_of_day_targets_the_last_date_that_has_settled(now, want):
    assert jobs.latest_settled_date(now) == want


# ----------------------------------------------------------------------------------------------- 3. health
def _source(name: str, status: str) -> dict:
    return {"source": name, "status": status}


def test_only_a_critical_source_turns_the_overall_status_red():
    critical = DEFAULT_CRITICAL
    assert critical == {"brent_front", "wti_front", "bno_daily"}
    optional_red = [_source("brent_front", "green"), _source("insider_form4", "red"), _source("gpr", "red")]
    assert overall_from_sources(optional_red, critical) == "yellow"
    assert overall_from_sources([_source("brent_front", "red"), _source("gpr", "green")], critical) == "red"
    assert overall_from_sources([_source("brent_front", "green"), _source("wti_front", "green")], critical) == "green"
    assert overall_from_sources([], critical) == "red"  # no information is not good news


def test_a_vehicle_is_blocked_only_when_what_it_needs_is_red(tmp_path):
    ok = [
        _source("bno_daily", "green"),
        _source("cl_contracts", "yellow"),
        _source("wti_front", "red"),
        _source("gpr", "red"),
    ]
    assert jobs._blocked_vehicles(_runner(tmp_path, ok)) == {}
    no_fund = [_source("bno_daily", "red"), _source("cl_contracts", "green")]
    blocked = jobs._blocked_vehicles(_runner(tmp_path, no_fund))
    assert list(blocked) == ["BNO"] and "bno_daily" in blocked["BNO"]
    # the WTI book survives the loss of one of its two tables, not of both
    both = [_source("bno_daily", "green"), _source("cl_contracts", "red"), _source("wti_front", "red")]
    assert list(jobs._blocked_vehicles(_runner(tmp_path, both))) == ["MCL"]
    # a table never downloaded is not a table that works
    assert set(jobs._blocked_vehicles(_runner(tmp_path, []))) == {"BNO", "MCL"}


def test_the_weekly_table_is_retried_by_a_tick_at_most_once_a_day(tmp_path):
    now = datetime(2026, 10, 8, 19, 0, tzinfo=UTC)

    def insider(status: str, hours_ago: float, ok_days_ago: float | None = None) -> list[dict]:
        entry = {
            "source": "insider_form4",
            "status": status,
            "checked_at": (now - timedelta(hours=hours_ago)).isoformat(),
        }
        if ok_days_ago is not None:
            entry["last_success_at"] = (now - timedelta(days=ok_days_ago)).isoformat()
        return [entry]

    assert jobs._weekly_alt_due(_runner(tmp_path, []), now) is True  # never downloaded
    assert jobs._weekly_alt_due(_runner(tmp_path, insider("green", 100)), now) is False  # the weekly job owns it
    assert jobs._weekly_alt_due(_runner(tmp_path, insider("red", 2)), now) is False
    assert jobs._weekly_alt_due(_runner(tmp_path, insider("red", 21)), now) is True
    assert jobs._weekly_alt_due(_runner(tmp_path, insider("yellow", 30)), now) is True
    # downloaded this week but coloured yellow by a quality warning: twelve calls a day would be spent on nothing
    assert jobs._weekly_alt_due(_runner(tmp_path, insider("yellow", 30, ok_days_ago=1.25)), now) is False
    assert jobs._weekly_alt_due(_runner(tmp_path, insider("yellow", 30, ok_days_ago=9)), now) is True


def test_option_chains_are_read_once_at_first_start_then_only_in_market_hours(tmp_path):
    evening = datetime(2026, 10, 8, 21, 17, tzinfo=UTC)  # 17:17 New York: the desk's first tick on 8 October
    assert jobs._options_due(_runner(tmp_path, []), evening) is True  # never read: show the session's last quotes
    stamp = evening.isoformat()
    read = [{"source": "uso_options", "status": "green", "checked_at": stamp, "last_success_at": stamp}]
    assert jobs._options_due(_runner(tmp_path, read), evening + timedelta(minutes=31)) is False
    refused = [{"source": "uso_options", "status": "red", "checked_at": stamp}]
    assert jobs._options_due(_runner(tmp_path, refused), evening + timedelta(minutes=31)) is False  # not at night
    morning = datetime(2026, 10, 9, 13, 48, tzinfo=UTC)  # 09:48 New York: market open, quotes more than an hour old
    assert jobs._options_due(_runner(tmp_path, read), morning) is True
    assert jobs._options_due(_runner(tmp_path, refused), morning) is True


def test_a_tick_downloads_only_the_daily_tables_its_decisions_need(tmp_path):
    """The whole "prices" group is about sixty requests to Yahoo and the desk reads three of its tables. Asking
    for all of it at every refresh got the address rate limited, and a rate limit costs the tick its bars."""
    from engine.core.config import RiskConfig
    from engine.desk.live import build_desk

    desk = build_desk(tmp_path / "state", RiskConfig.load())
    bno, mcl = set(jobs.DESK_VEHICLE_ENTRIES["BNO"]), set(jobs.DESK_VEHICLE_ENTRIES["MCL"])
    context = set(jobs.DESK_CONTEXT_ENTRIES)
    # the fund's own tables, plus the inverse fund a fund book buys to be short
    assert bno == {"bno_daily", "brent_front", "bz_contracts", "sco_daily"}
    assert mcl == {"cl_contracts", "wti_front", "uso_daily"}

    macro = set(jobs.DESK_MACRO_ENTRIES)
    assert macro == {"copper_daily", "dxy_daily"}
    wednesday_decision = datetime(2026, 10, 7, 18, 48, tzinfo=UTC)  # 14:48 New York: Wednesday's first decision

    def health(
        bno_at: datetime | None,
        mcl_at: datetime | None,
        context_at: datetime | None,
        macro_at: datetime | None = wednesday_decision,
    ) -> list[dict]:
        rows = []
        groups = ((bno, bno_at), (mcl, mcl_at), (context, context_at), (macro, macro_at))
        for names, when in groups:  # every table of a group is downloaded together, so they share a time
            if when is not None:
                rows += [{"source": n, "status": "green", "last_success_at": when.isoformat()} for n in sorted(names)]
        return rows

    def entries(now: datetime, *checked: datetime | None) -> set[str]:
        return jobs._desk_daily_entries(_runner(tmp_path, health(*checked)), desk, now)[0]

    morning = datetime(2026, 10, 8, 14, 18, tzinfo=UTC)  # 10:18 New York, Thursday
    # a first run downloads everything once
    assert entries(morning, None, None, None, None) == bno | mcl | context | macro
    # A new desk owes the previous session's decision. Tables read after last night's close can serve it, and
    # so can the copper and dollar tables read at that session's own first decision.
    last_night = datetime(2026, 10, 7, 20, 20, tzinfo=UTC)
    assert entries(morning, last_night, last_night, last_night) == set()
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-07"
    # At 14:48 only the futures book owes today's decision (it decides at 14:35), so only its tables are read -
    # and, with them, copper and the dollar: the first decision of a day is what downloads them, because the
    # close a decision reads (yesterday's) is on file in its final form only from today on.
    at_1448 = datetime(2026, 10, 8, 18, 48, tzinfo=UTC)
    got, why = jobs._desk_daily_entries(_runner(tmp_path, health(last_night, last_night, last_night)), desk, at_1448)
    assert got == mcl | macro
    assert "macro: chiusure di rame e dollaro per la decisione del 2026-10-08" in why
    desk.state.last_decision_day["spinto"] = "2026-10-08"
    at_1518 = datetime(2026, 10, 8, 19, 18, tzinfo=UTC)
    # then the fund's, half an hour later: that one download of copper and the dollar serves this decision too
    assert entries(at_1518, last_night, at_1448, last_night, at_1448) == bno
    assert entries(at_1518, last_night, at_1448, last_night) == bno | macro  # ... unless it never arrived
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-08"
    at_1548 = datetime(2026, 10, 8, 19, 48, tzinfo=UTC)
    assert entries(at_1548, at_1518, at_1448, last_night, at_1448) == set()  # nothing owed: only the bars
    assert entries(at_1548, at_1518, at_1448, last_night) == set()  # no decision, no copper: nobody would read it
    # once after the US close, for the official closes; then nothing until tomorrow's decisions
    at_1618 = datetime(2026, 10, 8, 20, 18, tzinfo=UTC)
    assert entries(at_1618, at_1518, at_1448, last_night, at_1448) == bno | mcl
    assert entries(at_1618 + timedelta(minutes=30), at_1618, at_1618, at_1618, at_1448) == set()
    # the slow context tables are attempted once a day, and a red probe is asked again on the next tick
    assert entries(at_1618 + timedelta(minutes=30), at_1618, at_1618, last_night, at_1448) == context
    red = [
        *[{"source": n, "status": "red", "checked_at": at_1618.isoformat()} for n in sorted(bno)],
        *health(None, at_1618, at_1618, at_1448),
    ]
    # a table whose last download failed: asked again an hour later, or at once if a decision is waiting on it
    assert jobs._desk_daily_entries(_runner(tmp_path, red), desk, at_1618 + timedelta(minutes=30))[0] == set()
    at_1718 = at_1618 + timedelta(minutes=60)
    got, why = jobs._desk_daily_entries(_runner(tmp_path, red), desk, at_1718)
    assert got == bno and "BNO: ultimo tentativo non riuscito" in why
    desk.state.last_decision_day["prudente"] = "2026-10-07"
    assert jobs._desk_daily_entries(_runner(tmp_path, red), desk, at_1618 + timedelta(minutes=30))[0] == bno
    desk.state.last_decision_day["prudente"] = "2026-10-08"

    # Copper and the dollar are NOT downloaded in the evening, when their session has just ended: the row of
    # the day a table is read on is not that day's close (the live quote of another contract month until 17:00,
    # the first minutes of the next session after 18:00; measured on Yahoo, 5-9 October 2026). It is read the
    # day after, by the first decision that needs it - which on a Monday is Friday's close, final since Saturday.
    fresh_evening = (at_1618, at_1618, at_1618)
    for late in (at_1718, at_1718 + timedelta(hours=3), datetime(2026, 10, 9, 13, 48, tzinfo=UTC)):
        assert entries(late, *fresh_evening, at_1448) == set()
    friday_1448 = datetime(2026, 10, 9, 18, 48, tzinfo=UTC)
    got, why = jobs._desk_daily_entries(_runner(tmp_path, health(*fresh_evening, at_1448)), desk, friday_1448)
    assert got == mcl | macro and "per la decisione del 2026-10-09" in why
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-09"
    friday_close = datetime(2026, 10, 9, 20, 18, tzinfo=UTC)
    quiet = (friday_close, friday_close, friday_close)
    sunday = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)
    monday_morning = datetime(2026, 10, 12, 14, 18, tzinfo=UTC)
    for idle in (friday_close + timedelta(hours=1), sunday, monday_morning):
        assert not entries(idle, *quiet, friday_1448) & macro  # the weekend and Monday morning: not asked for
    monday_1448 = datetime(2026, 10, 12, 18, 48, tzinfo=UTC)
    monday_1518 = monday_1448 + timedelta(minutes=30)
    this_morning = (monday_morning, monday_morning, monday_morning)
    assert entries(monday_1448, *this_morning, friday_1448) == mcl | macro
    desk.state.last_decision_day["spinto"] = "2026-10-12"
    # A download that fails does not hold the decision back (the sleeve reads the close before, and says which),
    # but it is asked again at every tick while a decision of the day is still to be taken ...
    fresh = (monday_morning, monday_1448, monday_morning)
    failed = [
        *health(*fresh, None),
        *[{"source": n, "status": "red", "checked_at": monday_1448.isoformat()} for n in sorted(macro)],
    ]
    got, why = jobs._desk_daily_entries(_runner(tmp_path, failed), desk, monday_1518)
    assert got == bno | macro and "macro: ultimo tentativo non riuscito" in why
    # ... each table on its own: the dollar's download failed, copper's did not. Probing copper for both left
    # the dollar unasked, and the books on a dollar close two days old without anybody being told.
    one_failed = [
        *[row for row in health(*fresh, monday_1448) if row["source"] != "dxy_daily"],
        {"source": "dxy_daily", "status": "red", "checked_at": monday_1448.isoformat()},
    ]
    got, why = jobs._desk_daily_entries(_runner(tmp_path, one_failed), desk, monday_1518)
    assert got == bno | macro and "macro: ultimo tentativo non riuscito" in why
    one_old = [
        *[row for row in health(*fresh, monday_1448) if row["source"] != "dxy_daily"],
        {"source": "dxy_daily", "status": "green", "last_success_at": friday_1448.isoformat()},
    ]
    got, why = jobs._desk_daily_entries(_runner(tmp_path, one_old), desk, monday_1518)
    assert got == bno | macro and "macro: chiusure di rame e dollaro per la decisione del 2026-10-12" in why
    # ... and once every decision of the day has been taken, once an hour like any table nobody is waiting for
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-12"
    decided = (monday_1518, monday_1448, monday_morning)
    still_red = [
        *health(*decided, None),
        *[{"source": n, "status": "red", "checked_at": monday_1518.isoformat()} for n in sorted(macro)],
    ]
    assert jobs._desk_daily_entries(_runner(tmp_path, still_red), desk, monday_1518 + timedelta(minutes=30))[0] == set()
    got, why = jobs._desk_daily_entries(_runner(tmp_path, still_red), desk, monday_1518 + timedelta(minutes=60))
    assert macro <= got and "macro: ultimo tentativo non riuscito" in why
    # A desk that was down at a decision time catches the decision up the next morning, and downloads the two
    # tables for it then; the same day's own decision, in the afternoon, downloads them again: the close it
    # reads is yesterday's, and the morning's table was read before the sources had finished writing it.
    tuesday_morning = datetime(2026, 10, 13, 14, 18, tzinfo=UTC)
    tuesday_1448 = datetime(2026, 10, 13, 18, 48, tzinfo=UTC)
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-09"  # Monday's was missed
    up = (tuesday_morning, tuesday_morning, tuesday_morning)
    assert entries(tuesday_morning, *up, friday_1448) == macro
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-12"
    assert entries(tuesday_morning + timedelta(minutes=30), *up, tuesday_morning) == set()
    assert entries(tuesday_1448, *up, tuesday_morning) == mcl | macro
    # A day the stock exchange is shut (Thanksgiving, 26 November 2026) has no decision and asks for nothing;
    # copper and the dollar do print a close that day, and Friday's first decision is what reads it.
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-11-25"
    wednesday_1448 = datetime(2026, 11, 25, 19, 48, tzinfo=UTC)  # 14:48 New York, winter time
    thanksgiving = datetime(2026, 11, 26, 19, 48, tzinfo=UTC)
    black_friday = datetime(2026, 11, 27, 19, 48, tzinfo=UTC)
    calm = (thanksgiving, thanksgiving, thanksgiving)
    assert entries(thanksgiving, *calm, wednesday_1448) == set()
    assert entries(thanksgiving + timedelta(hours=3), *calm, wednesday_1448) == set()
    assert macro <= entries(black_friday, *calm, wednesday_1448)

    # A table added to a list since the last download (the inverse fund, when the short leg was added) has
    # never been asked for: it is downloaded at the next tick, not when the probe is next due - it alone, the
    # tables beside it keep their own times.
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-09"
    known = health(*this_morning, friday_1448)
    without_new = [row for row in known if row["source"] not in {"sco_daily", "dxy_daily"}]
    got, why = jobs._desk_daily_entries(_runner(tmp_path, without_new), desk, monday_morning)
    assert got == {"sco_daily"} | macro and "BNO: mai scaricate: sco_daily" in why and "macro: da scaricare" in why
    # ... and with the rest of its group when that is due on the same tick: a decision is not made to wait
    got, why = jobs._desk_daily_entries(_runner(tmp_path, without_new), desk, monday_1518)
    assert bno <= got and "BNO: decisione del 2026-10-12 da prendere" in why


def test_a_decision_time_that_passes_during_the_downloads_waits_for_the_next_tick(tmp_path, monkeypatch):
    """What to download is planned on the clock the tick starts with (14:59:52 New York: the fund's decision is
    not due, nothing is asked for it); the decisions are taken on the clock read after the downloads (15:00:03:
    it is due). With a row for today on file from this morning the fund's books decided on it."""
    from engine.core.config import RiskConfig
    from engine.desk.live import build_desk
    from engine.live.runner import LiveRunner
    from tests.synthetic import make_desk_raw_frames, save_raw_frame

    desk = build_desk(tmp_path / "plain", RiskConfig.load())
    before, after = datetime(2026, 10, 8, 18, 59, 52, tzinfo=UTC), datetime(2026, 10, 8, 19, 0, 3, tzinfo=UTC)
    waits = jobs._overtaken_by_the_clock(desk, before, after)
    assert set(waits) == {"BNO"} and "prossimo giro" in waits["BNO"]  # the future decided at 14:35, long ago
    assert jobs._overtaken_by_the_clock(desk, after, after + timedelta(seconds=9)) == {}
    assert set(jobs._overtaken_by_the_clock(desk, before - timedelta(hours=1), after)) == {"BNO", "MCL"}

    # the whole tick: tables read at 10:18 with today's running row in them, yesterday's decisions taken
    settings = _settings(tmp_path)
    runner = LiveRunner(settings)
    morning = datetime(2026, 10, 8, 14, 18, tzinfo=UTC)
    for entry, frame in make_desk_raw_frames(end=date(2026, 10, 8)).items():
        save_raw_frame(runner.raw, entry, frame, fetched_at=morning)
    names = {"insider_form4", "uso_options", *jobs.DESK_CONTEXT_ENTRIES, *jobs.DESK_MACRO_ENTRIES}
    names |= {name for group in jobs.DESK_VEHICLE_ENTRIES.values() for name in group}
    rows = [{**_source(name, "green"), "last_success_at": morning.isoformat()} for name in sorted(names)]
    runner.store.write_json("health.json", {"overall": "green", "sources": rows})
    desk = build_desk(settings.state_dir, runner.risk)
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-07"
    desk.save()
    clock = iter([before, after])
    monkeypatch.setattr(jobs, "now_utc", lambda: next(clock, after))
    monkeypatch.setattr(jobs, "_fetch", lambda r, g, e=None: {"ok": 0, "failed": 0, "overall": "green", "sources": []})
    monkeypatch.setattr(jobs, "_desk_backtest_stale", lambda *a, **k: "")
    outcome = jobs.tick(runner, "t-race", legacy=False)
    assert outcome.status == "ok"
    assert [d["book"] for d in outcome.detail["desk"]["decisions"]] == []  # nobody decided on this morning's row
    assert any("prossimo giro" in note for note in outcome.detail["desk"]["notes"])
    # the next tick starts after the decision time: it asks for the fund's tables, and then decides
    at_1518 = datetime(2026, 10, 8, 19, 18, tzinfo=UTC)
    asked: list[str] = []
    monkeypatch.setattr(jobs, "now_utc", lambda: at_1518)
    monkeypatch.setattr(
        jobs,
        "_fetch",
        lambda r, g, e=None: asked.extend(sorted(e or ())) or {"ok": 0, "failed": 0, "overall": "green", "sources": []},
    )
    second = jobs.tick(runner, "t-race-2", legacy=False)
    assert set(jobs.DESK_VEHICLE_ENTRIES["BNO"]) <= set(asked)
    assert {d["book"] for d in second.detail["desk"]["decisions"]} >= {"prudente", "dinamico"}


def test_the_fetcher_downloads_single_tables_by_name_without_the_rest_of_their_group(tmp_path, monkeypatch):
    from engine.data.fetch import EntryResult
    from engine.data.quality import Health, SourceHealth

    settings = _settings(tmp_path)
    state = StateStore(settings.state_dir)
    fetcher = Fetcher(settings, RawStore(settings.state_dir), state)
    asked: list[tuple[str, str]] = []

    def fake(group: str, entry: str, chain: list, asof: datetime) -> EntryResult:
        asked.append((group, entry))
        return EntryResult(group, entry, SourceHealth(source=entry, status=Health.RED, checked_at=asof))

    def no_legacy_intraday(asof: datetime) -> EntryResult:
        raise AssertionError("the first system's intraday table belongs to a full download of the price group")

    monkeypatch.setattr(fetcher, "run_entry", fake)
    monkeypatch.setattr(fetcher, "fetch_intraday", no_legacy_intraday)
    state.write_json(
        "health.json",
        {"sources": [{"source": "bno_daily", "status": "green", "last_success_at": "2026-10-07T20:20:00Z"}]},
    )
    fetcher.run_all(["desk_intraday"], {"bno_daily", "wti_front"})
    assert asked == [
        ("desk_intraday", "bno_intraday"),
        ("desk_intraday", "sco_intraday"),
        ("desk_intraday", "cl_intraday"),
        ("desk_intraday", "bz_intraday"),
        ("prices", "wti_front"),
        ("desk", "bno_daily"),
    ]
    asked.clear()
    fetcher.run_all(None, {"hormuz"})  # tables only, no whole group
    assert asked == [("desk", "hormuz")]
    # a failed download does not erase the memory of the last good one
    by_name = {s["source"]: s for s in state.read_json("health.json")["sources"]}
    assert by_name["bno_daily"]["status"] == "red" and by_name["bno_daily"]["last_success_at"] == "2026-10-07T20:20:00Z"


def test_the_fetch_summary_is_small_and_names_what_failed():
    report = {
        "ok": 5,
        "failed": 1,
        "overall": "yellow",
        "sources": [_source("a", "green"), _source("b", "red")],
        "groups": {"x": {}},
    }
    out = jobs._fetch_summary(report, ["prices"])
    assert out == {"groups": ["prices"], "ok": 5, "failed": 1, "overall": "yellow", "red": ["b"]}
    assert jobs._fetch_summary({"error": "rete assente"}, ["prices"]) == {"groups": ["prices"], "error": "rete assente"}


def test_intraday_tables_are_not_judged_as_daily_series():
    idx = pd.date_range("2026-10-05 13:30", periods=60, freq="30min", tz="UTC")
    rng = np.random.default_rng(1)
    close = 60.0 * np.exp(np.cumsum(rng.normal(0, 0.001, size=60)))
    close[13:] *= 1.10  # an overnight gap of 10 %: normal between two sessions, an outlier inside a daily series
    frame = pd.DataFrame({"close": close, "published_at": idx}, index=idx)
    now = idx[-1].to_pydatetime() + timedelta(minutes=30)
    assert quality_issues("bno_intraday", frame, now) == []
    assert any("anomala" in str(i.message) for i in quality_issues("bno_daily", frame, now))


def test_the_intraday_symbol_is_a_contract_never_the_continuous_front():
    assert front_contract_symbol(datetime(2026, 10, 8, tzinfo=UTC)) == "BZZ26.NYM"
    # in the last days of a contract the session has already moved to the next one
    assert front_contract_symbol(datetime(2026, 10, 29, tzinfo=UTC)) == "BZF27.NYM"


# ----------------------------------------------------------------------------------------------- raw archive
def _snapshot(store: RawStore, entry: str, when: datetime) -> None:
    frame = pd.DataFrame({"close": [1.0], "published_at": [pd.Timestamp(when)]}, index=pd.DatetimeIndex([when]))
    store.save(entry, "sintetico", FetchResult("sintetico", frame, when))


def test_the_tick_trims_what_it_downloads(tmp_path):
    runner = _runner(tmp_path, [])
    start = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)  # a Monday
    for entry in ("bno_intraday", "uso_options", "bno_daily"):
        for i in range(30):  # two snapshots a day for three weeks
            _snapshot(runner.raw, entry, start + timedelta(hours=12 * i))
    deleted = jobs._compact_raw(runner, {"sources": [{"source": "bno_daily"}]})
    kept = {
        entry: len(runner.raw.history(entry, "sintetico")) for entry in ("bno_intraday", "uso_options", "bno_daily")
    }
    assert kept["bno_intraday"] == 1  # yesterday's bars are inside today's download
    assert kept["uso_options"] == 4  # the newest plus the first of each of three ISO weeks: the history of real quotes
    assert kept["bno_daily"] == 5  # the last two plus the three weekly firsts
    assert deleted == 90 - sum(kept.values())
    for entry in kept:
        assert runner.raw.load_latest(entry, "sintetico") is not None
    assert jobs._compact_raw(runner, {}) == 0  # nothing left to trim


# ----------------------------------------------------------------------------------------------- 4. insider
def test_a_bad_price_check_survives_a_table_with_several_rows_a_day():
    """It crashed (float() of a Series) on exactly the tables that have duplicate dates by design."""
    from engine.data.quality import check_nonpositive

    idx = pd.DatetimeIndex(pd.to_datetime(["2026-10-01", "2026-10-01", "2026-10-02"]))
    issues = check_nonpositive(pd.Series([0.0, 12.5, -1.0], index=idx), "t", "price")
    assert [i.extra["value"] for i in issues] == [0.0, -1.0]
    # ... and a Form 4 grant at price 0 is not an error at all
    frame = load_fixture(str(INSIDER_FIXTURE))
    assert (frame["price"] == 0).any()
    assert not [
        i for i in quality_issues("insider_form4", frame, datetime(2026, 10, 8, tzinfo=UTC)) if i.kind == "nonpositive"
    ]


def test_the_form4_table_is_read_back_into_market_data(tmp_path):
    settings = _settings(tmp_path)
    store = RawStore(settings.state_dir)
    assert _insider_table(Loader(store, settings))[0].empty  # nothing archived: an empty table, not an error
    frame = load_fixture(str(INSIDER_FIXTURE))
    store.save(
        "insider_form4",
        "insider_form4",
        FetchResult("insider_form4", frame, datetime(2026, 10, 5, tzinfo=UTC), approx=True),
    )
    table, sources = _insider_table(Loader(store, settings))
    assert len(table) == len(frame) > 100 and sources == {"insider": "insider_form4"}
    assert table.index.name == "transaction_date" and str(table["published_at"].dt.tz) == "UTC"
    assert {"ticker", "side", "open_market", "ten_percent_owner", "value_usd"} <= set(table.columns)
    md = build_market_data(store, settings)
    assert len(md.insider) == len(frame) and md.meta["source"]["insider"] == "insider_form4"
    # the point-in-time cut is on the FILING deadline, not on the transaction date
    cut = md.truncate(datetime(2020, 1, 1, tzinfo=UTC))
    assert 0 < len(cut.insider) < len(md.insider)
    assert (pd.to_datetime(cut.insider["published_at"], utc=True) <= pd.Timestamp("2020-01-01", tz="UTC")).all()


def test_a_throttled_download_keeps_what_was_known_about_the_names_that_did_not_answer(tmp_path):
    settings = _settings(tmp_path)
    store = RawStore(settings.state_dir)
    full = load_fixture(str(INSIDER_FIXTURE))
    tickers = sorted(full["ticker"].unique())
    assert len(tickers) == 2
    store.save("insider_form4", "insider_form4", FetchResult("insider_form4", full, datetime(2026, 10, 1, tzinfo=UTC)))
    only_one = full[full["ticker"] == tickers[0]]
    fetcher = Fetcher(settings, store, StateStore(settings.state_dir))
    merged = fetcher._merge_by_ticker(
        "insider_form4", FetchResult("insider_form4", only_one, datetime(2026, 10, 8, tzinfo=UTC))
    )
    assert sorted(merged["ticker"].unique()) == tickers and len(merged) == len(full)
    assert merged.index.is_monotonic_increasing


# ----------------------------------------------------------------------------------------------- the whole tick
def test_a_tick_decides_once_and_is_idempotent_from_the_state_on_disk(tmp_path, monkeypatch):
    """End to end on SYNTHETIC tables, with the downloads replaced by a stub: a tick takes the day's decisions,
    writes the books, and a second tick in the same half hour changes nothing."""
    from engine.live.runner import LiveRunner
    from tests.synthetic import make_desk_raw_frames, save_raw_frame

    settings = _settings(tmp_path)
    runner = LiveRunner(settings)
    for entry, frame in make_desk_raw_frames(end=date(2026, 10, 8)).items():
        save_raw_frame(runner.raw, entry, frame)
    now = datetime(2026, 10, 8, 19, 18, tzinfo=UTC)  # 15:18 New York: both decision times have passed
    names = {"insider_form4", *jobs.DESK_CONTEXT_ENTRIES, *jobs.DESK_MACRO_ENTRIES}
    names |= {name for group in jobs.DESK_VEHICLE_ENTRIES.values() for name in group}
    green = [_source(name, "green") for name in sorted(names)]
    for entry in green:
        entry["last_success_at"] = entry["checked_at"] = now.isoformat()
    runner.store.write_json("health.json", {"overall": "green", "sources": green})
    fetched: list[list[str]] = []
    monkeypatch.setattr(
        jobs,
        "_fetch",
        lambda r, groups, entries=None: (
            fetched.append([*groups, *sorted(entries or ())])
            or {"ok": 0, "failed": 0, "overall": "green", "sources": []}
        ),
    )
    monkeypatch.setattr(jobs, "now_utc", lambda: now)

    first = jobs.tick(runner, "t-1", legacy=False)
    assert first.status == "ok" and first.exit_code == 0
    decisions = first.detail["desk"]["decisions"]
    assert [d["book"] for d in decisions] == ["prudente", "dinamico"]
    # the futures book has a forecast but no price for the contract it would hold (no per-contract table in
    # this archive): it does not guess one from another contract, it waits and says so
    assert any("nessun prezzo recente per CL" in note for note in first.detail["desk"]["notes"])
    # no option chain in this archive: the options book has nothing to decide on and its decision stays owed
    assert first.detail["options"]["decisions"] == [] and first.detail["options"]["open"] == 0
    # the daily tables were fresh, so only the per-tick bars are downloaded - plus the option chains, because
    # the options book owes its decision on this tick and trades only on quotes it has just read
    assert fetched == [["desk_intraday", "options"]]
    assert (settings.state_dir / "desk" / "prudente" / "account.json").exists()
    assert (settings.state_dir / "desk" / "opzioni" / "book.json").exists()
    # a desk that never had a backtest computes it on its first tick, so the terminal is whole from day one;
    # after that it is the weekly job's
    assert first.detail["desk_backtest"] == "calcolato: mancava"
    assert (settings.state_dir / "desk" / "backtest.json").exists()

    assert jobs.tick(runner, "t-1", legacy=False).status == "skipped"  # the same run, replayed
    again = jobs.tick(runner, "t-2", legacy=False)
    assert (
        again.status == "ok" and again.detail["desk"]["decisions"] == [] and again.detail["options"]["decisions"] == []
    )
    assert "desk_backtest" not in again.detail
    # the backtest on file carries the fingerprint of the rules it was computed with: one computed under
    # other rules (a merged change of sleeves, books or vehicles) is replaced at the next tick, not shown
    backtest_file = settings.state_dir / "desk" / "backtest.json"
    on_file = json.loads(backtest_file.read_text(encoding="utf-8"))
    current = on_file["meta"]["model"]
    assert len(current) == 12
    on_file["meta"]["model"] = "regole-vecchie"
    backtest_file.write_text(json.dumps(on_file), encoding="utf-8")
    changed = jobs.tick(runner, "t-2b", legacy=False)
    assert changed.detail["desk_backtest"] == "calcolato: le regole sono cambiate"
    assert json.loads(backtest_file.read_text(encoding="utf-8"))["meta"]["model"] == current
    assert "desk_backtest" not in jobs.tick(runner, "t-2c", legacy=False).detail
    # ... and so is one that was computed while a table was missing, once the table has arrived: a replay
    # that ran without the inverse fund's history has no short side in it
    assert "sco_daily" in on_file["meta"]["missing"]  # this archive has none
    assert jobs._desk_backtest_stale(runner, set(on_file["meta"]["missing"])) == ""
    arrived = jobs._desk_backtest_stale(runner, set(on_file["meta"]["missing"]) - {"sco_daily"})
    assert arrived == "sono arrivate tabelle che mancavano (sco_daily)"

    # The check before the replay can fail as well as the replay: a file cut short by a crash is a file that
    # is not there, and anything else that goes wrong while looking (a configuration that does not load) is
    # reported in the tick's detail. Neither costs the books their tick.
    whole = backtest_file.read_text(encoding="utf-8")
    backtest_file.write_text(whole[: len(whole) // 2], encoding="utf-8")
    assert jobs._desk_backtest_stale(runner, set()) == "mancava"
    backtest_file.write_text(whole, encoding="utf-8")

    def unreadable(*_args, **_kwargs):
        raise RuntimeError("configurazione illeggibile")

    with monkeypatch.context() as patch:
        patch.setattr(jobs, "_desk_backtest_stale", unreadable)
        broken = jobs.tick(runner, "t-2d", legacy=False)
    assert broken.status == "ok" and broken.exit_code == 0
    assert broken.detail["desk_backtest"] == "non riuscito: configurazione illeggibile"
    assert set(broken.detail["desk"]["books"]) == {"prudente", "dinamico", "spinto"}

    # a red fund table blocks the fund's books and nothing else
    others = [row for row in green if row["source"] != "bno_daily"]
    runner.store.write_json("health.json", {"overall": "red", "sources": [_source("bno_daily", "red"), *others]})
    monkeypatch.setattr(jobs, "now_utc", lambda: now + timedelta(days=1))
    blocked = jobs.tick(runner, "t-3", legacy=False)
    assert blocked.status == "ok" and any("bno_daily" in note for note in blocked.detail["desk"]["notes"])
    assert blocked.detail["desk"]["decisions"] == []
    assert set(blocked.detail["desk"]["books"]) == {"prudente", "dinamico", "spinto"}  # still marked, all of them
