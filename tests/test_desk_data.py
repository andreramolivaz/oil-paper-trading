"""The clean series the desk reads: which contract is held when, what its return is and where it came from.

The calendar facts are real (NYMEX expiries); every price is SYNTHETIC and chosen so that the right answer is
obvious and the wrong one - a return that mixes two contracts - is far away from it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from engine.core.calendar import expiry_for
from engine.data.raw_store import RawStore
from engine.desk import signals as sg
from engine.desk.data import (
    FAR_MIN_DAYS,
    build_desk_data,
    contracts_wide,
    curve_forecasts,
    far_december,
    final_rows,
    front_codes,
    held_codes,
    held_contract,
    hormuz_summary,
    investable_futures_returns,
    known_before,
    roll_day,
    slope_against,
)
from tests.synthetic import make_desk_raw_frames, save_raw_frame


# ----------------------------------------------------------------------------------------------- calendar
def test_the_book_leaves_a_contract_five_business_days_before_its_last_trading_day():
    assert expiry_for("CL", 2020, 5) == date(2020, 4, 21)  # the May 2020 WTI contract: the one that went negative
    assert roll_day("CL", 2020, 5) == date(2020, 4, 14)
    assert held_contract("CL", date(2020, 4, 14)) == "CLK20"
    assert held_contract("CL", date(2020, 4, 15)) == "CLM20"
    # on the day the front settled at -37 $ the book had been in the next contract for three sessions
    assert held_contract("CL", date(2020, 4, 20)) == "CLM20"
    assert held_contract("CL", date(2020, 4, 20), roll_days=0) == "CLK20"


def test_held_and_front_codes_agree_with_the_scalar_function_on_every_day():
    index = pd.bdate_range("2026-08-03", "2026-12-31")
    held = held_codes("CL", index)
    front = front_codes("CL", index)
    for ts in index[::7]:
        assert held[ts] == held_contract("CL", ts.date())
        assert front[ts] == held_contract("CL", ts.date(), roll_days=0)
    # the held contract is never behind the front, and differs from it only in the days before an expiry
    assert (held != front).sum() > 0
    assert (held != front).mean() < 0.35


def test_contracts_wide_pivots_and_drops_missing_bars():
    long = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-10-01", "2026-10-01", "2026-10-02", "2026-10-02", "2026-10-02"]),
            "code": ["CLX26", "CLZ26", "CLX26", "CLZ26", "CLZ26"],
            "close": [90.0, 88.0, 91.0, np.nan, 89.0],
        }
    )
    wide = contracts_wide(long)
    assert list(wide.columns) == ["CLX26", "CLZ26"]
    assert wide.loc["2026-10-02", "CLZ26"] == 89.0  # the null row is dropped, the later duplicate wins
    assert contracts_wide(None).empty and contracts_wide(pd.DataFrame({"date": [], "close": []})).empty


# ----------------------------------------------------------------------------------------------- returns
def _april_2020() -> pd.DatetimeIndex:
    return pd.bdate_range("2020-04-06", "2020-04-24").drop(pd.Timestamp("2020-04-10"))  # Good Friday


def test_the_return_on_a_roll_is_the_held_contract_own_return_never_the_gap_between_two():
    idx = _april_2020()
    may = pd.Series(20.0, index=idx)
    june = pd.Series(30.0 * 1.01 ** np.arange(len(idx)), index=idx)  # ten dollars above May, +1 % a day
    wide = pd.DataFrame({"CLK20": may, "CLM20": june})
    returns, source, held = investable_futures_returns(
        "CL", wide, pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float)
    )
    assert (source == "contract").all()
    assert held[pd.Timestamp("2020-04-14")] == "CLK20" and held[pd.Timestamp("2020-04-15")] == "CLM20"
    assert returns[pd.Timestamp("2020-04-14")] == pytest.approx(0.0)
    # first day in June: June against June. Mixing the two contracts would have printed +50 %.
    assert returns[pd.Timestamp("2020-04-15")] == pytest.approx(0.01)
    assert float(returns.abs().max()) < 0.02


def test_eia_settlements_give_the_exact_return_and_a_negative_front_does_not_void_the_held_contract():
    idx = _april_2020()
    c1 = pd.Series(20.0, index=idx)
    c2 = pd.Series(30.0 * 1.01 ** np.arange(len(idx)), index=idx)
    c1[pd.Timestamp("2020-04-20")] = -37.63  # the real settlement of the expiring contract that day
    # from 2020-04-22 contract 1 IS the June contract and contract 2 is July
    switch = idx >= pd.Timestamp("2020-04-22")
    c1 = c1.where(~switch, c2)
    eia = pd.DataFrame({"RCLC1": c1, "RCLC2": c2.where(~switch, c2 + 2.0)})
    returns, source, held = investable_futures_returns(
        "CL", pd.DataFrame(), eia, pd.Series(dtype=float), pd.Series(dtype=float)
    )
    assert (source == "eia").all()
    assert returns[pd.Timestamp("2020-04-20")] == pytest.approx(0.01)  # June's own return on the -37 $ day
    assert returns[pd.Timestamp("2020-04-21")] == pytest.approx(0.01)
    assert returns[pd.Timestamp("2020-04-22")] == pytest.approx(0.01)  # contract 1 today was contract 2 yesterday
    assert float(returns.min()) > -0.001 and float(returns.max()) < 0.011


def test_the_front_series_is_used_only_while_it_is_the_held_contract_and_the_fund_fills_the_roll_window():
    idx = _april_2020()
    front = pd.Series(20.0 * 1.02 ** np.arange(len(idx)), index=idx)
    fund = pd.Series(5.0 * 1.005 ** np.arange(len(idx)), index=idx)
    returns, source, _ = investable_futures_returns("CL", pd.DataFrame(), pd.DataFrame(), front, fund)
    assert source[pd.Timestamp("2020-04-13")] == "front" and returns[pd.Timestamp("2020-04-13")] == pytest.approx(0.02)
    # from the day after the roll the front is a contract the book no longer holds
    for day in ("2020-04-15", "2020-04-17", "2020-04-20", "2020-04-21", "2020-04-22"):
        assert source[pd.Timestamp(day)] == "fund (approx)", day
        assert returns[pd.Timestamp(day)] == pytest.approx(0.005)
    assert source[pd.Timestamp("2020-04-23")] == "front"


# ----------------------------------------------------------------------------------------------- curve
def test_far_december_is_the_nearest_one_far_enough():
    assert far_december("BZZ26", ["BZZ26", "BZF27", "BZZ27", "BZZ28"]) == "BZZ27"
    assert far_december("CLX26", ["CLZ26", "CLZ27", "CLZ28"]) == "CLZ27"
    assert far_december("CLX26", ["CLZ26"]) is None  # one month away is not a slope worth the name
    assert far_december("CLX26", ["BZZ27"]) is None  # another market's contract is not this curve


def test_slope_is_positive_in_backwardation_and_annualised_by_the_distance_between_expiries():
    idx = pd.bdate_range("2026-09-01", periods=10)
    front = pd.Series(100.0, index=idx)
    codes = front_codes("BZ", idx)
    far = pd.Series(80.0, index=idx)
    slope = slope_against(front, codes, far, "BZZ27")
    years = (pd.Timestamp(expiry_for("BZ", 2027, 12)) - pd.Timestamp(expiry_for("BZ", 2026, 11))).days / 365.25
    assert slope.iloc[0] == pytest.approx((100.0 - 80.0) / 80.0 / years)
    assert (slope_against(front, codes, far * 1.5, "BZZ27") < 0).all()  # contango
    near = slope_against(front, codes, far, "BZZ26")  # closer than FAR_MIN_DAYS: no slope
    assert near.isna().all() and FAR_MIN_DAYS == 300


def test_carry_momentum_is_never_computed_across_two_different_contract_pairs():
    """The designated far contract changes from Z27 to Z28 half way. A moving average straddling the change
    would compare a 20 % slope with a 5 % one; the forecast must come from one pair at a time."""
    idx = pd.bdate_range("2026-06-01", periods=80)
    front = pd.Series(100.0, index=idx)
    z27 = pd.Series(80.0, index=idx).iloc[:40]  # stops being quoted
    z28 = pd.Series(np.linspace(90.0, 96.0, 80), index=idx)  # contango easing day after day: slope falling
    contracts = pd.DataFrame({"BZZ27": z27, "BZZ28": z28})
    out = curve_forecasts(front, contracts, "BZ")
    first, second = out.iloc[:40], out.iloc[40:]
    assert first["slope_pair"].str.endswith("BZZ27").all() and second["slope_pair"].str.endswith("BZZ28").all()
    assert (first["carry"] == 10.0).all() and (second["carry"] == 10.0).all()
    # Z28's slope falls every day, so against ITS OWN 20-day mean it is always below: -10 from the first day
    # it is designated. Averaging across the switch would have compared it with Z27's much higher slope too,
    # but would also have needed 20 days of warm-up after the switch: there is none.
    assert (second["carry_momentum"] == -10.0).all()
    assert not out["slope_approx"].any()


def test_curve_fallback_is_flagged_and_used_only_where_the_own_curve_is_missing():
    idx = pd.bdate_range("2026-01-01", periods=60)
    front = pd.Series(100.0, index=idx)
    contracts = pd.DataFrame({"BZZ27": pd.Series(80.0, index=idx).iloc[30:]})
    proxy = pd.Series(-0.05, index=idx)  # the proxy says contango
    out = curve_forecasts(front, contracts, "BZ", fallback_slope=proxy, fallback_approx=True, fallback_label="proxy")
    assert out["slope_approx"].iloc[:30].all() and not out["slope_approx"].iloc[30:].any()
    assert (out["carry"].iloc[:30] == -10.0).all() and (out["carry"].iloc[30:] == 10.0).all()
    assert (out["slope_pair"].iloc[:30] == "proxy").all()


# ----------------------------------------------------------------------------------------------- the builder
def test_build_desk_data_assembles_both_vehicles_and_labels_what_is_a_proxy(tmp_path):
    store = RawStore(tmp_path)
    for entry, frame in make_desk_raw_frames().items():
        save_raw_frame(store, entry, frame)
    data = build_desk_data(store)
    assert set(data.series) == {"BNO", "MCL"}
    bno, mcl = data.series["BNO"], data.series["MCL"]
    assert (bno.return_source == "fund").all() and (mcl.return_source == "eia").all()
    # no Brent per-contract table was archived: the fund's slope is the WTI curve, and says so
    assert bno.slope_approx.all() and not mcl.slope_approx.any()
    assert list(bno.forecasts.columns) == [*sg.SLEEVES, "combined"] == list(mcl.forecasts.columns)
    # 560 days: every sleeve speaks but the skew, which needs years of its own history; the combination
    # goes on without it
    for series in (bno, mcl):
        last = series.forecasts.iloc[-1]
        assert last.drop("skew").notna().all() and pd.isna(last["skew"])
    assert set(data.macro) == {"copper", "dollar"} and len(data.macro["copper"]) == 560
    assert mcl.bars["symbol"].iloc[-1] == held_contract("CL", mcl.bars.index[-1].date())
    assert (mcl.bars["high"] >= mcl.bars[["open", "close"]].max(axis=1) - 1e-9).all()
    assert "cl_contracts" in data.meta["missing"] and len(data.ovx) > 500


@pytest.mark.parametrize("cut", [350, 480])
def test_build_desk_data_has_no_lookahead(tmp_path, cut):
    """Rebuild from an archive that stops at day ``cut``: every forecast and volatility up to that day is the
    one the full archive gives. (The MCL bar LEVELS are an index anchored on the last close, so they rescale;
    returns, forecasts and volatility must not move.)"""
    history = make_desk_raw_frames()
    full_store, cut_store = RawStore(tmp_path / "full"), RawStore(tmp_path / "cut")
    for entry, frame in history.items():
        save_raw_frame(full_store, entry, frame)
        save_raw_frame(cut_store, entry, frame.iloc[:cut])
    full, part = build_desk_data(full_store), build_desk_data(cut_store)
    for vehicle in ("BNO", "MCL"):
        a, b = part.series[vehicle], full.series[vehicle]
        last = a.bars.index[-1]
        pd.testing.assert_series_equal(a.returns, b.returns.loc[:last], check_names=False)
        pd.testing.assert_series_equal(a.vol, b.vol.loc[:last], check_names=False)
        pd.testing.assert_frame_equal(a.forecasts, b.forecasts.loc[:last])
        pd.testing.assert_series_equal(a.slope, b.slope.loc[:last], check_names=False)


@pytest.mark.parametrize("cut", [950, 1150])
def test_build_desk_data_has_no_lookahead_with_all_seven_sleeves(tmp_path, cut):
    """The same on an archive long enough for the skew: with 1 250 days every sleeve has a value at both cuts."""
    history = make_desk_raw_frames(n=1250)
    full_store, cut_store = RawStore(tmp_path / "full"), RawStore(tmp_path / "cut")
    for entry, frame in history.items():
        save_raw_frame(full_store, entry, frame)
        save_raw_frame(cut_store, entry, frame.iloc[:cut])
    full, part = build_desk_data(full_store), build_desk_data(cut_store)
    for vehicle in ("BNO", "MCL"):
        a, b = part.series[vehicle], full.series[vehicle]
        assert a.forecasts.iloc[-1].notna().all(), vehicle
        pd.testing.assert_frame_equal(a.forecasts, b.forecasts.loc[: a.bars.index[-1]])
    # one skew for both vehicles: it is read on the long WTI history, the fund is too young to have its own
    common = full.series["BNO"].forecasts.index.intersection(full.series["MCL"].forecasts.index)
    pd.testing.assert_series_equal(
        full.series["BNO"].forecasts.loc[common, "skew"], full.series["MCL"].forecasts.loc[common, "skew"]
    )


def test_known_before_reads_the_previous_close_and_drops_what_is_too_old():
    closes = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-07"]).as_unit("ms"))
    days = pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-14", "2026-10-15"])
    out = known_before(closes, days)  # the archive stores milliseconds, a frame in memory nanoseconds
    assert pd.isna(out.iloc[0])  # nothing is dated before the first close
    assert out.iloc[1:5].tolist() == [1.0, 2.0, 3.0, 3.0]  # never the close of the same day; a week old is still read
    assert pd.isna(out.iloc[5])  # eight days old is not a reading of now
    assert known_before(pd.Series(dtype="float64"), days).isna().all()
    # the answer does not depend on the order the days are asked in
    shuffled = known_before(closes, days[::-1])
    assert shuffled.iloc[::-1].tolist()[1:5] == [1.0, 2.0, 3.0, 3.0]


def test_the_row_of_the_day_a_table_is_read_on_is_not_that_day_close(tmp_path):
    """Yahoo's copper future, 5-9 October 2026: the row dated "today" is the live quote of another contract
    month while the session is open, the first minutes of the NEXT session after 18:00 New York, and the
    settlement the history is made of only from the day after. A book reads a row once a later day's download
    has confirmed it - and it is New York's calendar that says which day a download belongs to."""
    days = pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"])
    table = pd.DataFrame({"close": [6.5855, 6.5945, 6.5965, 6.5505]}, index=days)
    evening = pd.Timestamp("2026-10-08 21:18", tz="UTC")  # 17:18 New York on the 8th: the row of the 8th is live
    assert final_rows(table, evening).index[-1] == pd.Timestamp("2026-10-07")
    night = pd.Timestamp("2026-10-09 01:48", tz="UTC")  # 21:48 New York, still the 8th there
    assert final_rows(table, night).index[-1] == pd.Timestamp("2026-10-07")
    next_day = pd.Timestamp("2026-10-09 18:48", tz="UTC")  # the tick of the next day's first decision
    assert final_rows(table, next_day).index[-1] == pd.Timestamp("2026-10-08")
    assert final_rows(table, next_day.tz_localize(None)).index[-1] == pd.Timestamp("2026-10-08")  # naive = UTC
    assert len(final_rows(table, None)) == 4 and final_rows(table.iloc[:0], evening).empty  # a fixture; no rows

    # through the archive: the same copper table, read on the evening of its last row and read the day after
    history = make_desk_raw_frames()
    last = history["copper_daily"].index[-1]
    same_day, day_after = RawStore(tmp_path / "same"), RawStore(tmp_path / "after")
    for entry, frame in history.items():
        save_raw_frame(day_after, entry, frame)
        read = (last + pd.Timedelta(hours=21, minutes=18)).to_pydatetime().replace(tzinfo=UTC)
        save_raw_frame(same_day, entry, frame, fetched_at=read if entry == "copper_daily" else None)
    early, late = build_desk_data(same_day), build_desk_data(day_after)
    assert late.macro["copper"].index[-1] == last and early.macro["copper"].index[-1] < last
    assert early.macro["dollar"].index[-1] == last  # each table has its own download
    # every forecast of the history is the same: a decision never read the close of its own day anyway. What
    # changes is what the NEXT session would read - the close before, not a row nobody has confirmed.
    for vehicle in ("BNO", "MCL"):
        pd.testing.assert_frame_equal(early.series[vehicle].forecasts, late.series[vehicle].forecasts)
    next_session = pd.DatetimeIndex([last + pd.Timedelta(days=1)])
    assert known_before(late.macro["copper"], next_session).iloc[0] == late.macro["copper"].iloc[-1]
    assert known_before(early.macro["copper"], next_session).iloc[0] == late.macro["copper"].iloc[-2]


def test_the_half_hour_tables_reach_the_live_tick_with_the_time_they_were_downloaded(tmp_path):
    """ "A bar is complete fifteen minutes after its end, counted from the download" is a rule of the live tick,
    and the live tick gets its tables from here. The first version of the rule was tested on tables built by
    hand, with the download time on them; this builder dropped that column with the rest of the archive's own,
    so in production the rule never ran and a failed download still turned a forming bar into a finished one
    (found by an independent review, on the real path: archive -> builder -> tick)."""
    from engine.desk.live import completed_bars, last_quote

    store = RawStore(tmp_path)
    for entry, frame in make_desk_raw_frames(end=date(2026, 10, 8)).items():
        save_raw_frame(store, entry, frame)
    bars = pd.DataFrame(
        {"open": [60.0, 60.2], "high": [60.3, 60.4], "low": [59.9, 60.1], "close": [60.2, 60.3], "volume": [1e3, 4e2]},
        index=pd.DatetimeIndex(["2026-10-08 13:30", "2026-10-08 14:00"], tz="UTC", name="ts"),
    )
    read = datetime(2026, 10, 8, 14, 18, tzinfo=UTC)  # the second bar was forming
    long = bars.reset_index().assign(code="CLX26")
    for entry, frame in (("bno_intraday", bars), ("sco_intraday", bars), ("cl_intraday", long)):
        save_raw_frame(store, entry, frame, fetched_at=read)
    data = build_desk_data(store)
    next_tick = datetime(2026, 10, 8, 14, 48, tzinfo=UTC)  # ... and this tick's download failed
    for vehicle, symbol in (("BNO", "BNO"), ("SCO", "SCO"), ("MCL", "CLX26")):
        table = data.intraday[vehicle]
        assert pd.Timestamp(table["observed_at"].max()) == pd.Timestamp(read)
        assert [b.ts.strftime("%H:%M") for b in completed_bars(table, symbol, next_tick, None)] == ["14:00"]
        assert last_quote(table, symbol, next_tick)[1] == datetime(2026, 10, 8, 14, 3, tzinfo=UTC)
    for entry, frame in (("bno_intraday", bars), ("sco_intraday", bars), ("cl_intraday", long)):
        save_raw_frame(store, entry, frame, fetched_at=next_tick)  # the download works again
    data = build_desk_data(store)
    for vehicle, symbol in (("BNO", "BNO"), ("SCO", "SCO"), ("MCL", "CLX26")):
        done = completed_bars(data.intraday[vehicle], symbol, next_tick, None)
        assert [b.ts.strftime("%H:%M") for b in done] == ["14:00", "14:30"]
    # the daily tables carry no such column into the series: nothing downstream of them reads it
    assert "observed_at" not in data.series["BNO"].bars.columns and "observed_at" not in data.legs.get("SCO", bars)


def test_a_macro_close_moves_the_forecast_of_the_next_day_never_of_its_own(tmp_path):
    """Copper and the dollar close after the oil settlement: a book deciding on day D has the close of D-1."""
    history = make_desk_raw_frames()
    shocked = {k: v.copy() for k, v in history.items()}
    day = history["copper_daily"].index[-2]
    shocked["copper_daily"].loc[day:, "close"] *= 1.5  # copper jumps on the second-to-last day and stays there
    base_store, shock_store = RawStore(tmp_path / "base"), RawStore(tmp_path / "shock")
    for entry in history:
        save_raw_frame(base_store, entry, history[entry])
        save_raw_frame(shock_store, entry, shocked[entry])
    base, shock = build_desk_data(base_store), build_desk_data(shock_store)
    for vehicle in ("BNO", "MCL"):
        a, b = base.series[vehicle].forecasts, shock.series[vehicle].forecasts
        pd.testing.assert_frame_equal(a.loc[:day], b.loc[:day])  # the day of the jump itself is unchanged
        # the day after reads it (a jump of that size also inflates copper's volatility: the forecast moves,
        # in which direction is not the point here)
        assert abs(b["copper"].iloc[-1] - a["copper"].iloc[-1]) > 1.0
        assert b["combined"].iloc[-1] != a["combined"].iloc[-1]
        assert b["dollar"].iloc[-1] == a["dollar"].iloc[-1]


def test_without_the_macro_tables_only_the_macro_sleeves_are_silent(tmp_path):
    history = make_desk_raw_frames()
    with_store, without_store = RawStore(tmp_path / "with"), RawStore(tmp_path / "without")
    for entry, frame in history.items():
        save_raw_frame(with_store, entry, frame)
        if entry not in {"copper_daily", "dxy_daily"}:
            save_raw_frame(without_store, entry, frame)
    full, bare = build_desk_data(with_store), build_desk_data(without_store)
    assert bare.macro == {} and {"copper_daily", "dxy_daily"} <= set(bare.meta["missing"])
    for vehicle in ("BNO", "MCL"):
        a, b = full.series[vehicle].forecasts, bare.series[vehicle].forecasts
        assert b[["copper", "dollar"]].isna().all().all()
        pd.testing.assert_frame_equal(
            a[["trend", "accel", "carry", "carry_momentum"]], b[["trend", "accel", "carry", "carry_momentum"]]
        )
        last = b.iloc[-1]
        # the other two sources share the forecast, with the smaller multiplier of four sleeves out of seven
        want = (np.mean([last["trend"], last["accel"]]) + np.mean([last["carry"], last["carry_momentum"]])) / 2.0
        assert last["combined"] == pytest.approx(float(np.clip(want * (1.0 + 0.75 * 3 / 6), -20.0, 20.0)))


# ----------------------------------------------------------------------------------------------- satellites
def test_a_satellite_fund_is_read_on_its_own_price_and_forecast_on_the_price_source_alone(tmp_path):
    """The satellite markets (phase 12): a fund's daily table becomes a vehicle series like BNO's - adjusted
    close as the return, raw bars for the fills - with trend, acceleration and skew on its own history and
    nothing else; the series of the two oil vehicles do not change by its presence."""
    from engine.desk.data import SATELLITE_TABLES, fund_bars, price_only_series

    assert set(SATELLITE_TABLES) == {"GLD", "CPER", "DBA"}
    history = make_desk_raw_frames(n=1100)
    rng = np.random.default_rng(7)
    idx = history["bno_daily"].index
    gold = 150.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, size=len(idx))))
    gld = pd.DataFrame(
        {"open": gold * 0.999, "high": gold * 1.004, "low": gold * 0.995, "close": gold, "adjclose": gold * 0.98},
        index=idx,
    )
    plain, with_gold = RawStore(tmp_path / "plain"), RawStore(tmp_path / "gold")
    for entry, frame in history.items():
        save_raw_frame(plain, entry, frame)
        save_raw_frame(with_gold, entry, frame)
    save_raw_frame(with_gold, "gld_daily", gld)
    base, data = build_desk_data(plain), build_desk_data(with_gold)
    assert set(base.series) == {"BNO", "MCL"} and set(data.series) == {"BNO", "MCL", "GLD"}
    assert {"cper_daily", "dba_daily"} <= set(data.meta["missing"]) and "gld_daily" not in data.meta["missing"]
    for vehicle in ("BNO", "MCL"):  # the oil vehicles are untouched by a satellite's presence
        pd.testing.assert_frame_equal(base.series[vehicle].forecasts, data.series[vehicle].forecasts)
    series = data.series["GLD"]
    bars, returns = fund_bars(gld, "GLD")
    pd.testing.assert_series_equal(series.returns, returns)
    assert (series.returns == gld["adjclose"].pct_change().dropna()).all()  # the adjusted close is the return
    assert (series.bars["close"] == gld["close"].reindex(series.bars.index)).all()  # the raw bars fill orders
    assert (series.bars["symbol"] == "GLD").all() and (series.return_source == "fund").all()
    f = series.forecasts
    assert list(f.columns) == [*sg.SLEEVES, "combined"]
    assert f[["carry", "carry_momentum", "copper", "dollar"]].isna().all().all()  # no curve, no other market
    assert f[["trend", "accel", "skew"]].iloc[-1].notna().all()  # 1100 days: the skew has its history
    pd.testing.assert_series_equal(f["trend"], sg.trend_forecast(series.returns), check_names=False)
    pd.testing.assert_series_equal(f["skew"], sg.skew_forecast(series.returns), check_names=False)
    last = f.iloc[-1]
    want = np.mean([last["trend"], last["accel"], last["skew"]]) * (1.0 + 0.75 * 2 / 6)  # three sleeves of seven
    assert last["combined"] == pytest.approx(float(np.clip(want, -20.0, 20.0)))
    assert series.slope.isna().all() and not series.slope_approx.any()
    # too short a history is no series at all, never a half-built one
    assert price_only_series("GLD", gld.iloc[:200]) is None


# ----------------------------------------------------------------------------------------------- Hormuz
def test_hormuz_summary_measures_a_closure_against_the_years_before_it():
    idx = pd.date_range("2024-01-01", "2026-10-04", freq="D")
    tankers = pd.Series(50.0, index=idx)
    tankers.loc["2026-03-01":] = 1.0
    frame = pd.DataFrame({"portname": "Strait of Hormuz", "n_tanker": tankers})
    out = hormuz_summary(frame)
    assert out["tankers_7d"] == 1.0 and out["baseline"] == 50.0 and out["closed"] is True
    assert out["ratio"] == pytest.approx(0.02) and out["asof"] == "2026-10-04"
    # seven months of closure have not dragged the reference down: it is a median of the whole history
    open_ = hormuz_summary(frame.loc[:"2026-02-28"])
    assert open_["closed"] is False and open_["ratio"] == pytest.approx(1.0)
    assert hormuz_summary(pd.DataFrame()) == {} and hormuz_summary(frame.iloc[:10]) == {}


# -------------------------------------------------------------------------------------------- distributions
def _fund_table_with_distributions(events: dict[int, float], n: int = 400, seed: int = 5) -> pd.DataFrame:
    """A fund's daily table as Yahoo serves it: the raw close is what traded, the adjusted close is the close
    scaled by a factor that drops, for every day BEFORE an ex-date, by ``1 - amount / close_prev``."""
    idx = pd.bdate_range("2024-01-02", periods=n, name="date")
    rng = np.random.default_rng(seed)
    raw = 20.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.008, size=n)))
    factor = np.ones(n)
    for row, amount in sorted(events.items()):
        factor[:row] *= 1.0 - amount / raw[row - 1]
    adj = raw * factor
    return pd.DataFrame(
        {"open": raw * 0.999, "high": raw * 1.004, "low": raw * 0.996, "close": raw, "adjclose": adj.round(6)},
        index=idx,
    )


def test_a_funds_distributions_are_read_off_the_adjusted_close():
    """Yahoo gives no dividend table with the bars: the amount paid per share on an ex-date is the jump in the
    ratio of adjusted to raw close, and nothing else in the table moves it (splits are in both closes)."""
    from engine.desk.data import fund_distributions

    table = _fund_table_with_distributions({150: 0.96, 330: 1.084})
    paid = fund_distributions(table)
    assert list(paid.index) == [table.index[150], table.index[330]]
    assert paid.iloc[0] == pytest.approx(0.96, abs=1e-3) and paid.iloc[1] == pytest.approx(1.084, abs=1e-3)
    assert paid.name == "distribution"
    # the table of a fund that never paid anything, and a table without an adjusted close, have none
    assert fund_distributions(_fund_table_with_distributions({})).empty
    assert fund_distributions(table.drop(columns=["adjclose"])).empty
    # rounding noise in the adjusted close is not a payment: half a cent on a 20 $ fund is below both thresholds
    noisy = table.copy()
    noisy["adjclose"] = (noisy["adjclose"] * (1.0 + 1e-5 * np.sin(np.arange(len(noisy))))).round(6)
    assert len(fund_distributions(noisy)) == 2
    # and the desk data keeps them by symbol, truncated with everything else
    from engine.desk.data import DeskData

    data = DeskData(meta={"missing": [], "notes": []})
    data.distributions["DBA"] = paid
    cut = data.truncate(table.index[200])
    assert list(cut.distributions["DBA"].index) == [table.index[150]]


def test_the_desk_data_notes_the_distributions_of_every_fund_it_reads(tmp_path):
    """BNO, the inverse fund and the satellites: each fund's table is read for its payments, and a fund that
    never paid has no entry (the archived BNO, SCO, GLD and CPER tables show none; DBA pays every December)."""
    from engine.desk.data import fund_distributions

    history = make_desk_raw_frames(n=1100)
    idx = history["bno_daily"].index
    dba = _fund_table_with_distributions({700: 0.5}, n=len(idx)).set_axis(idx)
    store = RawStore(tmp_path / "raw")
    for entry, frame in history.items():
        save_raw_frame(store, entry, frame)
    save_raw_frame(store, "dba_daily", dba)
    data = build_desk_data(store)
    assert "DBA" in data.series and set(data.distributions) == {"DBA"}
    pd.testing.assert_series_equal(data.distributions["DBA"], fund_distributions(dba))
    assert "BNO" not in data.distributions  # the synthetic BNO table pays nothing
