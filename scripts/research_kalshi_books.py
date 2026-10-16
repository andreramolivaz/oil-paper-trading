"""The speculative Kalshi books on the other markets (docs/RESEARCH.md chapter 11), measured once as registered.

A research script, not a job: it does not touch the live state. It reads a Kalshi history download (the
``research-data`` workflow's ``kalshi-history`` artifact with every series of ``oracle.ALL_SERIES``), the desk's
own archive of the markets if one is given (the tick's readings win over the candles, as in the backfill), and the
funds' series from a copy of the data branch; then, for every book of chapter 11 B and every session, the forecast
the rule would have made (each sleeve by ``oracle.forecast_on`` with the index's registered specification, the
book's signs, the mean) and what the book's sizing would have earned on the next session's return, long only,
with the vehicle's costs. The neighbours of the declared choices (horizon, scale) are printed and not chosen.

    PYTHONPATH=. python scripts/research_kalshi_books.py --history history.json --state /path/to/data-branch/state \\
        [--archive /path/to/data-branch/state/desk] [--until 2026-10-14]
"""

from __future__ import annotations

import argparse
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

VOL_TARGET = 0.50
CAP = 2.0  # a fund on Regulation T margin
BUFFER = 0.10
EQUITY = 10_000.0
# chapter 11 B: the books, their vehicle and their sleeves with the sign the book reads them with
BOOKS: dict[str, tuple[str, dict[str, float]]] = {
    "oracle_bonds": ("TLT", {"fedpath": 1.0, "recession": 1.0}),
    "oracle_equities": ("SPY", {"fedpath": 1.0, "recession": -1.0}),
    "oracle_gold": ("GLD", {"fedpath": 1.0, "deescalation": 1.0}),
    "oracle_utilities": ("XLU", {"fedpath": 1.0}),
}
# the neighbours shown and not chosen (chapter 11 D)
HORIZON_NEIGHBOURS: dict[str, tuple[int, ...]] = {"fedpath": (60, 180), "recession": (180, 730)}
SCALE_NEIGHBOURS = (0.5, 2.0)


def _stats(pnl: pd.Series) -> tuple[float, float, float, int]:
    p = pnl.dropna()
    mu, sd = float(p.mean()), float(p.std())
    sharpe = mu / sd * math.sqrt(252) if sd > 0 else float("nan")
    return sharpe, mu * 252, sd * math.sqrt(252), len(p)


def _se(sharpe: float, n: int) -> float:
    return math.sqrt((1 + sharpe**2 / 2) / (n / 252)) if n else float("nan")


def load_records(history_path: Path, archive: Path | None) -> list[dict]:
    history = json.loads(history_path.read_text(encoding="utf-8"))
    records = [r for r in records_from_history(history) if r["series"] in oracle.ALL_SERIES]
    if archive:
        live = list(StateStore(archive).iter_jsonl(KALSHI_LOG, skip_bad=True))
        keys = {str(r.get("key")) for r in live}
        records = [r for r in records if r["key"] not in keys] + live
    return records


def measure(
    book: str,
    vehicle_id: str,
    weights: dict[str, float],
    specs: dict[str, oracle.IndexSpec],
    readings: dict[str, list[oracle.IndexReading]],
    data: Any,  # DeskData
    until: date,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """One book, one specification per sleeve: the session table and the summary numbers."""
    series = data.series[vehicle_id]
    vehicle = get_vehicle(vehicle_id)
    runup = runup_table(sg.total_return_index(series.returns), AllocatorConfig.load())
    first = min(r.day for s in weights for r in readings[s] if r.components)
    days = [d.date() for d in series.returns.index if first <= d.date() <= until]
    rows = []
    for d in days:
        ts = pd.Timestamp(d)
        values: dict[str, float | None] = {}
        for sleeve in weights:
            f = oracle.forecast_on(readings[sleeve], d, specs[sleeve])
            values[sleeve] = f.forecast
        total, weight = 0.0, 0.0
        for sleeve, w in weights.items():
            v = values[sleeve]
            if v is not None and math.isfinite(v):
                total += w * v
                weight += abs(w)
        combined = None if weight == 0 else max(-20.0, min(20.0, total / weight))
        rows.append(
            {
                "day": d,
                **{f"F_{s}": values[s] for s in weights},
                "combined": combined,
                "m": 0.5 if (ts in runup.index and bool(runup.loc[ts, "active"])) else 1.0,
                "vol": float(series.vol.get(ts, np.nan)),
                "price": float(series.bars.loc[ts, "close"]),
                "r_next": float(series.returns.shift(-1).get(ts, np.nan)),
            }
        )
    df = pd.DataFrame(rows).set_index("day")
    # the exposure: the common rule, long only, capped at the fund's margin; a day without a forecast holds
    wanted = (df["combined"] / sg.FORECAST_NORMAL * VOL_TARGET * df["m"] / df["vol"]).clip(lower=0.0, upper=CAP)
    expo = wanted.where(df["combined"].notna()).ffill().fillna(0.0)
    df["expo"] = expo
    turnover = expo.diff().abs().fillna(expo.abs())
    financing = (expo - 1.0).clip(lower=0.0) * vehicle.financing_rate / 252.0
    df["pnl_gross"] = expo * df["r_next"]
    df["pnl"] = df["pnl_gross"] - turnover * vehicle.half_spread_bps * 1e-4 - financing
    # whole shares on a fixed account (no compounding): the inertia band, the vehicle's spread, financing
    shares, held, pnl_sh, trades, cost = 0.0, [], [], 0, 0.0
    for _, row in df.iterrows():
        per_share = EQUITY / row["price"]
        band = BUFFER * VOL_TARGET * row["m"] / row["vol"]
        if row["combined"] is not None and not math.isnan(row["combined"]):
            target = buffered_target(row["expo"] * per_share, band * per_share, shares, CAP * per_share)
            target = max(0.0, math.floor(target))
        else:
            target = shares
        if target != shares:
            cost += abs(target - shares) * row["price"] * vehicle.half_spread_bps * 1e-4
            trades += 1
            shares = target
        held.append(shares)
        r = row["r_next"] if not math.isnan(row["r_next"]) else 0.0
        notional = shares * row["price"]
        cost += max(0.0, notional - EQUITY) * vehicle.financing_rate / 252.0
        pnl_sh.append(notional * r)
    df["shares"] = held
    df["pnl_shares"] = pnl_sh
    sharpe, ann, vol, n = _stats(df["pnl"])
    gross_sh = float(sum(pnl_sh))
    active = df["combined"].notna()
    summary = {
        "sessions": n,
        "first": df.index[0],
        "last": df.index[-1],
        "sharpe": sharpe,
        "se": _se(sharpe, n),
        "annual": ann,
        "vol": vol,
        "sharpe_gross": _stats(df["pnl_gross"])[0],
        "hit": float((df["pnl"][df["expo"] > 0] > 0).mean()) if (df["expo"] > 0).any() else float("nan"),
        "time_in_market": float((df["expo"] > 0).mean()),
        "mean_expo": float(df["expo"].mean()),
        "max_expo": float(df["expo"].max()),
        "decided": int(active.sum()),
        "long": int((df["combined"] > 0).sum()),
        "negative": int((df["combined"] < 0).sum()),
        "net_shares": gross_sh - cost,
        "gross_shares": gross_sh,
        "cost_shares": cost,
        "trades": trades,
        **{f"meanF_{s}": float(df[f"F_{s}"].abs().mean()) for s in weights},
        **{f"nonzero_{s}": int((df[f"F_{s}"].fillna(0) != 0).sum()) for s in weights},
    }
    return df, summary


def _show(label: str, n: dict[str, float], sleeve: str) -> None:
    print(f"  {label}: Sharpe {n['sharpe']:.2f}, {n['annual']:+.1%}/yr, mean |F| {n[f'meanF_{sleeve}']:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True, help="a kalshi-history download (JSON) with every index series")
    parser.add_argument("--state", required=True, help="a copy of the data branch's state/ (the raw store)")
    parser.add_argument("--archive", default=None, help="the desk's archive directory (state/desk): its readings win")
    parser.add_argument("--until", default="2026-10-14", help="last session (its return is the last one used)")
    parser.add_argument("--csv", default=None, help="write each book's session table here (a directory)")
    args = parser.parse_args()
    until = date.fromisoformat(args.until)
    records = load_records(Path(args.history), Path(args.archive) if args.archive else None)
    data = build_desk_data(RawStore(args.state), None)
    base = {s: oracle.index_readings(records, spec) for s, spec in oracle.INDICES.items()}
    for s, rs in base.items():
        with_values = [r for r in rs if r.components]
        print(
            f"index {s}: {len(rs)} days in the archive, {len(with_values)} with a value "
            f"({with_values[0].day if with_values else None} to {with_values[-1].day if with_values else None}); "
            f"last {oracle.INDICES[s].fmt(with_values[-1].value) if with_values else 'n/a'}"
        )
    for book, (vehicle_id, weights) in BOOKS.items():
        print(f"\n=== {book} on {vehicle_id}: sleeves {weights}")
        df, s = measure(book, vehicle_id, weights, oracle.INDICES, base, data, until)
        print(
            f"sessions {s['sessions']} ({s['first']} to {s['last']}), decided on {s['decided']} "
            f"(positive {s['long']}, negative {s['negative']}); in the market {s['time_in_market']:.0%} of days, "
            f"mean exposure {s['mean_expo']:.2f}x, max {s['max_expo']:.2f}x"
        )
        print(
            f"continuous sizing, net of costs: Sharpe {s['sharpe']:.2f} (SE {s['se']:.2f}), {s['annual']:+.1%}/yr, "
            f"vol {s['vol']:.1%}, hit rate {s['hit']:.2f}; gross Sharpe {s['sharpe_gross']:.2f}"
        )
        print(
            f"whole shares on {EQUITY:,.0f} $: {s['gross_shares']:+,.0f} $ gross, {s['cost_shares']:,.0f} $ costs, "
            f"{s['net_shares']:+,.0f} $ net ({s['net_shares'] / EQUITY:+.1%}); {s['trades']} trades"
        )
        for sleeve in weights:
            print(
                f"  sleeve {sleeve}: mean |F| {s[f'meanF_{sleeve}']:.2f}, non-zero on {s[f'nonzero_{sleeve}']} sessions"
            )
        monthly = df.groupby(pd.to_datetime(df.index).to_period("M"))["pnl"].sum()
        print("monthly (continuous):", ", ".join(f"{k} {v:+.1%}" for k, v in monthly.items()))
        # attribution: each sleeve alone, with the book's sign (reporting, not a choice: the book keeps both)
        if len(weights) > 1:
            for sleeve, w in weights.items():
                _, n = measure(book, vehicle_id, {sleeve: w}, oracle.INDICES, base, data, until)
                _show(f"alone {sleeve} ({'+' if w > 0 else '-'})", n, sleeve)
        # the neighbours: shown, not chosen
        for sleeve in weights:
            spec = oracle.INDICES[sleeve]
            for h in HORIZON_NEIGHBOURS.get(sleeve, ()):
                alt = replace(spec, horizon_days=h)
                readings = {**base, sleeve: oracle.index_readings(records, alt)}
                _, n = measure(book, vehicle_id, weights, {**oracle.INDICES, sleeve: alt}, readings, data, until)
                _show(f"neighbour {sleeve} horizon {h}", n, sleeve)
            for k in SCALE_NEIGHBOURS:
                alt = replace(spec, per_unit=spec.per_unit * k)
                _, n = measure(book, vehicle_id, weights, {**oracle.INDICES, sleeve: alt}, base, data, until)
                _show(f"neighbour {sleeve} scale x{k:g}", n, sleeve)
        if args.csv:
            out = Path(args.csv)
            out.mkdir(parents=True, exist_ok=True)
            df.to_csv(out / f"{book}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
