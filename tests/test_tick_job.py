"""What the scheduler's tick decides to do, and the four causes of the flat account that it must not repeat.

1. a guard that says "too early" is not a failure (it used to paint the run red);
2. the end of day targets the last date that HAS settled, whatever time GitHub starts the job;
3. only a critical source can stop new risk (one optional table going red had halted the whole account);
4. the Form 4 table is read back after being fetched (it was archived and never loaded).
"""

from __future__ import annotations

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


def test_a_tick_downloads_only_the_daily_tables_its_decisions_need(tmp_path):
    """The whole "prices" group is about sixty requests to Yahoo and the desk reads three of its tables. Asking
    for all of it at every refresh got the address rate limited, and a rate limit costs the tick its bars."""
    from engine.core.config import RiskConfig
    from engine.desk.live import build_desk

    desk = build_desk(tmp_path / "state", RiskConfig.load())
    bno, mcl = set(jobs.DESK_VEHICLE_ENTRIES["BNO"]), set(jobs.DESK_VEHICLE_ENTRIES["MCL"])
    context = set(jobs.DESK_CONTEXT_ENTRIES)
    assert bno == {"bno_daily", "brent_front", "bz_contracts"} and mcl == {"cl_contracts", "wti_front", "uso_daily"}

    def health(bno_at: datetime | None, mcl_at: datetime | None, context_at: datetime | None) -> list[dict]:
        rows = []
        for name, when in (("bno_daily", bno_at), ("cl_contracts", mcl_at), ("hormuz", context_at)):
            if when is not None:
                rows.append({"source": name, "status": "green", "last_success_at": when.isoformat()})
        return rows

    def entries(now: datetime, *checked: datetime | None) -> set[str]:
        return jobs._desk_daily_entries(_runner(tmp_path, health(*checked)), desk, now)[0]

    morning = datetime(2026, 10, 8, 14, 18, tzinfo=UTC)  # 10:18 New York, Thursday
    assert entries(morning, None, None, None) == bno | mcl | context  # a first run downloads everything once
    # A new desk owes the previous session's decision. Tables read after last night's close can serve it...
    last_night = datetime(2026, 10, 7, 20, 20, tzinfo=UTC)
    assert entries(morning, last_night, last_night, last_night) == set()
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-07"
    # ...and at 14:48 only the futures book owes today's (it decides at 14:35), so only its tables are read
    at_1448 = datetime(2026, 10, 8, 18, 48, tzinfo=UTC)
    assert entries(at_1448, last_night, last_night, last_night) == mcl
    desk.state.last_decision_day["spinto"] = "2026-10-08"
    at_1518 = datetime(2026, 10, 8, 19, 18, tzinfo=UTC)
    assert entries(at_1518, last_night, at_1448, last_night) == bno  # then the fund's, half an hour later
    for book in desk.books.values():
        desk.state.last_decision_day[book.id] = "2026-10-08"
    at_1548 = datetime(2026, 10, 8, 19, 48, tzinfo=UTC)
    assert entries(at_1548, at_1518, at_1448, last_night) == set()  # nothing owed: only the half-hour bars
    # once after the US close, for the official closes; then nothing until tomorrow's decisions
    at_1618 = datetime(2026, 10, 8, 20, 18, tzinfo=UTC)
    assert entries(at_1618, at_1518, at_1448, last_night) == bno | mcl
    assert entries(at_1618 + timedelta(minutes=30), at_1618, at_1618, at_1618) == set()
    # the slow context tables are attempted once a day, and a red probe is asked again on the next tick
    assert entries(at_1618 + timedelta(minutes=30), at_1618, at_1618, last_night) == context
    red = [{"source": "bno_daily", "status": "red", "checked_at": at_1618.isoformat()}, *health(None, at_1618, at_1618)]
    # a table whose last download failed: asked again an hour later, or at once if a decision is waiting on it
    assert jobs._desk_daily_entries(_runner(tmp_path, red), desk, at_1618 + timedelta(minutes=30))[0] == set()
    got, why = jobs._desk_daily_entries(_runner(tmp_path, red), desk, at_1618 + timedelta(minutes=60))
    assert got == bno and "BNO: ultimo tentativo non riuscito" in why
    desk.state.last_decision_day["prudente"] = "2026-10-07"
    assert jobs._desk_daily_entries(_runner(tmp_path, red), desk, at_1618 + timedelta(minutes=30))[0] == bno


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
    green = [_source(name, "green") for name in ("bno_daily", "wti_front", "cl_contracts", "insider_form4", "hormuz")]
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

    # a red fund table blocks the fund's books and nothing else
    runner.store.write_json("health.json", {"overall": "red", "sources": [_source("bno_daily", "red"), *green[1:]]})
    monkeypatch.setattr(jobs, "now_utc", lambda: now + timedelta(days=1))
    blocked = jobs.tick(runner, "t-3", legacy=False)
    assert blocked.status == "ok" and any("bno_daily" in note for note in blocked.detail["desk"]["notes"])
    assert blocked.detail["desk"]["decisions"] == []
    assert set(blocked.detail["desk"]["books"]) == {"prudente", "dinamico", "spinto"}  # still marked, all of them
