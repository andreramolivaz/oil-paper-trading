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


def test_an_empty_state_exports_the_untouched_books_and_no_made_up_price(tmp_path):
    settings = _settings(tmp_path)
    written = ex.export_desk(
        settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir), tmp_path / "out"
    )
    text = (tmp_path / "out" / "desk.json").read_text()
    assert "NaN" not in text and "Infinity" not in text
    doc = json.loads(text)
    # desk.json, then one history file per book for its own page; no backtest on file: its copy is not written
    assert written == [
        "desk.json",
        *(f"books/{b['id']}.json" for b in doc["books"] if b["kind"] == "linear"),
        "books/opzioni.json",
    ]
    assert json.loads((tmp_path / "out" / "books" / "spinto.json").read_text())["fills"] == []
    # the core books first, the options book among them, then the candidate books of phases 12 and 13
    candidates = ["gold", "copper", "agri", "bonds", "equities", "uranium", "utilities", "lithium", "semis", "clean"]
    kalshi = ["oracle", "oracle_equities", "oracle_utilities"]  # the speculative books of phases 14 and 15
    assert [b["id"] for b in doc["books"]] == ["prudente", "dinamico", "spinto", "opzioni", *candidates, *kalshi]
    assert [b["role"] for b in doc["books"]] == ["core"] * 4 + ["candidate"] * (len(candidates) + len(kalshi))
    assert all(b["equity"] == 10_000.0 and b["pnl_total"] == 0.0 for b in doc["books"])
    assert doc["total"] == {"equity": 40_000.0, "initial_capital": 40_000.0, "pnl": 0.0, "pnl_pct": 0.0, "n_books": 4}
    assert doc["total_candidates"]["equity"] == 10_000.0 * (len(candidates) + len(kalshi))
    assert doc["total_candidates"]["n_books"] == len(candidates) + len(kalshi)
    # the Kalshi book (phase 14) says what it is on every row: speculative, no backtest, its own forecast
    by_id = {b["id"]: b for b in doc["books"]}
    assert by_id["oracle"]["forecast_family"] == "kalshi" and by_id["oracle"]["experimental"] is True
    assert by_id["oracle"]["backtested"] is False and by_id["spinto"]["backtested"] is True
    assert by_id["spinto"]["forecast_family"] == "desk" and by_id["spinto"]["experimental"] is False
    # and its index block is there even with an empty archive: no reading, no forecast, the rule written out
    assert [b["id"] for b in doc["oracle"]["books"]] == kalshi and doc["oracle"]["archive_days"] == 0
    assert doc["oracle"]["books"][0]["sleeves"] == {"deescalation": 1}
    assert doc["oracle"]["books"][1]["sleeves"] == {"fedpath": 1, "recession": -1}
    assert [i["id"] for i in doc["oracle"]["indices"]] == ["deescalation", "fedpath", "recession"]
    assert [b["id"] for b in doc["oracle"]["indices"][1]["books"]] == ["oracle_equities", "oracle_utilities"]
    assert doc["oracle"]["indices"][2]["books"] == [{"id": "oracle_equities", "vehicle": "SPY", "sign": -1}]
    assert doc["oracle"]["forecast"] is None and doc["oracle"]["index"] is None
    assert doc["oracle"]["rule"]["horizon_days"] == 90 and "never promoted" in doc["oracle"]["rule"]["text"]
    # a Kalshi archive that cannot be read costs the page its oracle block, never the page (found by review)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            ex.oracle, "archive_readings", lambda store, spec=None: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        broken = ex.build_desk_payload(settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir))
    assert broken["oracle"] == {"error": "RuntimeError: boom", "books": kalshi}
    assert [b["id"] for b in broken["books"]] == [b["id"] for b in doc["books"]]
    # a book with nothing in it loses nothing on any gap
    assert doc["books"][0]["stress"] == {"-30%": 0.0, "-20%": 0.0, "-10%": 0.0, "+10%": 0.0, "+20%": 0.0}
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
    # the gap stress: a short side held in a -2x fund GAINS on a fall of the oil, twice its dollars' worth
    assert b["stress"]["-20%"] == pytest.approx(-0.2 * b["exposure"], abs=1e-4) and b["stress"]["-20%"] > 0
    assert b["stress"]["+10%"] == pytest.approx(0.1 * b["exposure"], abs=1e-4) and b["stress"]["+10%"] < 0
    decision = next(d for d in doc["decisions"] if d["book"] == "dinamico")
    assert decision["order_units"] > 0 and decision["legs"][0]["symbol"] == "SCO"  # the order was on the leg
    assert decision["exposure"] < 0 and "through SCO" in decision["text"]
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


def _two_ticks(tmp_path, monkeypatch):
    """The state after a decision (15:18 New York) and the tick that fills it (the 15:30 bar is in)."""
    settings = _settings(tmp_path)
    data = make_desk_data(n_days=420, start=date(2025, 2, 24))  # SYNTHETIC series ending on a Thursday
    day = data.series["BNO"].bars.index[-1].date()
    data.intraday["BNO"] = half_hour_bars(day, float(data.series["BNO"].bars["close"].iloc[-1]), n=13)
    monkeypatch.setattr(ex, "build_desk_data", lambda raw, cfg: data)
    t0 = datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC)
    live_tick(build_desk(settings.state_dir, RISK), data, t0)
    live_tick(build_desk(settings.state_dir, RISK), data, t0 + timedelta(hours=1))
    return settings, t0 + timedelta(hours=1)


def test_the_desk_page_groups_the_books_and_sums_each_group(tmp_path, monkeypatch):
    settings, now = _two_ticks(tmp_path, monkeypatch)
    doc = ex.build_desk_payload(settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir), now)
    by_id = {b["id"]: b for b in doc["books"]}
    groups = {g["id"]: g for g in doc["portfolios"]}
    # three groups, in the page's order, and every book in exactly one of them: the group its forecast and its
    # configured role give it
    assert [g["id"] for g in doc["portfolios"]] == ["core", "candidates", "kalshi"]
    assert groups["core"]["books"] == ["prudente", "dinamico", "spinto", "opzioni"]
    assert groups["kalshi"]["books"] == ["oracle", "oracle_equities", "oracle_utilities"]
    assert sorted(i for g in doc["portfolios"] for i in g["books"]) == sorted(by_id)
    assert all(by_id[i]["group"] == gid for gid, g in groups.items() for i in g["books"])
    assert all(g["n_books"] == len(g["books"]) and g["label"] and g["note"] for g in doc["portfolios"])
    # what each group holds, and its totals: sums of its own books, nothing else
    core = groups["core"]
    assert core["holding"] == ["prudente", "dinamico"] and core["queued"] == []
    members = [by_id[i] for i in core["books"]]
    assert core["equity"] == pytest.approx(sum(b["equity"] for b in members), abs=0.01)
    assert core["pnl"] == pytest.approx(core["equity"] - core["initial_capital"], abs=0.01)
    assert core["initial_capital"] == 40_000.0 and core["pnl"] != 0.0
    assert core["curve"][-1]["v"] == pytest.approx(core["pnl"], abs=0.02)  # the last day's P&L is today's
    assert groups["candidates"]["holding"] == [] and groups["candidates"]["pnl"] == 0.0
    # a position carries its exposure as a multiple of the book's equity: here the book's whole exposure
    for book_id in core["holding"]:
        b = by_id[book_id]
        (position,) = b["positions"]
        assert position["exposure"] == pytest.approx(position["units"] * position["last_price"] / b["equity"], abs=1e-3)
        assert position["exposure"] == pytest.approx(b["exposure"], abs=1e-3)
    json.dumps(doc, allow_nan=False)


def test_a_group_curve_sums_the_pnl_and_a_book_that_starts_later_joins_at_zero():
    def book(book_id, group, equity, curve, **extra):
        return {"id": book_id, "group": group, "equity": equity, "initial_capital": 10_000.0, "curve": curve, **extra}

    books = [
        book(
            "a",
            "core",
            10_020.0,
            [
                {"t": "2026-10-08T21:00:00Z", "v": 10_000.0},
                {"t": "2026-10-09T15:00:00Z", "v": 10_004.0},
                {"t": "2026-10-09T20:00:00Z", "v": 10_010.0},  # the last of the day is the day's
                {"t": "2026-10-12T20:00:00Z", "v": 10_020.0},
            ],
            pnl_day=10.0,
            positions=[{"symbol": "BNO"}],
            pending=[],
        ),
        book(
            "b",
            "core",
            9_990.0,
            [{"t": "2026-10-09T20:00:00Z", "v": 10_000.0}, {"t": "2026-10-12T20:00:00Z", "v": 9_990.0}],
            pnl_day=-10.0,
            positions=[],
            pending=[{"symbol": "BNO", "units": 3.0}],
        ),
        book("c", "candidates", 10_000.0, []),
        book("d", "claude", 10_000.0, [], structures=[{"id": "s"}]),  # a group the page does not know yet
    ]
    out = ex.portfolios_block(books)
    assert [g["id"] for g in out] == ["core", "candidates", "claude"]  # the known groups first, then the others
    core = out[0]
    # 8 Oct: only "a", flat; 9 Oct: "a" +10 and "b" joins at 0; 12 Oct: +20 and -10
    assert core["curve"] == [
        {"t": "2026-10-08", "v": 0.0},
        {"t": "2026-10-09", "v": 10.0},
        {"t": "2026-10-12", "v": 10.0},
    ]
    assert core["holding"] == ["a"] and core["queued"] == ["b"]
    assert core["equity"] == 20_010.0 and core["pnl"] == 10.0 and core["pnl_pct"] == pytest.approx(0.0005)
    assert core["pnl_day"] == 0.0 and core["pnl_day_pct"] == 0.0
    assert out[1]["curve"] == [] and out[1]["pnl"] == 0.0 and out[1]["holding"] == []
    assert out[2]["label"] == "Claude" and out[2]["holding"] == ["d"]  # an options book holds structures
    assert ex.book_group("core", "desk") == "core" and ex.book_group("candidate", "desk") == "candidates"
    assert ex.book_group("candidate", "kalshi") == "kalshi" and ex.book_group(None, None) == "core"


def test_each_book_has_a_history_file_with_its_whole_current_life(tmp_path, monkeypatch):
    settings, now = _two_ticks(tmp_path, monkeypatch)
    out = tmp_path / "out"
    (out / "books").mkdir(parents=True)
    (out / "books" / "gone.json").write_text("{}\n")  # left by a book that is no longer configured
    written = ex.export_desk(settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir), out, now)
    doc = json.loads((out / "desk.json").read_text())
    assert written[0] == "desk.json"
    assert sorted(written[1:]) == sorted(f"books/{b['id']}.json" for b in doc["books"])
    assert not (out / "books" / "gone.json").exists()
    by_id = {b["id"]: b for b in doc["books"]}
    h = json.loads((out / "books" / "prudente.json").read_text())
    assert (h["id"], h["kind"], h["vehicle"], h["unit"], h["epoch"]) == ("prudente", "linear", "BNO", "shares", 1)
    # every fill of the book's life, with the equity after it; every decision of the book's, newest first
    trades = list(StateStore(settings.state_dir / "desk" / "prudente").iter_jsonl("trades"))
    assert len(h["fills"]) == len(trades) == 1
    assert h["fills"][0]["units"] == trades[0]["qty_bbl"] and h["fills"][0]["equity_after"] is not None
    assert h["decisions"] and all(d["book"] == "prudente" for d in h["decisions"])
    assert [d["ts"] for d in h["decisions"]] == sorted((d["ts"] for d in h["decisions"]), reverse=True)
    assert h["decisions"][-1]["order_units"] == h["fills"][0]["units"] and h["decisions"][-1]["text"]
    assert h["summary"]["n_fills"] == 1 and h["summary"]["n_orders"] >= 1 and h["epochs"] == []
    assert h["curve"][-1]["v"] == by_id["prudente"]["equity"]
    options = json.loads((out / "books" / "opzioni.json").read_text())
    assert (options["kind"], options["unit"], options["fills"]) == ("options", "spread", [])
    # a reset starts a new life: the page shows what the book did since, and the life before it in `epochs`
    later = now + timedelta(hours=1)
    desk = build_desk(settings.state_dir, RISK)
    desk.books["prudente"].broker.reset(later)
    desk.save()
    ex.export_desk(settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir), out, later)
    h = json.loads((out / "books" / "prudente.json").read_text())
    assert h["epoch"] == 2 and h["fills"] == [] and h["decisions"] == [] and h["summary"]["n_fills"] == 0
    (life,) = h["epochs"]
    assert life["epoch"] == 1 and life["end_reason"] == "reset_manual" and life["n_trades"] == 2  # bought, closed
    assert life["start_equity"] == 10_000.0 and life["end_equity"] is not None and life["ended_at"]
    dinamico = json.loads((out / "books" / "dinamico.json").read_text())
    assert dinamico["epoch"] == 1 and len(dinamico["fills"]) == 1  # the other books keep their life


def test_an_open_structure_carries_its_open_pnl_and_a_life_has_one_shape_for_both_books():
    s = {"credit": 0.25, "mark": 0.275, "contracts": 2, "width": 2.0}
    row = ex._structure_row(s)
    assert row["unrealized"] == -5.0 and "unrealized" not in s  # the state is not touched
    assert row["max_loss_now"] == 345.0  # (2.00 - 0.275) x 100 x 2: what the spread can still cost from its mark
    assert ex._structure_row({"credit": 0.25})["unrealized"] is None
    linear = {
        "epoch": 1,
        "started_ts": "2026-10-08T21:00:00Z",
        "ended_ts": "2026-10-20T15:00:00Z",
        "start_equity": 10_000.0,
        "end_equity": 9_500.0,
        "min_equity": 9_400.0,
        "end_reason": "dead",
        "n_trades": 7,
    }
    options = {"epoch": 1, "ended_at": "2026-10-20T15:00:00Z", "final_equity": 9_800.0, "n_closed": 3}
    assert ex._epoch_row(linear) == {
        "epoch": 1,
        "started_at": "2026-10-08T21:00:00Z",
        "ended_at": "2026-10-20T15:00:00Z",
        "start_equity": 10_000.0,
        "end_equity": 9_500.0,
        "min_equity": 9_400.0,
        "end_reason": "dead",
        "n_trades": 7,
    }
    row = ex._epoch_row(options)
    assert row["end_equity"] == 9_800.0 and row["end_reason"] == "reset_manual" and row["n_trades"] == 3
    assert row["started_at"] is None and row["start_equity"] is None


def test_a_failing_history_export_costs_the_book_pages_and_never_desk_json(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("a log cut short")

    monkeypatch.setattr(ex, "export_book_histories", boom)
    written = ex.export_desk(
        settings, RawStore(settings.state_dir), RISK, StateStore(settings.state_dir), tmp_path / "out"
    )
    assert written == ["desk.json"] and json.loads((tmp_path / "out" / "desk.json").read_text())["books"]
