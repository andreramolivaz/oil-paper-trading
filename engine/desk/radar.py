"""The radar (phase 22, ``docs/RESEARCH.md`` chapter 17 J): what the desk measures that can move BEFORE the price,
and what says that something does not add up, in one block for the top of the page.

Seven tiles. Each one is a number, a sentence in the reader's words, when it was read and where from, and - where
a rule was declared - whether it is flagged. A flag is a footprint by a declared rule, never a verdict, and no book
reads this module: the one use any of it has in a decision is the event budget of the Oracle 3x, which lives in
``twostate.event_caps`` and the tick.

1. ``kalshi``    the de-escalation index now, against yesterday and a week ago, and the unusual-activity alerts
2. ``options``   what the WTI fund's options price for the next weeks: one state or two, the quantiles, the tails
3. ``weekend``   what the prediction markets did while crude was closed (``weekend.py``); flagged at 5 points
4. ``reopening`` when the strait reopens: the median date of Kalshi's ladder beside where half of the Brent
                 curve's premium is gone
5. ``term``      the term structure of implied volatility; flagged when the nearest expiry is 5 points above the
                 one nearest 45 days (an event priced within days)
6. ``spread``    Brent against WTI: the 5-session move of the two funds' ratio against a year of such moves;
                 flagged beyond 3 standard deviations
7. ``system``    the desk itself: sources not green, the last tick, books halted

The thresholds are declared round numbers; the rate of flags is to be counted for sixty sessions and reviewed once.
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso, parse_iso
from engine.data.raw_store import RawStore
from engine.desk import oracle, prediction, twostate, weekend
from engine.desk.data import DeskData, _latest
from engine.desk.options import NEW_YORK
from engine.desk.watch import recent_alerts

log = logging.getLogger(__name__)

STRAIT_SERIES = "KXHORMUZNORM"
ALERT_FLAG_HOURS = 48.0  # an alert is "now" for two days, as its GitHub issue is
SPREAD_WINDOW = 5  # sessions
SPREAD_HISTORY = 252
SPREAD_FLAG_SD = 3.0
REOPENING_HORIZONS = (91, 182, 365)
SYSTEM_STALE_MINUTES = 90.0  # a tick every 30 minutes: three missed is something to look at
MONTH_CODES = "FGHJKMNQUVXZ"


def _r(x: Any, digits: int = 4) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, digits) if math.isfinite(v) else None


def _pct(x: float | None, digits: int = 0) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x * 100:.{digits}f}%"


def _signed_pct(x: float | None, digits: int = 0) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x * 100:+.{digits}f}%"


def _money(x: float | None) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x:,.0f} $"


# ----------------------------------------------------------------------------------------------- 1. Kalshi
def _mean_change(now: Mapping[str, float], before: Mapping[str, float]) -> float | None:
    common = sorted(set(now) & set(before))
    if not common:
        return None
    return sum(float(now[c]) - float(before[c]) for c in common) / len(common)


def kalshi_tile(
    records: list[dict[str, Any]],
    reading: prediction.Reading | None,
    alerts: Iterable[Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """The de-escalation index on the newest download, against the archive's reading of the day before and of a
    week before (the reference the Oracle uses), and the unusual-activity alerts of the last two days."""
    now = ensure_utc(now)
    spec = oracle.DEESCALATION
    archive = [r for r in oracle.index_readings(records, spec) if r.components]
    live = weekend.index_of(reading) if reading is not None else {}
    asof = None if reading is None else iso(ensure_utc(reading.seen.to_pydatetime()))
    if not live and archive:  # no download on file: the archive's newest reading is what there is
        live, asof = dict(archive[-1].components), archive[-1].ts or archive[-1].day.isoformat()
    today = now.date()
    index = spec.combine(live.values()) if live else None
    yesterday = next((r for r in reversed(archive) if r.day <= today - timedelta(days=1)), None)
    week = next((r for r in reversed(archive) if r.day <= today - timedelta(days=oracle.LOOKBACK_DAYS)), None)
    change_day = None if yesterday is None else _mean_change(live, yesterday.components)
    change_week = None if week is None else _mean_change(live, week.components)
    recent = []
    for a in alerts:
        when = parse_iso(str(a.get("ts") or ""))
        if when is not None and (now - ensure_utc(when)).total_seconds() <= ALERT_FLAG_HOURS * 3600.0:
            recent.append(a)
    flag = bool(recent)
    if index is None:
        text = "No reading of Kalshi's de-escalation markets on file."
    else:
        moves = []
        if change_week is not None:
            moves.append(f"{change_week * 100:+.1f} points in a week")
        if change_day is not None:
            moves.append(f"{change_day * 100:+.1f} since yesterday")
        text = (
            f"The crowd gives {index * 100:.0f}% to de-escalation within 90 days"
            + (f" ({', '.join(moves)})" if moves else "")
            + ". Rising odds are what the Oracle books read as bearish for oil."
        )
        if flag:
            text += f" {len(recent)} unusual-activity alert{'s' if len(recent) != 1 else ''} in the last two days."
    return {
        "id": "kalshi",
        "label": "Kalshi: de-escalation odds",
        "value": _r(index),
        "components": [
            {"series": s, "label": spec.components.get(s, s), "p": _r(live.get(s))}
            for s in spec.components
            if s in live
        ],
        "change_day": _r(change_day),
        "change_week": _r(change_week),
        "horizon_days": spec.horizon_days,
        "alerts_recent": len(recent),
        "flag": flag,
        "text": text,
        "asof": asof,
        "source": "Kalshi (public trade API); the desk's hourly download and daily archive",
    }


# ----------------------------------------------------------------------------------------------- 2. the options
def _kalshi_by(records: list[dict[str, Any]], dte: int) -> dict[str, Any] | None:
    """What Kalshi gives each component of the de-escalation index by the options' expiry: the same three
    ladders read at a horizon of ``dte`` days, on the archive's newest day."""
    if dte <= 0:
        return None
    spec = dataclasses.replace(oracle.DEESCALATION, horizon_days=int(dte))
    readings = [r for r in oracle.index_readings(records, spec) if r.components]
    if not readings:
        return None
    last = readings[-1]
    return {
        "day": last.day.isoformat(),
        "mean": _r(last.value),
        "components": [
            {"series": s, "label": spec.components.get(s, s), "p": _r(last.components.get(s))}
            for s in spec.components
            if s in last.components
        ],
    }


def options_tile(
    store: StateStore, records: list[dict[str, Any]], market: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """The newest two-state reading of the WTI fund's options (``twostate.py``), in prices of the desk's own
    Brent and WTI quotes, with Kalshi's probabilities by the same date beside it."""
    reading = twostate.latest_reading(store)
    head = twostate.headline_of(reading)
    base = {
        "id": "options",
        "label": "What the options price",
        "flag": False,
        "source": "Cboe delayed quotes of the WTI fund's options (USO); a model of them, approx",
        "approx": True,
        "rule": twostate.describe(),
    }
    if reading is None or head is None:
        return {**base, "state": "none", "text": "No reading of the option chain on file yet.", "asof": None}
    asof = parse_iso(str(reading.get("ts") or ""))
    age_hours = None if asof is None else (ensure_utc(now) - asof).total_seconds() / 3600.0
    mix, single = head.get("mixture") or {}, head.get("single") or {}
    dist = head.get("distribution") or {}
    quantiles, tails = dist.get("quantiles") or {}, dist.get("tails") or {}
    two = bool(head.get("two_states"))

    def prices(move: float | None) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for key in ("brent", "wti"):
            quote = (market.get(key) or {}).get("price")
            out[key] = None if quote is None or move is None else _r(float(quote) * (1.0 + float(move)), 2)
        return out

    expiry = str(head.get("expiry"))
    when = date.fromisoformat(expiry).strftime("%-d %B")
    brent_low, brent_high = prices(quantiles.get("q05")).get("brent"), prices(quantiles.get("q95")).get("brent")
    if two:
        low, high, w = mix.get("low_move"), mix.get("high_move"), mix.get("w_low")
        text = (
            f"The options price TWO states by {when}: a low one ({_signed_pct(low)}, weight {_pct(w)}) and a high "
            f"one ({_signed_pct(high)}, weight {_pct(None if w is None else 1.0 - w)})."
        )
        state = "two"
    elif quantiles:
        text = (
            f"One state with fat tails, not two prices: by {when} the options put 1 chance in 20 on Brent below "
            f"{_money(brent_low)} and 1 in 20 above {_money(brent_high)} "
            f"({_signed_pct(quantiles.get('q05'))} and {_signed_pct(quantiles.get('q95'))}); a fall of 20% or more "
            f"weighs {_pct(tails.get('down_20'))}, a rise of 20% or more {_pct(tails.get('up_20'))}."
        )
        state = "one"
    else:
        text = f"The options show one state by {when}; the fit is too poor to read its tails today."
        state = "one"
    return {
        **base,
        "state": state,
        "text": text,
        "asof": reading.get("ts"),
        "age_hours": _r(age_hours, 1),
        "expiry": expiry,
        "dte": head.get("dte"),
        "forward": head.get("forward"),
        "atm_vol": head.get("atm_vol"),
        "two_states": two,
        "why_not": list(head.get("why_not") or []),
        "single": single,
        "mixture": mix,
        "quantiles": [{"level": k, "move": _r(v), **prices(v)} for k, v in sorted(quantiles.items()) if v is not None],
        "tails": tails,
        "kalshi": _kalshi_by(records, int(head.get("dte") or 0)),
        "history": twostate.history(store, 30),
        "risk_neutral_note": (
            "These are the probabilities option prices imply (risk-neutral): they include what buyers pay for "
            "protection, so they are not a forecast of how often things happen."
        ),
    }


# ----------------------------------------------------------------------------------------------- 4. reopening
def contract_month(code: str) -> date | None:
    """``BZM27`` -> 1 June 2027 (the delivery month of a futures code); None when the code is not one."""
    code = str(code or "").strip().upper()
    if len(code) < 4 or code[-3] not in MONTH_CODES or not code[-2:].isdigit():
        return None
    return date(2000 + int(code[-2:]), MONTH_CODES.index(code[-3]) + 1, 1)


def curve_half_life(closes: Mapping[str, float], today: date, months: int = 12) -> dict[str, Any] | None:
    """The delivery month by which half of the premium between the first and the ``months``-th contract of a
    curve in backwardation is gone: ``(P_1 - P_m) / (P_1 - P_n) >= 0.5``. None when the curve is not in
    backwardation or has fewer than four contracts."""
    dated = sorted(
        (m, float(p)) for c, p in closes.items() if (m := contract_month(c)) is not None and m > today and p is not None
    )
    dated = [(m, p) for m, p in dated if math.isfinite(p)][:months]
    if len(dated) < 4:
        return None
    first, last = dated[0], dated[-1]
    premium = first[1] - last[1]
    if premium <= 0:
        return {"backwardation": False, "first": first[0].isoformat(), "last": last[0].isoformat()}
    for month, price in dated:
        if (first[1] - price) / premium >= 0.5:
            return {
                "backwardation": True,
                "half_gone_by": month.isoformat(),
                "first": first[0].isoformat(),
                "first_price": _r(first[1], 2),
                "last": last[0].isoformat(),
                "last_price": _r(last[1], 2),
                "premium": _r(premium, 2),
            }
    return None


def ladder_median(points: list[tuple[date, float]], today: date) -> tuple[date | None, bool]:
    """(the date where a deadline ladder crosses 50 %, interpolated in time from (today, 0); whether it crosses
    at all). A ladder that never reaches a half has no median inside its deadlines."""
    curve = [(today, 0.0), *sorted((d, p) for d, p in points if d > today)]
    for (d0, p0), (d1, p1) in itertools.pairwise(curve):
        if p0 < 0.5 <= p1:
            share = (0.5 - p0) / (p1 - p0) if p1 > p0 else 0.0
            return d0 + timedelta(days=round((d1 - d0).days * share)), True
    return None, False


def reopening_tile(reading: prediction.Reading | None, data: DeskData, now: datetime) -> dict[str, Any]:
    """Two readings of WHEN, side by side and deliberately not combined: Kalshi's ladder "the strait back to
    normal before..." as a distribution of the date, and the Brent curve's own half-way point."""
    today = ensure_utc(now).date()
    points: list[tuple[date, float]] = []
    asof = None
    if reading is not None:
        asof = iso(ensure_utc(reading.seen.to_pydatetime()))
        for rec in prediction._event_records(reading):
            if str(rec.get("series")) != STRAIT_SERIES:
                continue
            points += oracle._ladder_points(oracle._markets_of(rec), str(rec.get("title") or ""))
    merged: dict[date, list[float]] = {}
    for d, p in points:
        merged.setdefault(d, []).append(p)
    ladder = sorted((d, sum(ps) / len(ps)) for d, ps in merged.items() if d > today)
    median, crosses = ladder_median(ladder, today)
    within = {
        f"{h}d": _r(oracle.horizon_probability(ladder, today, h)) for h in REOPENING_HORIZONS if ladder
    }  # fmt: skip
    closes: dict[str, float] = {}
    bz = data.contracts.get("BZ")
    curve_day = None
    if bz is not None and not bz.empty:
        row = bz.dropna(how="all").iloc[-1].dropna()
        closes = {str(k): float(v) for k, v in row.items()}
        curve_day = pd.Timestamp(str(row.name)).date().isoformat()
    curve = curve_half_life(closes, today) if closes else None
    parts = []
    if ladder:
        if crosses and median is not None:
            parts.append(f"Kalshi's median date for the strait back to normal is {median.strftime('%B %Y')}")
        else:
            last = ladder[-1]
            parts.append(
                f"Kalshi gives the strait back to normal only {_pct(last[1])} by {last[0].strftime('%B %Y')}, "
                "its last deadline: no median inside the ladder"
            )
        if within.get("182d") is not None:
            parts[-1] += f" ({_pct(within['182d'])} within six months)"
    if curve and curve.get("backwardation"):
        by = date.fromisoformat(curve["half_gone_by"])
        parts.append(f"half of the Brent curve's premium is gone by the {by.strftime('%B %Y')} contract")
    elif curve is not None:
        parts.append("the Brent curve is not in backwardation: it prices no shortage to end")
    if parts:
        joined = "; ".join(parts)
        text = (
            f"{joined[0].upper()}{joined[1:]}. Two different things: Kalshi settles on the AIS ship count, the "
            "curve mixes in stocks."
        )
    else:
        text = "No ladder and no curve on file."
    return {
        "id": "reopening",
        "label": "When the strait reopens",
        "flag": False,
        "text": text,
        "median": None if median is None else median.isoformat(),
        "crosses": crosses,
        "within": within,
        "ladder": [{"deadline": d.isoformat(), "p": _r(p)} for d, p in ladder],
        "curve": curve,
        "curve_day": curve_day,
        "asof": asof,
        "source": "Kalshi KXHORMUZNORM (deadline ladder); Yahoo Finance, Brent monthly contracts",
    }


# ----------------------------------------------------------------------------------------------- 5. the term
def term_tile(store: StateStore) -> dict[str, Any]:
    reading = twostate.latest_reading(store)
    flag = twostate.term_flag(reading)
    base = {
        "id": "term",
        "label": "Volatility by expiry",
        "source": "Cboe delayed quotes of the WTI fund's options (USO); at-the-money, approx",
        "approx": True,
        "flag_points": twostate.TERM_FLAG_POINTS,
    }
    if reading is None or flag is None:
        return {**base, "flag": False, "text": "No term structure of implied volatility on file yet.", "asof": None}
    near, far, gap = flag["near"], flag["far"], float(flag["gap"])
    if flag["flag"]:
        text = (
            f"An event is priced within days: the options expiring in {near['dte']} days imply "
            f"{_pct(near['atm_vol'])} volatility, {gap * 100:.0f} points above those {far['dte']} days out "
            f"({_pct(far['atm_vol'])})."
        )
    elif gap < 0:
        text = (
            f"Nothing is priced for the next days: the options expiring in {near['dte']} days imply "
            f"{_pct(near['atm_vol'])} volatility, {abs(gap) * 100:.0f} points BELOW those {far['dte']} days out "
            f"({_pct(far['atm_vol'])})."
        )
    else:
        text = (
            f"The options expiring in {near['dte']} days imply {_pct(near['atm_vol'])} volatility against "
            f"{_pct(far['atm_vol'])} at {far['dte']} days: {gap * 100:.0f} points above, under the 5 that flag an "
            "event within days."
        )
    return {
        **base,
        "flag": bool(flag["flag"]),
        "text": text,
        "gap": _r(gap),
        "near": near,
        "far": far,
        "term": list((reading or {}).get("term") or []),
        "asof": reading.get("ts"),
    }


# ----------------------------------------------------------------------------------------------- 6. Brent-WTI
def spread_tile(data: DeskData, raw: RawStore, market: Mapping[str, Any]) -> dict[str, Any]:
    """Brent against WTI. The dollar difference between the two contracts the desk quotes jumps at every roll,
    so the MOVE is read on the ratio of the two funds' adjusted closes, which has no roll: its change over
    ``SPREAD_WINDOW`` sessions against the standard deviation of such changes over the last ``SPREAD_HISTORY``."""
    base = {
        "id": "spread",
        "label": "Brent against WTI",
        "value": (market.get("brent_wti") or {}).get("value"),
        "note": (market.get("brent_wti") or {}).get("note"),
        "flag_sd": SPREAD_FLAG_SD,
        "source": "Yahoo Finance: the desk's Brent and WTI contracts; the BNO and USO funds' adjusted closes",
    }
    bno = data.series.get("BNO")
    uso = _latest(raw, "uso_daily")
    if bno is None or uso is None or uso.empty:
        return {**base, "flag": False, "text": "No history of the two funds on file to read the move on.", "asof": None}
    column = "adjclose" if "adjclose" in uso.columns else "close"
    uso_close = uso[column].astype("float64")
    uso_close.index = pd.DatetimeIndex(pd.to_datetime(uso_close.index)).tz_localize(None).normalize()
    bno_index = (1.0 + bno.returns.fillna(0.0)).cumprod()
    frame = pd.concat([np.log(bno_index), np.log(uso_close)], axis=1, keys=["bno", "uso"], sort=True).dropna()
    if len(frame) < SPREAD_HISTORY // 2:
        return {**base, "flag": False, "text": "Too little history of the two funds to read the move on.", "asof": None}
    ratio = frame["bno"] - frame["uso"]
    moves = ratio.diff(SPREAD_WINDOW).dropna()
    history = moves.iloc[-(SPREAD_HISTORY + 1) : -1]
    sd = float(history.std())
    last = float(moves.iloc[-1])
    z = last / sd if sd > 0 else None
    flag = z is not None and abs(z) >= SPREAD_FLAG_SD
    value = base["value"]
    where = "n/a" if value is None else f"{float(value):+.2f} $ a barrel"
    if z is None:
        text = f"Brent minus WTI: {where}."
    else:
        side = "gained" if last > 0 else "lost"
        text = (
            f"Brent minus WTI: {where}. Over five sessions the Brent fund has {side} {abs(last) * 100:.1f}% against "
            f"the WTI fund, {abs(z):.1f} standard deviations of a normal week"
            + (": a dislocation between the two crudes." if flag else ", inside the usual range.")
            + " It is also the basis risk of the books that trade WTI for a Brent view."
        )
    return {
        **base,
        "flag": bool(flag),
        "text": text,
        "move": _r(last, 5),
        "z": _r(z, 2),
        "asof": pd.Timestamp(moves.index[-1]).date().isoformat(),
    }


# ----------------------------------------------------------------------------------------------- 7. the system
def system_tile(health: Mapping[str, Any], books: Iterable[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    """The desk itself, in one line: what is not green, how old the last tick is, which books are halted. On a
    weekend the runner sleeps by design, so an old tick is flagged only while the futures trade."""
    now = ensure_utc(now)
    issues: list[str] = []
    last_ok = parse_iso(str(health.get("last_ok_tick") or ""))
    local = now.astimezone(NEW_YORK)
    asleep = (
        local.weekday() == 5
        or (local.weekday() == 4 and (local.hour, local.minute) >= (18, 30))
        or (local.weekday() == 6 and (local.hour, local.minute) < (17, 30))
    )
    age = None if last_ok is None else (now - ensure_utc(last_ok)).total_seconds() / 60.0
    if not asleep and (age is None or age > SYSTEM_STALE_MINUTES):
        issues.append("no tick on record" if age is None else f"the last good tick is {age / 60.0:.1f} hours old")
    overall = str(health.get("overall") or "")
    if overall == "red":
        issues.append("a critical data source is red: no new risk is taken")
    failed = int(health.get("failed_24h") or 0)
    if failed > 0:
        issues.append(f"{failed} failed tick{'s' if failed != 1 else ''} in 24 hours")
    halted = [
        str(b.get("name") or b.get("id"))
        for b in books
        if str(b.get("status") or "").lower() not in ("active", "", "none")
    ]
    if halted:
        issues.append(f"not active: {', '.join(halted[:4])}")
    # a secondary source that is not green silences what reads it and nothing else: said, never flagged
    partial = len(health.get("not_green") or [])
    aside = (
        f" {partial} secondary source{'s' if partial != 1 else ''} not green: only what reads them is silent."
        if partial
        else ""
    )
    if issues:
        text = "To look at: " + "; ".join(issues) + "." + aside
    elif asleep:
        text = (
            "Weekend: the runner sleeps until the futures reopen on Sunday evening; the prediction markets are "
            "still read every few hours." + aside
        )
    else:
        text = f"All good: the last tick ran {age:.0f} minutes ago." + aside
    return {
        "id": "system",
        "label": "The desk itself",
        "flag": bool(issues),
        "issues": issues,
        "asleep": bool(asleep),
        "last_ok_tick": health.get("last_ok_tick"),
        "tick_age_minutes": _r(age, 1),
        "text": text,
        "asof": iso(now),
        "source": "the desk's own run log and source health",
    }


# ----------------------------------------------------------------------------------------------- the block
def weekend_gaps(data: DeskData) -> dict[str, dict[str, Any]]:
    """For every Friday of the Brent fund's history since 2026: the next session's gap and continuation (facts
    the weekend tile's history shows beside each logged weekend)."""
    bno = data.series.get("BNO")
    if bno is None or bno.bars.empty or not {"open", "close"} <= set(bno.bars.columns):
        return {}
    bars = bno.bars[["open", "close"]].dropna()
    days = [pd.Timestamp(d).date() for d in bars.index]
    out: dict[str, dict[str, Any]] = {}
    for i in range(1, len(days)):
        if (days[i] - days[i - 1]).days < 3 or days[i].year < 2026:
            continue
        prev_close = float(bars["close"].iloc[i - 1])
        open_, close = float(bars["open"].iloc[i]), float(bars["close"].iloc[i])
        if prev_close <= 0 or open_ <= 0:
            continue
        friday = days[i - 1] - timedelta(days=(days[i - 1].weekday() - 4) % 7)
        out[friday.isoformat()] = {
            "gap": round(open_ / prev_close - 1.0, 5),
            "continuation": round(close / open_ - 1.0, 5),
            "session": days[i].isoformat(),
        }
    return out


def radar_block(
    store: StateStore,
    raw: RawStore,
    data: DeskData,
    market: Mapping[str, Any],
    health: Mapping[str, Any],
    books: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """The seven tiles, each built on its own: one that fails costs the page that tile and nothing else."""
    now = ensure_utc(now or datetime.now(tz=UTC))
    try:
        records = list(store.iter_jsonl(prediction.KALSHI_LOG, skip_bad=True))
    except Exception as exc:
        log.warning("radar: the Kalshi archive could not be read (%s)", exc)
        records = []
    try:
        reading = prediction.latest_reading(raw)
    except Exception as exc:
        log.warning("radar: no reading of the prediction markets (%s)", exc)
        reading = None

    builders: list[tuple[str, Any]] = [
        ("kalshi", lambda: kalshi_tile(records, reading, recent_alerts(store, now), now)),
        ("options", lambda: options_tile(store, records, market, now)),
        ("weekend", lambda: {"id": "weekend", "label": "The weekend", **weekend.block(store, now, weekend_gaps(data)),
                             "source": "Kalshi, the desk's hourly readings; Yahoo Finance for the fund's open"}),
        ("reopening", lambda: reopening_tile(reading, data, now)),
        ("term", lambda: term_tile(store)),
        ("spread", lambda: spread_tile(data, raw, market)),
        ("system", lambda: system_tile(health, books, now)),
    ]  # fmt: skip
    tiles: list[dict[str, Any]] = []
    for name, build in builders:
        try:
            tiles.append(build())
        except Exception as exc:  # a tile is a view: its failure must never cost the page
            log.exception("radar tile %s failed", name)
            error = f"{type(exc).__name__}: {exc}"[:160]
            tiles.append({"id": name, "label": name.capitalize(), "flag": False, "error": error,
                          "text": "This reading could not be computed on this export."})  # fmt: skip
    flagged = [t["id"] for t in tiles if t.get("flag")]
    return {
        "generated_at": iso(now),
        "tiles": tiles,
        "flagged": flagged,
        "summary": (
            "Nothing flagged: no signal is outside its declared range."
            if not flagged
            else f"{len(flagged)} thing{'s' if len(flagged) != 1 else ''} to look at: "
            + ", ".join(next(str(t["label"]) for t in tiles if t["id"] == f) for f in flagged)
            + "."
        ),
        "note": (
            "A flag is a footprint by a declared rule, never a verdict, and no book trades on this block. The "
            "thresholds are round numbers written down before the first reading (docs/RESEARCH.md 17 J)."
        ),
    }
