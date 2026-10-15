"""The unusual-activity alert on the prediction markets (phase 13, on the owner's request: "there is a lot of
insider trading on Kalshi: add that as an alert").

What an informed trade looks like, in the cases that became public (a trader buying "Israel strikes Iran" on
Polymarket hours before the June 2025 strikes; the accounts that bought "Maduro out" before the January 2026
operation; the 2012 Intrade trader of Rothschild & Sethi 2016) and in the equity literature (Meulbroek 1992: on
days insiders trade, the price moves and the volume is abnormal): a market moves a lot, in one direction, on
volume far above its own usual, often through a few large trades on one side, before any public news. Nobody
can tell an insider from a well-informed hedger or from news that reached the market first; this module flags
the footprint and says what it is, a rule of thumb declared before it was switched on (docs/RESEARCH.md,
chapter 9 F). It never trades.

The rule, per market, at every reading (the tick reads the markets every hour):
1. the market is an event market (the strait, the talks, OPEC, the EIA stocks, ...), not a price ladder on
   Brent or WTI: a ladder moves with the oil price, and that is not information about an event;
2. the move over 24 hours (the API's own "a day ago" quote against the quote now) is at least MOVE_MIN points,
   or at least MOVE_ODDS_MIN points with the odds multiplied or divided by ODDS_RATIO_MIN (a move from 3 % to
   9 % is a tripling of the odds; from 45 % to 51 % it is not);
3. the 24-hour volume is unusual: at least VOLUME_MULTIPLE times the market's own median daily volume and
   its 90th percentile, once BASELINE_MIN_DAYS daily readings exist (state/desk/kalshi-*.jsonl); before that,
   at least OI_SHARE_MIN of the market's open interest, for a market open for at least MIN_AGE_HOURS (a
   market listed yesterday has no usual) - and never under VOLUME_MIN contracts.
The flagged markets of one event (the strikes of one ladder) make ONE alert, written once per event and UTC
day (state/desk/kalshi_alerts-YYYY-MM.jsonl), saying whether the rest of the ladder moved too (news reaches a
whole ladder; a single strike moving alone is the odder sight) and carrying the footprint of the day's trades
on the flagged market with the most volume, when they could be read (``KalshiAdapter.fetch_trades``, a
handful of events a day at most): how many trades, the share of the volume in the five largest, the share on
the taker's dominant side, the largest trade and the first of the large ones. The alerts job turns an alert of
the last two days into a GitHub issue (``engine/monitoring/alerts.py``); the terminal shows the last week's.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso, parse_iso
from engine.data.raw_store import RawStore
from engine.desk.prediction import (
    KALSHI_LOG,
    PRICE_SERIES,
    Reading,
    _group_label,
    _market_view,
    _records,
    _series_of,
    _text,
    latest_reading,
    question_of,
)

log = logging.getLogger(__name__)

ALERTS_LOG = "kalshi_alerts"  # state/desk/kalshi_alerts-YYYY-MM.jsonl
WATCH_FILE = "kalshi_watch.json"  # the last reading judged, so a reading is judged once

# the rule's constants: rules of thumb, declared in docs/RESEARCH.md chapter 9 F before the alert went live
MOVE_MIN = 0.10  # 10 points of probability over 24 hours
MOVE_ODDS_MIN = 0.05  # 5 points are enough when ...
ODDS_RATIO_MIN = 3.0  # ... the odds tripled (3 % -> 9 %) or were divided by three
VOLUME_MULTIPLE = 3.0  # the 24h volume against the market's own median daily volume
BASELINE_MIN_DAYS = 10  # daily readings before a market has a baseline of its own
OI_SHARE_MIN = 0.25  # without a baseline: a quarter of the open interest traded in a day
MIN_AGE_HOURS = 48.0  # ... for a market open at least this long (a new market's first day is all its volume)
VOLUME_MIN = 500.0  # contracts in 24 hours, always
LADDER_FLAT = 0.05  # the rest of a ladder is "flat" when its median 24-hour move is under this
TOP_TRADES = 5
TRADES_MAX_EVENTS = 8  # trades are read for at most this many flagged events per tick
ALERT_WINDOW_DAYS = 7  # shown on the terminal
ISSUE_WINDOW_HOURS = 48.0  # turned into a GitHub issue


@dataclass(frozen=True)
class Baseline:
    days: int
    median_volume: float
    p90_volume: float


def describe() -> dict[str, Any]:
    """The rule in force, for the page."""
    return {
        "move": (
            f"{MOVE_MIN:.0%} of probability over 24 hours, or {MOVE_ODDS_MIN:.0%} with the odds multiplied or divided "
            f"by {ODDS_RATIO_MIN:g}"
        ),
        "volume": (
            f"24-hour volume at least {VOLUME_MULTIPLE:g}x the market's median daily volume and its 90th "
            f"percentile (after {BASELINE_MIN_DAYS} daily readings), before that at least {OI_SHARE_MIN:.0%} of the "
            f"open interest of a market open for {MIN_AGE_HOURS:.0f} hours or more; never under {VOLUME_MIN:.0f} "
            "contracts"
        ),
        "scope": "event markets only: a price ladder on Brent or WTI moves with the oil price",
        "footprint": (
            f"the day's trades on the busiest flagged market, when read: their number, the share of the {TOP_TRADES} "
            "largest, the share on the dominant taker side, the largest and the first large one; and whether the "
            "rest of the ladder moved"
        ),
        "meaning": (
            "unusual activity, not a verdict: news that reached the market first, a large hedger and an informed "
            "trader leave the same footprint"
        ),
    }


# ----------------------------------------------------------------------------------------------- the baselines
def baselines(store: StateStore) -> dict[str, Baseline]:
    """Per market, the distribution of its daily 24-hour volumes in the daily archive (the records written
    with the alert carry ``volume_24h``; the older ones do not and are skipped)."""
    volumes: dict[str, list[float]] = {}
    for rec in store.iter_jsonl(KALSHI_LOG, skip_bad=True):
        cols = list(rec.get("columns") or [])
        if "volume_24h" not in cols or "ticker" not in cols:
            continue
        i_t, i_v = cols.index("ticker"), cols.index("volume_24h")
        for m in rec.get("markets") or []:
            if len(m) <= max(i_t, i_v) or m[i_v] is None:
                continue
            try:
                v = float(m[i_v])
            except (TypeError, ValueError):
                continue
            if math.isfinite(v) and v >= 0:
                volumes.setdefault(str(m[i_t]), []).append(v)
    out: dict[str, Baseline] = {}
    for ticker, values in volumes.items():
        if len(values) >= BASELINE_MIN_DAYS:
            arr = np.asarray(values, dtype="float64")
            out[ticker] = Baseline(len(values), float(np.median(arr)), float(np.percentile(arr, 90)))
    return out


# ----------------------------------------------------------------------------------------------- the rule
def _odds(p: float) -> float | None:
    if p <= 0.0 or p >= 1.0:
        return None
    return p / (1.0 - p)


def move_is_large(now_p: float | None, before_p: float | None) -> tuple[bool, str]:
    """Condition 2: the 24-hour move, in points or in odds."""
    if now_p is None or before_p is None:
        return False, "no quote a day ago"
    delta = now_p - before_p
    if abs(delta) >= MOVE_MIN:
        return True, f"{delta:+.0%} in 24 hours"
    o_now, o_before = _odds(now_p), _odds(before_p)
    if abs(delta) >= MOVE_ODDS_MIN and o_now is not None and o_before is not None:
        ratio = o_now / o_before
        if ratio >= ODDS_RATIO_MIN or ratio <= 1.0 / ODDS_RATIO_MIN:
            return True, f"{delta:+.0%} in 24 hours, the odds x {ratio:.1f}"
    return False, f"{delta:+.0%} in 24 hours"


def volume_is_unusual(
    volume_24h: float | None,
    open_interest: float | None,
    baseline: Baseline | None,
    age_hours: float | None = None,
) -> tuple[bool, str]:
    """Condition 3: the 24-hour volume against the market's own days, or - for a market old enough to have
    an open interest that means something - against its open interest."""
    if volume_24h is None or not math.isfinite(volume_24h):
        return False, "no 24-hour volume"
    if volume_24h < VOLUME_MIN:
        return False, f"{volume_24h:,.0f} contracts in 24 hours (under {VOLUME_MIN:.0f})"
    if baseline is not None:
        bar = max(VOLUME_MULTIPLE * baseline.median_volume, baseline.p90_volume, VOLUME_MIN)
        times = volume_24h / baseline.median_volume if baseline.median_volume > 0 else math.inf
        text = f"{volume_24h:,.0f} contracts in 24 hours, {times:.1f}x the usual day ({baseline.days} days)"
        return volume_24h >= bar, text
    if age_hours is not None and age_hours < MIN_AGE_HOURS:
        return False, f"{volume_24h:,.0f} contracts in 24 hours on a market open for {age_hours:.0f} hours"
    if open_interest is None or open_interest <= 0:
        return False, f"{volume_24h:,.0f} contracts in 24 hours, no open interest to compare with"
    share = volume_24h / open_interest
    text = f"{volume_24h:,.0f} contracts in 24 hours, {share:.0%} of the open interest (no baseline yet)"
    return share >= OI_SHARE_MIN, text


def _age_hours(open_time: str | None, now: datetime) -> float | None:
    when = parse_iso(open_time) if open_time else None
    return None if when is None else (now - ensure_utc(when)).total_seconds() / 3600.0


def flagged_events(reading: Reading, bases: dict[str, Baseline]) -> list[dict[str, Any]]:
    """The events of the reading with at least one market that meets the rule: one entry per event with its
    flagged markets and what they measured. Price ladders are skipped."""
    now = reading.seen.to_pydatetime()
    rows = _records(reading.frame)
    by_event: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_event.setdefault(_text(r.get("event_ticker")) or _text(r.get("ticker")), []).append(r)
    out: list[dict[str, Any]] = []
    for event, members in by_event.items():
        series = _series_of(members[0])
        if series in PRICE_SERIES:
            continue
        views = [_market_view(m) for m in members]
        flagged: list[dict[str, Any]] = []
        for v in views:
            large, move_text = move_is_large(v["mid"], v["mid_24h_ago"])
            if not large:
                continue
            unusual, volume_text = volume_is_unusual(
                v["volume_24h"], v["open_interest"], bases.get(v["ticker"]), _age_hours(v["open_time"], now)
            )
            if not unusual:
                continue
            flagged.append(
                {
                    "ticker": v["ticker"],
                    "label": v["label"],
                    "mid": v["mid"],
                    "mid_24h_ago": v["mid_24h_ago"],
                    "change_24h": v["change_24h"],
                    "volume_24h": v["volume_24h"],
                    "open_interest": v["open_interest"],
                    "move": move_text,
                    "volume": volume_text,
                    "baseline_days": bases[v["ticker"]].days if v["ticker"] in bases else 0,
                }
            )
        if not flagged:
            continue
        hit = {f["ticker"] for f in flagged}
        rest = [abs(v["change_24h"]) for v in views if v["ticker"] not in hit and v["change_24h"] is not None]
        rest_move = float(np.median(rest)) if rest else None
        out.append(
            {
                "series": series,
                "event": event,
                "group": _group_label(series),
                "title": question_of(_text(members[0].get("title"))),
                "markets": sorted(flagged, key=lambda f: -(f["volume_24h"] or 0.0)),
                "n_markets": len(views),
                "rest_of_ladder_move": None if rest_move is None else round(rest_move, 4),
                "rest_flat": rest_move is not None and rest_move < LADDER_FLAT,
                "close_time": min((v["close_time"] for v in views if v["close_time"]), default=None),
            }
        )
    return out


# ----------------------------------------------------------------------------------------------- the footprint
def footprint(trades: list[dict[str, Any]], since: datetime) -> dict[str, Any]:
    """What the day's trades say: how many, how concentrated, on which side, when the large ones came."""
    since = ensure_utc(since)
    rows: list[tuple[datetime, float, float | None, str, bool]] = []
    for t in trades:
        when = parse_iso(_text(t.get("created_time")) or None)
        count = t.get("count")
        if when is None or count is None:
            continue
        when = ensure_utc(when)
        if when < since:
            continue
        rows.append((when, float(count), t.get("yes_price"), _text(t.get("taker_side")), bool(t.get("block"))))
    if not rows:
        return {"trades": 0, "note": "no trade in the window"}
    rows.sort(key=lambda r: r[0])
    total = sum(r[1] for r in rows)
    largest = sorted(rows, key=lambda r: r[1], reverse=True)[:TOP_TRADES]
    top_share = sum(r[1] for r in largest) / total if total > 0 else None
    yes_volume = sum(r[1] for r in rows if r[3] == "yes")
    yes_share = yes_volume / total if total > 0 else None
    side = "yes" if yes_share is not None and yes_share >= 0.5 else "no"
    side_share = yes_share if side == "yes" else (None if yes_share is None else 1.0 - yes_share)
    big = largest[0]
    first_big = min(largest, key=lambda r: r[0])
    prices = [r[2] for r in rows if r[2] is not None]
    return {
        "trades": len(rows),
        "contracts": round(total, 2),
        "top_share": None if top_share is None else round(top_share, 4),
        "top_trades": TOP_TRADES,
        "side": side,
        "side_share": None if side_share is None else round(side_share, 4),
        "largest": {"contracts": round(big[1], 2), "yes_price": big[2], "at": iso(big[0]), "side": big[3]},
        "first_large": {"contracts": round(first_big[1], 2), "yes_price": first_big[2], "at": iso(first_big[0])},
        "block_trades": sum(1 for r in rows if r[4]),
        "price_path": [prices[0], prices[-1]] if prices else None,
        "window_start": iso(since),
    }


def footprint_text(fp: dict[str, Any] | None) -> str:
    if not fp or not fp.get("trades"):
        return "no trade read in the window"
    parts = [f"{fp['trades']} trades, {fp['contracts']:,.0f} contracts"]
    if fp.get("top_share") is not None:
        parts.append(f"the {fp['top_trades']} largest carry {fp['top_share']:.0%}")
    if fp.get("side_share") is not None:
        parts.append(f"{fp['side_share']:.0%} with the taker on {fp['side'].upper()}")
    big = fp.get("largest") or {}
    if big:
        price = "" if big.get("yes_price") is None else f" at {big['yes_price']:.2f}"
        parts.append(f"the largest {big['contracts']:,.0f} contracts{price} at {str(big.get('at', ''))[11:16]} UTC")
    if fp.get("block_trades"):
        parts.append(f"{fp['block_trades']} block trades")
    return "; ".join(parts)


# ----------------------------------------------------------------------------------------------- the alerts
def _already_alerted(store: StateStore, day: str) -> set[str]:
    return {str(r.get("key")) for r in store.iter_jsonl(ALERTS_LOG, skip_bad=True) if str(r.get("day", "")) == day}


def alert_text(a: dict[str, Any]) -> str:
    """One sentence a reader can act on, in English, for the page and the issue."""
    ms = a["markets"]
    n = len(ms)
    strikes = "; ".join(
        f"{m['label']} {m['mid_24h_ago']:.0%} -> {m['mid']:.0%}"
        if m.get("mid") is not None and m.get("mid_24h_ago") is not None
        else m["label"]
        for m in ms[:4]
    )
    if n > 4:
        strikes += f"; and {n - 4} more"
    lead = f"{a['group']} · {a['title']} "
    moves = [m["change_24h"] for m in ms if m.get("change_24h") is not None]
    span = ""
    if moves:
        lo, hi = min(abs(x) for x in moves), max(abs(x) for x in moves)
        span = f"moved {lo:.0%}" if n == 1 or abs(lo - hi) < 0.005 else f"moved {lo:.0%}-{hi:.0%}"
    what = f"{n} of {a['n_markets']} markets {span} in 24 hours"
    volume = ms[0]["volume"] if n == 1 else f"{ms[0]['volume']} on the busiest"
    scope = (
        "the rest of the ladder is flat"
        if a.get("rest_flat")
        else "the whole ladder moved too"
        if a.get("rest_of_ladder_move") is not None
        else "no other market to compare with"
    )
    text = f"{lead}- {what} ({strikes}) on {volume}; {scope}"
    if a.get("footprint") is not None:
        text += f". Trades on {a.get('footprint_ticker') or ms[0]['ticker']}: {footprint_text(a['footprint'])}"
    return text + "."


def watch(
    raw: RawStore,
    store: StateStore,
    now: datetime,
    *,
    fetch_trades: Callable[[str, int], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """What the tick does after archiving a reading: judge it once, write the new alerts (one per event and
    UTC day), read the trades of the busiest flagged market of each when a reader is given. Returns a small
    summary."""
    now = ensure_utc(now)
    reading = latest_reading(raw)
    if reading is None:
        return {"status": "no reading on file"}
    state = store.read_json(WATCH_FILE, {}) or {}
    seen = iso(reading.seen.to_pydatetime())
    if state.get("last_reading") == seen:
        return {"status": "already judged", "reading": seen, "alerts_written": 0}
    bases = baselines(store)
    events = flagged_events(reading, bases)
    day = reading.day
    known = _already_alerted(store, day)
    written: list[str] = []
    read_trades = 0
    for a in events:
        key = f"{day}|{a['event']}"
        if key in known:
            continue
        record: dict[str, Any] = {**a, "key": key, "day": day, "ts": seen, "footprint": None, "footprint_note": None}
        busiest = a["markets"][0]["ticker"]
        record["footprint_ticker"] = busiest
        if fetch_trades is not None and read_trades < TRADES_MAX_EVENTS:
            read_trades += 1
            since = reading.seen.to_pydatetime() - timedelta(hours=24)
            try:
                record["footprint"] = footprint(fetch_trades(busiest, int(since.timestamp())), since)
            except Exception as exc:  # the alert stands without its footprint
                record["footprint_note"] = f"trades not read: {str(exc)[:120]}"
                log.warning("kalshi trades of %s not read: %s", busiest, exc)
        elif fetch_trades is None:
            record["footprint_note"] = "trades not read (no reader)"
        else:
            record["footprint_note"] = f"trades not read: more than {TRADES_MAX_EVENTS} events flagged"
        record["text"] = alert_text(record)
        store.append_jsonl(ALERTS_LOG, record, ts=reading.seen.to_pydatetime())
        written.append(a["event"])
        log.warning("kalshi unusual activity: %s", record["text"])
    store.write_json(
        WATCH_FILE,
        {
            "last_reading": seen,
            "judged_at": iso(now),
            "markets": len(reading.frame),
            "baselines": len(bases),
            "flagged": [a["event"] for a in events],
        },
    )
    return {
        "status": "ok",
        "reading": seen,
        "markets": len(reading.frame),
        "baselines": len(bases),
        "flagged": len(events),
        "alerts_written": len(written),
        "events": written,
    }


def recent_alerts(store: StateStore, now: datetime, days: float = ALERT_WINDOW_DAYS) -> list[dict[str, Any]]:
    """The alerts of the last ``days``, newest first (what the terminal shows)."""
    now = ensure_utc(now)
    cutoff = now - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for rec in store.iter_jsonl(ALERTS_LOG, skip_bad=True):
        when = parse_iso(_text(rec.get("ts")) or None)
        if when is None or ensure_utc(when) < cutoff:
            continue
        out.append(rec)
    out.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
    return out
