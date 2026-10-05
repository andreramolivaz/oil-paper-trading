"""Commitments of Traders adapters: CFTC Socrata API, CFTC current-week text file, ICE Futures Europe yearly CSVs.

All three return the same frame (index ``period`` = report date, tz-naive midnight Timestamp, normally a Tuesday):

======================  =======================================================================================
column                  meaning (contracts; WTI/Brent = 1 000 bbl, Gasoil = 100 tonnes)
======================  =======================================================================================
market                  label: ``wti`` (CFTC 067651), ``brent`` / ``gasoil`` (ICE)
oi                      open interest, all
mm_long / mm_short      managed money long / short
mm_net                  ``mm_long - mm_short``
prod_long / prod_short  producer/merchant/processor/user long / short
swap_long / swap_short  swap dealers long / short
published_at            UTC release timestamp (see below)
======================  =======================================================================================

Sources verified on 2026-10-05 (fixtures and real values in ``tests/fixtures/cot/README.md``):

* **CFTC Socrata** ``https://publicreporting.cftc.gov/resource/72hh-3qpy.json`` ("Disaggregated - Futures Only",
  194 fields, no key required). Field names checked against the dataset's ``/api/views`` column list: the
  producer/merchant fields are ``prod_merc_positions_long`` / ``prod_merc_positions_short`` (**no** ``_all``
  suffix, unlike every other group), swap dealer shorts are ``swap__positions_short_all`` (double underscore),
  managed money is ``m_money_positions_long_all`` / ``m_money_positions_short_all``. Rows are paged with
  ``$limit``/``$offset`` ordered by ``report_date_as_yyyy_mm_dd`` ascending and narrowed with ``$select`` to the
  fields used here. Contract code ``067651`` is NYMEX WTI: 1 060 weekly rows from 2006-06-13, labelled
  ``CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE`` through 2022-02-01 and ``WTI-PHYSICAL - NEW YORK
  MERCANTILE EXCHANGE`` from 2022-02-08 (consecutive Tuesdays, one row per week, no gap or overlap: the two
  labels are the same contract and are merged under the single label ``wti``).
* **CFTC current file** ``https://www.cftc.gov/dea/newcot/f_disagg.txt``: header-less CSV, 191 fields, one row per
  market for the latest week only. Positions were verified against the header row of
  ``files/dea/history/fut_disagg_txt_2026.zip`` and, value by value, against the Socrata row of the same date:
  0 name, 1 ``As_of_Date_In_Form_YYMMDD``, 2 ``Report_Date_as_YYYY-MM-DD``, 3 contract code, 7 ``Open_Interest_All``,
  8/9 ``Prod_Merc_Positions_Long/Short_All``, 10/11 ``Swap_Positions_Long/Short_All``, 13/14
  ``M_Money_Positions_Long/Short_All``, 190 ``FutOnly_or_Combined``.
* **ICE** ``https://www.ice.com/publicdocs/futures/COTHist{YYYY}.csv``, one file per year from **2011** (2010 ->
  404). Same CFTC disaggregated layout with a header row; quirks handled here: UTF-8 BOM on some years (2023,
  2026), ``Swap__Positions_Short_All`` spelt with a double underscore through 2020, a trailing empty column in
  2018, and - crucially - from 2011 until 2013-03-05 the futures-and-options *combined* report is filed under the
  same ``Market_and_Exchange_Names`` as the futures-only one ("ICE Brent Crude Futures - ICE Futures Europe"),
  so rows are selected on the name **and** ``FutOnly_or_Combined == "FutOnly"``. The "... Futures and Options -
  ICE Futures Europe" label exists from 2013-03-12 onwards and is never used here.

``published_at`` (UTC): the CFTC releases both reports at 15:30 America/New_York on the Friday after the report
date, and ICE publishes its own COT in line with the CFTC calendar. The official 2026 schedule
(cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule) shows that a federal holiday on the Wednesday,
Thursday or Friday of the release week moves the release to the following Monday (Jan 1 Thu -> Jan 5, Jun 19
Fri -> Jun 22, Jul 3 Fri -> Jul 6, Nov 11 Wed -> Nov 16, Nov 26 Thu -> Nov 30, Dec 25 Fri -> Dec 28), while a
Monday or Tuesday holiday does not delay it (the report date itself moves to Monday when Tuesday is a holiday,
e.g. 2023-07-03, 2025-11-10). :func:`cot_release_day` implements exactly that. Holidays come from
:func:`engine.core.calendar.us_holidays` adjusted to the federal calendar the CFTC follows: Good Friday removed
(not a federal holiday; the 2026 schedule lists April 3 as a regular release) and Veterans Day added (federal,
not NYSE). The 15:30 New York -> UTC conversion is :func:`engine.core.timeutil.cot_release_ts`. Not modelled:
US government shutdowns (Oct 2013, Dec 2018-Jan 2019, Oct-Nov 2025) delayed releases by weeks; those weeks
carry the scheduled timestamp, which is earlier than the real one.

Nothing here invents values: rows with a missing numeric field are dropped and listed in ``meta["dropped"]``
(a warning is logged); empty results, duplicate periods and transport failures raise
:class:`engine.core.errors.DataUnavailable`.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from engine.core.calendar import _easter, us_holidays
from engine.core.errors import DataUnavailable
from engine.core.timeutil import cot_release_ts
from engine.data.base import FetchResult, HttpClient, utc_now
from engine.data.market_data import COT_COLUMNS

log = logging.getLogger(__name__)

# Frame columns in contract order: COT_COLUMNS without the period (which is the index) plus published_at.
COT_VALUE_COLUMNS: list[str] = [c for c in COT_COLUMNS if c != "period"]
COT_FRAME_COLUMNS: list[str] = [*COT_VALUE_COLUMNS, "published_at"]
COT_NUMERIC_COLUMNS: list[str] = [c for c in COT_VALUE_COLUMNS if c != "market"]
UNITS = "contracts"

# --- CFTC Socrata ---------------------------------------------------------------------------------------------
SOCRATA_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
SOCRATA_PAGE_SIZE = 50000
SOCRATA_MAX_PAGES = 20  # safety stop (WTI: 1 060 rows = one page)
SOCRATA_DATE_FIELD = "report_date_as_yyyy_mm_dd"
SOCRATA_CODE_FIELD = "cftc_contract_market_code"
SOCRATA_NAME_FIELD = "market_and_exchange_names"
SOCRATA_CONTRACT_FIELD = "contract_market_name"
# frame column -> Socrata field (names verified on the live dataset, see module docstring)
SOCRATA_FIELDS: dict[str, str] = {
    "oi": "open_interest_all",
    "mm_long": "m_money_positions_long_all",
    "mm_short": "m_money_positions_short_all",
    "prod_long": "prod_merc_positions_long",
    "prod_short": "prod_merc_positions_short",
    "swap_long": "swap_positions_long_all",
    "swap_short": "swap__positions_short_all",
}
SOCRATA_SELECT: list[str] = [
    SOCRATA_DATE_FIELD,
    SOCRATA_CODE_FIELD,
    SOCRATA_NAME_FIELD,
    SOCRATA_CONTRACT_FIELD,
    *SOCRATA_FIELDS.values(),
]
WTI_CODE = "067651"
CFTC_MARKET_LABELS: dict[str, str] = {WTI_CODE: "wti"}

# --- CFTC current-week text file ------------------------------------------------------------------------------
CFTC_DISAGG_TXT_URL = "https://www.cftc.gov/dea/newcot/f_disagg.txt"
# 0-based field positions of the header-less f_disagg.txt (verified, see module docstring)
DISAGG_TXT_POS: dict[str, int] = {
    "name": 0,
    "yymmdd": 1,
    "date": 2,
    "code": 3,
    "oi": 7,
    "prod_long": 8,
    "prod_short": 9,
    "swap_long": 10,
    "swap_short": 11,
    "mm_long": 13,
    "mm_short": 14,
    "futonly": 190,
}
DISAGG_TXT_MIN_FIELDS = 15

# --- ICE ------------------------------------------------------------------------------------------------------
ICE_URL_TEMPLATE = "https://www.ice.com/publicdocs/futures/COTHist{year}.csv"
ICE_FIRST_YEAR = 2011
ICE_PACING_SECONDS = 0.5
ICE_MARKETS: dict[str, str] = {
    "brent": "ICE Brent Crude Futures - ICE Futures Europe",
    "gasoil": "ICE Gasoil Futures - ICE Futures Europe",
}
ICE_NAME_COL = "Market_and_Exchange_Names"
ICE_DATE_COL = "As_of_Date_In_Form_YYMMDD"
ICE_DATE_ALT_COL = "As_of_Date_Form_MM/DD/YYYY"
ICE_KIND_COL = "FutOnly_or_Combined"
ICE_FUTONLY = "FutOnly"
# frame column -> normalised ICE header (underscore runs collapsed, see _normalise_header)
ICE_FIELDS: dict[str, str] = {
    "oi": "Open_Interest_All",
    "mm_long": "M_Money_Positions_Long_All",
    "mm_short": "M_Money_Positions_Short_All",
    "prod_long": "Prod_Merc_Positions_Long_All",
    "prod_short": "Prod_Merc_Positions_Short_All",
    "swap_long": "Swap_Positions_Long_All",
    "swap_short": "Swap_Positions_Short_All",
}


# --------------------------------------------------------------------------------------------------------------
# published_at rules (pure functions, unit-tested against the official 2026 release schedule)
# --------------------------------------------------------------------------------------------------------------
def cftc_holidays(year: int) -> frozenset[date]:
    """US federal holidays as observed by the CFTC: NYSE-style ``us_holidays`` minus Good Friday plus Veterans Day."""
    hols = set(us_holidays(year))
    hols.discard(_easter(year) - timedelta(days=2))
    veterans = date(year, 11, 11)
    if veterans.weekday() == 5:
        veterans -= timedelta(days=1)
    elif veterans.weekday() == 6:
        veterans += timedelta(days=1)
    hols.add(veterans)
    return frozenset(hols)


def cot_release_day(period: date) -> date:
    """Release date of the COT report whose data are as of ``period`` (normally a Tuesday).

    The Friday of the report week, or the following Monday when a federal holiday falls on the Wednesday,
    Thursday or Friday of that week (then the next business day if that Monday is itself a holiday).
    """
    friday = period + timedelta(days=(4 - period.weekday()) % 7)
    hols = cftc_holidays(friday.year) | cftc_holidays(friday.year + 1)
    release = friday
    if any(friday - timedelta(days=k) in hols for k in range(3)):  # Wed, Thu, Fri
        release = friday + timedelta(days=3)
    while release.weekday() >= 5 or release in hols:
        release += timedelta(days=1)
    return release


def cot_published_at(period: date) -> datetime:
    """UTC timestamp of the COT release (15:30 America/New_York) for the report date ``period``."""
    return cot_release_ts(cot_release_day(period))


# --------------------------------------------------------------------------------------------------------------
# frame assembly
# --------------------------------------------------------------------------------------------------------------
def _slug(name: str) -> str:
    """'BRENT LAST DAY - NEW YORK MERCANTILE EXCHANGE' -> 'brent_last_day'."""
    head = name.split(" - ", maxsplit=1)[0]
    return re.sub(r"[^a-z0-9]+", "_", head.lower()).strip("_")


def _build_frame(records: pd.DataFrame, market: str, what: str) -> tuple[pd.DataFrame, list[str]]:
    """Long records (columns: period + numeric COT fields, any dtype) -> contract frame; returns dropped periods."""
    if records.empty:
        raise DataUnavailable(f"{what}: no rows")
    period = pd.to_datetime(records["period"], errors="coerce")
    if getattr(period.dt, "tz", None) is not None:
        period = period.dt.tz_convert(None)
    period = period.dt.normalize()
    numeric = pd.DataFrame(
        {c: pd.to_numeric(records[c], errors="coerce") for c in SOCRATA_FIELDS},
        index=records.index,
    )
    bad = period.isna() | numeric.isna().any(axis=1)
    dropped = sorted({str(p.date()) if pd.notna(p) else "?" for p in period[bad]})
    if dropped:
        log.warning("%s: dropping %d row(s) with missing fields: %s", what, len(dropped), dropped)
    period = period[~bad]
    numeric = numeric[~bad]
    if numeric.empty:
        raise DataUnavailable(f"{what}: every row had a missing period or value")
    idx = pd.DatetimeIndex(period.to_numpy(), name="period")
    if not idx.is_unique:
        dups = sorted({str(d.date()) for d in idx[idx.duplicated()]})
        raise DataUnavailable(f"{what}: duplicate report dates {dups}")
    out = pd.DataFrame(index=idx)
    out["market"] = market
    for c in COT_NUMERIC_COLUMNS:
        if c == "mm_net":
            continue
        out[c] = numeric[c].to_numpy().astype("int64")
    out["mm_net"] = out["mm_long"] - out["mm_short"]
    out["published_at"] = pd.to_datetime([cot_published_at(d.date()) for d in idx], utc=True)
    out = out[COT_FRAME_COLUMNS].sort_index()
    out.attrs["units"] = UNITS
    return out, dropped


def _result(name: str, frame: pd.DataFrame, meta: dict[str, Any]) -> FetchResult:
    meta = {
        **meta,
        "rows": len(frame),
        "first": frame.index.min().date().isoformat(),
        "last": frame.index.max().date().isoformat(),
    }
    return FetchResult(source=name, frame=frame, fetched_at=utc_now(), meta=meta, approx=False)


def _is_not_found(exc: Exception) -> bool:
    return "404" in str(exc)


# --------------------------------------------------------------------------------------------------------------
# CFTC Socrata
# --------------------------------------------------------------------------------------------------------------
class CftcAdapter:
    """Disaggregated futures-only COT history from the CFTC Public Reporting Environment (Socrata, no key)."""

    name = "cftc_socrata"

    def __init__(
        self,
        client: HttpClient | None = None,
        page_size: int = SOCRATA_PAGE_SIZE,
        max_pages: int = SOCRATA_MAX_PAGES,
    ):
        self.client = client or HttpClient(timeout=30.0, retries=3, min_interval=0.5)
        self.page_size = page_size
        self.max_pages = max_pages

    def fetch(self, market_code: str = WTI_CODE) -> FetchResult:
        rows: list[dict[str, Any]] = []
        pages = 0
        for page in range(self.max_pages):
            params = {
                "$select": ",".join(SOCRATA_SELECT),
                "$where": f"{SOCRATA_CODE_FIELD}='{market_code}'",
                "$order": SOCRATA_DATE_FIELD,
                "$limit": self.page_size,
                "$offset": page * self.page_size,
            }
            payload = self.client.get_json(SOCRATA_URL, params=params)
            if not isinstance(payload, list):
                raise DataUnavailable(f"CFTC Socrata {market_code}: unexpected payload {type(payload).__name__}")
            pages += 1
            rows.extend(payload)
            if len(payload) < self.page_size:
                break
        else:
            raise DataUnavailable(f"CFTC Socrata {market_code}: more than {self.max_pages} pages, giving up")
        if not rows:
            raise DataUnavailable(f"CFTC Socrata {market_code}: no rows for this contract code")
        df = pd.DataFrame(rows)
        missing = {SOCRATA_DATE_FIELD, *SOCRATA_FIELDS.values()} - set(df.columns)
        if missing:
            raise DataUnavailable(f"CFTC Socrata {market_code}: rows lack fields {sorted(missing)}")
        records = pd.DataFrame({"period": df[SOCRATA_DATE_FIELD]})
        for col, field in SOCRATA_FIELDS.items():
            records[col] = df[field]
        names = sorted(df[SOCRATA_NAME_FIELD].dropna().astype(str).unique()) if SOCRATA_NAME_FIELD in df else []
        market = CFTC_MARKET_LABELS.get(market_code) or _slug(names[-1] if names else market_code)
        frame, dropped = _build_frame(records, market, f"CFTC Socrata {market_code}")
        meta = {"market_code": market_code, "market": market, "pages": pages, "names": names, "dropped": dropped}
        return _result(self.name, frame, meta)


# --------------------------------------------------------------------------------------------------------------
# CFTC current-week text file
# --------------------------------------------------------------------------------------------------------------
def parse_disagg_txt(text: str, market_code: str) -> pd.DataFrame:
    """Rows of ``f_disagg.txt`` for one contract code -> long records (period + numeric fields as strings)."""
    recs: list[dict[str, Any]] = []
    for fields in csv.reader(io.StringIO(text)):
        if len(fields) < DISAGG_TXT_MIN_FIELDS or fields[DISAGG_TXT_POS["code"]].strip() != market_code:
            continue
        kind_pos = DISAGG_TXT_POS["futonly"]
        if len(fields) > kind_pos and fields[kind_pos].strip() not in ("", ICE_FUTONLY):
            continue
        rec: dict[str, Any] = {
            "name": fields[DISAGG_TXT_POS["name"]].strip(),
            "period": fields[DISAGG_TXT_POS["date"]].strip(),
        }
        for col in SOCRATA_FIELDS:
            rec[col] = fields[DISAGG_TXT_POS[col]].strip()
        recs.append(rec)
    return pd.DataFrame(recs)


class CftcFileAdapter:
    """Latest-week disaggregated futures-only report from the CFTC text file (fallback, no history)."""

    name = "cftc_file"

    def __init__(self, client: HttpClient | None = None, url: str = CFTC_DISAGG_TXT_URL):
        self.client = client or HttpClient(timeout=30.0, retries=3)
        self.url = url

    def fetch_current(self, market_code: str = WTI_CODE) -> FetchResult:
        raw = self.client.get_bytes(self.url)
        text = raw.decode("utf-8-sig", errors="replace")
        records = parse_disagg_txt(text, market_code)
        if records.empty:
            raise DataUnavailable(f"CFTC f_disagg.txt: no futures-only row for contract code {market_code}")
        names = sorted(records["name"].astype(str).unique())
        market = CFTC_MARKET_LABELS.get(market_code) or _slug(names[-1])
        frame, dropped = _build_frame(records, market, f"CFTC f_disagg.txt {market_code}")
        meta = {"market_code": market_code, "market": market, "names": names, "dropped": dropped, "url": self.url}
        return _result(self.name, frame, meta)


# --------------------------------------------------------------------------------------------------------------
# ICE Futures Europe
# --------------------------------------------------------------------------------------------------------------
def _normalise_header(name: str) -> str:
    """'Swap__Positions_Short_All' (2011-2020 spelling) -> 'Swap_Positions_Short_All'."""
    return re.sub(r"_+", "_", name.strip().lstrip("﻿"))


def _parse_ice_date(yymmdd: str, mmddyyyy: str) -> date | None:
    for raw, fmt in ((yymmdd, "%y%m%d"), (mmddyyyy, "%m/%d/%Y")):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_ice_cot_csv(text: str, market_name: str) -> tuple[pd.DataFrame, set[str]]:
    """One ``COTHist{YYYY}.csv`` -> futures-only long records for ``market_name`` + every market name seen."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [_normalise_header(h) for h in next(reader)]
    except StopIteration as e:
        raise DataUnavailable("ICE COT csv: empty file") from e
    pos = {h: i for i, h in enumerate(header)}
    required = [ICE_NAME_COL, ICE_DATE_COL, *ICE_FIELDS.values()]
    missing = [c for c in required if c not in pos]
    if missing:
        raise DataUnavailable(f"ICE COT csv: header lacks columns {missing}")
    kind_pos = pos.get(ICE_KIND_COL)
    alt_pos = pos.get(ICE_DATE_ALT_COL)
    seen: set[str] = set()
    recs: list[dict[str, Any]] = []
    for fields in reader:
        if len(fields) <= pos[ICE_NAME_COL]:
            continue
        name = fields[pos[ICE_NAME_COL]].strip()
        seen.add(name)
        if name != market_name:
            continue
        if kind_pos is not None and kind_pos < len(fields) and fields[kind_pos].strip() != ICE_FUTONLY:
            continue
        alt = fields[alt_pos] if alt_pos is not None and alt_pos < len(fields) else ""
        rec: dict[str, Any] = {"period": _parse_ice_date(fields[pos[ICE_DATE_COL]], alt)}
        for col, hdr in ICE_FIELDS.items():
            i = pos[hdr]
            rec[col] = fields[i].strip() if i < len(fields) else None
        recs.append(rec)
    return pd.DataFrame(recs), seen


class IceCotAdapter:
    """ICE Futures Europe COT history (Brent, Gasoil): one CSV per year, downloaded sequentially with pacing."""

    name = "ice_cot"

    def __init__(
        self,
        client: HttpClient | None = None,
        today: Callable[[], date] | None = None,
        url_template: str = ICE_URL_TEMPLATE,
    ):
        self.client = client or HttpClient(timeout=30.0, retries=3, min_interval=ICE_PACING_SECONDS)
        self._today = today or (lambda: utc_now().date())
        self.url_template = url_template

    @staticmethod
    def resolve_market(market_name: str) -> tuple[str, str]:
        """'brent' | 'gasoil' | full ICE label -> (label, full ``Market_and_Exchange_Names``)."""
        key = market_name.strip().lower()
        if key in ICE_MARKETS:
            return key, ICE_MARKETS[key]
        for label, full in ICE_MARKETS.items():
            if market_name.strip() == full:
                return label, full
        return _slug(market_name), market_name.strip()

    def _download_year(self, year: int) -> str | None:
        url = self.url_template.format(year=year)
        try:
            raw = self.client.get_bytes(url)
        except DataUnavailable as e:
            if _is_not_found(e):
                log.info("ICE COT %s not found (404)", url)
                return None
            raise
        return raw.decode("utf-8-sig", errors="replace")

    def fetch(self, market_name: str, first_year: int = ICE_FIRST_YEAR) -> FetchResult:
        label, full_name = self.resolve_market(market_name)
        last_year = self._today().year
        if first_year > last_year:
            raise DataUnavailable(f"ICE COT: first_year {first_year} is after the current year {last_year}")
        parts: list[pd.DataFrame] = []
        years: list[int] = []
        missing_years: list[int] = []
        names_seen: set[str] = set()
        for year in range(first_year, last_year + 1):
            text = self._download_year(year)
            if text is None:
                missing_years.append(year)
                continue
            recs, seen = parse_ice_cot_csv(text, full_name)
            names_seen |= seen
            if not recs.empty:
                parts.append(recs)
                years.append(year)
        if not parts:
            hint = sorted(n for n in names_seen if "Brent" in n or "Gasoil" in n) or sorted(names_seen)[:10]
            raise DataUnavailable(
                f"ICE COT: no futures-only rows for {full_name!r} in {first_year}-{last_year} "
                f"(missing files: {missing_years}; names seen include {hint})"
            )
        records = pd.concat(parts, ignore_index=True)
        frame, dropped = _build_frame(records, label, f"ICE COT {label}")
        meta = {
            "market": label,
            "market_name": full_name,
            "years": years,
            "missing_years": missing_years,
            "dropped": dropped,
        }
        return _result(self.name, frame, meta)
