"""``desk.json``: what the terminal reads. Strict JSON, nothing invented, the same numbers the books hold."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from engine.core.config import RiskConfig, Settings
from engine.core.store import StateStore
from engine.data.raw_store import RawStore
from engine.desk import export as ex
from engine.desk.book import load_books
from engine.desk.live import build_desk, live_tick
from tests.synthetic import half_hour_bars, make_desk_data

RISK = RiskConfig.load()


def _settings(tmp_path) -> Settings:
    base = Settings.from_env()
    import dataclasses

    return dataclasses.replace(base, state_dir=tmp_path / "state")


def test_an_empty_state_exports_four_untouched_books_and_no_made_up_price(tmp_path):
    settings = _settings(tmp_path)
    written = ex.export_desk(
        settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir), tmp_path / "out"
    )
    assert written == ["desk.json"]  # no backtest on file: its copy is not written
    text = (tmp_path / "out" / "desk.json").read_text()
    assert "NaN" not in text and "Infinity" not in text
    doc = json.loads(text)
    assert [b["id"] for b in doc["books"]] == ["prudente", "dinamico", "spinto", "opzioni"]
    assert all(b["equity"] == 10_000.0 and b["pnl_total"] == 0.0 for b in doc["books"])
    assert doc["total"] == {"equity": 40_000.0, "initial_capital": 40_000.0, "pnl": 0.0, "pnl_pct": 0.0}
    for key in ("brent", "wti"):
        assert doc["market"][key]["price"] is None and doc["market"][key]["change"] is None
    assert doc["forecast"] == {} and doc["decisions"] == [] and doc["fills"] == [] and doc["backtest"] is None
    assert doc["market"]["hormuz"] is None and doc["health"]["overall"] is None


def test_the_export_shows_what_the_books_hold_after_a_tick(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    data = make_desk_data(n_days=420, start=date(2025, 2, 24))  # SYNTHETIC series ending on a Thursday
    day = data.series["BNO"].bars.index[-1].date()
    data.intraday["BNO"] = half_hour_bars(day, float(data.series["BNO"].bars["close"].iloc[-1]), n=13)
    monkeypatch.setattr(ex, "build_desk_data", lambda raw, cfg: data)
    desk = build_desk(settings.state_dir, RISK)
    t0 = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)  # 15:18 New York
    live_tick(desk, data, t0)
    live_tick(build_desk(settings.state_dir, RISK), data, t0 + timedelta(hours=1))  # the 15:30 bar is in
    doc = ex.build_desk_payload(
        settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir), t0 + timedelta(hours=1)
    )
    by_id = {b["id"]: b for b in doc["books"]}
    configs = {b.id: b for b in load_books()}
    for book_id in ("prudente", "dinamico"):
        b = by_id[book_id]
        assert b["rules"]["vol_target"] == configs[book_id].vol_target and b["kind"] == "linear"
        assert b["last_decision"] is not None and b["decision_owed"] is False
        assert b["leverage"] <= b["cap_today"] <= b["rules"]["max_leverage"]
        position = b["positions"][0]
        assert position["symbol"] == "BNO" and position["side"] == "long" and position["units"] > 0
        assert b["equity"] == pytest.approx(10_000 + position["unrealized"] - b["costs"]["commission"], abs=0.01)
        assert len(b["curve"]) >= 2 and b["curve"][-1]["v"] == b["equity"]
    assert by_id["opzioni"]["kind"] == "options" and by_id["opzioni"]["structures"] == []
    assert doc["forecast"]["BNO"]["day"] == day.isoformat() and doc["forecast"]["BNO"]["combined"] is not None
    assert {d["book"] for d in doc["decisions"]} >= {"prudente", "dinamico"}
    assert len(doc["fills"]) == 2
    assert all(f["price"] > f["reference_price"] for f in doc["fills"] if f["units"] > 0)  # a buy pays the spread
    # a fill is shown at the time its bar OPENED (15:30), which is when it was executed, after the decision
    opened = datetime(day.year, day.month, day.day, 19, 30, tzinfo=UTC)
    assert all(pd.Timestamp(f["ts"]) == pd.Timestamp(opened) for f in doc["fills"])
    assert all(pd.Timestamp(f["decided_at"]) == pd.Timestamp(t0) for f in doc["fills"])
    assert doc["market"]["bno"]["price"] is not None and doc["market"]["bno"]["symbol"] == "BNO"
    json.dumps(doc, allow_nan=False)


def test_the_export_shows_a_short_side_held_in_the_inverse_fund_as_a_short(tmp_path, monkeypatch):
    from engine.desk import signals as sg

    settings = _settings(tmp_path)
    data = make_desk_data(n_days=420, start=date(2025, 2, 24))
    series = data.series["BNO"]
    day = series.bars.index[-1].date()
    series.forecasts.loc[series.forecasts.index[-1], list(sg.SLEEVES)] = -12.0  # every sleeve short today
    series.forecasts.loc[series.forecasts.index[-1], "combined"] = -20.0
    data.intraday["BNO"] = half_hour_bars(day, float(series.bars["close"].iloc[-1]), n=13)
    data.intraday["SCO"] = half_hour_bars(day, float(data.legs["SCO"]["close"].iloc[-1]), n=13, step=-0.002)
    # copper and the dollar: closes up to the day itself, of which a forecast may only read the day before
    days = series.bars.index[-5:]
    data.macro = {"copper": pd.Series([4.0, 4.1, 4.2, 4.3, 4.4], index=days), "dollar": pd.Series(101.0, index=days)}
    monkeypatch.setattr(ex, "build_desk_data", lambda raw, cfg: data)
    t0 = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)
    live_tick(build_desk(settings.state_dir, RISK), data, t0)
    live_tick(build_desk(settings.state_dir, RISK), data, t0 + timedelta(hours=1))
    doc = ex.build_desk_payload(
        settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir), t0 + timedelta(hours=1)
    )
    by_id = {b["id"]: b for b in doc["books"]}
    # the long-only book sits in cash; the one with a short leg holds shares of the inverse fund
    assert by_id["prudente"]["positions"] == [] and by_id["prudente"]["rules"]["long_only"] is True
    assert by_id["prudente"]["rules"]["short_via"] is None
    b = by_id["dinamico"]
    assert b["rules"]["long_only"] is False and b["rules"]["short_via"] == "SCO" and "-2" in b["rules"]["short_note"]
    (position,) = b["positions"]
    assert position["symbol"] == "SCO" and position["units"] > 0 and position["multiplier"] == -2.0
    assert position["side"] == "short"  # shares of an inverse fund are a short position in oil
    # exposure and leverage are in oil terms: twice the dollars held in the fund
    assert b["exposure"] == pytest.approx(-2.0 * position["notional"] / b["equity"], abs=1e-3)
    assert b["leverage"] == pytest.approx(-b["exposure"]) and 1.0 < b["leverage"] <= b["cap_today"]
    assert position["notional"] <= b["equity"]  # paid in cash
    decision = next(d for d in doc["decisions"] if d["book"] == "dinamico")
    assert decision["order_units"] > 0 and decision["legs"][0]["symbol"] == "SCO"  # the order was on the leg
    assert decision["exposure"] < 0 and "tramite SCO" in decision["text"]
    assert [(f["book"], f["symbol"], f["units"] > 0) for f in doc["fills"]] == [("dinamico", "SCO", True)]
    # the forecast block: the seven sleeves, the three sources, and the close the macro sleeves were read at
    fc = doc["forecast"]["BNO"]
    assert all(fc[name] == -12.0 for name in sg.SLEEVES)
    assert fc["sources"] == {"prezzo": -12.0, "curva": -12.0, "macro": -12.0} and fc["combined"] == -20.0
    assert fc["macro_day"] == {"copper": days[-2].date().isoformat(), "dollar": days[-2].date().isoformat()}
    assert doc["market"]["copper"]["value"] == 4.4 and doc["market"]["copper"]["asof"] == day.isoformat()
    assert "HG=F" in doc["market"]["copper"]["source"] and doc["market"]["dollar"]["value"] == 101.0
    json.dumps(doc, allow_nan=False)


def test_the_equity_curve_keeps_recent_detail_and_one_point_a_day_before(tmp_path):
    now = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)
    rows = [
        {"ts": (now - timedelta(minutes=30 * i)).isoformat(), "equity": 10_000.0 + i, "epoch": 1} for i in range(2000)
    ]
    curve = ex._curve(rows)
    assert 2 <= len(curve) <= ex.MAX_CURVE_POINTS + 1
    assert curve[-1]["v"] == 10_000.0 and curve[0]["t"] < curve[-1]["t"]
    assert ex._curve([]) == [] and ex._current_epoch([{"epoch": 1}, {"epoch": 2}], 2) == [{"epoch": 2}]
