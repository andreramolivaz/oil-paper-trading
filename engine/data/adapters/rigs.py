"""Baker Hughes North America rig count adapter.

Source: ``https://rigcount.bakerhughes.com/na-rig-count`` (a Drupal page whose workbook links are opaque
``/static-files/<uuid>`` URLs; the real file name and type live in the anchor's ``title`` and ``type``
attributes and change every week). Verified from this container on 2026-10-05 - the page answered **HTTP 200 in
0.75 s** this time, although it had returned 503/timeouts earlier (see ``docs/DATA_SOURCES.md``); the adapter
therefore raises :class:`DataUnavailable` cleanly on any transport failure and the Fetcher degrades to yellow.

Anchors found on 2026-10-05 (the two the adapter uses are marked ``*``):

* ``*`` ``10-02-2026 North_America Rig_Count Report.xlsx`` (7.3 MB) - "North America Rig Count Report - New
  Report": the **current** weekly workbook. Sheets ``NAM Summary``, ``NAM Breakdown``, ``NAM Yearly``,
  ``NAM Quarterly``, ``NAM Monthly``, ``NAM Weekly``. The weekly data is a LONG table on ``NAM Weekly``
  (header on row 11): ``Country, County, Basin, GOM, DrillFor, Location, State/Province, Trajectory, Year,
  Month, US_PublishDate, Rig Count Value`` - 27 189 rows covering **2024-01-05 .. 2026-10-02**. The US weekly
  split is ``Country == "UNITED STATES"`` grouped by ``US_PublishDate`` and ``DrillFor`` in
  {``Oil``, ``Gas``, ``Miscellaneous``}; it reproduces the ``NAM Summary`` headline (2026-10-02: oil 456,
  gas 133, misc 9, total 598).
* ``*`` ``North America Rotary Rig Count (Jan 2000 - Mar 2024).xlsb`` (0.7 MB) - sheet ``US Oil & Gas Split``
  is the classic WIDE layout (header on row 7): ``Date | Oil | Gas | Misc | Total | % Oil | % Gas`` with Excel
  serial dates, and it actually starts **1987-07-17** (serial 31975) and ends 2024-03-28.
* other anchors (pivot table, rigs by state, pre-2016 archives, workover) are not used.

:meth:`BakerHughesAdapter.fetch` parses the current report and, by default, prepends the non-overlapping history
of the ``.xlsb`` archive, so one call returns 1987 -> today. Both layouts go through the same pure parser
(:func:`parse_rig_workbook`), which is unit-tested on a tiny synthetic workbook: no value is ever interpolated,
rows without a usable date or total are dropped.

Frame contract: index ``week`` = publication Friday (tz-naive midnight), columns ``rigs_oil``, ``rigs_gas``,
``rigs_total`` and ``published_at`` = that Friday 13:00 America/New_York in UTC.
"""

from __future__ import annotations

import io
import logging
import re
import warnings
from datetime import UTC, date, datetime
from datetime import time as dtime
from typing import Any

import pandas as pd

from engine.core.errors import DataUnavailable
from engine.core.timeutil import NEW_YORK
from engine.data.base import FetchResult, HttpClient, utc_now

log = logging.getLogger(__name__)

RIG_PAGE_URL = "https://rigcount.bakerhughes.com/na-rig-count"
RIG_COLUMNS = ["rigs_oil", "rigs_gas", "rigs_total"]
RELEASE_TIME_NY = dtime(13, 0)
PUBLISHED_AT_RULE = "publication Friday 13:00 America/New_York"
EXCEL_EPOCH = "1899-12-30"  # Excel serial 1 = 1900-01-01 with the 1900 leap-year bug
WORKBOOK_EXTENSIONS = (".xlsb", ".xlsx", ".xls")
ENGINE_BY_EXTENSION = {".xlsb": "pyxlsb", ".xlsx": "openpyxl", ".xls": None}

# anchors worth downloading (matched against href + title + link text, case-insensitive)
LINK_PATTERNS = (
    re.compile(r"north[\s_]*america\w*[\s_]+rotary[\s_]+rig[\s_]*count", re.IGNORECASE),
    re.compile(r"north[\s_]*america\w*[\s_]+rig[\s_]*count[\s_]+report", re.IGNORECASE),
    re.compile(r"rig[\s_]*count[\s_]+overview", re.IGNORECASE),
)
PIVOT_PATTERN = re.compile(r"pivot|by[\s_]*state|workover|annual|monthly[\s_]*average", re.IGNORECASE)
REPORT_DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
# openpyxl warns "Unknown extension is not supported and will be removed" on the Baker Hughes workbooks (they
# carry Excel extension records openpyxl drops). It is harmless and would spam every weekly job log.
OPENPYXL_EXTENSION_WARNING = "Unknown extension is not supported"
ANCHOR_RE = re.compile(r"<a\s+([^>]*?)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"|([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*'([^']*)'""")
TAG_RE = re.compile(r"<[^>]+>")

LONG_KEY_COLUMNS = ("country", "drillfor", "rig count value")
WIDE_KEY_COLUMNS = ("date", "oil", "gas", "total")
US_LABELS = frozenset({"united states", "united states total", "us", "u.s."})


class RigLink:
    """One candidate workbook link on the Baker Hughes page."""

    __slots__ = ("extension", "report_date", "text", "title", "url")

    def __init__(self, url: str, title: str, text: str, extension: str, report_date: date | None):
        self.url = url
        self.title = title
        self.text = text
        self.extension = extension
        self.report_date = report_date

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"RigLink({self.url!r}, title={self.title!r}, date={self.report_date})"

    @property
    def is_current_report(self) -> bool:
        return self.report_date is not None


def _attrs(blob: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in ATTR_RE.finditer(blob):
        name = (m.group(1) or m.group(3) or "").lower()
        out[name] = m.group(2) if m.group(2) is not None else (m.group(4) or "")
    return out


def _extension(*blobs: str) -> str | None:
    for blob in blobs:
        low = blob.lower()
        for ext in WORKBOOK_EXTENSIONS:
            if ext in low:
                return ext
    return None


def _report_date(title: str) -> date | None:
    m = REPORT_DATE_RE.search(title)
    if not m:
        return None
    month, day, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_workbook_links(html: str, page_url: str = RIG_PAGE_URL) -> list[RigLink]:
    """Workbook anchors on the rig-count page, best candidate first.

    Sorted: the dated current report (newest first), then the other matching workbooks. Pivot tables,
    by-state and average files are filtered out.
    """
    base = page_url.split("/na-rig-count", maxsplit=1)[0].rstrip("/")
    links: list[RigLink] = []
    for m in ANCHOR_RE.finditer(html):
        attrs = _attrs(m.group(1))
        href = attrs.get("href", "")
        title = attrs.get("title", "")
        text = TAG_RE.sub("", m.group(2)).strip()
        if not href:
            continue
        ext = _extension(href, title, attrs.get("type", ""))
        if ext is None:
            continue
        haystack = f"{href} {title} {text}"
        if PIVOT_PATTERN.search(f"{title} {text}"):
            continue
        if not any(p.search(haystack) for p in LINK_PATTERNS):
            continue
        url = href if href.startswith("http") else f"{base}/{href.lstrip('/')}"
        links.append(RigLink(url, title, text, ext, _report_date(title)))
    links.sort(key=lambda link: (0 if link.is_current_report else 1, -(link.report_date or date(1, 1, 1)).toordinal()))
    return links


def _published_at(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    local = pd.DatetimeIndex(index).normalize() + pd.Timedelta(
        hours=RELEASE_TIME_NY.hour, minutes=RELEASE_TIME_NY.minute
    )
    return local.tz_localize(NEW_YORK).tz_convert(UTC)


def _to_dates(values: pd.Series) -> pd.Series:
    """Excel serial numbers (``.xlsb`` via pyxlsb) or real datetimes -> tz-naive Timestamps."""
    if pd.api.types.is_datetime64_any_dtype(values):
        return pd.to_datetime(values, errors="coerce").dt.tz_localize(None).dt.normalize()
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() >= max(1, int(0.8 * values.notna().sum())):
        return pd.to_datetime(numeric, unit="D", origin=EXCEL_EPOCH, errors="coerce")
    return pd.to_datetime(values, errors="coerce")


def _header_row(raw: pd.DataFrame, keys: tuple[str, ...], scan: int = 20) -> int | None:
    """First row (within the first ``scan``) that carries every key in ``keys`` as a cell of its own.

    Matching is exact (after strip/lower) on purpose: the ``US Count by Basin`` sheet of the archive workbook
    holds dozens of basin columns and a substring match on "oil"/"gas" would select it by mistake.
    """
    for i in range(min(scan, len(raw))):
        cells = {str(v).strip().lower() for v in raw.iloc[i].tolist() if not pd.isna(v)}
        if all(key in cells for key in keys):
            return i
    return None


def _column(frame: pd.DataFrame, *names: str) -> str | None:
    """The frame column whose lower-cased name equals one of ``names`` (in the order given)."""
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    for name in names:
        if name in lowered:
            return str(lowered[name])
    return None


def _parse_long_sheet(frame: pd.DataFrame) -> pd.DataFrame:
    """``NAM Weekly``-style long table -> wide US weekly oil/gas/total counts."""
    country = _column(frame, "country")
    drill = _column(frame, "drillfor", "drill for")
    value = _column(frame, "rig count value", "rig count")
    pub = _column(frame, "us_publishdate", "publishdate", "publish date")
    if country is None or drill is None or value is None or pub is None:
        raise DataUnavailable("Baker Hughes: long sheet is missing Country/DrillFor/Rig Count Value/US_PublishDate")
    df = frame[[country, drill, value, pub]].copy()
    df.columns = ["country", "drill", "value", "week"]
    df["country"] = df["country"].astype("string").str.strip().str.lower()
    df = df[df["country"].isin(US_LABELS)]
    df["week"] = _to_dates(df["week"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["week", "value"])
    if df.empty:
        raise DataUnavailable("Baker Hughes: long sheet has no usable United States row")
    df["drill"] = df["drill"].astype("string").str.strip().str.lower()
    total = df.groupby("week")["value"].sum()
    oil = df[df["drill"] == "oil"].groupby("week")["value"].sum()
    gas = df[df["drill"] == "gas"].groupby("week")["value"].sum()
    out = pd.DataFrame({"rigs_oil": oil, "rigs_gas": gas, "rigs_total": total}).sort_index()
    out.index = pd.DatetimeIndex(out.index, name="week")
    return out


def _parse_wide_sheet(frame: pd.DataFrame) -> pd.DataFrame:
    """``US Oil & Gas Split``-style wide table (Date | Oil | Gas | Misc | Total) -> the engine frame."""
    date_col = _column(frame, "date")
    oil = _column(frame, "oil")
    gas = _column(frame, "gas")
    total = _column(frame, "total")
    if date_col is None or oil is None or gas is None or total is None:
        raise DataUnavailable("Baker Hughes: wide sheet is missing Date/Oil/Gas/Total")
    out = pd.DataFrame(
        {
            "rigs_oil": pd.to_numeric(frame[oil], errors="coerce"),
            "rigs_gas": pd.to_numeric(frame[gas], errors="coerce"),
            "rigs_total": pd.to_numeric(frame[total], errors="coerce"),
        }
    )
    out.index = pd.DatetimeIndex(_to_dates(frame[date_col]), name="week")
    out = out[out.index.notna() & out["rigs_total"].notna().to_numpy()]
    return out.sort_index()


def _sheet_priority(sheet: object) -> tuple[int, str]:
    """Try the sheets that hold the US weekly split first (``US Oil & Gas Split``, ``NAM Weekly``)."""
    low = str(sheet).strip().lower()
    if "oil" in low and "gas" in low and "split" in low and "canada" not in low:
        return (0, low)
    if "weekly" in low:
        return (1, low)
    return (2, low)


def parse_rig_workbook(content: bytes, extension: str = ".xlsx") -> pd.DataFrame:
    """Baker Hughes workbook bytes -> ``rigs_oil``/``rigs_gas``/``rigs_total`` + ``published_at``.

    Both published layouts are supported (long ``NAM Weekly`` table, wide ``US Oil & Gas Split`` sheet); the
    first sheet that parses wins. ``DataUnavailable`` is raised when no sheet matches either layout.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=OPENPYXL_EXTENSION_WARNING, category=UserWarning)
        return _parse_rig_workbook(content, extension)


def _parse_rig_workbook(content: bytes, extension: str) -> pd.DataFrame:
    """Implementation of :func:`parse_rig_workbook` (wrapped there by the openpyxl warnings filter)."""
    # the anchor's extension is a hint, not a guarantee (the links are opaque /static-files/<uuid> URLs and the
    # published type attribute has been wrong before), so fall back to pandas' own sniffing and then to pyxlsb
    engines: list[str | None] = [ENGINE_BY_EXTENSION.get(extension.lower())]
    engines += [e for e in (None, "openpyxl", "pyxlsb") if e not in engines]
    book: pd.ExcelFile | None = None
    opening: list[str] = []
    for engine in engines:
        try:
            book = pd.ExcelFile(io.BytesIO(content), engine=engine)  # type: ignore[arg-type]
            break
        except Exception as e:  # openpyxl/pyxlsb/xlrd raise many error types on HTML pages or truncated files
            opening.append(f"{engine or 'auto'}: {e}")
    if book is None:
        raise DataUnavailable(f"Baker Hughes workbook ({extension}): cannot open ({'; '.join(opening[:3])})")
    errors: list[str] = []
    for sheet in sorted(book.sheet_names, key=_sheet_priority):
        try:
            head = book.parse(sheet, header=None, nrows=24)
        except Exception as e:  # a single broken sheet must not kill the workbook
            errors.append(f"{sheet}: {e}")
            continue
        for keys, parser in ((LONG_KEY_COLUMNS, _parse_long_sheet), (WIDE_KEY_COLUMNS, _parse_wide_sheet)):
            row = _header_row(head, keys)
            if row is None:
                continue
            try:
                frame = parser(book.parse(sheet, header=row))
            except DataUnavailable as e:
                errors.append(f"{sheet}: {e}")
                continue
            if frame.empty:
                errors.append(f"{sheet}: no rows after parsing")
                continue
            frame["published_at"] = _published_at(pd.DatetimeIndex(frame.index))
            frame.attrs["sheet"] = sheet
            frame.attrs["layout"] = "long" if parser is _parse_long_sheet else "wide"
            frame.attrs["units"] = "rigs"
            return frame
    raise DataUnavailable(
        f"Baker Hughes workbook ({extension}): no sheet with a weekly US rig count "
        f"(sheets={book.sheet_names}; {'; '.join(errors[:4])})"
    )


class BakerHughesAdapter:
    """Weekly US oil/gas rig count scraped from the Baker Hughes rig-count page."""

    name = "baker_hughes"

    def __init__(self, client: HttpClient | None = None, url: str = RIG_PAGE_URL):
        self.client = client or HttpClient(timeout=30.0, retries=2, backoff=2.0, min_interval=0.5)
        self.url = url

    def fetch(self, **kwargs: Any) -> FetchResult:
        """Current report (+ the archive's older history unless ``history=False``).

        Raises :class:`DataUnavailable` when the page or every candidate workbook is unreachable or unparseable.
        """
        page_url = str(kwargs.get("url") or self.url)
        want_history = bool(kwargs.get("history", True))
        try:
            html = self.client.get(page_url).text
        except DataUnavailable as e:
            raise DataUnavailable(f"Baker Hughes page {page_url}: {e}") from e
        links = find_workbook_links(html, page_url)
        if not links:
            raise DataUnavailable(f"Baker Hughes page {page_url}: no rig-count workbook link found")
        current: pd.DataFrame | None = None
        used: list[dict[str, Any]] = []
        failed: list[str] = []
        history: pd.DataFrame | None = None
        for link in links:
            if current is not None and (not want_history or history is not None):
                break
            if current is not None and link.is_current_report:
                continue  # only the newest dated report is used
            try:
                frame = parse_rig_workbook(self.client.get_bytes(link.url), link.extension)
            except DataUnavailable as e:
                failed.append(f"{link.title or link.text}: {e}")
                log.warning("Baker Hughes %s unusable: %s", link.url, e)
                continue
            entry = {
                "url": link.url,
                "title": link.title,
                "layout": frame.attrs.get("layout"),
                "sheet": frame.attrs.get("sheet"),
                "rows": len(frame),
                "first_week": frame.index[0].date().isoformat(),
                "last_week": frame.index[-1].date().isoformat(),
            }
            if current is None:
                current = frame
                entry["role"] = "current"
            else:
                history = frame
                entry["role"] = "history"
            used.append(entry)
        if current is None:
            raise DataUnavailable(f"Baker Hughes: no usable workbook ({'; '.join(failed[:3])})")
        frame = current
        if history is not None:
            older = history[history.index < frame.index[0]]
            if not older.empty:
                frame = pd.concat([older, frame]).sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        frame.attrs["units"] = "rigs"
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={
                "page": page_url,
                "workbooks": used,
                "failed": failed,
                "candidates": [link.title or link.text for link in links],
                "rows": len(frame),
                "first_week": frame.index[0].date().isoformat(),
                "last_week": frame.index[-1].date().isoformat(),
                "published_at_rule": PUBLISHED_AT_RULE,
            },
        )


def release_timestamp(week: date) -> datetime:
    """UTC timestamp of the Baker Hughes release for a publication Friday."""
    return datetime.combine(week, RELEASE_TIME_NY, tzinfo=NEW_YORK).astimezone(UTC)
