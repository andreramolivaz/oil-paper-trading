"""The options book on REAL chains (tests/fixtures/cboe, 2026-10-08) and its bookkeeping on made-up closes.

The chains are real delayed quotes; the forecasts and the settlement prices used to exercise the arithmetic are
SYNTHETIC and say so where they appear.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine.core.config import RiskConfig
from engine.data.adapters.cboe import parse_chain, parse_occ
from engine.data.base import FetchResult
from engine.data.raw_store import RawStore
from engine.desk import options as op
from engine.desk.data import DeskData, VehicleSeries

FIXTURES = Path(__file__).parent / "fixtures" / "cboe"
RISK = RiskConfig.load()
NOW = datetime(2026, 10, 8, 18, 30, tzinfo=UTC)  # 14:30 New York, 19 minutes after the USO quotes
CFG = dataclasses.replace(op.OptionsConfig(), decision_time_ny=(14, 0))


def _payload(symbol: str) -> dict:
    return json.loads((FIXTURES / f"{symbol.lower()}_20261008.json").read_text())


def _store(tmp_path: Path, closes: dict[str, float] | None = None) -> RawStore:
    """A raw store holding the two real chains and (optionally) SYNTHETIC daily closes of USO."""
    store = RawStore(tmp_path)
    for symbol in ("USO", "BNO"):
        frame = parse_chain(_payload(symbol))
        store.save(
            f"{symbol.lower()}_options", "cboe", FetchResult("cboe", frame, datetime(2026, 10, 8, 18, 12, tzinfo=UTC))
        )
    if closes:
        idx = pd.DatetimeIndex(pd.to_datetime(list(closes)), name="date")
        daily = pd.DataFrame(
            {"close": list(closes.values()), "published_at": pd.Timestamp("2026-10-08", tz="UTC")}, index=idx
        )
        store.save("uso_daily", "sintetico", FetchResult("sintetico", daily, datetime(2026, 10, 8, tzinfo=UTC)))
    return store


def _data(forecast_wti: float | None, forecast_brent: float | None = 2.0) -> DeskData:
    """SYNTHETIC forecasts for the two series the book reads (the last row is the decision day)."""
    idx = pd.DatetimeIndex(pd.to_datetime(["2026-10-07", "2026-10-08"]), name="date")

    def series(vehicle: str, value: float | None) -> VehicleSeries:
        empty = pd.Series(dtype="float64")
        forecasts = pd.DataFrame({"combined": [value, value]}, index=idx, dtype="float64")
        return VehicleSeries(
            vehicle, pd.DataFrame(), empty, empty, forecasts, pd.Series([0.42, 0.42], index=idx), empty, empty, empty
        )

    data = DeskData()
    data.series = {"MCL": series("MCL", forecast_wti), "BNO": series("BNO", forecast_brent)}
    return data


# ----------------------------------------------------------------------------------------------- the adapter
def test_occ_symbols():
    assert parse_occ("BNO261016C00064000") == ("BNO", pd.Timestamp("2026-10-16"), "C", 64.0)
    assert parse_occ("USO261120P00132500") == ("USO", pd.Timestamp("2026-11-20"), "P", 132.5)
    assert parse_occ("BRENT") is None and parse_occ("USO269999P00132500") is None


def test_parse_chain_keeps_the_feed_values_and_the_expiry_window():
    payload = _payload("USO")
    frame = parse_chain(payload)
    assert len(frame) == len(payload["data"]["options"]) == 190
    assert set(frame["expiry"].dt.strftime("%Y-%m-%d")) == {"2026-11-06", "2026-11-20"}
    assert frame["underlying_price"].iloc[0] == 148.35 and frame["iv30"].iloc[0] == pytest.approx(45.887)
    assert str(frame["quote_ts"].iloc[0]) == "2026-10-08 18:11:13+00:00"  # the payload stamp is UTC
    row = frame[frame["option"] == "USO261120P00135000"].iloc[0]
    assert (row["bid"], row["ask"], row["strike"], row["right"]) == (3.55, 3.75, 135.0, "P")
    assert set(parse_chain(payload, max_days=30)["expiry"].dt.strftime("%Y-%m-%d")) == {"2026-11-06"}
    narrow = parse_chain(payload, moneyness=0.05)
    assert narrow["strike"].between(148.35 * 0.95, 148.35 * 1.05).all() and len(narrow) < len(frame)


# ----------------------------------------------------------------------------------------------- the chains
def test_the_wti_fund_chain_is_tradeable_and_the_brent_fund_chain_was_not(tmp_path):
    store = _store(tmp_path)
    uso = op.load_chain(store, "uso_options", "USO")
    bno = op.load_chain(store, "bno_options", "BNO")
    assert uso is not None and bno is not None
    assert uso.iv30 == pytest.approx(0.45887) and uso.today() == date(2026, 10, 8)
    q_uso, q_bno = op.chain_quality(uso, CFG), op.chain_quality(bno, CFG)
    assert q_uso["expiry"] == "2026-11-06" and q_uso["dte"] == 29  # the listed expiry closest to 30 days
    assert q_uso["tradeable"] is True and q_uso["median_rel_spread"] < 0.15
    assert 0.40 < q_uso["atm_iv"] < 0.50 and 0.08 < q_uso["straddle_pct"] < 0.13
    assert q_bno["tradeable"] is False and q_bno["median_rel_spread"] > 0.40
    # the market priced a bigger move on the Brent fund than on the WTI fund
    assert q_bno["straddle_pct"] > q_uso["straddle_pct"]


def test_the_candidate_is_a_put_spread_inside_every_rule(tmp_path):
    chain = op.load_chain(_store(tmp_path), "uso_options", "USO")
    assert chain is not None
    cand, why = op.candidate(chain, CFG, equity=10_000.0)
    assert cand is not None and why == ""
    short, long = cand["short"], cand["long"]
    assert abs(abs(short["delta"]) - CFG.short_delta) <= CFG.delta_tolerance
    assert long["strike"] < short["strike"] < chain.price
    for leg in (short, long):
        mid = (leg["bid"] + leg["ask"]) / 2
        assert (leg["ask"] - leg["bid"]) / mid <= CFG.max_leg_spread
    # sold at bid + 25 % of the spread, bought at ask - 25 %: worse than the mid on both legs
    assert short["fill"] == pytest.approx(short["bid"] + 0.25 * (short["ask"] - short["bid"]))
    assert long["fill"] == pytest.approx(long["ask"] - 0.25 * (long["ask"] - long["bid"]))
    assert cand["credit"] == pytest.approx(short["fill"] - long["fill"]) and cand["credit"] < cand["credit_mid"]
    assert cand["width"] == short["strike"] - long["strike"] >= CFG.min_width_pct * chain.price
    assert cand["max_loss_per_contract"] == pytest.approx((cand["width"] - cand["credit"]) * 100 + 0.10)
    assert cand["max_loss_per_contract"] <= 0.05 * 10_000 and cand["contracts"] == 1
    assert cand["breakeven"] == pytest.approx(short["strike"] - cand["credit"], abs=0.006) and 21 <= cand["dte"] <= 45
    # the widest wing the budget allows: the next tradeable strike down would cost more than 5 % of equity
    puts = chain.frame[(chain.frame["right"] == "P") & (chain.frame["expiry"] == pd.Timestamp(cand["expiry"]))]
    lower = puts[(puts["strike"] < long["strike"]) & (puts["rel_spread"] <= CFG.max_leg_spread) & (puts["bid"] > 0)]
    for _, row in lower.iterrows():
        credit = short["fill"] - op.buy_price(row["bid"], row["ask"], CFG.fill_fraction)
        assert (short["strike"] - row["strike"] - credit) * 100 + 0.10 > 0.05 * 10_000


def test_the_candidate_scales_with_the_account_and_disappears_when_the_budget_is_too_small(tmp_path):
    chain = op.load_chain(_store(tmp_path), "uso_options", "USO")
    assert chain is not None
    big, _ = op.candidate(chain, CFG, equity=200_000.0)
    assert big is not None and big["max_loss_per_contract"] * big["contracts"] <= 0.05 * 200_000
    small, why = op.candidate(chain, CFG, equity=1_500.0)  # 75 $ of risk cannot pay for a 3 % wing
    assert small is None and "budget" in why


def test_the_brent_fund_chain_gives_no_candidate_and_says_which_quote_was_too_wide(tmp_path):
    chain = op.load_chain(_store(tmp_path), "bno_options", "BNO")
    assert chain is not None
    cand, why = op.candidate(chain, CFG, equity=10_000.0)
    assert cand is None and "larga" in why and "del prezzo" in why


def test_paper_fills_sit_a_quarter_of_the_spread_inside_the_bad_side():
    assert op.sell_price(1.00, 1.20, 0.25) == pytest.approx(1.05)
    assert op.buy_price(1.00, 1.20, 0.25) == pytest.approx(1.15)
    assert op.sell_price(1.00, 1.20, 0.0) == 1.00 and op.buy_price(1.00, 1.20, 0.0) == 1.20


# ----------------------------------------------------------------------------------------------- bookkeeping
def _open_book(tmp_path) -> tuple[op.OptionsBook, dict, op.Chain]:
    chain = op.load_chain(_store(tmp_path), "uso_options", "USO")
    assert chain is not None
    cand, _ = op.candidate(chain, CFG, 10_000.0)
    assert cand is not None
    book = op.OptionsBook.load(CFG, RISK, None)
    book.open(cand, NOW, date(2026, 10, 8), 7.0, "test")
    return book, cand, chain


def test_opening_costs_the_distance_from_the_mid_and_nothing_else(tmp_path):
    book, cand, chain = _open_book(tmp_path)
    assert book.cash == pytest.approx(10_000 + cand["credit"] * 100 - 0.10)
    assert book.equity() == pytest.approx(10_000 - (cand["credit_mid"] - cand["credit"]) * 100 - 0.10)
    assert book.max_loss_open() == pytest.approx((cand["width"] - cand["credit_mid"]) * 100)
    book.mark({"USO": chain}, NOW)
    s = book.structures[0]
    assert s["mark"] == pytest.approx(cand["credit_mid"], abs=1e-4) and s["mark_stale"] is False
    assert s["delta_notional"] > 0  # short a put spread = long the fund
    book.mark({}, NOW + timedelta(hours=1))  # no chain: the last mark stays and is flagged
    assert book.structures[0]["mark"] == s["mark"] and book.structures[0]["mark_stale"] is True


@pytest.mark.parametrize("where", ["above", "between", "below"])
def test_settlement_pays_the_intrinsic_value_and_never_more_than_the_maximum_loss(tmp_path, where):
    book, cand, _ = _open_book(tmp_path)
    k_short, k_long = cand["short"]["strike"], cand["long"]["strike"]
    close = {"above": k_short + 5.0, "between": (k_short + k_long) / 2, "below": k_long - 20.0}[
        where
    ]  # SYNTHETIC closes
    expiry = pd.Timestamp(cand["expiry"])
    after = datetime(expiry.year, expiry.month, expiry.day, 22, 0, tzinfo=UTC)  # 17:00 New York (EST)
    assert book.settle({"USO": pd.Series({expiry: close})}, {}, after - timedelta(days=1)) == []  # not due yet
    done = book.settle({"USO": pd.Series({expiry: close})}, {}, after)
    assert len(done) == 1 and book.structures == [] and book.n_closed == 1
    expected = {"above": cand["credit"] * 100 - 0.10, "between": (cand["credit"] - cand["width"] / 2) * 100 - 0.10}
    if where == "below":
        assert done[0]["pnl"] == pytest.approx(-cand["max_loss_per_contract"]) and done[0][
            "r_multiple"
        ] == pytest.approx(-1.0)
    else:
        assert done[0]["pnl"] == pytest.approx(expected[where])
    assert book.equity() == pytest.approx(10_000 + done[0]["pnl"]) and book.realized_pnl == pytest.approx(
        done[0]["pnl"]
    )
    assert book.n_wins == (1 if where == "above" else 0)


def test_an_expired_structure_waits_for_the_official_close_then_falls_back_and_says_approx(tmp_path):
    book, cand, chain = _open_book(tmp_path)
    expiry = pd.Timestamp(cand["expiry"])
    evening = datetime(expiry.year, expiry.month, expiry.day, 22, 0, tzinfo=UTC)
    assert book.settle({"USO": pd.Series(dtype=float)}, {"USO": chain}, evening) == []
    assert book.structures[0]["awaiting_close"] is True
    late = book.settle({"USO": pd.Series(dtype=float)}, {"USO": chain}, evening + timedelta(days=5))
    assert len(late) == 1 and late[0]["approx"] is True and late[0]["settle_price"] == chain.price


def test_reset_archives_the_life_and_restarts_from_the_initial_capital(tmp_path):
    book, _, _ = _open_book(tmp_path)
    book.reset(NOW)
    assert book.epoch == 2 and book.cash == 10_000.0 and book.structures == [] and book.equity() == 10_000.0
    assert book.epochs[0]["epoch"] == 1 and book.epochs[0]["open_abandoned"] == 1


# ----------------------------------------------------------------------------------------------- the tick
def test_the_tick_sells_nothing_below_the_threshold_and_says_why(tmp_path):
    store, state = _store(tmp_path / "raw"), tmp_path / "state"
    report = op.options_tick(state, RISK, _data(3.9), store, NOW, CFG)
    assert report["opened"] == 0 and report["equity"] == 10_000.0
    uso, bno = report["decisions"]
    assert uso["underlying"] == "USO" and uso["action"] == "nessuna" and "sotto la soglia di +5" in uso["reason"]
    assert uso["candidate"] is not None  # the structure it WOULD sell is still on the record
    assert bno["action"] == "nessuna" and "minuti fa" in bno["reason"]  # the Brent chain was two hours old
    monitor = json.loads((state / "desk" / "opzioni" / "monitor.json").read_text())
    assert [u["symbol"] for u in monitor["underlyings"]] == ["USO", "BNO"]
    assert monitor["underlyings"][0]["gate"]["open"] is False


def test_the_tick_opens_one_structure_once_and_spaces_the_next(tmp_path):
    store, state = _store(tmp_path / "raw"), tmp_path / "state"
    first = op.options_tick(state, RISK, _data(7.0), store, NOW, CFG)
    assert first["opened"] == 1 and first["open"] == 1 and first["equity"] < 10_000.0
    assert (
        first["decisions"][0]["action"] == "aperta" and "Vendo 1 spread di put USO" in first["decisions"][0]["reason"]
    )
    again = op.options_tick(state, RISK, _data(7.0), store, NOW + timedelta(minutes=30), CFG)
    assert again["opened"] == 0 and again["decisions"] == [] and again["open"] == 1  # one decision a day
    # the next session: the forecast is still long, but the last entry was one session ago
    tomorrow = NOW + timedelta(days=1)
    data = _data(7.0)
    for series in data.series.values():
        series.forecasts.loc[pd.Timestamp("2026-10-09")] = 7.0
        series.vol.loc[pd.Timestamp("2026-10-09")] = 0.42
    fresh = op.load_chain(store, "uso_options", "USO")
    assert fresh is not None
    frame = fresh.frame.drop(columns=["mid", "rel_spread"])
    frame["quote_ts"] = pd.Timestamp(tomorrow - timedelta(minutes=10))
    store.save("uso_options", "cboe", FetchResult("cboe", frame, tomorrow))
    later = op.options_tick(state, RISK, data, store, tomorrow, CFG)
    assert later["opened"] == 0 and "una nuova ogni 10 sedute" in later["decisions"][0]["reason"]
    assert not op.decision_due(state, tomorrow)  # the day's decision is on record, whatever the config's clock


def test_stale_quotes_and_closed_markets_never_trade(tmp_path):
    store, state = _store(tmp_path / "raw"), tmp_path / "state"
    late = datetime(2026, 10, 8, 19, 40, tzinfo=UTC)  # 89 minutes after the quotes
    report = op.options_tick(state, RISK, _data(9.0), store, late, CFG)
    # no fresh chain at all: nothing to decide on. The decision stays owed (the next tick may have quotes) and
    # the monitor carries the reason
    assert report["opened"] == 0 and report["decisions"] == [] and op.decision_due(state, late) is True
    assert op.OptionsBook.load(CFG, RISK, op.options_store(state, CFG)).last_decision_day is None
    monitor = json.loads((state / "desk" / "opzioni" / "monitor.json").read_text())
    assert "servono meno di 45 minuti" in monitor["underlyings"][0]["gate"]["reason"]
    closed = datetime(2026, 10, 8, 20, 30, tzinfo=UTC)  # after the close: no decision at all
    assert op.options_tick(tmp_path / "other", RISK, _data(9.0), store, closed, CFG)["decisions"] == []
    saturday = datetime(2026, 10, 10, 18, 30, tzinfo=UTC)
    assert op.options_tick(tmp_path / "other", RISK, _data(9.0), store, saturday, CFG)["decisions"] == []
    assert op.session_day(saturday) is None and op.decision_window(CFG, NOW) and not op.decision_window(CFG, closed)


def test_a_structure_is_settled_by_the_tick_on_the_official_close(tmp_path):
    state = tmp_path / "state"
    store = _store(tmp_path / "raw")
    opened = op.options_tick(state, RISK, _data(7.0), store, NOW, CFG)
    assert opened["opened"] == 1
    book = op.OptionsBook.load(CFG, RISK, op.options_store(state, CFG))
    s = book.structures[0]
    closes = {s["expiry"]: s["short"]["strike"] + 10.0}  # SYNTHETIC close, above the short strike
    store2 = _store(tmp_path / "raw2", closes)
    expiry = pd.Timestamp(s["expiry"])
    after = datetime(expiry.year, expiry.month, expiry.day, 22, 0, tzinfo=UTC)  # 17:00 New York (EST)
    report = op.options_tick(state, RISK, _data(1.0), store2, after, CFG)
    assert report["settled"] == 1 and report["open"] == 0
    assert report["equity"] == pytest.approx(10_000 + s["credit"] * 100 - 0.10)
    trades = op.options_store(state, CFG).read_jsonl("trades")
    assert [t["action"] for t in trades] == ["open", "expiry"] and trades[1]["pnl"] > 0


def test_reset_through_the_public_function(tmp_path):
    state = tmp_path / "state"
    op.options_tick(state, RISK, _data(7.0), _store(tmp_path / "raw"), NOW, CFG)
    assert op.reset_options_book(state, RISK, NOW + timedelta(hours=1), CFG) is True
    saved = op.options_store(state, CFG).read_json(op.STATE_FILE)
    assert saved["epoch"] == 2 and saved["cash"] == 10_000.0 and saved["structures"] == []


# ----------------------------------------------------------------------------------------------- config
def test_the_config_on_file_is_inside_the_book_own_limits():
    cfg = op.load_options_config()
    assert cfg is not None and cfg.id == "opzioni"
    assert cfg.risk_per_structure * cfg.max_open <= 0.10  # never more than a tenth of the account at risk
    assert cfg.forecast_threshold == 5.0 and cfg.short_delta == 0.20 and cfg.fill_fraction == 0.25
    assert [u.symbol for u in cfg.underlyings] == ["USO", "BNO"] and cfg.decision_time_ny == (15, 0)
    for bad in ({"risk_per_structure": 0.2}, {"max_open": 9}, {"short_delta": 0.6}, {"fill_fraction": 0.8}):
        with pytest.raises(ValueError):
            dataclasses.replace(cfg, **bad)


# ----------------------------------------------------------------------------------------------- the model
def test_black_scholes_helpers_are_coherent():
    spot, years, vol = 150.0, 21 / 252, 0.45
    strike = op.put_strike_for_delta(spot, years, vol, 0.20)
    assert strike < spot and op.put_delta(spot, strike, years, vol) == pytest.approx(0.20, abs=1e-9)
    assert op.bs_put(spot, strike, 0.0, vol) == 0.0 and op.bs_put(100.0, 120.0, 0.0, vol) == 20.0
    assert op.bs_put(spot, strike, years, vol) < op.bs_put(spot, strike, years, vol * 1.2)  # more vol, dearer put
    assert op.bs_put(spot, strike, years, vol) > op.bs_put(spot, strike - 6.0, years, vol) > 0


def _replay_inputs(n: int = 900, seed: int = 4):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    price = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, size=n))), index=idx)
    implied = pd.Series(0.35 + 0.05 * np.sin(np.arange(n) / 50.0), index=idx)
    forecast = pd.Series(8.0 * np.sin(np.arange(n) / 35.0), index=idx)
    return price, implied, forecast


def test_the_replay_is_causal_bounded_and_respects_the_rules():
    price, implied, forecast = _replay_inputs()
    cfg = op.OptionsConfig()
    equity, trades = op.replay(price, implied, forecast, cfg)
    assert len(trades) > 5 and len(equity) == len(price)
    assert min(t["r"] for t in trades) >= -1.0 - 1e-9  # a structure never loses more than its defined risk
    for t in trades:  # entries only when the forecast known that day was above the threshold
        assert forecast[t["opened"]] >= cfg.forecast_threshold
        assert (t["closed"] - t["opened"]).days >= 28  # held the 21 sessions to expiry
    # truncating the inputs leaves the past of the equity path untouched
    cut = 600
    part, _ = op.replay(price.iloc[:cut], implied.iloc[:cut], forecast.iloc[:cut], cfg)
    settled = cut - op.REPLAY_LIFE - 1  # entries need 21 sessions ahead of them to exist in the short sample
    pd.testing.assert_series_equal(part.iloc[:settled], equity.iloc[:settled])
    # a forecast that never reaches the threshold never trades
    flat, none = op.replay(price, implied, forecast * 0.1, cfg)
    assert none == [] and (flat == 1.0).all()


def test_costs_and_a_steeper_smile_can_only_lower_the_replay():
    price, implied, forecast = _replay_inputs()
    cfg = op.OptionsConfig()
    base = op.replay(price, implied, forecast, cfg, smile="piatto")[0].iloc[-1]
    costly = op.replay(price, implied, forecast, cfg, smile="piatto", cost_multiplier=3.0)[0].iloc[-1]
    assert costly < base
    assert set(op.SMILES) == {"piatto", "ottobre_2026", "put_ripido"}
