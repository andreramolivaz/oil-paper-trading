"""The prediction markets' archive, filled from Kalshi's daily candlesticks (phase 14).

The desk has archived one reading a day of the markets it reads since 14 October 2026 (``prediction.py``). The
speculative book (``oracle.py``) compares today's reading with the one a week before, so on its first days it
had nothing to compare with - and the research notes said no history existed. Kalshi's API, read again on the
15th, does publish each market's daily candlesticks since the market opened (``KalshiAdapter.fetch_history``):
the bid and ask at the close of every day, the last trade, the contracts traded in the day, the open interest.
That is the reading the desk would have archived had it been running, taken a little later in the evening:
a daily candle ends at midnight New York (04:00 UTC in summer), the tick's reading is taken around 00:20 UTC,
which is 20:20 New York the evening before. Both are "the evening before the decision day".

What this module does with it: one archive record per event and UTC day, in the format the tick writes
(``prediction.LOG_COLUMNS``), for every day a market was open and its candle complete, marked
``backfilled: true`` with its ``source``; and ``apply_backfill`` appends the records whose day and event the
archive does not have yet. A reading the tick took is never replaced: the live reading of a day wins, the
candle fills only the days before the desk existed (and a day it missed). The oracle, the alert's baselines
and the research then read the archive as if it had always been there.

What the candle cannot give: the market's cumulative volume at each day (``volume``) is the running sum of
the candles' daily volumes from the first one - exact for a market whose history starts at its opening, short
by the volume before ``since`` otherwise -, and ``prev_mid`` is the previous candle's mid. Neither is read by
the oracle; the alert reads the live readings only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from engine.core.store import StateStore
from engine.core.timeutil import iso
from engine.desk.prediction import KALSHI_LOG, LOG_COLUMNS, _market_order, _num, _text

log = logging.getLogger(__name__)

SOURCE = "kalshi candlesticks (daily close at midnight New York)"


def _when(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(str(value))
    except (TypeError, ValueError):
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or not (0.0 <= bid <= ask <= 1.0):
        return None
    return round((bid + ask) / 2.0, 4)


def records_from_history(history: Mapping[str, Any], until: datetime | None = None) -> list[dict[str, Any]]:
    """The archive records (one per event and UTC day) a history download holds, oldest first.

    ``until`` (default: the download time, ``history["fetched_at"]``) drops the candle still forming: the API
    returns the period in progress, whose close is not a close. A market is in a day's record when it was open
    at the candle's end (its ``open_time`` before it, its ``close_time`` not before it).
    """
    cutoff = _when(until or history.get("fetched_at")) or pd.Timestamp(datetime.now(tz=UTC))
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for series_ticker, block in (history.get("series") or {}).items():
        markets = {str(m.get("ticker")): m for m in block.get("markets") or [] if isinstance(m, dict)}
        for ticker, candles in (block.get("candles") or {}).items():
            m = markets.get(str(ticker))
            if m is None:
                continue
            opened, closed = _when(m.get("open_time")), _when(m.get("close_time"))
            event = _text(m.get("event_ticker")) or str(ticker)
            running = 0.0
            prev_mid: float | None = None
            for c in sorted((c for c in candles if isinstance(c, dict)), key=lambda c: int(c.get("end_ts") or 0)):
                end = pd.Timestamp(int(c.get("end_ts") or 0), unit="s", tz="UTC")
                volume = _num(c.get("volume")) or 0.0
                running += volume
                mid = _mid(_num(c.get("yes_bid_close")), _num(c.get("yes_ask_close")))
                try:
                    if end > cutoff or (opened is not None and end < opened) or (closed is not None and end > closed):
                        continue
                    day = end.strftime("%Y-%m-%d")
                    rec = by_key.setdefault(
                        (day, event),
                        {
                            "key": f"{day}|{event}",
                            "day": day,
                            "ts": iso(end.to_pydatetime()),
                            "series": str(series_ticker),
                            "event": event,
                            "title": _text(m.get("title"))[:160],
                            "strike_type": _text(m.get("strike_type")),
                            "status": "active",
                            "columns": list(LOG_COLUMNS),
                            "markets": [],
                            "backfilled": True,
                            "source": SOURCE,
                        },
                    )
                    rec["markets"].append(
                        [
                            str(ticker),
                            _text(m.get("yes_sub_title")),
                            _num(m.get("floor_strike")),
                            _num(m.get("cap_strike")),
                            _num(c.get("yes_bid_close")),
                            _num(c.get("yes_ask_close")),
                            _num(c.get("price_close")),
                            round(running, 2),
                            _num(c.get("open_interest")),
                            _text(m.get("close_time")),
                            volume,
                            prev_mid,
                        ]
                    )
                finally:
                    prev_mid = mid
    out = sorted(by_key.values(), key=lambda r: (r["day"], r["series"], r["event"]))
    for rec in out:
        rec["markets"].sort(key=lambda row: _market_order(_row_dict(rec, row)))
    return out


def _row_dict(rec: Mapping[str, Any], row: list[Any]) -> dict[str, Any]:
    d = dict(zip(rec["columns"], row, strict=False))
    return {
        "series_ticker": rec["series"],
        "event_ticker": rec["event"],
        "floor_strike": d.get("floor"),
        "cap_strike": d.get("cap"),
        "close_time": d.get("close_time"),
        "ticker": d.get("ticker"),
    }


def backfilled_days(store: StateStore) -> int:
    """How many days of the archive come from the candlesticks rather than from the tick's own readings."""
    return len({str(r.get("day")) for r in store.iter_jsonl(KALSHI_LOG, skip_bad=True) if r.get("backfilled")})


def apply_backfill(store: StateStore, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Append to the archive the records whose ``day|event`` it does not have. Returns what was done: the
    records written, the ones skipped because the day's reading exists, the days covered."""
    known = {str(r.get("key")) for r in store.iter_jsonl(KALSHI_LOG, skip_bad=True)}
    written, skipped = 0, 0
    days: set[str] = set()
    for rec in records:
        key = str(rec.get("key"))
        if key in known:
            skipped += 1
            continue
        when = _when(rec.get("ts"))
        store.append_jsonl(KALSHI_LOG, dict(rec), ts=None if when is None else when.to_pydatetime())
        known.add(key)
        written += 1
        days.add(str(rec.get("day")))
    return {
        "written": written,
        "skipped": skipped,
        "days": len(days),
        "first_day": min(days) if days else None,
        "last_day": max(days) if days else None,
    }
