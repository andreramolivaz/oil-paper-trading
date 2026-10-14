"""The allocator (phase 13): the bubble switch, drawdown control, the strait's weekend rule and the lifecycle,
each as a scale a book applies to its own rules; the same code in the replay and on the live path."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.core.config import RiskConfig
from engine.core.store import StateStore
from engine.desk import signals as sg
from engine.desk.allocator import (
    ROLE_CANDIDATE,
    ROLE_CORE,
    ROLE_RETIRED,
    AllocatorConfig,
    LiveRecord,
    daily_closes,
    describe,
    drawdown_scale,
    hormuz_weekend_scale,
    lifecycle_verdict,
    live_record,
    ratio_known_on,
    runup_scales,
    runup_state,
    runup_table,
    transit_ratios,
)
from engine.desk.backtest import run_backtest
from engine.desk.book import Book, BookConfig
from engine.desk.engine import Desk
from engine.desk.live import build_desk, live_tick, review_lifecycle
from tests.synthetic import make_desk_data

RISK = RiskConfig.load()
CFG = AllocatorConfig()  # the defaults are the file's values (pinned below)
PRUDENTE = BookConfig(id="prudente", name="Prudent", vehicle="BNO", vol_target=0.12, max_leverage=1.0, long_only=True)
DINAMICO = BookConfig(
    id="dinamico", name="Dynamic", vehicle="BNO", vol_target=0.25, max_leverage=2.0,
    weekend_max_leverage=1.5, long_only=True,
)  # fmt: skip


# ------------------------------------------------------------------------------------------------- the config
def test_the_configuration_on_file_is_the_registered_one_and_rejects_nonsense():
    cfg = AllocatorConfig.load()
    assert cfg == AllocatorConfig()  # the dataclass defaults ARE the file: a change in one must change the other
    assert cfg.runup_enabled and (cfg.runup_lookback_days, cfg.runup_threshold) == (504, 1.0)
    assert (cfg.runup_horizon_days, cfg.runup_scale) == (252, 0.5)
    assert not cfg.drawdown_enabled and (cfg.drawdown_max, cfg.drawdown_floor) == (0.20, 0.25)
    assert cfg.hormuz_enabled and (cfg.hormuz_closed_share, cfg.hormuz_weekend_scale, cfg.hormuz_lag_days) == (
        0.10,
        0.5,
        5,
    )
    assert cfg.lifecycle_enabled and (cfg.promote_after_months, cfg.retire_after_months) == (12, 24)
    assert (cfg.promote_min_sharpe, cfg.retire_sharpe) == (0.0, -0.5)
    for bad in (
        {"runup_scale": 1.5},
        {"drawdown_floor": -0.1},
        {"hormuz_closed_share": 1.0},
        {"runup_lookback_days": 0},
    ):
        with pytest.raises(ValueError):
            dataclasses.replace(CFG, **bad)
    rules = describe(cfg)
    assert set(rules) == {"runup", "drawdown_control", "hormuz", "lifecycle"} and "+100%" in rules["runup"]["rule"]


# ------------------------------------------------------------------------------------------------- the bubble switch
def _index_with_a_doubling(n: int = 2000, double_at: int = 900) -> pd.Series:
    """A flat index that doubles once (over 100 days ending at ``double_at``) and stays there."""
    idx = pd.bdate_range("2019-01-02", periods=n)
    level = np.ones(n)
    ramp = np.linspace(1.0, 2.0, 101)
    level[double_at - 100 : double_at + 1] = ramp
    level[double_at + 1 :] = 2.0
    return pd.Series(level, index=idx)


def test_the_bubble_switch_halves_the_risk_for_a_year_after_a_doubling_and_never_looks_ahead():
    index = _index_with_a_doubling()
    scales = runup_scales(index, CFG)
    # the run-up over 504 days reaches +100 % at double_at and holds for 504 days; the scale is 0.5 from the
    # first day it holds until 252 days after the last day it holds
    first = int(np.flatnonzero((scales < 1.0).to_numpy())[0])
    last = int(np.flatnonzero((scales < 1.0).to_numpy())[-1])
    assert first == 900
    held_last = 800 + 504  # the last day whose base, 504 days earlier, is still the pre-ramp level 1.0
    assert last == held_last + 252 - 1
    assert (scales.iloc[:900] == 1.0).all() and (scales.iloc[first : last + 1] == 0.5).all()
    assert (scales.iloc[last + 1 :] == 1.0).all()
    # the table says the same, with the day the run-up last held
    table = runup_table(index, CFG)
    assert bool(table["active"].iloc[first]) and not bool(table["active"].iloc[first - 1])
    assert pd.Timestamp(table["since"].iloc[last]) == index.index[held_last]
    # day by day, the state the live path asks for agrees with the table
    for i in (899, 900, 1200, held_last, held_last + 100, last, last + 1, 1999):
        active, since = runup_state(index, CFG, index.index[i].date())
        assert active == bool(table["active"].iloc[i]), i
        assert since == (None if pd.isna(table["since"].iloc[i]) else pd.Timestamp(table["since"].iloc[i]).date())
    # no look-ahead: cutting the index at any T leaves every earlier value as it was
    for cut in (950, 1300):
        pd.testing.assert_series_equal(runup_scales(index.iloc[:cut], CFG), scales.iloc[:cut])
    # switched off: nothing
    off = dataclasses.replace(CFG, runup_enabled=False)
    assert (runup_scales(index, off) == 1.0).all() and runup_state(index, off, index.index[1000].date()) == (
        False,
        None,
    )


# ------------------------------------------------------------------------------------------------- drawdown control
def test_drawdown_control_is_off_by_default_and_follows_grossman_zhou_when_on():
    assert drawdown_scale(8_000.0, 10_000.0, CFG) == 1.0  # off
    on = dataclasses.replace(CFG, drawdown_enabled=True)
    assert drawdown_scale(10_000.0, 10_000.0, on) == 1.0
    assert drawdown_scale(9_000.0, 10_000.0, on) == pytest.approx(0.5)  # 10 % of a 20 % budget
    assert drawdown_scale(7_000.0, 10_000.0, on) == 0.25  # the floor
    assert drawdown_scale(11_000.0, 10_000.0, on) == 1.0  # a new high
    assert drawdown_scale(0.0, 10_000.0, on) == 1.0  # nothing to scale


# ------------------------------------------------------------------------------------------------- the strait
def _hormuz(collapse_on: date, days: int = 900) -> pd.DataFrame:
    start = collapse_on - timedelta(days=days - 120)
    idx = pd.date_range(start, periods=days, freq="D")
    n = np.where(idx < pd.Timestamp(collapse_on), 85.0, 4.0)
    return pd.DataFrame({"n_tanker": n, "portname": "Strait of Hormuz"}, index=idx)


def test_a_closed_strait_halves_the_weekend_ceiling_from_the_day_the_reading_can_be_acted_on():
    collapse = date(2026, 3, 1)
    ratios = transit_ratios(_hormuz(collapse), CFG)
    assert not ratios.empty
    # before the collapse the ratio is about one; a week into it the 7-day mean is 4/85
    assert ratio_known_on(ratios, collapse - timedelta(days=10)) == pytest.approx(1.0, abs=0.05)
    late = collapse + timedelta(days=7 + CFG.hormuz_lag_days)
    assert ratio_known_on(ratios, late) == pytest.approx(4.0 / 85.0, abs=0.01)
    # the lag: the reading of the collapse's seventh day cannot be acted on before `lag` days later
    assert ratio_known_on(ratios, collapse + timedelta(days=7)) == pytest.approx(
        ratios.loc[: pd.Timestamp(collapse + timedelta(days=7))].iloc[-1]
    )
    assert hormuz_weekend_scale(ratio_known_on(ratios, late), CFG) == 0.5
    assert hormuz_weekend_scale(0.5, CFG) == 1.0 and hormuz_weekend_scale(None, CFG) == 1.0
    assert hormuz_weekend_scale(0.05, dataclasses.replace(CFG, hormuz_enabled=False)) == 1.0
    # a stale reading (older than 45 days) is no reading; a short history gives nothing
    assert ratio_known_on(ratios, ratios.index[-1].date() + timedelta(days=46)) is None
    assert (
        transit_ratios(_hormuz(collapse, days=100), CFG).empty
        and ratio_known_on(pd.Series(dtype="float64"), collapse) is None
    )


# ------------------------------------------------------------------------------------------------- the book
def test_a_book_applies_the_scales_to_its_target_and_its_weekend_ceiling_and_says_so():
    book = Book(DINAMICO, RISK)
    friday = date(2026, 10, 9)  # a Friday: pre-closure
    raw, capped, _ = book.exposure(10.0, 0.25, date(2026, 10, 7))
    assert capped == pytest.approx(1.0)  # 10/10 x 0.25/0.25
    book.set_scale(risk=0.5, reasons=["price doubled in two years"])
    assert book.vol_target == pytest.approx(0.125)
    raw2, capped2, _ = book.exposure(10.0, 0.25, date(2026, 10, 7))
    assert capped2 == pytest.approx(0.5) and raw2 == pytest.approx(raw / 2)
    # the weekend scale only bites on a pre-closure day, on the weekend ceiling
    book.set_scale(risk=1.0, weekend=0.5)
    assert book._cap(date(2026, 10, 7)) == 2.0 and book._cap(friday) == pytest.approx(0.75)
    # retired: no exposure at all, and the reason is the allocator's
    book.set_scale(risk=0.0, reasons=["retired by the lifecycle rule: no new risk"])
    assert book.exposure(10.0, 0.25, date(2026, 10, 7)) == (0.0, 0.0, "retired by the allocator")
    # the scales are clipped: the allocator never adds risk
    book.set_scale(risk=1.7, weekend=2.0)
    assert book.risk_scale == 1.0 and book.weekend_scale == 1.0
    # the decision names the scale and carries it
    book = Book(PRUDENTE, RISK)
    book.broker.note_price("BNO", 60.0)
    book.set_scale(risk=0.5, reasons=["price doubled in two years (last on 2026-09-01): risk x 0.5 for a year"])
    forecast = dict.fromkeys(sg.SLEEVES, 10.0) | {"combined": 10.0}
    decision, _ = book.orders(
        datetime(2026, 10, 7, 19, 18, tzinfo=UTC), date(2026, 10, 7), "BNO", 60.0, forecast, 0.24, None
    )
    assert decision.risk_scale == 0.5 and decision.scale_reasons and "times 0.5" in decision.rationale
    # the seven sleeves at +10 combine to +17.5 (the multiplier): 17.5 / 10 x 6 % / 24 % = 0.4375
    assert "target of 6%" in decision.rationale and decision.exposure == pytest.approx(0.4375)


# ------------------------------------------------------------------------------------------------- the desk
def test_the_desk_sets_every_scale_of_a_vehicle_and_records_them():
    desk = Desk([Book(PRUDENTE, RISK), Book(DINAMICO, RISK)], allocator=CFG)
    day = date(2026, 10, 9)
    scales = desk.allocate("BNO", day, runup=(True, date(2026, 9, 1)), hormuz_ratio=0.05)
    assert set(scales) == {"prudente", "dinamico"}
    for book_id, scale in scales.items():
        assert scale.risk == 0.5 and scale.weekend == 0.5 and len(scale.reasons) == 2
        assert desk.books[book_id].risk_scale == 0.5 and desk.books[book_id].weekend_scale == 0.5
        assert desk.state.allocator[book_id]["day"] == day.isoformat() and desk.state.allocator[book_id]["risk"] == 0.5
    # a retired role from the lifecycle: risk zero
    desk.state.roles["dinamico"] = {"role": ROLE_RETIRED, "since": "2026-10-01", "reason": "test"}
    scales = desk.allocate("BNO", day)
    assert scales["dinamico"].risk == 0.0 and "retired" in scales["dinamico"].reasons[-1]
    assert scales["prudente"].risk == 1.0 and scales["prudente"].reasons == []
    assert desk.role_of(desk.books["dinamico"]) == ROLE_RETIRED and desk.role_of(desk.books["prudente"]) == "core"
    # without an allocator the books keep their own rules whatever is passed
    plain = Desk([Book(PRUDENTE, RISK)])
    scale = plain.allocate("BNO", day, runup=(True, date(2026, 9, 1)), hormuz_ratio=0.0)["prudente"]
    assert scale.risk == 1.0 and scale.weekend == 1.0 and scale.reasons == []
    # the state survives a round trip
    from engine.desk.engine import DeskState

    again = DeskState.from_dict(desk.state.to_dict())
    assert again.roles == desk.state.roles and again.allocator == desk.state.allocator


# ------------------------------------------------------------------------------------------------- the replay
def test_the_replay_runs_the_switch_and_reports_with_and_without_the_allocator():
    data = make_desk_data(n_days=1400)
    series = data.series["BNO"]
    # make the fund double over the middle of the sample: the switch must be on for a year after
    rets = series.returns.copy()
    rets.iloc[700:800] = 0.007  # +100 % in 100 days
    bars = series.bars.copy()
    index = sg.total_return_index(rets)
    for col in ("open", "high", "low", "close"):
        bars[col] = bars[col].iloc[0] * index / index.iloc[0]
    data.series["BNO"] = dataclasses.replace(series, returns=rets, bars=bars, vol=sg.ew_vol(rets))
    with_ = run_backtest(data, [PRUDENTE], RISK, ruin=False, allocator=CFG)
    alone = run_backtest(data, [PRUDENTE], RISK, ruin=False)
    table = runup_table(index, CFG)
    on = table.index[table["active"]]
    assert len(on) > 300
    lev_with, lev_alone = with_.books["prudente"].leverage, alone.books["prudente"].leverage
    common = lev_with.index.intersection(lev_alone.index)
    before = common[common < on[0]]
    pd.testing.assert_series_equal(lev_with.loc[before], lev_alone.loc[before])  # identical until the switch
    during = common[(common >= on[0]) & (common <= on[-1])]
    invested = during[lev_alone.loc[during] > 0.2]
    assert len(invested) > 50
    ratio = (lev_with.loc[invested] / lev_alone.loc[invested]).median()
    assert 0.4 < ratio < 0.7  # half the target, within the inertia band's rounding
    # the payload carries both, and says which rules were in force
    from engine.desk.report import desk_backtest_payload

    payload = desk_backtest_payload(data, [PRUDENTE], RISK, allocator=CFG)
    assert payload["allocator"]["runup"]["enabled"] and payload["allocator"]["config"]["runup_scale"] == 0.5
    assert payload["without_allocator"]["books"]["prudente"]["stats"] == alone.books["prudente"].stats
    assert payload["books"]["prudente"]["stats"] == with_.books["prudente"].stats
    assert desk_backtest_payload(data, [PRUDENTE], RISK)["allocator"] is None


# ------------------------------------------------------------------------------------------------- the lifecycle
def _rows(start: date, days: int, drift: float, vol: float, seed: int = 1) -> list[dict]:
    rng = np.random.default_rng(seed)
    eq = 10_000.0 * np.cumprod(1.0 + rng.normal(drift, vol, size=days))
    rows = []
    for i, e in enumerate(eq):
        day = start + timedelta(days=int(i * 7 / 5))  # roughly business days
        for hour in (14, 20):  # two snapshots a day: the last one is the close
            rows.append(
                {
                    "ts": f"{day.isoformat()}T{hour:02d}:18:00Z",
                    "equity": float(e) * (1.0 if hour == 20 else 0.999),
                    "epoch": 1,
                }
            )
    return rows


def test_the_lifecycle_promotes_after_twelve_good_months_and_retires_after_twenty_four_bad_ones():
    good = live_record(daily_closes(_rows(date(2025, 10, 8), 270, 0.0008, 0.006)))
    bad = live_record(daily_closes(_rows(date(2024, 10, 8), 520, -0.001, 0.006)))
    assert good.sharpe is not None and good.sharpe > 0 and 12 <= good.months < 13
    assert bad.sharpe is not None and bad.sharpe < -0.5 and bad.months >= 24
    assert lifecycle_verdict(ROLE_CANDIDATE, good, -0.25, CFG) == ROLE_CORE
    assert lifecycle_verdict(ROLE_CANDIDATE, good, -0.01, CFG) == ROLE_CANDIDATE  # drawdown deeper than the backtest's
    assert lifecycle_verdict(ROLE_CANDIDATE, bad, None, CFG) == ROLE_RETIRED
    assert lifecycle_verdict(ROLE_CORE, bad, None, CFG) == ROLE_RETIRED
    assert lifecycle_verdict(ROLE_RETIRED, good, None, CFG) == ROLE_RETIRED  # only the owner reopens a book
    young = LiveRecord(days=40, sharpe=None, max_drawdown=-0.01, start=None, end=None)
    assert lifecycle_verdict(ROLE_CANDIDATE, young, None, CFG) == ROLE_CANDIDATE
    assert (
        lifecycle_verdict(ROLE_CANDIDATE, good, None, dataclasses.replace(CFG, lifecycle_enabled=False))
        == ROLE_CANDIDATE
    )
    # one close per day, the last snapshot of the day, this epoch only
    rows = _rows(date(2026, 1, 5), 5, 0.0, 0.0) + [{"ts": "2026-01-04T20:18:00Z", "equity": 1.0, "epoch": 0}]
    closes = daily_closes(rows, epoch=1)
    assert len(closes) == 5 and (closes == 10_000.0).all()
    assert daily_closes([]).empty


def test_the_review_runs_once_a_day_on_each_books_own_log_and_changes_the_role(tmp_path):
    cand = dataclasses.replace(PRUDENTE, id="gold", name="Gold", vehicle="GLD", role="candidate")
    desk = build_desk(tmp_path, RISK, [PRUDENTE, cand], allocator=CFG)
    store = StateStore(tmp_path / "desk" / "gold")
    for row in _rows(date(2025, 10, 8), 270, 0.0008, 0.006):
        store.append_jsonl("equity", row)
    notes = review_lifecycle(desk, date(2026, 10, 13), {"gold": -0.25})
    assert notes and notes[0].startswith("gold: candidate -> core")
    assert desk.state.roles["gold"]["role"] == ROLE_CORE and desk.role_of(desk.books["gold"]) == ROLE_CORE
    assert desk.state.allocator["_lifecycle"]["gold"]["role"] == ROLE_CORE
    assert desk.state.allocator["_lifecycle"]["prudente"]["months_live"] == 0.0
    assert review_lifecycle(desk, date(2026, 10, 13), {"gold": -0.25}) == []  # once a day


# ------------------------------------------------------------------------------------------------- the live path
def test_on_the_live_path_the_decision_carries_the_scale_and_the_note(tmp_path):
    from tests.synthetic import half_hour_bars

    data = make_desk_data(n_days=1400, start=date(2021, 2, 22))
    series = data.series["BNO"]
    # the vehicle's own index doubled within the last two years: the switch is on today
    rets = series.returns.copy()
    rets.iloc[-300:-200] = 0.007
    series = dataclasses.replace(series, returns=rets)
    day = series.bars.index[-1].date()
    series.forecasts.loc[series.forecasts.index[-1], list(sg.SLEEVES)] = 12.0
    data.series["BNO"] = series
    data.legs = {}
    data.intraday["BNO"] = half_hour_bars(day, float(series.bars["close"].iloc[-1]), n=13, first=(13, 30))
    desk = build_desk(tmp_path / "live", RISK, [PRUDENTE], allocator=CFG)
    assert runup_state(sg.total_return_index(rets), CFG, day)[0]
    report = live_tick(desk, data, datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC))
    assert report.decisions and all(d["risk_scale"] == 0.5 for d in report.decisions)
    assert any("price doubled in two years" in n for n in report.notes)
    assert (
        desk.state.allocator["prudente"]["risk"] == 0.5 and desk.state.allocator["prudente"]["day"] == day.isoformat()
    )
    # the same desk without an allocator decides at the book's own target
    plain = build_desk(tmp_path / "plain", RISK, [PRUDENTE])
    report2 = live_tick(plain, data, datetime(day.year, day.month, day.day, 19, 18, tzinfo=UTC))
    assert report2.decisions[0]["risk_scale"] == 1.0
    assert report2.decisions[0]["exposure"] == pytest.approx(2 * report.decisions[0]["exposure"], rel=1e-6)
