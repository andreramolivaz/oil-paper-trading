"""The daily-change rule of docs/RESEARCH.md chapter 15 B, measured once on the archive.

A research script, not a job: it does not touch the live state. It reads the desk's own Kalshi archive (the
tick's daily readings plus the backfilled candles, as the data branch holds them), the fund's and the long WTI
series from a copy of the data branch, and for every session computes the forecast the registered rule would
have made and what the book's sizing would have earned on the NEXT session's return, with the fund's costs as
the broker charges them. The verdict is read against the five declared conditions of chapter 15 D. The numbers
it printed on 23 October 2026 are in chapter 15 E.

    python scripts/research_kalshi_daily.py --state /path/to/data-branch/state \\
        --archive /path/to/data-branch/state/desk [--start 2026-03-18]

Every constant below is the one registered in chapter 15 B, under the same name.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from engine.core.store import StateStore
from engine.data.raw_store import RawStore
from engine.desk import oracle
from engine.desk import signals as sg
from engine.desk.allocator import AllocatorConfig, runup_table
from engine.desk.book import buffered_target
from engine.desk.data import build_desk_data
from engine.desk.vehicles import BNO

DAILY_SCALE = -3.5  # forecast per percentage point of one-day change (chapter 15 B 3-4)
DEADBAND = 0.003  # 0.3 points, the weekly rule's 1 point divided by sqrt(5)
DAILY_VOL_TARGET = 0.25
DAILY_CAP = 2.0
BUFFER = 0.10
LOOKBACK = 1  # one calendar day, not seven
REFERENCE_WINDOW = 0
EQUITY = 10_000.0

NORMAL_VOL = 0.25  # engine/broker/costs.py: above this the half spread widens linearly
SLIPPAGE_BPS_PER_TURN = 1.0
MAX_SLIPPAGE_BPS = 50.0


def daily_forecast(
    readings: list[oracle.IndexReading],
    day: date,
    spec: oracle.IndexSpec,
    lookback: int = LOOKBACK,
    deadband: float = DEADBAND,
) -> tuple[float | None, float | None]:
    """(change, forecast) of the registered daily rule, or (None, None) when there is no decision.

    Same machinery as ``oracle.forecast_on`` - the newest reading with components at most ``STALE_DAYS`` old,
    the change read on the components both readings quote - with the reference ``lookback`` calendar days back
    instead of seven, and this chapter's scale and deadband.
    """
    usable = [r for r in readings if r.components]
    latest = [r for r in usable if r.day <= day]
    if not latest:
        return None, None
    reading = latest[-1]
    if (day - reading.day).days > oracle.STALE_DAYS:
        return None, 0.0  # a stale reading is no view: flat, as the Oracle does
    first = day - timedelta(days=lookback + REFERENCE_WINDOW)
    last = day - timedelta(days=lookback)
    candidates = [r for r in usable if first <= r.day <= last]
    if not candidates:
        return None, None
    today, before = oracle.common_values(reading, candidates[-1], day, spec)
    if not today:
        return None, 0.0
    change = spec.combine(today[c] - before[c] for c in today)
    if change is None:
        return None, 0.0
    if abs(change) < deadband:
        return change, 0.0
    raw = DAILY_SCALE * change * spec.unit_scale
    return change, max(-oracle.FORECAST_CAP, min(oracle.FORECAST_CAP, raw))


def _cost(traded_notional: float, vol: float) -> float:
    """The broker's charge on a traded notional: half spread widened by volatility, plus market impact."""
    if traded_notional <= 0:
        return 0.0
    scale = 1.0 + max(0.0, vol / NORMAL_VOL - 1.0) if math.isfinite(vol) else 1.0
    half_spread = BNO.half_spread_bps * scale
    slippage = min(MAX_SLIPPAGE_BPS, SLIPPAGE_BPS_PER_TURN * traded_notional / EQUITY)
    return traded_notional * (half_spread + slippage) * 1e-4


def _stats(pnl: pd.Series) -> dict[str, float]:
    p = pnl.dropna()
    if len(p) < 10:
        return {"sharpe": float("nan"), "n": len(p)}
    mu, sd = float(p.mean()), float(p.std())
    sharpe = mu / sd * math.sqrt(252) if sd > 0 else float("nan")
    years = len(p) / 252
    return {
        "sharpe": sharpe,
        "se": math.sqrt((1 + sharpe**2 / 2) / years) if math.isfinite(sharpe) else float("nan"),
        "ann": mu * 252,
        "vol": sd * math.sqrt(252),
        "n": len(p),
        "hit": float((p[p != 0] > 0).mean()) if (p != 0).any() else float("nan"),
    }


def _line(label: str, s: dict[str, float]) -> str:
    if not math.isfinite(s.get("sharpe", float("nan"))):
        return f"  {label:<34} too few sessions ({s.get('n', 0)})"
    return (
        f"  {label:<34} Sharpe {s['sharpe']:+.2f} (SE {s['se']:.2f})  {s['ann']:+.1%}/yr  "
        f"vol {s['vol']:.1%}  {s['n']} sessions"
    )


def run(
    readings: list[oracle.IndexReading],
    spec: oracle.IndexSpec,
    series: object,
    runup: pd.DataFrame,
    days: list[date],
    lookback: int = LOOKBACK,
    cost_multiplier: float = 1.0,
    deadband: float = DEADBAND,
) -> pd.DataFrame:
    """One pass of the rule over ``days``: forecast, exposure, next session's return, costs."""
    rows = []
    for d in days:
        ts = pd.Timestamp(d)
        change, forecast = daily_forecast(readings, d, spec, lookback, deadband)
        m = 0.5 if (ts in runup.index and bool(runup.loc[ts, "active"])) else 1.0
        rows.append(
            {
                "day": d,
                "change": change,
                "forecast": forecast,
                "m": m,
                "vol": float(series.vol.get(ts, np.nan)),  # type: ignore[attr-defined]
                "r_next": float(series.returns.shift(-1).get(ts, np.nan)),  # type: ignore[attr-defined]
            }
        )
    df = pd.DataFrame(rows).set_index("day")
    expo, held = [], 0.0
    for _, row in df.iterrows():
        if row["forecast"] is None or (isinstance(row["forecast"], float) and math.isnan(row["forecast"])):
            expo.append(held)
            continue
        want = row["forecast"] / sg.FORECAST_NORMAL * DAILY_VOL_TARGET * row["m"] / row["vol"]
        want = max(-DAILY_CAP, min(DAILY_CAP, want))
        band = BUFFER * DAILY_VOL_TARGET * row["m"] / row["vol"]
        held = buffered_target(want, band, held, DAILY_CAP)
        expo.append(held)
    df["expo"] = expo
    df["traded"] = df["expo"].diff().abs().fillna(df["expo"].abs()) * EQUITY
    df["cost"] = [cost_multiplier * _cost(t, v) / EQUITY for t, v in zip(df["traded"], df["vol"], strict=True)]
    df["gross"] = df["expo"] * df["r_next"]
    df["pnl"] = df["gross"] - df["cost"]
    return df


def diagnostics(df: pd.DataFrame, shifts: int = 20_000, seed: int = 7) -> None:
    """The three tests that decide whether a Sharpe this high is a signal or an artefact.

    1. The delay test of chapter 15 B's neighbour (a): the same signal one session late. A real edge loses
       strength; the crack-spread momentum of phase 11 changed sign, and so does this one.
    2. The level's own precision: the standard error of a Sharpe measured over this many sessions.
    3. A circular shift of the signal, which keeps its autocorrelation and the realised return path: the null
       for "is this association chance?". A plain permutation destroys the signal's serial structure and makes
       the null too easy to beat, so the shift is the conservative one.
    """
    r = df["r_next"].astype("float64").to_numpy()
    f = df["forecast"].astype("float64").fillna(0.0).to_numpy()
    m, v = df["m"].to_numpy(), df["vol"].to_numpy()

    def sharpe_of(signal: np.ndarray) -> float:
        expo = np.clip(signal / sg.FORECAST_NORMAL * DAILY_VOL_TARGET * m / v, -DAILY_CAP, DAILY_CAP)
        p = pd.Series(expo * r).dropna()
        return float(p.mean() / p.std() * math.sqrt(252)) if p.std() > 0 else float("nan")

    base = sharpe_of(f)
    delays = ", ".join(
        f"{k} session{'s' if k > 1 else ''} late {sharpe_of(np.concatenate([np.zeros(k), f[:-k]])):+.2f}"
        for k in (1, 2, 3)
    )
    print(f"  DELAY TEST (neighbour a): as registered {base:+.2f}; {delays}")
    n = len(df["pnl"].dropna())
    se = math.sqrt((1 + base**2 / 2) / (n / 252))
    print(f"  the level: {n} sessions = {n / 252:.2f} years -> SE {se:.2f}, t {base / se:.2f}")
    rng = np.random.default_rng(seed)
    sims = np.array([sharpe_of(np.roll(f, int(rng.integers(1, len(f))))) for _ in range(shifts)])
    sims = sims[np.isfinite(sims)]
    print(f"  circular shift ({len(sims)} draws): median {np.median(sims):+.2f}, "
          f"5-95% [{np.percentile(sims, 5):+.2f}, {np.percentile(sims, 95):+.2f}], "
          f"p(>= {base:+.2f}) = {(sims >= base).mean():.4f}")  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, help="a copy of the data branch's state/ (the raw store)")
    parser.add_argument("--archive", required=True, help="the desk's archive directory (state/desk)")
    parser.add_argument("--start", default="2026-03-18", help="first session (the strait's ladder opened on 17 March)")
    args = parser.parse_args()

    store = StateStore(args.archive)
    data = build_desk_data(RawStore(args.state), None)
    cfg = AllocatorConfig.load()
    start = date.fromisoformat(args.start)

    spec = oracle.DEESCALATION
    readings = oracle.archive_readings(store, spec)
    with_components = sum(1 for r in readings if r.components)
    print(f"archive: {len(readings)} readings of the {spec.label}, {readings[0].day} to "
          f"{readings[-1].day} ({with_components} with components)")  # fmt: skip

    for vehicle in ("BNO", "MCL"):
        series = data.series.get(vehicle)
        if series is None:
            print(f"{vehicle}: no series")
            continue
        runup = runup_table(sg.total_return_index(series.returns), cfg)
        days = [d.date() for d in series.returns.index if d.date() >= start]
        df = run(readings, spec, series, runup, days)
        label = "the fund (BNO)" if vehicle == "BNO" else "the long WTI series (MCL)"
        print()
        print(f"=== {label}: {len(df)} sessions, {df.index[0]} to {df.index[-1]}")
        f = df["forecast"].astype("float64")
        print(f"  decisions: {int(f.notna().sum())} with a forecast, {int((f != 0).sum())} non-zero "
              f"(long {int((f > 0).sum())}, short {int((f < 0).sum())})")  # fmt: skip
        ch = df["change"].astype("float64").abs() * 100
        print(f"  mean |one-day change| {ch.mean():.2f} points (median {ch.median():.2f}); "
              f"mean |F| {f.abs().mean():.2f}; |F| = 20 on {(f.abs() >= 20).mean():.0%} of sessions")  # fmt: skip
        print(f"  turnover {df['traded'].sum() / EQUITY / (len(df) / 252):.1f}x of equity a year; "
              f"costs {df['cost'].sum() / (len(df) / 252):.2%} a year")  # fmt: skip
        print(_line("gross", _stats(df["gross"])))
        print(_line("after costs", _stats(df["pnl"])))
        half = len(df) // 2
        print(_line("after costs, first half", _stats(df["pnl"].iloc[:half])))
        print(_line("after costs, second half", _stats(df["pnl"].iloc[half:])))
        months = df.groupby(pd.to_datetime(df.index).to_period("M"))["pnl"].sum()
        best = months.idxmax()
        print(f"  monthly after costs: {', '.join(f'{k} {v:+.1%}' for k, v in months.items())}")
        without = df[pd.to_datetime(df.index).to_period("M") != best]
        print(_line(f"after costs, without {best}", _stats(without["pnl"])))
        double = run(readings, spec, series, runup, days, cost_multiplier=2.0)
        print(_line("at double costs", _stats(double["pnl"])))
        # the lag table: a real signal should not work only at the lag that made it noticeable
        idx = pd.Series(
            [None if r is None else float(r) for r in df["change"].astype("float64")], index=df.index
        ).astype("float64")
        r = pd.Series([float(series.returns.get(pd.Timestamp(d), np.nan)) for d in df.index], index=df.index)
        lags = ", ".join(f"D{k:+d} {idx.corr(r.shift(-k)):+.2f}" for k in (-2, -1, 0, 1, 2, 3))
        print(f"  corr(one-day change, return of day D+k): {lags}")
        if vehicle == "BNO":
            diagnostics(df)
            for lb, name in ((2, "two-day change"), (3, "three-day change")):
                near = run(readings, spec, series, runup, days, lookback=lb)
                print(_line(f"neighbour: {name}", _stats(near["pnl"])))
            flat = run(readings, spec, series, runup, days, deadband=0.0)
            print(_line("neighbour: no deadband", _stats(flat["pnl"])))
            for other in (oracle.FEDPATH, oracle.RECESSION):
                rd = oracle.archive_readings(store, other)
                sp = replace(other, per_unit=DAILY_SCALE)
                print(_line(f"neighbour: {other.label} daily", _stats(run(rd, sp, series, runup, days)["pnl"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
