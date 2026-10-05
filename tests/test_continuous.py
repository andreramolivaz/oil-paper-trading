"""engine.data.continuous: roll dates from the ICE calendar, back-adjusted continuous series, curve table."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from engine.core.calendar import add_business_days, ice_brent_expiry
from engine.data.base import FetchResult
from engine.data.continuous import (
    CONT_COLUMN,
    SOURCE_COLUMN,
    build_curve_table,
    build_roll_adjusted,
    curve_spreads_from_table,
    roll_dates_from_calendar,
)
from engine.data.market_data import CURVE_COLUMNS


def bars(closes: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.date_range(start, periods=len(closes), freq="B"), name="date")
    close = pd.Series(closes, index=idx, dtype="float64")
    return pd.DataFrame(
        {"open": close - 0.1, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 1000.0}
    )


# -------------------------------------------------------------------------------------------------- roll dates
def test_roll_dates_follow_the_ice_expiry_calendar():
    rolls = roll_dates_from_calendar("BZ", date(2026, 1, 1), date(2026, 12, 31), roll_buffer_days=2)
    # one roll per delivery month
    assert len(rolls) == 12
    assert rolls == sorted(set(rolls))
    # the Dec-26 contract expires on the last business day of October 2026
    expiry = ice_brent_expiry(2026, 12)
    assert expiry == date(2026, 10, 30)
    assert add_business_days(expiry, -2) in rolls
    # all rolls are business days inside the window
    assert all(date(2026, 1, 1) <= r <= date(2026, 12, 31) for r in rolls)


def test_roll_buffer_zero_rolls_on_the_expiry():
    rolls = roll_dates_from_calendar("BZ", date(2026, 10, 1), date(2026, 11, 30), roll_buffer_days=0)
    assert ice_brent_expiry(2026, 12) in rolls
    assert ice_brent_expiry(2027, 1) in rolls


def test_roll_dates_validates_the_window():
    with pytest.raises(ValueError, match="is after end"):
        roll_dates_from_calendar("BZ", date(2026, 10, 5), date(2026, 1, 1))


# ------------------------------------------------------------------------------------- back-adjusted continuous
def test_roll_jump_never_appears_as_a_return_in_cont():
    """The core invariant: the front series jumps at the roll, the continuous series must not."""
    #            ... quiet ...  roll day (front switches to a 3 $ cheaper contract)  ... quiet ...
    closes = [100.0, 100.5, 101.0, 98.2, 98.4, 98.9]
    frame = bars(closes)
    roll_day = frame.index[3].date()
    # the real curve spread of that roll: the new contract is 3 $ below the old one
    spreads = pd.Series([-3.0], index=pd.DatetimeIndex([pd.Timestamp(roll_day)]))
    out = build_roll_adjusted(frame, [roll_day], spreads, None)

    front_move = frame["close"].diff().iloc[3]
    cont_move = out[CONT_COLUMN].diff().iloc[3]
    assert front_move == pytest.approx(-2.8)  # 98.2 - 101.0: mostly the roll
    assert cont_move == pytest.approx(0.2)  # the real move of the new contract
    assert cont_move == pytest.approx(front_move - (-3.0))
    # anchored at the end: today's continuous level IS the front close
    assert out[CONT_COLUMN].iloc[-1] == pytest.approx(closes[-1])
    # history is shifted by the cumulative adjustment, every other return is untouched
    assert out[CONT_COLUMN].iloc[0] == pytest.approx(100.0 - 3.0)
    diffs_front = frame["close"].diff().drop(frame.index[3])
    diffs_cont = out[CONT_COLUMN].diff().drop(frame.index[3])
    pd.testing.assert_series_equal(diffs_front, diffs_cont, check_names=False)
    # OHLC are shifted by the same adjustment
    assert out["cont_open"].iloc[0] == pytest.approx(99.9 - 3.0)
    assert out[SOURCE_COLUMN].iloc[3] == "curve"
    assert set(out[SOURCE_COLUMN].drop(frame.index[3])) == {"none"}
    assert out.attrs["approx"] is False
    assert out.attrs["roll_sources"]["curve"] == 1


def test_two_rolls_accumulate_additively():
    closes = [100.0, 97.0, 97.5, 95.5, 96.0]
    frame = bars(closes)
    d1, d2 = frame.index[1].date(), frame.index[3].date()
    spreads = pd.Series([-3.0, -2.0], index=pd.DatetimeIndex([pd.Timestamp(d1), pd.Timestamp(d2)]))
    out = build_roll_adjusted(frame, [d1, d2], spreads, None)
    assert out[CONT_COLUMN].iloc[0] == pytest.approx(100.0 - 3.0 - 2.0)
    assert out[CONT_COLUMN].diff().iloc[1] == pytest.approx(0.0)
    assert out[CONT_COLUMN].diff().iloc[3] == pytest.approx(0.0)
    assert out[CONT_COLUMN].iloc[-1] == pytest.approx(96.0)


def test_wti_proxy_is_used_when_the_brent_spread_is_missing():
    frame = bars([100.0, 100.5, 98.0, 98.3])
    roll_day = frame.index[2].date()
    spreads = pd.Series([np.nan], index=pd.DatetimeIndex([pd.Timestamp(roll_day)]))
    spreads.attrs["wti_proxy"] = pd.Series([-2.3], index=pd.DatetimeIndex([pd.Timestamp(roll_day)]))
    out = build_roll_adjusted(frame, [roll_day], spreads, None)
    assert out[SOURCE_COLUMN].iloc[2] == "wti_proxy"
    assert out[CONT_COLUMN].diff().iloc[2] == pytest.approx(-2.5 + 2.3)
    assert out.attrs["approx"] is True
    assert out.attrs["roll_sources"] == {"curve": 0, "wti_proxy": 1, "spot_return": 0, "none": 0}


def test_spot_return_fallback_replaces_the_roll_day_return():
    frame = bars([100.0, 101.0, 98.0, 98.5])
    roll_day = frame.index[2].date()
    # the physical market fell 0.4 $ that day: that is the move the continuous series must show
    spot = pd.Series([110.0, 111.0, 110.6, 110.8], index=frame.index)
    out = build_roll_adjusted(frame, [roll_day], None, spot)
    assert out[SOURCE_COLUMN].iloc[2] == "spot_return"
    assert out[CONT_COLUMN].diff().iloc[2] == pytest.approx(-0.4)
    assert out.attrs["approx"] is True


def test_no_spread_leaves_the_day_unadjusted_and_says_so():
    frame = bars([100.0, 101.0, 98.0, 98.5])
    roll_day = frame.index[2].date()
    out = build_roll_adjusted(frame, [roll_day], None, None)
    assert out[SOURCE_COLUMN].iloc[2] == "none"
    assert out[CONT_COLUMN].diff().iloc[2] == pytest.approx(-3.0)  # the jump stays visible: honest
    assert out.attrs["rolls"] == []
    pd.testing.assert_series_equal(out[CONT_COLUMN], frame["close"], check_names=False)


def test_roll_without_a_bar_is_skipped():
    frame = bars([100.0, 101.0, 102.0])
    holiday = (frame.index[-1] + pd.Timedelta(days=1)).date()
    out = build_roll_adjusted(frame, [holiday, frame.index[0].date()], None, None)
    assert out.attrs["rolls"] == []  # no bar on the holiday, and the first bar has no previous close
    assert out[CONT_COLUMN].equals(frame["close"].rename(CONT_COLUMN))


def test_build_roll_adjusted_requires_a_close():
    with pytest.raises(ValueError, match="must carry a 'close' column"):
        build_roll_adjusted(pd.DataFrame({"settle": [1.0]}), [], None, None)


def test_curve_spreads_from_table_uses_the_last_known_curve():
    curve = pd.DataFrame(
        {"M1": [101.41, 101.22], "M2": [97.95, 98.25]},
        index=pd.DatetimeIndex(["2026-10-02", "2026-10-05"]),
    )
    spreads = curve_spreads_from_table(curve, [date(2026, 10, 5), date(2026, 10, 6)])
    assert spreads.loc[pd.Timestamp("2026-10-05")] == pytest.approx(98.25 - 101.22)
    assert spreads.loc[pd.Timestamp("2026-10-06")] == pytest.approx(98.25 - 101.22)  # carried from the last snapshot
    # a curve older than the staleness bound is NOT carried forward: the caller falls back to the spot return
    assert curve_spreads_from_table(curve, [date(2026, 10, 20)]).empty
    assert len(curve_spreads_from_table(curve, [date(2026, 10, 20)], max_staleness_days=30)) == 1
    assert curve_spreads_from_table(pd.DataFrame(), [date(2026, 10, 5)]).empty
    assert curve_spreads_from_table(curve, []).empty


# --------------------------------------------------------------------------------------------------- curve table
def curve_snapshot(asof: str, closes: dict[str, float], codes: dict[str, str]) -> FetchResult:
    rows = []
    for rank, close in closes.items():
        rows.append(
            {
                "code": codes[rank],
                "rank": rank,
                "close": close,
                "date": pd.Timestamp(asof),
                "published_at": pd.Timestamp(f"{asof} 22:00:00+00:00"),
            }
        )
    frame = pd.DataFrame(rows).set_index("code")
    return FetchResult(source="yahoo", frame=frame, fetched_at=datetime.now(tz=UTC), meta={"asof": asof})


def test_build_curve_table_from_snapshots():
    s1 = curve_snapshot(
        "2026-10-02", {"M1": 101.41, "M2": 97.95, "M6": 90.5}, {"M1": "BZZ26", "M2": "BZF27", "M6": "BZK27"}
    )
    s2 = curve_snapshot("2026-10-05", {"M1": 101.22, "M2": 98.25}, {"M1": "BZZ26", "M2": "BZF27"})
    table = build_curve_table([s1, s2])
    assert list(table.index.strftime("%Y-%m-%d")) == ["2026-10-02", "2026-10-05"]
    assert list(table.columns) == [*CURVE_COLUMNS, "M1_code", "published_at"]
    assert table.loc["2026-10-05", "M1"] == pytest.approx(101.22)
    assert table.loc["2026-10-05", "M1_code"] == "BZZ26"
    # a rank the snapshot does not carry stays NaN: never filled across ranks or across days
    assert bool(pd.isna(table.loc["2026-10-05", "M6"]))
    assert bool(pd.isna(table.loc["2026-10-02", "M3"]))
    assert table.loc["2026-10-02", "M6"] == pytest.approx(90.5)
    assert table["published_at"].iloc[-1] == pd.Timestamp("2026-10-05 22:00:00+00:00")
    assert table["M1"].dtype == "float64"


def test_build_curve_table_ignores_unusable_snapshots():
    empty = FetchResult(source="yahoo", frame=pd.DataFrame(), fetched_at=datetime.now(tz=UTC), meta={})
    assert build_curve_table([empty]).empty
    assert list(build_curve_table([]).columns) == [*CURVE_COLUMNS, "M1_code", "published_at"]
    # a snapshot without an as-of date and without a 'date' column cannot be placed in time
    frame = pd.DataFrame({"rank": ["M1"], "close": [100.0]}, index=pd.Index(["BZZ26"]))
    orphan = FetchResult(source="yahoo", frame=frame, fetched_at=datetime.now(tz=UTC), meta={})
    assert build_curve_table([orphan]).empty


def test_build_curve_table_on_the_real_fixture_snapshot():
    """The archived 2026-10-05 Brent curve: steep backwardation, M1 = BZZ26."""
    from engine.data.fixtures import load_fixture_market_data

    md = load_fixture_market_data()
    if md.curve.empty:
        pytest.skip("fixture without a curve snapshot")
    row = md.curve.iloc[-1]
    assert row["M1_code"] == "BZZ26"
    assert row["M1"] > row["M2"] > row["M3"] > row["M6"] > row["M12"]  # backwardation
    assert 70.0 < float(row["M12"]) < 90.0
