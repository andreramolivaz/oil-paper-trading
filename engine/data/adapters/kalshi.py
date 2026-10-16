"""Kalshi, the prediction market: the open markets of a few series, with their prices (probabilities).

Source: the public trade API (``https://api.elections.kalshi.com/trade-api/v2``), no key, read-only. Verified
from a GitHub runner on 2026-10-13: 15 011 series exist; the ones this project reads are the price of Brent and
WTI by day, week and month, the Strait of Hormuz (its closure, when traffic returns to normal), the US-Iran
talks, OPEC's cuts, the EIA inventories and the Saudi production. About thirty quick calls answer HTTP 429, so
this adapter makes one call per series, a second apart, and backs off on a 429.

Why the engine reads it (docs/RESEARCH.md, chapter 9): the owner asked for "the markets that know first". A
binary market's price is the crowd's probability of the event, in real time, on the questions the desk's
forecast cannot see (will the strait reopen, by when). No book trades on it: there is no history to test a
rule on. The tick archives the day's reading so that in a year there will be one, and the terminal shows the
probabilities next to what the options imply.

Frame contract: index ``ts`` (one UTC timestamp, the download time, for every row), one row per open market:
``series_ticker``, ``event_ticker``, ``ticker``, ``title``, ``subtitle`` (the market's own label inside its event:
"Above $103.99", "Before Jan 1, 2027"), ``strike_type``, ``floor_strike``, ``cap_strike``, ``open_time``,
``close_time``, ``expiration_time``, ``yes_bid``, ``yes_ask``, ``last_price``, ``volume`` (contracts since listing),
``volume_24h`` (the last 24 hours), ``open_interest``, ``prev_yes_bid``, ``prev_yes_ask``, ``prev_price`` (the
quote and the last trade "a day ago", as the API states them), ``yes_bid_size``, ``yes_ask_size`` (contracts
resting at the touch), ``status``, ``rules`` and ``published_at`` (= ``ts``). Prices are in dollars per contract
that pays one dollar: a probability.

The trades (``fetch_trades``): ``/markets/trades?ticker=`` paged, newest first, each ``created_time``, ``count``
(contracts), ``yes_price``, ``taker_side`` ("yes" when the taker bought YES or sold NO, "no" otherwise:
``taker_outcome_side``) and ``block``. Read only for the markets the unusual-activity alert flags
(``engine/desk/watch.py``), a handful a day.

Seen on the real API (the runner's first reading, 2026-10-14 01:22 UTC) and handled downstream
(``engine/desk/prediction.py``): a market object has no ``series_ticker`` of its own (the series is the part of
``event_ticker`` before the first dash, and the query by series is what links them); a series can have no open
market at all (``KXCLOSEHORMUZ``, ``KXHORMUZRETURN``, ``KXOPECCUTS``, ``KXSAUDICRUDE``, ``KXWTIMONTHLY`` that
night); the numbers come as strings ("0.0400"); a reading is a probability only while the market's close time
is still ahead. Probes made from the workspace through a fetch proxy had returned months-old copies of some
series: the runner's own download is the only reading that counts.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterable
from typing import Any

import pandas as pd

from engine.core.errors import DataUnavailable
from engine.data.base import FetchResult, HttpClient, utc_now

log = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
PAGE = 200
# the series read by default (config/data_sources.yaml may list others): what the oil desk would want to know
DEFAULT_SERIES = (
    "KXBRENTD", "KXBRENTW", "KXBRENTMON",  # the price of Brent by day, week, month
    "KXWTI", "KXWTIW", "KXWTIMONTHLY",  # the price of WTI by day, week, month
    "KXCLOSEHORMUZ", "KXHORMUZRETURN", "KXHORMUZNORM", "KXHORMUZWEEKLY", "KXMAXSHIPSHORMUZ",  # the strait
    "KXUSAIRANAGREEMENT", "KXUSIRANMEETING",  # the talks
    "KXOPECCUTS", "KXEIACRUDEW", "KXSAUDICRUDE",  # supply
    "KXFEDDECISION", "KXRECSSNBER",  # the FOMC meetings and the yearly recession markets (phase 15)
)  # fmt: skip
COLUMNS = [
    "series_ticker", "event_ticker", "ticker", "title", "subtitle", "strike_type", "floor_strike", "cap_strike",
    "open_time", "close_time", "expiration_time", "yes_bid", "yes_ask", "last_price", "volume", "volume_24h",
    "open_interest", "prev_yes_bid", "prev_yes_ask", "prev_price", "yes_bid_size", "yes_ask_size", "status", "rules",
]  # fmt: skip
_NUMERIC = (
    "floor_strike", "cap_strike", "yes_bid", "yes_ask", "last_price", "volume", "volume_24h", "open_interest",
    "prev_yes_bid", "prev_yes_ask", "prev_price", "yes_bid_size", "yes_ask_size",
)  # fmt: skip
TRADE_COLUMNS = ["trade_id", "ticker", "created_time", "count", "yes_price", "no_price", "taker_side", "block"]
TRADES_PAGE = 1000
TRADES_MAX_PAGES = 5  # 5 000 trades: more than any of these markets does in a week
# The candlesticks (``fetch_candlesticks``): one row per period, prices in dollars. ``end_ts`` is the period's
# inclusive end as a Unix time; ``source`` says which endpoint answered (live, or historical for an archived
# market: the two spell their fields differently, ``close_dollars`` against ``close``).
CANDLE_COLUMNS = [
    "ticker", "end_ts", "yes_bid_close", "yes_ask_close", "yes_bid_open", "yes_ask_open", "price_close",
    "price_previous", "volume", "open_interest", "source",
]  # fmt: skip
CANDLE_DAY = 1440  # period_interval in minutes: a daily candle


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _unix(value: Any) -> int | None:
    """An ISO time ("2026-03-17T14:00:00Z") -> Unix seconds; None when missing or unreadable."""
    if value is None or value == "":
        return None
    try:
        return int(pd.Timestamp(str(value)).tz_convert("UTC").timestamp())
    except (TypeError, ValueError):
        try:
            return int(pd.Timestamp(str(value)).tz_localize("UTC").timestamp())
        except (TypeError, ValueError):
            return None


def _candle_field(block: Any, name: str) -> float | None:
    """A field of a candlestick's bid, ask or price block, in the live spelling (``close_dollars``) or the
    historical one (``close``)."""
    if not isinstance(block, dict):
        return None
    value = block.get(f"{name}_dollars")
    return _num(value if value is not None else block.get(name))


def parse_candlesticks(candles: Iterable[dict[str, Any]], ticker: str, source: str = "live") -> list[dict[str, Any]]:
    """The API's candlestick objects -> rows of ``CANDLE_COLUMNS``, oldest first."""
    rows: list[dict[str, Any]] = []
    for c in candles:
        if not isinstance(c, dict) or c.get("end_period_ts") is None:
            continue
        end = _num(c.get("end_period_ts"))
        if end is None:
            continue
        volume = c.get("volume_fp") if c.get("volume_fp") is not None else c.get("volume")
        oi = c.get("open_interest_fp") if c.get("open_interest_fp") is not None else c.get("open_interest")
        rows.append(
            {
                "ticker": ticker,
                "end_ts": int(end),
                "yes_bid_close": _candle_field(c.get("yes_bid"), "close"),
                "yes_ask_close": _candle_field(c.get("yes_ask"), "close"),
                "yes_bid_open": _candle_field(c.get("yes_bid"), "open"),
                "yes_ask_open": _candle_field(c.get("yes_ask"), "open"),
                "price_close": _candle_field(c.get("price"), "close"),
                "price_previous": _candle_field(c.get("price"), "previous"),
                "volume": _num(volume),
                "open_interest": _num(oi),
                "source": source,
            }
        )
    rows.sort(key=lambda r: r["end_ts"])
    return rows


def parse_markets(markets: Iterable[dict[str, Any]], series_ticker: str, ts: pd.Timestamp) -> list[dict[str, Any]]:
    """The API's market objects -> rows of the frame contract (prices in dollars, the ``*_dollars`` fields)."""
    rows: list[dict[str, Any]] = []
    for m in markets:
        if not isinstance(m, dict) or not m.get("ticker"):
            continue
        rows.append(
            {
                "ts": ts,
                "series_ticker": str(m.get("series_ticker") or series_ticker),
                "event_ticker": str(m.get("event_ticker") or ""),
                "ticker": str(m["ticker"]),
                "title": str(m.get("title") or ""),
                "subtitle": str(m.get("yes_sub_title") or ""),
                "strike_type": str(m.get("strike_type") or ""),
                "floor_strike": _num(m.get("floor_strike")),
                "cap_strike": _num(m.get("cap_strike")),
                "open_time": str(m.get("open_time") or ""),
                "close_time": str(m.get("close_time") or ""),
                "expiration_time": str(m.get("expected_expiration_time") or m.get("expiration_time") or ""),
                "yes_bid": _num(m.get("yes_bid_dollars")),
                "yes_ask": _num(m.get("yes_ask_dollars")),
                "last_price": _num(m.get("last_price_dollars")),
                "volume": _num(m.get("volume_fp")),
                "volume_24h": _num(m.get("volume_24h_fp")),
                "open_interest": _num(m.get("open_interest_fp")),
                "prev_yes_bid": _num(m.get("previous_yes_bid_dollars")),
                "prev_yes_ask": _num(m.get("previous_yes_ask_dollars")),
                "prev_price": _num(m.get("previous_price_dollars")),
                "yes_bid_size": _num(m.get("yes_bid_size_fp")),
                "yes_ask_size": _num(m.get("yes_ask_size_fp")),
                "status": str(m.get("status") or ""),
                "rules": str(m.get("rules_primary") or "")[:400],
            }
        )
    return rows


def to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=[*COLUMNS, "published_at"], index=pd.DatetimeIndex([], name="ts", tz="UTC"))
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.set_index("ts")
    for col in _NUMERIC:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    frame = frame.sort_values(["series_ticker", "close_time", "floor_strike"], na_position="first")
    out = frame[COLUMNS].copy()
    out["published_at"] = out.index
    out.index.name = "ts"
    return out


def parse_trades(trades: Iterable[dict[str, Any]], ticker: str) -> list[dict[str, Any]]:
    """The API's trade objects -> rows (``TRADE_COLUMNS``), the price in dollars, the side as the taker's outcome."""
    rows: list[dict[str, Any]] = []
    for t in trades:
        if not isinstance(t, dict) or not t.get("created_time"):
            continue
        side = str(t.get("taker_outcome_side") or t.get("taker_side") or "")
        rows.append(
            {
                "trade_id": str(t.get("trade_id") or ""),
                "ticker": str(t.get("ticker") or ticker),
                "created_time": str(t["created_time"]),
                "count": _num(t.get("count_fp") if t.get("count_fp") is not None else t.get("count")),
                "yes_price": _num(t.get("yes_price_dollars")),
                "no_price": _num(t.get("no_price_dollars")),
                "taker_side": side,
                "block": bool(t.get("is_block_trade", False)),
            }
        )
    return rows


class KalshiAdapter(HttpClient):
    name = "kalshi"

    def __init__(self, *, timeout: float = 30.0, retries: int = 2, backoff: float = 6.0) -> None:
        # one call per second: the public API answers 429 to a burst of about thirty
        super().__init__(timeout=timeout, retries=retries, backoff=backoff, min_interval=1.0)
        self.session.headers["Accept"] = "application/json"

    def fetch_series(self, series_ticker: str, ts: pd.Timestamp) -> list[dict[str, Any]]:
        """Every open market of one series (paged)."""
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"series_ticker": series_ticker, "status": "open", "limit": PAGE}
            if cursor:
                params["cursor"] = cursor
            body = self.get_json(f"{BASE_URL}/markets", params=params)
            if not isinstance(body, dict):
                raise DataUnavailable(f"kalshi {series_ticker}: unexpected answer {str(body)[:120]}")
            rows += parse_markets(body.get("markets") or [], series_ticker, ts)
            cursor = body.get("cursor") or None
            if not cursor:
                break
        return rows

    def fetch_trades(self, ticker: str, min_ts: int | None = None) -> list[dict[str, Any]]:
        """The trades of one market since ``min_ts`` (a Unix time; None: the newest pages), newest first."""
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(TRADES_MAX_PAGES):
            params: dict[str, Any] = {"ticker": ticker, "limit": TRADES_PAGE}
            if min_ts is not None:
                params["min_ts"] = int(min_ts)
            if cursor:
                params["cursor"] = cursor
            body = self.get_json(f"{BASE_URL}/markets/trades", params=params)
            if not isinstance(body, dict):
                raise DataUnavailable(f"kalshi trades {ticker}: unexpected answer {str(body)[:120]}")
            rows += parse_trades(body.get("trades") or [], ticker)
            cursor = body.get("cursor") or None
            if not cursor:
                break
        return rows

    def fetch_markets(self, series_ticker: str, status: str | None = None) -> list[dict[str, Any]]:
        """Every market of one series with ``status`` ("open", "closed", "settled"; None: any status), paged,
        as the API's own objects (not the frame contract): the history needs the dates and the labels."""
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"series_ticker": series_ticker, "limit": PAGE}
            if status:
                params["status"] = status
            if cursor:
                params["cursor"] = cursor
            body = self.get_json(f"{BASE_URL}/markets", params=params)
            if not isinstance(body, dict):
                raise DataUnavailable(f"kalshi {series_ticker}: unexpected answer {str(body)[:120]}")
            rows += [m for m in body.get("markets") or [] if isinstance(m, dict) and m.get("ticker")]
            cursor = body.get("cursor") or None
            if not cursor:
                break
        return rows

    def _get_optional(self, url: str, params: dict[str, Any]) -> Any | None:
        """One GET at the adapter's pace that answers None on a 404 (a market the live endpoint no longer has)
        instead of retrying it for a minute; every other failure goes through the usual retries."""
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        r = self.session.get(url, params=params, timeout=self.timeout)
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            return self.get_json(url, params=params)
        r.raise_for_status()
        return r.json()

    def fetch_candlesticks(
        self, series_ticker: str, ticker: str, start_ts: int, end_ts: int, period: int = CANDLE_DAY
    ) -> list[dict[str, Any]]:
        """The candlesticks of one market between two Unix times (``period`` minutes: 1, 60 or 1440), from the
        live endpoint or, for a market archived after its settlement, the historical one. Rows of
        ``CANDLE_COLUMNS``: the period's end, the bid and ask at its close, the last trade's price (None when
        none), the contracts traded in the period and the open interest at its end - prices in dollars."""
        params = {"start_ts": int(start_ts), "end_ts": int(end_ts), "period_interval": int(period)}
        body = self._get_optional(f"{BASE_URL}/series/{series_ticker}/markets/{ticker}/candlesticks", params)
        source = "live"
        if body is None:
            body = self._get_optional(f"{BASE_URL}/historical/markets/{ticker}/candlesticks", params)
            source = "historical"
        if body is None:
            raise DataUnavailable(f"kalshi candlesticks {ticker}: not found on the live or the historical endpoint")
        if not isinstance(body, dict):
            raise DataUnavailable(f"kalshi candlesticks {ticker}: unexpected answer {str(body)[:120]}")
        return parse_candlesticks(body.get("candlesticks") or [], ticker, source)

    def fetch_history(self, series: Iterable[str], since: int, until: int | None = None) -> dict[str, Any]:
        """Every market of each series, whatever its status, with its daily candlesticks from ``since`` (a Unix
        time; a market opened later starts at its open) to ``until`` (now by default): what the archive of the
        prediction markets would hold had the desk read them every day since ``since`` (``engine/desk/backfill``).
        One series failing is noted and the others are still returned."""
        end = int(until if until is not None else utc_now().timestamp())
        out: dict[str, Any] = {"fetched_at": utc_now().isoformat(), "since": int(since), "until": end, "series": {}}
        failed: dict[str, str] = {}
        for series_ticker in series:
            try:
                markets = self.fetch_markets(series_ticker)
            except DataUnavailable as exc:
                failed[series_ticker] = str(exc)[:160]
                log.warning("kalshi history %s: %s", series_ticker, exc)
                continue
            try:
                # the plain listing stops showing a market a few weeks after it settles (the September 2026 FOMC
                # markets were listed on 15 October, July's were not): the settled ones are asked for by name
                # and merged, so that a series' past is as long as the API keeps it; this second listing is a
                # bonus and its failure costs nothing (the open markets are already in hand)
                seen = {str(m.get("ticker")) for m in markets}
                markets += [m for m in self.fetch_markets(series_ticker, "settled") if str(m.get("ticker")) not in seen]
            except DataUnavailable as exc:
                failed[f"{series_ticker} (settled)"] = str(exc)[:160]
                log.warning("kalshi history %s, settled listing: %s", series_ticker, exc)
            candles: dict[str, list[dict[str, Any]]] = {}
            for m in markets:
                ticker = str(m["ticker"])
                opened = _unix(m.get("open_time"))
                closed = _unix(m.get("close_time"))
                start = max(int(since), opened or int(since))
                stop = min(end, closed) if closed else end
                if stop <= start:
                    continue
                try:
                    candles[ticker] = self.fetch_candlesticks(series_ticker, ticker, start, stop + 86_400)
                except DataUnavailable as exc:
                    failed[ticker] = str(exc)[:160]
                    log.warning("kalshi candlesticks %s: %s", ticker, exc)
            out["series"][series_ticker] = {"markets": markets, "candles": candles}
        out["failed"] = failed
        return out

    def fetch(self, series: Iterable[str] | None = None) -> FetchResult:
        """The open markets of ``series`` (the default list when None). A series that answers nothing is noted
        and skipped: the day's reading of the others still counts."""
        wanted = list(series) if series else list(DEFAULT_SERIES)
        ts = pd.Timestamp(utc_now())
        rows: list[dict[str, Any]] = []
        failed: dict[str, str] = {}
        for ticker in wanted:
            try:
                rows += self.fetch_series(ticker, ts)
            except DataUnavailable as exc:
                failed[ticker] = str(exc)[:160]
                log.warning("kalshi %s: %s", ticker, exc)
        if not rows:
            raise DataUnavailable(f"kalshi: no open market in {len(wanted)} series ({failed})")
        frame = to_frame(rows)
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={"series": wanted, "rows": len(frame), "failed": failed, "published_at_rule": "the download time"},
        )
