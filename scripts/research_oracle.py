"""The speculative Kalshi book's rule, as registered (docs/RESEARCH.md chapter 10 C), measured on the archive.

A research script, not a job: it does not touch the live state. It reads a Kalshi history download (the
``research-data`` workflow's ``kalshi-history`` artifact, or ``tests/fixtures/kalshi_history_2026-10-15.json``),
the desk's own daily archive of the markets if one is given (the tick's readings win over the candles, as in
the backfill), and the desk's series of the micro WTI future from a copy of the data branch; then, for every
session, the forecast the rule would have made and what the book's sizing would have earned on the next
session's return, with the vehicle's costs. The numbers it printed on 15 October 2026 are in chapter 10 H.

    python scripts/research_oracle.py --history tests/fixtures/kalshi_history_2026-10-15.json \\
        --state /path/to/data-branch/state [--archive /path/to/data-branch/state/desk] [--start 2026-03-18]

Chapter 17 D (the Oracle 3x: the same rule at a volatility target of 1.50, on the micro WTI future, with the
ceilings that bind at that size and the model's odds):

    python scripts/research_oracle.py ... --vehicle MCL --vol-target 1.5 --weekend-cap 3.0 --weekend-scale 0.5 \\
        --event-budget 0.5 --odds
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
from engine.desk.book import buffered_target, is_pre_closure
from engine.desk.data import build_desk_data
from engine.desk.prediction import KALSHI_LOG
from engine.desk.twostate import largest_move
from engine.desk.vehicles import BNO, MCL, Vehicle

VOL_TARGET = 0.50
CAP = 10.0
BUFFER = 0.10
LOT = 100.0
EQUITY = 10_000.0

# chapter 16: the same rule on an instrument fine enough to express it. A fund's lot is one share and
# Regulation T caps it at 2x, which the rule cannot reach anyway (F x 0.069, so 1.39x at a maximum forecast).
NORMAL_VOL = 0.25  # engine/broker/costs.py: above this the half spread widens linearly
VEHICLES_UNDER_TEST: dict[str, tuple[Vehicle, float, float]] = {  # vehicle -> (spec, lot, leverage cap)
    "MCL": (MCL, LOT, CAP),
    "BNO": (BNO, 1.0, 2.0),
}


def _trade_cost(vehicle: Vehicle, units: float, price: float, vol: float) -> float:
    """What the broker charges to move ``units``: the half spread widened by volatility, plus commission."""
    scale = 1.0 + max(0.0, vol / NORMAL_VOL - 1.0) if math.isfinite(vol) else 1.0
    notional = abs(units) * price * (LOT if vehicle is MCL else 1.0)
    return abs(units) * vehicle.commission_per_lot + notional * vehicle.half_spread_bps * scale * 1e-4


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
    parser.add_argument("--vehicle", default="MCL", choices=sorted(VEHICLES_UNDER_TEST), help="chapter 16 B")
    parser.add_argument("--no-runup", action="store_true", help="neighbour (d): without the bubble switch")
    # chapter 17 D: the Oracle 3x. Left at their defaults, the numbers of chapters 10 and 16 are reproduced.
    parser.add_argument("--vol-target", type=float, default=VOL_TARGET, help="17 D: 1.5 for the Oracle 3x")
    parser.add_argument("--weekend-cap", type=float, default=None, help="the ceiling before a closure (3.0)")
    parser.add_argument("--weekend-scale", type=float, default=1.0, help="the strait rule's halving (0.5)")
    parser.add_argument("--event-budget", type=float, default=None, help="17 C 2 with its fallback: 0.5")
    parser.add_argument("--odds", action="store_true", help="17 D 6: the block bootstrap of the daily net returns")
    args = parser.parse_args()
    vol_target = float(args.vol_target)

    history = json.loads(Path(args.history).read_text(encoding="utf-8"))
    records = [r for r in records_from_history(history) if r["series"] in oracle.COMPONENTS]
    if args.archive:
        live = list(StateStore(args.archive).iter_jsonl(KALSHI_LOG, skip_bad=True))
        keys = {str(r.get("key")) for r in live}
        records = [r for r in records if r["key"] not in keys] + live
    readings = oracle.index_readings(records)
    data = build_desk_data(RawStore(args.state), None)
    vehicle, lot, cap = VEHICLES_UNDER_TEST[args.vehicle]
    series = data.series[args.vehicle]
    runup = runup_table(sg.total_return_index(series.returns), AllocatorConfig.load())
    start = date.fromisoformat(args.start)
    days = [d.date() for d in series.returns.index if d.date() >= start]

    rows = []
    for d in days:
        f = oracle.forecast_on(readings, d)
        ts = pd.Timestamp(d)
        m = 1.0 if args.no_runup else (0.5 if (ts in runup.index and bool(runup.loc[ts, "active"])) else 1.0)
        # the ceilings of the day (17 D 4): the book's, the lower one before a closure, the event budget on the
        # largest one-session move of the 252 sessions up to the day (its fallback: no option history exists)
        day_cap = cap
        if args.weekend_cap is not None and is_pre_closure(d):
            day_cap = min(day_cap, args.weekend_cap * args.weekend_scale)
        if args.event_budget is not None:
            worst = largest_move(series.returns.loc[:ts].tolist())
            if worst:
                day_cap = min(day_cap, args.event_budget / worst)
        rows.append(
            {
                "day": d,
                "cap": day_cap,
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
    raw_expo = forecast / sg.FORECAST_NORMAL * vol_target * df["m"] / df["vol"]
    df["expo"] = raw_expo.clip(-df["cap"], df["cap"])
    df["pnl"] = df["expo"] * df["r_next"]

    # whole lots on a fixed account (no compounding): the inertia band, the vehicle's commission and half spread
    lots, held, pnl_lots, day_cost, trades = 0.0, [], [], [], 0
    for _, row in df.iterrows():
        per_lot = EQUITY / row["price"] / lot
        band = BUFFER * vol_target * row["m"] / row["vol"]
        if row["forecast"] is not None and not math.isnan(row["forecast"]):
            target = buffered_target(row["expo"] * per_lot, band * per_lot, lots, row["cap"] * per_lot)
        else:
            target = lots
        spent = 0.0
        if target != lots:
            spent = _trade_cost(vehicle, target - lots, row["price"], row["vol"])
            trades += 1
            lots = target
        day_cost.append(spent)
        held.append(lots)
        r = row["r_next"] if not math.isnan(row["r_next"]) else 0.0
        pnl_lots.append(lots * lot * row["price"] * r)
    df["lots"] = held
    df["pnl_lots"] = pnl_lots
    df["cost"] = day_cost
    df["net"] = (df["pnl_lots"] - df["cost"]) / EQUITY  # after costs, as a fraction of the fixed account

    sharpe, ann, vol, n = _stats(df["pnl"])
    print(f"=== {args.vehicle} ({vehicle.name}), lot {lot:g} {vehicle.unit}, cap {cap:g}x, vol target {vol_target:g}"
          f"{', no bubble switch' if args.no_runup else ''}")  # fmt: skip
    if args.weekend_cap is not None or args.event_budget is not None:
        bound = (raw_expo.abs() > df["cap"] + 1e-12) & (forecast != 0)
        print(f"ceilings: the day's ceiling between {df['cap'].min():.2f}x and {df['cap'].max():.2f}x; it cut the "
              f"size wanted on {int(bound.sum())} of {int((forecast != 0).sum())} days with a forecast "
              f"(wanted up to {raw_expo.abs().max():.2f}x)")  # fmt: skip
    print(f"sessions {n}, {df.index[0]} to {df.index[-1]}; forecast non-zero on {int((forecast != 0).sum())} "
          f"(short {int((forecast < 0).sum())}, long {int((forecast > 0).sum())})")  # fmt: skip
    print(f"continuous sizing: Sharpe {sharpe:.2f} (SE {math.sqrt((1 + sharpe**2 / 2) / (n / 252)):.2f}), "
          f"{ann:+.1%}/yr, vol {vol:.1%}, hit rate {(df['pnl'][forecast != 0] > 0).mean():.2f}")  # fmt: skip
    gross, cost = sum(pnl_lots), sum(day_cost)
    wanted = forecast != 0
    holds = df["lots"] != 0
    print(f"whole lots on {EQUITY:,.0f} $: {gross:+,.0f} $ gross, {cost:,.0f} $ costs, {gross - cost:+,.0f} $ net "
          f"({(gross - cost) / EQUITY:+.1%}); {trades} trades, max {df['lots'].abs().max():.0f} lots")  # fmt: skip
    kept = (holds & wanted).sum() / max(1, int(wanted.sum()))
    print(f"ACTIVITY: a position on {holds.mean():.0%} of sessions ({int(holds.sum())} of {len(df)}); on the "
          f"{int(wanted.sum())} days the rule wanted one, it held on {kept:.0%}")  # fmt: skip
    sh_after, ann_after, _, n_after = _stats(df["net"])
    half = len(df) // 2
    print(f"AFTER COSTS at whole lots: Sharpe {sh_after:+.2f} "
          f"(SE {math.sqrt((1 + sh_after**2 / 2) / (n_after / 252)):.2f}), {ann_after:+.1%}/yr; "
          f"halves {_stats(df['net'].iloc[:half])[0]:+.2f} / {_stats(df['net'].iloc[half:])[0]:+.2f}")  # fmt: skip
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
    net = df["net"].dropna()
    print(f"daily net return of the book: volatility {net.std() * math.sqrt(252):.0%} a year, worst day "
          f"{net.min():+.1%}, best {net.max():+.1%}, "
          f"mean exposure held {(df['lots'].abs() * lot * df['price'] / EQUITY).mean():.2f}x "
          f"(max {(df['lots'].abs() * lot * df['price'] / EQUITY).max():.2f}x)")  # fmt: skip
    if args.odds:
        values = net.to_numpy(dtype="float64")
        print("ODDS (17 D 6), the archive's days in another order: " + json.dumps(bootstrap_odds(values)))
        # added after the first run (17 M): the same days with their mean removed - what the book looks like if
        # the rule has no edge, which is the prior. The first line inherits eight months that went well.
        print("ODDS with no edge (the mean removed): " + json.dumps(bootstrap_odds(values - values.mean())))
    return 0


ODDS_BLOCK = 5
ODDS_HORIZON = 63
ODDS_PATHS = 20_000
ODDS_SEED = 7
ODDS_FLOOR = 0.05


def bootstrap_odds(
    net: np.ndarray,
    horizon: int = ODDS_HORIZON,
    paths: int = ODDS_PATHS,
    block: int = ODDS_BLOCK,
    seed: int = ODDS_SEED,
) -> dict[str, float]:
    """What three months of the book look like if the days of the archive came again in another order: blocks of
    ``block`` consecutive daily net returns drawn with replacement (a circular bootstrap), compounded over
    ``horizon`` sessions, the account dead the day it falls to 5 % of its capital. A model of ONE war's eight
    months: approx."""
    rng = np.random.default_rng(seed)
    n = len(net)
    n_blocks = math.ceil(horizon / block)
    starts = rng.integers(0, n, size=(paths, n_blocks))
    index = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    drawn = net[index.reshape(paths, -1)[:, :horizon]]
    equity = np.cumprod(1.0 + np.maximum(drawn, -1.0), axis=1)
    dead = (equity <= ODDS_FLOOR).any(axis=1)
    final = np.where(dead, ODDS_FLOOR, equity[:, -1])
    low = equity.min(axis=1)
    return {
        "paths": paths,
        "sessions": horizon,
        "p_double": round(float((final >= 2.0).mean()), 4),
        "p_up_50": round(float((final >= 1.5).mean()), 4),
        "p_gain": round(float((final > 1.0).mean()), 4),
        "p_lose_half": round(float((final <= 0.5).mean()), 4),
        "p_touch_half": round(float((low <= 0.5).mean()), 4),
        "p_dead": round(float(dead.mean()), 4),
        "median_multiple": round(float(np.median(final)), 3),
        "p10_multiple": round(float(np.quantile(final, 0.10)), 3),
        "p90_multiple": round(float(np.quantile(final, 0.90)), 3),
    }


if __name__ == "__main__":
    raise SystemExit(main())
