"""The speculative Kalshi book (phase 14): a forecast read on the prediction markets' daily archive.

The owner asked for "a highly speculative, leveraged strategy based on Kalshi". A Kalshi contract pays one
dollar if an event happens and costs its probability: it cannot be bought on margin, and the desk has no paper
broker for event contracts. So the leverage is not on Kalshi, it is on oil: the book trades the micro WTI future
(/MCL, the one leveraged instrument the desk has) and Kalshi gives it the DIRECTION. Everything here was written
down in ``docs/RESEARCH.md`` (chapter 10) before the book took a decision, because there is no history of these
markets to test a rule on: the archive the desk keeps (``state/desk/kalshi-YYYY-MM.jsonl``, one reading a day)
IS the history, and the first test is possible when it is a year long. Until then the book is judged on its
live fills alone, has no backtest, and is never promoted by the lifecycle rule.

The rule, exactly as registered:

1. Three deadline ladders, the three ways the war ends (``COMPONENTS``): the strait back to normal before a
   date, a US-Iran senior-level meeting before a date, a nuclear deal before a date. Equal weights.
2. For each, the probability that the event happens within ``HORIZON_DAYS`` of the reading, read off the
   ladder: the points (deadline, mid) of the markets quoted two-sided within ``MAX_SPREAD``, plus (today, 0)
   because an open market has not happened yet, interpolated linearly in time; beyond the last deadline, the
   last value. A fixed deadline loses probability by itself as the days pass; a constant horizon removes that
   drift to first order.
3. The de-escalation index D is the mean of the components present.
4. The decision of day D reads the archived reading of UTC day D (the first of the day, about 00:20 UTC, i.e.
   the previous evening in New York - like the macro sleeves read yesterday's close) or the newest one at most
   ``STALE_DAYS`` old; the reference is the newest reading between ``LOOKBACK_DAYS`` and ``LOOKBACK_DAYS +
   REFERENCE_WINDOW_DAYS`` days before. The change is the mean, over the components present in BOTH readings,
   of p_today - p_reference: a ladder that appears or resolves is not a move.
5. Forecast F = -``FORECAST_PER_POINT`` x (change in percentage points), capped at +-20, and 0 inside the
   ``DEADBAND`` (one cent is the spread). Five points of de-escalation in a week: -10, a normal short; ten:
   -20, the cap; five points of escalation: +10, long. The scale is a declared round number, to be measured
   after 60 readings (the mean |weekly change|, written in the research notes) and touched only if off by a
   factor of two.
6. Without a recent reading the book has no view: forecast 0, flat. Without a reference (the archive is
   younger than a week) it takes no decision and stays where it is - flat, at the start.

The prior, written down with the rule: no edge. Oil is a thousand times more liquid than these markets; it is
likelier that Kalshi follows oil by a day than the reverse, in which case this is yesterday's oil trend read
with the opposite sign. The book exists to measure that with paper money.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from engine.core.store import StateStore
from engine.desk.prediction import KALSHI_LOG, deadline_of

log = logging.getLogger(__name__)

FAMILY = "kalshi"  # BookConfig.forecast of a book that reads this forecast instead of the seven sleeves
SLEEVE = "deescalation"
SLEEVES: tuple[str, ...] = (SLEEVE,)
DEFAULT_WEIGHTS: dict[str, float] = {SLEEVE: 1.0}
FORECAST_CAP = 20.0
HORIZON_DAYS = 90
LOOKBACK_DAYS = 7
REFERENCE_WINDOW_DAYS = 3
STALE_DAYS = 3
MAX_SPREAD = 0.10
DEADBAND = 0.01
FORECAST_PER_POINT = 2.0
# the three deadline ladders the index is made of, and how the rationale names them
COMPONENTS: dict[str, str] = {
    "KXHORMUZNORM": "the strait back to normal",
    "KXUSIRANMEETING": "a US-Iran senior-level meeting",
    "KXUSAIRANAGREEMENT": "a US-Iran nuclear deal",
}
# the keys of the forecast dict besides the sleeve: every input of a decision, so that the rule can be replayed
# on the archive line by line (the reading days are given as ages in days from the decision day)
KEY_INDEX = "kalshi_index"
KEY_CHANGE = "kalshi_change"
KEY_COMMON = "kalshi_components"
KEY_AGE = "kalshi_age_days"
KEY_REF_AGE = "kalshi_ref_age_days"
COMPONENT_PREFIX = "kalshi:"
REFERENCE_PREFIX = "kalshi_ref:"


# ----------------------------------------------------------------------------------------------- the ladder
def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def two_sided_mid(bid: float | None, ask: float | None, max_spread: float = MAX_SPREAD) -> float | None:
    """The probability of a market quoted on both sides within ``max_spread``; None otherwise. On this API a
    bid of 0 means no bid and an ask of 1 no ask (the fixture has dozens of each): a resting order on each side
    is required. A last trade is not enough here: a market nobody quotes is an absence, not a probability."""
    if bid is None or ask is None or not (0.0 < bid <= ask < 1.0) or ask - bid > max_spread:
        return None
    return (bid + ask) / 2.0


def horizon_probability(
    points: Iterable[tuple[date, float]], asof: date, horizon_days: int = HORIZON_DAYS
) -> float | None:
    """P(the event happens within ``horizon_days`` of ``asof``) read off a deadline ladder given as (deadline,
    probability) points: linear in time between the points, with (asof, 0) as the first one; the last value
    beyond the last deadline. Deadlines not after ``asof`` are ignored (those markets are over). None when the
    ladder has no usable point."""
    ladder = sorted((d, p) for d, p in points if d > asof)
    if not ladder:
        return None
    merged: dict[date, float] = {}
    for d, p in ladder:  # two markets on one date (two events of a series): the mean of what they quote
        merged[d] = (merged[d] + p) / 2.0 if d in merged else p
    curve = [(asof, 0.0), *sorted(merged.items())]
    target = asof + timedelta(days=horizon_days)
    if target >= curve[-1][0]:
        return float(curve[-1][1])
    for (d0, p0), (d1, p1) in pairwise(curve):
        if d0 <= target <= d1:
            span = (d1 - d0).days
            return float(p0) if span == 0 else float(p0 + (p1 - p0) * (target - d0).days / span)
    return None  # pragma: no cover - the loop covers [asof, last deadline]


# ----------------------------------------------------------------------------------------------- the archive
@dataclass(frozen=True)
class IndexReading:
    """The index on one archived reading: the day of the archive (UTC) and each component's probability."""

    day: date
    ts: str
    components: dict[str, float] = field(default_factory=dict)

    @property
    def value(self) -> float | None:
        if not self.components:
            return None
        return sum(self.components.values()) / len(self.components)


def _markets_of(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    columns = [str(c) for c in record.get("columns") or ()]
    out: list[dict[str, Any]] = []
    for row in record.get("markets") or ():
        if isinstance(row, list | tuple) and len(row) >= len(columns):
            out.append(dict(zip(columns, row, strict=False)))
    return out


def _ladder_points(markets: list[dict[str, Any]], title: str) -> list[tuple[date, float]]:
    points: list[tuple[date, float]] = []
    for m in markets:
        deadline = deadline_of(str(m.get("label") or "")) or deadline_of(title)
        mid = two_sided_mid(_num(m.get("yes_bid")), _num(m.get("yes_ask")))
        if deadline is not None and mid is not None:
            points.append((deadline, mid))
    return points


def index_readings(records: Iterable[Mapping[str, Any]]) -> list[IndexReading]:
    """The index on every day of the archive, from its records (``prediction.record_reading`` writes one per
    event and UTC day). A series with two open events on one day counts the busier one. Days are sorted."""
    by_day: dict[str, dict[str, tuple[float, str, list[tuple[date, float]]]]] = {}
    stamps: dict[str, str] = {}
    for rec in records:
        series = str(rec.get("series") or "")
        if series not in COMPONENTS:
            continue
        day_text = str(rec.get("day") or "")[:10]
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            continue
        markets = _markets_of(rec)
        volume = sum(_num(m.get("volume")) or 0.0 for m in markets)
        points = _ladder_points(markets, str(rec.get("title") or ""))
        stamps[day_text] = max(stamps.get(day_text, ""), str(rec.get("ts") or ""))
        current = by_day.setdefault(day_text, {}).get(series)
        if current is None or volume > current[0]:
            by_day[day_text][series] = (volume, str(rec.get("event") or ""), points)
    out: list[IndexReading] = []
    for day_text in sorted(by_day):
        day = date.fromisoformat(day_text)
        components: dict[str, float] = {}
        for series, (_volume, _event, points) in by_day[day_text].items():
            p = horizon_probability(points, day)
            if p is not None:
                components[series] = p
        out.append(IndexReading(day=day, ts=stamps.get(day_text, ""), components=components))
    return out


def archive_readings(store: StateStore) -> list[IndexReading]:
    """``index_readings`` on the desk's archive (``state/desk/kalshi-*.jsonl``)."""
    return index_readings(store.iter_jsonl(KALSHI_LOG, skip_bad=True))


# ----------------------------------------------------------------------------------------------- the forecast
@dataclass(frozen=True)
class OracleForecast:
    """What the book is handed for one decision day, and why."""

    day: date
    reading: IndexReading | None
    reference: IndexReading | None
    common: tuple[str, ...]
    change: float | None  # mean over the common components of p_today - p_reference
    forecast: float | None  # the sleeve's value: None when the book cannot decide, 0 when it has no view
    note: str

    def as_forecast(self) -> dict[str, float | None]:
        """The forecast dict a ``Book`` reads: the sleeve plus every input of the decision, as numbers."""
        out: dict[str, float | None] = {SLEEVE: self.forecast}
        if self.reading is not None:
            out[KEY_INDEX] = self.reading.value
            out[KEY_AGE] = float((self.day - self.reading.day).days)
            for series, p in self.reading.components.items():
                out[f"{COMPONENT_PREFIX}{series}"] = p
        if self.reference is not None:
            out[KEY_REF_AGE] = float((self.day - self.reference.day).days)
            for series in self.common:
                out[f"{REFERENCE_PREFIX}{series}"] = self.reference.components[series]
        out[KEY_CHANGE] = self.change
        out[KEY_COMMON] = float(len(self.common))
        return out

    def to_dict(self) -> dict[str, Any]:
        """For the terminal (``desk.json``)."""
        reading, reference = self.reading, self.reference
        common = list(self.common)
        return {
            "day": self.day.isoformat(),
            "forecast": None if self.forecast is None else round(self.forecast, 2),
            # the index as it is today (every component read); the two numbers the CHANGE is made of are over
            # the components both readings have, so that change = index_common - index_reference exactly
            "index": None if reading is None or reading.value is None else round(reading.value, 4),
            "index_common": (
                None
                if reading is None or not common
                else round(sum(reading.components[s] for s in common) / len(common), 4)
            ),
            "index_reference": (
                None
                if reference is None or not common
                else round(sum(reference.components[s] for s in common) / len(common), 4)
            ),
            "components_common": len(common),
            "components_read": 0 if reading is None else len(reading.components),
            "change": None if self.change is None else round(self.change, 4),
            "reading_day": None if reading is None else reading.day.isoformat(),
            "reading_ts": None if reading is None else reading.ts or None,
            "reference_day": None if reference is None else reference.day.isoformat(),
            "components": [
                {
                    "series": series,
                    "label": label,
                    "p": (
                        None
                        if reading is None or series not in reading.components
                        else round(reading.components[series], 4)
                    ),
                    "p_reference": (
                        None
                        if reference is None or series not in reference.components
                        else round(reference.components[series], 4)
                    ),
                }
                for series, label in COMPONENTS.items()
            ],
            "note": self.note,
            "rule": describe(),
        }


def forecast_on(readings: list[IndexReading], day: date) -> OracleForecast:
    """The rule of the module docstring, point 4 to 6, for a decision taken on ``day``.

    A day whose records held no readable component (every quote one-sided or wider than the limit, a label the
    parser does not know) is not a reading: the newest reading WITH components within ``STALE_DAYS`` is today's
    view, as the rule says "the newest one at most three days old". Closing a leveraged position on a quote
    outage and reopening it the next day is the round trip that rule exists to avoid.
    """
    usable = [r for r in readings if r.components]
    latest = [r for r in usable if r.day <= day]
    reading = latest[-1] if latest else None
    if reading is None:
        return OracleForecast(
            day, None, None, (), None, None, "no Kalshi reading in the archive: the book cannot decide"
        )
    if (day - reading.day).days > STALE_DAYS:
        return OracleForecast(
            day, reading, None, (), None, 0.0,
            f"the newest Kalshi reading is {(day - reading.day).days} days old (more than {STALE_DAYS}): no view, flat",
        )  # fmt: skip
    first, last = day - timedelta(days=LOOKBACK_DAYS + REFERENCE_WINDOW_DAYS), day - timedelta(days=LOOKBACK_DAYS)
    candidates = [r for r in usable if first <= r.day <= last]
    reference = candidates[-1] if candidates else None
    if reference is None:
        return OracleForecast(
            day, reading, None, (), None, None,
            f"no Kalshi reading {LOOKBACK_DAYS} to {LOOKBACK_DAYS + REFERENCE_WINDOW_DAYS} days ago: the book waits "
            "for a week of archive and holds what it has",
        )  # fmt: skip
    common = tuple(s for s in COMPONENTS if s in reading.components and s in reference.components)
    if not common:
        return OracleForecast(
            day, reading, reference, (), None, 0.0, "no component present in both readings: no measurable move, flat"
        )
    change = sum(reading.components[s] - reference.components[s] for s in common) / len(common)
    if abs(change) < DEADBAND:
        forecast, why = 0.0, f"inside the deadband of {DEADBAND * 100:.0f} point"
    else:
        raw = -FORECAST_PER_POINT * change * 100.0
        forecast = max(-FORECAST_CAP, min(FORECAST_CAP, raw))
        why = "capped at +-20" if abs(raw) > FORECAST_CAP else f"{FORECAST_PER_POINT:g} per point"
    note = (
        f"de-escalation index {reading.value:.1%} on {reading.day.isoformat()} against "
        f"{sum(reference.components[s] for s in common) / len(common):.1%} on {reference.day.isoformat()} "
        f"({len(common)} of {len(COMPONENTS)} components in both): {change * 100:+.1f} points -> forecast "
        f"{forecast:+.1f} ({why})"
    )
    return OracleForecast(day, reading, reference, common, change, forecast, note)


def forecast_for(store: StateStore, day: date) -> OracleForecast:
    """``forecast_on`` the desk's archive, for the decision of ``day``."""
    return forecast_on(archive_readings(store), day)


# ----------------------------------------------------------------------------------------------- the words
def _pct(x: float | None) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x * 100:.1f} %"


def lead_text(forecast: Mapping[str, float | None]) -> str:
    """The first sentence of a decision's rationale, from the forecast dict alone (``as_forecast``): what the
    three ladders say today and a week ago, the change, the forecast."""
    value = forecast.get(SLEEVE)
    change = forecast.get(KEY_CHANGE)
    parts = []
    for series, label in COMPONENTS.items():
        today = forecast.get(f"{COMPONENT_PREFIX}{series}")
        if today is None:
            continue
        before = forecast.get(f"{REFERENCE_PREFIX}{series}")
        parts.append(f"{label} {_pct(today)}" + (f" (a week ago {_pct(before)})" if before is not None else ""))
    head = f"Kalshi, within {HORIZON_DAYS} days: " + (", ".join(parts) if parts else "no component read")
    index = forecast.get(KEY_INDEX)
    if index is not None:
        head += f"; index {_pct(index)}"
    if change is not None:
        head += f", {change * 100:+.1f} points in a week"
    tail = "no forecast" if value is None or not math.isfinite(value) else f"forecast {value:+.1f} out of 20"
    return f"{head}: {tail}. "


def describe() -> dict[str, Any]:
    """The rule in force, for the page and the state."""
    return {
        "components": [{"series": s, "label": label} for s, label in COMPONENTS.items()],
        "horizon_days": HORIZON_DAYS,
        "lookback_days": LOOKBACK_DAYS,
        "reference_window_days": REFERENCE_WINDOW_DAYS,
        "stale_days": STALE_DAYS,
        "max_spread": MAX_SPREAD,
        "deadband": DEADBAND,
        "forecast_per_point": FORECAST_PER_POINT,
        "text": (
            f"The mean probability, on Kalshi, that within {HORIZON_DAYS} days the strait is back to normal, the US "
            f"and Iran meet at senior level and they sign a nuclear deal (read off each deadline ladder, markets "
            f"quoted within {MAX_SPREAD * 100:.0f} cents). Its change over {LOOKBACK_DAYS} days, times "
            f"-{FORECAST_PER_POINT:g} per point, is the forecast: de-escalation rising means short oil, falling "
            f"means long; under {DEADBAND * 100:.0f} point, no view. Capped at +-20. No backtest: there is no "
            "history of these markets; the book is judged on its live fills only and is never promoted."
        ),
    }
