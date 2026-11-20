"""The three Kalshi books (docs/RESEARCH.md chapter 18), measured once as registered.

A research script, not a job: it does not touch the live state. It reads the desk's own archive of the
prediction markets (and a ``kalshi-history`` download of the weekly transit series, if one is given), the funds'
series from a copy of the data branch, and then measures, in the order the chapter registers them:

* **E** the deadband in each index's own units: ``0.16 x mean |weekly change|``, with the obligatory anchor (the
  de-escalation index must come back within 10 % of its 1.0 point, or the rule applies to no index at all);
* **F** the scales, the appointment phases 14 and 15 set for 60 readings: mean |F| per sleeve against the 10 of
  Carver's normalisation, touched only if off by a factor of two;
* **C** Carver's diversification multiplier on the non-desk books, with the exact check that a one-sleeve book
  comes out identical;
* **D** the fourth sleeve (the strait's weekly transit ladder read as the crowd's median), its history, its
  correlation with the de-escalation sleeve, and the Oracle with one sleeve against the Oracle with two.

    PYTHONPATH=. python scripts/research_phase23.py --state /path/to/data-branch/state \\
        --archive /path/to/data-branch/state/desk [--history hormuz_history.json] [--until 2026-11-19]
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.core.store import StateStore
from engine.data.raw_store import RawStore
from engine.desk import oracle
from engine.desk import signals as sg
from engine.desk.allocator import AllocatorConfig, runup_table
from engine.desk.backfill import records_from_history
from engine.desk.book import buffered_target
from engine.desk.data import build_desk_data
from engine.desk.prediction import KALSHI_LOG
from engine.desk.vehicles import get_vehicle

# ---------------------------------------------------------------------------------------- the registered rule
DEADBAND_FRACTION = 0.16  # chapter 18 E: 1.0 point / 6.3 points, the ratio the de-escalation index already has
ANCHOR_TOLERANCE = 0.10  # the de-escalation deadband must come back within 10 % of 1.0 point
STRAIT_PER_SHIP = -4.0  # chapter 18 D 4: fewer expected transits -> less supply -> dearer oil
STRAIT_MIN_READINGS = 120  # D, filter 1
STRAIT_MAX_CORRELATION = 0.5  # D, filter 2
MEAN_F_TARGET = 10.0  # Carver's normalisation, the check of phases 14 and 15
FDM_CAP = 1.75  # C: the core desk's own multiplier, the house ceiling

VOL_TARGET = 0.50
BUFFER = 0.10
EQUITY = 10_000.0
# the books of chapters 10, 11 and 16 as they run today: the vehicle, the signed sleeves, whether they may short
BOOKS: dict[str, tuple[str, dict[str, float], bool, float]] = {
    "oracle": ("BNO", {"deescalation": 1.0}, True, 2.0),
    "oracle_equities": ("SPY", {"fedpath": 1.0, "recession": -1.0}, False, 2.0),
    "oracle_utilities": ("XLU", {"fedpath": 1.0}, False, 2.0),
}


def _stats(pnl: pd.Series) -> tuple[float, float, int]:
    p = pnl.dropna()
    mu, sd = float(p.mean()), float(p.std())
    sharpe = mu / sd * math.sqrt(252) if sd > 0 else float("nan")
    return sharpe, mu * 252, len(p)


def _se(sharpe: float, n: int) -> float:
    return math.sqrt((1 + sharpe**2 / 2) / (n / 252)) if n else float("nan")


# ------------------------------------------------------------------- the count ladder (chapter 18 D 2)
def ladder_median(markets: list[dict[str, Any]]) -> float | None:
    """The crowd's median of a count ladder: the number at which P(more than k) crosses one half, interpolated
    linearly between the two strikes that bracket it. None when no two strikes bracket the half."""
    points: list[tuple[float, float]] = []
    for m in markets:
        floor = m.get("floor")
        bid, ask = m.get("yes_bid"), m.get("yes_ask")
        if floor is None or bid is None or ask is None:
            continue
        mid = (float(bid) + float(ask)) / 2.0
        if not 0.0 <= mid <= 1.0 or float(ask) - float(bid) > 0.10:  # the one-sided and the very wide are not quotes
            continue
        points.append((float(floor), mid))
    points.sort()
    if len(points) < 2:
        return None
    for (k0, p0), (k1, p1) in itertools.pairwise(points):
        if p0 >= 0.5 >= p1 and p0 > p1:
            return k0 + (k1 - k0) * (p0 - 0.5) / (p0 - p1)
    return None


def strait_readings(records: list[dict[str, Any]], series: str = "KXHORMUZWEEKLY") -> list[oracle.IndexReading]:
    """One median per day, off the busier event when the series has two open (a week rolls mid-week)."""
    spec = strait_spec()
    by_day: dict[str, tuple[float, float]] = {}
    stamps: dict[str, str] = {}
    for rec in records:
        if str(rec.get("series") or "") != series:
            continue
        day = str(rec.get("day") or "")
        if not day:
            continue
        markets = oracle._markets_of(rec)  # the research script reads the rows the tick writes
        volume = sum(float(m.get("volume") or 0.0) for m in markets)
        median = ladder_median(markets)
        if median is None:
            continue
        stamps[day] = max(stamps.get(day, ""), str(rec.get("ts") or ""))
        current = by_day.get(day)
        if current is None or volume > current[0]:
            by_day[day] = (volume, median)
    return [
        oracle.IndexReading(
            day=date.fromisoformat(d), ts=stamps.get(d, ""), components={series: by_day[d][1]}, spec=spec
        )
        for d in sorted(by_day)
    ]


def strait_spec(deadband: float = 1.0) -> oracle.IndexSpec:
    """The sleeve as chapter 18 D registers it: the median in ships, -4 of forecast per ship of weekly change."""
    return oracle.IndexSpec(
        id="strait_flow",
        label="strait flow",
        kind=oracle.KIND_DEADLINES,
        components={"KXHORMUZWEEKLY": "the week's transits"},
        horizon_days=7,
        per_unit=STRAIT_PER_SHIP,
        deadband=deadband,
        unit="bp",  # the unit scale is 1: the index is a number of ships, not a probability
    )


# ------------------------------------------------------------------------------------------------ the replay
def sleeve_series(readings: list[oracle.IndexReading], spec: oracle.IndexSpec, days: list[date]) -> pd.Series:
    """The sleeve's forecast on every session, as the book would have read it."""
    return pd.Series({d: oracle.forecast_on(readings, d, spec).forecast for d in days}, dtype="float64")


def replay(
    sleeves: dict[str, pd.Series],
    weights: dict[str, float],
    vehicle_id: str,
    data: Any,
    days: list[date],
    *,
    short: bool,
    cap: float,
    fdm: float = 1.0,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """One book over the sessions: the common sizing rule, whole shares, the vehicle's costs."""
    series = data.series[vehicle_id]
    vehicle = get_vehicle(vehicle_id)
    runup = runup_table(sg.total_return_index(series.returns), AllocatorConfig.load())
    rows = []
    for d in days:
        ts = pd.Timestamp(d)
        total, weight = 0.0, 0.0
        for sleeve, w in weights.items():
            v = sleeves[sleeve].get(d)
            if v is not None and isinstance(v, float) and math.isfinite(v):
                total += w * v
                weight += abs(w)
        combined = None if weight == 0 else max(-20.0, min(20.0, total / weight * fdm))
        rows.append(
            {
                "day": d,
                "combined": combined,
                "m": 0.5 if (ts in runup.index and bool(runup.loc[ts, "active"])) else 1.0,
                "vol": float(series.vol.get(ts, np.nan)),
                "price": float(series.bars.loc[ts, "close"]),
                "r_next": float(series.returns.shift(-1).get(ts, np.nan)),
            }
        )
    df = pd.DataFrame(rows).set_index("day")
    raw = df["combined"] / sg.FORECAST_NORMAL * VOL_TARGET * df["m"] / df["vol"]
    wanted = raw.clip(lower=-cap if short else 0.0, upper=cap)
    df["expo"] = wanted.where(df["combined"].notna()).ffill().fillna(0.0)
    decided = raw.notna()
    at_cap = float((raw[decided].abs() >= cap).mean()) if decided.any() else float("nan")

    shares, held, pnl_sh, trades, cost = 0.0, [], [], 0, 0.0
    for _, row in df.iterrows():
        per_share = EQUITY / row["price"]
        band = BUFFER * VOL_TARGET * row["m"] / row["vol"]
        if row["combined"] is not None and not math.isnan(row["combined"]):
            target = buffered_target(row["expo"] * per_share, band * per_share, shares, cap * per_share)
            target = math.floor(target) if target >= 0 else math.ceil(target)
        else:
            target = shares
        if target != shares:
            cost += abs(target - shares) * row["price"] * vehicle.half_spread_bps * 1e-4
            trades += 1
            shares = target
        held.append(shares)
        r = 0.0 if math.isnan(row["r_next"]) else row["r_next"]
        notional = shares * row["price"]
        cost += max(0.0, abs(notional) - EQUITY) * vehicle.financing_rate / 252.0
        pnl_sh.append(notional * r)
    df["shares"] = held
    df["pnl"] = pd.Series(pnl_sh, index=df.index) / EQUITY
    gross = float(sum(pnl_sh))
    sharpe, ann, n = _stats(df["pnl"])
    summary = {
        "sessions": float(n),
        "sharpe": sharpe,
        "se": _se(sharpe, n),
        "annual": ann,
        "net": gross - cost,
        "gross": gross,
        "cost": cost,
        "trades": float(trades),
        "time_in_market": float((df["shares"] != 0).mean()),
        "decided": float(df["combined"].notna().mean()),
        "at_cap": at_cap,
    }
    return df, summary


def halves(days: list[date]) -> tuple[list[date], list[date]]:
    cut = len(days) // 2
    return days[:cut], days[cut:]


def sharpe_of(
    sleeves: dict[str, pd.Series], weights: dict[str, float], vid: str, data: Any, days: list[date], **kw: Any
) -> float:
    return replay(sleeves, weights, vid, data, days, **kw)[1]["sharpe"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, help="a copy of the data branch's state/ (the raw store)")
    parser.add_argument("--archive", required=True, help="the desk's archive directory (state/desk)")
    parser.add_argument("--history", default=None, help="a kalshi-history download for the transit series")
    parser.add_argument("--until", default=None, help="last session (ISO date)")
    args = parser.parse_args()

    records = list(StateStore(Path(args.archive)).iter_jsonl(KALSHI_LOG, skip_bad=True))
    if args.history:
        history = json.loads(Path(args.history).read_text(encoding="utf-8"))
        keys = {str(r.get("key")) for r in records}
        extra = [r for r in records_from_history(history) if str(r.get("key")) not in keys]
        records = records + extra
        print(f"history: {len(extra)} records added to the {len(records) - len(extra)} of the archive")
    data = build_desk_data(RawStore(Path(args.state)), None)
    until = date.fromisoformat(args.until) if args.until else max(date.fromisoformat(str(r["day"])) for r in records)

    specs = dict(oracle.INDICES)
    readings = {sid: oracle.index_readings(records, spec) for sid, spec in specs.items()}
    strait = strait_readings(records)
    print(f"archive: {len({r['day'] for r in records})} days, until {until.isoformat()}")
    for sid, rs in readings.items():
        with_values = [r for r in rs if r.components]
        print(f"  {sid:14} {len(with_values):4} readings with a value, from {with_values[0].day.isoformat()}")
    print(f"  {'strait_flow':14} {len(strait):4} readings with a value" + (f", from {strait[0].day}" if strait else ""))

    # ---------------------------------------------------------------- E. the deadband in each index's own units
    print("\n=== E. the deadband in the index's own units ===")
    changes: dict[str, pd.Series] = {}
    new_deadband: dict[str, float] = {}
    all_days = sorted({r.day for rs in readings.values() for r in rs if r.components})
    for sid, spec in specs.items():
        raw = pd.Series(
            {d: oracle.forecast_on(readings[sid], d, replace(spec, deadband=0.0)).change for d in all_days},
            dtype="float64",
        ).dropna()
        changes[sid] = raw * spec.unit_scale
        mean_abs = float(changes[sid].abs().mean())
        band = round(DEADBAND_FRACTION * mean_abs, 2)
        new_deadband[sid] = band
        unit = "points" if spec.unit == "points" else "bp"
        shown = spec.deadband * 100 if spec.unit == "points" else spec.deadband
        print(
            f"  {sid:14} mean |weekly change| {mean_abs:6.2f} {unit:6} -> deadband {band:5.2f} "
            f"(today {shown:.2f}) [{len(changes[sid])} weeks]"
        )
    anchor = new_deadband["deescalation"]
    ok_anchor = abs(anchor - 1.0) <= ANCHOR_TOLERANCE
    verdict = "HOLDS" if ok_anchor else "FAILS"
    print(f"  anchor: de-escalation comes back at {anchor:.2f} points against 1.00 -> {verdict}")

    # ---------------------------------------------------------------------------- F. the scales, as scheduled
    print("\n=== F. the scales (mean |F| against 10) ===")
    sleeves_now: dict[str, pd.Series] = {}
    sessions: dict[str, list[date]] = {}
    for sid, spec in specs.items():
        vid = next(v for _b, (v, w, _s, _c) in BOOKS.items() if sid in w)
        days = [d.date() for d in data.series[vid].returns.index if d.date() <= until]
        first = min(r.day for r in readings[sid] if r.components)
        days = [d for d in days if d >= first]
        sessions[sid] = days
        sleeves_now[sid] = sleeve_series(readings[sid], spec, days)
        s = sleeves_now[sid].dropna()
        nonzero = s[s != 0]
        mean_f = float(s.abs().mean())
        speaks = float(nonzero.abs().mean()) if len(nonzero) else float("nan")
        ok = "ok" if 0.5 * MEAN_F_TARGET <= mean_f <= 2 * MEAN_F_TARGET else "OFF BY MORE THAN TWO"
        print(
            f"  {sid:14} mean |F| {mean_f:5.2f} over {len(s)} sessions "
            f"({speaks:5.2f} when it speaks, {len(nonzero)} of {len(s)}) -> {ok}"
        )

    # --------------------------------------------------------- C. Carver's multiplier on the non-desk books
    print("\n=== C. the diversification multiplier ===")
    for book, (vid, weights, short, cap) in BOOKS.items():
        days = [d.date() for d in data.series[vid].returns.index if d.date() <= until]
        first = max(min(r.day for r in readings[s] if r.components) for s in weights)
        days = [d for d in days if d >= first]
        sl = {s: sleeve_series(readings[s], specs[s], days) for s in weights}
        if len(weights) == 1:
            base = replay(sl, weights, vid, data, days, short=short, cap=cap)[0]
            same = replay(sl, weights, vid, data, days, short=short, cap=cap, fdm=1.0)[0]
            identical = base.equals(same)
            print(f"  {book:17} one sleeve: FDM = 1 by construction, identical: {identical}")
            continue
        signed = pd.DataFrame({s: sl[s] * w for s, w in weights.items()}).dropna()
        rho = float(signed.corr().iloc[0, 1]) if len(signed) > 2 else float("nan")
        v = np.array([abs(w) for w in weights.values()])
        v = v / v.sum()
        corr = np.array([[1.0, rho], [rho, 1.0]])
        fdm = min(FDM_CAP, float(1.0 / math.sqrt(float(v @ corr @ v))))
        _, before = replay(sl, weights, vid, data, days, short=short, cap=cap)
        _, after = replay(sl, weights, vid, data, days, short=short, cap=cap, fdm=fdm)
        h1, h2 = halves(days)
        b1 = sharpe_of(sl, weights, vid, data, h1, short=short, cap=cap)
        b2 = sharpe_of(sl, weights, vid, data, h2, short=short, cap=cap)
        a1 = sharpe_of(sl, weights, vid, data, h1, short=short, cap=cap, fdm=fdm)
        a2 = sharpe_of(sl, weights, vid, data, h2, short=short, cap=cap, fdm=fdm)
        print(f"  {book:17} both sleeves speak on {len(signed)} days, correlation {rho:+.2f} -> FDM {fdm:.2f}")
        print(
            f"    at its ceiling on {before['at_cap']:.0%} of the sessions it decides"
            f" -> {after['at_cap']:.0%} with the multiplier"
        )
        print(f"    whole window  Sharpe {before['sharpe']:+.2f} -> {after['sharpe']:+.2f} (SE {before['se']:.2f})")
        print(f"    first half    Sharpe {b1:+.2f} -> {a1:+.2f}")
        print(f"    second half   Sharpe {b2:+.2f} -> {a2:+.2f}")
        passes = (a1 >= b1 or math.isnan(b1)) and (a2 >= b2 or math.isnan(b2)) and fdm < FDM_CAP
        print(f"    filter (2) and (3): {'PASSES' if passes else 'FAILS'}")

    # ------------------------------------------------------------------------- D. the strait-intensity sleeve
    print("\n=== D. the strait-intensity sleeve ===")
    print(f"  filter (1): {len(strait)} readings with a value against the {STRAIT_MIN_READINGS} registered")
    if len(strait) < STRAIT_MIN_READINGS:
        print("  -> the sleeve does NOT enter: the archive does not reach the registered minimum.")
    vid, weights, short, cap = BOOKS["oracle"]
    days = [d.date() for d in data.series[vid].returns.index if d.date() <= until]
    if strait:
        sspec = strait_spec()
        sdays = [d for d in days if d >= strait[0].day]
        s_sleeve = sleeve_series(strait, sspec, sdays)
        s_vals = s_sleeve.dropna()
        d_sleeve = sleeve_series(readings["deescalation"], specs["deescalation"], sdays)
        both = pd.DataFrame({"strait": s_sleeve, "deesc": d_sleeve}).dropna()
        rho = float(both.corr().iloc[0, 1]) if len(both) > 2 else float("nan")
        mean_f = float(s_vals.abs().mean())
        print(f"  the sleeve speaks on {len(s_vals)} of {len(sdays)} sessions, mean |F| {mean_f:.2f}")
        print(
            f"  filter (2): correlation with de-escalation {rho:+.2f} on {len(both)} days "
            f"-> {'passes' if abs(rho) < STRAIT_MAX_CORRELATION else 'FAILS'}"
        )
        print(f"  filter (4): mean |F| {mean_f:.2f} -> {'passes' if 5.0 <= mean_f <= 20.0 else 'FAILS'}")
        if len(sdays) > 20:
            one = {"deescalation": d_sleeve}
            two = {"deescalation": d_sleeve, "strait_flow": s_sleeve}
            w1 = {"deescalation": 1.0}
            w2 = {"deescalation": 1.0, "strait_flow": 1.0}
            h1, h2 = halves(sdays)
            print(f"  filter (3): the Oracle with one sleeve against two, on {len(sdays)} sessions")
            for label, dd in (("whole window", sdays), ("first half", h1), ("second half", h2)):
                a = sharpe_of(one, w1, vid, data, dd, short=short, cap=cap)
                b = sharpe_of(two, w2, vid, data, dd, short=short, cap=cap)
                print(f"    {label:14} {a:+.2f} -> {b:+.2f}")
    print("\n(every number above is the one measurement chapter 18 registers; the verdicts go in section M.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
