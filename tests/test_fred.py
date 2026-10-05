"""Tests for engine.data.adapters.fred.

Offline tests run on real snapshots captured on 2026-10-05 (tests/fixtures/fred/README.md): trimmed ``fredgraph.csv``
downloads (DCOILBRENTEU, OVXCLS) with their real missing-value rows, the real FRED API error envelopes, and a
``series/observations`` payload holding the same real Brent values in the API's documented JSON shape. The API is
replayed by a fake client that honours ``limit``/``offset``, so pagination is exercised for real. Network tests are
opt-in (``pytest -m network``); the API test needs ``FRED_API_KEY``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

from engine.core.errors import DataUnavailable
from engine.data.adapters.fred import (
    API_URL,
    CSV_URL,
    SERIES,
    SERIES_ALIAS,
    UNITS,
    FredAdapter,
    FredApiError,
    FredCsvAdapter,
    FredHttpClient,
    fred_published_at,
    parse_fredgraph_csv,
    redact_key,
    resolve_series,
)

FIX = Path(__file__).parent / "fixtures" / "fred"
BRENT_JSON = "dcoilbrenteu_observations_2026-08-03.json"
BRENT_CSV = "dcoilbrenteu_fredgraph_2026-08-03.csv"
OVX_CSV = "ovxcls_fredgraph_2026-08-03.csv"


def _payload(name: str) -> dict[str, Any]:
    return dict(json.loads((FIX / name).read_text(encoding="utf-8")))


class FakeApiClient:
    """Replays a full ``series/observations`` payload per series id, paging with ``limit``/``offset`` like FRED."""

    def __init__(self, payloads: dict[str, dict[str, Any]]):
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def get_json(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        params = dict(params or {})
        self.calls.append({"url": url, **params})
        sid = params["series_id"]
        if sid not in self.payloads:
            return {"error_code": 400, "error_message": "Bad Request.  The series does not exist."}
        full = self.payloads[sid]
        obs = full["observations"]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 100000))
        return {
            **full,
            "count": len(obs),
            "offset": offset,
            "limit": limit,
            "observations": obs[offset : offset + limit],
        }


class FakeBytesClient:
    def __init__(self, content: bytes):
        self.content = content
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_bytes(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> bytes:
        self.calls.append((url, dict(params or {})))
        return self.content


class _Resp:
    def __init__(self, status: int, body: str, reason: str = ""):
        self.status_code = status
        self.text = body
        self.content = body.encode()
        self.reason = reason

    def json(self) -> Any:
        return json.loads(self.text)


class _StubSession:
    def __init__(self, replies: list[Any]):
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: Any = None, **kw: Any) -> Any:
        self.calls.append({"url": url, "params": dict(params or {})})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _api(payloads: dict[str, dict[str, Any]], page_size: int = 100_000) -> tuple[FredAdapter, FakeApiClient]:
    client = FakeApiClient(payloads)
    return FredAdapter("test-key", client=client, page_size=page_size), client  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------------------
# contract: series map, aliases, published_at rule
# ---------------------------------------------------------------------------------------------------------------
def test_series_map_matches_contract() -> None:
    assert SERIES == {
        "brent_spot": "DCOILBRENTEU",
        "wti_spot": "DCOILWTICO",
        "ovx": "OVXCLS",
        "vix": "VIXCLS",
        "dxy": "DTWEXBGS",
        "us10y": "DGS10",
        "breakeven10y": "T10YIE",
        "fedfunds": "DFF",
    }
    assert SERIES_ALIAS["OVXCLS"] == "ovx" and set(UNITS) == set(SERIES.values())
    assert API_URL == "https://api.stlouisfed.org/fred/series/observations"
    assert CSV_URL == "https://fred.stlouisfed.org/graph/fredgraph.csv"


def test_resolve_series_accepts_alias_or_id() -> None:
    assert resolve_series("brent_spot") == "DCOILBRENTEU"
    assert resolve_series("DCOILBRENTEU") == "DCOILBRENTEU"
    assert resolve_series(" dgs10 ") == "DGS10"
    assert resolve_series("ovx") == "OVXCLS"
    with pytest.raises(ValueError):
        resolve_series("  ")


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 29), datetime(2026, 9, 30, 12, tzinfo=UTC)),  # Tuesday -> Wednesday
        (date(2026, 10, 2), datetime(2026, 10, 5, 12, tzinfo=UTC)),  # Friday -> Monday
        (date(2026, 9, 4), datetime(2026, 9, 8, 12, tzinfo=UTC)),  # Friday before Labor Day -> Tuesday
        (date(2026, 1, 16), datetime(2026, 1, 20, 12, tzinfo=UTC)),  # Friday before MLK day -> Tuesday
        (date(2026, 4, 2), datetime(2026, 4, 6, 12, tzinfo=UTC)),  # Thursday before Good Friday -> Monday
        (date(2026, 11, 25), datetime(2026, 11, 27, 12, tzinfo=UTC)),  # Wednesday before Thanksgiving -> Friday
        (date(2026, 12, 24), datetime(2026, 12, 28, 12, tzinfo=UTC)),  # Christmas Eve (Thu) -> Monday
        (date(2026, 10, 3), datetime(2026, 10, 5, 12, tzinfo=UTC)),  # Saturday (DFF is a 7-day series) -> Monday
        (date(2026, 10, 4), datetime(2026, 10, 5, 12, tzinfo=UTC)),  # Sunday -> Monday
    ],
)
def test_published_at_is_next_us_business_day_noon_utc(day: date, expected: datetime) -> None:
    out = fred_published_at(pd.DatetimeIndex([pd.Timestamp(day)]))
    assert str(out.tz) == "UTC" and out[0].to_pydatetime() == expected


def test_published_at_vectorised_and_empty() -> None:
    idx = pd.DatetimeIndex(["2026-09-03", "2026-09-04", "2026-09-04", "2026-09-08"])
    out = fred_published_at(idx)
    assert list(out) == [
        pd.Timestamp("2026-09-04 12:00", tz="UTC"),
        pd.Timestamp("2026-09-08 12:00", tz="UTC"),
        pd.Timestamp("2026-09-08 12:00", tz="UTC"),
        pd.Timestamp("2026-09-09 12:00", tz="UTC"),
    ]
    empty = fred_published_at(pd.DatetimeIndex([]))
    assert len(empty) == 0 and str(empty.tz) == "UTC"


# ---------------------------------------------------------------------------------------------------------------
# FredAdapter (API)
# ---------------------------------------------------------------------------------------------------------------
def test_missing_key_raises_before_any_request() -> None:
    client = FakeApiClient({"DCOILBRENTEU": _payload(BRENT_JSON)})
    for key in (None, ""):
        adapter = FredAdapter(key, client=client)  # type: ignore[arg-type]
        assert adapter.api_key is None
        with pytest.raises(DataUnavailable, match="API key"):
            adapter.fetch_series("DCOILBRENTEU")
    assert client.calls == []


def test_fetch_series_shape_and_missing_values_dropped() -> None:
    adapter, client = _api({"DCOILBRENTEU": _payload(BRENT_JSON)})
    res = adapter.fetch_series("DCOILBRENTEU")
    f = res.frame

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == API_URL
    assert call["series_id"] == "DCOILBRENTEU" and call["api_key"] == "test-key"
    assert call["file_type"] == "json" and call["observation_start"] == "1980-01-01"
    assert call["sort_order"] == "asc" and call["limit"] == 100_000 and call["offset"] == 0

    assert res.source == "fred" and res.approx is False
    assert list(f.columns) == ["value", "published_at"]
    assert f.index.name == "date" and f.index.tz is None and f.index.is_monotonic_increasing and f.index.is_unique
    assert f["value"].dtype == "float64" and str(f["published_at"].dt.tz) == "UTC"
    assert len(f) == 41  # 42 observations, 2026-08-31 is "." (UK bank holiday, no Brent assessment)
    assert pd.Timestamp("2026-08-31") not in f.index
    assert pd.Timestamp("2026-08-28") in f.index and pd.Timestamp("2026-09-01") in f.index
    assert f.index[0] == pd.Timestamp("2026-08-03") and f["value"].iloc[0] == 88.90
    assert f.index[-1] == pd.Timestamp("2026-09-29") and f["value"].iloc[-1] == 113.96  # = EIA RBRTE 2026-09-29
    assert f.loc[pd.Timestamp("2026-09-04"), "value"] == 102.24
    assert (f["value"] > 0).all()  # exact values asserted above; no synthetic range
    assert f.attrs["units"] == "USD/bbl" and f.attrs["series_id"] == "DCOILBRENTEU"
    assert res.meta["series_id"] == "DCOILBRENTEU" and res.meta["alias"] == "brent_spot"
    assert res.meta["count"] == 42 and res.meta["rows"] == 41 and res.meta["missing_dropped"] == 1
    assert res.meta["pages"] == 1 and res.meta["published_at_rule"] == "next US business day 12:00 UTC"
    assert res.asof == datetime(2026, 9, 29, tzinfo=UTC)


def test_fetch_series_published_at_follows_us_calendar() -> None:
    adapter, _ = _api({"DCOILBRENTEU": _payload(BRENT_JSON)})
    f = adapter.fetch_series("DCOILBRENTEU").frame
    pub = f["published_at"]
    assert pub.loc[pd.Timestamp("2026-09-29")] == pd.Timestamp("2026-09-30 12:00", tz="UTC")
    assert pub.loc[pd.Timestamp("2026-09-04")] == pd.Timestamp("2026-09-08 12:00", tz="UTC")  # Labor Day 09-07
    assert pub.loc[pd.Timestamp("2026-08-28")] == pd.Timestamp("2026-08-31 12:00", tz="UTC")  # plain Friday
    assert pub.loc[pd.Timestamp("2026-09-03")] == pd.Timestamp("2026-09-04 12:00", tz="UTC")
    # point-in-time: every value becomes knowable strictly after its observation date
    assert (pub > pd.DatetimeIndex(f.index).tz_localize(UTC)).all()
    assert (pub.dt.hour == 12).all() and (pub.dt.weekday < 5).all()


def test_fetch_series_paginates_with_offset() -> None:
    adapter, client = _api({"DCOILBRENTEU": _payload(BRENT_JSON)}, page_size=15)
    res = adapter.fetch_series("DCOILBRENTEU")

    assert [c["offset"] for c in client.calls] == [0, 15, 30]
    assert all(c["limit"] == 15 for c in client.calls)
    assert res.meta["pages"] == 3 and res.meta["count"] == 42
    assert len(res.frame) == 41 and res.frame.index.is_unique

    full, _ = _api({"DCOILBRENTEU": _payload(BRENT_JSON)})
    pd.testing.assert_frame_equal(res.frame, full.fetch_series("DCOILBRENTEU").frame)


def test_fetch_series_resolves_alias() -> None:
    adapter, client = _api({"DCOILBRENTEU": _payload(BRENT_JSON)})
    res = adapter.fetch_series("brent_spot")
    assert client.calls[0]["series_id"] == "DCOILBRENTEU"
    assert res.meta["series_id"] == "DCOILBRENTEU" and res.meta["alias"] == "brent_spot"
    assert adapter.fetch(series_id="brent_spot").frame.equals(res.frame)


def test_fetch_series_unknown_series_error_envelope() -> None:
    adapter, _ = _api({"DCOILBRENTEU": _payload(BRENT_JSON)})
    with pytest.raises(FredApiError, match="does not exist"):
        adapter.fetch_series("NOPE123XYZ")


@pytest.mark.parametrize("name", ["api_error_400_no_key.json", "api_error_400_unregistered_key.json"])
def test_fetch_series_real_error_envelopes_raise(name: str) -> None:
    body = _payload(name)

    class ErrClient:
        def get_json(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
            return body

    adapter = FredAdapter("test-key", client=ErrClient())  # type: ignore[arg-type]
    with pytest.raises(FredApiError, match="Bad Request") as info:
        adapter.fetch_series("DCOILBRENTEU")
    assert "api_key" in str(info.value) and "test-key" not in str(info.value)


def test_fetch_series_rejects_malformed_payloads() -> None:
    good = _payload(BRENT_JSON)
    cases: list[Any] = [
        "<html>maintenance</html>",
        {"observations": "nope"},
        {"observations": [{"date": "2026-09-29"}]},
        {**good, "observations": []},
        {**good, "observations": [{"date": "2026-09-29", "value": "n/a"}]},
        {**good, "observations": [{"date": "2026-09-29", "value": "."}, {"date": "2026-09-30", "value": "."}]},
        {**good, "observations": [{"date": "09/29/2026", "value": "113.96"}]},
    ]
    for payload in cases:

        class OneClient:
            def __init__(self, p: Any):
                self.p = p

            def get_json(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
                return self.p

        adapter = FredAdapter("test-key", client=OneClient(payload))  # type: ignore[arg-type]
        with pytest.raises(DataUnavailable):
            adapter.fetch_series("DCOILBRENTEU")


def test_fetch_series_keeps_last_duplicate_and_sorts() -> None:
    payload = _payload(BRENT_JSON)
    obs = list(payload["observations"])
    obs.append({**obs[-1], "value": "114.00"})  # a revised repeat of 2026-09-29
    obs.reverse()
    payload = {**payload, "observations": obs}
    adapter, _ = _api({"DCOILBRENTEU": payload})
    f = adapter.fetch_series("DCOILBRENTEU").frame
    assert f.index.is_monotonic_increasing and f.index.is_unique and len(f) == 41
    assert f.loc[pd.Timestamp("2026-09-29"), "value"] == 113.96  # last in ascending order wins


def test_fetch_series_wraps_transport_errors() -> None:
    class DownClient:
        def get_json(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
            raise DataUnavailable(f"GET {url}?api_key={params['api_key']} failed after 4 attempts: timeout")

    adapter = FredAdapter("test-key", client=DownClient())  # type: ignore[arg-type]
    with pytest.raises(DataUnavailable, match="FRED API DCOILBRENTEU") as info:
        adapter.fetch_series("DCOILBRENTEU")
    assert "test-key" not in str(info.value) and "api_key=***" in str(info.value)


# ---------------------------------------------------------------------------------------------------------------
# FredHttpClient (transport policy)
# ---------------------------------------------------------------------------------------------------------------
def _client(replies: list[Any], retries: int = 2) -> tuple[FredHttpClient, _StubSession]:
    client = FredHttpClient(timeout=1.0, retries=retries, backoff=0.0, min_interval=0.0)
    session = _StubSession(replies)
    client.session = session  # type: ignore[assignment]
    return client, session


def test_http_client_does_not_retry_400_and_surfaces_fred_message() -> None:
    body = (FIX / "api_error_400_unregistered_key.json").read_text(encoding="utf-8")
    client, session = _client([_Resp(400, body, "Bad Request")])
    with pytest.raises(FredApiError, match="not registered") as info:
        client.get(API_URL, params={"series_id": "DCOILBRENTEU", "api_key": "secret-key"})
    assert len(session.calls) == 1
    assert "secret-key" not in str(info.value) and "HTTP 400" in str(info.value)


def test_http_client_retries_5xx_then_succeeds() -> None:
    client, session = _client([_Resp(503, "", "Service Unavailable"), _Resp(200, '{"observations": []}')])
    r = client.get(API_URL, params={"series_id": "DFF", "api_key": "k"})
    assert r.status_code == 200 and len(session.calls) == 2


def test_http_client_redacts_key_from_transport_errors() -> None:
    err = requests.ConnectionError(f"HTTPSConnectionPool: {API_URL}?series_id=DFF&api_key=SECRET123 reset")
    client, session = _client([err, err, err], retries=2)
    with pytest.raises(DataUnavailable, match="after 3 attempts") as info:
        client.get(API_URL, params={"series_id": "DFF", "api_key": "SECRET123"})
    assert len(session.calls) == 3
    assert "SECRET123" not in str(info.value) and "api_key=***" in str(info.value)
    assert redact_key("x?api_key=abc&file_type=json 'api_key=def'") == "x?api_key=***&file_type=json 'api_key=***'"


# ---------------------------------------------------------------------------------------------------------------
# fredgraph.csv fallback
# ---------------------------------------------------------------------------------------------------------------
def test_parse_fredgraph_csv_brent_real_download() -> None:
    f = parse_fredgraph_csv((FIX / BRENT_CSV).read_text(encoding="utf-8"), "DCOILBRENTEU")
    assert list(f.columns) == ["value", "published_at"] and f.index.name == "date" and f.index.tz is None
    assert len(f) == 41 and pd.Timestamp("2026-08-31") not in f.index  # empty field -> dropped, not filled
    assert f.index[0] == pd.Timestamp("2026-08-03") and f["value"].iloc[0] == 88.90
    assert f.index[-1] == pd.Timestamp("2026-09-29") and f["value"].iloc[-1] == 113.96
    assert f.loc[pd.Timestamp("2026-09-29"), "published_at"] == pd.Timestamp("2026-09-30 12:00", tz="UTC")
    assert f.loc[pd.Timestamp("2026-09-04"), "published_at"] == pd.Timestamp("2026-09-08 12:00", tz="UTC")
    assert f.attrs["units"] == "USD/bbl" and f.attrs["missing_dropped"] == 1


def test_parse_fredgraph_csv_ovx_real_download() -> None:
    f = parse_fredgraph_csv((FIX / OVX_CSV).read_text(encoding="utf-8"), "OVXCLS")
    assert len(f) == 43 and pd.Timestamp("2026-09-07") not in f.index  # Labor Day: CBOE closed, empty field
    assert f.index[0] == pd.Timestamp("2026-08-03") and f["value"].iloc[0] == 57.20
    assert f.index[-1] == pd.Timestamp("2026-10-01") and f["value"].iloc[-1] == 51.69
    assert f["published_at"].iloc[-1] == pd.Timestamp("2026-10-02 12:00", tz="UTC")
    assert f.loc[pd.Timestamp("2026-09-04"), "value"] == 44.96
    assert f["value"].between(10, 150).all()
    assert f.attrs["units"].startswith("index")


def test_parse_fredgraph_csv_legacy_header_dot_marker_and_bom() -> None:
    text = "﻿DATE,DGS10\r\n2026-09-28,4.14\r\n2026-09-29,.\r\n2026-09-30,4.10\r\n\r\n"
    f = parse_fredgraph_csv(text, "dgs10")
    assert list(f.index) == [pd.Timestamp("2026-09-28"), pd.Timestamp("2026-09-30")]
    assert list(f["value"]) == [4.14, 4.10] and f.attrs["missing_dropped"] == 1


def test_parse_fredgraph_csv_rejects_html_wrong_series_and_garbage() -> None:
    with pytest.raises(DataUnavailable, match="unexpected header"):
        parse_fredgraph_csv("<!DOCTYPE html>\n<html><title>Error - St. Louis Fed</title></html>", "DCOILBRENTEU")
    with pytest.raises(DataUnavailable, match="names series"):
        parse_fredgraph_csv((FIX / OVX_CSV).read_text(encoding="utf-8"), "DCOILBRENTEU")
    with pytest.raises(DataUnavailable, match="non-numeric"):
        parse_fredgraph_csv("observation_date,DFF\n2026-09-29,n/a\n", "DFF")
    with pytest.raises(DataUnavailable, match="no observations"):
        parse_fredgraph_csv("observation_date,DFF\n", "DFF")
    with pytest.raises(DataUnavailable):
        parse_fredgraph_csv("", "DFF")


def test_fred_csv_adapter_via_fake_client() -> None:
    client = FakeBytesClient((FIX / BRENT_CSV).read_bytes())
    res = FredCsvAdapter(client=client).fetch_series("brent_spot")  # type: ignore[arg-type]
    assert client.calls == [(CSV_URL, {"id": "DCOILBRENTEU"})]
    assert res.source == "fred_csv" and res.approx is False
    assert res.meta["url"] == f"{CSV_URL}?id=DCOILBRENTEU" and res.meta["alias"] == "brent_spot"
    assert res.meta["rows"] == 41 and res.meta["missing_dropped"] == 1 and res.meta["units"] == "USD/bbl"
    assert res.frame["value"].iloc[-1] == 113.96


def test_fred_csv_adapter_wraps_transport_failure() -> None:
    class DownClient:
        def get_bytes(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> bytes:
            raise DataUnavailable(f"GET {url} failed after 3 attempts: Read timed out")

    with pytest.raises(DataUnavailable, match="fredgraph.csv DCOILBRENTEU"):
        FredCsvAdapter(client=DownClient()).fetch_series("DCOILBRENTEU")  # type: ignore[arg-type]


def test_api_and_csv_fallback_agree() -> None:
    api, _ = _api({"DCOILBRENTEU": _payload(BRENT_JSON)})
    via_api = api.fetch_series("DCOILBRENTEU").frame
    via_csv = FredCsvAdapter(client=FakeBytesClient((FIX / BRENT_CSV).read_bytes())).fetch_series("DCOILBRENTEU").frame  # type: ignore[arg-type]
    pd.testing.assert_frame_equal(via_api, via_csv)


# ---------------------------------------------------------------------------------------------------------------
# network (opt-in)
# ---------------------------------------------------------------------------------------------------------------
def _network_selected(request: pytest.FixtureRequest) -> None:
    """Network tests run only when explicitly selected (``pytest -m network``): the default suite stays offline."""
    if "network" not in str(request.config.getoption("markexpr") or ""):
        pytest.skip("network tests run only with -m network")


@pytest.mark.network
def test_network_fred_api_brent_and_ovx(request: pytest.FixtureRequest) -> None:
    _network_selected(request)
    key = os.environ.get("FRED_API_KEY")
    if not key:
        pytest.skip("FRED_API_KEY not set")
    adapter = FredAdapter(key)
    brent = adapter.fetch_series("brent_spot")
    f = brent.frame
    assert f.index[0] == pd.Timestamp("1987-05-20") and f["value"].iloc[0] == 18.63
    assert f.loc[pd.Timestamp("2026-09-29"), "value"] == 113.96
    assert len(f) > 9000 and brent.meta["pages"] == 1
    ovx = adapter.fetch_series("OVXCLS").frame
    assert ovx.index[0] == pd.Timestamp("2007-05-10") and ovx["value"].iloc[0] == 27.09
    assert ovx.loc[pd.Timestamp("2026-10-01"), "value"] == 51.69


@pytest.mark.network
def test_network_fred_api_rejects_bad_key_without_retry(request: pytest.FixtureRequest) -> None:
    _network_selected(request)
    with pytest.raises(FredApiError, match="not registered") as info:
        FredAdapter("abcdefabcdefabcdefabcdefabcdefab").fetch_series("DFF")
    assert "abcdefabcdefabcdefabcdefabcdefab" not in str(info.value)


@pytest.mark.network
@pytest.mark.xfail(
    raises=DataUnavailable,
    strict=False,
    reason="fredgraph.csv is intermittent from shared-egress containers (HTTP/2 INTERNAL_ERROR / timeouts seen on "
    "2026-10-05; worked with HTTP/1.1 in 0.2-0.9 s). A transport failure is a known, tolerated outcome here; the "
    "engine falls back to Yahoo ^OVX/^VIX/DX-Y.NYB and EIA spot when it happens.",
)
def test_network_fredgraph_csv_brent_and_ovx(request: pytest.FixtureRequest) -> None:
    _network_selected(request)
    adapter = FredCsvAdapter()
    brent = adapter.fetch_series("DCOILBRENTEU").frame
    assert brent.index[0] == pd.Timestamp("1987-05-20") and brent["value"].iloc[0] == 18.63
    assert brent.loc[pd.Timestamp("2026-09-29"), "value"] == 113.96 and len(brent) > 9000
    assert brent.loc[pd.Timestamp("2026-09-29"), "published_at"] == pd.Timestamp("2026-09-30 12:00", tz="UTC")
    ovx = adapter.fetch_series("ovx").frame
    assert ovx.index[0] == pd.Timestamp("2007-05-10") and ovx["value"].iloc[0] == 27.09
    assert ovx.loc[pd.Timestamp("2026-10-01"), "value"] == 51.69 and len(ovx) > 4800
