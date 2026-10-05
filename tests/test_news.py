"""engine.data.adapters.news: offline tests on the real snapshots in tests/fixtures/news (captured 2026-10-05).

The GDELT export file and ``lastupdate.txt`` are byte-for-byte real; the GPR workbook holds real values
(trimmed, re-encoded as .xlsx); the DOC API timeline is a hand-written shape because that endpoint answers 429
from this container (the real 429 body is a fixture too). Network tests are opt-in (``pytest -m network``).
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from engine.core.errors import DataUnavailable
from engine.data.adapters.news import (
    CONFLICT_ROOT_CODES,
    GDELT_COLUMNS,
    GDELT_EXPORT_FIELDS,
    GULF_GEO_CODES,
    OIL_ACTOR_CODES,
    POS_ACTION_GEO_COUNTRY,
    POS_ACTOR1_COUNTRY,
    POS_ACTOR2_COUNTRY,
    POS_AVG_TONE,
    POS_DATEADDED,
    POS_EVENT_ROOT,
    POS_GOLDSTEIN,
    POS_NUM_ARTICLES,
    POS_SOURCEURL,
    GdeltDocApiAdapter,
    GdeltFilesAdapter,
    GprAdapter,
    aggregate_export_text,
    aggregate_rows,
    day_slots,
    export_url,
    gpr_published_at,
    parse_doc_timeline,
    parse_gpr_workbook,
    parse_lastupdate,
    row_matches,
    slot_timestamp_from_url,
)

FIX = Path(__file__).parent / "fixtures" / "news"
EXPORT_ZIP = FIX / "20261005160000.export.CSV.zip"
LASTUPDATE = FIX / "gdelt_lastupdate_20261005T1600.txt"
GPR_XLSX = FIX / "gpr_daily_recent_trimmed.xlsx"
DOC_JSON = FIX / "gdelt_doc_timelinevol_HANDWRITTEN.json"
DOC_429 = FIX / "gdelt_doc_api_429.txt"
SLOT = datetime(2026, 10, 5, 16, 0, tzinfo=UTC)


def export_text() -> str:
    with zipfile.ZipFile(EXPORT_ZIP) as z:
        return z.read(z.namelist()[0]).decode("utf-8", errors="replace")


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.content = body
        self.status_code = status

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)


class FakeClient:
    """Serves bytes (or raises a stored exception) per URL, like HttpClient would."""

    def __init__(self, replies: dict[str, bytes | Exception]):
        self.replies = replies
        self.urls: list[str] = []

    def _reply(self, url: str) -> bytes:
        self.urls.append(url)
        reply = self.replies.get(url)
        if reply is None:
            raise DataUnavailable(f"GET {url} -> 404 (not in fixture set)")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def get_bytes(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> bytes:
        return self._reply(url)

    def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> FakeResponse:
        return FakeResponse(self._reply(url))


# ---------------------------------------------------------------------------------------------------- GDELT files
def test_real_export_file_has_61_tab_separated_fields():
    rows = [line.split("\t") for line in export_text().splitlines() if line]
    assert len(rows) == 963
    assert {len(r) for r in rows} == {GDELT_EXPORT_FIELDS}
    # the verified positions: every row of this file is stamped with the slot and carries a source URL
    assert {r[POS_DATEADDED] for r in rows} == {"20261005160000"}
    assert all(r[POS_SOURCEURL].startswith("http") for r in rows)
    # actor codes are 3-letter CAMEO, ActionGeo is 2-letter FIPS
    actors = {r[POS_ACTOR1_COUNTRY] for r in rows} | {r[POS_ACTOR2_COUNTRY] for r in rows}
    assert {len(a) for a in actors if a} == {3}
    assert {len(g) for g in {r[POS_ACTION_GEO_COUNTRY] for r in rows} if g} == {2}
    assert "USA" in actors and "SAU" in actors
    # numeric columns parse
    assert all(np.isfinite(float(r[POS_NUM_ARTICLES])) for r in rows)
    assert all(-11 <= float(r[POS_GOLDSTEIN]) <= 11 for r in rows)
    assert all(-101 <= float(r[POS_AVG_TONE]) <= 101 for r in rows)
    assert {r[POS_EVENT_ROOT] for r in rows} <= {f"{i:02d}" for i in range(1, 21)}


def test_row_matches_rules():
    fields = [""] * GDELT_EXPORT_FIELDS
    fields[POS_SOURCEURL] = "https://example.com/politics"
    assert not row_matches(fields)
    # rule 1: an oil actor AND a Gulf geography
    fields[POS_ACTOR1_COUNTRY] = "IRN"
    assert not row_matches(fields)  # actor alone is not enough
    fields[POS_ACTION_GEO_COUNTRY] = "IR"
    assert row_matches(fields)
    # rule 2: the keyword alone is enough, wherever the event happened
    fields2 = [""] * GDELT_EXPORT_FIELDS
    fields2[POS_SOURCEURL] = "https://example.com/2026/strait-of-HORMUZ-closure"
    assert row_matches(fields2)
    fields2[POS_SOURCEURL] = "https://example.com/2026/tanker-rates"
    assert row_matches(fields2)
    assert "IRN" in OIL_ACTOR_CODES and "IR" in GULF_GEO_CODES
    assert {f"{i:02d}" for i in range(13, 21)} == CONFLICT_ROOT_CODES


def test_aggregate_export_text_on_the_real_file():
    text = export_text()
    agg = aggregate_export_text(text, SLOT)
    rows = [line.split("\t") for line in text.splitlines() if line]
    matched = [r for r in rows if row_matches(r)]
    assert agg["gdelt_total_events"] == float(len(rows)) == 963.0
    assert agg["gdelt_matched_events"] == float(len(matched)) > 0
    # volume is the sum of NumArticles over the matching events, recomputed independently here
    assert agg["gdelt_volume"] == pytest.approx(sum(float(r[POS_NUM_ARTICLES]) for r in matched))
    weight = sum(float(r[POS_NUM_ARTICLES]) for r in matched)
    tone = sum(float(r[POS_NUM_ARTICLES]) * float(r[POS_AVG_TONE]) for r in matched) / weight
    assert agg["gdelt_tone"] == pytest.approx(tone)
    gold = sum(float(r[POS_NUM_ARTICLES]) * float(r[POS_GOLDSTEIN]) for r in matched) / weight
    assert agg["gdelt_goldstein"] == pytest.approx(gold)
    share = sum(1 for r in matched if r[POS_EVENT_ROOT] in CONFLICT_ROOT_CODES) / len(matched)
    assert agg["gdelt_conflict_share"] == pytest.approx(share)
    assert 0.0 <= agg["gdelt_conflict_share"] <= 1.0


def test_aggregate_export_text_skips_malformed_rows():
    agg = aggregate_export_text("a\tb\tc\n", SLOT)
    assert agg["gdelt_total_events"] == 0.0
    assert np.isnan(agg["gdelt_tone"]) and np.isnan(agg["gdelt_conflict_share"])


def test_parse_lastupdate_rewrites_http_to_https():
    urls = parse_lastupdate(LASTUPDATE.read_text())
    assert set(urls) == {"export", "mentions", "gkg"}
    assert urls["export"] == "https://data.gdeltproject.org/gdeltv2/20261005160000.export.CSV.zip"
    assert all(u.startswith("https://") for u in urls.values())
    assert slot_timestamp_from_url(urls["export"]) == SLOT
    with pytest.raises(DataUnavailable):
        parse_lastupdate("garbage")


def test_fetch_latest_uses_lastupdate_then_the_export_file():
    url = "https://data.gdeltproject.org/gdeltv2/20261005160000.export.CSV.zip"
    client = FakeClient(
        {
            "https://data.gdeltproject.org/gdeltv2/lastupdate.txt": LASTUPDATE.read_bytes(),
            url: EXPORT_ZIP.read_bytes(),
        }
    )
    res = GdeltFilesAdapter(client=client).fetch_latest()  # type: ignore[arg-type]
    assert res.source == "gdelt_files"
    assert list(res.frame.columns) == [*GDELT_COLUMNS, "published_at"]
    assert len(res.frame) == 1
    assert res.frame.index[0] == pd.Timestamp(SLOT)
    # published_at = slot + 15 minutes
    assert res.frame["published_at"].iloc[0] == pd.Timestamp("2026-10-05 16:15:00+00:00")
    assert res.frame["gdelt_total_events"].iloc[0] == 963.0
    assert res.meta["slot"] == "2026-10-05T16:00:00Z"


def test_fetch_day_tolerates_missing_slots():
    day = date(2026, 10, 5)
    slots = day_slots(day)
    assert len(slots) == 96 and slots[0].hour == 0 and slots[-1].strftime("%H%M") == "2345"
    replies: dict[str, bytes | Exception] = {export_url(slots[64]): EXPORT_ZIP.read_bytes()}
    replies[export_url(slots[65])] = b"not a zip"
    client = FakeClient(replies)
    res = GdeltFilesAdapter(client=client).fetch_day(day)  # type: ignore[arg-type]
    assert len(res.frame) == 1  # one readable slot, 95 missing/unreadable
    assert res.meta["slots_fetched"] == 1 and len(res.meta["slots_missing"]) == 95
    assert len(client.urls) == 96  # every slot was attempted, with pacing


def test_fetch_day_raises_when_nothing_is_readable():
    with pytest.raises(DataUnavailable, match="no readable export slot"):
        GdeltFilesAdapter(client=FakeClient({})).fetch_day(date(2026, 10, 4))  # type: ignore[arg-type]


def test_aggregate_rows_weights_by_volume_and_counts():
    idx = pd.DatetimeIndex(
        [
            "2026-10-04 23:45:00+00:00",
            "2026-10-05 00:00:00+00:00",
            "2026-10-05 00:15:00+00:00",
        ]
    )
    frame = pd.DataFrame(
        {
            "gdelt_volume": [10.0, 30.0, 10.0],
            "gdelt_tone": [-2.0, -4.0, 0.0],
            "gdelt_goldstein": [-5.0, -1.0, 3.0],
            "gdelt_conflict_share": [0.5, 0.25, 0.75],
            "gdelt_matched_events": [4.0, 8.0, 4.0],
            "gdelt_total_events": [100.0, 900.0, 800.0],
        },
        index=idx,
    )
    daily = aggregate_rows(frame)
    assert list(daily.index.strftime("%Y-%m-%d")) == ["2026-10-04", "2026-10-05"]
    assert daily.loc["2026-10-05", "gdelt_volume"] == 40.0
    assert daily.loc["2026-10-05", "gdelt_tone"] == pytest.approx((30 * -4 + 10 * 0) / 40)
    assert daily.loc["2026-10-05", "gdelt_goldstein"] == pytest.approx((30 * -1 + 10 * 3) / 40)
    assert daily.loc["2026-10-05", "gdelt_conflict_share"] == pytest.approx((8 * 0.25 + 4 * 0.75) / 12)
    assert daily.loc["2026-10-05", "gdelt_total_events"] == 1700.0
    # published_at = midnight UTC of the next day
    assert daily.loc["2026-10-05", "published_at"] == pd.Timestamp("2026-10-06 00:00:00+00:00")
    assert aggregate_rows(pd.DataFrame()).empty


def test_aggregate_rows_keeps_nan_when_nothing_matched():
    idx = pd.DatetimeIndex(["2026-10-05 00:00:00+00:00"])
    frame = pd.DataFrame(
        {
            "gdelt_volume": [0.0],
            "gdelt_tone": [np.nan],
            "gdelt_goldstein": [np.nan],
            "gdelt_conflict_share": [np.nan],
            "gdelt_matched_events": [0.0],
            "gdelt_total_events": [850.0],
        },
        index=idx,
    )
    daily = aggregate_rows(frame)
    assert np.isnan(daily["gdelt_tone"].iloc[0])
    assert np.isnan(daily["gdelt_conflict_share"].iloc[0])
    assert daily["gdelt_total_events"].iloc[0] == 850.0


def test_real_slot_roundtrip_through_the_daily_aggregate():
    """One real slot: the daily row must repeat it exactly (single-slot day)."""
    agg = aggregate_export_text(export_text(), SLOT)
    slot_frame = pd.DataFrame([agg], index=pd.DatetimeIndex([SLOT]))
    daily = aggregate_rows(slot_frame)
    assert len(daily) == 1
    for col in ("gdelt_volume", "gdelt_tone", "gdelt_goldstein", "gdelt_conflict_share"):
        assert daily[col].iloc[0] == pytest.approx(agg[col])


# ------------------------------------------------------------------------------------------------------------ GPR
def test_parse_gpr_workbook_real_values():
    frame = parse_gpr_workbook(GPR_XLSX.read_bytes())
    assert list(frame.columns)[:4] == ["gpr", "gpr_act", "gpr_threat", "n10d"]
    assert "published_at" in frame.columns
    assert frame.index.name == "date" and frame.index.tz is None
    assert frame.index.is_monotonic_increasing
    # real values captured on 2026-10-05
    assert frame.loc["2026-10-01", "gpr"] == pytest.approx(176.278549, abs=1e-5)
    assert frame.loc["2026-10-05", "gpr"] == pytest.approx(141.491577, abs=1e-5)
    assert frame.loc["2026-09-29", "gpr_act"] == pytest.approx(227.749741, abs=1e-5)
    assert frame.loc["2026-10-01", "n10d"] == 451
    assert frame.index[0] == pd.Timestamp("1985-01-01")
    # published_at = date + 2 days 12:00 UTC
    assert frame.loc["2026-10-01", "published_at"] == pd.Timestamp("2026-10-03 12:00:00+00:00")


def test_gpr_published_at_rule():
    pub = gpr_published_at(pd.DatetimeIndex(["2026-10-01", "2026-10-02"]))
    assert list(pub) == [pd.Timestamp("2026-10-03 12:00+00:00"), pd.Timestamp("2026-10-04 12:00+00:00")]


def test_gpr_adapter_fetch_and_failures():
    url = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
    res = GprAdapter(client=FakeClient({url: GPR_XLSX.read_bytes()})).fetch()  # type: ignore[arg-type]
    assert res.source == "gpr" and not res.approx
    assert res.meta["last_day"] == "2026-10-05"
    assert res.meta["published_at_rule"] == "date + 2 days 12:00 UTC"
    with pytest.raises(DataUnavailable, match="cannot read"):
        parse_gpr_workbook(b"<html>503</html>")


def test_gpr_rejects_a_workbook_without_the_expected_columns():
    bio = io.BytesIO()
    pd.DataFrame({"A": [1], "B": [2]}).to_excel(bio, index=False)  # a valid workbook with the wrong columns
    with pytest.raises(DataUnavailable, match="unexpected columns"):
        parse_gpr_workbook(bio.getvalue())


# -------------------------------------------------------------------------------------------------- GDELT DOC API
def test_parse_doc_timeline_handwritten_shape():
    payload = json.loads(DOC_JSON.read_text())
    frame = parse_doc_timeline(payload, "timelinevol")
    assert list(frame.columns) == ["volume_intensity", "average_tone", "published_at"]
    assert len(frame) == 3
    assert frame["volume_intensity"].iloc[1] == pytest.approx(0.0456)
    assert np.isnan(frame["average_tone"].iloc[2])  # null stays NaN, never filled
    assert frame.index[0] == pd.Timestamp("2026-07-01 00:00:00+00:00")
    assert frame["published_at"].iloc[0] == pd.Timestamp("2026-07-02 00:00:00+00:00")
    with pytest.raises(DataUnavailable, match="no 'timeline'"):
        parse_doc_timeline({"x": 1}, "timelinevol")
    with pytest.raises(DataUnavailable, match="no usable point"):
        parse_doc_timeline({"timeline": [{"series": "x", "data": []}]}, "timelinevol")


def test_doc_api_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="unsupported GDELT DOC mode"):
        GdeltDocApiAdapter().fetch_timeline("oil", mode="nonsense")


def test_doc_api_429_body_is_not_json_and_raises_once():
    class Rate429Client:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> FakeResponse:
            self.calls += 1
            return FakeResponse(DOC_429.read_bytes(), 429)

    client = Rate429Client()
    adapter = GdeltDocApiAdapter(client=client, min_interval=0.0)  # type: ignore[arg-type]
    with pytest.raises(DataUnavailable, match="non-JSON body"):
        adapter.fetch_timeline("oil hormuz")
    assert client.calls == 1  # the plain-text 429 is detected on the first call, no pointless retry


def test_doc_api_retries_a_transport_failure_at_most_once():
    class FailingClient:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> FakeResponse:
            self.calls += 1
            raise DataUnavailable("HTTP 429")

    client = FailingClient()
    adapter = GdeltDocApiAdapter(client=client, min_interval=0.0)  # type: ignore[arg-type]
    with pytest.raises(DataUnavailable):
        adapter.fetch_timeline("oil hormuz")
    assert client.calls == 2


# ------------------------------------------------------------------------------------------------- network (opt-in)
@pytest.fixture(scope="module")
def live(request: pytest.FixtureRequest) -> None:
    """Skip the network tests in the default offline run (OPT_OFFLINE=1 without ``-m network``)."""
    selected = "network" in str(request.config.getoption("-m") or "")
    if os.environ.get("OPT_OFFLINE") == "1" and not selected:
        pytest.skip("network tests are opt-in: run `pytest -m network`")


@pytest.mark.network
def test_network_gpr_matches_the_fixture_shape(live: None):
    res = GprAdapter().fetch()
    assert len(res.frame) > 15_000
    assert res.frame.index[0] == pd.Timestamp("1985-01-01")
    assert res.frame["gpr"].notna().all()


@pytest.mark.network
def test_network_gdelt_latest_slot(live: None):
    res = GdeltFilesAdapter().fetch_latest()
    assert len(res.frame) == 1
    assert res.frame["gdelt_total_events"].iloc[0] > 0
    slot = pd.Timestamp(res.frame.index[0])
    assert slot <= pd.Timestamp.now(tz=UTC)


@pytest.mark.network
@pytest.mark.xfail(reason="the GDELT DOC API answers 429 from shared-egress IPs", raises=DataUnavailable)
def test_network_gdelt_doc_timeline(live: None):
    res = GdeltDocApiAdapter().fetch_timeline("(oil OR hormuz) sourcelang:english", timespan="1week")
    assert not res.frame.empty
