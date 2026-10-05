"""YahooAdapter: offline tests on real snapshots (tests/fixtures/yahoo, captured 2026-10-05) + opt-in network tests."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from engine.core.calendar import listed_months
from engine.core.errors import DataUnavailable
from engine.data.adapters.yahoo import (
    SymbolNotFound,
    YahooAdapter,
    daily_published_at,
    trading_dates,
)

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"
NOT_FOUND = "bzx26_nym_404.json"
PRE_HISTORY = "bz_f_1d_pre_history_400.json"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> dict[str, Any]:
    return json.loads(fixture_bytes(name))


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body

    def json(self) -> Any:
        return json.loads(self.content)


class FakeSession:
    """Routes `GET .../chart/<symbol>` to (status, body) replies; a list of replies is consumed call by call."""

    def __init__(self, routes: dict[str, Any]):
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> FakeResponse:
        symbol = url.rsplit("/", 1)[1]
        self.calls.append((symbol, dict(params or {})))
        reply = self.routes[symbol]
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        status, body = reply
        return FakeResponse(status, body if isinstance(body, bytes) else json.dumps(body).encode())


def adapter(routes: dict[str, Any]) -> tuple[YahooAdapter, FakeSession]:
    a = YahooAdapter(min_interval=0.0, backoff=0.001, retries=2)
    s = FakeSession(routes)
    a.session = s  # type: ignore[assignment]
    return a, s


# ---------------------------------------------------------------------------- daily parsing


def test_fetch_daily_parses_real_snapshot():
    a, s = adapter({"BZ=F": (200, fixture_bytes("bz_f_1d_5d.json"))})
    res = a.fetch_daily("BZ=F", date(2026, 9, 28), date(2026, 10, 5))
    df = res.frame
    assert res.source == "yahoo" and not res.approx
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "open_interest", "adjclose", "published_at"]
    # index: tz-naive trading dates, 6 sessions (28/9 .. 5/10, the 5th is the session in progress)
    assert isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None and df.index.name == "date"
    assert [d.strftime("%Y-%m-%d") for d in df.index] == [
        "2026-09-28",
        "2026-09-29",
        "2026-09-30",
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
    ]
    assert all(ts == ts.normalize() for ts in df.index)
    # values straight from the snapshot (BZ=F 2026-10-02 close 102.25, volume 54607)
    assert df.loc["2026-10-02", "close"] == pytest.approx(102.25)
    assert df.loc["2026-10-02", "volume"] == pytest.approx(54607.0)
    assert df.loc["2026-09-28", "open"] == pytest.approx(105.80, abs=0.01)
    assert df["open_interest"].isna().all()
    assert df["close"].notna().all()
    assert df.attrs["units"] == "USD" and res.meta["exchangeTimezoneName"] == "America/New_York"
    assert res.meta["firstTradeDate"] == 1185768000  # 2007-07-30 00:00 New York
    # one request, explicit period1/period2 (never range=max) with interval=1d
    assert len(s.calls) == 1
    sym, params = s.calls[0]
    assert sym == "BZ=F" and params["interval"] == "1d" and "range" not in params
    assert params["period1"] < params["period2"]


def test_daily_published_at_is_23_london_in_utc():
    a, _ = adapter({"BZ=F": (200, fixture_bytes("bz_f_1d_5d.json"))})
    df = a.fetch_daily("BZ=F", date(2026, 9, 28), date(2026, 10, 5)).frame
    pub = df["published_at"]
    assert str(pub.dtype).endswith("UTC]")
    # 2026-10-05 is British Summer Time: 23:00 London = 22:00 UTC
    assert pub.loc["2026-10-05"] == pd.Timestamp("2026-10-05 22:00", tz="UTC")
    assert pub.loc["2026-09-28"] == pd.Timestamp("2026-09-28 22:00", tz="UTC")
    # the row of the session in progress is not knowable before 23:00 London -> point-in-time filters exclude it
    assert pub.loc["2026-10-05"] > pd.Timestamp("2026-10-05 06:45", tz="UTC")
    # GMT in winter: 23:00 London = 23:00 UTC
    winter = daily_published_at(pd.DatetimeIndex([pd.Timestamp("2026-12-07")]))
    assert winter[0] == pd.Timestamp("2026-12-07 23:00", tz="UTC")
    # equals the helper used by the engine
    assert (pub.to_numpy() == daily_published_at(df.index).to_numpy()).all()


def test_trading_date_conversion_from_session_start():
    # BZ=F daily bars are stamped 04:00 UTC (00:00 New York, EDT); ^OVX 13:30 UTC (09:30 New York). Same local date.
    ny = ZoneInfo("America/New_York")
    stamps = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-10-05 04:00", tz="UTC"),  # EDT midnight
            pd.Timestamp("2026-01-05 05:00", tz="UTC"),  # EST midnight
            pd.Timestamp("2026-10-02 13:30", tz="UTC"),  # index open 09:30 NY
            pd.Timestamp("2009-04-13 14:30", tz="UTC"),  # odd historical stamp 10:30 NY
        ]
    )
    out = trading_dates(stamps, ny)
    assert out.tz is None
    assert [d.strftime("%Y-%m-%d") for d in out] == ["2026-10-05", "2026-01-05", "2026-10-02", "2009-04-13"]
    # a UTC-naive reading would have given the same dates here, but 23:00 New York stamps roll over in UTC
    late = pd.DatetimeIndex([pd.Timestamp("2026-10-06 02:00", tz="UTC")])  # 22:00 New York on 2026-10-05
    assert trading_dates(late, ny)[0] == pd.Timestamp("2026-10-05")
    # ^OVX snapshot end-to-end
    a, _ = adapter({"^OVX": (200, fixture_bytes("ovx_1d_5d.json"))})
    df = a.fetch_daily("^OVX", date(2026, 9, 28), date(2026, 10, 2)).frame
    assert [d.day for d in df.index] == [28, 29, 30, 1, 2]
    assert df.loc["2026-10-02", "close"] == pytest.approx(51.0)
    assert df.loc["2026-10-02", "published_at"] == pd.Timestamp("2026-10-02 22:00", tz="UTC")


def test_null_rows_are_dropped_never_filled():
    # real snapshot: BZ=F 2009-04-13..24 has 10 timestamps, 8 of them with null quotes
    a, _ = adapter({"BZ=F": (200, fixture_bytes("bz_f_1d_2009-04_nulls.json"))})
    df = a.fetch_daily("BZ=F", date(2009, 4, 13), date(2009, 4, 24)).frame
    assert len(df) == 2
    assert [d.strftime("%Y-%m-%d") for d in df.index] == ["2009-04-13", "2009-04-14"]
    assert df.loc["2009-04-13", "close"] == pytest.approx(52.14, abs=0.01)
    assert df.drop(columns=["open_interest"]).notna().all().all()  # open_interest is NaN by design
    # in-memory variant: null close in the middle of the 5d snapshot -> that row disappears, neighbours untouched
    payload = fixture_json("bz_f_1d_5d.json")
    quote = payload["chart"]["result"][0]["indicators"]["quote"][0]
    quote["close"][1] = None
    quote["volume"][3] = None  # a null volume alone keeps the bar (volume NaN)
    a, _ = adapter({"BZ=F": (200, payload)})
    df = a.fetch_daily("BZ=F", date(2026, 9, 28), date(2026, 10, 5)).frame
    assert "2026-09-29" not in df.index.strftime("%Y-%m-%d")
    assert len(df) == 5
    assert np.isnan(df.loc["2026-10-01", "volume"]) and df.loc["2026-10-01", "close"] == pytest.approx(102.31, abs=0.01)
    assert not df["close"].isna().any()


def test_duplicate_dates_keep_last_and_window_filter():
    payload = fixture_json("bz_f_1d_5d.json")
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    # Yahoo occasionally repeats a session with a different stamp (e.g. 14:30 UTC): same NY date -> keep the last one
    result["timestamp"].insert(1, result["timestamp"][0] + 10 * 3600)
    for key in quote:
        quote[key].insert(1, 999.0 if key != "volume" else 1.0)
    result["indicators"]["adjclose"][0]["adjclose"].insert(1, 999.0)
    a, _ = adapter({"BZ=F": (200, payload)})
    df = a.fetch_daily("BZ=F", date(2026, 9, 28), date(2026, 10, 5)).frame
    assert df.index.is_unique and df.index.is_monotonic_increasing
    assert df.loc["2026-09-28", "close"] == pytest.approx(999.0)
    # window filter is inclusive and trims what Yahoo returns around period1/period2
    df2 = a.fetch_daily("BZ=F", date(2026, 9, 30), date(2026, 10, 1)).frame
    assert [d.day for d in df2.index] == [30, 1]


def test_live_tick_row_is_dropped_from_daily():
    # Yahoo appends the live quote stamped meta.regularMarketTime when the window includes now (seen with range=max)
    payload = fixture_json("bz_f_1d_5d.json")
    result = payload["chart"]["result"][0]
    rmt = result["meta"]["regularMarketTime"]  # 2026-10-05 06:34:52 UTC
    result["timestamp"].append(rmt)
    for key, val in (("open", 101.59), ("high", 101.59), ("low", 101.59), ("close", 101.59), ("volume", 0)):
        result["indicators"]["quote"][0][key].append(val)
    result["indicators"]["adjclose"][0]["adjclose"].append(101.59)
    a, _ = adapter({"BZ=F": (200, payload)})
    df = a.fetch_daily("BZ=F", date(2026, 9, 28), date(2026, 10, 5)).frame
    assert len(df) == 6 and df.loc["2026-10-05", "open"] == pytest.approx(103.01)  # the 04:00 bar, not the tick


# ---------------------------------------------------------------------------- errors, chunking, retries


def test_contract_history_404_raises_without_retry():
    a, s = adapter({"BZX26.NYM": (404, fixture_bytes(NOT_FOUND))})
    with pytest.raises(DataUnavailable) as ei:
        a.fetch_contract_history("BZX26")
    assert isinstance(ei.value, SymbolNotFound) and "BZX26.NYM" in str(ei.value)
    assert len(s.calls) == 1  # a 404 is final: no retries, no older chunks


def test_contract_history_builds_symbol_and_sets_meta():
    a, s = adapter({"BZZ26.NYM": (200, fixture_bytes("bzz26_nym_1d_5d.json"))})
    res = a.fetch_contract_history("BZZ26")
    assert s.calls[0][0] == "BZZ26.NYM" and res.meta["code"] == "BZZ26"
    assert res.frame.loc["2026-10-02", "close"] == pytest.approx(102.25)
    # firstTradeDate 2018-11-01 trims the default 1980 start: a single request covers the whole history
    assert len(s.calls) == 1 and res.meta["start"] == "1980-01-01"
    with pytest.raises(ValueError):
        a.fetch_contract_history("NOPE")


def test_empty_result_is_data_unavailable():
    body = {
        "chart": {
            "result": [{"meta": {"symbol": "BZ=F"}, "timestamp": [], "indicators": {"quote": [{}]}}],
            "error": None,
        }
    }
    a, _ = adapter({"BZ=F": (200, body)})
    with pytest.raises(DataUnavailable):
        a.fetch_daily("BZ=F", date(2026, 9, 28), date(2026, 10, 5))


def test_long_span_is_chunked_in_10_year_windows_and_prehistory_skipped():
    # BZ=F since 1990: most recent chunk (2016-10 .. 2026-10) first, then 2007-07-30 (firstTradeDate) .. 2016-10.
    # The 1980-1990 style 'Data doesn't exist' answer is skipped, not fatal.
    replies = [(200, fixture_bytes("bz_f_1d_5d.json")), (400, fixture_bytes(PRE_HISTORY))]
    a, s = adapter({"BZ=F": replies})
    res = a.fetch_daily("BZ=F", date(1990, 1, 1), date(2026, 10, 5))
    assert len(s.calls) == 2
    spans = [(p["period2"] - p["period1"]) / 86400 for _, p in s.calls]
    assert all(span <= 3652 + 3 for span in spans)
    assert s.calls[0][1]["period1"] > s.calls[1][1]["period1"]  # newest window first
    # second window starts at firstTradeDate (2007-07-30 NY) minus the 1-day guard, not in 1990
    assert datetime.fromtimestamp(s.calls[1][1]["period1"], tz=UTC).date() == date(2007, 7, 29)
    assert len(res.frame) == 6


def test_retry_on_empty_body_and_429_then_success():
    replies = [(200, b""), (429, b"Too Many Requests"), (200, fixture_bytes("bz_f_1d_5d.json"))]
    a, s = adapter({"BZ=F": replies})
    res = a.fetch_daily("BZ=F", date(2026, 9, 28), date(2026, 10, 5))
    assert len(s.calls) == 3 and len(res.frame) == 6


def test_unprocessable_range_is_not_retried():
    body = {
        "chart": {
            "result": None,
            "error": {
                "code": "Unprocessable Entity",
                "description": "1h data not available ... within the last 730 days.",
            },
        }
    }
    a, s = adapter({"BZ=F": (422, body)})
    with pytest.raises(DataUnavailable):
        a.fetch_intraday("BZ=F", "1h", 730)
    assert len(s.calls) == 1


def test_fetch_protocol_dispatch():
    a, s = adapter({"BZ=F": (200, fixture_bytes("bz_f_1d_5d.json"))})
    res = a.fetch(symbol="BZ=F", start=date(2026, 9, 28), end=date(2026, 10, 5))
    assert len(res.frame) == 6 and s.calls[0][1]["interval"] == "1d"


# ---------------------------------------------------------------------------- intraday


def test_intraday_1h_snapshot():
    a, s = adapter({"BZ=F": (200, fixture_bytes("bz_f_1h_7d.json"))})
    res = a.fetch_intraday("BZ=F", "1h", 7)
    df = res.frame
    assert s.calls[0][1] == {"range": "7d", "interval": "1h"}
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "published_at"]
    assert isinstance(df.index, pd.DatetimeIndex) and str(df.index.tz) == "UTC" and df.index.name == "ts"
    # 148 points in the file: 29 null rows + 1 trailing live tick (06:34:52) dropped -> 118 hourly bars on the grid
    assert len(df) == 118
    assert ((df.index.minute == 0) & (df.index.second == 0)).all()
    assert df.index.is_unique and df.index.is_monotonic_increasing
    assert df.index[0] == pd.Timestamp("2026-09-28 04:00", tz="UTC")
    assert df.index[-1] == pd.Timestamp("2026-10-05 06:00", tz="UTC")
    assert df["close"].notna().all()
    assert df.iloc[0]["close"] == pytest.approx(99.14, abs=0.01)
    # published_at = bar end
    assert (df["published_at"] == df.index + pd.Timedelta(hours=1)).all()
    assert df["published_at"].iloc[-1] == pd.Timestamp("2026-10-05 07:00", tz="UTC")
    assert res.meta["dataGranularity"] == "1h" and res.meta["lookback_days"] == 7


def test_intraday_lookback_clamped_and_bad_interval():
    a, s = adapter({"BZ=F": (200, fixture_bytes("bz_f_1h_7d.json"))})
    res = a.fetch_intraday("BZ=F", "1h", 10_000)
    assert s.calls[0][1]["range"] == "730d" and res.meta["lookback_days"] == 730
    with pytest.raises(ValueError):
        a.fetch_intraday("BZ=F", "2h", 7)


# ---------------------------------------------------------------------------- curve


def test_curve_rank_assembly_skips_404_keeps_rank_order():
    asof = date(2026, 10, 5)
    months = listed_months("BZ", asof, 3)
    assert months == [(2026, 12), (2027, 1), (2027, 2)]  # BZX26 expired 2026-09-30 -> Dec-26 is M1
    a, s = adapter(
        {
            "BZZ26.NYM": (200, fixture_bytes("bzz26_nym_1d_5d.json")),
            "BZF27.NYM": (404, fixture_bytes(NOT_FOUND)),  # simulate an unlisted month
            "BZG27.NYM": (200, fixture_bytes("bzg27_nym_1d_5d.json")),
        }
    )
    res = a.fetch_curve("BZ", asof, 3)
    df = res.frame
    assert [c for c, _ in s.calls] == ["BZZ26.NYM", "BZF27.NYM", "BZG27.NYM"]  # one request per contract, in order
    assert list(df.index) == ["BZZ26", "BZG27"] and df.index.name == "code"
    assert list(df["rank"]) == ["M1", "M3"]  # rank follows the listed-month calendar, not the rows found
    assert res.meta["missing"] == ["BZF27"] and res.meta["asof"] == "2026-10-05"
    assert res.meta["symbols"]["BZF27"] == "BZF27.NYM"
    for col in ["rank", "year", "month", "expiry", "close", "volume", "open_interest", "ts", "published_at"]:
        assert col in df.columns
    row = df.loc["BZZ26"]
    assert (row["year"], row["month"]) == (2026, 12)
    assert row["expiry"] == pd.Timestamp("2026-10-30")  # last business day of October (ICE rule)
    assert row["close"] == pytest.approx(101.59, abs=0.01) and row["date"] == pd.Timestamp("2026-10-05")
    assert row["ts"] == pd.Timestamp("2026-10-05 22:00", tz="UTC") and row["published_at"] == row["ts"]
    assert df.loc["BZG27", "close"] == pytest.approx(95.55, abs=0.01)
    assert df["open_interest"].isna().all()
    assert df["close"].iloc[0] > df["close"].iloc[1]  # backwardation in the snapshot
    # explicit window around asof, never range=
    for _, params in s.calls:
        assert params["interval"] == "1d" and "range" not in params
        assert params["period1"] < params["period2"]


def test_curve_uses_last_bar_on_or_before_asof():
    a, _ = adapter(
        {
            "BZZ26.NYM": (200, fixture_bytes("bzz26_nym_1d_5d.json")),
            "BZF27.NYM": (200, fixture_bytes("bzf27_nym_1d_5d.json")),
        }
    )
    res = a.fetch_curve("BZ", date(2026, 10, 2), 2)
    df = res.frame
    assert list(df["rank"]) == ["M1", "M2"]
    assert (df["date"] == pd.Timestamp("2026-10-02")).all()
    assert df.loc["BZZ26", "close"] == pytest.approx(102.25) and df.loc["BZF27", "close"] == pytest.approx(
        98.71, abs=0.01
    )
    assert res.meta["missing"] == []


def test_curve_all_missing_raises():
    a, _ = adapter({sym: (404, fixture_bytes(NOT_FOUND)) for sym in ("BZZ26.NYM", "BZF27.NYM")})
    with pytest.raises(DataUnavailable):
        a.fetch_curve("BZ", date(2026, 10, 5), 2)


# ---------------------------------------------------------------------------- network (opt-in)


@pytest.fixture(scope="module")
def live(request: pytest.FixtureRequest) -> YahooAdapter:
    """Real adapter for the opt-in network tests; skipped in the default offline run (OPT_OFFLINE=1 without -m)."""
    selected = "network" in str(request.config.getoption("-m") or "")
    if os.environ.get("OPT_OFFLINE") == "1" and not selected:
        pytest.skip("network tests are opt-in: run `pytest -m network`")
    return YahooAdapter(min_interval=1.0)


@pytest.mark.network
def test_network_bz_f_daily_since_2007(live: YahooAdapter):
    res = live.fetch_daily("BZ=F", date(2007, 8, 1))
    df = res.frame
    assert len(df) > 4000
    assert df.index.is_unique and df.index.is_monotonic_increasing and df.index.tz is None
    assert df.index[0] >= pd.Timestamp("2007-08-01")
    assert (df.index.weekday < 5).all()
    assert df["close"].notna().all() and (df["close"] > 0).all()
    assert df.index[-1] >= pd.Timestamp(date.today() - timedelta(days=7))
    assert str(df["published_at"].dtype).endswith("UTC]")
    assert (df["published_at"].dt.tz_convert("Europe/London").dt.hour == 23).all()


@pytest.mark.network
def test_network_bz_f_1h_730d(live: YahooAdapter):
    res = live.fetch_intraday("BZ=F", "1h", 730)
    df = res.frame
    assert len(df) > 8000
    assert str(df.index.tz) == "UTC" and df.index.is_unique and df.index.is_monotonic_increasing
    assert df["close"].notna().all()
    assert (df["published_at"] == df.index + pd.Timedelta(hours=1)).all()
    assert df.index[0] <= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=700)


@pytest.mark.network
def test_network_curve_bz_12_months(live: YahooAdapter):
    asof = date.today()
    res = live.fetch_curve("BZ", asof, 12)
    df = res.frame
    assert len(df) >= 9  # a few deferred months may be unlisted on Yahoo
    assert df["rank"].iloc[0] == "M1"
    assert df["close"].notna().all() and (df["close"] > 0).all()
    assert (df["published_at"] <= pd.Timestamp(asof + timedelta(days=1), tz="UTC")).all()
    assert all(code not in df.index for code in res.meta["missing"])
