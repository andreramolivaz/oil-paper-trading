"""Yahoo Finance chart API adapter (unofficial, quotes delayed ~10-15 minutes).

Endpoint: ``https://query1.finance.yahoo.com/v8/finance/chart/{symbol}``.

Behaviour verified against the live endpoint on 2026-10-05 (snapshots in ``tests/fixtures/yahoo/``):

* ``period1``/``period2`` (unix seconds) with ``interval=1d`` return daily bars. ``range=max`` with ``interval=1d``
  silently degrades to MONTHLY bars, so this adapter always sends explicit ``period1``/``period2`` and splits long
  spans into chunks of at most 10 years (a 19-year BZ=F span works in one call, chunking is a safety margin).
* Daily bar timestamps are the SESSION START in the exchange time zone (``meta.exchangeTimezoneName``):
  NYM futures 04:00/05:00 UTC = 00:00 America/New_York, CBOE indices 13:30 UTC = 09:30 New York. The trading date
  is the local calendar date of that timestamp.
* Quote arrays contain ``null`` entries (holidays, no trade: 58 of 4 831 BZ=F rows since 2007-08-01). Rows whose
  close is null are DROPPED, never filled.
* When the window includes "now", Yahoo appends the live quote as a trailing row stamped ``meta.regularMarketTime``
  (off the bar grid, volume 0, open=high=low=close). It is not a bar and is dropped.
* Intraday history must be requested with ``range={n}d``: ``period1``/``period2`` at the 730-day edge answer
  HTTP 422. Limits: 1h x 730 d, 30m/15m/5m x 60 d, 1m x 7 d.
* An expired/unknown contract answers HTTP 404 with ``chart.error.code == "Not Found"``; a window before the first
  trade answers HTTP 400 ``"Data doesn't exist for startDate = ..."``. Neither is retried.
* Bursts of requests return empty bodies: requests are paced >= 1 s apart and an empty body is retried.

``published_at`` (UTC) is the earliest moment the datum was knowable:

* daily bars: 23:00 Europe/London of the trading date (ICE Brent close; the daily close is final only then);
* intraday bars: bar end = bar start + interval.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import requests

from engine.core.calendar import listed_months
from engine.core.errors import DataUnavailable
from engine.core.instruments import Future
from engine.core.timeutil import LONDON, NEW_YORK
from engine.data.base import FetchResult, HttpClient, utc_now

log = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DEFAULT_START = date(1980, 1, 1)  # earlier than every symbol we use (DX-Y.NYB starts 1985); trimmed by firstTradeDate
CHUNK_DAYS = 3652  # <= 10 years per request
DAILY_PUBLISH_LONDON = dtime(23, 0)  # ICE Brent close
DAILY_SECONDS = 86_400
INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "90m": 5400,
    "1h": 3600,
}
INTRADAY_MAX_DAYS: dict[str, int] = {
    "1m": 7,
    "2m": 60,
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "60m": 730,
    "90m": 60,
    "1h": 730,
}
QUOTE_FIELDS = ("open", "high", "low", "close", "volume")
DAILY_COLUMNS = ["open", "high", "low", "close", "volume", "open_interest"]
META_KEYS = (
    "symbol",
    "currency",
    "exchangeName",
    "fullExchangeName",
    "exchangeTimezoneName",
    "instrumentType",
    "firstTradeDate",
    "regularMarketTime",
    "regularMarketPrice",
    "dataGranularity",
    "range",
)


class SymbolNotFound(DataUnavailable):
    """Yahoo answered HTTP 404 / ``chart.error.code == "Not Found"`` (expired contract, unknown symbol)."""


class _NonRetryable(DataUnavailable):
    """A 4xx answer that will not change on retry (bad interval, range too long, ...)."""


def _epoch(d: date) -> int:
    return int(datetime.combine(d, dtime(0, 0), tzinfo=UTC).timestamp())


def _exchange_tz(meta: dict[str, Any]) -> ZoneInfo:
    name = meta.get("exchangeTimezoneName")
    if isinstance(name, str) and name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            log.warning("yahoo: unknown exchange time zone %r, assuming America/New_York", name)
    return NEW_YORK


def _float_column(values: Any, n: int) -> np.ndarray:
    """Quote array -> float64 with NaN for nulls (missing or ragged arrays become all-NaN)."""
    if not isinstance(values, list) or len(values) != n:
        return np.full(n, np.nan)
    return np.array([np.nan if v is None else float(v) for v in values], dtype=float)


def _public_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: meta.get(k) for k in META_KEYS if k in meta}


def daily_published_at(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """23:00 Europe/London of each (tz-naive) trading date, as UTC."""
    local = pd.DatetimeIndex(dates) + pd.Timedelta(hours=DAILY_PUBLISH_LONDON.hour)
    return local.tz_localize(LONDON).tz_convert(UTC)


def trading_dates(ts: pd.DatetimeIndex, tz: ZoneInfo) -> pd.DatetimeIndex:
    """Bar timestamps (UTC, session start) -> tz-naive midnight Timestamps of the exchange-local trading date."""
    return pd.DatetimeIndex(ts.tz_convert(tz).normalize().tz_localize(None), name="date")


class YahooAdapter(HttpClient):
    """Daily/intraday bars, single-contract history and the forward curve from Yahoo Finance."""

    name = "yahoo"

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 1.5,
        min_interval: float = 1.0,
    ) -> None:
        super().__init__(timeout=timeout, retries=retries, backoff=backoff, min_interval=min_interval)
        self.session.headers["Accept"] = "application/json"
        self.requests_made = 0

    # ------------------------------------------------------------------ Adapter protocol
    def fetch(self, **kwargs: Any) -> FetchResult:
        """Protocol entry point: ``fetch(symbol=..., interval="1d", start=, end=)`` or an intraday interval."""
        symbol = str(kwargs["symbol"])
        interval = str(kwargs.get("interval", "1d"))
        if interval == "1d":
            return self.fetch_daily(symbol, kwargs.get("start"), kwargs.get("end"))
        return self.fetch_intraday(symbol, interval, int(kwargs.get("lookback_days", 730)))

    # ------------------------------------------------------------------ public API
    def fetch_daily(self, symbol: str, start: date | None = None, end: date | None = None) -> FetchResult:
        """Daily bars in [start, end] (inclusive). Index: tz-naive trading date. Columns: open, high, low, close,
        volume, open_interest (NaN: Yahoo has none), adjclose when present, published_at (23:00 London -> UTC)."""
        end_d = end or utc_now().date()
        start_d = start or DEFAULT_START
        if start_d > end_d:
            raise ValueError(f"start {start_d} is after end {end_d}")
        frames: list[pd.DataFrame] = []
        meta: dict[str, Any] = {}
        start_eff = start_d
        hi = end_d
        while True:
            lo = max(start_eff, hi - timedelta(days=CHUNK_DAYS))
            params = {
                "period1": _epoch(lo - timedelta(days=1)),
                "period2": _epoch(hi + timedelta(days=2)),
                "interval": "1d",
            }
            try:
                result = self._request_chart(symbol, params)
            except SymbolNotFound:
                if not frames:
                    raise
                result = None  # older window of a symbol that exists: treat as no data for the chunk
            if result is not None:
                raw_meta = result.get("meta") or {}
                if not meta:
                    meta = _public_meta(raw_meta)
                first_trade = raw_meta.get("firstTradeDate")
                if isinstance(first_trade, int | float):
                    ftd = datetime.fromtimestamp(float(first_trade), tz=UTC).astimezone(_exchange_tz(raw_meta)).date()
                    start_eff = max(start_eff, ftd)
                frame = self._daily_frame(result, lo, hi)
                if not frame.empty:
                    frames.append(frame)
            if lo <= start_eff:
                break
            hi = lo - timedelta(days=1)
        if not frames:
            raise DataUnavailable(f"yahoo {symbol}: no daily bars between {start_d} and {end_d}")
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        meta.update(
            {"symbol_requested": symbol, "interval": "1d", "start": start_d.isoformat(), "end": end_d.isoformat()}
        )
        return self._result(df, symbol, meta)

    def fetch_intraday(self, symbol: str, interval: str = "1h", lookback_days: int = 730) -> FetchResult:
        """Intraday bars. Index: UTC bar START. Columns: open, high, low, close, volume, published_at (= bar end)."""
        if interval not in INTERVAL_SECONDS:
            raise ValueError(f"unsupported Yahoo intraday interval {interval!r}; use one of {sorted(INTERVAL_SECONDS)}")
        seconds = INTERVAL_SECONDS[interval]
        max_days = INTRADAY_MAX_DAYS[interval]
        days = max(1, min(int(lookback_days), max_days))
        if days != lookback_days:
            log.warning(
                "yahoo %s: lookback %d d clamped to Yahoo's %d-day limit for %s",
                symbol,
                lookback_days,
                max_days,
                interval,
            )
        result = self._request_chart(symbol, {"range": f"{days}d", "interval": interval})
        if result is None:
            raise DataUnavailable(f"yahoo {symbol}: no {interval} bars for the last {days} days")
        raw_meta = result.get("meta") or {}
        df = self._quotes(result, bar_seconds=seconds)
        df = df.set_index("ts").rename_axis("ts")
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df["published_at"] = pd.DatetimeIndex(df.index) + pd.Timedelta(seconds=seconds)
        meta = _public_meta(raw_meta)
        meta.update({"symbol_requested": symbol, "interval": interval, "lookback_days": days})
        return self._result(df, symbol, meta)

    def fetch_contract_history(self, code: str) -> FetchResult:
        """Daily bars of one futures contract, e.g. ``"BZZ26"`` -> ``BZZ26.NYM``.

        Raises DataUnavailable (SymbolNotFound) on 404 or when Yahoo returns no bars.
        """
        symbol = code if ("." in code or "=" in code) else self._symbol_for(code)
        res = self.fetch_daily(symbol)  # SymbolNotFound / empty -> DataUnavailable
        res.meta["code"] = code
        return res

    def fetch_curve(self, root: str, asof: date, n_months: int) -> FetchResult:
        """Forward curve as of ``asof``: one row per listed contract month (``engine.core.calendar.listed_months``).

        Index: contract code. Columns: rank ("M1".."Mn", the position in the listed-month calendar, kept even when
        earlier contracts are missing), year, month, expiry, symbol, date (trading date of the bar used), close,
        volume, open_interest (NaN), ts (UTC end of that bar = 23:00 London), published_at (same instant).
        Contracts Yahoo does not know (404) or has no bar for on/before ``asof`` are skipped and listed in
        ``meta["missing"]``.
        """
        months = listed_months(root, asof, n_months)
        rows: list[dict[str, Any]] = []
        missing: list[str] = []
        symbols: dict[str, str] = {}
        window = {
            "period1": _epoch(asof - timedelta(days=10)),
            "period2": _epoch(asof + timedelta(days=2)),
            "interval": "1d",
        }
        for rank, (year, month) in enumerate(months, start=1):
            fut = Future(root, year, month)
            symbols[fut.code] = fut.yahoo_symbol
            try:
                result = self._request_chart(fut.yahoo_symbol, window)
            except SymbolNotFound:
                result = None
            frame = pd.DataFrame() if result is None else self._daily_frame(result, None, asof)
            if frame.empty:
                missing.append(fut.code)
                log.info("yahoo curve %s: %s (%s) has no bar on/before %s", root, fut.code, fut.yahoo_symbol, asof)
                continue
            last = frame.iloc[-1]
            bar_date = pd.Timestamp(frame.index[-1])
            rows.append(
                {
                    "code": fut.code,
                    "rank": f"M{rank}",
                    "year": year,
                    "month": month,
                    "expiry": pd.Timestamp(fut.expiry),
                    "symbol": fut.yahoo_symbol,
                    "date": bar_date,
                    "close": float(last["close"]),
                    "volume": float(last["volume"]),
                    "open_interest": np.nan,
                    "ts": last["published_at"],
                    "published_at": last["published_at"],
                }
            )
        if not rows:
            raise DataUnavailable(
                f"yahoo curve {root}: none of {len(months)} contracts had data as of {asof}: {missing}"
            )
        df = pd.DataFrame(rows).set_index("code")
        df["ts"] = pd.DatetimeIndex(df["ts"]).tz_convert(UTC)
        df["published_at"] = pd.DatetimeIndex(df["published_at"]).tz_convert(UTC)
        meta = {
            "asof": asof.isoformat(),
            "root": root,
            "n_months": n_months,
            "missing": missing,
            "symbols": symbols,
            "requests": len(months),
        }
        return self._result(df, f"{root} curve", meta)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _symbol_for(code: str) -> str:
        try:
            return Future.from_code(code).yahoo_symbol
        except (KeyError, ValueError) as e:
            raise ValueError(f"malformed contract code {code!r} (expected e.g. 'BZZ26')") from e

    def _result(self, frame: pd.DataFrame, symbol: str, meta: dict[str, Any]) -> FetchResult:
        frame.attrs["source"] = self.name
        frame.attrs["symbol"] = symbol
        if meta.get("currency"):
            frame.attrs["units"] = str(meta["currency"])
        return FetchResult(source=self.name, frame=frame, fetched_at=utc_now(), meta=meta)

    def _request_chart(self, symbol: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """One chart call with pacing and retries. Returns ``chart.result[0]`` or None when Yahoo has no data for
        the window (HTTP 400 "Data doesn't exist", empty result). Raises SymbolNotFound on 404 and DataUnavailable
        after exhausting retries (429/5xx/empty body/network)."""
        url = CHART_URL.format(symbol=symbol)
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_call = time.monotonic()
                self.requests_made += 1
                r = self.session.get(url, params=params, timeout=self.timeout)
                return self._parse_reply(symbol, r)
            except (SymbolNotFound, _NonRetryable):
                raise
            except (requests.RequestException, DataUnavailable, ValueError) as e:
                last_exc = e
                sleep = self.backoff ** (attempt + 1)
                log.warning("GET %s %s failed (%s); retry in %.1fs", url, params, e, sleep)
                if attempt < self.retries:
                    time.sleep(sleep)
        raise DataUnavailable(f"GET {url} failed after {self.retries + 1} attempts: {last_exc}")

    @staticmethod
    def _parse_reply(symbol: str, r: requests.Response) -> dict[str, Any] | None:
        status = r.status_code
        if status == 429 or status >= 500:
            raise DataUnavailable(f"yahoo {symbol}: HTTP {status}")
        if not r.content:
            raise DataUnavailable(f"yahoo {symbol}: HTTP {status} with empty body (rate-limited burst?)")
        try:
            payload = r.json()
        except ValueError as e:
            raise DataUnavailable(f"yahoo {symbol}: HTTP {status} with non-JSON body") from e
        chart = payload.get("chart") if isinstance(payload, dict) else None
        if not isinstance(chart, dict):
            raise _NonRetryable(f"yahoo {symbol}: HTTP {status} unexpected payload {str(payload)[:200]!r}")
        error = chart.get("error")
        if error:
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            desc = str(error.get("description", error)) if isinstance(error, dict) else str(error)
            if status == 404 or code == "Not Found":
                raise SymbolNotFound(f"yahoo {symbol}: {code}: {desc}")
            if "doesn't exist" in desc or "does not exist" in desc:
                log.info("yahoo %s: no data in window (%s)", symbol, desc)
                return None
            raise _NonRetryable(f"yahoo {symbol}: HTTP {status} {code}: {desc}")
        if status >= 400:
            raise _NonRetryable(f"yahoo {symbol}: HTTP {status} without chart.error")
        results = chart.get("result") or []
        if not results or not isinstance(results[0], dict):
            return None
        first: dict[str, Any] = results[0]
        return first

    @staticmethod
    def _quotes(result: dict[str, Any], bar_seconds: int) -> pd.DataFrame:
        """``chart.result[0]`` -> frame with UTC ``ts`` + float OHLCV (+ adjclose).

        The trailing live quote and rows with a null close are dropped.
        """
        meta = result.get("meta") or {}
        stamps = [int(t) for t in (result.get("timestamp") or [])]
        n = len(stamps)
        indicators = result.get("indicators") or {}
        quote_list = indicators.get("quote") or [{}]
        quote = quote_list[0] if isinstance(quote_list[0], dict) else {}
        data: dict[str, Any] = {"ts": pd.to_datetime(stamps, unit="s", utc=True)}
        for field in QUOTE_FIELDS:
            data[field] = _float_column(quote.get(field), n)
        adj_list = indicators.get("adjclose") or []
        if adj_list and isinstance(adj_list[0], dict) and isinstance(adj_list[0].get("adjclose"), list):
            data["adjclose"] = _float_column(adj_list[0]["adjclose"], n)
        df = pd.DataFrame(data)
        # Trailing live quote: stamped meta.regularMarketTime, off the bar grid or < 1 bar after the previous row.
        rmt = meta.get("regularMarketTime")
        if n and isinstance(rmt, int | float) and stamps[-1] == int(rmt):
            off_grid = stamps[-1] % 300 != 0
            too_close = n == 1 or (stamps[-1] - stamps[-2]) < bar_seconds
            if off_grid or too_close:
                df = df.iloc[:-1]
        df = df[df["close"].notna()]  # never fill: a null close is a missing bar
        return df.reset_index(drop=True)

    def _daily_frame(self, result: dict[str, Any], start: date | None, end: date | None) -> pd.DataFrame:
        meta = result.get("meta") or {}
        tz = _exchange_tz(meta)
        q = self._quotes(result, bar_seconds=DAILY_SECONDS)
        dates = trading_dates(pd.DatetimeIndex(q["ts"]), tz)
        df = q.drop(columns=["ts"])
        df.index = dates
        df["open_interest"] = np.nan
        df = df[~df.index.duplicated(keep="last")].sort_index()
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        cols = DAILY_COLUMNS + (["adjclose"] if "adjclose" in df.columns else [])
        df = df[cols]
        df["published_at"] = daily_published_at(pd.DatetimeIndex(df.index))
        return df
