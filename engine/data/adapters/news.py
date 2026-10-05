"""News / geopolitics adapters: GPR daily index, GDELT 2.0 raw event files, GDELT DOC API timelines.

GPR (Caldara-Iacoviello)
------------------------
``https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls`` (verified 2026-10-05, 3.3 MB, real
``.xls`` CDFV2 workbook readable by ``xlrd``). One sheet, one row per calendar day from 1985-01-01, **15 253 rows
on 2026-10-05 with the last row 2026-10-05**. Columns actually present (the brief's list is a subset):
``DAY`` (int ``yyyymmdd``), ``N10D``, ``GPRD``, ``GPRD_ACT``, ``GPRD_THREAT``, ``date`` (datetime, same day),
``GPRD_MA30``, ``GPRD_MA7``, ``event`` (11 labelled historical episodes) and the two legend columns ``var_name`` /
``var_label`` (only the first 9 rows carry them; they document the file and are dropped here).

The index is revised: the 2026-10-01 value read on 2026-10-05 is ``176.278549``, while the same cell read a few
hours earlier (file with 15 249 rows, last day 2026-10-01) was ``181.736877``. The daily index is rescaled when
new days are appended, so a snapshot is a vintage: every fetch is archived with its ``observed_at`` and nothing
is overwritten.

``published_at`` = date + 2 days 12:00 UTC: the file is refreshed with a lag of one to three days and this is the
conservative rule used project-wide for GPR (``engine.features.news`` applies the same two-business-day lag).

GDELT 2.0 raw event files (primary news source)
----------------------------------------------
``https://data.gdeltproject.org/gdeltv2/lastupdate.txt`` returns three ``size md5 url`` lines (export, mentions,
gkg). The URLs are served over plain HTTP in the file; HTTPS works and is used here (plain HTTP answers 301).
Each 15-minute slot has an ``{YYYYMMDDHHMMSS}.export.CSV.zip`` of ~65 KB holding ~1 000 events.

Column positions **verified on the real file ``20261005160000.export.CSV``** (963 rows, every row exactly 61
tab-separated fields, no header). The brief's expected positions are all confirmed:

====  ==========================  ==========================================================================
pos   field                       note
====  ==========================  ==========================================================================
0     GLOBALEVENTID
1     SQLDATE                     ``yyyymmdd`` of the event, which can be years older than the file
7     Actor1CountryCode           3-letter CAMEO country code (``USA``, ``SAU``, ``IRN``), empty when unknown
17    Actor2CountryCode           idem
28    EventRootCode               2-digit CAMEO root (``01``..``20``)
30    GoldsteinScale              -10..+10
33    NumArticles                 articles mentioning the event in the slot
34    AvgTone                     average tone of those articles (negative = hostile)
53    ActionGeo_CountryCode       **2-letter FIPS 10-4** code (``IR`` Iran, ``IS`` Israel, ``SA`` Saudi Arabia),
                                  NOT the 3-letter actor code: the sets below differ for that reason
60    SOURCEURL
====  ==========================  ==========================================================================

Also confirmed from the same file (used by the parser): 6 ``Actor1Name``, 16 ``Actor2Name``, 26 ``EventCode``,
31 ``NumMentions``, 32 ``NumSources``, 59 ``DATEADDED`` (= the file timestamp for every row).

A row matches the oil/Middle-East filter when

1. ``Actor1CountryCode`` or ``Actor2CountryCode`` is in :data:`OIL_ACTOR_CODES` **and** ``ActionGeo_CountryCode``
   is in :data:`GULF_GEO_CODES` (FIPS), **or**
2. ``SOURCEURL``, ``Actor1Name`` or ``Actor2Name`` matches :data:`OIL_KEYWORDS` (``hormuz|opec|tanker|oil``,
   case-insensitive).

``published_at`` = file timestamp + 15 min (the slot is complete only at its end).

GDELT DOC API (secondary, unreliable)
-------------------------------------
``https://api.gdeltproject.org/api/v2/doc/doc`` is limited to one request every 5 s per IP and answers **HTTP 429
with a plain-text body** from this container's shared egress (verified again 2026-10-05). The adapter paces calls
6 s apart, does not retry a 429 more than once and raises :class:`DataUnavailable`; its parser is tested on a
hand-written payload (``tests/fixtures/news/gdelt_doc_timelinevol_HANDWRITTEN.json``) and the live call is an
opt-in network test.
"""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from engine.core.errors import DataUnavailable
from engine.data.base import FetchResult, HttpClient, utc_now

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------------------- GPR
GPR_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
GPR_COLUMNS = ["gpr", "gpr_act", "gpr_threat", "n10d"]
GPR_SOURCE_COLUMNS: dict[str, str] = {
    "GPRD": "gpr",
    "GPRD_ACT": "gpr_act",
    "GPRD_THREAT": "gpr_threat",
    "N10D": "n10d",
}
GPR_EXTRA_COLUMNS: dict[str, str] = {"GPRD_MA7": "gpr_ma7", "GPRD_MA30": "gpr_ma30"}
GPR_PUBLISH_LAG_DAYS = 2
GPR_PUBLISH_HOUR_UTC = 12
GPR_PUBLISHED_AT_RULE = "date + 2 days 12:00 UTC"
GPR_FIRST_DAY = date(1985, 1, 1)

# --------------------------------------------------------------------------------------------------- GDELT files
GDELT_BASE = "https://data.gdeltproject.org/gdeltv2/"
GDELT_LASTUPDATE = GDELT_BASE + "lastupdate.txt"
GDELT_SLOT_MINUTES = 15
GDELT_SLOTS_PER_DAY = 96
GDELT_FILE_PACING = 0.2
GDELT_EXPORT_FIELDS = 61

# verified field positions (see module docstring)
POS_SQLDATE = 1
POS_ACTOR1_NAME = 6
POS_ACTOR1_COUNTRY = 7
POS_ACTOR2_NAME = 16
POS_ACTOR2_COUNTRY = 17
POS_EVENT_CODE = 26
POS_EVENT_ROOT = 28
POS_GOLDSTEIN = 30
POS_NUM_MENTIONS = 31
POS_NUM_SOURCES = 32
POS_NUM_ARTICLES = 33
POS_AVG_TONE = 34
POS_ACTION_GEO_COUNTRY = 53
POS_DATEADDED = 59
POS_SOURCEURL = 60

# CAMEO 3-letter actor country codes (the brief's set)
OIL_ACTOR_CODES: frozenset[str] = frozenset(
    {"IRN", "SAU", "ISR", "USA", "IRQ", "YEM", "ARE", "KWT", "QAT", "OMN", "BHR"}
)
# FIPS 10-4 two-letter codes used by ActionGeo_CountryCode for the Gulf / Middle East
GULF_GEO_CODES: frozenset[str] = frozenset(
    {
        "IR",  # Iran
        "IZ",  # Iraq
        "IS",  # Israel
        "SA",  # Saudi Arabia
        "YM",  # Yemen
        "AE",  # United Arab Emirates
        "KU",  # Kuwait
        "QA",  # Qatar
        "MU",  # Oman
        "BA",  # Bahrain
        "SY",  # Syria
        "LE",  # Lebanon
        "JO",  # Jordan
        "EG",  # Egypt
        "TU",  # Turkey
        "GZ",  # Gaza Strip
        "WE",  # West Bank
        "PG",  # Persian Gulf
    }
)
OIL_KEYWORDS = re.compile(r"hormuz|opec|tanker|oil", re.IGNORECASE)
CONFLICT_ROOT_CODES: frozenset[str] = frozenset(f"{i:02d}" for i in range(13, 21))

GDELT_COLUMNS = [
    "gdelt_volume",
    "gdelt_tone",
    "gdelt_goldstein",
    "gdelt_conflict_share",
    "gdelt_matched_events",
    "gdelt_total_events",
]
GDELT_PUBLISHED_AT_RULE = "file timestamp + 15 minutes"

# ------------------------------------------------------------------------------------------------- GDELT DOC API
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_DOC_MIN_INTERVAL = 6.0
GDELT_DOC_MODES = ("timelinevol", "timelinevolraw", "timelinetone", "timelinelang", "timelinesourcecountry")


def gpr_published_at(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """GPR publication rule: observation date + 2 days at 12:00 UTC."""
    naive = dates.tz_convert(None) if dates.tz is not None else dates
    shifted = pd.DatetimeIndex(naive).normalize() + pd.Timedelta(days=GPR_PUBLISH_LAG_DAYS, hours=GPR_PUBLISH_HOUR_UTC)
    return shifted.tz_localize(UTC)


def parse_gpr_workbook(content: bytes) -> pd.DataFrame:
    """Parse the GPR daily workbook into the engine frame (index ``date``, tz-naive midnight).

    Columns: ``gpr``, ``gpr_act``, ``gpr_threat``, ``n10d`` (+ ``gpr_ma7`` / ``gpr_ma30`` when present) and
    ``published_at``. Rows whose ``DAY`` is unparseable or whose ``GPRD`` is missing are dropped, never filled.
    """
    try:
        raw = pd.read_excel(io.BytesIO(content))
    except Exception as e:  # xlrd/openpyxl raise many error types on HTML error pages or truncated files
        raise DataUnavailable(f"GPR workbook: cannot read ({e})") from e
    if "DAY" not in raw.columns or "GPRD" not in raw.columns:
        raise DataUnavailable(f"GPR workbook: unexpected columns {list(raw.columns)[:12]}")
    day = pd.to_datetime(raw["DAY"].astype("string"), format="%Y%m%d", errors="coerce")
    data: dict[str, Any] = {}
    for src, col in {**GPR_SOURCE_COLUMNS, **GPR_EXTRA_COLUMNS}.items():
        if src in raw.columns:
            data[col] = pd.to_numeric(raw[src], errors="coerce").astype("float64").to_numpy()
    frame = pd.DataFrame(data, index=pd.DatetimeIndex(day, name="date"))
    frame = frame[frame.index.notna() & frame["gpr"].notna().to_numpy()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if frame.empty:
        raise DataUnavailable("GPR workbook: no usable rows")
    frame["published_at"] = gpr_published_at(pd.DatetimeIndex(frame.index))
    frame.attrs["units"] = "index (1985-2019 = 100)"
    return frame


class GprAdapter:
    """Daily Geopolitical Risk index (Caldara-Iacoviello), 1985 -> today."""

    name = "gpr"

    def __init__(self, client: HttpClient | None = None, url: str = GPR_URL):
        self.client = client or HttpClient(timeout=30.0, retries=3, backoff=2.0, min_interval=0.0)
        self.url = url

    def fetch(self, **kwargs: Any) -> FetchResult:
        url = str(kwargs.get("url") or self.url)
        content = self.client.get_bytes(url)
        frame = parse_gpr_workbook(content)
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={
                "url": url,
                "rows": len(frame),
                "first_day": frame.index[0].date().isoformat(),
                "last_day": frame.index[-1].date().isoformat(),
                "bytes": len(content),
                "published_at_rule": GPR_PUBLISHED_AT_RULE,
                "note": "the daily index is revised when new days are appended; each snapshot is a vintage",
            },
        )


# --------------------------------------------------------------------------------------------------------------
# GDELT raw event files
# --------------------------------------------------------------------------------------------------------------
def parse_lastupdate(text: str) -> dict[str, str]:
    """``lastupdate.txt`` -> ``{"export": url, "mentions": url, "gkg": url}`` with HTTPS URLs."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        url = parts[2].replace("http://", "https://", 1)
        for kind in ("export", "mentions", "gkg"):
            if f".{kind}." in url.lower():
                out[kind] = url
    if "export" not in out:
        raise DataUnavailable(f"GDELT lastupdate.txt: no export URL in {text[:200]!r}")
    return out


def slot_timestamp_from_url(url: str) -> datetime:
    """``.../20261005160000.export.CSV.zip`` -> the UTC slot timestamp."""
    m = re.search(r"(\d{14})\.(?:export|mentions|gkg)\.", url, re.IGNORECASE)
    if not m:
        raise DataUnavailable(f"GDELT: cannot read a slot timestamp from {url!r}")
    return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def day_slots(day: date) -> list[datetime]:
    """The 96 UTC 15-minute slot timestamps of a day."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [start + timedelta(minutes=GDELT_SLOT_MINUTES * i) for i in range(GDELT_SLOTS_PER_DAY)]


def export_url(slot: datetime, base: str = GDELT_BASE) -> str:
    return f"{base}{slot.astimezone(UTC).strftime('%Y%m%d%H%M%S')}.export.CSV.zip"


def _unzip_export(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise DataUnavailable("GDELT export zip: no CSV inside")
            return z.read(names[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as e:
        raise DataUnavailable(f"GDELT export: not a zip file ({e})") from e


def _num(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return float("nan")


def row_matches(fields: list[str]) -> bool:
    """The oil / Middle-East filter described in the module docstring."""
    actor_hit = fields[POS_ACTOR1_COUNTRY] in OIL_ACTOR_CODES or fields[POS_ACTOR2_COUNTRY] in OIL_ACTOR_CODES
    if actor_hit and fields[POS_ACTION_GEO_COUNTRY] in GULF_GEO_CODES:
        return True
    for pos in (POS_SOURCEURL, POS_ACTOR1_NAME, POS_ACTOR2_NAME):
        if fields[pos] and OIL_KEYWORDS.search(fields[pos]):
            return True
    return False


def aggregate_export_text(text: str, slot: datetime) -> dict[str, Any]:
    """One 15-minute export file -> the slot aggregate (see :data:`GDELT_COLUMNS`)."""
    total = 0
    matched = 0
    articles = 0.0
    tone_num = 0.0
    gold_num = 0.0
    weight = 0.0
    conflict = 0
    for line in text.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != GDELT_EXPORT_FIELDS:
            log.warning("GDELT %s: skipping row with %d fields (expected %d)", slot, len(fields), GDELT_EXPORT_FIELDS)
            continue
        total += 1
        if not row_matches(fields):
            continue
        matched += 1
        n = _num(fields[POS_NUM_ARTICLES])
        tone = _num(fields[POS_AVG_TONE])
        gold = _num(fields[POS_GOLDSTEIN])
        if np.isfinite(n):
            articles += n
            if np.isfinite(tone):
                tone_num += n * tone
            if np.isfinite(gold):
                gold_num += n * gold
            if np.isfinite(tone) or np.isfinite(gold):
                weight += n
        if fields[POS_EVENT_ROOT] in CONFLICT_ROOT_CODES:
            conflict += 1
    return {
        "gdelt_volume": articles,
        "gdelt_tone": tone_num / weight if weight > 0 else np.nan,
        "gdelt_goldstein": gold_num / weight if weight > 0 else np.nan,
        "gdelt_conflict_share": conflict / matched if matched else np.nan,
        "gdelt_matched_events": float(matched),
        "gdelt_total_events": float(total),
    }


def _slot_frame(rows: list[dict[str, Any]], slots: list[datetime]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(s) for s in slots], name="slot").tz_convert(UTC)
    frame = pd.DataFrame(rows, index=idx, columns=GDELT_COLUMNS).astype("float64")
    frame["published_at"] = idx + pd.Timedelta(minutes=GDELT_SLOT_MINUTES)
    return frame.sort_index()


def aggregate_rows(frame_15min: pd.DataFrame) -> pd.DataFrame:
    """15-minute slot aggregates -> one row per UTC calendar day.

    Volumes and event counts are summed; tone and Goldstein are re-weighted by ``gdelt_volume``; the conflict
    share is re-weighted by ``gdelt_matched_events``. ``published_at`` = 00:00 UTC of the next day (the day is
    complete only then). Days with no matching event keep NaN for tone/Goldstein/share: nothing is invented.
    """
    if frame_15min.empty:
        return pd.DataFrame(columns=[*GDELT_COLUMNS, "published_at"], index=pd.DatetimeIndex([], name="date")).astype(
            dict.fromkeys(GDELT_COLUMNS, "float64")
        )
    df = frame_15min.copy()
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize(UTC)
    day = pd.DatetimeIndex(idx.tz_convert(UTC).normalize().tz_localize(None), name="date")
    vol = pd.to_numeric(df["gdelt_volume"], errors="coerce").astype("float64")
    ev = pd.to_numeric(df["gdelt_matched_events"], errors="coerce").astype("float64")
    work = pd.DataFrame(
        {
            "day": day,
            "volume": vol,
            "events": ev,
            "total": pd.to_numeric(df["gdelt_total_events"], errors="coerce").astype("float64"),
            "tone_w": vol * pd.to_numeric(df["gdelt_tone"], errors="coerce").astype("float64"),
            "gold_w": vol * pd.to_numeric(df["gdelt_goldstein"], errors="coerce").astype("float64"),
            "conf_w": ev * pd.to_numeric(df["gdelt_conflict_share"], errors="coerce").astype("float64"),
        }
    )
    g = work.groupby("day", sort=True)
    vol_sum = g["volume"].sum(min_count=1)
    ev_sum = g["events"].sum(min_count=1)
    tone_w = g["tone_w"].sum(min_count=1)
    gold_w = g["gold_w"].sum(min_count=1)
    conf_w = g["conf_w"].sum(min_count=1)
    out = pd.DataFrame(
        {
            "gdelt_volume": vol_sum,
            "gdelt_tone": tone_w.where(vol_sum > 0) / vol_sum.where(vol_sum > 0),
            "gdelt_goldstein": gold_w.where(vol_sum > 0) / vol_sum.where(vol_sum > 0),
            "gdelt_conflict_share": conf_w.where(ev_sum > 0) / ev_sum.where(ev_sum > 0),
            "gdelt_matched_events": ev_sum,
            "gdelt_total_events": g["total"].sum(min_count=1),
        }
    )
    out.index = pd.DatetimeIndex(out.index, name="date")
    out["published_at"] = pd.DatetimeIndex(out.index).tz_localize(UTC) + pd.Timedelta(days=1)
    return out


class GdeltFilesAdapter:
    """GDELT 2.0 raw ``export`` files: the 15-minute slot aggregates and their daily roll-up."""

    name = "gdelt_files"

    def __init__(self, client: HttpClient | None = None, base: str = GDELT_BASE):
        self.client = client or HttpClient(timeout=30.0, retries=2, backoff=1.5, min_interval=GDELT_FILE_PACING)
        self.base = base if base.endswith("/") else base + "/"

    # ---- public API -------------------------------------------------------------------------------------------
    def fetch(self, **kwargs: Any) -> FetchResult:
        """Adapter protocol entry point: ``fetch()`` = :meth:`fetch_latest`, ``fetch(day=...)`` = :meth:`fetch_day`."""
        day = kwargs.get("day")
        if day is not None:
            return self.fetch_day(day if isinstance(day, date) else date.fromisoformat(str(day)))
        return self.fetch_latest()

    def fetch_latest(self) -> FetchResult:
        """The slot named by ``lastupdate.txt``: one row, index = slot timestamp (UTC)."""
        text = self.client.get(GDELT_LASTUPDATE.replace(GDELT_BASE, self.base)).text
        urls = parse_lastupdate(text)
        url = urls["export"]
        slot = slot_timestamp_from_url(url)
        agg = aggregate_export_text(_unzip_export(self.client.get_bytes(url)), slot)
        frame = _slot_frame([agg], [slot])
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={
                "url": url,
                "lastupdate": urls,
                "slot": slot.isoformat().replace("+00:00", "Z"),
                "slots_fetched": 1,
                "slots_missing": [],
                "published_at_rule": GDELT_PUBLISHED_AT_RULE,
            },
        )

    def fetch_day(self, day: date) -> FetchResult:
        """All available 15-minute slots of ``day`` (up to 96, paced, missing slots tolerated)."""
        rows: list[dict[str, Any]] = []
        slots: list[datetime] = []
        missing: list[str] = []
        for slot in day_slots(day):
            url = export_url(slot, self.base)
            try:
                content = self.client.get_bytes(url)
            except DataUnavailable as e:
                missing.append(slot.strftime("%H%M%S"))
                log.info("GDELT %s: slot missing (%s)", url, e)
                continue
            try:
                rows.append(aggregate_export_text(_unzip_export(content), slot))
            except DataUnavailable as e:
                missing.append(slot.strftime("%H%M%S"))
                log.warning("GDELT %s: unreadable (%s)", url, e)
                continue
            slots.append(slot)
        if not rows:
            raise DataUnavailable(f"GDELT: no readable export slot on {day.isoformat()} ({len(missing)} missing)")
        frame = _slot_frame(rows, slots)
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={
                "day": day.isoformat(),
                "slots_fetched": len(rows),
                "slots_missing": missing,
                "published_at_rule": GDELT_PUBLISHED_AT_RULE,
            },
        )

    @staticmethod
    def aggregate_rows(frame_15min: pd.DataFrame) -> pd.DataFrame:
        """See :func:`aggregate_rows` (exposed on the adapter for convenience)."""
        return aggregate_rows(frame_15min)


# --------------------------------------------------------------------------------------------------------------
# GDELT DOC API
# --------------------------------------------------------------------------------------------------------------
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "value"


def parse_doc_timeline(payload: Any, mode: str) -> pd.DataFrame:
    """DOC API timeline JSON -> frame indexed by UTC timestamp, one float column per series.

    Shape (documented by GDELT, reproduced in the hand-written fixture): ``{"timeline": [{"series": "<name>",
    "data": [{"date": "20260901T000000Z", "value": 0.012, "norm": 1234}, ...]}, ...]}``. ``published_at`` = the
    bucket timestamp + 1 day (a daily bucket is complete only at the end of the day).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("timeline"), list):
        raise DataUnavailable(f"GDELT DOC API ({mode}): no 'timeline' array in the reply")
    series_frames: list[pd.Series] = []
    for entry in payload["timeline"]:
        if not isinstance(entry, dict):
            continue
        name = _slug(str(entry.get("series", "value")))
        points = entry.get("data") or []
        stamps: list[pd.Timestamp] = []
        values: list[float] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            ts = pd.to_datetime(str(point.get("date")), format="%Y%m%dT%H%M%SZ", errors="coerce", utc=True)
            if pd.isna(ts):
                continue
            stamps.append(ts)
            values.append(_num(str(point.get("value"))))
        if stamps:
            series_frames.append(pd.Series(values, index=pd.DatetimeIndex(stamps), name=name, dtype="float64"))
    if not series_frames:
        raise DataUnavailable(f"GDELT DOC API ({mode}): timeline carries no usable point")
    frame = pd.concat(series_frames, axis=1).sort_index()
    frame.index = pd.DatetimeIndex(frame.index, name="ts")
    frame = frame[~frame.index.duplicated(keep="last")]
    frame["published_at"] = pd.DatetimeIndex(frame.index) + pd.Timedelta(days=1)
    return frame


class GdeltDocApiAdapter:
    """GDELT DOC 2.0 timelines. Rate-limited to one call per 5 s per IP; 429 is not retried more than once."""

    name = "gdelt_doc_api"

    def __init__(
        self,
        client: HttpClient | None = None,
        url: str = GDELT_DOC_URL,
        min_interval: float = GDELT_DOC_MIN_INTERVAL,
    ):
        self.client = client or HttpClient(timeout=30.0, retries=0, backoff=1.0, min_interval=min_interval)
        self.url = url
        self.min_interval = min_interval
        self._last_call = 0.0
        self.attempts = 0

    def fetch(self, **kwargs: Any) -> FetchResult:
        """Adapter protocol entry point: ``fetch(query=..., mode=..., timespan=...)``."""
        return self.fetch_timeline(
            str(kwargs.get("query", "oil hormuz")),
            mode=str(kwargs.get("mode", "timelinevol")),
            timespan=str(kwargs.get("timespan", "3months")),
        )

    def fetch_timeline(self, query: str, mode: str = "timelinevol", timespan: str = "3months") -> FetchResult:
        """One timeline call. Raises :class:`DataUnavailable` on 429 after at most one extra attempt."""
        if mode not in GDELT_DOC_MODES:
            raise ValueError(f"unsupported GDELT DOC mode {mode!r}; use one of {GDELT_DOC_MODES}")
        params = {"query": query, "mode": mode, "format": "json", "timespan": timespan}
        last: Exception | None = None
        for attempt in range(2):  # one retry at most: the limit is per IP and will not clear quickly
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            self.attempts += 1
            try:
                response = self.client.get(self.url, params=params)
            except DataUnavailable as e:
                last = e
                log.warning("GDELT DOC API %s (%s): %s", mode, query, e)
                if attempt == 0:
                    continue
                break
            try:
                payload = response.json()
            except ValueError as e:
                raise DataUnavailable(
                    f"GDELT DOC API ({mode}): non-JSON body {response.text[:160]!r} (rate limit?)"
                ) from e
            frame = parse_doc_timeline(payload, mode)
            return FetchResult(
                source=self.name,
                frame=frame,
                fetched_at=utc_now(),
                meta={
                    "url": self.url,
                    "query": query,
                    "mode": mode,
                    "timespan": timespan,
                    "rows": len(frame),
                    "attempts": self.attempts,
                    "published_at_rule": "bucket timestamp + 1 day",
                },
                approx=True,
            )
        raise DataUnavailable(f"GDELT DOC API ({mode}, {query!r}): {last}")
