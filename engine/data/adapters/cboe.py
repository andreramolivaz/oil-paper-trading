"""Cboe delayed option chains: real bids and asks, 15 minutes late, no key.

Endpoint: ``https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json`` (it redirects to
``cdn-api.cboe.com``; index symbols take a leading underscore). Verified on 2026-10-08 for BNO (1 006 contracts,
weekly expiries) and USO (6 152 contracts): every row carries bid, ask, sizes, implied volatility, greeks, open
interest and volume, and the payload carries the underlying's own quote and its 30-day implied volatility.

Why this and not a model: the engine used to "price" options with Black-76 on the OVX index and an assumed
skew. That answers what an option should cost, not what it costs. On 2026-10-08 the BNO chain was quoted 45-50 %
of the mid-price wide near the money and the USO chain about 11 %: a strategy that looks fine at a model mid can
lose its whole edge crossing those spreads, and only real quotes show it.

What the quotes are NOT: they are Cboe's delayed feed, not a consolidated best bid and offer, so the true
market is usually somewhat tighter than this. The paper book treats the quotes as they are and fills inside
them only by a stated fraction; it never fills at a price the feed did not bracket.

Frame contract (one row per option, default integer index): ``option`` (OCC symbol), ``underlying``, ``expiry``
(tz-naive date), ``right`` ("C"/"P"), ``strike``, ``bid``, ``ask``, ``bid_size``, ``ask_size``, ``iv``,
``delta``, ``gamma``, ``vega``, ``theta``, ``open_interest``, ``volume``, ``underlying_price``, ``quote_ts`` and
``published_at`` (both UTC: the feed's own timestamp), ``iv30`` (the underlying's 30-day implied volatility in
percent, the same on every row).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from engine.core.errors import DataUnavailable
from engine.data.base import FetchResult, HttpClient, utc_now

log = logging.getLogger(__name__)

CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
OCC_RE = re.compile(r"^([A-Z^_.]+?)(\d{6})([CP])(\d{8})$")
NEW_YORK = ZoneInfo("America/New_York")
NUMERIC = ("bid", "ask", "bid_size", "ask_size", "iv", "delta", "gamma", "vega", "theta", "open_interest", "volume")
COLUMNS = ["option", "underlying", "expiry", "right", "strike", *NUMERIC, "underlying_price", "quote_ts"]
# A browser-like agent: the CDN answers 403 to the default python-requests one.
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def parse_occ(symbol: str) -> tuple[str, pd.Timestamp, str, float] | None:
    """``BNO261016C00064000`` -> ("BNO", 2026-10-16, "C", 64.0). None when it is not an OCC option symbol."""
    m = OCC_RE.match(symbol)
    if not m:
        return None
    root, ymd, right, strike = m.groups()
    try:
        expiry = pd.Timestamp(datetime.strptime(ymd, "%y%m%d"))
    except ValueError:
        return None
    return root, expiry, right, int(strike) / 1000.0


def parse_chain(payload: dict[str, Any], max_days: int = 75, moneyness: float = 0.30) -> pd.DataFrame:
    """Cboe payload -> the frame contract. Only expiries within ``max_days`` and strikes within ``moneyness`` of
    the underlying are kept: that is everything a short-dated defined-risk book can trade, at a tenth of the
    size of the full chain (the state branch stores every snapshot)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("options"), list):
        raise DataUnavailable("cboe: payload without data.options")
    price = data.get("current_price") or data.get("close")
    try:
        underlying_price = float(price)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DataUnavailable("cboe: payload without an underlying price") from exc
    stamp = str(payload.get("timestamp") or "")
    try:
        # the payload stamp is UTC without an offset (checked against the clock on 2026-10-08); the quotes
        # themselves are 15 minutes older than it
        quote_ts = pd.Timestamp(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC))
    except ValueError:
        quote_ts = pd.Timestamp(utc_now())
    today = quote_ts.tz_convert(NEW_YORK).normalize().tz_localize(None)
    rows: list[dict[str, Any]] = []
    for raw in data["options"]:
        if not isinstance(raw, dict):
            continue
        parsed = parse_occ(str(raw.get("option", "")))
        if parsed is None:
            continue
        root, expiry, right, strike = parsed
        days = (expiry - today).days
        if days < 0 or days > max_days:
            continue
        if underlying_price > 0 and abs(strike / underlying_price - 1.0) > moneyness:
            continue
        row: dict[str, Any] = {
            "option": raw["option"],
            "underlying": root,
            "expiry": expiry,
            "right": right,
            "strike": strike,
        }
        for key in NUMERIC:
            try:
                row[key] = float(raw.get(key))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                row[key] = float("nan")
        rows.append(row)
    if not rows:
        raise DataUnavailable("cboe: no option inside the expiry and strike window")
    frame = pd.DataFrame(rows)
    frame["underlying_price"] = underlying_price
    frame["quote_ts"] = quote_ts
    frame = frame[COLUMNS].sort_values(["expiry", "right", "strike"], kind="stable").reset_index(drop=True)
    frame["published_at"] = quote_ts
    try:
        frame["iv30"] = float(data.get("iv30"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        frame["iv30"] = float("nan")
    frame.attrs["iv30"] = data.get("iv30")
    frame.attrs["underlying_bid"] = data.get("bid")
    frame.attrs["underlying_ask"] = data.get("ask")
    return frame


class CboeAdapter(HttpClient):
    name = "cboe"

    def __init__(self, *, timeout: float = 40.0, retries: int = 2, backoff: float = 2.0) -> None:
        super().__init__(timeout=timeout, retries=retries, backoff=backoff, min_interval=1.0)
        self.session.headers["User-Agent"] = BROWSER_UA
        self.session.headers["Accept"] = "application/json"

    def fetch_chain(self, symbol: str = "BNO", max_days: int = 75, moneyness: float = 0.30) -> FetchResult:
        payload = self.get_json(CHAIN_URL.format(symbol=symbol.upper()))
        frame = parse_chain(payload, max_days=max_days, moneyness=moneyness)
        meta = {
            "symbol": symbol.upper(),
            "rows": len(frame),
            "underlying_price": float(frame["underlying_price"].iloc[0]),
            "iv30": frame.attrs.get("iv30"),
            "underlying_bid": frame.attrs.get("underlying_bid"),
            "underlying_ask": frame.attrs.get("underlying_ask"),
            "quote_ts": frame["quote_ts"].iloc[0].isoformat(),
            "delayed_minutes": 15,
        }
        return FetchResult(source=self.name, frame=frame, fetched_at=utc_now(), meta=meta)
