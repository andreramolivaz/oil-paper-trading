"""The weekend watch (phase 22, ``docs/RESEARCH.md`` chapter 17 E): Kalshi trades, crude does not.

From Friday 17:00 New York to Sunday 18:00 the crude futures are closed and Kalshi's markets stay open; in a war
the news often lands there. This module keeps one number for the weekend in progress: ``W``, the change of the
de-escalation index (the one the Oracle reads, ``oracle.DEESCALATION``) from the first reading after the futures
closed on Friday to the newest reading at or before Sunday 17:00, over the components both readings have.

What it is, and is not, exactly as registered and measured:

* It is a FACT shown on the page (and flagged at five points). The tick marks the index at its first reading after
  Friday 17:00 New York; the weekend job (``engine.cli watch``, hourly at best) compares every later reading with
  that mark. If no reading was taken within three hours of the close, the mark is the archive's first reading of
  the UTC Saturday instead, and the page says how late it is.
* It is NOT a forecast of Monday's gap and it trades nothing. Measured once on 34 weekends (``MEASURED``, chapter
  17 M): the sign is the expected one (more de-escalation, a lower open: rank correlation -0.50) but the size of
  the gap could not be told from the size of the move (robust t -1.86, under the 2 the page rule asked for), and
  what the fund did AFTER the open had nothing to do with the move (t +0.26). So the page shows the move alone,
  with that sentence, and no book reads it.

Every finished weekend is one line of ``weekend-YYYY-MM.jsonl``: the live record the measurement can be repeated on.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso, parse_iso
from engine.data.raw_store import RawStore
from engine.desk import oracle, prediction
from engine.desk.options import NEW_YORK

log = logging.getLogger(__name__)

WEEKEND_FILE = "weekend.json"  # state/desk/weekend.json: the weekend in progress (or the last one)
WEEKEND_LOG = "weekend"  # state/desk/weekend-YYYY-MM.jsonl: one record per finished weekend
MARK_TIME_NY = time(17, 0)  # the futures close on Friday; one hour before they reopen on Sunday
WEEKEND_DAYS = 2
MARK_MAX_LATE_HOURS = 3.0  # a mark later than this after the close is replaced by the archive's Saturday reading
FLAG_POINTS = 0.05  # 17 J 3: a move of five points is flagged
MIN_COMPONENTS = 2
# What the one measurement found (scripts/research_weekend.py, 15 November 2026, chapter 17 M): a result shown
# beside the number, not a parameter of anything.
MEASURED: dict[str, Any] = {
    "weekends": 34,
    "span": "20 March - 6 November 2026",
    "gap_slope_pct_per_point": -0.242,
    "gap_t": -1.86,
    "gap_r2": 0.115,
    "gap_rank_corr": -0.50,
    "gap_rank_p": 0.003,
    "continuation_t": 0.26,
    "placebo_t": 0.09,
    "page_shows_gap": False,  # 17 E 7: |t| >= 2 on at least 25 weekends was asked for
    "source": "docs/RESEARCH.md 17 M: Kalshi's hourly candles against the Brent fund's open and close",
}


# ----------------------------------------------------------------------------------------------- the calendar
def friday_close(now: datetime) -> datetime:
    """The latest Friday 17:00 New York at or before ``now``."""
    local = ensure_utc(now).astimezone(NEW_YORK)
    day = local.date() - timedelta(days=(local.weekday() - 4) % 7)
    close = datetime.combine(day, MARK_TIME_NY, tzinfo=NEW_YORK)
    if close > local:
        close = datetime.combine(day - timedelta(days=7), MARK_TIME_NY, tzinfo=NEW_YORK)
    return close.astimezone(UTC)


def sunday_mark(close: datetime) -> datetime:
    """Sunday 17:00 New York of the weekend that began at ``close``: the last instant a reading counts."""
    day = ensure_utc(close).astimezone(NEW_YORK).date() + timedelta(days=WEEKEND_DAYS)
    return datetime.combine(day, MARK_TIME_NY, tzinfo=NEW_YORK).astimezone(UTC)


# ----------------------------------------------------------------------------------------------- the index
def index_of(reading: prediction.Reading) -> dict[str, float]:
    """The de-escalation index's components on one download of the markets, as the Oracle reads them."""
    readings = oracle.index_readings(prediction._event_records(reading))
    return dict(readings[-1].components) if readings else {}


def change_of(now: Mapping[str, float], mark: Mapping[str, float]) -> tuple[float | None, list[str]]:
    """(the index now minus the index at the mark over the components both have, those components); None with
    fewer than ``MIN_COMPONENTS`` in common."""
    common = sorted(set(now) & set(mark))
    if len(common) < MIN_COMPONENTS:
        return None, common
    a = sum(float(now[c]) for c in common) / len(common)
    b = sum(float(mark[c]) for c in common) / len(common)
    return a - b, common


def _archive_mark(store: StateStore, saturday: date, before: datetime) -> dict[str, Any] | None:
    """The archive's reading of the UTC Saturday (the first of that day), if it is earlier than ``before``."""
    for reading in oracle.archive_readings(store):
        if reading.day != saturday or not reading.components:
            continue
        ts = parse_iso(reading.ts) if reading.ts else None
        if ts is None or ts >= before:
            return None
        return {"ts": iso(ts), "components": dict(reading.components), "source": "archive"}
    return None


def _finish(store: StateStore, state: Mapping[str, Any]) -> bool:
    """Append the weekend of ``state`` to the log once it has a mark and a later reading. True when written."""
    weekend = str(state.get("weekend") or "")
    latest, mark = state.get("latest") or {}, state.get("mark") or {}
    if not weekend or not mark or not latest or state.get("change") is None:
        return False
    if any(str(r.get("weekend")) == weekend for r in store.tail_jsonl(WEEKEND_LOG, 60)):
        return False
    record = {
        "weekend": weekend,
        "w": state["change"],
        "components": list(state.get("common") or []),
        "mark_ts": mark.get("ts"),
        "mark_source": mark.get("source"),
        "mark_late_hours": mark.get("late_hours"),
        "last_ts": latest.get("ts"),
        "index_mark": state.get("index_mark"),
        "index_last": state.get("index_last"),
    }
    store.append_jsonl(WEEKEND_LOG, record, parse_iso(str(latest.get("ts") or "")) or datetime.now(tz=UTC))
    return True


def update(raw: RawStore, store: StateStore, now: datetime) -> dict[str, Any]:
    """What the tick and the weekend job do after every download of the markets: mark the index at the first
    reading after Friday's close, follow it until Sunday 17:00 New York, log the weekend when it is over.
    Idempotent: the same download twice changes nothing."""
    now = ensure_utc(now)
    reading = prediction.latest_reading(raw)
    if reading is None:
        return {"status": "no reading on file"}
    seen = ensure_utc(reading.seen.to_pydatetime())
    close = friday_close(seen)
    last_instant = sunday_mark(close)
    weekend = close.astimezone(NEW_YORK).date().isoformat()
    state: dict[str, Any] = dict(store.read_json(WEEKEND_FILE, {}) or {})
    out: dict[str, Any] = {"status": "ok", "weekend": weekend}
    components = index_of(reading)
    if state.get("weekend") != weekend:
        if _finish(store, state):
            out["logged"] = state.get("weekend")
        if not components:
            return {**out, "status": "the reading has no component of the index: no mark"}
        late = (seen - close).total_seconds() / 3600.0
        mark: dict[str, Any] = {"ts": iso(seen), "components": components, "source": "reading"}
        if late > MARK_MAX_LATE_HOURS:
            saturday = close.astimezone(NEW_YORK).date() + timedelta(days=1)
            try:
                earlier = _archive_mark(store, saturday, seen)
            except Exception as exc:  # the archive is on the side: a bad line costs the fallback, not the mark
                log.warning("weekend mark: the archive could not be read (%s)", exc)
                earlier = None
            if earlier is not None:
                mark = earlier
                late = ((parse_iso(mark["ts"]) or seen) - close).total_seconds() / 3600.0
        mark["late_hours"] = round(late, 2)
        state = {"weekend": weekend, "close": iso(close), "until": iso(last_instant), "mark": mark}
        out["mark"] = mark["source"]
    if components and seen <= last_instant and iso(seen) >= str((state.get("mark") or {}).get("ts") or ""):
        change, common = change_of(components, (state.get("mark") or {}).get("components") or {})
        state["latest"] = {"ts": iso(seen), "components": components}
        state["change"] = None if change is None else round(change, 5)
        state["common"] = common
        mark_components = (state.get("mark") or {}).get("components") or {}
        if common:
            state["index_mark"] = round(sum(float(mark_components[c]) for c in common) / len(common), 5)
            state["index_last"] = round(sum(float(components[c]) for c in common) / len(common), 5)
    state["over"] = bool(now > last_instant)
    if state["over"] and _finish(store, state):
        out["logged"] = weekend
    store.write_json(WEEKEND_FILE, state)
    out["change"] = state.get("change")
    return out


# ----------------------------------------------------------------------------------------------- the page
def _pts(x: float | None) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x * 100:+.1f} points"


def block(store: StateStore, now: datetime, gaps: Mapping[str, Mapping[str, float]] | None = None) -> dict[str, Any]:
    """The weekend watch for the page: the weekend in progress or the last one, whether it is flagged, the
    sentence the measurement allows, and the finished weekends with what the Brent fund did at the next open
    (``gaps``: weekend -> {"gap", "continuation"}, facts the export reads off the fund's daily bars)."""
    now = ensure_utc(now)
    state: dict[str, Any] = dict(store.read_json(WEEKEND_FILE, {}) or {})
    change = state.get("change")
    mark = state.get("mark") or {}
    until = parse_iso(str(state.get("until") or "")) if state.get("until") else None
    close = parse_iso(str(state.get("close") or "")) if state.get("close") else None
    if not state.get("weekend") or close is None or until is None:
        phase = "none"
    elif now <= until:
        phase = "in progress"
    else:
        phase = "over"
    flag = change is not None and abs(float(change)) >= FLAG_POINTS
    if phase == "none":
        text = "No weekend on file yet: the watch marks the index at the first reading after Friday 17:00 New York."
    elif change is None:
        text = "The index has fewer than two components in both readings: no weekend move to show."
    else:
        direction = "up" if change > 0 else "down"
        lead = "are" if phase == "in progress" else "ended the weekend"
        text = (
            f"De-escalation odds {lead} {direction} {abs(change) * 100:.1f} points since the futures closed on "
            f"Friday{' (flagged: five points or more)' if flag else ''}. "
            f"On {MEASURED['weekends']} past weekends a rise "
            "went with a lower Monday open more often than not, but the size of the gap could not be told from the "
            "size of the move, and what the fund did after the open had nothing to do with it."
        )
    late = mark.get("late_hours")
    history: list[dict[str, Any]] = []
    for r in store.tail_jsonl(WEEKEND_LOG, 12):
        weekend = str(r.get("weekend"))
        history.append({"weekend": weekend, "w": r.get("w"), **dict((gaps or {}).get(weekend) or {})})
    return {
        "phase": phase,
        "weekend": state.get("weekend"),
        "close": state.get("close"),
        "until": state.get("until"),
        "change": change,
        "components": list(state.get("common") or []),
        "index_mark": state.get("index_mark"),
        "index_last": state.get("index_last"),
        "mark_ts": mark.get("ts"),
        "mark_source": mark.get("source"),
        "mark_late_hours": late,
        "mark_note": (
            None
            if late is None or late <= MARK_MAX_LATE_HOURS
            else (
                f"the mark was taken {late:.0f} hours after the futures closed: "
                "what happened before it is not in the move"
            )
        ),
        "last_ts": (state.get("latest") or {}).get("ts"),
        "flag": bool(flag),
        "flag_points": FLAG_POINTS,
        "text": text,
        "measured": dict(MEASURED),
        "history": history,
    }
