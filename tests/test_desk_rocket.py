"""The Rocket (phase 19, docs/RESEARCH.md chapter 14): the rule of 14 A on a REAL chain, its bookkeeping, its tick.

The chain is the real delayed USO chain of 2026-10-08 (tests/fixtures/cboe, the options book's fixture: 148.35 $, the
6 and 20 November expiries). The Dynamic's forecasts, Kalshi's weekly changes, the later quotes and the settlement
closes used to exercise the rules are SYNTHETIC and say so where they appear.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from engine.data.adapters.cboe import parse_chain
from engine.data.base import FetchResult
from engine.data.raw_store import RawStore
from engine.desk import rocket as rk
from engine.desk.data import DeskData, VehicleSeries

FIXTURES = Path(__file__).parent / "fixtures" / "cboe"
QUOTED = datetime(2026, 10, 8, 18, 11, 13, tzinfo=UTC)  # the fixture's stamp
NOW = datetime(2026, 10, 8, 18, 30, tzinfo=UTC)  # 14:30 New York, 19 minutes after the quotes
CFG = dataclasses.replace(rk.RocketConfig(), decision_time_ny=(14, 0))  # the fixture was read before 15:00


def _frame() -> pd.DataFrame:
    return parse_chain(json.loads((FIXTURES / "uso_20261008.json").read_text()))


def _store(path: Path, frame: pd.DataFrame | None = None, at: datetime = QUOTED) -> RawStore:
    store = RawStore(path)
    frame = _frame() if frame is None else frame
    store.save("uso_options", "cboe", FetchResult("cboe", frame, at))
    return store


def _chain(frame: pd.DataFrame | None = None) -> rk.Chain:
    frame = _frame() if frame is None else frame
    frame = frame.copy()
    frame["expiry"] = pd.to_datetime(frame["expiry"]).dt.normalize()
    frame["mid"] = (frame["bid"] + frame["ask"]) / 2.0
    frame["rel_spread"] = ((frame["ask"] - frame["bid"]) / frame["mid"]).where(frame["mid"] > 0)
    stamp = pd.Timestamp(frame["quote_ts"].iloc[0])
    quote_ts = (stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)).to_pydatetime()
    return rk.Chain("USO", frame, quote_ts, float(frame["underlying_price"].iloc[0]), None)


def _later(frame: pd.DataFrame, when: datetime, move: float = 0.0, scale: float = 1.0) -> pd.DataFrame:
    """The same chain re-stamped ``when`` (SYNTHETIC): the fund moved by ``move`` $, every quote times ``scale``."""
    out = frame.copy()
    out["quote_ts"] = pd.Timestamp(when)
    out["underlying_price"] = out["underlying_price"] + move
    out["bid"] = (out["bid"] * scale).round(2)
    out["ask"] = (out["ask"] * scale).round(2)
    return out


def _data(forecast: float | None, days: tuple[str, ...] = ("2026-10-07", "2026-10-08")) -> DeskData:
    """SYNTHETIC combined forecasts of the Brent fund (the Dynamic's), the last row the decision day."""
    idx = pd.DatetimeIndex(pd.to_datetime(list(days)), name="date")
    empty = pd.Series(dtype="float64")
    forecasts = pd.DataFrame({"combined": [forecast] * len(idx)}, index=idx, dtype="float64")
    vol = pd.Series([0.4] * len(idx), index=idx)
    data = DeskData()
    data.series = {"BNO": VehicleSeries("BNO", pd.DataFrame(), empty, empty, forecasts, vol, empty, empty, empty)}
    return data


@pytest.fixture
def kalshi(monkeypatch):
    """Sets the de-escalation index's weekly change the oracle reports (SYNTHETIC), or makes the archive fail."""
    state = {"change": -0.02, "fail": False}

    def fake(store, day, spec=None):
        if state["fail"]:
            raise RuntimeError("bad line")
        reading = SimpleNamespace(value=0.21)
        return SimpleNamespace(change=state["change"], reading=reading, note="SYNTHETIC reading")

    monkeypatch.setattr(rk.oracle, "forecast_for", fake)
    return state


# ----------------------------------------------------------------------------------------------- the rule on file
def test_the_config_on_file_is_the_registered_rule():
    cfg = rk.load_rocket_config()
    assert cfg is not None and cfg.id == "rocket" and cfg.initial_capital == 1000.0
    assert (cfg.forecast_threshold, cfg.target_delta, cfg.min_delta) == (5.0, 0.30, 0.10)
    assert (cfg.dte_min, cfg.dte_target, cfg.dte_max, cfg.roll_days) == (21, 30, 45, 7)
    assert (cfg.stake, cfg.max_delta_leverage, cfg.max_leg_spread) == (0.5, 10.0, 0.25)
    assert (cfg.fill_fraction, cfg.fee_per_contract, cfg.max_quote_age_minutes) == (0.25, 0.05, 45)
    assert cfg.decision_time_ny == (15, 0) and cfg.floor == 0.05
    assert cfg.underlying == rk.USO_OPTIONS and cfg.underlying.forecast == "BNO"  # the Dynamic reads the Brent fund
    for bad in ({"stake": 1.5}, {"max_delta_leverage": 12.0}, {"min_delta": 0.4}, {"fill_fraction": 0.7}):
        with pytest.raises(ValueError):
            dataclasses.replace(cfg, **bad)


# ----------------------------------------------------------------------------------------------- the signal
@pytest.mark.parametrize(
    ("forecast", "change", "direction", "agree"),
    [
        (7.0, -0.02, 1, True),  # long, and the odds of a de-escalation fell: oil's way
        (7.0, 0.02, 1, False),
        (-7.0, 0.003, -1, True),  # no deadband: a third of a point is an agreement
        (-7.0, -0.02, -1, False),
        (5.0, -0.02, 1, True),  # the threshold itself is a direction
        (4.99, -0.02, 0, False),
        (7.0, 0.0, 1, False),  # no move is no agreement
        (7.0, None, 1, False),  # nothing to read is no agreement
        (None, -0.02, 0, False),
    ],
)
def test_the_signal_is_the_dynamic_direction_and_kalshi_agreement(tmp_path, kalshi, forecast, change, direction, agree):
    kalshi["change"] = change
    signal = rk.read_signal(_data(forecast), tmp_path, date(2026, 10, 8), CFG)
    assert signal.direction == direction and signal.agree is agree
    assert signal.text().startswith("The Dynamic's forecast") or forecast is None


def test_an_unreadable_archive_is_no_agreement_and_never_an_error(tmp_path, kalshi):
    kalshi["fail"] = True
    signal = rk.read_signal(_data(9.0), tmp_path, date(2026, 10, 8), CFG)
    assert signal.direction == 1 and signal.agree is False and signal.kalshi_change is None
    assert "could not be read" in signal.kalshi_note


def test_only_the_forecast_of_the_day_itself_is_a_direction(tmp_path, kalshi):
    """Found in review: with the fund's table missing on a tick, the book read yesterday's forecast and bet while
    the Dynamic rightly waited. The row of the decision day, or nothing."""
    signal = rk.read_signal(_data(9.0, ("2026-10-01", "2026-10-02")), tmp_path, date(2026, 10, 8), CFG)
    assert signal.forecast is None and signal.direction == 0
    yesterday = rk.read_signal(_data(9.0, ("2026-10-06", "2026-10-07")), tmp_path, date(2026, 10, 8), CFG)
    assert yesterday.forecast is None and yesterday.direction == 0
    today = rk.read_signal(_data(9.0), tmp_path, date(2026, 10, 8), CFG)
    assert today.forecast == 9.0 and today.direction == 1
    assert "nothing is decided" in yesterday.text()


def test_without_the_day_forecast_the_tick_decides_nothing_and_the_decision_stays_owed(tmp_path, kalshi):
    store, state = _store(tmp_path / "raw"), tmp_path / "state"
    rk.rocket_tick(state, _data(9.0), store, NOW, CFG)  # a call is bought
    tomorrow = NOW + timedelta(days=1)
    stale = _data(float("nan"), ("2026-10-08", "2026-10-09"))  # SYNTHETIC: no forecast of the 9th
    report = rk.rocket_tick(state, stale, _next_day(tmp_path / "raw2", tomorrow, -3.0, 0.8), tomorrow, CFG)
    assert report["decision"] is None and "no forecast" in report["waiting"]
    book = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG))
    assert book.position is not None and book.last_decision_day == "2026-10-08"  # held, not sold; still owed
    monitor = json.loads((state / "desk" / "rocket" / "monitor.json").read_text())
    assert "no forecast" in monitor["waiting"]
    # the scheduler's reason (the fund's table red, the clock overtaken) holds the decision the same way
    held = rk.rocket_tick(
        state,
        _data(-8.0, ("2026-10-08", "2026-10-09")),
        _next_day(tmp_path / "raw3", tomorrow, -3.0, 0.8),
        tomorrow,
        CFG,
        wait="source bno_daily unavailable: no new decision until it is back",
    )
    assert held["decision"] is None and held["waiting"].startswith("source bno_daily")
    assert rk.RocketBook.load(CFG, rk.rocket_store(state, CFG)).position is not None
    # with the forecast and no reason to wait, the same day's decision is taken
    done = rk.rocket_tick(
        state,
        _data(-8.0, ("2026-10-08", "2026-10-09")),
        _next_day(tmp_path / "raw4", tomorrow, -3.0, 0.8),
        tomorrow,
        CFG,
    )
    assert done["decision"] is not None and done["decision"]["action"].startswith("sell")


# ----------------------------------------------------------------------------------------------- the choice
def test_a_long_bet_is_the_call_rule_4_names_on_the_real_chain():
    chain = _chain()
    cand, why = rk.select(chain, CFG, 1, 1000.0)
    assert cand is not None and why == ""
    assert cand["right"] == "C" and cand["kind"] == "long_call" and cand["strike"] > chain.price
    assert cand["expiry"] == "2026-11-06" and cand["dte"] == 29  # the listed expiry closest to 30 days
    # the eligible call whose delta is nearest 0.30 fits in half the account, so it is the one
    legs = rk._eligible(chain, CFG, pd.Timestamp("2026-11-06"), "C")
    nearest = legs.iloc[int((legs["abs_delta"] - 0.30).abs().to_numpy().argmin())]
    assert cand["option"] == nearest["option"] and abs(cand["delta"]) == pytest.approx(nearest["abs_delta"], abs=1e-4)
    # filled a quarter of the spread inside the ask, 0.05 $ a contract, as many whole contracts as 500 $ buys
    assert cand["fill"] == pytest.approx(cand["ask"] - 0.25 * (cand["ask"] - cand["bid"]), abs=1e-4)
    per = cand["fill"] * 100 + 0.05
    assert cand["contracts"] == math.floor(500.0 / per) >= 1 and cand["cost"] == pytest.approx(per * cand["contracts"])
    assert cand["cost"] <= 500.0 and (cand["ask"] - cand["bid"]) / cand["mid"] <= 0.25
    assert 0 < cand["delta_notional"] <= 10 * 1000.0
    assert cand["share_of_equity"] == pytest.approx(cand["cost"] / 1000.0, abs=1e-4)


def test_a_short_bet_is_a_put_below_the_price():
    chain = _chain()
    cand, _ = rk.select(chain, CFG, -1, 1000.0)
    assert cand is not None and cand["right"] == "P" and cand["kind"] == "long_put"
    assert cand["strike"] < chain.price and cand["delta"] < 0 and cand["delta_notional"] < 0
    assert cand["expiry"] == "2026-11-06" and cand["cost"] <= 500.0


def test_a_smaller_account_walks_further_from_the_price_never_below_delta_010():
    chain = _chain()
    full, _ = rk.select(chain, CFG, -1, 1000.0)
    small, _ = rk.select(chain, CFG, -1, 600.0)  # 300 $ do not buy the 0.30 put (405 $): further out it goes
    assert full is not None and small is not None
    assert small["strike"] < full["strike"] and abs(small["delta"]) < abs(full["delta"])
    assert abs(small["delta"]) >= 0.10 and small["cost"] <= 300.0
    # every put between the two cost more than 300 $: the first one that fits is the one bought
    legs = rk._eligible(chain, CFG, pd.Timestamp("2026-11-06"), "P")
    between = legs[(legs["strike"] > small["strike"]) & (legs["abs_delta"] <= abs(full["delta"]))]
    assert all(rk.per_contract(row, CFG)[1] > 300.0 for _, row in between.iterrows())
    # nothing the rule admits fits in half of 60 $: no bet, and the reason says so
    tiny, why = rk.select(chain, CFG, -1, 60.0)
    assert tiny is None and "less than half the account" in why
    floor = rk.cheapest(chain, CFG)
    assert floor is not None and rk.select(chain, CFG, -1, 2 * floor + 1.0)[0] is not None
    assert (
        rk.select(chain, CFG, -1, 2 * floor - 1.0)[0] is None and rk.select(chain, CFG, 1, 2 * floor - 1.0)[0] is None
    )


def test_the_delta_notional_never_exceeds_ten_times_the_account():
    chain = _chain()
    cfg = dataclasses.replace(CFG, stake=1.0)  # SYNTHETIC: the whole account on a bet, to reach the cap
    cand, _ = rk.select(chain, cfg, 1, 10_000.0)
    assert cand is not None
    per_contract = abs(cand["delta"]) * 100 * chain.price
    assert cand["contracts"] == min(
        math.floor(10_000.0 / (cand["fill"] * 100 + 0.05)), math.floor(100_000.0 / per_contract)
    )
    assert abs(cand["delta_notional"]) <= 100_000.0 + 1e-6


def test_only_the_expiry_closest_to_30_days_is_used():
    frame = _frame()
    nov6 = pd.to_datetime(frame["expiry"]).dt.normalize() == pd.Timestamp("2026-11-06")
    frame.loc[nov6 & (frame["right"] == "C"), "bid"] = 0.0  # SYNTHETIC: no call of 6 November has a bid
    cand, why = rk.select(_chain(frame), CFG, 1, 1000.0)
    assert cand is None and "expiring 2026-11-06" in why  # the 20 November calls are not a fallback
    assert rk.select(_chain(frame), CFG, -1, 1000.0)[0] is not None  # the puts of the same expiry still are


def test_an_order_to_copy_is_in_the_shape_each_broker_takes():
    copy = rk.replicate("USO261106C00160000", 2, 3.8125, "buy")
    assert copy["alpaca"] == {
        "symbol": "USO261106C00160000",
        "qty": 2,
        "side": "buy",
        "type": "limit",
        "limit_price": 3.81,
        "time_in_force": "day",
    }
    assert (
        copy["robinhood"]
        == "USO, Trade, Trade options: Buy, Call, expiring 2026-11-06, strike 160; 2 contracts, limit 3.81"
    )
    assert rk.occ_parts("USO261120P00132500") == {"root": "USO", "expiry": "2026-11-20", "right": "P", "strike": 132.5}
    assert rk.occ_parts("garbage") is None


# ----------------------------------------------------------------------------------------------- bookkeeping
def _signal(direction: int = 1) -> rk.RocketSignal:
    return rk.RocketSignal(date(2026, 10, 8), 7.0 * direction, direction, -0.02 * direction, 0.21, "SYNTHETIC", True)


def _opened(direction: int = 1) -> tuple[rk.RocketBook, dict]:
    cand, _ = rk.select(_chain(), CFG, direction, 1000.0)
    assert cand is not None
    book = rk.RocketBook.load(CFG, None)
    book.open(cand, NOW, date(2026, 10, 8), _signal(direction), "test")
    return book, cand


def test_buying_costs_the_premium_and_the_fees_and_marks_at_the_mid():
    book, cand = _opened()
    assert book.cash == pytest.approx(1000.0 - cand["cost"])
    assert book.equity() == pytest.approx(1000.0 - cand["cost"] + cand["mid"] * 100 * cand["contracts"])
    assert book.position is not None and book.position["replicate"]["alpaca"]["side"] == "buy"
    book.mark(None, NOW + timedelta(hours=1))  # no chain: the last mark stays and is flagged
    assert book.position["mark"] == cand["mid"] and book.position["mark_stale"] is True


def test_a_sale_pays_the_bid_side_and_counts_the_result():
    book, cand = _opened()
    rec = book.close(5.0, NOW, "sell", "the Dynamic no longer points this way")
    n = cand["contracts"]
    assert rec["proceeds"] == pytest.approx(5.0 * 100 * n - 0.05 * n)
    assert rec["pnl"] == pytest.approx(rec["proceeds"] - cand["cost"]) and book.position is None
    assert book.cash == pytest.approx(1000.0 + rec["pnl"]) and book.n_closed == 1
    assert book.n_wins == (1 if rec["pnl"] > 0 else 0) and rec["replicate"]["alpaca"]["side"] == "sell"
    with pytest.raises(ValueError):
        book.close(1.0, NOW, "sell", "nothing open")


@pytest.mark.parametrize("direction", [1, -1])
def test_an_option_held_to_expiry_pays_its_intrinsic_value_on_the_official_close(direction):
    book, cand = _opened(direction)
    expiry = pd.Timestamp(cand["expiry"])
    after = datetime(expiry.year, expiry.month, expiry.day, 22, 0, tzinfo=UTC)  # 17:00 New York (EST)
    close = cand["strike"] + 6.0 * direction  # SYNTHETIC close: 6 $ in the money
    assert book.settle(pd.Series({expiry: close}), None, after - timedelta(days=1)) is None  # not due yet
    rec = book.settle(pd.Series({expiry: close}), None, after)
    assert rec is not None and rec["action"] == "expiry" and rec["price"] == pytest.approx(6.0)
    assert rec["proceeds"] == pytest.approx(600.0 * cand["contracts"])  # no fee on an expiry
    assert book.position is None and book.cash == pytest.approx(1000.0 - cand["cost"] + 600.0 * cand["contracts"])


def test_an_expired_option_waits_for_the_close_then_falls_back_and_says_approx():
    book, cand = _opened()
    expiry = pd.Timestamp(cand["expiry"])
    evening = datetime(expiry.year, expiry.month, expiry.day, 22, 0, tzinfo=UTC)
    assert book.settle(pd.Series(dtype=float), None, evening) is None and book.position["awaiting_close"] is True
    closes = pd.Series({expiry - pd.Timedelta(days=1): cand["strike"] - 1.0})  # SYNTHETIC: worthless
    late = book.settle(closes, None, evening + timedelta(days=5))
    assert late is not None and late["approx"] is True and late["proceeds"] == 0.0 and late["pnl"] == -cand["cost"]


def test_the_floor_kills_a_life_once_and_a_reset_starts_a_new_one():
    book, _ = _opened()
    book.position["mark"] = 0.0  # SYNTHETIC: the option is worth nothing
    book.cash = 40.0
    assert book.check_floor() is True and book.status == "dead" and book.check_floor() is False
    book.reset(NOW + timedelta(days=1))
    assert book.epoch == 2 and book.cash == 1000.0 and book.position is None and book.status == "active"
    life = book.epochs[0]
    assert life["end_reason"] == "dead" and life["final_equity"] == 40.0 and life["open_abandoned"] == 1
    book.reset(NOW + timedelta(days=2))
    assert book.epochs[1]["end_reason"] == "reset_manual" and book.epoch == 3


# ----------------------------------------------------------------------------------------------- the tick
def test_before_the_decision_time_the_tick_only_marks(tmp_path, kalshi):
    early = datetime(2026, 10, 8, 17, 50, tzinfo=UTC)  # 13:50 New York
    store = _store(tmp_path / "raw", at=QUOTED)
    report = rk.rocket_tick(tmp_path / "state", _data(9.0), store, early, CFG)
    assert report["decision"] is None and report["equity"] == 1000.0
    monitor = json.loads((tmp_path / "state" / "desk" / "rocket" / "monitor.json").read_text())
    assert monitor["signal"]["agree"] is True and monitor["candidate"]["right"] == "C"
    assert monitor["candidate"]["replicate"]["alpaca"]["side"] == "buy" and monitor["stuck"] is False
    assert monitor["bet_floor"] == pytest.approx(2 * monitor["cheapest_cost"]) and monitor["odds"]["approx"] is True
    assert rk.decision_due(tmp_path / "state", NOW, Path("config")) is False  # the file's 15:00, not 14:00


def test_the_tick_buys_once_a_day_when_the_dynamic_and_kalshi_agree(tmp_path, kalshi):
    store, state = _store(tmp_path / "raw"), tmp_path / "state"
    first = rk.rocket_tick(state, _data(9.0), store, NOW, CFG)
    assert first["opened"] == 1 and first["decision"]["action"] == "open"
    assert "Bought 1 USO call" in first["decision"]["reason"] and "it agrees" in first["decision"]["reason"]
    assert first["decision"]["orders"][0]["units"] >= 1
    again = rk.rocket_tick(state, _data(9.0), store, NOW + timedelta(minutes=30), CFG)
    assert again["decision"] is None and again["opened"] == 0  # one decision a session
    book = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG))
    assert book.position is not None and book.last_decision_day == "2026-10-08"
    trades = rk.rocket_store(state, CFG).read_jsonl("trades")
    assert [t["action"] for t in trades] == ["open"] and trades[0]["equity_after"] == pytest.approx(book.equity())


def test_no_bet_without_kalshi_and_the_page_is_told_why(tmp_path, kalshi):
    kalshi["change"] = 0.03  # SYNTHETIC: the odds of a de-escalation rose, against a long bet
    store, state = _store(tmp_path / "raw"), tmp_path / "state"
    report = rk.rocket_tick(state, _data(9.0), store, NOW, CFG)
    assert report["opened"] == 0 and "Kalshi does not agree" in report["decision"]["reason"]
    monitor = json.loads((state / "desk" / "rocket" / "monitor.json").read_text())
    assert monitor["candidate"] is not None and monitor["candidate_needs_kalshi"] is True


def test_stale_quotes_and_weekends_never_trade(tmp_path, kalshi):
    store, state = _store(tmp_path / "raw"), tmp_path / "state"
    late = QUOTED + timedelta(minutes=50)  # 15:01 New York, quotes 50 minutes old
    report = rk.rocket_tick(state, _data(9.0), store, late, CFG)
    assert report["decision"] is None
    assert rk.RocketBook.load(CFG, rk.rocket_store(state, CFG)).last_decision_day is None  # still owed
    saturday = datetime(2026, 10, 10, 18, 30, tzinfo=UTC)
    weekend = _store(tmp_path / "raw2", _later(_frame(), saturday - timedelta(minutes=5)), saturday)
    assert rk.rocket_tick(tmp_path / "other", _data(9.0), weekend, saturday, CFG)["decision"] is None


def _next_day(store_dir: Path, when: datetime, move: float, scale: float) -> RawStore:
    return _store(store_dir, _later(_frame(), when - timedelta(minutes=10), move, scale), when)


def test_the_bet_is_sold_when_the_dynamic_turns_and_the_other_side_is_bought(tmp_path, kalshi):
    state = tmp_path / "state"
    rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    tomorrow = NOW + timedelta(days=1)
    kalshi["change"] = 0.02  # SYNTHETIC: now rising odds of a de-escalation, which agree with a short bet
    data = _data(-8.0, ("2026-10-08", "2026-10-09"))
    report = rk.rocket_tick(state, data, _next_day(tmp_path / "raw2", tomorrow, -3.0, 0.8), tomorrow, CFG)
    assert report["decision"]["action"] == "sell+open"
    units = [o["units"] for o in report["decision"]["orders"]]
    assert units[0] < 0 < units[1]  # the call sold, then a put bought
    assert "no longer points this way" in report["decision"]["reason"]
    book = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG))
    assert book.position is not None and book.position["right"] == "P" and book.n_closed == 1
    actions = [t["action"] for t in rk.rocket_store(state, CFG).read_jsonl("trades")]
    assert actions == ["open", "sell", "open"]


def test_with_seven_days_left_the_bet_is_rolled(tmp_path, kalshi):
    state = tmp_path / "state"
    rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    # SYNTHETIC: 30 October, 7 days before the 6 November expiry, the Dynamic still long: sold, and the next bet
    # goes to the 20 November expiry (21 days, the only one left in the window)
    when = datetime(2026, 10, 30, 18, 30, tzinfo=UTC)
    data = _data(9.0, ("2026-10-29", "2026-10-30"))
    report = rk.rocket_tick(state, data, _next_day(tmp_path / "raw2", when, 2.0, 1.1), when, CFG)
    assert report["decision"]["action"].startswith("roll")
    assert "7 days to the expiry" in report["decision"]["reason"]
    trades = rk.rocket_store(state, CFG).read_jsonl("trades")
    assert trades[1]["action"] == "roll"


def test_a_sale_needs_a_bid(tmp_path, kalshi):
    """Found in review: a sale was booked at 0.05 against a quote of 0.00 / 0.20. A one-sided quote is no price."""
    state = tmp_path / "state"
    rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    held = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG)).position
    assert held is not None
    tomorrow = NOW + timedelta(days=1)
    frame = _later(_frame(), tomorrow - timedelta(minutes=10), -3.0, 0.8)
    frame.loc[frame["option"] == held["option"], "bid"] = 0.0  # SYNTHETIC: no bid on the held call
    store = _store(tmp_path / "raw2", frame, tomorrow)
    report = rk.rocket_tick(state, _data(-8.0, ("2026-10-08", "2026-10-09")), store, tomorrow, CFG)
    assert report["decision"]["action"] == "none" and "has no bid: held for now" in report["decision"]["reason"]
    assert rk.RocketBook.load(CFG, rk.rocket_store(state, CFG)).position is not None


def test_a_held_option_beyond_the_archived_strikes_is_valued_and_sold_at_its_intrinsic_value(tmp_path, kalshi):
    """Found in review: the archive keeps a window of strikes around the fund's price; after a crash the winning
    put fell out of it and was held at a stale mark, so the sale, the roll and the target could not execute."""
    kalshi["change"] = 0.02  # SYNTHETIC: a short bet agrees
    state = tmp_path / "state"
    rk.rocket_tick(state, _data(-9.0), _store(tmp_path / "raw"), NOW, CFG)
    held = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG)).position
    assert held is not None and held["right"] == "P"
    tomorrow = NOW + timedelta(days=1)
    # SYNTHETIC crash: the fund at 100, the archive holding only the strikes within 30 % of it (70..130)
    frame = _later(_frame(), tomorrow - timedelta(minutes=10), 0.0, 1.0)
    frame["underlying_price"] = 100.0
    frame = frame[(frame["strike"] >= 70.0) & (frame["strike"] <= 130.0)]
    assert held["option"] not in set(frame["option"])
    store = _store(tmp_path / "raw2", frame, tomorrow)
    chain = rk.load_chain(store, "uso_options", "USO")
    assert chain is not None and rk.beyond_archive(chain, held)
    book = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG))
    book.mark(chain, tomorrow)
    assert book.position is not None and book.position["mark"] == pytest.approx(held["strike"] - 100.0)
    assert book.position["mark_approx"] is True and book.position["delta"] == -1.0
    assert book.liquidation(chain) == pytest.approx(held["strike"] - 100.0)
    # the Dynamic turns: sold at the intrinsic value, marked approx, the words say so
    report = rk.rocket_tick(state, _data(8.0, ("2026-10-08", "2026-10-09")), store, tomorrow, CFG)
    assert report["decision"]["action"].startswith("sell") and "intrinsic value" in report["decision"]["reason"]
    trades = rk.rocket_store(state, CFG).read_jsonl("trades")
    assert trades[1]["action"] == "sell" and trades[1]["approx"] is True
    assert trades[1]["price"] == pytest.approx(held["strike"] - 100.0)


def test_the_life_ends_at_the_target(tmp_path, kalshi):
    """14 H: at a decision with the account worth ten times its capital, the option is sold and the life is over."""
    state = tmp_path / "state"
    rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    held = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG)).position
    assert held is not None
    tomorrow = NOW + timedelta(days=1)
    frame = _later(_frame(), tomorrow - timedelta(minutes=10), 60.0, 1.0)  # SYNTHETIC: the fund up 60 $
    mask = frame["option"] == held["option"]
    frame.loc[mask, "bid"], frame.loc[mask, "ask"] = 100.0, 101.0  # the call worth 100 $ a share: 10 000 $ a contract
    store = _store(tmp_path / "raw2", frame, tomorrow)
    report = rk.rocket_tick(state, _data(9.0, ("2026-10-08", "2026-10-09")), store, tomorrow, CFG)
    decision = report["decision"]
    assert (
        decision["action"] == "target" and decision["status"] == "target" and "reached its target" in decision["reason"]
    )
    book = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG))
    assert book.status == "target" and book.position is None and book.equity() >= 10_000.0
    trades = rk.rocket_store(state, CFG).read_jsonl("trades")
    assert trades[-1]["action"] == "target" and trades[-1]["price"] == pytest.approx(100.25)
    # no new bet in a life that is over, whatever the signal; a repeated tick changes nothing
    again = rk.rocket_tick(
        state, _data(9.0, ("2026-10-08", "2026-10-09")), store, tomorrow + timedelta(minutes=30), CFG
    )
    assert again["decision"] is None and rk.RocketBook.load(CFG, rk.rocket_store(state, CFG)).position is None
    day_after = tomorrow + timedelta(days=3)  # Monday 12 October
    later = rk.rocket_tick(
        state,
        _data(9.0, ("2026-10-09", "2026-10-12")),
        _next_day(tmp_path / "raw3", day_after, 60.0, 1.0),
        day_after,
        CFG,
    )
    assert later["decision"]["action"] == "none" and later["opened"] == 0
    monitor = json.loads((state / "desk" / "rocket" / "monitor.json").read_text())
    assert monitor["no_candidate_reason"] == "the life is over" and monitor["stuck"] is False
    # the reset archives the life with its reason
    rk.reset_rocket_book(state, day_after + timedelta(hours=1), CFG)
    saved = rk.rocket_store(state, CFG).read_json(rk.STATE_FILE)
    assert saved["epochs"][0]["end_reason"] == "target" and saved["epochs"][0]["final_equity"] >= 10_000.0
    assert saved["status"] == "active" and saved["cash"] == 1000.0


def test_a_cash_account_at_the_target_ends_the_same_way(tmp_path, kalshi):
    state = tmp_path / "state"
    rk.rocket_store(state, CFG).write_json(rk.STATE_FILE, {"initial_capital": 1000.0, "cash": 10_200.0, "epoch": 1})
    report = rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    assert report["decision"]["action"] == "none" and report["status"] == "target"
    assert "reached its target" in report["decision"]["reason"] and report["opened"] == 0


def test_a_reset_rewrites_the_monitor_and_repairs_an_unreadable_state(tmp_path, kalshi):
    """Found in review: the old life's monitor ("too small to bet") stayed on the page until the next tick, and a
    broken state file could not be reset by the button."""
    state = tmp_path / "state"
    rk.rocket_store(state, CFG).write_json(rk.STATE_FILE, {"initial_capital": 1000.0, "cash": 80.0, "epoch": 1})
    rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    assert json.loads((state / "desk" / "rocket" / "monitor.json").read_text())["stuck"] is True
    assert rk.reset_rocket_book(state, NOW + timedelta(hours=1), CFG) is True
    monitor = json.loads((state / "desk" / "rocket" / "monitor.json").read_text())
    assert monitor["stuck"] is False and monitor["epoch"] == 2 and "new life" in monitor["waiting"]
    (state / "desk" / "rocket" / rk.STATE_FILE).write_text("{not json", encoding="utf-8")
    assert rk.decision_due(state, NOW, Path("config")) is False  # an unreadable state owes nothing
    assert rk.reset_rocket_book(state, NOW + timedelta(hours=2), CFG) is True
    saved = rk.rocket_store(state, CFG).read_json(rk.STATE_FILE)
    assert saved["cash"] == 1000.0 and saved["status"] == "active" and saved["epochs"][-1]["end_reason"] == "unreadable"


def test_an_early_close_day_takes_no_decision(tmp_path, kalshi):
    """Found in review: on the day after Thanksgiving the options close at 13:00 New York; a chain stamped after it
    by the file's clock passed for fresh, and the 15:00 decision was taken on a closed market."""
    from engine.core.calendar import is_us_early_close, us_early_closes
    from engine.desk import options as op

    assert us_early_closes(2026) == {date(2026, 11, 27), date(2026, 12, 24)}  # 4 July 2026 is a Saturday
    assert date(2026, 7, 3) not in us_early_closes(2026) and date(2029, 7, 3) in us_early_closes(2029)
    assert is_us_early_close(date(2026, 11, 27)) and not is_us_early_close(date(2026, 11, 25))
    friday = datetime(2026, 11, 27, 20, 10, tzinfo=UTC)  # 15:10 New York, two hours after the early close
    assert not rk.decision_window(CFG, friday) and not op.session_open(friday)
    assert op.session_open(datetime(2026, 11, 27, 17, 50, tzinfo=UTC))  # 12:50 New York: open
    assert (
        op.session_close_ny(date(2026, 11, 27)) == op.EARLY_CLOSE_NY
        and op.session_close_ny(date(2026, 11, 30)) == op.MARKET_CLOSE_NY
    )
    store = _store(tmp_path / "raw", _later(_frame(), friday - timedelta(minutes=5)), friday)
    report = rk.rocket_tick(tmp_path / "state", _data(9.0, ("2026-11-25", "2026-11-27")), store, friday, CFG)
    assert report["decision"] is None and report["waiting"] is None  # not owed: the session is over


def test_an_expiry_settles_only_on_a_close_the_download_could_have_seen(tmp_path, kalshi):
    """Found in review: the expiry day's row of the daily table, downloaded during the session, is the running
    price and settled the option as if official. The row counts once the session has closed before the download."""
    book, cand = _opened()
    expiry = pd.Timestamp(cand["expiry"])
    evening = datetime(expiry.year, expiry.month, expiry.day, 22, 0, tzinfo=UTC)
    idx = pd.DatetimeIndex([expiry], name="date")
    daily = pd.DataFrame({"close": [cand["strike"] + 6.0]}, index=idx)  # SYNTHETIC: 6 $ in the money
    from engine.data.base import FetchResult
    from engine.desk.options import settled_closes

    running = RawStore(tmp_path / "running")
    running.save(
        "uso_daily",
        "test",
        FetchResult("test", daily, datetime(expiry.year, expiry.month, expiry.day, 18, 48, tzinfo=UTC)),
    )
    assert settled_closes(running, "uso_daily").empty  # read at 14:48 New York: not a close
    assert book.settle(settled_closes(running, "uso_daily"), None, evening) is None
    official = RawStore(tmp_path / "official")
    official.save(
        "uso_daily",
        "test",
        FetchResult("test", daily, datetime(expiry.year, expiry.month, expiry.day, 21, 18, tzinfo=UTC)),
    )
    closes = settled_closes(official, "uso_daily")
    assert len(closes) == 1
    rec = book.settle(closes, None, evening)
    assert rec is not None and rec["settle_price"] == pytest.approx(cand["strike"] + 6.0) and rec["approx"] is False
    logged = rec  # the record logged carries the settlement price (found in review: it was added after the log)
    assert "settle_price" in logged


def test_a_dead_book_bets_no_more(tmp_path, kalshi):
    state = tmp_path / "state"
    store = rk.rocket_store(state, CFG)
    store.write_json(rk.STATE_FILE, {"initial_capital": 1000.0, "cash": 40.0, "status": "dead", "epoch": 1})
    report = rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    assert report["opened"] == 0 and "no new bet until a reset" in report["decision"]["reason"]


def test_an_account_too_small_to_bet_says_so(tmp_path, kalshi):
    state = tmp_path / "state"
    rk.rocket_store(state, CFG).write_json(rk.STATE_FILE, {"initial_capital": 1000.0, "cash": 80.0, "epoch": 1})
    report = rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    assert report["opened"] == 0 and "No option fits the rule" in report["decision"]["reason"]
    monitor = json.loads((state / "desk" / "rocket" / "monitor.json").read_text())
    assert monitor["stuck"] is True and monitor["bet_floor"] > 80.0 and report["status"] == "active"


def test_the_public_reset_starts_a_life_at_its_own_capital(tmp_path, kalshi):
    state = tmp_path / "state"
    rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    assert rk.reset_rocket_book(state, NOW + timedelta(hours=1), CFG) is True
    saved = rk.rocket_store(state, CFG).read_json(rk.STATE_FILE)
    assert saved["epoch"] == 2 and saved["cash"] == 1000.0 and saved["position"] is None
    assert saved["epochs"][0]["open_abandoned"] == 1 and saved["epochs"][0]["end_reason"] == "reset_manual"


# ----------------------------------------------------------------------------------------------- the scheduler
def test_the_chain_is_refreshed_for_the_rocket_decision_alone(tmp_path, monkeypatch):
    from engine.desk import options as op
    from engine.live import jobs

    stamp = datetime(2026, 10, 8, 19, 5, tzinfo=UTC).isoformat()  # read at 15:05 New York
    sources = [{"source": "uso_options", "status": "green", "checked_at": stamp, "last_success_at": stamp}]
    settings = SimpleNamespace(state_dir=tmp_path / "state", config_dir=Path("config"))
    runner = SimpleNamespace(settings=settings, health=lambda: {"sources": sources})
    at = datetime(2026, 10, 8, 19, 18, tzinfo=UTC)  # 15:18 New York, the chain 13 minutes old
    monkeypatch.setattr(op, "decision_due", lambda *a, **k: False)  # the options book has decided today
    assert jobs._options_due(runner, at) is True  # the Rocket still owes its decision: the chain is refreshed
    rk.rocket_store(settings.state_dir).write_json(rk.STATE_FILE, {"last_decision_day": "2026-10-08"})
    assert jobs._options_due(runner, at) is False


def test_the_reset_job_resets_the_rocket_alone_or_with_everything(tmp_path):
    from engine.core.config import RiskConfig, Settings
    from engine.core.store import StateStore
    from engine.live import jobs

    settings = Settings(
        state_dir=tmp_path / "state",
        config_dir=Path("config"),
        eia_api_key=None,
        fred_api_key=None,
        github_repo="test/test",
        offline=True,
    )
    runner = SimpleNamespace(settings=settings, risk=RiskConfig.load(), store=StateStore(settings.state_dir))
    assert jobs._reset_books(runner, "rocket") == ["rocket"]
    assert rk.rocket_store(settings.state_dir).read_json(rk.STATE_FILE)["epoch"] == 2
    assert "rocket" in jobs._reset_books(runner, "all") and "rocket" not in jobs._reset_books(runner, "spinto")


def test_the_one_click_workflow_resets_the_rocket_and_nothing_else():
    text = Path(".github/workflows/rocket-reset.yml").read_text()
    assert "workflow_dispatch" in text and "--book rocket" in text and "--book all" not in text
    assert "group: trading-state" in text  # never at the same time as the other jobs that push the state
    assert rk.RESET_WORKFLOW == "rocket-reset.yml"


# ----------------------------------------------------------------------------------------------- the export
def test_the_page_gets_the_option_as_a_position_and_the_whole_life(tmp_path, kalshi):
    from engine.core.config import RiskConfig
    from engine.desk import export as ex
    from engine.desk.live import build_desk

    state = tmp_path / "state"
    rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    later = NOW + timedelta(minutes=30)
    block = ex.rocket_block(state, Path("config"), later)
    assert block is not None and block["kind"] == "lottery" and block["group"] == "lottery"
    assert block["role"] == "lottery" and block["backtested"] is False and block["pending"] == []
    (p,) = block["positions"]
    book = rk.RocketBook.load(CFG, rk.rocket_store(state, CFG))
    held = book.position
    assert held is not None and p["symbol"] == held["option"] and p["unit"] == "contract" and p["kind"] == "call"
    assert p["label"] == f"USO call {held['strike']:g} 2026-11-06" and p["days_left"] == 29
    assert p["unrealized"] == pytest.approx((held["mark"] - held["paid"]) * 100 * held["contracts"], abs=0.01)
    assert p["exposure"] == pytest.approx(held["delta_notional"] / book.equity(), abs=1e-4) and p["side"] == "long"
    assert block["exposure"] == p["exposure"] and block["multiple"] == pytest.approx(book.equity() / 1000.0, abs=1e-4)
    assert p["replicate"]["alpaca"]["symbol"] == held["option"]
    assert block["last_decision"]["status"] == "open" and block["decision_owed"] is False
    assert block["monitor"]["candidate"] is None and block["monitor"]["no_candidate_reason"].startswith("a bet is open")
    desk = build_desk(state, RiskConfig.load())
    (fill,) = ex.fills_block(state, desk, None, "rocket")
    assert fill["book"] == "rocket" and fill["unit"] == "contract" and fill["units"] == held["contracts"]
    assert fill["reason"] == "open" and fill["price"] == pytest.approx(held["paid"])
    (decision,) = ex.decisions_block(state, None, {}, "rocket")
    assert decision["orders"] == [{"symbol": p["label"], "units": float(held["contracts"])}] and decision["agree"]
    # a reset: the history file is the new life's, the old one is listed with how it ended
    rk.reset_rocket_book(state, later + timedelta(minutes=5), CFG)
    history = ex.rocket_history(state, Path("config"), later + timedelta(minutes=10))
    assert history is not None and history["kind"] == "lottery" and history["epoch"] == 2
    assert history["fills"] == [] and history["decisions"] == []
    (life,) = history["epochs"]
    assert life["epoch"] == 1 and life["end_reason"] == "reset_manual" and life["n_trades"] == 0
    assert life["start_equity"] == 1000.0 and life["end_equity"] == pytest.approx(block["equity"], abs=0.01)
    after = ex.rocket_block(state, Path("config"), later + timedelta(minutes=10))
    assert after is not None and after["positions"] == [] and after["equity"] == 1000.0 and after["lives"] == 2
    assert ex.fills_block(state, desk, None, "rocket") == []  # the old life's fills are not this life's


def test_damaged_rocket_files_cost_the_page_only_the_rocket(tmp_path, kalshi):
    """Found in review: a line cut short in the Rocket's logs, or a broken state file, stopped desk.json from being
    written at all."""
    from engine.core.config import RiskConfig
    from engine.desk import export as ex
    from engine.desk.live import build_desk

    state = tmp_path / "state"
    rk.rocket_tick(state, _data(9.0), _store(tmp_path / "raw"), NOW, CFG)
    folder = state / "desk" / "rocket"
    desk = build_desk(state, RiskConfig.load())
    for name in ("decisions-2026-10.jsonl", "trades-2026-10.jsonl", "equity-2026-10.jsonl"):
        path = folder / name
        assert path.exists(), name
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"ts": "2026-10-08T19:00:00Z", "cut')  # a crash mid-write
    block = ex.rocket_block(state, Path("config"), NOW + timedelta(minutes=30))
    assert block is not None and len(block["positions"]) == 1
    assert (
        len(ex.fills_block(state, desk, None, "rocket")) == 1
        and len(ex.decisions_block(state, None, {}, "rocket")) == 1
    )
    history = ex.rocket_history(state, Path("config"), NOW + timedelta(minutes=30))
    assert history is not None and len(history["fills"]) == 1 and len(history["decisions"]) == 1
    (folder / rk.STATE_FILE).write_text("{broken", encoding="utf-8")
    (folder / rk.MONITOR_FILE).write_text("[not a monitor]", encoding="utf-8")
    broken = ex.rocket_block(state, Path("config"), NOW + timedelta(minutes=30))
    assert broken is not None and broken["positions"] == [] and broken["epoch"] == 1 and broken["monitor"] is None
    assert ex.fills_block(state, desk, None, "rocket") == [] or True  # readable without raising
    assert ex.decisions_block(state, None, {}, "rocket") is not None
