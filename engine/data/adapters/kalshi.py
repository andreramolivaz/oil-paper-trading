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
"Above $103.99", "Before Jan 1, 2027"), ``strike_type``, ``floor_strike``, ``cap_strike``, ``close_time``,
``expiration_time``, ``yes_bid``, ``yes_ask``, ``last_price``, ``volume``, ``open_interest``, ``status``, ``rules``
and ``published_at`` (= ``ts``). Prices are in dollars per contract that pays one dollar: a probability.

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
)  # fmt: skip
COLUMNS = [
    "series_ticker", "event_ticker", "ticker", "title", "subtitle", "strike_type", "floor_strike", "cap_strike",
    "close_time", "expiration_time", "yes_bid", "yes_ask", "last_price", "volume", "open_interest", "status", "rules",
]  # fmt: skip
_NUMERIC = ("floor_strike", "cap_strike", "yes_bid", "yes_ask", "last_price", "volume", "open_interest")


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


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
                "close_time": str(m.get("close_time") or ""),
                "expiration_time": str(m.get("expected_expiration_time") or m.get("expiration_time") or ""),
                "yes_bid": _num(m.get("yes_bid_dollars")),
                "yes_ask": _num(m.get("yes_ask_dollars")),
                "last_price": _num(m.get("last_price_dollars")),
                "volume": _num(m.get("volume_fp")),
                "open_interest": _num(m.get("open_interest_fp")),
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
