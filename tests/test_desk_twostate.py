"""The two-state reading of the option chain (phase 22, ``engine/desk/twostate.py``), and the event budget it
feeds (``docs/RESEARCH.md`` 17 B and C).

The chains here are BUILT: quotes priced from a known distribution, so that what the fit recovers can be checked
against what was put in. A real chain cannot do that - it never says what generated it - and the one real chain on
file (13 November 2026) is used for the one thing it can prove: that the reading runs end to end on it and refuses
to call it two states.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine.core.store import StateStore
from engine.desk import twostate as ts
from engine.desk.options import Chain

NOW = datetime(2026, 11, 13, 21, 17, tzinfo=UTC)
EXPIRY = date(2026, 12, 31)


def _chain(price: float, quotes: list[tuple[str, float, float, float]], expiry: date = EXPIRY) -> Chain:
    """A chain from (right, strike, bid, ask) rows."""
    frame = pd.DataFrame(
        [
            {
                "expiry": pd.Timestamp(expiry),
                "right": right,
                "strike": strike,
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2,
                "delta": None,
                "underlying_price": price,
                "quote_ts": NOW,
            }
            for right, strike, bid, ask in quotes
        ]
    )
    return Chain("USO", frame, NOW, price, None)


def _priced(
    forward: float, years: float, strikes: list[float], price_of, half_spread: float = 0.02
) -> list[tuple[str, float, float, float]]:
    """Quotes around the given prices: a put below the forward, a call above, each with the same half spread."""
    rows = []
    for k in strikes:
        call = k > forward
        mid = float(price_of(k, call))
        if mid < 0.02:
            continue
        rows.append(("C" if call else "P", k, round(mid - half_spread, 4), round(mid + half_spread, 4)))
        # the other right at the same strike, by parity, so that the forward can be recovered
        other = mid - (forward - k) if call else mid + (forward - k)
        if other > 0.02:
            rows.append(("P" if call else "C", k, round(other - half_spread, 4), round(other + half_spread, 4)))
    return rows


def _mixture_chain(w: float, low: float, high: float, vol: float = 0.25, spot: float = 100.0) -> Chain:
    """A chain priced from a mixture of two lognormals with known parameters."""
    years = ts.years_to_expiry(EXPIRY, NOW)
    forward = w * low + (1 - w) * high
    d = math.log(high / low)

    def price_of(k: float, call: bool) -> float:
        return float(ts.mixture_prices(forward, np.array([k]), np.array([call]), years, w, d, vol, vol)[0])

    strikes = [round(x, 1) for x in np.arange(spot * 0.55, spot * 1.5, spot * 0.025)]
    return _chain(spot, _priced(forward, years, strikes, price_of))


def _single_chain(vol: float = 0.45, spot: float = 100.0) -> Chain:
    years = ts.years_to_expiry(EXPIRY, NOW)
    forward = spot * 1.01

    def price_of(k: float, call: bool) -> float:
        return float(ts.black(forward, np.array([k]), vol * math.sqrt(years), np.array([call]))[0])

    strikes = [round(x, 1) for x in np.arange(spot * 0.55, spot * 1.5, spot * 0.025)]
    return _chain(spot, _priced(forward, years, strikes, price_of))


# ----------------------------------------------------------------------------------------------- the pricing
def test_black_prices_satisfy_put_call_parity_and_the_implied_volatility_is_its_inverse():
    f, k, sd = 90.0, 95.0, 0.4
    call = float(ts.black(f, np.array([k]), sd, np.array([True]))[0])
    put = float(ts.black(f, np.array([k]), sd, np.array([False]))[0])
    assert call - put == pytest.approx(f - k, abs=1e-9)
    years = 0.25
    vol = sd / math.sqrt(years)
    assert ts.implied_vol(call, f, k, years, True) == pytest.approx(vol, abs=1e-4)
    # a price no volatility inside the bounds can make is not forced into one
    assert ts.implied_vol(f * 1.5, f, k, years, True) is None


def test_the_two_state_means_keep_the_forward_whatever_the_weight():
    for w in (0.05, 0.4, 0.95):
        low, high = ts.state_forwards(100.0, w, 0.5)
        assert w * low + (1 - w) * high == pytest.approx(100.0, abs=1e-9)
        assert math.log(high / low) == pytest.approx(0.5, abs=1e-12)


# ----------------------------------------------------------------------------------------------- the fit
def test_a_chain_priced_from_two_states_is_read_as_two_states_with_the_weights_it_was_made_with():
    chain = _mixture_chain(w=0.35, low=75.0, high=115.0, vol=0.22)
    reading = ts.read_chain(chain)
    head = ts.headline_of(reading)
    assert head is not None and head["two_states"] is True and head["why_not"] == []
    mix = head["mixture"]
    forward = head["forward"]
    assert mix["w_low"] == pytest.approx(0.35, abs=0.06)
    assert forward * (1 + mix["low_move"]) == pytest.approx(75.0, rel=0.04)
    assert forward * (1 + mix["high_move"]) == pytest.approx(115.0, rel=0.04)
    # and the mixture must be the better description, by the registered margin
    assert mix["rmse"] <= ts.IMPROVEMENT * head["single"]["rmse"]
    assert ts.state_moves(reading) == (mix["low_move"], mix["high_move"])


def test_a_chain_priced_from_one_lognormal_is_not_called_two_states_and_says_why():
    reading = ts.read_chain(_single_chain(vol=0.45))
    head = ts.headline_of(reading)
    assert head is not None and head["two_states"] is False
    assert head["why_not"], "a refusal must say which condition failed"
    assert ts.state_moves(reading) is None  # nothing downstream may read states that were not found
    # the single lognormal recovers the volatility it was priced with, and its band is shown instead
    assert head["single"]["vol"] == pytest.approx(0.45, abs=0.02)
    assert head["single"]["low_move"] < 0 < head["single"]["high_move"]


def test_the_quantiles_come_from_the_fit_and_are_ordered_with_the_tails_agreeing():
    reading = ts.read_chain(_single_chain(vol=0.45))
    head = ts.headline_of(reading)
    q = head["distribution"]["quantiles"]
    assert q["q05"] < q["q25"] < q["q50"] < q["q75"] < q["q95"]
    tails = ts.tail_moves(reading)
    assert tails == (q["q05"], q["q95"])
    # a 20 % fall weighs less than a 10 % fall, and both are probabilities
    down = head["distribution"]["tails"]
    assert 0 < down["down_20"] < down["down_10"] < 1
    assert 0 < down["up_20"] < down["up_10"] < 1


def test_a_verdict_needs_every_one_of_its_five_conditions():
    chain = _mixture_chain(w=0.35, low=75.0, high=115.0, vol=0.22)
    years = ts.years_to_expiry(EXPIRY, NOW)
    quotes = ts.otm_quotes(chain.frame, chain.price, years)
    single, mix = ts.fit_single(quotes), ts.fit_mixture(quotes)
    assert ts.verdict(quotes, single, mix)[0] is True
    # a weight outside the band is a tail, not a state
    assert ts.verdict(quotes, single, {**mix, "w": 0.01})[0] is False
    # states closer than one state's own width overlap
    assert ts.verdict(quotes, single, {**mix, "d": 0.001})[0] is False
    # a mixture that is not clearly better than one lognormal is not two states
    assert ts.verdict(quotes, single, {**mix, "rmse": single["rmse"]})[0] is False
    # a fit that misses the quotes describes nothing
    assert ts.verdict(quotes, single, {**mix, "rmse": ts.MAX_RMSE * 2})[0] is False


def test_a_chain_with_too_few_quotes_on_one_side_is_refused_before_anything_is_fitted():
    years = ts.years_to_expiry(EXPIRY, NOW)
    forward = 101.0

    def price_of(k: float, call: bool) -> float:
        return float(ts.black(forward, np.array([k]), 0.4 * math.sqrt(years), np.array([call]))[0])

    strikes = [round(x, 1) for x in np.arange(60.0, 100.0, 2.5)]  # puts only
    reading = ts.read_chain(_chain(100.0, _priced(forward, years, strikes, price_of)))
    head = ts.headline_of(reading)
    assert head["two_states"] is False and "too few quotes" in " ".join(head["why_not"])


def test_a_quote_wider_than_a_quarter_of_its_mid_is_not_read():
    years = ts.years_to_expiry(EXPIRY, NOW)
    rows = _priced(101.0, years, [90.0, 95.0, 105.0, 110.0], lambda k, c: 3.0, half_spread=0.1)
    narrow = ts.otm_quotes(_chain(100.0, rows).frame, 100.0, years)
    rows_wide = [(r, k, round(3.0 - 1.0, 4), round(3.0 + 1.0, 4)) for r, k, _, _ in rows]
    wide = ts.otm_quotes(_chain(100.0, rows_wide).frame, 100.0, years)
    assert narrow is not None and narrow.n_puts + narrow.n_calls > 0
    assert wide is None or wide.n_puts + wide.n_calls == 0


# ----------------------------------------------------------------------------------------------- the event budget
def test_the_event_budget_divides_the_budget_by_the_adverse_move_on_each_side():
    long_cap, short_cap, text = ts.event_caps(0.5, (-0.25, 0.32), None)
    assert long_cap == pytest.approx(2.0) and short_cap == pytest.approx(0.5 / 0.32)
    assert "event budget" in text and "25" in text
    # without a reading: the largest one-session move of the year, the same on both sides
    long_cap, short_cap, text = ts.event_caps(0.5, None, 0.16)
    assert long_cap == short_cap == pytest.approx(0.5 / 0.16) and "largest one-session move" in text
    # with neither, no ceiling at all rather than an invented one
    assert ts.event_caps(0.5, None, None) == (None, None, "event budget: nothing to size it on today")


def test_the_largest_move_reads_only_the_window_it_is_given():
    returns = [0.5] + [0.01] * 300
    assert ts.largest_move(returns, sessions=252) == pytest.approx(0.01)
    assert ts.largest_move(returns, sessions=400) == pytest.approx(0.5)
    assert ts.largest_move([]) is None


def test_a_scenario_is_a_straight_line_on_the_exposure_and_nothing_without_two_states():
    assert ts.scenario_pnl(0.45, 10_000.0, (-0.25, 0.30)) == {"low": -1125.0, "high": 1350.0}
    assert ts.scenario_pnl(0.0, 10_000.0, (-0.25, 0.30)) == {"low": -0.0, "high": 0.0}
    assert ts.scenario_pnl(0.45, 10_000.0, None) is None


# ----------------------------------------------------------------------------------------------- the archive
def test_the_reading_is_archived_once_per_chain_and_the_quotes_once_per_session(tmp_path, monkeypatch):
    chain = _mixture_chain(w=0.3, low=80.0, high=120.0)
    store = StateStore(tmp_path)
    monkeypatch.setattr(ts, "load_chain", lambda *a, **k: chain)
    first = ts.record_reading(None, store, NOW)
    assert first["reading"] == "written" and first["chain"] == "archived"
    again = ts.record_reading(None, store, NOW + timedelta(minutes=30))
    assert again["reading"] == "already read" and "chain" not in again
    assert len(store.tail_jsonl(ts.TWOSTATE_LOG, 10)) == 1
    assert len(store.tail_jsonl(ts.CHAIN_LOG, 10)) == 1
    # the archived reading keeps the headline whole and the other expiries short
    archived = store.tail_jsonl(ts.TWOSTATE_LOG, 1)[0]
    head = ts.headline_of(archived)
    assert head["single"]["vol"] > 0 and head["two_states"] is True
    assert ts.state_moves(archived) == ts.state_moves(ts.read_chain(chain))


def test_a_reading_is_used_only_while_it_is_fresh_and_never_from_the_future(tmp_path, monkeypatch):
    store = StateStore(tmp_path)
    monkeypatch.setattr(ts, "load_chain", lambda *a, **k: _mixture_chain(w=0.3, low=80.0, high=120.0))
    ts.record_reading(None, store, NOW)
    assert ts.reading_for(store, NOW + timedelta(hours=1)) is not None
    assert ts.reading_for(store, NOW + timedelta(hours=ts.MAX_AGE_HOURS + 1)) is None
    assert ts.reading_for(store, NOW - timedelta(hours=1)) is None


def test_the_term_structure_is_flagged_only_when_the_near_expiry_is_five_points_above():
    reading = {
        "term": [
            {"expiry": "2026-11-18", "dte": 5, "atm_vol": 0.60},
            {"expiry": "2026-12-31", "dte": 48, "atm_vol": 0.47},
        ]
    }
    flag = ts.term_flag(reading)
    assert flag["flag"] is True and flag["gap"] == pytest.approx(0.13)
    calm = ts.term_flag({"term": [dict(reading["term"][0], atm_vol=0.36), reading["term"][1]]})
    assert calm["flag"] is False and calm["gap"] < 0
    assert ts.term_flag({"term": []}) is None


# ----------------------------------------------------------------------------------------------- the real chain
FIXTURE = Path(__file__).parent / "fixtures" / "cboe" / "uso_chain_2026-11-13.parquet"


def _real_chain() -> Chain:
    frame = pd.read_parquet(FIXTURE)
    stamp = pd.Timestamp(frame["quote_ts"].iloc[0])
    quote_ts = (stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)).to_pydatetime()
    frame["expiry"] = pd.to_datetime(frame["expiry"]).dt.normalize()
    frame["mid"] = (frame["bid"] + frame["ask"]) / 2
    return Chain("USO", frame, quote_ts, float(frame["underlying_price"].iloc[0]), None)


def test_on_the_real_chain_of_13_november_the_mixture_fits_the_quotes_but_is_not_two_states():
    """What a real chain can prove, and the result of 17 M: the mixture describes the smile far better than one
    lognormal (every quote inside its bid and ask), and the separation condition still refuses to call its two
    components two STATES. Two humps in the fit are not two prices, and the page says one state."""
    reading = ts.read_chain(_real_chain())
    head = ts.headline_of(reading)
    assert head["expiry"] == "2026-12-31" and head["dte"] == 48 and head["n_puts"] >= 20 and head["n_calls"] >= 20
    assert head["mixture"]["rmse"] < 0.3 and head["mixture"]["inside"] == 1.0  # inside the quotes
    assert head["mixture"]["rmse"] <= ts.IMPROVEMENT * head["single"]["rmse"]  # and better than one lognormal
    assert head["two_states"] is False
    assert head["why_not"] == ["the two states overlap: their means are closer than one state's own width"]
    assert ts.state_moves(reading) is None
    # what the page shows instead: the options' own one-in-twenty moves by that date, and the term structure
    low, high = ts.tail_moves(reading)
    assert low == pytest.approx(-0.256, abs=0.01) and high == pytest.approx(0.315, abs=0.01)
    flag = ts.term_flag(reading)
    assert flag["near"]["dte"] == 5 and flag["far"]["dte"] == 48 and flag["flag"] is False
    assert [e["dte"] for e in reading["expiries"]] == [14, 21, 28, 35, 41, 48, 63]


def test_the_quotes_of_a_real_chain_are_archived_as_rows_a_later_measurement_can_read():
    record = ts.chain_record(_real_chain())
    assert record["day"] == "2026-11-13" and record["symbol"] == "USO"
    assert record["columns"] == list(ts.CHAIN_COLUMNS) and len(record["rows"]) > 200
    assert all(len(r) == len(ts.CHAIN_COLUMNS) for r in record["rows"])
    by_name = dict(zip(ts.CHAIN_COLUMNS, record["rows"][0], strict=True))
    assert by_name["right"] in ("P", "C") and by_name["ask"] > by_name["bid"] > 0
    # every row is out of the money against its own expiry's forward, which is archived with it
    for row in record["rows"]:
        r = dict(zip(ts.CHAIN_COLUMNS, row, strict=True))
        forward = record["forwards"][r["expiry"]]
        assert (r["strike"] > forward) if r["right"] == "C" else (r["strike"] < forward)
