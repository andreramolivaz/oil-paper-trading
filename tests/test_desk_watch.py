"""The unusual-activity alert on the prediction markets (engine/desk/watch.py): the rule on a real reading, the
footprint on real trades, the alert written once, the issue it becomes.

Fixtures: the runner's reading of 2026-10-14 03:03 UTC with the 24-hour fields
(``tests/fixtures/kalshi_markets_2026-10-14T03.json``) - a quiet day by the rule - and a week of real trades on
the strait ladder, the US-Iran deal and one WTI strike (``tests/fixtures/kalshi_trades_2026-10-14.json``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from engine.core.store import StateStore
from engine.data.adapters.kalshi import parse_markets, parse_trades, to_frame
from engine.data.base import FetchResult
from engine.data.raw_store import RawStore
from engine.desk import prediction as pm
from engine.desk import watch as w

MARKETS = Path(__file__).parent / "fixtures" / "kalshi_markets_2026-10-14T03.json"
TRADES = Path(__file__).parent / "fixtures" / "kalshi_trades_2026-10-14.json"
READ_AT = pd.Timestamp("2026-10-14T03:03:00Z")
NOW = datetime(2026, 10, 14, 3, 18, tzinfo=UTC)
MEETING = "KXUSIRANMEETING-27-26NOV01"  # a thin event market: 2 780 contracts of open interest


def markets(series: list[str] | None = None) -> dict[str, list[dict]]:
    doc = json.loads(MARKETS.read_text())["series"]
    return {k: v for k, v in doc.items() if series is None or k in series}


def reading(series: list[str] | None = None, patch: dict[str, dict] | None = None, at: pd.Timestamp = READ_AT):
    rows: list[dict] = []
    for ticker, ms in markets(series).items():
        ms = [dict(m, **(patch or {}).get(m["ticker"], {})) for m in ms]
        rows += parse_markets(ms, ticker, at)
    return pm.Reading(to_frame(rows).reset_index(drop=True), at)


def trades_of(ticker: str) -> list[dict]:
    return parse_trades(json.loads(TRADES.read_text())["trades"][ticker], ticker)


# ----------------------------------------------------------------------------------------------- the fields
def test_the_reading_carries_the_24_hour_fields_and_the_previous_mid():
    frame = reading().frame
    row = frame[frame["ticker"] == "KXHORMUZNORM-26MAR17-B270101"].iloc[0]
    assert (row["yes_bid"], row["yes_ask"]) == (0.061, 0.064)
    assert (row["prev_yes_bid"], row["prev_yes_ask"], row["prev_price"]) == (0.099, 0.10, 0.10)
    assert row["volume_24h"] == 54181.34 and row["open_interest"] == 1253239.51
    assert row["yes_bid_size"] == 25.0 and row["yes_ask_size"] == 16.28
    view = pm._market_view({str(k): v for k, v in row.items()})
    assert view["mid"] == 0.0625 and view["mid_24h_ago"] == 0.0995 and view["change_24h"] == -0.037
    assert view["volume_24h"] == 54181.34
    # the daily archive records the 24h volume and the previous mid (the baselines read the first)
    rec = pm._event_records(reading(["KXHORMUZNORM"]))[0]
    assert rec["columns"][-2:] == ["volume_24h", "prev_mid"]
    first = dict(zip(rec["columns"], rec["markets"][0], strict=True))
    assert first["ticker"] == "KXHORMUZNORM-26MAR17-B261101" and first["volume_24h"] == 181505.54
    assert first["prev_mid"] == 0.015


# ----------------------------------------------------------------------------------------------- the rule
def test_the_move_condition_counts_points_or_odds():
    assert w.move_is_large(0.30, 0.18) == (True, "+12% in 24 hours")
    assert w.move_is_large(0.09, 0.03)[0] is True  # 6 points, the odds x 3.2
    assert w.move_is_large(0.03, 0.09)[0] is True  # and the way down
    assert w.move_is_large(0.51, 0.45)[0] is False  # 6 points, the odds x 1.3
    assert w.move_is_large(0.0625, 0.0995)[0] is False  # the Jan 1 2027 strait market that day: -3.7 points
    assert w.move_is_large(0.30, None) == (False, "no quote a day ago")


def test_the_volume_condition_uses_the_market_baseline_or_its_open_interest():
    assert w.volume_is_unusual(300.0, 2780.0, None)[0] is False  # under the floor
    ok, text = w.volume_is_unusual(1200.0, 2780.0, None)
    assert ok is True and "43% of the open interest (no baseline yet)" in text
    assert w.volume_is_unusual(600.0, 2780.0, None)[0] is False  # 22 % of the open interest
    base = w.Baseline(days=20, median_volume=150.0, p90_volume=700.0)
    ok, text = w.volume_is_unusual(1200.0, 2780.0, base)
    assert ok is True and "8.0x the usual day (20 days)" in text
    assert w.volume_is_unusual(650.0, 2780.0, base)[0] is False  # 4.3x the median but under the 90th percentile
    assert w.volume_is_unusual(None, 2780.0, base) == (False, "no 24-hour volume")
    ok, text = w.volume_is_unusual(1200.0, 2780.0, None, age_hours=20.0)
    assert ok is False and text.endswith("on a market open for 20 hours")
    assert w.volume_is_unusual(1200.0, 2780.0, base, age_hours=20.0)[0] is True  # a baseline needs no age


def test_the_real_reading_flags_two_ladders_and_ignores_the_yield_trade_and_the_price_ladders():
    """The reading of 03:03 UTC: 181 506 contracts in 24 hours on "strait normal before Nov 1" at 1-2 cents (a
    yield trade, no move: not flagged); the WTI ladders of the 14th up 30-50 points with the oil price (price
    ladders: out of scope); the EIA inventories ladder down 10-24 points the evening the API's own report came
    out (three strikes, the whole ladder moved); one strike of the week's transit ladder down 12 points on twice
    its open interest, the rest flat. Two alerts, one per event."""
    events = w.flagged_events(reading(), {})
    assert [(e["event"], len(e["markets"]), e["n_markets"], e["rest_flat"]) for e in events] == [
        ("KXEIACRUDEW-26OCT14", 3, 13, False),
        ("KXHORMUZWEEKLY-26OCT18", 1, 12, True),
    ]
    eia = events[0]
    assert [m["ticker"] for m in eia["markets"]] == [
        "KXEIACRUDEW-26OCT14-T422",
        "KXEIACRUDEW-26OCT14-T428",
        "KXEIACRUDEW-26OCT14-T429",
    ]  # the busiest first
    assert eia["markets"][0]["move"] == "-24% in 24 hours" and eia["markets"][0]["volume"].startswith("1,312 contracts")
    assert (
        eia["title"]
        == "Will U.S. commercial crude oil inventories for the week ending Oct 9, 2026 be above 418 million barrels?"
    )
    text = w.alert_text({**eia, "footprint": None})
    assert text.startswith("Supply · Will U.S. commercial crude oil inventories")
    assert "3 of 13 markets moved 10%-24% in 24 hours (Above 422 million barrels 68% -> 44%;" in text
    assert text.endswith("on the busiest; the whole ladder moved too.")
    strait = events[1]
    assert strait["markets"][0]["label"] == "Above 25" and strait["rest_of_ladder_move"] < w.LADDER_FLAT
    assert w.alert_text({**strait, "footprint": None}).endswith("the rest of the ladder is flat.")
    # the strait's "normal before" ladder: nothing (the Jan 1 2027 market fell 3.7 points on 54 000 contracts)
    assert w.flagged_events(reading(["KXHORMUZNORM", "KXUSAIRANAGREEMENT", "KXUSIRANMEETING"]), {}) == []


def test_a_large_move_on_unusual_volume_in_a_thin_event_market_is_flagged_with_its_scope():
    # the US-Iran meeting before Nov 1: 13.5 % a day ago (bid 0.10, ask 0.17), now 31 %, on 1 200 contracts
    # (43 % of the open interest, a market open since 24 September)
    patch = {MEETING: {"yes_bid_dollars": "0.3000", "yes_ask_dollars": "0.3200", "volume_24h_fp": "1200.00"}}
    (e,) = w.flagged_events(reading(["KXUSIRANMEETING"], patch), {})
    assert e["event"] == "KXUSIRANMEETING-27" and e["group"] == "US-Iran" and e["n_markets"] == 6
    assert e["title"] == "Will the U.S. and Iran hold a senior-level meeting?"
    (f,) = e["markets"]
    assert f["ticker"] == MEETING and f["mid"] == 0.31 and f["mid_24h_ago"] == 0.135 and f["change_24h"] == 0.175
    assert f["move"].startswith("+1") and f["move"].endswith("% in 24 hours")
    assert f["volume"].startswith("1,200 contracts in 24 hours, 43% of the open interest (no baseline yet)")
    assert e["rest_flat"] is True
    text = w.alert_text({**e, "footprint": None})
    assert text.startswith("US-Iran · Will the U.S. and Iran hold a senior-level meeting? - 1 of 6 markets moved")
    assert "(Before Nov 1, 2026 14% -> 31%)" in text and text.endswith("the rest of the ladder is flat.")
    # the same move with the whole ladder moving along: not flat
    ladder = {
        m["ticker"]: {"yes_bid_dollars": "0.9000", "yes_ask_dollars": "0.9200"}
        for m in markets(["KXUSIRANMEETING"])["KXUSIRANMEETING"]
    }
    ladder[MEETING] = patch[MEETING]
    (e2,) = w.flagged_events(reading(["KXUSIRANMEETING"], ladder), {})
    assert e2["rest_flat"] is False and len(e2["markets"]) == 1  # the others moved on no volume
    # the same move on a day's volume that is nothing for the market: no flag
    quiet = {MEETING: {"yes_bid_dollars": "0.3000", "yes_ask_dollars": "0.3200", "volume_24h_fp": "400.00"}}
    assert w.flagged_events(reading(["KXUSIRANMEETING"], quiet), {}) == []
    # a market listed yesterday: its first day is all its volume, no yardstick
    young = {MEETING: {**patch[MEETING], "open_time": "2026-10-13T02:00:00Z"}}
    assert w.flagged_events(reading(["KXUSIRANMEETING"], young), {}) == []
    # a price ladder is never flagged, whatever it does
    jump = {
        "KXBRENTW-26OCT1617-T103.99": {
            "yes_bid_dollars": "0.9000",
            "yes_ask_dollars": "0.9200",
            "volume_24h_fp": "50000.00",
        }
    }
    assert w.flagged_events(reading(["KXBRENTW"], jump), {}) == []


def test_the_baselines_come_from_the_daily_archive_after_ten_days(tmp_path):
    store = StateStore(tmp_path)
    for i in range(12):
        at = READ_AT - pd.Timedelta(days=12 - i)
        patch = {MEETING: {"volume_24h_fp": f"{100 + 10 * i}.00"}}
        pm.record_reading(store, reading(["KXUSIRANMEETING", "KXHORMUZNORM"], patch, at))
    bases = w.baselines(store)
    assert bases[MEETING].days == 12 and bases[MEETING].median_volume == pytest.approx(155.0)
    assert bases[MEETING].p90_volume == pytest.approx(199.0)
    assert "KXHORMUZNORM-26MAR17-B261101" in bases
    # a market with nine records has none; an archive record without the column (before the alert) is skipped
    store2 = StateStore(tmp_path / "short")
    for i in range(9):
        pm.record_reading(store2, reading(["KXUSIRANMEETING"], at=READ_AT - pd.Timedelta(days=9 - i)))
    store2.append_jsonl(
        pm.KALSHI_LOG,
        {
            "key": "2026-09-01|x",
            "day": "2026-09-01",
            "columns": list(pm.LOG_COLUMNS[:10]),
            "markets": [[MEETING] + [None] * 9],
        },
        ts=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert w.baselines(store2) == {}


# ----------------------------------------------------------------------------------------------- the footprint
def test_the_footprint_reads_concentration_side_and_timing_off_real_trades():
    since = datetime(2026, 10, 13, 3, 3, tzinfo=UTC)
    # the Nov 1 market: twenty trades, one of 100 000 contracts at a cent, the takers on NO (buying NO at 0.99)
    fp = w.footprint(trades_of("KXHORMUZNORM-26MAR17-B261101"), since)
    assert fp["trades"] == 20 and fp["contracts"] == pytest.approx(181505.54, abs=1)
    assert fp["top_share"] > 0.98 and fp["side"] == "no" and fp["side_share"] > 0.95
    assert fp["largest"]["contracts"] == 100000.0 and fp["largest"]["yes_price"] == 0.01
    assert fp["largest"]["at"].startswith("2026-10-13T10:40")
    assert fp["price_path"] == [0.02, 0.02]
    # the sweep of the 13th: one taker selling YES across the ladder's 2027 dates within two minutes
    fp = w.footprint(trades_of("KXHORMUZNORM-26MAR17-B270801"), since)
    assert fp["trades"] == 3 and fp["side"] == "no" and fp["side_share"] == 1.0
    assert {k: v for k, v in fp["largest"].items() if k != "at"} == {
        "contracts": 850.0,
        "yes_price": 0.39,
        "side": "no",
    }
    assert fp["largest"]["at"].startswith("2026-10-13T15:55:46")
    text = w.footprint_text(fp)
    assert text.startswith("3 trades, 1,436 contracts; the 5 largest carry 100%; 100% with the taker on NO")
    # a busy WTI strike: many small trades, no concentration
    fp = w.footprint(trades_of("KXWTI-26OCT1414-T93.99"), since)
    assert fp["trades"] == 132 and fp["top_share"] < 0.3
    assert w.footprint([], since) == {"trades": 0, "note": "no trade in the window"}
    assert w.footprint_text({}) == "no trade read in the window"


# ----------------------------------------------------------------------------------------------- the alerts
def _store_with_reading(tmp_path, patch=None, at=READ_AT, series=None):
    raw, store = RawStore(tmp_path / "raw"), StateStore(tmp_path / "desk")
    r = reading(series, patch, at)
    raw.save(
        "kalshi",
        "kalshi",
        FetchResult("kalshi", r.frame.set_index(pd.DatetimeIndex([at] * len(r.frame), name="ts")), at.to_pydatetime()),
    )
    return raw, store


def test_the_watch_judges_a_reading_once_and_writes_one_alert_per_event_and_day(tmp_path):
    patch = {MEETING: {"yes_bid_dollars": "0.3000", "yes_ask_dollars": "0.3200", "volume_24h_fp": "1200.00"}}
    raw, store = _store_with_reading(tmp_path, patch)
    calls: list[tuple[str, int]] = []

    def reader(ticker: str, min_ts: int) -> list[dict]:
        calls.append((ticker, min_ts))
        return trades_of(MEETING)

    out = w.watch(raw, store, NOW, fetch_trades=reader)
    assert out["status"] == "ok" and out["flagged"] == 3 and out["alerts_written"] == 3
    assert out["events"] == ["KXEIACRUDEW-26OCT14", "KXHORMUZWEEKLY-26OCT18", "KXUSIRANMEETING-27"]
    # the trades of the busiest flagged market of each event, over the 24 hours before the reading
    since = int((READ_AT - pd.Timedelta(hours=24)).timestamp())
    assert calls == [("KXEIACRUDEW-26OCT14-T422", since), ("KXHORMUZWEEKLY-26OCT18-T25", since), (MEETING, since)]
    rows = store.read_jsonl(w.ALERTS_LOG)
    assert len(rows) == 3
    a = next(r for r in rows if r["event"] == "KXUSIRANMEETING-27")
    assert a["key"] == "2026-10-14|KXUSIRANMEETING-27" and a["ts"] == "2026-10-14T03:03:00Z"
    assert a["footprint"]["trades"] >= 1 and a["footprint_note"] is None and a["footprint_ticker"] == MEETING
    assert a["text"].startswith("US-Iran · Will the U.S. and Iran hold a senior-level meeting? - 1 of 6 markets moved")
    assert "the rest of the ladder is flat. Trades on KXUSIRANMEETING-27-26NOV01:" in a["text"]
    # the same reading again: judged once
    assert w.watch(raw, store, NOW + timedelta(minutes=30), fetch_trades=reader)["status"] == "already judged"
    # a later reading the same day with the move still there: no second alert (one per event and day)
    later = READ_AT + pd.Timedelta(hours=1)
    raw2, _ = _store_with_reading(tmp_path / "again", patch, later)
    out2 = w.watch(raw2, store, NOW + timedelta(hours=1), fetch_trades=reader)
    assert out2["flagged"] == 3 and out2["alerts_written"] == 0 and len(store.read_jsonl(w.ALERTS_LOG)) == 3
    # the next day it is news again
    tomorrow = READ_AT + pd.Timedelta(days=1)
    raw3, _ = _store_with_reading(tmp_path / "tomorrow", patch, tomorrow)
    assert w.watch(raw3, store, NOW + timedelta(days=1), fetch_trades=reader)["alerts_written"] == 3
    assert store.read_json(w.WATCH_FILE)["flagged"][-1] == "KXUSIRANMEETING-27"


def test_an_alert_stands_without_its_footprint_when_the_trades_cannot_be_read(tmp_path):
    patch = {MEETING: {"yes_bid_dollars": "0.3000", "yes_ask_dollars": "0.3200", "volume_24h_fp": "1200.00"}}
    raw, store = _store_with_reading(tmp_path, patch, series=["KXUSIRANMEETING"])

    def broken(ticker: str, min_ts: int) -> list[dict]:
        raise RuntimeError("HTTP 429")

    assert w.watch(raw, store, NOW, fetch_trades=broken)["alerts_written"] == 1
    a = store.read_jsonl(w.ALERTS_LOG)[0]
    assert a["footprint"] is None and a["footprint_note"] == "trades not read: HTTP 429"
    assert a["text"].endswith("the rest of the ladder is flat.")
    # and with no reader at all (offline settings) the note says so
    raw2, store2 = _store_with_reading(tmp_path / "offline", patch, series=["KXUSIRANMEETING"])
    w.watch(raw2, store2, NOW)
    assert store2.read_jsonl(w.ALERTS_LOG)[0]["footprint_note"] == "trades not read (no reader)"
    assert w.watch(RawStore(tmp_path / "empty"), store2, NOW) == {"status": "no reading on file"}


def test_recent_alerts_and_the_issues_they_become(tmp_path):
    from engine.monitoring.alerts import Alerter, prediction_market_alerts

    patch = {MEETING: {"yes_bid_dollars": "0.3000", "yes_ask_dollars": "0.3200", "volume_24h_fp": "1200.00"}}
    raw, store = _store_with_reading(tmp_path / "state", patch, series=["KXUSIRANMEETING"])
    state = StateStore(tmp_path / "state")  # the first system's store: the desk's is its "desk" folder
    w.watch(raw, store, NOW)
    assert [a["event"] for a in w.recent_alerts(store, NOW)] == ["KXUSIRANMEETING-27"]
    assert w.recent_alerts(store, NOW + timedelta(days=8)) == []
    issues = prediction_market_alerts(state, NOW)
    assert [i.key for i in issues] == ["kalshi-kxusiranmeeting-27"]
    assert issues[0].title == "Prediction market: KXUSIRANMEETING-27" and "Not a verdict" in issues[0].body
    assert issues[0].full_title.endswith("(kalshi-kxusiranmeeting-27)")
    assert prediction_market_alerts(state, NOW + timedelta(hours=49)) == []  # aged out: the alerter closes it
    # without gh the alerter only logs
    assert Alerter(None, state).raise_alerts(issues) == {issues[0].key: "logged"}


# ----------------------------------------------------------------------------------------------- the live path
def test_the_tick_reads_the_markets_every_hour_judges_them_and_the_export_shows_the_alerts(tmp_path, monkeypatch):
    from datetime import date

    from engine.core.config import Settings
    from engine.desk import export as ex
    from engine.live import jobs
    from engine.live.runner import LiveRunner
    from tests.synthetic import make_desk_raw_frames, save_raw_frame

    repo = Path(__file__).resolve().parents[1]
    settings = Settings(
        state_dir=tmp_path / "state",
        config_dir=repo / "config",
        eia_api_key=None,
        fred_api_key=None,
        github_repo="t/t",
        offline=True,
    )
    runner = LiveRunner(settings)
    for entry, frame in make_desk_raw_frames(end=date(2026, 10, 8)).items():
        save_raw_frame(runner.raw, entry, frame)
    now = datetime(2026, 10, 8, 21, 18, tzinfo=UTC)
    at = pd.Timestamp("2026-10-08T21:05:00Z")
    patch = {MEETING: {"yes_bid_dollars": "0.3000", "yes_ask_dollars": "0.3200", "volume_24h_fp": "1200.00"}}
    r = reading(["KXUSIRANMEETING", "KXHORMUZNORM"], patch, at)
    runner.raw.save(
        "kalshi",
        "kalshi",
        FetchResult("kalshi", r.frame.set_index(pd.DatetimeIndex([at] * len(r.frame), name="ts")), at.to_pydatetime()),
    )
    names = {"insider_form4", *jobs.DESK_CONTEXT_ENTRIES, *jobs.DESK_MACRO_ENTRIES}
    names |= {name for group in jobs.DESK_VEHICLE_ENTRIES.values() for name in group}
    stamp = now.isoformat()
    green = [{"source": n, "status": "green", "checked_at": stamp, "last_success_at": stamp} for n in names]
    runner.store.write_json("health.json", {"overall": "green", "sources": green})
    asked: list[list[str]] = []
    monkeypatch.setattr(
        jobs,
        "_fetch",
        lambda r, g, e=None: asked.append(sorted(e or ())) or {"ok": 0, "failed": 0, "overall": "green", "sources": []},
    )
    monkeypatch.setattr(jobs, "_desk_backtest_stale", lambda *a, **k: "")
    monkeypatch.setattr(jobs, "now_utc", lambda: now)

    outcome = jobs.tick(runner, "t-watch", legacy=False)
    assert outcome.status == "ok"
    assert "kalshi" in asked[0]  # never attempted: asked at once
    assert outcome.detail["prediction_watch"]["alerts_written"] == 1
    assert outcome.detail["prediction_watch"]["events"] == ["KXUSIRANMEETING-27"]
    # the planning: attempted half an hour ago, not asked; an hour ago, asked again
    green.append({"source": "kalshi", "status": "green", "checked_at": stamp, "last_success_at": stamp})
    runner.store.write_json("health.json", {"overall": "green", "sources": green})
    from engine.desk.live import build_desk

    desk = build_desk(runner.settings.state_dir, runner.risk)
    entries, why = jobs._desk_daily_entries(runner, desk, now + timedelta(minutes=30))
    assert "kalshi" not in entries
    entries, why = jobs._desk_daily_entries(runner, desk, now + timedelta(minutes=60))
    assert "kalshi" in entries and "kalshi: hourly" in why

    payload = ex.build_desk_payload(settings, runner.raw, runner.risk, runner.store, now)
    block = payload["prediction_markets"]
    assert [a["event"] for a in block["alerts"]] == ["KXUSIRANMEETING-27"]
    assert block["alerts"][0]["text"].startswith("US-Iran · Will the U.S. and Iran hold a senior-level meeting?")
    assert block["alerts"][0]["footprint_note"] == "trades not read (no reader)"  # offline settings
    assert block["watch"]["scope"].startswith("event markets only")
    # the ladder rows carry the 24-hour change
    strait = next(e for g in block["groups"] for e in g["events"] if e["series"] == "KXHORMUZNORM")
    jan = next(m for m in strait["markets"] if m["ticker"] == "KXHORMUZNORM-26MAR17-B270101")
    assert jan["mid_24h_ago"] == 0.0995 and jan["change_24h"] == -0.037 and jan["volume_24h"] == 54181.34
