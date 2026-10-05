"""FRED adapters: the official JSON API (needs a free ``FRED_API_KEY``) and the key-less ``fredgraph.csv`` fallback.

Both endpoints were exercised from the development container on 2026-10-05 (captures and real values in
``tests/fixtures/fred/README.md``):

* ``https://api.stlouisfed.org/fred/series/observations`` with ``series_id``, ``api_key``, ``file_type=json``,
  ``observation_start=1980-01-01``, ``sort_order=asc``, ``limit`` (hard cap 100 000) and ``offset``. The reply
  carries ``count`` (rows matching the request), ``offset``, ``limit`` and ``observations`` = list of
  ``{"realtime_start", "realtime_end", "date", "value"}``; a missing observation has ``"value": "."``. Series longer
  than ``limit`` are paginated with ``offset`` until ``count`` rows have been received. Without a valid key the API
  answers HTTP 400 ``{"error_code": 400, "error_message": "Bad Request. ..."}``: deterministic, so it is raised at
  once (no retries) and the key is never echoed in messages or logs (:class:`FredHttpClient`).
* ``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>``: two columns, header ``observation_date,<SERIES>``
  (older downloads: ``DATE,<SERIES>``), ISO dates and an EMPTY second field for a missing observation (the
  2026-10-05 download of DCOILBRENTEU has 1 173 such rows, e.g. ``2026-08-31,``; older files used ``.``). An
  unknown id answers HTTP 404 with an HTML error page. The endpoint is intermittent from shared-egress containers
  (HTTP/2 ``INTERNAL_ERROR`` / timeouts with curl's default HTTP/2; ``curl --http1.1`` and the ``requests`` fetch
  below worked in 0.2-0.9 s). ``requests``/``urllib3`` speak HTTP/1.1 only, so the fallback already behaves like
  ``--http1.1``; the timeout stays at 30 s with 2 retries and any failure raises :class:`DataUnavailable`.

Missing observations are DROPPED, never filled or interpolated. Values are the latest vintage FRED serves (no ALFRED
revision history).

``published_at`` (UTC, the earliest moment the datum was knowable) = the next US business day after the observation
date at 12:00 UTC, via :func:`engine.core.calendar.add_business_days` with the US calendar (a Friday maps to Monday,
a Friday before Labor Day to Tuesday; weekend observations of the 7-day series DFF map to Monday). This is the
project-wide rule for FRED series. It matches the CBOE/Treasury/Fed series (on Monday 2026-10-05 07:00 UTC the last
OVXCLS and DFF observations were Thursday 2026-10-01; Friday's were not yet posted) and is optimistic for the
EIA-sourced spot prices (DCOILBRENTEU's last observation on 2026-10-05 was 2026-09-29: EIA refreshes the daily spot
series weekly). When EIA and FRED spot prices are merged, the EIA adapter's next-Wednesday rule is the stricter one.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd
import requests

from engine.core.calendar import add_business_days
from engine.core.errors import DataUnavailable
from engine.data.base import FetchResult, HttpClient, utc_now

log = logging.getLogger(__name__)

API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
OBSERVATION_START = date(1980, 1, 1)
PAGE_SIZE = 100_000  # FRED's hard cap for ``limit``
MAX_PAGES = 20  # safety stop for the offset loop (longest series we use from 1980: DFF, ~17k rows = one page)
PUBLISH_HOUR_UTC = 12
PUBLISHED_AT_RULE = "next US business day 12:00 UTC"
MISSING_MARKERS = frozenset({".", ""})  # API: "."; fredgraph.csv: empty field (older downloads: ".")
CSV_DATE_HEADERS = frozenset({"observation_date", "date"})

# alias used by the engine -> FRED series id (all daily)
SERIES: dict[str, str] = {
    "brent_spot": "DCOILBRENTEU",  # Brent Europe spot FOB, USD/bbl (EIA), 1987-05-20 ->
    "wti_spot": "DCOILWTICO",  # WTI Cushing spot FOB, USD/bbl (EIA), 1986-01-02 ->
    "ovx": "OVXCLS",  # CBOE crude oil volatility index, 2007-05-10 ->
    "vix": "VIXCLS",  # CBOE VIX, 1990-01-02 ->
    "dxy": "DTWEXBGS",  # nominal broad US dollar index, Jan 2006 = 100, 2006-01-02 ->
    "us10y": "DGS10",  # 10-year Treasury constant maturity, percent
    "breakeven10y": "T10YIE",  # 10-year breakeven inflation, percent, 2003-01-02 ->
    "fedfunds": "DFF",  # effective federal funds rate, percent, 7-day series
}
SERIES_ALIAS: dict[str, str] = {sid: alias for alias, sid in SERIES.items()}
UNITS: dict[str, str] = {
    "DCOILBRENTEU": "USD/bbl",
    "DCOILWTICO": "USD/bbl",
    "OVXCLS": "index points (annualised implied vol, %)",
    "VIXCLS": "index points (annualised implied vol, %)",
    "DTWEXBGS": "index (Jan 2006 = 100)",
    "DGS10": "percent",
    "T10YIE": "percent",
    "DFF": "percent",
}

_API_KEY_RE = re.compile(r"api_key=[^&\s'\"]+")


class FredApiError(DataUnavailable):
    """A deterministic 4xx answer from the FRED API (bad key, unknown series, bad parameter): never retried."""


def redact_key(text: str) -> str:
    """Hide ``api_key=...`` wherever a URL with the query string leaks into a message."""
    return _API_KEY_RE.sub("api_key=***", text)


def resolve_series(series: str) -> str:
    """Engine alias (``"brent_spot"``) or FRED id (``"DCOILBRENTEU"``, case-insensitive) -> FRED series id."""
    key = series.strip()
    if not key:
        raise ValueError("empty FRED series id")
    return SERIES.get(key.lower(), key.upper())


# --------------------------------------------------------------------------------------------------------------
# published_at rule (pure function, unit-tested)
# --------------------------------------------------------------------------------------------------------------
def fred_published_at(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Next US business day after each (tz-naive) observation date, 12:00 UTC."""
    if len(dates) == 0:
        return pd.DatetimeIndex([], tz=UTC, name="published_at")
    naive = dates.tz_convert(None) if dates.tz is not None else dates
    days = [ts.date() for ts in naive]
    next_bd = {d: add_business_days(d, 1, "US") for d in set(days)}
    stamps = [datetime(nd.year, nd.month, nd.day, PUBLISH_HOUR_UTC, tzinfo=UTC) for nd in (next_bd[d] for d in days)]
    return pd.DatetimeIndex(stamps, name="published_at")


# --------------------------------------------------------------------------------------------------------------
# parsing (shared by the API and the CSV fallback)
# --------------------------------------------------------------------------------------------------------------
def rows_to_frame(rows: list[tuple[str, str]], series_id: str, what: str) -> tuple[pd.DataFrame, int]:
    """``(date, value)`` text pairs -> frame indexed by tz-naive date with ``value`` (float) and ``published_at``.

    Missing markers (``"."``, empty) are dropped and counted; any other non-numeric value or unparseable date is a
    format change and raises :class:`DataUnavailable`. Returns ``(frame, n_missing_dropped)``.
    """
    if not rows:
        raise DataUnavailable(f"{what}: no observations returned")
    dates: list[str] = []
    values: list[float] = []
    n_missing = 0
    for d, v in rows:
        text = v.strip()
        if text in MISSING_MARKERS:
            n_missing += 1
            continue
        try:
            values.append(float(text))
        except ValueError as e:
            raise DataUnavailable(f"{what}: non-numeric value {text!r} on {d!r} (format change?)") from e
        dates.append(d.strip())
    if not values:
        raise DataUnavailable(f"{what}: all {n_missing} observations are missing")
    parsed = pd.to_datetime(pd.Index(dates), format="%Y-%m-%d", errors="coerce")
    bad = np.asarray(parsed.isna())
    if bad.any():
        raise DataUnavailable(f"{what}: unparseable observation date {dates[int(np.argmax(bad))]!r}")
    frame = pd.DataFrame({"value": np.asarray(values, dtype=float)}, index=pd.DatetimeIndex(parsed, name="date"))
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame["published_at"] = fred_published_at(pd.DatetimeIndex(frame.index))
    frame.attrs["series_id"] = series_id
    frame.attrs["missing_dropped"] = n_missing
    if series_id in UNITS:
        frame.attrs["units"] = UNITS[series_id]
    return frame, n_missing


def parse_observations_page(payload: Any, series_id: str) -> tuple[list[tuple[str, str]], int]:
    """One ``series/observations`` JSON reply -> ``([(date, value), ...], count)``; error envelopes raise."""
    if not isinstance(payload, dict):
        raise DataUnavailable(f"FRED API {series_id}: unexpected payload {str(payload)[:200]!r}")
    if "error_code" in payload or "error_message" in payload:
        raise FredApiError(
            f"FRED API {series_id}: {payload.get('error_code')}: {redact_key(str(payload.get('error_message')))}"
        )
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise DataUnavailable(f"FRED API {series_id}: reply has no 'observations' list")
    rows: list[tuple[str, str]] = []
    for obs in observations:
        if not isinstance(obs, dict) or "date" not in obs or "value" not in obs:
            raise DataUnavailable(f"FRED API {series_id}: malformed observation {str(obs)[:100]!r}")
        rows.append((str(obs["date"]), str(obs["value"])))
    try:
        count = int(payload.get("count", len(rows)))
    except (TypeError, ValueError):
        count = len(rows)
    return rows, count


def parse_fredgraph_csv(text: str, series_id: str) -> pd.DataFrame:
    """``fredgraph.csv`` text -> frame (same shape as the API). Header must name ``series_id``; HTML pages fail."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    what = f"fredgraph.csv {series_id}"
    if header is None or len(header) < 2 or header[0].strip().lower() not in CSV_DATE_HEADERS:
        shown = text[:80].replace("\n", " ")
        raise DataUnavailable(f"{what}: unexpected header {shown!r} (HTML error page or format change)")
    if header[1].strip().upper() != series_id.upper():
        raise DataUnavailable(f"{what}: header names series {header[1]!r}")
    rows = [(row[0], row[1] if len(row) > 1 else "") for row in reader if row and row[0].strip()]
    frame, _ = rows_to_frame(rows, series_id, what)
    return frame


# --------------------------------------------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------------------------------------------
class FredHttpClient(HttpClient):
    """:class:`HttpClient` for the FRED API: 4xx answers other than 429 are deterministic (bad key, unknown series)
    and raised at once as :class:`FredApiError` carrying FRED's ``error_message``; 429/5xx/network errors are
    retried with backoff as usual; the api_key never appears in messages or logs."""

    def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_call = time.monotonic()
                r = self.session.get(url, params=params, timeout=self.timeout, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    raise DataUnavailable(f"{url} -> HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise FredApiError(redact_key(f"{url} -> HTTP {r.status_code}: {_fred_error_message(r)}"))
                return r
            except FredApiError:
                raise
            except (requests.RequestException, DataUnavailable) as e:
                last_exc = e
                sleep = self.backoff ** (attempt + 1)
                log.warning("GET %s failed (%s); retry in %.1fs", url, redact_key(str(e)), sleep)
                if attempt < self.retries:
                    time.sleep(sleep)
        raise DataUnavailable(redact_key(f"GET {url} failed after {self.retries + 1} attempts: {last_exc}"))


def _fred_error_message(r: requests.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:200].strip() or str(r.reason)
    if isinstance(body, dict) and body.get("error_message"):
        return str(body["error_message"])
    return str(body)[:200]


# --------------------------------------------------------------------------------------------------------------
# adapters
# --------------------------------------------------------------------------------------------------------------
class FredAdapter:
    """FRED JSON API. Every fetch raises :class:`DataUnavailable` immediately when no key is configured."""

    name = "fred"

    def __init__(
        self,
        api_key: str | None,
        client: HttpClient | None = None,
        base_url: str = API_URL,
        page_size: int = PAGE_SIZE,
        observation_start: date = OBSERVATION_START,
    ):
        self.api_key = api_key or None
        self.client = client or FredHttpClient(timeout=30.0, retries=3, backoff=1.5, min_interval=0.5)
        self.base_url = base_url
        self.page_size = max(1, min(int(page_size), PAGE_SIZE))
        self.observation_start = observation_start

    def fetch(self, **kwargs: Any) -> FetchResult:
        """Adapter protocol entry point: ``fetch(series_id=...)``."""
        return self.fetch_series(str(kwargs["series_id"]))

    def _require_key(self) -> str:
        if not self.api_key:
            raise DataUnavailable(
                "FRED API: no API key configured (set FRED_API_KEY; free at fredaccount.stlouisfed.org/apikeys)"
            )
        return self.api_key

    def fetch_series(self, series_id: str) -> FetchResult:
        """Daily observations of ``series_id`` (FRED id or ``SERIES`` alias) from ``observation_start``.

        Index ``date`` (tz-naive); columns ``value`` (float) and ``published_at`` (UTC, next US business day 12:00).
        Pages of ``limit`` rows are requested with increasing ``offset`` until ``count`` rows have been received.
        Observations whose value is ``"."`` are dropped, never filled.
        """
        key = self._require_key()
        sid = resolve_series(series_id)
        params: dict[str, Any] = {
            "series_id": sid,
            "api_key": key,
            "file_type": "json",
            "observation_start": self.observation_start.isoformat(),
            "sort_order": "asc",
            "limit": self.page_size,
            "offset": 0,
        }
        rows: list[tuple[str, str]] = []
        count: int | None = None
        offset = 0
        pages = 0
        while pages < MAX_PAGES:
            params["offset"] = offset
            try:
                payload = self.client.get_json(self.base_url, params=params)
            except DataUnavailable as e:
                raise type(e)(f"FRED API {sid}: {redact_key(str(e))}") from e
            except ValueError as e:  # non-JSON body (requests.JSONDecodeError is a ValueError)
                raise DataUnavailable(f"FRED API {sid}: reply is not JSON") from e
            pages += 1
            page, page_count = parse_observations_page(payload, sid)
            if count is None:
                count = page_count
            rows.extend(page)
            offset += len(page)
            if not page or len(page) < self.page_size or offset >= count:
                break
        else:
            raise DataUnavailable(f"FRED API {sid}: more than {MAX_PAGES} pages, giving up")
        frame, n_missing = rows_to_frame(rows, sid, f"FRED API {sid}")
        meta: dict[str, Any] = {
            "url": self.base_url,
            "series_id": sid,
            "alias": SERIES_ALIAS.get(sid),
            "observation_start": self.observation_start.isoformat(),
            "count": count,
            "pages": pages,
            "rows": len(frame),
            "missing_dropped": n_missing,
            "units": UNITS.get(sid),
            "published_at_rule": PUBLISHED_AT_RULE,
        }
        return FetchResult(source=self.name, frame=frame, fetched_at=utc_now(), meta=meta)


class FredCsvAdapter:
    """Key-less ``fredgraph.csv`` fallback: same frame shape as :class:`FredAdapter`, full history, no pagination."""

    name = "fred_csv"

    def __init__(self, client: HttpClient | None = None, base_url: str = CSV_URL):
        self.client = client or HttpClient(timeout=30.0, retries=2, backoff=2.0, min_interval=1.0)
        self.base_url = base_url

    def fetch(self, **kwargs: Any) -> FetchResult:
        """Adapter protocol entry point: ``fetch(series_id=...)``."""
        return self.fetch_series(str(kwargs["series_id"]))

    def fetch_series(self, series_id: str) -> FetchResult:
        """``fredgraph.csv?id=<series>`` -> frame indexed by tz-naive date with ``value`` and ``published_at``.

        Empty (or ``"."``) values are dropped, never filled. Transport failures (the endpoint times out or resets
        intermittently from some networks), an HTML error page or a header naming another series all raise
        :class:`DataUnavailable`.
        """
        sid = resolve_series(series_id)
        try:
            content = self.client.get_bytes(self.base_url, params={"id": sid})
        except DataUnavailable as e:
            raise DataUnavailable(f"fredgraph.csv {sid}: {e}") from e
        frame = parse_fredgraph_csv(content.decode("utf-8-sig", errors="replace"), sid)
        meta: dict[str, Any] = {
            "url": f"{self.base_url}?id={sid}",
            "series_id": sid,
            "alias": SERIES_ALIAS.get(sid),
            "rows": len(frame),
            "missing_dropped": int(frame.attrs.get("missing_dropped", 0)),
            "units": UNITS.get(sid),
            "published_at_rule": PUBLISHED_AT_RULE,
        }
        return FetchResult(source=self.name, frame=frame, fetched_at=utc_now(), meta=meta)
