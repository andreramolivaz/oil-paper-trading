"""The Rocket's improvement candidates (docs/RESEARCH.md 14 F), measured once on the same MODEL as 14 E.

A research script, not a job: it reads a copy of the data branch's ``state`` folder and writes nothing there. The
model is the one of ``research_rocket.py`` (Black-Scholes at a zero rate on OVX, the October 2026 put smile, a quoted
spread of 6 % of the mid plus 2 bp of the underlying, fills a quarter of the spread inside the bad side), with what
14 F registers on top: whole contracts at today's price, the touch of 10x, bold play, the cheap-volatility filter,
the +-10 threshold, longer options, rolling up on a winner, the WTI forecast. Every variant is run on the whole
history and on its two halves; the decision rule is 14 F's, not this script's. Everything it prints is approximate.

    PYTHONPATH=. python scripts/research_rocket_variants.py --state /path/to/data-branch/state [--out results.json]
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.data.raw_store import RawStore
from engine.desk.data import _daily, _latest, build_desk_data
from engine.desk.options import SMILES, _smile, put_delta
from scripts.research_rocket import (
    FEE_PER_SHARE,
    FILL,
    FLOOR,
    KALSHI_FROM,
    MAX_DELTA_LEVERAGE,
    STEP,
    WINDOW,
    call_delta,
    call_strike_for_delta,
    kalshi_changes,
    option_mid,
    put_strike_for_delta,
    quoted_spread,
)
from scripts.research_rocket import SMILE as SMILE_NAME

TODAY_PRICE = (
    162.66  # USO on 22 October 2026 (the runner's chain of 20:17 UTC): the price whole contracts are priced at
)
FEE_PER_CONTRACT = 0.05
TARGET = 10.0
SPLIT = date(2019, 1, 1)  # the two halves of the history the decision rule reads


@dataclass(frozen=True)
class Variant:
    name: str
    stake: float = 0.5
    delta: float = 0.30
    cost: float = 1.0
    kalshi: bool = False
    threshold: float = 5.0
    life: int = 21  # sessions to expiry at the entry
    roll_left: int = 5  # sold with this many sessions left
    iv_filter: float | None = None  # new bet only if OVX - realized vol (points) is below this
    restrike: float | None = None  # sold and rebought at the target delta when the delta passes this
    lots: bool = False  # whole contracts, the price path rescaled to today's level at the window's start
    forecast: str = "BNO"  # the desk series whose combined forecast is the direction


def _delta(right: str, spot: float, k: float, years: float, vol: float) -> float:
    years = max(years, 1e-6)
    return call_delta(spot, k, years, vol) if right == "C" else put_delta(spot, k, years, vol)


def window(
    px: np.ndarray,
    iv: np.ndarray,
    rv: np.ndarray,
    fc: np.ndarray,
    kalshi: np.ndarray,
    start: int,
    v: Variant,
) -> dict[str, Any]:
    """One three-month life from 1 000 $ (as a multiple of 1) at session ``start``."""
    scale = (TODAY_PRICE / px[start]) if v.lots else 1.0
    cash, pos, bets, peak, stuck = 1.0, None, 0, 1.0, False
    end = min(start + WINDOW, len(px))
    capital = 1000.0 if v.lots else 1.0  # whole contracts need dollars: the window then runs in dollars
    cash = capital
    for i in range(start, end):
        spot, vol = px[i] * scale, iv[i]
        if not (math.isfinite(spot) and math.isfinite(vol) and vol > 0.05):
            continue
        direction = 0
        if math.isfinite(fc[i]):
            direction = 1 if fc[i] >= v.threshold else (-1 if fc[i] <= -v.threshold else 0)
        value = 0.0
        liquidation = 0.0
        if pos is not None:
            left = pos["expiry"] - i
            years = max(left, 0) / 252.0
            mid = option_mid(pos["right"], spot, pos["k"], years, vol)
            sale = max(0.0, mid - FILL * v.cost * quoted_spread(mid, spot))
            delta_now = abs(_delta(pos["right"], spot, pos["k"], years, vol))
            rolling_up = v.restrike is not None and delta_now >= v.restrike and direction == pos["dir"]
            if left <= v.roll_left or direction != pos["dir"] or rolling_up:
                cash += pos["n"] * (sale - FEE_PER_SHARE) * (100.0 if v.lots else 1.0)  # 0.05 $ a contract
                pos = None
                if rolling_up:
                    direction_for_entry = direction  # a continuation: no Kalshi check
                else:
                    direction_for_entry = None
            else:
                value = pos["n"] * mid * (100.0 if v.lots else 1.0)
                liquidation = pos["n"] * sale * (100.0 if v.lots else 1.0)
                direction_for_entry = None
        else:
            direction_for_entry = None
        equity = cash + value
        peak = max(peak, (cash + liquidation) / capital)
        if equity <= FLOOR * capital:
            return {"final": 0.0, "dead": True, "touched": peak >= TARGET, "bets": bets, "stuck": False}
        if pos is None and direction != 0 and i + v.life < len(px):
            if direction_for_entry is None:  # a fresh entry: the registered conditions
                agree = True
                if v.kalshi:
                    change = kalshi[i]
                    agree = math.isfinite(change) and ((change < 0) if direction > 0 else (change > 0))
                cheap = v.iv_filter is None or (math.isfinite(rv[i]) and (vol - rv[i]) * 100.0 < v.iv_filter)
                allowed = agree and cheap
            else:
                allowed = True
            if allowed:
                years = v.life / 252.0
                right = "C" if direction > 0 else "P"
                if right == "C":
                    k = call_strike_for_delta(spot, years, vol, v.delta)
                else:
                    k = put_strike_for_delta(spot, years, vol * _smile(SMILES[SMILE_NAME], v.delta), v.delta)
                mid = option_mid(right, spot, k, years, vol)
                fill = mid + FILL * v.cost * quoted_spread(mid, spot) + FEE_PER_SHARE
                if mid > 0 and fill > 0:
                    budget = v.stake * equity
                    if v.lots:
                        n = math.floor(budget / (fill * 100.0))
                        n = min(n, math.floor(MAX_DELTA_LEVERAGE * equity / (v.delta * spot * 100.0)))
                        if n < 1:
                            stuck = True
                            continue
                        cash -= n * fill * 100.0
                    else:
                        n = budget / fill
                        n = min(n, MAX_DELTA_LEVERAGE * equity / (v.delta * spot))
                        cash -= n * fill
                    pos = {"dir": direction, "right": right, "k": k, "n": n, "expiry": i + v.life}
                    bets += 1
                    stuck = False
    i = end - 1
    liquidation = 0.0
    if pos is not None:
        spot = px[i] * scale
        mid = option_mid(pos["right"], spot, pos["k"], max(pos["expiry"] - i, 0) / 252.0, iv[i])
        liquidation = pos["n"] * max(0.0, mid - FILL * v.cost * quoted_spread(mid, spot)) * (100.0 if v.lots else 1.0)
    final = (cash + liquidation) / capital
    peak = max(peak, final)
    return {
        "final": final,
        "dead": final <= FLOOR,
        "touched": peak >= TARGET,
        "bets": bets,
        "stuck": stuck and pos is None,
    }


def odds(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    if not n:
        return {"windows": 0}
    m = np.array([r["final"] for r in results])
    return {
        "windows": n,
        "p_10x": round(float((m >= TARGET).mean()), 4),
        "p_touch_10x": round(float(np.mean([r["touched"] for r in results])), 4),
        "p_2x": round(float((m >= 2).mean()), 4),
        "p_half": round(float((m <= 0.5).mean()), 4),
        "p_dead": round(float(np.mean([r["dead"] for r in results])), 4),
        "p_stuck": round(float(np.mean([r["stuck"] for r in results])), 4),
        "median": round(float(np.median(m)), 3),
        "best": round(float(m.max()), 2),
        "bets_per_window": round(float(np.mean([r["bets"] for r in results])), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state", required=True, help="a copy of the data branch's state folder")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    state = Path(args.state)
    raw = RawStore(state)
    data = build_desk_data(raw, None)
    uso = _daily(_latest(raw, "uso_daily"))
    column = "adjclose" if "adjclose" in uso.columns and uso["adjclose"].notna().any() else "close"
    price = uso[column].astype("float64").dropna()
    forecasts = {k: data.series[k].forecasts["combined"] for k in ("BNO", "MCL")}
    rv_series = data.series["MCL"].vol
    index = price.index.intersection(forecasts["BNO"].dropna().index).intersection(data.ovx.dropna().index)
    index = pd.DatetimeIndex(index).sort_values()
    px = price.reindex(index).to_numpy(dtype="float64")
    iv = (data.ovx / 100.0).reindex(index).to_numpy(dtype="float64")
    rv = rv_series.reindex(index).to_numpy(dtype="float64")
    fcs = {k: s.reindex(index).to_numpy(dtype="float64") for k, s in forecasts.items()}
    kc = kalshi_changes(state, index)
    gap = float(np.nanmedian((iv - rv) * 100))
    print(f"sessions {len(index)}: {index[0].date()} - {index[-1].date()}; OVX - RV median {gap:.1f} pt")

    starts = list(range(0, len(index) - WINDOW, STEP))
    first = [s for s in starts if index[s].date() < SPLIT]
    second = [s for s in starts if index[s].date() >= SPLIT]
    k0 = int(np.searchsorted(index, pd.Timestamp(KALSHI_FROM)))
    kalshi_starts = [s for s in starts if s >= k0]
    base = Variant("the rule (14 A)")
    variants = [
        base,
        replace(base, name="whole contracts at today's price", lots=True),
        replace(base, name="bold play: the whole account", stake=1.0),
        replace(base, name="bold play, whole contracts", stake=1.0, lots=True),
        replace(base, name="cheap volatility: OVX - RV < 0", iv_filter=0.0),
        replace(base, name="  neighbour: OVX - RV < +5", iv_filter=5.0),
        replace(base, name="  neighbour: OVX - RV < -5", iv_filter=-5.0),
        replace(base, name="threshold +-10", threshold=10.0),
        replace(base, name="  neighbour: threshold +-15", threshold=15.0),
        replace(base, name="60-day options (42 sessions, roll at 10)", life=42, roll_left=10),
        replace(base, name="  neighbour: 90-day options (63, roll at 15)", life=63, roll_left=15),
        replace(base, name="rolling up at delta 0.70", restrike=0.70),
        replace(base, name="  neighbour: rolling up at delta 0.80", restrike=0.80),
        replace(base, name="the WTI forecast (MCL)", forecast="MCL"),
        replace(base, name="rolling up 0.70 + bold play", restrike=0.70, stake=1.0),
        replace(base, name="rolling up 0.70 + 60-day options", restrike=0.70, life=42, roll_left=10),
    ]
    results: dict[str, Any] = {"model": "approx", "sessions": [str(index[0].date()), str(index[-1].date())]}
    columns = ("win", "10x", "touch", "2x", "half", "dead", "stuck", "median", "best", "bets")
    head = f"{'variant':44s} {'period':10s} " + " ".join(f"{c:>6s}" for c in columns)
    print("\n" + head)
    for v in variants:
        fc = fcs[v.forecast]
        rows = {}
        for label, ss in (("2011-2026", starts), ("2011-2018", first), ("2019-2026", second)):
            rows[label] = odds([window(px, iv, rv, fc, kc, s, v) for s in ss])
        vk = replace(v, kalshi=True)
        rows["2026 Kalshi"] = odds([window(px, iv, rv, fc, kc, s, vk) for s in kalshi_starts])
        results[v.name.strip()] = rows
        for label, r in rows.items():
            figures = (
                f"{r['windows']:6d}",
                f"{r['p_10x']:6.1%}",
                f"{r['p_touch_10x']:6.1%}",
                f"{r['p_2x']:6.1%}",
                f"{r['p_half']:6.1%}",
                f"{r['p_dead']:6.1%}",
                f"{r['p_stuck']:6.1%}",
                f"{r['median']:6.2f}",
                f"{r['best']:6.1f}",
                f"{r['bets_per_window']:6.1f}",
            )
            print(f"{v.name:44s} {label:10s} " + " ".join(figures))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
