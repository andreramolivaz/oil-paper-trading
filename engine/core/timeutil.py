"""Timezone helpers. Everything inside the engine is UTC-aware; London/New York only at the edges."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")

# ICE Brent futures: Monday-Friday 01:00-23:00 London (verified on ice.com, 2026-10-05).
ICE_OPEN_LONDON = time(1, 0)
ICE_CLOSE_LONDON = time(23, 0)
# Settlement window 19:28:00-19:30:00 London.
ICE_SETTLEMENT_LONDON = time(19, 30)
# EIA Weekly Petroleum Status Report: Wednesday 10:30 ET (Thursday after a Monday US holiday).
EIA_WPSR_RELEASE_NY = time(10, 30)
# CFTC/ICE COT: Friday 15:30 ET with Tuesday data.
COT_RELEASE_NY = time(15, 30)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def ensure_utc(ts: datetime) -> datetime:
    """Return an aware UTC datetime; naive input is assumed to be UTC."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def to_london(ts: datetime) -> datetime:
    return ensure_utc(ts).astimezone(LONDON)


def to_new_york(ts: datetime) -> datetime:
    return ensure_utc(ts).astimezone(NEW_YORK)


def london_date(ts: datetime) -> date:
    """Trading date of a timestamp in the ICE (London) session."""
    return to_london(ts).date()


def is_ice_session_open(ts: datetime) -> bool:
    """True when ICE Brent is trading (Mon-Fri 01:00-23:00 London). Holidays handled by the calendar module."""
    local = to_london(ts)
    if local.weekday() >= 5:
        return False
    return ICE_OPEN_LONDON <= local.time() < ICE_CLOSE_LONDON


def is_after_settlement(ts: datetime) -> bool:
    """True when the London clock is past the ICE settlement window on a weekday."""
    local = to_london(ts)
    return local.weekday() < 5 and local.time() >= ICE_SETTLEMENT_LONDON


def settlement_ts(day: date) -> datetime:
    """UTC timestamp of the ICE settlement on a given London trading date."""
    return datetime.combine(day, ICE_SETTLEMENT_LONDON, tzinfo=LONDON).astimezone(UTC)


def eia_wpsr_release_ts(release_day: date) -> datetime:
    return datetime.combine(release_day, EIA_WPSR_RELEASE_NY, tzinfo=NEW_YORK).astimezone(UTC)


def cot_release_ts(release_day: date) -> datetime:
    return datetime.combine(release_day, COT_RELEASE_NY, tzinfo=NEW_YORK).astimezone(UTC)


def iso(ts: datetime | date | None) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ensure_utc(ts).isoformat().replace("+00:00", "Z")
    return ts.isoformat()


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return ensure_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))


def days_between(a: date, b: date) -> int:
    return (b - a).days


def next_weekend_gap_hours(ts: datetime) -> float:
    """Hours until the next Friday 23:00 London close; 0 if the market is already closed for the weekend."""
    local = to_london(ts)
    if local.weekday() >= 5 or (local.weekday() == 4 and local.time() >= ICE_CLOSE_LONDON):
        return 0.0
    days_ahead = 4 - local.weekday()
    close = datetime.combine(local.date() + timedelta(days=days_ahead), ICE_CLOSE_LONDON, tzinfo=LONDON)
    return max(0.0, (close - local).total_seconds() / 3600.0)
