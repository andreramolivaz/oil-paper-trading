"""EIA adapters: API v2 (needs a free ``EIA_API_KEY``) and key-less XLS/CSV fallbacks.

Routes verified against ``api.eia.gov`` on 2026-10-05 (see ``tests/fixtures/eia/README.md`` for the captured
responses and the real values used in the tests):

===========================  ====================================================================================
route                        series
===========================  ====================================================================================
petroleum/pri/spt/data       daily spot prices, ``facets[series][]=RBRTE`` (Brent, from 1987-05-20) or ``RWTC``
petroleum/pri/fut/data       NYMEX WTI futures ``RCLC1..RCLC4`` (1983 -> 2024-04-05, series discontinued)
petroleum/stoc/wstk/data     weekly stocks (MBBL = thousand barrels): ``WCESTUS1`` (US commercial crude),
                             ``W_EPC0_SAX_YCUOK_MBBL`` (Cushing), ``WGTSTUS1`` (gasoline), ``WDISTUS1`` (distillate)
petroleum/sum/sndw/data      weekly supply/disposition (MBBL/D): ``WCRRIUS2`` (refiner net input), ``WCRFPUS2``
                             (field production), ``WCRIMUS2`` (imports), ``WCREXUS2`` (exports), ``WGFUPUS2``
                             (gasoline product supplied), ``WDIUPUS2`` (distillate product supplied)
steo/data                    Short-Term Energy Outlook, ``facets[seriesId][]=BREPUUS`` (monthly Brent, 1990-01 ->
                             end of next year; the API serves the current vintage only)
===========================  ====================================================================================

Every call carries ``frequency``, ``data[0]=value``, a sort on ``period`` and ``length=5000`` (the hard cap of the
JSON API); longer series are paginated with ``offset`` until ``response.total`` rows have been received.

``published_at`` rules (UTC, the earliest moment the datum was knowable):

* **Daily spot prices.** EIA refreshes the daily spot series once a week, on Wednesday morning (ET), with data
  through the previous Tuesday/Friday (the RBRTEd.xls workbook fetched on 2026-10-05 was built 2026-09-30 10:44
  with the last price dated 2026-09-29). ``published_at`` = first Wednesday *strictly after* the price date,
  18:00 UTC: conservative versus the real ~10:30-11:00 ET refresh.
* **Futures history** (discontinued 2024-04-05, historical proxy only): date + 1 day 00:00 UTC.
* **WPSR weekly series.** The Weekly Petroleum Status Report covers the week ending Friday and is released the
  following Wednesday at 10:30 ET. A US federal holiday on the Monday, Tuesday or Wednesday of the release week
  delays the release by one business day each (Labor Day Monday -> Thursday 10:30 ET; a Wednesday holiday such as
  Juneteenth 2024 -> Thursday). Holidays come from :func:`engine.core.calendar.us_holidays`; the ET -> UTC
  conversion from :func:`engine.core.timeutil.eia_wpsr_release_ts`.
* **STEO.** The API has no release-date field and serves only the current vintage. STEO is never released before
  the 6th of the month (first Tuesday after the first Thursday, 2024-2025 range: 6th-14th), so the vintage month
  is the fetch month when fetching on/after the 6th, otherwise the previous month; ``published_at`` is
  approximated as the 8th of the vintage month 17:00 UTC (documented approximation; pass ``vintage`` explicitly
  when the release date is known).

Fallbacks without an API key (``EiaFallbackAdapter``): the historical spot workbook
``https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls`` (sheet ``Data 1``) and the current WPSR Table 1 CSV
``https://ir.eia.gov/wpsr/table1.csv`` (302 redirect to a signed URL; requests follows it). Table 1 carries all
WPSR columns except Cushing stocks, which is NaN.

Nothing here invents values: missing/null observations are dropped (spot, futures) or left NaN (wide frames), and
any transport or parsing failure raises :class:`engine.core.errors.DataUnavailable`.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from engine.core.calendar import us_holidays
from engine.core.errors import DataUnavailable
from engine.core.timeutil import eia_wpsr_release_ts
from engine.data.base import FetchResult, HttpClient, utc_now
from engine.data.market_data import WPSR_COLUMNS

log = logging.getLogger(__name__)

EIA_API_BASE = "https://api.eia.gov/v2"
PAGE_SIZE = 5000  # the EIA v2 JSON API never returns more rows per call
MAX_PAGES = 100  # safety stop for the offset loop (largest series we use: ~40k rows = 9 pages)

SPOT_ROUTE = "petroleum/pri/spt/data/"
FUTURES_ROUTE = "petroleum/pri/fut/data/"
STOCKS_ROUTE = "petroleum/stoc/wstk/data/"
SUPPLY_ROUTE = "petroleum/sum/sndw/data/"
STEO_ROUTE = "steo/data/"

FUTURES_SERIES: tuple[str, ...] = ("RCLC1", "RCLC2", "RCLC3", "RCLC4")
STEO_BRENT_SERIES = "BREPUUS"

# WPSR column -> (API route, EIA series id). Every id verified on 2026-10-05: each returns weekly rows in the
# route shown (stocks live in stoc/wstk, flows in sum/sndw; none of them is in move/wkly).
WPSR_SERIES: dict[str, tuple[str, str]] = {
    "crude_stocks": (STOCKS_ROUTE, "WCESTUS1"),
    "cushing_stocks": (STOCKS_ROUTE, "W_EPC0_SAX_YCUOK_MBBL"),
    "gasoline_stocks": (STOCKS_ROUTE, "WGTSTUS1"),
    "distillate_stocks": (STOCKS_ROUTE, "WDISTUS1"),
    "refinery_inputs": (SUPPLY_ROUTE, "WCRRIUS2"),
    "crude_production": (SUPPLY_ROUTE, "WCRFPUS2"),
    "crude_imports": (SUPPLY_ROUTE, "WCRIMUS2"),
    "crude_exports": (SUPPLY_ROUTE, "WCREXUS2"),
    "gasoline_supplied": (SUPPLY_ROUTE, "WGFUPUS2"),
    "distillate_supplied": (SUPPLY_ROUTE, "WDIUPUS2"),
}
WPSR_VALUE_COLUMNS: list[str] = [c for c in WPSR_COLUMNS if c != "period"]
WPSR_UNITS: dict[str, str] = {c: ("kbbl" if c.endswith("_stocks") else "kbbl/d") for c in WPSR_VALUE_COLUMNS}

SPOT_XLS_URL = "https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls"
WPSR_TABLE1_URL = "https://ir.eia.gov/wpsr/table1.csv"

# WPSR Table 1, first block (stocks, million barrels): row label -> column. First occurrence wins.
TABLE1_STOCK_LABELS: dict[str, str] = {
    "Commercial (Excluding SPR)": "crude_stocks",
    "Total Motor Gasoline": "gasoline_stocks",
    "Distillate Fuel Oil": "distillate_stocks",
}
# WPSR Table 1, second block (supply/disposition, kb/d): (group, label without line number) -> column.
TABLE1_SUPPLY_LABELS: dict[tuple[str, str], str] = {
    ("Crude Oil Supply", "Domestic Production"): "crude_production",
    ("Crude Oil Supply", "Imports"): "crude_imports",
    ("Crude Oil Supply", "Exports"): "crude_exports",
    ("Crude Oil Supply", "Crude Oil Input to Refineries"): "refinery_inputs",
    ("Products Supplied", "Finished Motor Gasoline"): "gasoline_supplied",
    ("Products Supplied", "Distillate Fuel Oil"): "distillate_supplied",
}

SPOT_PUBLISH_HOUR_UTC = 18
STEO_RELEASE_DAY = 8
STEO_RELEASE_HOUR_UTC = 17
STEO_EARLIEST_RELEASE_DAY = 6


# --------------------------------------------------------------------------------------------------------------
# published_at rules (pure functions, unit-tested)
# --------------------------------------------------------------------------------------------------------------
def spot_published_at(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """First Wednesday strictly after each (tz-naive) date, 18:00 UTC: EIA refreshes daily spot prices weekly."""
    naive = dates.tz_convert(None) if dates.tz is not None else dates
    days_ahead = (2 - np.asarray(naive.weekday, dtype=np.int64)) % 7  # Wednesday == 2
    days_ahead = np.where(days_ahead == 0, 7, days_ahead)
    shifted = naive.normalize() + pd.to_timedelta(days_ahead, unit="D") + pd.Timedelta(hours=SPOT_PUBLISH_HOUR_UTC)
    return pd.DatetimeIndex(shifted).tz_localize(UTC)


def wpsr_release_day(period: date) -> date:
    """Release date of the WPSR covering the week ending ``period`` (normally the following Wednesday).

    Each US federal holiday falling on the Monday, Tuesday or Wednesday of the release week pushes the release
    back by one day (Labor Day -> Thursday); a release landing on a holiday or weekend moves to the next day.
    """
    monday = period + timedelta(days=((7 - period.weekday()) % 7) or 7)
    wednesday = monday + timedelta(days=2)
    hols = us_holidays(monday.year) | us_holidays(wednesday.year)
    delay = sum(1 for d in (monday, monday + timedelta(days=1), wednesday) if d in hols)
    release = wednesday + timedelta(days=delay)
    while release.weekday() >= 5 or release in us_holidays(release.year):
        release += timedelta(days=1)
    return release


def wpsr_published_at(period: date) -> datetime:
    """UTC timestamp of the WPSR release (10:30 America/New_York) for the week ending ``period``."""
    return eia_wpsr_release_ts(wpsr_release_day(period))


def steo_vintage(fetched_at: datetime) -> date:
    """First day of the STEO vintage month implied by a fetch time (see module docstring)."""
    d = fetched_at.astimezone(UTC).date()
    first = d.replace(day=1)
    if d.day >= STEO_EARLIEST_RELEASE_DAY:
        return first
    return (first - timedelta(days=1)).replace(day=1)


def steo_published_at(vintage: date) -> datetime:
    """Approximate STEO release: the 8th of the vintage month at 17:00 UTC."""
    return datetime(vintage.year, vintage.month, STEO_RELEASE_DAY, STEO_RELEASE_HOUR_UTC, tzinfo=UTC)


# --------------------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------------------
def _period_param(d: date, frequency: str) -> str:
    return d.strftime("%Y-%m") if frequency == "monthly" else d.isoformat()


def _rows_to_long(rows: list[dict[str, Any]], series_key: str, what: str) -> pd.DataFrame:
    """API rows -> long frame with columns period (Timestamp), series (str), value (float); nulls dropped."""
    if not rows:
        raise DataUnavailable(f"EIA API {what}: no rows returned")
    df = pd.DataFrame(rows)
    missing = {"period", "value", series_key} - set(df.columns)
    if missing:
        raise DataUnavailable(f"EIA API {what}: rows lack columns {sorted(missing)}")
    out = pd.DataFrame(
        {
            "period": pd.to_datetime(df["period"].astype(str), errors="coerce"),
            "series": df[series_key].astype(str),
            "value": pd.to_numeric(df["value"], errors="coerce"),
        }
    )
    out = out.dropna(subset=["period", "value"])
    if out.empty:
        raise DataUnavailable(f"EIA API {what}: every row had a null period or value")
    return out


def _pivot(long: pd.DataFrame, columns: list[str], index_name: str) -> pd.DataFrame:
    wide = long.pivot_table(index="period", columns="series", values="value", aggfunc="last")
    wide = wide.reindex(columns=columns).sort_index()
    wide.columns.name = None
    wide.index.name = index_name
    wide = wide.dropna(how="all")
    if wide.empty:
        raise DataUnavailable("EIA API: no usable rows after pivot")
    return wide.astype(float)


def _parse_us_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _num(text: str) -> float:
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return float("nan")


_LINE_NUMBER = re.compile(r"^\(\d+\)\s*")


def parse_eia_data1_sheet(raw: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Parse the ``Data 1`` sheet of an EIA ``hist_xls`` workbook read with ``header=None``.

    Layout: row 0 title, row 1 ``Sourcekey | <id>``, row 2 ``Date | <description>``, then one row per observation.
    Returns (frame indexed by tz-naive date with a float ``value`` column, sourcekey).
    """
    if raw.shape[1] < 2:
        raise DataUnavailable("EIA xls: Data 1 sheet has fewer than two columns")
    first_col = raw.iloc[:, 0].astype(str).str.strip()
    key_rows = raw.index[first_col.str.lower() == "sourcekey"]
    sourcekey = str(raw.iloc[int(key_rows[0]), 1]).strip() if len(key_rows) else ""
    date_rows = raw.index[first_col.str.lower() == "date"]
    if not len(date_rows):
        raise DataUnavailable("EIA xls: no 'Date' header row in Data 1 sheet")
    body = raw.iloc[int(date_rows[0]) + 1 :, :2]
    dates = pd.to_datetime(body.iloc[:, 0], errors="coerce")
    values = pd.to_numeric(body.iloc[:, 1], errors="coerce")
    frame = pd.DataFrame({"value": values.to_numpy(dtype=float)}, index=pd.DatetimeIndex(dates, name="date"))
    frame = frame[np.asarray(frame.index.notna()) & frame["value"].notna().to_numpy()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if frame.empty:
        raise DataUnavailable("EIA xls: Data 1 sheet has no observations")
    return frame, sourcekey


def parse_wpsr_table1(text: str) -> pd.DataFrame:
    """Parse WPSR Table 1 (``table1.csv``) into a one-row WPSR frame for the latest week.

    Block 1 (header ``STUB_1,<date>,...``) holds stocks in million barrels -> converted to kbbl. Block 2 (header
    ``STUB_1,STUB_2,<date>,...``) holds supply/disposition in kb/d. Columns not present (Cushing) are NaN.
    """
    values: dict[str, float] = {c: float("nan") for c in WPSR_VALUE_COLUMNS}
    found: set[str] = set()
    period: date | None = None
    section = 0
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip():
            continue
        if row[0].strip() == "STUB_1":
            if len(row) > 2 and row[1].strip() == "STUB_2":
                section, date_text = 2, row[2]
            elif len(row) > 1:
                section, date_text = 1, row[1]
            else:
                continue
            parsed = _parse_us_date(date_text)
            if parsed is None:
                raise DataUnavailable(f"WPSR table1: cannot parse period {date_text!r}")
            if period is None:
                period = parsed
            elif parsed != period:
                raise DataUnavailable(f"WPSR table1: inconsistent periods {period} vs {parsed}")
            continue
        if section == 1 and len(row) > 1:
            col = TABLE1_STOCK_LABELS.get(row[0].strip())
            if col and col not in found:
                values[col] = _num(row[1]) * 1000.0  # million barrels -> thousand barrels
                found.add(col)
        elif section == 2 and len(row) > 2:
            label = _LINE_NUMBER.sub("", row[1].strip())
            col = TABLE1_SUPPLY_LABELS.get((row[0].strip(), label))
            if col and col not in found:
                values[col] = _num(row[2])
                found.add(col)
    if period is None or not found:
        raise DataUnavailable("WPSR table1: no recognisable header/rows (format changed?)")
    frame = pd.DataFrame([values], index=pd.DatetimeIndex([pd.Timestamp(period)], name="period"))
    frame = frame.reindex(columns=WPSR_VALUE_COLUMNS).astype(float)
    frame["published_at"] = pd.DatetimeIndex([wpsr_published_at(period)])
    frame.attrs["units"] = dict(WPSR_UNITS)
    frame.attrs["missing"] = [c for c in WPSR_VALUE_COLUMNS if c not in found]
    return frame


# --------------------------------------------------------------------------------------------------------------
# API v2 adapter
# --------------------------------------------------------------------------------------------------------------
class EiaAdapter:
    """EIA Open Data API v2. Every fetch raises :class:`DataUnavailable` immediately when no key is configured."""

    name = "eia"

    def __init__(
        self,
        api_key: str | None,
        client: HttpClient | None = None,
        base_url: str = EIA_API_BASE,
        page_size: int = PAGE_SIZE,
    ):
        self.api_key = api_key or None
        self.client = client or HttpClient(timeout=30.0, retries=3, backoff=1.5, min_interval=0.3)
        self.base_url = base_url.rstrip("/")
        self.page_size = max(1, min(int(page_size), PAGE_SIZE))

    # ---- transport --------------------------------------------------------------------------------------------
    def _require_key(self) -> str:
        if not self.api_key:
            raise DataUnavailable("EIA API: no API key configured (set EIA_API_KEY; free at eia.gov/opendata)")
        return self.api_key

    def _fetch_rows(
        self,
        route: str,
        frequency: str,
        facets: dict[str, list[str]],
        start: date | None = None,
        end: date | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Page through ``route`` (ascending period, ``length``/``offset``) and return all rows + call metadata."""
        key = self._require_key()
        url = f"{self.base_url}/{route}"
        params: dict[str, Any] = {
            "api_key": key,
            "frequency": frequency,
            "data[0]": "value",
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": self.page_size,
        }
        for facet, ids in facets.items():
            params[f"facets[{facet}][]"] = list(ids)
        if start is not None:
            params["start"] = _period_param(start, frequency)
        if end is not None:
            params["end"] = _period_param(end, frequency)

        rows: list[dict[str, Any]] = []
        total: int | None = None
        offset = 0
        pages = 0
        while pages < MAX_PAGES:
            params["offset"] = offset
            payload = self.client.get_json(url, params=params)
            pages += 1
            response = payload.get("response") if isinstance(payload, dict) else None
            if not isinstance(response, dict) or "data" not in response:
                err = payload.get("error", payload) if isinstance(payload, dict) else payload
                raise DataUnavailable(f"EIA API {route}: unexpected response {str(err)[:200]!r}")
            page = response.get("data") or []
            if not isinstance(page, list):
                raise DataUnavailable(f"EIA API {route}: 'data' is not a list")
            rows.extend(page)
            if total is None:
                try:
                    total = int(response.get("total") or 0)
                except (TypeError, ValueError):
                    total = 0
            offset += len(page)
            if not page or len(page) < self.page_size or (total and offset >= total):
                break
        else:
            raise DataUnavailable(f"EIA API {route}: more than {MAX_PAGES} pages, giving up")
        return rows, {"route": route, "total": total, "pages": pages, "rows": len(rows)}

    # ---- public API -------------------------------------------------------------------------------------------
    def fetch_spot(self, series: str = "RBRTE", start: date | None = None, end: date | None = None) -> FetchResult:
        """Daily spot price (``RBRTE`` Brent FOB, ``RWTC`` WTI Cushing). Index: tz-naive date; column ``value``.

        ``published_at`` = first Wednesday strictly after the date, 18:00 UTC (EIA publishes the daily spot series
        weekly). Rows with a null value (EIA marks some holidays that way) are dropped, never filled.
        """
        rows, info = self._fetch_rows(SPOT_ROUTE, "daily", {"series": [series]}, start, end)
        long = _rows_to_long(rows, "series", f"spot {series}")
        long = long[long["series"] == series]
        if long.empty:
            raise DataUnavailable(f"EIA API spot: no rows for series {series}")
        long = long.drop_duplicates(subset="period", keep="last").sort_values("period")
        frame = pd.DataFrame(
            {"value": long["value"].to_numpy(dtype=float)}, index=pd.DatetimeIndex(long["period"], name="date")
        )
        frame["published_at"] = spot_published_at(pd.DatetimeIndex(frame.index))
        frame.attrs["units"] = "USD/bbl"
        units = str(rows[0].get("units", "$/BBL"))
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={**info, "series": series, "units": units, "published_at_rule": "next Wednesday 18:00 UTC"},
        )

    def fetch_futures_hist(self, start: date | None = None, end: date | None = None) -> FetchResult:
        """NYMEX WTI futures ``RCLC1..RCLC4`` (USD/bbl) as a wide daily frame; the series ended 2024-04-05.

        ``published_at`` = date + 1 day 00:00 UTC. Historical curve-slope proxy only (``approx`` is set because the
        downstream use is a proxy for the Brent curve).
        """
        rows, info = self._fetch_rows(FUTURES_ROUTE, "daily", {"series": list(FUTURES_SERIES)}, start, end)
        long = _rows_to_long(rows, "series", "futures RCLC1..4")
        frame = _pivot(long, list(FUTURES_SERIES), "date")
        frame["published_at"] = pd.DatetimeIndex(frame.index).tz_localize(UTC) + pd.Timedelta(days=1)
        frame.attrs["units"] = "USD/bbl"
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={**info, "series": list(FUTURES_SERIES), "published_at_rule": "date + 1 day 00:00 UTC"},
            approx=True,
        )

    def fetch_wpsr(self, start: date | None = None, end: date | None = None) -> FetchResult:
        """Weekly Petroleum Status Report series as one wide frame.

        Index ``period`` = week-ending Friday (tz-naive). Columns = ``WPSR_COLUMNS`` minus ``period`` (stocks in
        kbbl, flows in kbbl/d, NaN where a series has no observation) plus ``published_at`` = release Wednesday
        10:30 America/New_York (Thursday after a Monday US holiday), in UTC. Both API routes must succeed.
        """
        by_route: dict[str, list[str]] = {}
        for route, sid in WPSR_SERIES.values():
            by_route.setdefault(route, []).append(sid)
        id_to_col = {sid: col for col, (_, sid) in WPSR_SERIES.items()}
        longs: list[pd.DataFrame] = []
        meta: dict[str, Any] = {"routes": {}}
        for route, ids in by_route.items():
            rows, info = self._fetch_rows(route, "weekly", {"series": ids}, start, end)
            long = _rows_to_long(rows, "series", f"wpsr {route}")
            long = long[long["series"].isin(ids)]
            long["series"] = long["series"].map(id_to_col)
            longs.append(long)
            meta["routes"][route] = {"series": ids, "rows": info["rows"], "pages": info["pages"]}
        frame = _pivot(pd.concat(longs, ignore_index=True), WPSR_VALUE_COLUMNS, "period")
        frame["published_at"] = pd.DatetimeIndex([wpsr_published_at(ts.date()) for ts in frame.index])
        frame.attrs["units"] = dict(WPSR_UNITS)
        meta["series"] = {col: sid for col, (_, sid) in WPSR_SERIES.items()}
        meta["published_at_rule"] = "Wednesday 10:30 America/New_York after the week; +1 day per Mon-Wed US holiday"
        return FetchResult(source=self.name, frame=frame, fetched_at=utc_now(), meta=meta)

    def fetch_steo_brent(self, vintage: date | None = None) -> FetchResult:
        """Monthly Brent spot price path from the Short-Term Energy Outlook (``BREPUUS``, USD/bbl).

        Index ``period`` = first day of the month (tz-naive); columns ``value``, ``is_forecast`` (period on/after
        the vintage month) and ``published_at`` = 8th of the vintage month 17:00 UTC (approximation, see module
        docstring). ``vintage`` overrides the fetch-time heuristic when the real release month is known.
        """
        fetched_at = utc_now()
        rows, info = self._fetch_rows(STEO_ROUTE, "monthly", {"seriesId": [STEO_BRENT_SERIES]})
        long = _rows_to_long(rows, "seriesId", f"steo {STEO_BRENT_SERIES}")
        long = long[long["series"] == STEO_BRENT_SERIES].drop_duplicates(subset="period", keep="last")
        if long.empty:
            raise DataUnavailable(f"EIA API steo: no rows for {STEO_BRENT_SERIES}")
        long = long.sort_values("period")
        frame = pd.DataFrame(
            {"value": long["value"].to_numpy(dtype=float)}, index=pd.DatetimeIndex(long["period"], name="period")
        )
        v = (vintage or steo_vintage(fetched_at)).replace(day=1)
        frame["is_forecast"] = frame.index >= pd.Timestamp(v)
        frame["published_at"] = steo_published_at(v)
        frame.attrs["units"] = "USD/bbl"
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=fetched_at,
            meta={
                **info,
                "series": STEO_BRENT_SERIES,
                "vintage": v.strftime("%Y-%m"),
                "published_at_rule": "8th of the vintage month 17:00 UTC (approximation; API has no release date)",
            },
        )


# --------------------------------------------------------------------------------------------------------------
# key-less fallbacks
# --------------------------------------------------------------------------------------------------------------
class EiaFallbackAdapter:
    """Key-less EIA files: historical spot workbook (slow, ~450 KB) and the current WPSR Table 1 CSV."""

    name = "eia_xls"

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient(timeout=60.0, retries=2, backoff=2.0, min_interval=1.0)

    def fetch_spot_xls(self, url: str = SPOT_XLS_URL) -> FetchResult:
        """Daily spot history from an EIA ``hist_xls`` workbook (sheet ``Data 1``); same shape as ``fetch_spot``."""
        content = self.client.get_bytes(url)
        try:
            raw = pd.read_excel(io.BytesIO(content), sheet_name="Data 1", header=None)
        except Exception as e:  # xlrd/openpyxl raise a zoo of errors on HTML error pages or truncated files
            raise DataUnavailable(f"EIA xls {url}: cannot read sheet 'Data 1': {e}") from e
        frame, sourcekey = parse_eia_data1_sheet(raw)
        frame["published_at"] = spot_published_at(pd.DatetimeIndex(frame.index))
        frame.attrs["units"] = "USD/bbl"
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={
                "url": url,
                "series": sourcekey,
                "rows": len(frame),
                "published_at_rule": "next Wednesday 18:00 UTC",
            },
        )

    def fetch_wpsr_table1(self, url: str = WPSR_TABLE1_URL) -> FetchResult:
        """Latest WPSR week from Table 1 (``table1.csv``): same columns as ``fetch_wpsr``; Cushing stocks NaN."""
        content = self.client.get_bytes(url)
        text = content.decode("cp1252", errors="replace")  # the file uses cp1252 en dashes for n/a cells
        frame = parse_wpsr_table1(text)
        missing = list(frame.attrs.get("missing", []))
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={
                "url": url,
                "missing": missing,
                "period": frame.index[0].date().isoformat(),
                "published_at_rule": "Wednesday 10:30 America/New_York after the week; +1 day per Mon-Wed US holiday",
            },
        )
