"""Event calendar: scheduled binary events (EIA WPSR, OPEC+, FOMC...) from config/events.yaml plus rule-based
recurring events (ICE Brent expiries). Used for hours-to-event features, L_event leverage cuts and event cost windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.core.calendar import ice_brent_expiry, listed_months, us_holidays
from engine.core.config import load_config
from engine.core.timeutil import ensure_utc


@dataclass(frozen=True)
class ScheduledEvent:
    event_id: str
    name: str
    ts: datetime  # UTC
    binary: bool
    confirmed: bool = True
    kind: str = "oneoff"

    def hours_from(self, ts: datetime) -> float:
        return (self.ts - ensure_utc(ts)).total_seconds() / 3600.0


def _parse_time(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def _local(d: date, t: time, tz: str) -> datetime:
    return datetime.combine(d, t, tzinfo=ZoneInfo(tz)).astimezone(UTC)


class EventCalendar:
    def __init__(self, config: dict[str, Any] | None = None, config_dir: Path | None = None):
        self.cfg = config if config is not None else load_config("events", config_dir)
        self._oneoff = [self._oneoff_event(e) for e in self.cfg.get("oneoff", [])]
        self._recurring = list(self.cfg.get("recurring", []))

    # ------------------------------------------------------------------
    @staticmethod
    def _oneoff_event(e: dict[str, Any]) -> ScheduledEvent:
        d = e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"]))
        return ScheduledEvent(
            event_id=str(e["id"]),
            name=str(e.get("name", e["id"])),
            ts=_local(d, _parse_time(str(e.get("time", "12:00"))), str(e.get("tz", "UTC"))),
            binary=bool(e.get("binary", False)),
            confirmed=bool(e.get("confirmed", True)),
        )

    def _recurring_between(self, start: date, end: date) -> list[ScheduledEvent]:
        out: list[ScheduledEvent] = []
        for r in self._recurring:
            rule = r.get("rule")
            t = _parse_time(str(r.get("time", "12:00")))
            tz = str(r.get("tz", "UTC"))
            binary = bool(r.get("binary", False))
            if rule == "weekly":
                wd = int(r["weekday"])
                d = start
                while d <= end:
                    if d.weekday() == wd:
                        day = d
                        if r.get("shift_after_us_holiday") and any(
                            (d - timedelta(days=k)) in us_holidays(d.year) for k in range(1, 3)
                        ):
                            day = d + timedelta(days=1)  # EIA shifts to Thursday after a Monday/Tuesday holiday
                        out.append(
                            ScheduledEvent(str(r["id"]), str(r["name"]), _local(day, t, tz), binary, True, "recurring")
                        )
                    d += timedelta(days=1)
            elif rule == "ice_brent_expiry":
                for y, m in listed_months("BZ", start, 4):
                    exp = ice_brent_expiry(y, m)
                    if start <= exp <= end:
                        out.append(
                            ScheduledEvent(
                                f"{r['id']}_{y}{m:02d}", str(r["name"]), _local(exp, t, tz), binary, True, "recurring"
                            )
                        )
        return out

    # ------------------------------------------------------------------
    def events_between(self, start: datetime, end: datetime, include_unconfirmed: bool = True) -> list[ScheduledEvent]:
        s, e = ensure_utc(start), ensure_utc(end)
        evs = [x for x in self._oneoff if s <= x.ts <= e and (include_unconfirmed or x.confirmed)]
        evs += [
            x
            for x in self._recurring_between(s.date() - timedelta(days=1), e.date() + timedelta(days=1))
            if s <= x.ts <= e
        ]
        return sorted(evs, key=lambda x: x.ts)

    def next_events(self, ts: datetime, horizon_days: int = 14, binary_only: bool = False) -> list[ScheduledEvent]:
        t = ensure_utc(ts)
        evs = self.events_between(t, t + timedelta(days=horizon_days))
        return [x for x in evs if x.binary] if binary_only else evs

    def hours_to_next_binary(self, ts: datetime, horizon_days: int = 30) -> tuple[float, ScheduledEvent | None]:
        evs = self.next_events(ts, horizon_days, binary_only=True)
        if not evs:
            return float("inf"), None
        return evs[0].hours_from(ts), evs[0]

    def in_event_window(self, ts: datetime, hours_before: float = 24.0, hours_after: float = 6.0) -> bool:
        """True when a binary event is within [-hours_after, +hours_before] of ts (wider costs, no new leverage)."""
        t = ensure_utc(ts)
        evs = self.events_between(t - timedelta(hours=hours_after), t + timedelta(hours=hours_before))
        return any(x.binary for x in evs)

    def is_hurricane_season(self, d: date) -> bool:
        for r in self._recurring:
            if r.get("rule") == "season":
                sm, sd = (int(x) for x in str(r["start"]).split("-"))
                em, ed = (int(x) for x in str(r["end"]).split("-"))
                return date(d.year, sm, sd) <= d <= date(d.year, em, ed)
        return False

    def episodes(self) -> list[dict[str, Any]]:
        return list(self.cfg.get("historical_episodes", []))
