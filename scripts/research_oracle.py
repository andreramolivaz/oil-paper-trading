"""The speculative Kalshi book's rule, as registered (docs/RESEARCH.md chapter 10 C), measured on the archive.

A research script, not a job: it does not touch the live state. It reads a Kalshi history download (the
``research-data`` workflow's ``kalshi-history`` artifact, or ``tests/fixtures/kalshi_history_2026-10-15.json``),
the desk's own daily archive of the markets if one is given (the tick's readings win over the candles, as in
the backfill), and the desk's series of the micro WTI future from a copy of the data branch; then, for every
session, the forecast the rule would have made and what the book's sizing would have earned on the next
session's return, with the vehicle's costs. The numbers it printed on 15 October 2026 are in chapter 10 H.

    python scripts/research_oracle.py --history tests/fixtures/kalshi_history_2026-10-15.json \\
        --state /path/to/data-branch/state [--archive /path/to/data-branch/state/desk] [--start 2026-03-18]
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

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
from engine.desk.vehicles import MCL

VOL_TARGET = 0.50
CAP = 10.0
BUFFER = 0.10
LOT = 100.0
EQUITY = 10_000.0


def _stats(pnl: pd.Series) -> tuple[float, float, float, int]:
    p = pnl.dropna()
    mu, sd = float(p.mean()), float(p.std())
    sharpe = mu / sd * math.sqrt(252) if sd > 0 else float("nan")
    return sharpe, mu * 252, sd * math.sqrt(252), len(p)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True, help="a kalshi-history download (JSON)")
    parser.add_argument("--state", required=True, help="a copy of the data branch's state/ (the raw store)")
    parser.add_argument("--archive", default=None, help="the desk's archive directory (state/desk): its readings win")
    parser.add_argument("--start", default="2026-03-18", help="first session (the strait's ladder opened on 17 March)")
    args = parser.parse_args()

    history = json.loads(Path(args.history).read_text(encoding="utf-8"))
    records = [r for r in records_from_history(history) if r["series"] in oracle.COMPONENTS]
    if args.archive:
        live = list(StateStore(args.archive).iter_jsonl(KALSHI_LOG, skip_bad=True))
        keys = {str(r.get("key")) for r in live}
        records = [r for r in records if r["key"] not in keys] + live
    readings = oracle.index_readings(records)
    data = build_desk_data(RawStore(args.state), None)
    series = data.series["MCL"]
    runup = runup_table(sg.total_return_index(series.returns), AllocatorConfig.load())
    start = date.fromisoformat(args.start)
    days = [d.date() for d in series.returns.index if d.date() >= start]

    rows = []
    for d in days:
        f = oracle.forecast_on(readings, d)
        ts = pd.Timestamp(d)
        m = 0.5 if (ts in runup.index and bool(runup.loc[ts, "active"])) else 1.0
        rows.append(
            {
                "day": d,
                "index": None if f.reading is None else f.reading.value,
                "change": f.change,
                "forecast": f.forecast,
                "m": m,
                "vol": float(series.vol.get(ts, np.nan)),
                "price": float(series.bars.loc[ts, "close"]),
                "r_next": float(series.returns.shift(-1).get(ts, np.nan)),
            }
        )
    df = pd.DataFrame(rows).set_index("day")
    forecast = df["forecast"].fillna(0.0)
    df["expo"] = (forecast / sg.FORECAST_NORMAL * VOL_TARGET * df["m"] / df["vol"]).clip(-CAP, CAP)
    df["pnl"] = df["expo"] * df["r_next"]

    # whole lots on a fixed account (no compounding): the inertia band, the vehicle's commission and half spread
    lots, held, pnl_lots, trades, cost = 0.0, [], [], 0, 0.0
    for _, row in df.iterrows():
        per_lot = EQUITY / row["price"] / LOT
        band = BUFFER * VOL_TARGET * row["m"] / row["vol"]
        if row["forecast"] is not None and not math.isnan(row["forecast"]):
            target = buffered_target(row["expo"] * per_lot, band * per_lot, lots, CAP * per_lot)
        else:
            target = lots
        if target != lots:
            cost += abs(target - lots) * (MCL.commission_per_lot + row["price"] * LOT * MCL.half_spread_bps * 1e-4)
            trades += 1
            lots = target
        held.append(lots)
        r = row["r_next"] if not math.isnan(row["r_next"]) else 0.0
        pnl_lots.append(lots * LOT * row["price"] * r)
    df["lots"] = held
    df["pnl_lots"] = pnl_lots

    sharpe, ann, vol, n = _stats(df["pnl"])
    print(f"sessions {n}, {df.index[0]} to {df.index[-1]}; forecast non-zero on {int((forecast != 0).sum())} "
          f"(short {int((forecast < 0).sum())}, long {int((forecast > 0).sum())})")  # fmt: skip
    print(f"continuous sizing: Sharpe {sharpe:.2f} (SE {math.sqrt((1 + sharpe**2 / 2) / (n / 252)):.2f}), "
          f"{ann:+.1%}/yr, vol {vol:.1%}, hit rate {(df['pnl'][forecast != 0] > 0).mean():.2f}")  # fmt: skip
    gross = sum(pnl_lots)
    print(f"whole lots on {EQUITY:,.0f} $: {gross:+,.0f} $ gross, {cost:,.0f} $ costs, {gross - cost:+,.0f} $ net "
          f"({(gross - cost) / EQUITY:+.1%}); {trades} trades, max {df['lots'].abs().max():.0f} lots")  # fmt: skip
    print(f"mean |weekly change| {df['change'].abs().mean() * 100:.2f} points (median "
          f"{df['change'].abs().median() * 100:.2f}); mean |F| {forecast.abs().mean():.2f}; |F| = 20 on "
          f"{(forecast.abs() >= 20).mean():.0%} of sessions")  # fmt: skip
    monthly = df.groupby(pd.to_datetime(df.index).to_period("M"))["pnl"].sum()
    print("monthly (continuous):", ", ".join(f"{k} {v:+.1%}" for k, v in monthly.items()))
    daily = df["index"].astype(float).diff()
    r = pd.Series([float(series.returns.get(pd.Timestamp(d), np.nan)) for d in df.index], index=df.index)
    lags = ", ".join(f"D{k:+d} {daily.corr(r.shift(-k)):+.2f}" for k in (-2, -1, 0, 1, 2, 3))
    print(f"corr(daily index change over NY day D-1, oil return of day D+k): {lags}")
    weekly_next = r.shift(-1).rolling(5).sum().shift(-4)
    print(f"corr(weekly change, next week's oil return): {df['change'].astype(float).corr(weekly_next):+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
