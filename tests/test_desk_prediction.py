"""The prediction markets (Kalshi) on the desk: the adapter's frame, the daily archive, the terminal's block.

The fixture is the engine's own first live reading (the runner, 2026-10-14 01:22 UTC;
``tests/fixtures/kalshi_markets_2026-10-14.json``): price ladders on Brent and WTI (one of them a weekly ladder of
bins), deadline ladders on the strait and the US-Iran talks, threshold ladders on the EIA inventories and the ships
through the strait, written back in the API's own shape.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from engine.core.errors import DataUnavailable
from engine.core.store import StateStore
from engine.data.adapters.kalshi import COLUMNS, KalshiAdapter, parse_markets, to_frame
from engine.data.base import FetchResult
from engine.data.raw_store import RawStore
from engine.desk import prediction as pm

FIXTURE = Path(__file__).parent / "fixtures" / "kalshi_markets_2026-10-14.json"
READ_AT = pd.Timestamp("2026-10-14T01:22:57Z")
NOW = datetime(2026, 10, 14, 1, 30, tzinfo=UTC)
# the desk's own quotes that night (desk.json on the data branch after the first tick)
BRENT = {"symbol": "BZZ26", "price": 107.02, "asof": "2026-10-14T01:18:00Z"}
WTI = {"symbol": "CLX26", "price": 95.24, "asof": "2026-10-14T01:18:00Z"}
BNO_OPTIONS = {"symbol": "BNO", "atm_iv": 0.5134, "quote_ts": "2026-10-13T20:00:00Z", "price": 64.78}


def fixture_markets() -> dict[str, list[dict]]:
    return json.loads(FIXTURE.read_text())["series"]


def fixture_frame(series: list[str] | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    for ticker, markets in fixture_markets().items():
        if series is None or ticker in series:
            rows += parse_markets(markets, ticker, READ_AT)
    return to_frame(rows)


def fixture_reading(series: list[str] | None = None) -> pm.Reading:
    return pm.Reading(fixture_frame(series).reset_index(drop=True), READ_AT)


def events_of(block: dict) -> dict[str, dict]:
    return {e["event"]: e for g in block["groups"] for e in g["events"]}


# ----------------------------------------------------------------------------------------------- the adapter
def test_the_frame_follows_the_contract_and_reads_the_api_strings_as_numbers():
    frame = fixture_frame()
    assert list(frame.columns) == [*COLUMNS, "published_at"]
    assert frame.index.name == "ts" and str(frame.index.tz) == "UTC" and (frame.index == READ_AT).all()
    assert len(frame) == 171
    wti = frame[frame["ticker"] == "KXWTI-26OCT1514-T94.49"].iloc[0]
    assert wti["series_ticker"] == "KXWTI" and wti["event_ticker"] == "KXWTI-26OCT1514"
    assert wti["subtitle"] == "Above $94.49" and wti["strike_type"] == "greater"
    assert wti["floor_strike"] == 94.49 and math.isnan(wti["cap_strike"])
    assert (wti["yes_bid"], wti["yes_ask"], wti["last_price"]) == (0.47, 0.51, 0.0)
    assert wti["close_time"] == "2026-10-15T18:30:00Z"
    top = frame[frame["ticker"] == "KXWTI-26OCT1514-T86.49"].iloc[0]  # the fixture keeps the rules on one row
    assert top["rules"].startswith("If the daily settlement price for WTI crude oil(December 2026 contract)")
    # a market object has no series ticker of its own: the one the query was made with is kept
    assert set(frame["series_ticker"]) == set(fixture_markets())
    # a deadline market has no strike at all
    deal = frame[frame["ticker"] == "KXUSAIRANAGREEMENT-27"].iloc[0]
    assert math.isnan(deal["floor_strike"]) and deal["strike_type"] == "" and deal["subtitle"] == "Before Jan 1, 2027"
    # a bin has both strikes, a "below" market only the cap
    low = frame[frame["ticker"] == "KXWTIW-26OCT1614-T80.00"].iloc[0]
    assert low["strike_type"] == "less" and math.isnan(low["floor_strike"]) and low["cap_strike"] == 80.0


def test_the_adapter_skips_a_series_that_answers_nothing_and_fails_only_when_all_do(monkeypatch):
    adapter = KalshiAdapter()
    pages = {
        "KXBRENTW": [{"markets": fixture_markets()["KXBRENTW"][:3], "cursor": "next"}, {"markets": [], "cursor": ""}],
        "KXBRENTD": [{"markets": [], "cursor": ""}],
    }
    calls: list[dict] = []

    def fake(url, params=None, **_kw):
        calls.append(dict(params or {}))
        if params["series_ticker"] == "KXBOOM":
            raise DataUnavailable("kalshi KXBOOM: HTTP 429")
        return pages[params["series_ticker"]].pop(0)

    monkeypatch.setattr(adapter, "get_json", fake)
    result = adapter.fetch(["KXBRENTW", "KXBRENTD", "KXBOOM"])
    assert len(result.frame) == 3 and result.meta["failed"] == {"KXBOOM": "kalshi KXBOOM: HTTP 429"}
    assert [c["series_ticker"] for c in calls] == ["KXBRENTW", "KXBRENTW", "KXBRENTD", "KXBOOM"]
    assert calls[1]["cursor"] == "next" and "cursor" not in calls[0]
    assert result.frame["series_ticker"].tolist() == ["KXBRENTW"] * 3
    monkeypatch.setattr(adapter, "get_json", lambda url, params=None, **kw: {"markets": [], "cursor": ""})
    with pytest.raises(DataUnavailable):
        adapter.fetch(["KXBRENTD"])


# ----------------------------------------------------------------------------------------------- the archive
def test_the_archive_writes_one_record_per_event_and_day_and_never_twice(tmp_path):
    store = StateStore(tmp_path)
    reading = fixture_reading()
    assert pm.record_reading(store, reading) == 12  # twelve events in the fixture
    assert pm.record_reading(store, reading) == 0  # the same day again: nothing
    rows = store.read_jsonl(pm.KALSHI_LOG)
    assert len(rows) == 12
    assert store.jsonl_path(pm.KALSHI_LOG, ts=READ_AT.to_pydatetime()).name == "kalshi-2026-10.jsonl"
    brent = next(r for r in rows if r["event"] == "KXBRENTW-26OCT1617")
    assert brent["key"] == "2026-10-14|KXBRENTW-26OCT1617" and brent["series"] == "KXBRENTW"
    assert brent["columns"] == list(pm.LOG_COLUMNS) and len(brent["markets"]) == 20
    top = dict(zip(brent["columns"], brent["markets"][0], strict=True))  # the markets in strike order
    assert top["ticker"] == "KXBRENTW-26OCT1617-T81.99" and top["floor"] == 81.99 and top["cap"] is None
    assert (top["yes_bid"], top["yes_ask"], top["last"]) == (0.98, 1.0, 0.0)
    # the next day's reading is new
    later = pm.Reading(reading.frame, READ_AT + pd.Timedelta(days=1))
    assert pm.record_reading(store, later) == 12
    assert len(store.read_jsonl(pm.KALSHI_LOG)) == 24


def test_the_tick_path_archives_the_newest_raw_snapshot(tmp_path):
    raw, store = RawStore(tmp_path / "raw"), StateStore(tmp_path / "desk")
    assert pm.archive_reading(raw, store) == {"status": "no reading on file"}
    frame = fixture_frame(["KXBRENTW", "KXHORMUZNORM"])
    raw.save("kalshi", "kalshi", FetchResult("kalshi", frame, READ_AT.to_pydatetime()))
    out = pm.archive_reading(raw, store)
    assert out == {"status": "ok", "day": "2026-10-14", "markets": 37, "events_written": 2}
    assert pm.archive_reading(raw, store)["events_written"] == 0
    reading = pm.latest_reading(raw)
    assert reading is not None and reading.seen == READ_AT and "observed_at" not in reading.frame.columns


# ----------------------------------------------------------------------------------------------- the ladders
def test_the_ladder_turns_bins_and_below_markets_into_one_curve():
    markets = [
        {"strike_type": "greater", "floor": 93.99, "cap": None, "mid": 0.025},
        {"strike_type": "less", "floor": None, "cap": 69.0, "mid": 0.005},
        {"strike_type": "between", "floor": 93.0, "cap": 93.99, "mid": 0.015},
        {"strike_type": "between", "floor": 85.0, "cap": 85.99, "mid": 0.135},
        {"strike_type": "between", "floor": 84.0, "cap": 84.99, "mid": 0.125},
        {"strike_type": "greater", "floor": 80.0, "cap": None, "mid": None},  # no quote: ignored
    ]
    curve = pm.above_curve(markets)
    # the "below" market turned around, the bins summed from the top on the "above" market that caps them
    assert curve == [
        (69.0, 0.995),
        (84.0, pytest.approx(0.3)),
        (85.0, pytest.approx(0.175)),
        (93.0, pytest.approx(0.04)),
        (93.99, 0.025),
    ]
    bins_only = pm.above_curve([m for m in markets if m["strike_type"] == "between"])
    assert bins_only == [(84.0, pytest.approx(0.275)), (85.0, pytest.approx(0.15)), (93.0, pytest.approx(0.015))]
    assert pm.interpolate_above(bins_only, 84.5) == pytest.approx(0.2125)
    assert pm.interpolate_above(bins_only, 83.0) is None and pm.interpolate_above(bins_only, 94.0) is None
    assert pm.implied_median([(100.0, 0.64), (102.0, 0.5), (104.0, 0.3)]) == 102.0
    assert pm.implied_median([(100.0, 0.9), (102.0, 0.8)]) is None


def test_the_deadline_is_read_off_the_label():
    assert pm.deadline_of("Before Jan 1, 2027").isoformat() == "2027-01-01"
    assert pm.deadline_of("before August 15, 2026").isoformat() == "2026-08-15"
    assert pm.deadline_of("Will the strait be back to normal before Dec 1, 2026?").isoformat() == "2026-12-01"
    assert pm.deadline_of("Above $103.99") is None and pm.deadline_of("") is None
    assert pm.question_of("Will the U.S. and Iran hold a senior-level meeting before Nov 1, 2026?") == (
        "Will the U.S. and Iran hold a senior-level meeting?"
    )


def test_the_options_probability_is_a_lognormal_at_the_atm_volatility():
    assert pm.norm_cdf(0.0) == pytest.approx(0.5) and pm.norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    years = 3.0 / 365.25
    at_the_money = pm.lognormal_p_above(100.0, 100.0, 0.5, years)
    assert at_the_money is not None and 0.49 < at_the_money < 0.5  # the driftless lognormal's median is below spot
    assert pm.lognormal_p_above(100.0, 110.0, 0.5, years) < 0.05
    assert pm.lognormal_p_above(100.0, 90.0, 0.5, years) > 0.95
    assert pm.lognormal_p_above(100.0, 100.0, 0.5, 0.0) is None and pm.lognormal_p_above(100.0, 100.0, 0.0, 1.0) is None


# ----------------------------------------------------------------------------------------------- the block
def test_the_block_reads_the_brent_ladder_against_the_desk_reference_and_the_options():
    block = pm.prediction_block(
        fixture_reading(["KXBRENTW"]), NOW, references={"brent": BRENT}, options={"BNO": BNO_OPTIONS}
    )
    assert block["asof"] == "2026-10-14T01:22:57Z" and block["stale"] is False and block["markets"] == 20
    (group,) = block["groups"]
    assert group["id"] == "brent" and len(group["events"]) == 1
    event = group["events"][0]
    assert event["kind"] == "price" and event["close_time"] == "2026-10-16T21:00:00Z"
    assert event["hours_to_close"] == pytest.approx(67.5, abs=0.1)
    by_strike = {m["floor"]: m for m in event["markets"]}
    assert by_strike[103.99]["mid"] == pytest.approx(0.535) and by_strike[105.99]["mid"] == pytest.approx(0.345)
    # the crowd's median is where the ladder crosses one half: between 103.99 (0.535) and 105.99 (0.345)
    assert event["implied"]["median"] == pytest.approx(103.99 + 2.0 * (0.535 - 0.5) / (0.535 - 0.345), abs=0.01)
    # P(above the reference, 107.02) sits between 105.99 (0.345) and 107.99 (0.185)
    assert event["implied"]["p_above_reference"] == pytest.approx(
        0.345 + (0.185 - 0.345) * (107.02 - 105.99) / 2.0, abs=1e-4
    )
    assert event["implied"]["p_above_reference_bound"] is None
    assert event["reference"] == {"symbol": "BZZ26", "price": 107.02, "asof": "2026-10-14T01:18:00Z"}
    assert "Pyth" in event["settles_on"]  # the market's own feed, not the contract quoted: a basis is expected
    # the options' twin: a lognormal at the ATM IV over the 67 hours to the close, marked approx
    opt = event["options_implied"]
    assert opt["approx"] is True and opt["symbol"] == "BNO" and opt["atm_iv"] == 0.5134
    sd = 0.5134 * math.sqrt(67.5 / (24 * 365.25))
    assert opt["median"] == pytest.approx(107.02 * math.exp(-0.5 * sd * sd), abs=0.02)
    assert opt["p_above_reference"] == pytest.approx(pm.norm_cdf(-0.5 * sd), abs=1e-3)
    assert by_strike[103.99]["options_p_above"] > 0.6 and by_strike[119.99]["options_p_above"] < 0.02
    # a bid of 0.98 against an ask of 1 is a quote (its mid); 0 bid / 1 ask would not be
    assert by_strike[81.99]["mid"] == pytest.approx(0.99)
    assert pm._mid(0.0, 1.0, 0.3) == 0.3 and pm._mid(0.0, 1.0, 0.0) == 0.5 and pm._mid(None, None, None) is None
    assert "the crowd's median 104.36 $" in event["summary"] and "P(above 107.02 $ now) 26 %" in event["summary"]
    assert "the BNO options' median" in event["summary"]


def test_a_reference_outside_the_ladder_gets_a_bound_not_a_number():
    # the daily Brent ladder of the 14th tops at 106.50 and the desk's Dec contract quoted 107.02
    block = pm.prediction_block(fixture_reading(["KXBRENTD"]), NOW, references={"brent": BRENT})
    event = block["groups"][0]["events"][0]
    assert event["implied"]["p_above_reference"] is None
    assert event["implied"]["p_above_reference_bound"].startswith(
        "under 12 % (the reference is above the ladder's top strike, 106.50)"
    )
    assert "P(above 107.02 $ now) under 12 %" in event["summary"]
    assert event["implied"]["median"] == pytest.approx(104.0 + 0.5 * (0.555 - 0.5) / (0.555 - 0.44), abs=0.01)


def test_a_weekly_ladder_of_bins_gives_a_sensible_median():
    block = pm.prediction_block(fixture_reading(["KXWTIW"]), NOW, references={"wti": WTI})
    event = block["groups"][0]["events"][0]
    curve = dict(map(tuple, event["curve"]))
    assert 0.98 <= curve[80.0] <= 1.0  # the "below 80" market turned around, under the bottom bin's cumulative
    assert curve[104.99] == pytest.approx(0.015)  # the "above" market as it is
    assert curve[104.0] == pytest.approx(0.025) and curve[94.0] == pytest.approx(0.585, abs=1e-3)  # bins summed on it
    assert 94.5 < event["implied"]["median"] < 95.0  # against the Dec contract at 95.24 that night
    assert 0.4 < event["implied"]["p_above_reference"] < 0.5


def test_without_a_fresh_option_chain_the_options_twin_says_so_and_nothing_else():
    old = {**BNO_OPTIONS, "quote_ts": "2026-10-12T20:00:00Z"}  # 29.5 hours old
    block = pm.prediction_block(fixture_reading(["KXBRENTW"]), NOW, references={"brent": BRENT}, options={"BNO": old})
    event = block["groups"][0]["events"][0]
    assert event["options_implied"] == {"symbol": "BNO", "stale": True, "quote_age_hours": 29.5}
    assert all("options_p_above" not in m for m in event["markets"])
    # and with no reference price at all the ladder is still shown, only the comparison is missing
    block = pm.prediction_block(fixture_reading(["KXBRENTW"]), NOW)
    event = block["groups"][0]["events"][0]
    assert event["reference"] is None and event["implied"]["p_above_reference"] is None
    assert event["implied"]["median"] == pytest.approx(104.36, abs=0.01) and event["options_implied"] is None


def test_a_market_past_its_close_time_is_never_a_probability():
    # the same reading looked at six days later: the ladders of the 14th, 15th and 16th have closed
    later = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    block = pm.prediction_block(fixture_reading(), later, references={"brent": BRENT, "wti": WTI})
    live = events_of(block)
    assert set(live) == {
        "KXBRENTMON-26OCT3017",
        "KXWTI-26NOV03",
        "KXHORMUZWEEKLY-26OCT18",  # closes at 13:00 that day: still an hour ahead
        "KXMAXSHIPSHORMUZ-26OCT01",
        "KXHORMUZNORM-26MAR17",
        "KXUSAIRANAGREEMENT-27",
        "KXUSIRANMEETING-27",
    }
    past = {(p["series"], p["event"]): p for p in block["past_close"]}
    assert set(past) == {
        ("KXBRENTD", "KXBRENTD-26OCT1417"),
        ("KXBRENTW", "KXBRENTW-26OCT1617"),
        ("KXWTI", "KXWTI-26OCT1514"),
        ("KXWTIW", "KXWTIW-26OCT1614"),
        ("KXEIACRUDEW", "KXEIACRUDEW-26OCT14"),
    }
    assert past[("KXBRENTW", "KXBRENTW-26OCT1617")]["markets"] == 20
    assert all("not a probability" in p["note"] for p in block["past_close"])
    assert block["stale"] is True  # and the reading itself is six days old


def test_a_deadline_ladder_keeps_the_dates_still_ahead_in_order_with_the_desk_reading_beside():
    hormuz = {"asof": "2026-10-04", "tankers_7d": 0.86, "baseline": 49.9, "ratio": 0.0172, "closed": True}
    block = pm.prediction_block(fixture_reading(["KXHORMUZNORM", "KXUSAIRANAGREEMENT"]), NOW, hormuz=hormuz)
    groups = {g["id"]: g for g in block["groups"]}
    strait = groups["hormuz"]["events"][0]
    assert (
        strait["kind"] == "deadline" and strait["close_time"] == "2029-01-01T14:59:00Z"
    )  # live while its last date is
    assert strait["title"] == (
        "Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF "
        "PortWatch be above 60?"
    )
    ladder = strait["ladder"]
    assert [m["before"] for m in ladder][:4] == ["2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01"]
    assert ladder[0]["p"] == 0.015 and ladder[1]["p"] == 0.035 and ladder[2]["p"] == 0.05  # the mid of bid and ask
    assert ladder[-1] == {"before": "2029-01-01", "p": 0.67} and len(ladder) == 17
    assert strait["summary"].startswith(
        "Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch "
        "be above 60? before Nov 1, 2026: 2 %, before Dec 1, 2026: 4 %, before Jan 1, 2027: 5 %"
    )
    assert strait["measured"]["ratio"] == 0.0172 and "tanker transits" in strait["measured"]["note"]
    deal = groups["iran"]["events"][0]
    assert deal["kind"] == "deadline" and deal["title"] == "Will the US agree to a new Iranian nuclear deal this year?"
    assert [m["before"] for m in deal["ladder"]][:3] == ["2026-11-01", "2026-12-01", "2027-01-01"]
    assert deal["ladder"][2] == {"before": "2027-01-01", "p": 0.115}
    assert deal["ladder"][-1] == {"before": "2029-01-20", "p": 0.385} and "measured" not in deal
    # a date already passed is dropped from the ladder, the event stays while its last date is ahead
    later = pm.prediction_block(fixture_reading(["KXUSAIRANAGREEMENT"]), datetime(2026, 12, 15, tzinfo=UTC))
    assert [m["before"] for m in events_of(later)["KXUSAIRANAGREEMENT-27"]["ladder"]][0] == "2027-01-01"
    assert block["past_close"] == []


def test_a_threshold_ladder_has_a_median_and_the_measured_figure_beside():
    inventories = {"crude_stocks_mmbbl": 424.1, "week_ending": "2026-10-02", "source": "EIA WPSR"}
    block = pm.prediction_block(
        fixture_reading(["KXEIACRUDEW", "KXMAXSHIPSHORMUZ", "KXHORMUZWEEKLY"]), NOW, inventories=inventories
    )
    groups = {g["id"]: g for g in block["groups"]}
    eia = groups["supply"]["events"][0]
    assert eia["kind"] == "threshold" and eia["measured"] == inventories
    # P(above 421) = 0.545 and P(above 422) = 0.445: the crowd's median is between the two
    assert eia["implied"]["median"] == pytest.approx(421.0 + (0.545 - 0.5) / (0.545 - 0.445), abs=0.01)
    assert "the crowd's median 421.4" in eia["summary"] and eia["title"].endswith("be above 421 million barrels?")
    ships = {e["series"]: e for e in groups["hormuz"]["events"]}
    assert 8.0 < ships["KXMAXSHIPSHORMUZ"]["implied"]["median"] < 9.0
    assert ships["KXMAXSHIPSHORMUZ"]["curve"][0] == [5.0, 0.98] and ships["KXMAXSHIPSHORMUZ"]["curve"][-1] == [
        25.0,
        0.065,
    ]
    assert ships["KXHORMUZWEEKLY"]["implied"]["median"] == pytest.approx(
        22.0 + 3.0 * (0.515 - 0.5) / (0.515 - 0.265), abs=0.01
    )
    assert "measured" not in ships["KXHORMUZWEEKLY"]  # no transit summary was handed in


def test_the_block_names_the_series_asked_for_that_had_no_market_and_a_stale_or_missing_reading():
    asked = ["KXBRENTW", "KXBRENTD", "KXWTIMONTHLY"]
    block = pm.prediction_block(fixture_reading(["KXBRENTW"]), NOW, asked=asked)
    assert block["empty_series"] == ["KXBRENTD", "KXWTIMONTHLY"] and block["asked"] == asked
    late = pm.prediction_block(fixture_reading(["KXBRENTW"]), NOW + timedelta(days=2), asked=asked)
    assert late["stale"] is True and late["age_hours"] == pytest.approx(48.1, abs=0.1)
    none = pm.prediction_block(None, NOW, asked=asked)
    assert none["asof"] is None and none["stale"] is True and none["groups"] == [] and none["empty_series"] == asked


def test_every_group_of_the_fixture_is_placed_and_json_clean():
    from engine.core.store import dumps

    block = pm.prediction_block(
        fixture_reading(),
        NOW,
        references={"brent": BRENT, "wti": WTI},
        options={"BNO": BNO_OPTIONS, "USO": {**BNO_OPTIONS, "symbol": "USO", "atm_iv": 0.4472}},
        asked=pm.configured_series(None),
    )
    assert [g["id"] for g in block["groups"]] == ["brent", "wti", "hormuz", "iran", "supply"]
    kinds = {e["event"]: e["kind"] for g in block["groups"] for e in g["events"]}
    assert kinds == {
        "KXBRENTD-26OCT1417": "price",
        "KXBRENTW-26OCT1617": "price",
        "KXBRENTMON-26OCT3017": "price",
        "KXWTI-26OCT1514": "price",
        "KXWTIW-26OCT1614": "price",
        "KXWTI-26NOV03": "price",
        "KXHORMUZNORM-26MAR17": "deadline",
        "KXMAXSHIPSHORMUZ-26OCT01": "threshold",
        "KXHORMUZWEEKLY-26OCT18": "threshold",
        "KXUSAIRANAGREEMENT-27": "deadline",
        "KXUSIRANMEETING-27": "deadline",
        "KXEIACRUDEW-26OCT14": "threshold",
    }
    # the events of a group in the order of their close
    assert [e["event"] for e in block["groups"][0]["events"]] == [
        "KXBRENTD-26OCT1417",
        "KXBRENTW-26OCT1617",
        "KXBRENTMON-26OCT3017",
    ]
    wti = events_of(block)["KXWTI-26NOV03"]
    assert wti["reference"]["symbol"] == "CLX26" and wti["options_implied"]["symbol"] == "USO"
    assert wti["title"].startswith("Will the WTI front-month settle oil price")
    text = dumps(block)
    assert "NaN" not in text and "Infinity" not in text
    assert all(e["summary"] for g in block["groups"] for e in g["events"])
    assert all("title" not in m for g in block["groups"] for e in g["events"] for m in e["markets"])


# ----------------------------------------------------------------------------------------------- the live path
def test_the_tick_archives_the_reading_and_the_export_shows_it_against_the_desk_quotes(tmp_path, monkeypatch):
    """End to end on the production path: a tick with a Kalshi snapshot on the raw store appends the day's
    events to ``desk/kalshi-*.jsonl``, and ``desk.json`` carries the block with the desk's own Brent quote as
    the reference. SYNTHETIC price tables, the real market objects of the fixture."""
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
        github_repo="test/test",
        offline=True,
    )
    runner = LiveRunner(settings)
    for entry, frame in make_desk_raw_frames(end=date(2026, 10, 8)).items():
        save_raw_frame(runner.raw, entry, frame)
    now = datetime(2026, 10, 8, 21, 18, tzinfo=UTC)
    reading_at = datetime(2026, 10, 8, 21, 5, tzinfo=UTC)
    runner.raw.save("kalshi", "kalshi", FetchResult("kalshi", fixture_frame(["KXBRENTW", "KXHORMUZNORM"]), reading_at))
    names = {"insider_form4", *jobs.DESK_CONTEXT_ENTRIES, *jobs.DESK_MACRO_ENTRIES}
    names |= {name for group in jobs.DESK_VEHICLE_ENTRIES.values() for name in group}
    stamp = now.isoformat()
    green = [{"source": n, "status": "green", "checked_at": stamp, "last_success_at": stamp} for n in names]
    runner.store.write_json("health.json", {"overall": "green", "sources": green})
    monkeypatch.setattr(jobs, "_fetch", lambda r, g, e=None: {"ok": 0, "failed": 0, "overall": "green", "sources": []})
    monkeypatch.setattr(jobs, "_desk_backtest_stale", lambda *a, **k: "")
    monkeypatch.setattr(jobs, "now_utc", lambda: now)

    outcome = jobs.tick(runner, "t-kalshi", legacy=False)
    assert outcome.status == "ok"
    assert outcome.detail["prediction_markets"] == {
        "status": "ok",
        "day": "2026-10-08",
        "markets": 37,
        "events_written": 2,
    }
    log = settings.state_dir / "desk" / "kalshi-2026-10.jsonl"
    assert log.exists() and len(log.read_text().splitlines()) == 2
    # the same tick again: nothing new to write
    again = jobs.tick(runner, "t-kalshi-2", legacy=False)
    assert again.detail["prediction_markets"]["events_written"] == 0
    assert "kalshi" in jobs.DESK_HOURLY_ENTRIES  # read every hour, judged by the alert at every reading

    payload = ex.build_desk_payload(settings, runner.raw, runner.risk, runner.store, now)
    block = payload["prediction_markets"]
    assert block["asof"] == "2026-10-08T21:05:00Z" and block["stale"] is False
    assert [g["id"] for g in block["groups"]] == ["brent", "hormuz"]
    brent = block["groups"][0]["events"][0]
    # the reference is the desk's own quote of the Brent contract a futures book holds, as the market block has it
    # (this synthetic archive has no per-contract table: no price, so no "P(above now)" - and no invented one)
    assert brent["reference"]["symbol"] == payload["market"]["brent"]["symbol"]
    assert brent["reference"]["price"] == payload["market"]["brent"]["price"] is None
    assert brent["implied"]["p_above_reference"] is None
    assert brent["implied"]["median"] == pytest.approx(104.36, abs=0.01)
    assert brent["options_implied"] is None  # no option chain in this archive
    asked = set(pm.configured_series(settings.config_dir))
    assert block["empty_series"] == sorted(asked - {"KXBRENTW", "KXHORMUZNORM"})
    assert "KXMAXSHIPSHORMUZ" in block["asked"]
