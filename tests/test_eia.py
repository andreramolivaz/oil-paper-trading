"""Tests for engine.data.adapters.eia.

Offline tests run on real snapshots captured on 2026-10-05 (tests/fixtures/eia/README.md). The EIA API is
replayed by a fake HttpClient that honours facets, sort, length and offset, so pagination is exercised for real.
Network tests are opt-in (``pytest -m network``) and need ``EIA_API_KEY`` for the API routes.
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from engine.core.errors import DataUnavailable
from engine.data.adapters.eia import (
    FUTURES_SERIES,
    SPOT_ROUTE,
    STEO_ROUTE,
    STOCKS_ROUTE,
    SUPPLY_ROUTE,
    WPSR_SERIES,
    WPSR_VALUE_COLUMNS,
    EiaAdapter,
    EiaFallbackAdapter,
    parse_wpsr_table1,
    spot_published_at,
    steo_published_at,
    steo_vintage,
    wpsr_published_at,
    wpsr_release_day,
)
from engine.data.market_data import WPSR_COLUMNS

FIX = Path(__file__).parent / "fixtures" / "eia"


def _rows(name: str) -> list[dict[str, Any]]:
    payload = json.loads((FIX / name).read_text(encoding="utf-8"))
    return list(payload["response"]["data"])


class FakeApiClient:
    """Replays fixture rows like the EIA API: facet filter, period sort, ``length``/``offset`` paging."""

    def __init__(self, routes: dict[str, list[dict[str, Any]]]):
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def get_json(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        params = dict(params or {})
        self.calls.append({"url": url, **params})
        route = next(r for r in self.routes if url.endswith(r))
        rows = self.routes[route]
        facet_keys = [k for k in params if k.startswith("facets[")]
        for key in facet_keys:
            name = key[len("facets[") : -len("][]")]
            wanted = set(params[key])
            rows = [r for r in rows if r.get(name) in wanted]
        if "start" in params:
            rows = [r for r in rows if r["period"] >= params["start"]]
        if "end" in params:
            rows = [r for r in rows if r["period"] <= params["end"]]
        rows = sorted(rows, key=lambda r: r["period"], reverse=params.get("sort[0][direction]") == "desc")
        offset = int(params.get("offset", 0))
        length = int(params.get("length", 5000))
        return {
            "response": {"total": str(len(rows)), "dateFormat": "YYYY-MM-DD", "data": rows[offset : offset + length]},
            "request": {"command": f"/v2/{route}", "params": params},
        }


class FakeBytesClient:
    def __init__(self, content: bytes):
        self.content = content
        self.urls: list[str] = []

    def get_bytes(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> bytes:
        self.urls.append(url)
        return self.content


def _api(routes: dict[str, list[dict[str, Any]]], page_size: int = 5000) -> tuple[EiaAdapter, FakeApiClient]:
    client = FakeApiClient(routes)
    return EiaAdapter("test-key", client=client, page_size=page_size), client  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------------------
# key handling
# ---------------------------------------------------------------------------------------------------------------
def test_missing_key_raises_before_any_request() -> None:
    client = FakeApiClient({SPOT_ROUTE: _rows("rbrte_spot_page1_100rows.json")})
    adapter = EiaAdapter(None, client=client)  # type: ignore[arg-type]
    for call in (adapter.fetch_spot, adapter.fetch_futures_hist, adapter.fetch_wpsr, adapter.fetch_steo_brent):
        with pytest.raises(DataUnavailable, match="API key"):
            call()
    assert client.calls == []
    assert EiaAdapter("").api_key is None


# ---------------------------------------------------------------------------------------------------------------
# spot prices
# ---------------------------------------------------------------------------------------------------------------
def test_fetch_spot_paginates_with_offset() -> None:
    adapter, client = _api({SPOT_ROUTE: _rows("rbrte_spot_page1_100rows.json")}, page_size=40)
    res = adapter.fetch_spot("RBRTE")

    assert [c["offset"] for c in client.calls] == [0, 40, 80]
    assert all(c["length"] == 40 for c in client.calls)
    assert all(c["sort[0][column]"] == "period" and c["sort[0][direction]"] == "asc" for c in client.calls)
    assert all(c["facets[series][]"] == ["RBRTE"] and c["frequency"] == "daily" for c in client.calls)
    assert all(c["data[0]"] == "value" and c["api_key"] == "test-key" for c in client.calls)
    assert client.calls[0]["url"] == "https://api.eia.gov/v2/" + SPOT_ROUTE

    f = res.frame
    assert res.source == "eia"
    assert len(f) == 100
    assert list(f.columns) == ["value", "published_at"]
    assert f.index.tz is None and f.index.is_monotonic_increasing and f.index.is_unique
    assert f.index[0] == pd.Timestamp("1987-05-20") and f["value"].iloc[0] == 18.63
    assert f.index[-1] == pd.Timestamp("1987-10-07") and f["value"].iloc[-1] == 18.58
    assert str(f["published_at"].dt.tz) == "UTC"
    assert f.attrs["units"] == "USD/bbl"
    assert res.meta["pages"] == 3 and res.meta["total"] == 100 and res.meta["series"] == "RBRTE"


def test_fetch_spot_drops_null_values_and_rejects_empty() -> None:
    rows = _rows("rbrte_spot_page1_100rows.json")[:5]
    rows[2] = {**rows[2], "value": None}
    adapter, _ = _api({SPOT_ROUTE: rows})
    f = adapter.fetch_spot("RBRTE").frame
    assert len(f) == 4 and pd.Timestamp(rows[2]["period"]) not in f.index

    adapter, _ = _api({SPOT_ROUTE: []})
    with pytest.raises(DataUnavailable):
        adapter.fetch_spot("RBRTE")


def test_fetch_spot_rejects_malformed_payload() -> None:
    class BadClient:
        def get_json(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
            return {"error": "invalid api key", "code": 403}

    adapter = EiaAdapter("k", client=BadClient())  # type: ignore[arg-type]
    with pytest.raises(DataUnavailable, match="unexpected response"):
        adapter.fetch_spot()


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 29), datetime(2026, 9, 30, 18, tzinfo=UTC)),  # Tuesday -> next day's Wednesday release
        (date(2026, 9, 25), datetime(2026, 9, 30, 18, tzinfo=UTC)),  # Friday -> following Wednesday
        (date(2026, 9, 30), datetime(2026, 10, 7, 18, tzinfo=UTC)),  # Wednesday -> strictly after: next Wednesday
        (date(2026, 10, 1), datetime(2026, 10, 7, 18, tzinfo=UTC)),  # Thursday
        (date(1987, 5, 20), datetime(1987, 5, 27, 18, tzinfo=UTC)),  # first Brent observation (a Wednesday)
    ],
)
def test_spot_published_at_is_next_wednesday_18_utc(day: date, expected: datetime) -> None:
    out = spot_published_at(pd.DatetimeIndex([pd.Timestamp(day)]))
    assert out[0].to_pydatetime() == expected


# ---------------------------------------------------------------------------------------------------------------
# WPSR
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("period", "release"),
    [
        (date(2026, 9, 25), date(2026, 9, 30)),  # plain week -> Wednesday
        (date(2026, 9, 4), date(2026, 9, 10)),  # Labor Day Monday 2026-09-07 -> Thursday
        (date(2026, 1, 16), date(2026, 1, 22)),  # MLK Monday 2026-01-19 -> Thursday
        (date(2026, 5, 22), date(2026, 5, 28)),  # Memorial Day Monday 2026-05-25 -> Thursday
        (date(2024, 6, 14), date(2024, 6, 20)),  # Juneteenth on Wednesday 2024-06-19 -> Thursday
        (date(2023, 6, 30), date(2023, 7, 6)),  # Independence Day on Tuesday 2023-07-04 -> Thursday
        (date(2026, 11, 20), date(2026, 11, 25)),  # Thanksgiving week: Thursday holiday does not move Wednesday
        (date(2026, 12, 18), date(2026, 12, 23)),  # Christmas on Friday: no shift
        (date(2026, 3, 27), date(2026, 4, 1)),  # Good Friday week: not a federal holiday on Mon-Wed, no shift
    ],
)
def test_wpsr_release_day(period: date, release: date) -> None:
    assert wpsr_release_day(period) == release


def test_wpsr_published_at_is_10_30_new_york_in_utc() -> None:
    assert wpsr_published_at(date(2026, 9, 25)) == datetime(2026, 9, 30, 14, 30, tzinfo=UTC)  # EDT
    assert wpsr_published_at(date(2026, 9, 4)) == datetime(2026, 9, 10, 14, 30, tzinfo=UTC)  # Labor Day week
    assert wpsr_published_at(date(2026, 1, 16)) == datetime(2026, 1, 22, 15, 30, tzinfo=UTC)  # EST, MLK week


def test_wpsr_series_mapping_covers_contract() -> None:
    assert list(WPSR_SERIES) == WPSR_VALUE_COLUMNS == WPSR_COLUMNS[1:]
    stocks = {sid for route, sid in WPSR_SERIES.values() if route == STOCKS_ROUTE}
    flows = {sid for route, sid in WPSR_SERIES.values() if route == SUPPLY_ROUTE}
    assert stocks == {"WCESTUS1", "W_EPC0_SAX_YCUOK_MBBL", "WGTSTUS1", "WDISTUS1"}
    assert flows == {"WCRRIUS2", "WCRFPUS2", "WCRIMUS2", "WCREXUS2", "WGFUPUS2", "WDIUPUS2"}


def test_fetch_wpsr_wide_frame_from_both_routes() -> None:
    adapter, client = _api(
        {STOCKS_ROUTE: _rows("wpsr_stoc_wstk_20weeks.json"), SUPPLY_ROUTE: _rows("wpsr_sum_sndw_20weeks.json")}
    )
    res = adapter.fetch_wpsr()
    f = res.frame

    assert len(client.calls) == 2
    assert {c["frequency"] for c in client.calls} == {"weekly"}
    assert list(f.columns) == [*WPSR_VALUE_COLUMNS, "published_at"]
    assert f.index.name == "period" and f.index.tz is None and len(f) == 20
    assert all(ts.weekday() == 4 for ts in f.index)  # week-ending Fridays

    last = f.loc[pd.Timestamp("2026-09-25")]
    assert last["crude_stocks"] == 427320.0
    assert last["cushing_stocks"] == 24301.0
    assert last["gasoline_stocks"] == 204362.0
    assert last["distillate_stocks"] == 105180.0
    assert last["refinery_inputs"] == 16257.0
    assert last["crude_production"] == 13955.0
    assert last["crude_imports"] == 5698.0
    assert last["crude_exports"] == 3570.0
    assert last["gasoline_supplied"] == 8689.0
    assert last["distillate_supplied"] == 3948.0
    assert last["published_at"] == pd.Timestamp("2026-09-30 14:30", tz="UTC")
    # Labor Day week: 2026-09-04 data released Thursday 2026-09-10
    assert f.loc[pd.Timestamp("2026-09-04"), "published_at"] == pd.Timestamp("2026-09-10 14:30", tz="UTC")
    assert f.attrs["units"]["crude_stocks"] == "kbbl" and f.attrs["units"]["refinery_inputs"] == "kbbl/d"
    assert res.meta["series"]["cushing_stocks"] == "W_EPC0_SAX_YCUOK_MBBL"


def test_fetch_wpsr_units_are_thousand_barrels() -> None:
    adapter, _ = _api(
        {STOCKS_ROUTE: _rows("wpsr_stoc_wstk_20weeks.json"), SUPPLY_ROUTE: _rows("wpsr_sum_sndw_20weeks.json")}
    )
    f = adapter.fetch_wpsr().frame
    # US commercial crude ~400-450 million bbl -> 400 000-450 000 kbbl; Cushing 20-30 Mbbl; inputs ~16 Mb/d
    assert f["crude_stocks"].between(300_000, 600_000).all()
    assert f["cushing_stocks"].between(15_000, 80_000).all()
    assert f["gasoline_stocks"].between(150_000, 300_000).all()
    assert f["distillate_stocks"].between(80_000, 200_000).all()
    assert f["refinery_inputs"].between(12_000, 20_000).all()
    assert f["crude_production"].between(10_000, 16_000).all()
    assert f["gasoline_supplied"].between(7_000, 10_500).all()
    assert f["distillate_supplied"].between(2_500, 5_500).all()
    assert f[WPSR_VALUE_COLUMNS].notna().all().all()


def test_fetch_wpsr_fails_when_one_route_fails() -> None:
    adapter, _ = _api({STOCKS_ROUTE: _rows("wpsr_stoc_wstk_20weeks.json"), SUPPLY_ROUTE: []})
    with pytest.raises(DataUnavailable):
        adapter.fetch_wpsr()


# ---------------------------------------------------------------------------------------------------------------
# futures history
# ---------------------------------------------------------------------------------------------------------------
def test_fetch_futures_hist_wide() -> None:
    adapter, client = _api({"petroleum/pri/fut/data/": _rows("fut_rclc1_4_last3days.json")})
    res = adapter.fetch_futures_hist()
    f = res.frame
    assert client.calls[0]["facets[series][]"] == list(FUTURES_SERIES)
    assert list(f.columns) == [*FUTURES_SERIES, "published_at"]
    assert list(f.index) == [pd.Timestamp("2024-04-03"), pd.Timestamp("2024-04-04"), pd.Timestamp("2024-04-05")]
    last = f.iloc[-1]
    assert (last["RCLC1"], last["RCLC2"], last["RCLC3"], last["RCLC4"]) == (86.91, 86.1, 85.2, 84.24)
    assert last["published_at"] == pd.Timestamp("2024-04-06 00:00", tz="UTC")
    assert res.approx is True


# ---------------------------------------------------------------------------------------------------------------
# STEO
# ---------------------------------------------------------------------------------------------------------------
def test_steo_vintage_heuristic() -> None:
    assert steo_vintage(datetime(2026, 10, 5, 12, tzinfo=UTC)) == date(2026, 9, 1)  # October STEO not yet out
    assert steo_vintage(datetime(2026, 10, 6, 0, tzinfo=UTC)) == date(2026, 10, 1)
    assert steo_vintage(datetime(2026, 1, 3, tzinfo=UTC)) == date(2025, 12, 1)
    assert steo_published_at(date(2026, 9, 1)) == datetime(2026, 9, 8, 17, tzinfo=UTC)


def test_fetch_steo_brent() -> None:
    adapter, client = _api({STEO_ROUTE: _rows("steo_brepuus_full.json")})
    res = adapter.fetch_steo_brent(vintage=date(2026, 9, 1))
    f = res.frame
    assert client.calls[0]["facets[seriesId][]"] == ["BREPUUS"] and client.calls[0]["frequency"] == "monthly"
    assert list(f.columns) == ["value", "is_forecast", "published_at"]
    assert f.index.name == "period" and f.index.tz is None and len(f) == 456
    assert f.index[0] == pd.Timestamp("1990-01-01") and f["value"].iloc[0] == 21.251
    assert f.index[-1] == pd.Timestamp("2027-12-01") and f["value"].iloc[-1] == 62.0
    assert f.loc[pd.Timestamp("2026-08-01"), "value"] == 91.08 and not f.loc[pd.Timestamp("2026-08-01"), "is_forecast"]
    assert f.loc[pd.Timestamp("2026-09-01"), "value"] == 93.0 and f.loc[pd.Timestamp("2026-09-01"), "is_forecast"]
    assert (f["published_at"] == pd.Timestamp("2026-09-08 17:00", tz="UTC")).all()
    assert res.meta["vintage"] == "2026-09"


# ---------------------------------------------------------------------------------------------------------------
# fallbacks
# ---------------------------------------------------------------------------------------------------------------
def test_fetch_spot_xls_trimmed_workbook() -> None:
    client = FakeBytesClient((FIX / "RBRTEd_trimmed.xlsx").read_bytes())
    res = EiaFallbackAdapter(client=client).fetch_spot_xls()  # type: ignore[arg-type]
    f = res.frame
    assert client.urls == ["https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls"]
    assert res.source == "eia_xls" and res.meta["series"] == "RBRTE"
    assert list(f.columns) == ["value", "published_at"] and f.index.name == "date" and f.index.tz is None
    assert len(f) == 120 and f.index.is_monotonic_increasing and f.index.is_unique
    assert f.index[0] == pd.Timestamp("1987-05-20") and f["value"].iloc[0] == 18.63
    assert f.index[-1] == pd.Timestamp("2026-09-29") and f["value"].iloc[-1] == 113.96
    assert f["published_at"].iloc[-1] == pd.Timestamp("2026-09-30 18:00", tz="UTC")
    assert f.attrs["units"] == "USD/bbl"


def test_fetch_spot_xls_rejects_non_workbook() -> None:
    adapter = EiaFallbackAdapter(client=FakeBytesClient(b"<html>503 Service Unavailable</html>"))  # type: ignore[arg-type]
    with pytest.raises(DataUnavailable):
        adapter.fetch_spot_xls()


def test_parse_wpsr_table1_latest_week() -> None:
    text = (FIX / "wpsr_table1_2026-09-25.csv").read_bytes().decode("cp1252")
    f = parse_wpsr_table1(text)
    assert list(f.columns) == [*WPSR_VALUE_COLUMNS, "published_at"]
    assert list(f.index) == [pd.Timestamp("2026-09-25")] and f.index.name == "period"
    row = f.iloc[0]
    assert row["crude_stocks"] == pytest.approx(427320.0)  # 427.320 million bbl -> kbbl
    assert math.isnan(row["cushing_stocks"])  # not in Table 1
    assert row["gasoline_stocks"] == pytest.approx(204362.0)
    assert row["distillate_stocks"] == pytest.approx(105180.0)
    assert row["refinery_inputs"] == 16257.0
    assert row["crude_production"] == 13955.0
    assert row["crude_imports"] == 5698.0
    assert row["crude_exports"] == 3570.0
    assert row["gasoline_supplied"] == 8689.0
    assert row["distillate_supplied"] == 3948.0
    assert row["published_at"] == pd.Timestamp("2026-09-30 14:30", tz="UTC")
    assert f.attrs["missing"] == ["cushing_stocks"]


def test_fetch_wpsr_table1_via_adapter_matches_api_fixture() -> None:
    client = FakeBytesClient((FIX / "wpsr_table1_2026-09-25.csv").read_bytes())
    res = EiaFallbackAdapter(client=client).fetch_wpsr_table1()  # type: ignore[arg-type]
    assert res.meta["missing"] == ["cushing_stocks"] and res.meta["period"] == "2026-09-25"

    api, _ = _api(
        {STOCKS_ROUTE: _rows("wpsr_stoc_wstk_20weeks.json"), SUPPLY_ROUTE: _rows("wpsr_sum_sndw_20weeks.json")}
    )
    api_row = api.fetch_wpsr().frame.loc[pd.Timestamp("2026-09-25")]
    csv_row = res.frame.iloc[0]
    for col in WPSR_VALUE_COLUMNS:
        if col == "cushing_stocks":
            continue
        assert csv_row[col] == pytest.approx(api_row[col]), col
    assert csv_row["published_at"] == api_row["published_at"]


def test_parse_wpsr_table1_rejects_garbage() -> None:
    with pytest.raises(DataUnavailable):
        parse_wpsr_table1("<html>maintenance</html>\n")


# ---------------------------------------------------------------------------------------------------------------
# network (opt-in)
# ---------------------------------------------------------------------------------------------------------------
def _network_selected(request: pytest.FixtureRequest) -> None:
    """Network tests run only when explicitly selected (``pytest -m network``): the default suite stays offline."""
    if "network" not in str(request.config.getoption("markexpr") or ""):
        pytest.skip("network tests run only with -m network")


def _api_key(request: pytest.FixtureRequest) -> str:
    _network_selected(request)
    key = os.environ.get("EIA_API_KEY")
    if not key:
        pytest.skip("EIA_API_KEY not set")
    return key


@pytest.mark.network
def test_network_spot_recent(request: pytest.FixtureRequest) -> None:
    res = EiaAdapter(_api_key(request)).fetch_spot("RBRTE", start=date(2026, 9, 1))
    f = res.frame
    assert f.index.min() >= pd.Timestamp("2026-09-01") and len(f) >= 15
    assert f.loc[pd.Timestamp("2026-09-29"), "value"] == 113.96
    assert f["value"].between(20, 300).all()


@pytest.mark.network
def test_network_wpsr_recent(request: pytest.FixtureRequest) -> None:
    res = EiaAdapter(_api_key(request)).fetch_wpsr(start=date(2026, 6, 1))
    f = res.frame
    assert pd.Timestamp("2026-09-25") in f.index
    assert f.loc[pd.Timestamp("2026-09-25"), "crude_stocks"] == 427320.0
    assert f.loc[pd.Timestamp("2026-09-25"), "cushing_stocks"] == 24301.0
    assert f[WPSR_VALUE_COLUMNS].notna().all().all()


@pytest.mark.network
def test_network_steo_and_futures(request: pytest.FixtureRequest) -> None:
    adapter = EiaAdapter(_api_key(request))
    steo = adapter.fetch_steo_brent().frame
    assert steo.index[0] == pd.Timestamp("1990-01-01") and steo.index[-1] >= pd.Timestamp("2027-12-01")
    fut = adapter.fetch_futures_hist(start=date(2024, 3, 1)).frame
    assert fut.index[-1] == pd.Timestamp("2024-04-05") and fut.loc[pd.Timestamp("2024-04-05"), "RCLC1"] == 86.91


@pytest.mark.network
def test_network_fallbacks_without_key(request: pytest.FixtureRequest) -> None:
    _network_selected(request)
    fb = EiaFallbackAdapter()
    spot = fb.fetch_spot_xls().frame
    assert spot.index[0] == pd.Timestamp("1987-05-20") and spot["value"].iloc[0] == 18.63
    assert len(spot) > 9000
    wpsr = fb.fetch_wpsr_table1().frame
    assert len(wpsr) == 1 and wpsr["crude_stocks"].iloc[0] > 300_000
