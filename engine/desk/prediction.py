"""Prediction markets on the desk (phase 13): the daily archive of Kalshi's oil, strait and Iran markets, and
what the terminal shows of them.

A Kalshi market is a contract that pays one dollar if an event happens: its price is the crowd's probability
of the event, quoted bid and ask, in real time, on questions the desk's forecast cannot see (will the strait be
back to normal by December; will Brent close above 104 on Friday). The owner asked for "the markets that know
first" (docs/RESEARCH.md, chapter 9 C). What this module does with them, exactly as registered there:

1. ARCHIVE. Every tick looks at the newest download (``engine/data/adapters/kalshi.py``, once a day) and
   appends what is new to ``state/desk/kalshi-YYYY-MM.jsonl``: one record per event and UTC day, carrying every
   market of the event (``record_reading``). Idempotent on the key ``day|event``. There is no free history of
   these prices, so the archive is the history; the first test of any rule on it is possible in a year.
2. SHOW. The terminal gets each event as a ladder (``prediction_block``): the price ladders of Brent and WTI
   with the probability the crowd gives to "above the desk's own reference price" and its implied median; the
   deadline ladders (the strait back to normal before a date, a US-Iran meeting or deal before a date); the
   threshold ladders (EIA inventories, ships through the strait) beside what the desk measures itself. Next to
   the Brent and WTI ladders, what the fund's options imply under a lognormal at the ATM implied volatility
   (``approx``: a model, no smile, no basis between fund and future).
3. NOT TRADE. No book reads these numbers. Nothing here changes a decision.

Seen on the real API (13 October 2026) and handled here: a market can stay "active" months after its close time
(``KXBRENTMON`` on July 31, ``KXWTIW`` on August 21): such a market is listed under ``past_close`` and is never
turned into a probability; a series can have no open market at all; the deadline of a market is in its
subtitle ("Before Jan 1, 2027"), not in a strike.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso, parse_iso
from engine.data.adapters.kalshi import DEFAULT_SERIES
from engine.data.raw_store import RawStore
from engine.desk.data import _latest_seen

log = logging.getLogger(__name__)

SOURCE = "kalshi"  # the raw table (config/data_sources.yaml, group desk)
KALSHI_LOG = "kalshi"  # state/desk/kalshi-YYYY-MM.jsonl
LOG_COLUMNS = ("ticker", "label", "floor", "cap", "yes_bid", "yes_ask", "last", "volume", "oi", "close_time")
MAX_READING_AGE_HOURS = 36.0  # a reading older than this is shown as stale (the tick downloads once a day)
OPTIONS_MAX_QUOTE_AGE_HOURS = 24.0  # the options' implied probability needs a chain read within the day
TRADING_YEAR_SECONDS = 365.25 * 86400.0
_DEADLINE = re.compile(r"(?i)\bbefore\s+([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")
_MONTHS = {
    m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}

# How the events are grouped on the page, by the series they belong to. A series not listed here falls into
# "other" with its own ticker as the label.
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("brent", "Brent", ("KXBRENTD", "KXBRENTW", "KXBRENTMON", "KXBRENTWEX")),
    ("wti", "WTI", ("KXWTI", "KXWTIW", "KXWTIMONTHLY")),
    (
        "hormuz",
        "Strait of Hormuz",
        ("KXHORMUZNORM", "KXHORMUZRETURN", "KXCLOSEHORMUZ", "KXMAXSHIPSHORMUZ", "KXHORMUZWEEKLY"),
    ),
    ("iran", "US-Iran", ("KXUSAIRANAGREEMENT", "KXUSIRANMEETING")),
    ("supply", "Supply", ("KXEIACRUDEW", "KXOPECCUTS", "KXSAUDICRUDE", "KXIRANCRUDE")),
)
# the price ladders: which of the desk's quotes is their reference and which fund's options speak about them
PRICE_SERIES: dict[str, tuple[str, str]] = {
    "KXBRENTD": ("brent", "BNO"),
    "KXBRENTW": ("brent", "BNO"),
    "KXBRENTMON": ("brent", "BNO"),
    "KXBRENTWEX": ("brent", "BNO"),
    "KXWTI": ("wti", "USO"),
    "KXWTIW": ("wti", "USO"),
    "KXWTIMONTHLY": ("wti", "USO"),
}
HORMUZ_SERIES = frozenset({"KXHORMUZNORM", "KXHORMUZRETURN", "KXCLOSEHORMUZ", "KXMAXSHIPSHORMUZ", "KXHORMUZWEEKLY"})
INVENTORY_SERIES = frozenset({"KXEIACRUDEW"})


# ----------------------------------------------------------------------------------------------- the reading
@dataclass(frozen=True)
class Reading:
    """One download of the markets: the frame of the adapter's contract and when it was taken."""

    frame: pd.DataFrame
    seen: pd.Timestamp

    @property
    def day(self) -> str:
        return self.seen.strftime("%Y-%m-%d")


def latest_reading(raw: RawStore) -> Reading | None:
    """The newest archived download of the markets, or None when there has never been one."""
    frame, seen = _latest_seen(raw, SOURCE)
    if frame is None or frame.empty:
        return None
    if seen is None:
        try:
            seen = pd.Timestamp(frame.index.max())
        except (TypeError, ValueError):
            return None
    seen = pd.Timestamp(seen)
    seen = seen.tz_localize("UTC") if seen.tzinfo is None else seen.tz_convert("UTC")
    return Reading(frame.reset_index(drop=True), seen)


def configured_series(config_dir: Path | None) -> list[str]:
    """The series the tick asks for (config/data_sources.yaml, group desk, entry kalshi); the adapter's defaults
    when the file does not list them."""
    if config_dir is not None:
        try:
            doc = yaml.safe_load((Path(config_dir) / "data_sources.yaml").read_text(encoding="utf-8")) or {}
            for entry in doc.get("desk", {}).get(SOURCE, []) or []:
                listed = entry.get("series") if isinstance(entry, dict) else None
                if isinstance(listed, list) and listed:
                    return [str(x) for x in listed]
        except (OSError, yaml.YAMLError, AttributeError) as exc:  # pragma: no cover - a broken file is not fatal here
            log.warning("kalshi series not read from config: %s", exc)
    return list(DEFAULT_SERIES)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _text(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{str(k): v for k, v in r.items()} for r in frame.to_dict("records")]


def _series_of(row: dict[str, Any]) -> str:
    series = _text(row.get("series_ticker"))
    if series:
        return series
    return _text(row.get("event_ticker")).split("-")[0]


# ----------------------------------------------------------------------------------------------- the archive
def _market_order(r: dict[str, Any]) -> tuple[str, str, float, str, str]:
    """Series, event, then the strike (floor, else cap) as a number, the close time, the ticker."""
    strike = _num(r.get("floor_strike"))
    if strike is None:
        strike = _num(r.get("cap_strike"))
    return (
        _series_of(r),
        _text(r.get("event_ticker")),
        math.inf if strike is None else strike,
        _text(r.get("close_time")),
        _text(r.get("ticker")),
    )


def _event_records(reading: Reading) -> list[dict[str, Any]]:
    """The reading as one record per event: the markets packed as rows under ``columns``."""
    out: list[dict[str, Any]] = []
    rows = _records(reading.frame)
    rows.sort(key=_market_order)
    by_event: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        event = _text(r.get("event_ticker")) or _text(r.get("ticker"))
        by_event.setdefault(event, []).append(r)
    for event, markets in by_event.items():
        first = markets[0]
        out.append(
            {
                "key": f"{reading.day}|{event}",
                "day": reading.day,
                "ts": iso(reading.seen.to_pydatetime()),
                "series": _series_of(first),
                "event": event,
                "title": _text(first.get("title"))[:160],
                "strike_type": _text(first.get("strike_type")),
                "status": _text(first.get("status")),
                "columns": list(LOG_COLUMNS),
                "markets": [
                    [
                        _text(m.get("ticker")),
                        _text(m.get("subtitle")),
                        _num(m.get("floor_strike")),
                        _num(m.get("cap_strike")),
                        _num(m.get("yes_bid")),
                        _num(m.get("yes_ask")),
                        _num(m.get("last_price")),
                        _num(m.get("volume")),
                        _num(m.get("open_interest")),
                        _text(m.get("close_time")),
                    ]
                    for m in markets
                ],
            }
        )
    return out


def record_reading(store: StateStore, reading: Reading) -> int:
    """Append the reading to the month's log, one record per event, once per UTC day. Returns how many were
    written (zero when the day's reading is already there: the tick calls this at every pass)."""
    records = _event_records(reading)
    if not records:
        return 0
    when = reading.seen.to_pydatetime()
    path = store.jsonl_path(KALSHI_LOG, ts=when)
    known: set[str] = set()
    if path.exists():
        for rec in store.iter_jsonl(KALSHI_LOG):
            if str(rec.get("day", "")) == reading.day:
                known.add(str(rec.get("key")))
    written = 0
    for rec in records:
        if rec["key"] in known:
            continue
        store.append_jsonl(KALSHI_LOG, rec, ts=when)
        written += 1
    return written


def archive_reading(raw: RawStore, store: StateStore) -> dict[str, Any]:
    """What the tick does: read the newest download, append what the log does not have yet."""
    reading = latest_reading(raw)
    if reading is None:
        return {"status": "no reading on file"}
    written = record_reading(store, reading)
    return {"status": "ok", "day": reading.day, "markets": len(reading.frame), "events_written": written}


# ----------------------------------------------------------------------------------------------- probabilities
def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def lognormal_p_above(reference: float, strike: float, vol: float, years: float) -> float | None:
    """P(S_T > K) for a driftless lognormal at volatility ``vol`` over ``years``: N(d2). None when undefined."""
    if reference <= 0 or strike <= 0 or vol <= 0 or years <= 0:
        return None
    sd = vol * math.sqrt(years)
    return norm_cdf((math.log(reference / strike) - 0.5 * sd * sd) / sd)


def _mid(bid: float | None, ask: float | None, last: float | None) -> float | None:
    """The probability a market quotes: the mid of bid and ask when both are there (an ask of 1 with no bid
    says nothing: then the last trade, if any)."""
    if bid is not None and ask is not None and 0.0 <= bid <= ask <= 1.0 and not (bid == 0.0 and ask >= 0.99):
        return (bid + ask) / 2.0
    if last is not None and 0.0 < last < 1.0:
        return last
    if bid is not None and ask is not None and 0.0 <= bid <= ask <= 1.0:
        return (bid + ask) / 2.0
    return None


def above_curve(markets: Iterable[dict[str, Any]]) -> list[tuple[float, float]]:
    """P(X > x) at the strikes of a ladder, from its markets: "greater" markets as they are, "less" markets
    turned around, "between" bins summed from the top. Sorted by x; the crowd is not forced to be monotone."""
    greater: list[tuple[float, float]] = []
    less: list[tuple[float, float]] = []
    bins: list[tuple[float, float, float]] = []
    for m in markets:
        p = m.get("mid")
        if p is None:
            continue
        kind, floor, cap = str(m.get("strike_type") or ""), m.get("floor"), m.get("cap")
        if kind in ("greater", "greater_or_equal") and floor is not None:
            greater.append((float(floor), float(p)))
        elif kind in ("less", "less_or_equal") and cap is not None:
            less.append((float(cap), 1.0 - float(p)))
        elif kind == "between" and floor is not None and cap is not None:
            bins.append((float(floor), float(cap), float(p)))
    points = greater + less
    if bins and not greater:
        tail = 0.0
        for floor, _cap, p in sorted(bins, key=lambda b: b[0], reverse=True):
            tail = min(1.0, tail + p)
            points.append((floor, tail))
    return sorted(points)


def interpolate_above(curve: list[tuple[float, float]], x: float) -> float | None:
    """P(X > x) read off the ladder, linearly between the two strikes around ``x``; None outside the ladder."""
    if len(curve) < 2 or x < curve[0][0] or x > curve[-1][0]:
        return None
    for (x0, p0), (x1, p1) in pairwise(curve):
        if x0 <= x <= x1:
            return p0 if x1 == x0 else p0 + (p1 - p0) * (x - x0) / (x1 - x0)
    return None  # pragma: no cover - the loop covers the closed range


def implied_median(curve: list[tuple[float, float]]) -> float | None:
    """The x where the ladder crosses one half (P falls through 0.5 as x rises); None when it never does."""
    if len(curve) < 2:
        return None
    for (x0, p0), (x1, p1) in pairwise(curve):
        if p0 >= 0.5 >= p1 and p0 != p1:
            return x0 + (x1 - x0) * (p0 - 0.5) / (p0 - p1)
    return None


def deadline_of(label: str) -> date | None:
    """The date in "Before Jan 1, 2027" / "before August 15, 2026"; None when the label has none."""
    m = _DEADLINE.search(label or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].lower())
    if month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


# ----------------------------------------------------------------------------------------------- the block
def _r(x: Any, digits: int = 4) -> float | None:
    v = _num(x)
    return None if v is None else round(v, digits)


def _market_view(row: dict[str, Any]) -> dict[str, Any]:
    bid, ask, last = _num(row.get("yes_bid")), _num(row.get("yes_ask")), _num(row.get("last_price"))
    label = _text(row.get("subtitle")) or _text(row.get("title"))
    return {
        "ticker": _text(row.get("ticker")),
        "label": label,
        "title": _text(row.get("title")),  # dropped before export, once the event's question is chosen
        "strike_type": _text(row.get("strike_type")) or None,
        "floor": _num(row.get("floor_strike")),
        "cap": _num(row.get("cap_strike")),
        "deadline": (deadline_of(label) or deadline_of(_text(row.get("title")))),
        "yes_bid": bid,
        "yes_ask": ask,
        "last": last,
        "mid": _r(_mid(bid, ask, last), 4),
        "volume": _num(row.get("volume")),
        "open_interest": _num(row.get("open_interest")),
        "close_time": _text(row.get("close_time")) or None,
    }


def _hours_until(ts: str | None, now: datetime) -> float | None:
    when = parse_iso(ts) if ts else None
    return None if when is None else (ensure_utc(when) - now).total_seconds() / 3600.0


def _event_kind(series: str, markets: list[dict[str, Any]]) -> str:
    with_strike = [m for m in markets if m["floor"] is not None or m["cap"] is not None]
    if series in PRICE_SERIES and with_strike:
        return "price"
    if with_strike and len(with_strike) >= max(2, len(markets) // 2):
        return "threshold"
    if sum(1 for m in markets if m["deadline"] is not None) >= max(1, len(markets) // 2):
        return "deadline"
    return "list"


def question_of(title: str) -> str:
    """A deadline ladder's question without its date: "... hold a senior-level meeting before Nov 1, 2026?" ->
    "... hold a senior-level meeting?" (every market of the event asks the same thing about another date)."""
    out = _DEADLINE.sub("", title or "").strip()
    out = re.sub(r"\s+([?.])$", r"\1", out)
    return out


def _nearest_title(markets: list[dict[str, Any]], x: float | None, fallback: str) -> str:
    """The title of the market whose strike is nearest to ``x`` (the at-the-money question of a ladder)."""
    if x is None:
        return fallback
    best = None
    for m in markets:
        strike = m["floor"] if m["floor"] is not None else m["cap"]
        if strike is None:
            continue
        if best is None or abs(float(strike) - x) < abs(best[0] - x):
            best = (float(strike), m)
    return fallback if best is None else _text(best[1].get("title")) or fallback


def _group_label(series: str) -> str:
    for _gid, label, members in GROUPS:
        if series in members:
            return label
    return series


def _pct(p: float | None) -> str:
    return "n/a" if p is None else f"{round(100.0 * p):.0f} %"


def _money(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f} $"


def _price_event(
    event: dict[str, Any],
    reference: dict[str, Any] | None,
    options: dict[str, Any] | None,
    now: datetime,
) -> None:
    """Fill a price ladder with the crowd's P(above the reference), its median, and the options' twin."""
    markets = event["markets"]
    curve = above_curve(markets)
    event["curve"] = [[_r(x, 2), _r(p, 4)] for x, p in curve]
    median = implied_median(curve)
    ref_price = _num((reference or {}).get("price"))
    hours = _hours_until(event["close_time"], now)
    years = None if hours is None or hours <= 0 else hours * 3600.0 / TRADING_YEAR_SECONDS
    event["reference"] = (
        None
        if reference is None
        else {"symbol": reference.get("symbol"), "price": _r(ref_price, 3), "asof": reference.get("asof")}
    )
    event["implied"] = {
        "median": _r(median, 2),
        "p_above_reference": _r(interpolate_above(curve, ref_price), 4) if ref_price is not None else None,
        "note": "from the mid of bid and ask of each strike, linearly between strikes",
    }
    event["title"] = _nearest_title(markets, median, event["title"])
    opt: dict[str, Any] | None = None
    iv = _num((options or {}).get("atm_iv"))
    quote_ts = parse_iso(_text((options or {}).get("quote_ts")) or None)
    age_h = None if quote_ts is None else (now - ensure_utc(quote_ts)).total_seconds() / 3600.0
    if iv is not None and iv > 0 and years is not None and ref_price is not None and age_h is not None:
        if age_h <= OPTIONS_MAX_QUOTE_AGE_HOURS:
            sd = iv * math.sqrt(years)
            opt = {
                "symbol": (options or {}).get("symbol"),
                "atm_iv": _r(iv, 4),
                "quote_ts": iso(ensure_utc(quote_ts)) if quote_ts else None,
                "median": _r(ref_price * math.exp(-0.5 * sd * sd), 2),
                "p_above_reference": _r(norm_cdf(-0.5 * sd), 4),
                "approx": True,
                "note": (
                    "driftless lognormal at the ATM implied volatility of the fund's options, over the time to the "
                    "market's close; no smile, no basis between the fund and the future"
                ),
            }
            for m in markets:
                strike = m["floor"] if m["floor"] is not None else m["cap"]
                m["options_p_above"] = (
                    _r(lognormal_p_above(ref_price, float(strike), iv, years), 4) if strike is not None else None
                )
        else:
            opt = {"symbol": (options or {}).get("symbol"), "stale": True, "quote_age_hours": _r(age_h, 1)}
    event["options_implied"] = opt
    unit = "$"
    when = _text(event.get("close_time"))[:10]
    parts = [f"the crowd's median {_money(median)}" if median is not None else "no median on the ladder"]
    if ref_price is not None:
        p_ref = event["implied"]["p_above_reference"]
        parts.append(f"P(above {ref_price:.2f} {unit} now) {_pct(p_ref)}")
    if opt and opt.get("median") is not None:
        parts.append(f"the {opt['symbol']} options' median {_money(opt['median'])} (approx)")
    event["summary"] = f"{event['label']} on {when}: " + ", ".join(parts)


def _deadline_event(event: dict[str, Any]) -> None:
    rows = sorted((m for m in event["markets"] if m["deadline"] is not None), key=lambda m: m["deadline"])
    event["ladder"] = [{"before": m["deadline"].isoformat(), "p": _r(m["mid"], 4)} for m in rows]
    event["title"] = question_of(event["title"])
    event["summary"] = f"{event['title']} " + ", ".join(
        f"before {m['deadline']:%b} {m['deadline'].day}, {m['deadline'].year}: {_pct(m['mid'])}" for m in rows
    )


def _threshold_event(event: dict[str, Any]) -> None:
    curve = above_curve(event["markets"])
    event["curve"] = [[_r(x, 2), _r(p, 4)] for x, p in curve]
    median = implied_median(curve)
    event["implied"] = {"median": _r(median, 2)}
    event["title"] = _nearest_title(event["markets"], median, event["title"])
    event["summary"] = f"{event['title']} " + (
        f"the crowd's median {median:.1f}" if median is not None else "no median on the ladder"
    )


def prediction_block(
    reading: Reading | None,
    now: datetime,
    *,
    references: dict[str, dict[str, Any]] | None = None,
    options: dict[str, dict[str, Any]] | None = None,
    hormuz: dict[str, Any] | None = None,
    inventories: dict[str, Any] | None = None,
    asked: Iterable[str] = (),
) -> dict[str, Any]:
    """``desk.json["prediction_markets"]``: the newest reading as event ladders, grouped for the page.

    ``references`` are the desk's own quotes by key ("brent", "wti": the dicts of ``market_block``); ``options``
    the options monitor's entries by underlying symbol ("BNO", "USO"); ``hormuz`` the desk's transit summary
    and ``inventories`` the last EIA weekly figure - shown beside the markets that ask about them.
    """
    now = ensure_utc(now)
    asked_list = list(asked)
    base: dict[str, Any] = {
        "source": "Kalshi (public trade API, read-only)",
        "unit": "price of a contract paying 1 $ = the crowd's probability",
        "note": (
            "No book trades on these markets: there is no history to test a rule on. The tick archives one "
            "reading a day (state/desk/kalshi-*.jsonl); the first test is possible in a year."
        ),
        "asked": asked_list,
    }
    if reading is None:
        return {**base, "asof": None, "stale": True, "groups": [], "past_close": [], "empty_series": asked_list}
    age_h = (now - reading.seen.to_pydatetime()).total_seconds() / 3600.0
    rows = _records(reading.frame)
    by_event: dict[str, list[dict[str, Any]]] = {}
    series_seen: set[str] = set()
    for r in rows:
        series_seen.add(_series_of(r))
        by_event.setdefault(_text(r.get("event_ticker")) or _text(r.get("ticker")), []).append(r)

    events: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    for event_id, members in by_event.items():
        series = _series_of(members[0])
        markets = [_market_view(m) for m in members]
        closes = [m["close_time"] for m in markets if m["close_time"]]
        close_time = min(closes) if closes else None
        latest_close = max(closes) if closes else None
        kind = _event_kind(series, markets)
        # a ladder of deadlines closes market by market: the event is live while its LAST market is; a price or
        # threshold ladder closes all at once
        live_until = latest_close if kind == "deadline" else close_time
        hours = _hours_until(live_until, now)
        if hours is not None and hours < 0:
            past.append(
                {
                    "series": series,
                    "event": event_id,
                    "title": _text(members[0].get("title"))[:160],
                    "markets": len(markets),
                    "close_time": live_until,
                    "note": "past its close time and not settled: not a probability",
                }
            )
            continue
        if kind == "deadline":  # the dates already passed are not probabilities any more
            markets = [m for m in markets if not ((_hours_until(m["close_time"], now) or 0.0) < 0)]
        event: dict[str, Any] = {
            "series": series,
            "event": event_id,
            "title": _text(members[0].get("title"))[:160],
            "label": _group_label(series),
            "kind": kind,
            "close_time": live_until,
            "hours_to_close": _r(hours, 1),
            "markets": markets,
            "volume": _r(sum(m["volume"] or 0.0 for m in markets), 0),
            "open_interest": _r(sum(m["open_interest"] or 0.0 for m in markets), 0),
        }
        if kind == "price":
            ref_key, fund = PRICE_SERIES[series]
            _price_event(event, (references or {}).get(ref_key), (options or {}).get(fund), now)
        elif kind == "deadline":
            _deadline_event(event)
        elif kind == "threshold":
            _threshold_event(event)
        else:
            event["summary"] = event["title"]
        if series in HORMUZ_SERIES and hormuz:
            event["measured"] = {
                **hormuz,
                "note": (
                    "the desk's own reading: tanker transits (7-day mean) against the median of the normal years; "
                    "the market counts every transit call, with its own threshold"
                ),
            }
        if series in INVENTORY_SERIES and inventories:
            event["measured"] = inventories
        for m in markets:  # dates to text for JSON; the per-market title served the event's question
            m["deadline"] = m["deadline"].isoformat() if m["deadline"] is not None else None
            m.pop("title", None)
        events.append(event)

    groups: list[dict[str, Any]] = []
    placed: set[str] = set()
    for gid, label, members_ in GROUPS:
        mine = [e for e in events if e["series"] in members_]
        placed.update(e["event"] for e in mine)
        if mine:
            mine.sort(key=lambda e: (e["close_time"] or "", e["series"]))
            groups.append({"id": gid, "label": label, "events": mine})
    rest = [e for e in events if e["event"] not in placed]
    if rest:
        groups.append({"id": "other", "label": "Other", "events": sorted(rest, key=lambda e: e["series"])})
    return {
        **base,
        "asof": iso(reading.seen.to_pydatetime()),
        "age_hours": _r(age_h, 1),
        "stale": bool(age_h > MAX_READING_AGE_HOURS),
        "markets": len(rows),
        "groups": groups,
        "past_close": sorted(past, key=lambda p: (p["series"], p["event"])),
        "empty_series": sorted(s for s in asked_list if s not in series_seen),
    }
