"""The speculative Kalshi books (phases 14 and 15): forecasts read on the prediction markets' daily archive.

The owner asked for "a highly speculative, leveraged strategy based on Kalshi". A Kalshi contract pays one
dollar if an event happens and costs its probability: it cannot be bought on margin, and the desk has no paper
broker for event contracts. So the leverage is not on Kalshi, it is on the vehicle: a book trades an instrument
the desk already holds and Kalshi gives it the DIRECTION. Everything here was written down in ``docs/RESEARCH.md``
(chapter 10 for the de-escalation index and the oil book, chapter 11 for the indices on the other markets) before
a book took a decision, and the archive the desk keeps (``state/desk/kalshi-YYYY-MM.jsonl``, one reading a day,
its past filled from Kalshi's daily candlesticks) is the only history the rules are measured on. The books have
no backtest in the desk's replay and are never promoted by the lifecycle rule.

The rule, exactly as registered, for every index (``INDICES``):

1. An index is made of COMPONENTS read on the archive. The de-escalation index: three deadline ladders, the
   three ways the war ends (the strait back to normal before a date, a US-Iran senior-level meeting before a
   date, a nuclear deal before a date), equal weights. The Fed path: the FOMC meetings within the horizon, each
   worth the expected change of the target rate at that meeting in basis points. The recession probability:
   the yearly NBER markets read as one ladder by year end.
2. For a deadline ladder, the probability that the event happens within ``horizon_days`` of the reading: the
   points (deadline, mid) of the markets quoted two-sided within ``MAX_SPREAD``, plus (today, 0) because an open
   market has not happened yet, interpolated linearly in time; beyond the last deadline, the last value. A fixed
   deadline loses probability by itself as the days pass; a constant horizon removes that drift to first order.
3. The index is the mean of the components present (the sum, for the Fed path: the expected cumulative change
   over the horizon).
4. The decision of day D reads the archived reading of UTC day D (the first of the day, about 00:20 UTC, i.e.
   the previous evening in New York - like the macro sleeves read yesterday's close) or the newest one at most
   ``STALE_DAYS`` old; the reference is the newest reading between ``LOOKBACK_DAYS`` and ``LOOKBACK_DAYS +
   REFERENCE_WINDOW_DAYS`` days before. The change is the mean (or sum), over the components present in BOTH
   readings, of today - reference: a ladder that appears or resolves, a meeting that passes, is not a move.
5. The sleeve's value F = ``per_unit`` x the change (in percentage points, or in basis points), capped at
   +-20, and 0 inside the ``deadband``. The de-escalation index: -2 per point (five points of de-escalation in a
   week: -10, a normal short for the oil book); the Fed path: -2.5 per basis point (positive when expectations
   turn dovish); recession: +4 per point (positive when the odds of a recession rise). A BOOK reads one or more
   sleeves with a SIGN of its own (``BookConfig.sleeves``) and averages them. Every scale is a declared round
   number, checked against the archive (the mean |F| should be about 10) and touched only if off by a factor of
   two.
6. Without a recent reading a sleeve has no view: 0, flat. Without a reference (the archive is younger than a
   week) it has no value and a book whose sleeves all lack one takes no decision and stays where it is.

The prior, written down with the rules: no edge. The vehicles are a thousand times more liquid than these markets
and their traders read the same news; it is likelier that Kalshi follows the price by a day than the reverse, in
which case this is yesterday's move read with a sign. The books exist to measure that with paper money.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from engine.core.store import StateStore
from engine.desk.prediction import KALSHI_LOG, deadline_of

log = logging.getLogger(__name__)

FAMILY = "kalshi"  # BookConfig.forecast of a book that reads these forecasts instead of the seven sleeves
FORECAST_CAP = 20.0
LOOKBACK_DAYS = 7
REFERENCE_WINDOW_DAYS = 3
STALE_DAYS = 3
MAX_SPREAD = 0.10
# the de-escalation index's own constants (phase 14), kept under their registered names
HORIZON_DAYS = 90
DEADBAND = 0.01
FORECAST_PER_POINT = 2.0
COMPONENTS: dict[str, str] = {
    "KXHORMUZNORM": "the strait back to normal",
    "KXUSIRANMEETING": "a US-Iran senior-level meeting",
    "KXUSAIRANAGREEMENT": "a US-Iran nuclear deal",
}
# the Fed path (phase 15): the outcomes of a meeting's markets by the last segment of their ticker, in basis
# points ("more than 25" counts as 50: a declared convention), and the least probability mass the quoted
# outcomes must add up to for the meeting to be readable (what is missing is one-cent tails)
MEETING_OUTCOMES_BP: dict[str, float] = {"C26": -50.0, "C25": -25.0, "H0": 0.0, "H25": 25.0, "H26": 50.0}
MIN_OUTCOME_MASS = 0.80
MIN_OUTCOMES = 2
CERTAIN_BID = 0.95  # an outcome bid at least this with no ask is read at its bid
# the scales of the phase-15 sleeves. Declared as 1 per basis point and 2 per point (docs/RESEARCH.md 11 B) with
# the rule that Carver's normalisation - the mean |F| should be about 10 - is checked once on the archive and the
# scale touched only if off by a factor of two: measured 3.9 and 4.7 on the archive through 14 October 2026
# (11 E), both off by more than two, so 2.5 and 4; re-measured after 60 live readings
FORECAST_PER_BP = 2.5
RECESSION_PER_POINT = 4.0
RECESSION_HORIZON_DAYS = 365

KIND_DEADLINES = "deadlines"  # a probability per series, read off its deadline ladder at a constant horizon
KIND_MEETINGS = "meetings"  # basis points per event (an FOMC meeting), the events within the horizon
KIND_YEARS = "years"  # a probability per series, its yearly events read as one ladder by year end


@dataclass(frozen=True)
class IndexSpec:
    """One index: what it reads, how, and what the sleeve is worth per unit of weekly change."""

    id: str  # the sleeve's name in a forecast dict and in BookConfig.sleeves
    label: str
    kind: str
    components: dict[str, str]  # series -> how the rationale names it
    horizon_days: int
    per_unit: float  # the sleeve's value per percentage point (probabilities) or per basis point (bp), signed
    deadband: float  # in index units: a fraction of probability, or basis points
    aggregate: str = "mean"  # "mean" | "sum": how the components make the index and the change
    unit: str = "points"  # "points": components are probabilities, shown in %; "bp": basis points

    @property
    def unit_scale(self) -> float:
        return 100.0 if self.unit == "points" else 1.0

    def combine(self, values: Iterable[float]) -> float | None:
        vals = list(values)
        if not vals:
            return None
        return float(sum(vals)) if self.aggregate == "sum" else float(sum(vals) / len(vals))

    def fmt(self, x: float | None) -> str:
        if x is None or not math.isfinite(x):
            return "n/a"
        return f"{x * 100:.1f} %" if self.unit == "points" else f"{x:+.0f} bp"

    def fmt_change(self, x: float) -> str:
        return f"{x * 100:+.1f} points" if self.unit == "points" else f"{x:+.1f} bp"


DEESCALATION = IndexSpec(
    id="deescalation",
    label="de-escalation index",
    kind=KIND_DEADLINES,
    components=COMPONENTS,
    horizon_days=HORIZON_DAYS,
    per_unit=-FORECAST_PER_POINT,
    deadband=DEADBAND,
)
FEDPATH = IndexSpec(
    id="fedpath",
    label="Fed path",
    kind=KIND_MEETINGS,
    components={"KXFEDDECISION": "the FOMC meeting"},
    horizon_days=HORIZON_DAYS,
    per_unit=-FORECAST_PER_BP,
    deadband=1.0,
    aggregate="sum",
    unit="bp",
)
RECESSION = IndexSpec(
    id="recession",
    label="recession probability",
    kind=KIND_YEARS,
    components={"KXRECSSNBER": "a US recession"},
    horizon_days=RECESSION_HORIZON_DAYS,
    per_unit=RECESSION_PER_POINT,
    deadband=DEADBAND,
)
INDICES: dict[str, IndexSpec] = {s.id: s for s in (DEESCALATION, FEDPATH, RECESSION)}
SLEEVE = DEESCALATION.id
SLEEVES: tuple[str, ...] = tuple(INDICES)
DEFAULT_WEIGHTS: dict[str, float] = {SLEEVE: 1.0}  # a kalshi book without `sleeves` is the oil book of phase 14
# every series an index reads: what the tick downloads hourly and the backfill fills
ALL_SERIES: tuple[str, ...] = tuple(dict.fromkeys(s for spec in INDICES.values() for s in spec.components))


def spec_of(sleeve: str) -> IndexSpec:
    try:
        return INDICES[sleeve]
    except KeyError as exc:
        raise ValueError(f"unknown Kalshi index {sleeve!r}; known: {sorted(INDICES)}") from exc


# the keys of the forecast dict besides the sleeve: every input of a decision, so that the rule can be replayed
# on the archive line by line (the reading days are given as ages in days from the decision day)
def key_index(spec: IndexSpec) -> str:
    return f"{spec.id}:index"


def key_change(spec: IndexSpec) -> str:
    return f"{spec.id}:change"


def key_common(spec: IndexSpec) -> str:
    return f"{spec.id}:common"


def key_age(spec: IndexSpec) -> str:
    return f"{spec.id}:age_days"


def key_ref_age(spec: IndexSpec) -> str:
    return f"{spec.id}:ref_age_days"


def key_component(spec: IndexSpec, component: str) -> str:
    return f"{spec.id}:c:{component}"


def key_reference(spec: IndexSpec, component: str) -> str:
    return f"{spec.id}:r:{component}"


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


def meeting_expectation(markets: list[dict[str, Any]]) -> float | None:
    """The expected change of the target rate at one FOMC meeting, in basis points, from the outcome markets of
    its event (``MEETING_OUTCOMES_BP`` by the ticker's last segment): the mean of the outcomes quoted two-sided,
    weighted by their mids and normalised by their sum. None when fewer than ``MIN_OUTCOMES`` are quoted or they
    add up to less than ``MIN_OUTCOME_MASS``: a meeting half of whose distribution is unquoted is not read."""
    mids: dict[str, float] = {}
    for m in markets:
        code = str(m.get("ticker") or "").rsplit("-", 1)[-1].upper()
        bp = MEETING_OUTCOMES_BP.get(code)
        bid, ask = _num(m.get("yes_bid")), _num(m.get("yes_ask"))
        mid = two_sided_mid(bid, ask)
        if mid is None and bid is not None and ask is not None and bid >= CERTAIN_BID and ask >= 1.0:
            # a near-certain outcome in the days before a meeting is bid 0.99 with nobody offering at 1.00:
            # that is a probability, not an absence (found in review; declared, not yet seen in the archive)
            mid = bid
        if bp is not None and mid is not None:
            mids[code] = mid
    mass = sum(mids.values())
    if len(mids) < MIN_OUTCOMES or mass < MIN_OUTCOME_MASS:
        return None
    return float(sum(p * MEETING_OUTCOMES_BP[c] for c, p in mids.items()) / mass)


_YEAR_SUFFIX = re.compile(r"-(\d{2}|\d{4})$")
_MEETING_SUFFIX = re.compile(r"-(\d{2})([A-Z]{3})$")
_YEAR_IN_TITLE = re.compile(r"\b(20\d\d)\b")


def year_of(event: str, title: str = "") -> int | None:
    """The year a yearly market is about: ``KXRECSSNBER-26`` -> 2026 (the ticker's suffix), else the year in
    its title ("Will there be a recession in 2027?")."""
    m = _YEAR_SUFFIX.search(event or "")
    if m:
        n = int(m.group(1))
        return 2000 + n if n < 100 else n
    t = _YEAR_IN_TITLE.search(title or "")
    return int(t.group(1)) if t else None


def year_ladder(years: Mapping[int, float], asof: date) -> list[tuple[date, float]]:
    """The yearly markets ("a recession in 2026", "... in 2027") as a deadline ladder: P(by 31 December of year
    Y) = 1 - prod(1 - p_y) over the years up to Y whose end is after ``asof`` (independence between years: a
    declared approximation)."""
    points: list[tuple[date, float]] = []
    survive = 1.0
    for year in sorted(years):
        end = date(year, 12, 31)
        if end <= asof:
            continue
        survive *= 1.0 - years[year]
        points.append((end, 1.0 - survive))
    return points


# ----------------------------------------------------------------------------------------------- the archive
@dataclass(frozen=True)
class IndexReading:
    """The index on one archived reading: the day of the archive (UTC) and each component's value (a probability,
    or basis points for the Fed path), plus the components' dates where they have one (meetings)."""

    day: date
    ts: str
    components: dict[str, float] = field(default_factory=dict)
    dates: dict[str, date] = field(default_factory=dict)
    spec: IndexSpec = DEESCALATION
    # the yearly quotes a ``years`` component was read from (series -> year -> probability): the change over a
    # week is read on the years BOTH readings quote, at today's date, so that a year market that lists, settles
    # or passes its end is not a move (found in review: the end of 2026 and the listing of the 2027 market each
    # moved the index by four to eight points with every quote unchanged)
    years: dict[str, dict[int, float]] = field(default_factory=dict)

    @property
    def value(self) -> float | None:
        return self.spec.combine(self.components.values())


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


def _day_of(record: Mapping[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(record.get("day") or "")[:10])
    except ValueError:
        return None


def _close_date(markets: list[dict[str, Any]]) -> date | None:
    """The day a meeting's markets close: the decision day."""
    dates = []
    for m in markets:
        try:
            dates.append(date.fromisoformat(str(m.get("close_time") or "")[:10]))
        except ValueError:
            continue
    return max(dates) if dates else None


def _readings_deadlines(records: Iterable[Mapping[str, Any]], spec: IndexSpec) -> list[IndexReading]:
    """One probability per series and day, read off the busier event's ladder when a series has two open."""
    by_day: dict[str, dict[str, tuple[float, str, list[tuple[date, float]]]]] = {}
    stamps: dict[str, str] = {}
    for rec in records:
        series = str(rec.get("series") or "")
        if series not in spec.components:
            continue
        day = _day_of(rec)
        if day is None:
            continue
        day_text = day.isoformat()
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
            p = horizon_probability(points, day, spec.horizon_days)
            if p is not None:
                components[series] = p
        out.append(IndexReading(day=day, ts=stamps.get(day_text, ""), components=components, spec=spec))
    return out


def _readings_meetings(records: Iterable[Mapping[str, Any]], spec: IndexSpec) -> list[IndexReading]:
    """Basis points per meeting (event) and day, for the meetings decided within the horizon of the day."""
    by_day: dict[str, dict[str, tuple[float, date]]] = {}
    stamps: dict[str, str] = {}
    for rec in records:
        if str(rec.get("series") or "") not in spec.components:
            continue
        day = _day_of(rec)
        if day is None:
            continue
        day_text = day.isoformat()
        stamps[day_text] = max(stamps.get(day_text, ""), str(rec.get("ts") or ""))
        by_day.setdefault(day_text, {})
        markets = _markets_of(rec)
        meeting = _close_date(markets)
        if meeting is None or not (day <= meeting <= day + timedelta(days=spec.horizon_days)):
            continue
        expected = meeting_expectation(markets)
        if expected is not None:
            by_day[day_text][str(rec.get("event") or "")] = (expected, meeting)
    out: list[IndexReading] = []
    for day_text in sorted(by_day):
        meetings = by_day[day_text]
        out.append(
            IndexReading(
                day=date.fromisoformat(day_text),
                ts=stamps.get(day_text, ""),
                components={event: bp for event, (bp, _d) in sorted(meetings.items(), key=lambda kv: kv[1][1])},
                dates={event: d for event, (_bp, d) in meetings.items()},
                spec=spec,
            )
        )
    return out


def _readings_years(records: Iterable[Mapping[str, Any]], spec: IndexSpec) -> list[IndexReading]:
    """One probability per series and day: its yearly events (one market each) as a ladder by year end."""
    by_day: dict[str, dict[str, dict[int, float]]] = {}
    stamps: dict[str, str] = {}
    for rec in records:
        series = str(rec.get("series") or "")
        if series not in spec.components:
            continue
        day = _day_of(rec)
        if day is None:
            continue
        day_text = day.isoformat()
        stamps[day_text] = max(stamps.get(day_text, ""), str(rec.get("ts") or ""))
        years = by_day.setdefault(day_text, {}).setdefault(series, {})
        year = year_of(str(rec.get("event") or ""), str(rec.get("title") or ""))
        if year is None:
            continue
        mids = [two_sided_mid(_num(m.get("yes_bid")), _num(m.get("yes_ask"))) for m in _markets_of(rec)]
        quoted = [p for p in mids if p is not None]
        if quoted:
            years[year] = sum(quoted) / len(quoted)
    out: list[IndexReading] = []
    for day_text in sorted(by_day):
        day = date.fromisoformat(day_text)
        components: dict[str, float] = {}
        for series, years in by_day[day_text].items():
            p = horizon_probability(year_ladder(years, day), day, spec.horizon_days)
            if p is not None:
                components[series] = p
        out.append(
            IndexReading(
                day=day,
                ts=stamps.get(day_text, ""),
                components=components,
                spec=spec,
                years={k: dict(v) for k, v in by_day[day_text].items()},
            )
        )
    return out


def common_values(
    reading: IndexReading, reference: IndexReading, day: date, spec: IndexSpec
) -> tuple[dict[str, float], dict[str, float]]:
    """The values the change is read on, component by component, for the components BOTH readings have: today's
    and the reference's. For a deadline ladder and the meetings, the components as read. For the yearly ladder
    (``KIND_YEARS``), both are re-read at ``day`` on the years both readings quote: the reference's quotes on
    today's ladder, so that only a quote moving is a move."""
    if spec.kind != KIND_YEARS:
        names = reading.components if spec.kind == KIND_MEETINGS else spec.components
        common = [c for c in names if c in reading.components and c in reference.components]
        return {c: reading.components[c] for c in common}, {c: reference.components[c] for c in common}
    today: dict[str, float] = {}
    before: dict[str, float] = {}
    for series in spec.components:
        q_now, q_ref = reading.years.get(series, {}), reference.years.get(series, {})
        years = sorted(set(q_now) & set(q_ref))
        if not years:
            continue
        p_now = horizon_probability(year_ladder({y: q_now[y] for y in years}, day), day, spec.horizon_days)
        p_ref = horizon_probability(year_ladder({y: q_ref[y] for y in years}, day), day, spec.horizon_days)
        if p_now is not None and p_ref is not None:
            today[series], before[series] = p_now, p_ref
    return today, before


def index_readings(records: Iterable[Mapping[str, Any]], spec: IndexSpec = DEESCALATION) -> list[IndexReading]:
    """The index on every day of the archive, from its records (``prediction.record_reading`` writes one per
    event and UTC day). Days are sorted; a day whose records hold no readable component is a reading without
    components (``forecast_on`` skips it)."""
    rows = list(records)
    if spec.kind == KIND_MEETINGS:
        return _readings_meetings(rows, spec)
    if spec.kind == KIND_YEARS:
        return _readings_years(rows, spec)
    return _readings_deadlines(rows, spec)


def archive_readings(store: StateStore, spec: IndexSpec = DEESCALATION) -> list[IndexReading]:
    """``index_readings`` on the desk's archive (``state/desk/kalshi-*.jsonl``)."""
    return index_readings(store.iter_jsonl(KALSHI_LOG, skip_bad=True), spec)


# ----------------------------------------------------------------------------------------------- the forecast
@dataclass(frozen=True)
class OracleForecast:
    """What a sleeve is worth for one decision day, and why."""

    day: date
    reading: IndexReading | None
    reference: IndexReading | None
    common: tuple[str, ...]
    change: float | None  # over the common components, today - reference (mean, or sum for the Fed path)
    forecast: float | None  # the sleeve's value: None when it cannot be read, 0 when it has no view
    note: str
    spec: IndexSpec = DEESCALATION
    # the values the change was read on (``common_values``): today's and the reference's, by common component
    today: dict[str, float] = field(default_factory=dict)
    before: dict[str, float] = field(default_factory=dict)

    def as_forecast(self) -> dict[str, float | None]:
        """The forecast dict a ``Book`` reads: the sleeve plus every input of the decision, as numbers."""
        spec = self.spec
        out: dict[str, float | None] = {spec.id: self.forecast}
        if self.reading is not None:
            out[key_index(spec)] = self.reading.value
            out[key_age(spec)] = float((self.day - self.reading.day).days)
            for component, value in self.reading.components.items():
                out[key_component(spec, component)] = value
        if self.reference is not None:
            out[key_ref_age(spec)] = float((self.day - self.reference.day).days)
            for component in self.common:
                out[key_reference(spec, component)] = self.before.get(component, self.reference.components[component])
        out[key_change(spec)] = self.change
        out[key_common(spec)] = float(len(self.common))
        return out

    def _common_value(self, values: Mapping[str, float]) -> float | None:
        if not self.common:
            return None
        return self.spec.combine(values[c] for c in self.common)

    def to_dict(self) -> dict[str, Any]:
        """For the terminal (``desk.json``)."""
        spec, reading, reference = self.spec, self.reading, self.reference
        names = set(reading.components if reading else ()) | set(reference.components if reference else ())
        if spec.kind == KIND_MEETINGS:
            dated = {**(reference.dates if reference else {}), **(reading.dates if reading else {})}
            components = sorted(names, key=lambda c: (dated.get(c) or date.max, c))
        else:
            components = [c for c in spec.components if c in names] or sorted(names)
        return {
            "id": spec.id,
            "label": spec.label,
            "unit": spec.unit,
            "day": self.day.isoformat(),
            "forecast": None if self.forecast is None else round(self.forecast, 2),
            # the index as it is today (every component read); the two numbers the CHANGE is made of are over
            # the components both readings have, so that change = index_common - index_reference exactly
            "index": None if reading is None or reading.value is None else round(reading.value, 4),
            "index_common": _round(self._common_value(self.today)),
            "index_reference": _round(self._common_value(self.before)),
            "components_common": len(self.common),
            "components_read": 0 if reading is None else len(reading.components),
            "change": None if self.change is None else round(self.change, 4),
            "reading_day": None if reading is None else reading.day.isoformat(),
            "reading_ts": None if reading is None else reading.ts or None,
            "reference_day": None if reference is None else reference.day.isoformat(),
            "components": [
                {
                    "series": c,
                    "label": component_label(spec, c, reading, reference),
                    "p": _round(None if reading is None else reading.components.get(c)),
                    "p_reference": _round(
                        self.before.get(c, None if reference is None else reference.components.get(c))
                    ),
                }
                for c in components
            ],
            "note": self.note,
            "rule": describe(spec),
        }


def _round(x: float | None, digits: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, digits)


def component_label(
    spec: IndexSpec, component: str, reading: IndexReading | None = None, reference: IndexReading | None = None
) -> str:
    """How the rationale names a component: a series by its registered label; a meeting by its date."""
    if spec.kind == KIND_MEETINGS:
        dated = {**(reference.dates if reference else {}), **(reading.dates if reading else {})}
        when = dated.get(component)
        base = next(iter(spec.components.values()), "the meeting")
        if when:
            return f"{base} of {when.strftime('%d %b %Y')}"
        m = _MEETING_SUFFIX.search(component)  # "KXFEDDECISION-26OCT": the month and year from the ticker
        return f"{base} of {m.group(2).capitalize()} 20{m.group(1)}" if m else component
    return spec.components.get(component, component)


def forecast_on(readings: list[IndexReading], day: date, spec: IndexSpec | None = None) -> OracleForecast:
    """The rule of the module docstring, point 4 to 6, for a decision taken on ``day``.

    A day whose records held no readable component (every quote one-sided or wider than the limit, a label the
    parser does not know) is not a reading: the newest reading WITH components within ``STALE_DAYS`` is today's
    view, as the rule says "the newest one at most three days old". Closing a leveraged position on a quote
    outage and reopening it the next day is the round trip that rule exists to avoid.
    """
    if spec is None:
        spec = readings[0].spec if readings else DEESCALATION
    usable = [r for r in readings if r.components]
    latest = [r for r in usable if r.day <= day]
    reading = latest[-1] if latest else None
    if reading is None:
        return OracleForecast(
            day, None, None, (), None, None, f"no Kalshi reading of the {spec.label} in the archive: no value", spec
        )
    if (day - reading.day).days > STALE_DAYS:
        return OracleForecast(
            day, reading, None, (), None, 0.0,
            f"the newest Kalshi reading of the {spec.label} is {(day - reading.day).days} days old "
            f"(more than {STALE_DAYS}): no view, flat",
            spec,
        )  # fmt: skip
    first, last = day - timedelta(days=LOOKBACK_DAYS + REFERENCE_WINDOW_DAYS), day - timedelta(days=LOOKBACK_DAYS)
    candidates = [r for r in usable if first <= r.day <= last]
    reference = candidates[-1] if candidates else None
    if reference is None:
        return OracleForecast(
            day, reading, None, (), None, None,
            f"no Kalshi reading of the {spec.label} {LOOKBACK_DAYS} to {LOOKBACK_DAYS + REFERENCE_WINDOW_DAYS} "
            "days ago: the sleeve waits for a week of archive",
            spec,
        )  # fmt: skip
    today, before = common_values(reading, reference, day, spec)
    common = tuple(today)
    if not common:
        return OracleForecast(
            day, reading, reference, (), None, 0.0,
            f"{spec.label}: no component present in both readings: no measurable move, flat", spec,
        )  # fmt: skip
    change = spec.combine(today[c] - before[c] for c in common)
    assert change is not None
    if abs(change) < spec.deadband:
        forecast, why = 0.0, f"inside the deadband of {spec.fmt_change(spec.deadband).lstrip('+')}"
    else:
        raw = spec.per_unit * change * spec.unit_scale
        forecast = max(-FORECAST_CAP, min(FORECAST_CAP, raw))
        unit = "point" if spec.unit == "points" else "bp"
        why = "capped at +-20" if abs(raw) > FORECAST_CAP else f"{spec.per_unit:+g} per {unit}"
    today_common = spec.combine(today.values())
    ref_common = spec.combine(before.values())
    total = len(reading.components) if spec.kind == KIND_MEETINGS else len(spec.components)
    note = (
        f"{spec.label} {spec.fmt(today_common)} on {reading.day.isoformat()} against {spec.fmt(ref_common)} on "
        f"{reference.day.isoformat()} ({len(common)} of {total} components in both): {spec.fmt_change(change)} "
        f"-> sleeve {forecast:+.1f} ({why})"
    )
    return OracleForecast(day, reading, reference, common, change, forecast, note, spec, today, before)


def forecast_for(store: StateStore, day: date, spec: IndexSpec = DEESCALATION) -> OracleForecast:
    """``forecast_on`` the desk's archive, for the decision of ``day``."""
    return forecast_on(archive_readings(store, spec), day, spec)


# ----------------------------------------------------------------------------------------------- the words
def lead_text(forecast: Mapping[str, float | None]) -> str:
    """The first sentences of a decision's rationale, from the forecast dict alone (``as_forecast`` of every
    sleeve the book reads): what each index's components say today and a week ago, the change, the sleeve."""
    out = ""
    for spec in INDICES.values():
        if spec.id not in forecast:
            continue
        value = forecast.get(spec.id)
        change = forecast.get(key_change(spec))
        prefix = key_component(spec, "")
        parts = []
        for key, today in forecast.items():
            if not key.startswith(prefix) or today is None:
                continue
            component = key[len(prefix) :]
            before = forecast.get(key_reference(spec, component))
            label = component_label(spec, component)
            ago = f" (a week ago {spec.fmt(before)})" if before is not None else ""
            parts.append(f"{label} {spec.fmt(today)}{ago}")
        meetings = spec.kind == KIND_MEETINGS
        horizon = f"the meetings within {spec.horizon_days} days" if meetings else f"within {spec.horizon_days} days"
        head = f"Kalshi, {horizon}: " + (", ".join(parts) if parts else "no component read")
        index = forecast.get(key_index(spec))
        if index is not None:
            head += f"; {spec.label} {spec.fmt(index)}"
        if change is not None:
            head += f", {spec.fmt_change(change)} in a week"
        tail = "no value" if value is None or not math.isfinite(value) else f"sleeve {value:+.1f} out of 20"
        out += f"{head}: {tail}. "
    return out


def describe(spec: IndexSpec = DEESCALATION) -> dict[str, Any]:
    """The rule in force for one index, for the page and the state."""
    if spec.kind == KIND_MEETINGS:
        text = (
            f"The expected change of the Fed's target rate over the FOMC meetings within {spec.horizon_days} days, "
            f"on Kalshi: each meeting's outcome markets (cut or hike by 25 or more, hold; quoted within "
            f"{MAX_SPREAD * 100:.0f} cents) weighted by their odds, summed in basis points. Its change over "
            f"{LOOKBACK_DAYS} days over the meetings both readings have, times {spec.per_unit:+g} per basis point, "
            f"is the sleeve: expectations turning dovish is a positive value, hawkish negative; under "
            f"{spec.deadband:g} bp, no view. Capped at +-20."
        )
    elif spec.kind == KIND_YEARS:
        text = (
            f"The probability, on Kalshi, that a US recession begins within {spec.horizon_days} days, read off the "
            f"yearly NBER markets as one ladder by year end (markets quoted within {MAX_SPREAD * 100:.0f} cents). "
            f"Its change over {LOOKBACK_DAYS} days, times {spec.per_unit:+g} per point, is the sleeve: the odds of a "
            f"recession rising is a positive value; under {spec.deadband * 100:.0f} point, no view. Capped at +-20."
        )
    else:
        text = (
            f"The mean probability, on Kalshi, that within {spec.horizon_days} days the strait is back to normal, "
            f"the US and Iran meet at senior level and they sign a nuclear deal (read off each deadline ladder, "
            f"markets quoted within {MAX_SPREAD * 100:.0f} cents). Its change over {LOOKBACK_DAYS} days, times "
            f"{spec.per_unit:+g} per point, is the sleeve: de-escalation rising means short oil, falling "
            f"means long; under {spec.deadband * 100:.0f} point, no view. Capped at +-20."
        )
    return {
        "id": spec.id,
        "label": spec.label,
        "kind": spec.kind,
        "unit": spec.unit,
        "aggregate": spec.aggregate,
        "components": [{"series": s, "label": label} for s, label in spec.components.items()],
        "horizon_days": spec.horizon_days,
        "lookback_days": LOOKBACK_DAYS,
        "reference_window_days": REFERENCE_WINDOW_DAYS,
        "stale_days": STALE_DAYS,
        "max_spread": MAX_SPREAD,
        "deadband": spec.deadband,
        "per_unit": spec.per_unit,
        # the de-escalation index's registered name for its scale (phase 14)
        "forecast_per_point": abs(spec.per_unit) if spec.unit == "points" else None,
        "text": text + " No backtest in the desk's replay: the books on it are judged on their live fills only and "
        "are never promoted.",
    }
