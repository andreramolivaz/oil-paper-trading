"""The 1 000 $ lottery book (docs/RESEARCH.md chapter 14), measured once on a MODEL, as registered in 14 C.

A research script, not a job: it reads a copy of the data branch's ``state`` folder and writes nothing there. No free
history of option quotes exists, so the options are priced as the options book's replay prices them (Black-Scholes at
a zero rate on the OVX index; the put smile of the USO chain of 8 October 2026, calls at the at-the-money volatility;
a quoted spread of 6 % of the mid plus 2 bp of the underlying; fills a quarter of the spread inside the bad side;
fractional contracts; 21 sessions to expiry at the entry, rolled with 5 left), on USO's adjusted close and the Brent
fund's combined forecast (the Dynamic's). Everything it prints is approximate and decides nothing: the book was asked
for as a lottery, and these are its odds.

    PYTHONPATH=. python scripts/research_rocket.py --state /path/to/data-branch/state [--out results.json]
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.core.store import StateStore
from engine.data.raw_store import RawStore
from engine.desk import oracle
from engine.desk.data import _daily, _latest, build_desk_data
from engine.desk.options import REPLAY_QUOTED_SPREAD, SMILES, _norm_ppf, _smile, bs_put, put_delta

START_CAPITAL = 1000.0
WINDOW = 63  # three months of sessions
STEP = 5  # a new window every week
LIFE = 21  # sessions to expiry at the entry (about 30 days)
ROLL_LEFT = 5  # sold with this many sessions left (about 7 days)
THRESHOLD = 5.0  # the Dynamic's forecast that is a direction
FLOOR = 0.05  # dead at 5 % of the start
MAX_DELTA_LEVERAGE = 10.0
FILL = 0.25  # a quarter of the spread inside the bad side
FEE_PER_SHARE = 0.05 / 100.0
SMILE = "ottobre_2026"
KALSHI_FROM = date(2026, 3, 18)


@dataclass(frozen=True)
class Variant:
    name: str
    stake: float = 0.5
    delta: float = 0.30
    cost: float = 1.0
    kalshi: bool = True


def bs_call(spot: float, strike: float, years: float, vol: float) -> float:
    return bs_put(spot, strike, years, vol) + spot - strike


def call_strike_for_delta(spot: float, years: float, vol: float, delta: float) -> float:
    sd = vol * math.sqrt(years)
    return spot * math.exp(-_norm_ppf(delta) * sd + 0.5 * sd * sd)


def put_strike_for_delta(spot: float, years: float, vol: float, delta: float) -> float:
    sd = vol * math.sqrt(years)
    return spot * math.exp(_norm_ppf(delta) * sd + 0.5 * sd * sd)


def call_delta(spot: float, strike: float, years: float, vol: float) -> float:
    return 1.0 - put_delta(spot, strike, years, vol)


def option_mid(right: str, spot: float, strike: float, years: float, atm_vol: float) -> float:
    if right == "C":
        return bs_call(spot, strike, years, atm_vol)
    m = _smile(SMILES[SMILE], put_delta(spot, strike, max(years, 1e-6), atm_vol))
    return bs_put(spot, strike, years, atm_vol * m)


def quoted_spread(mid: float, spot: float) -> float:
    a, b = REPLAY_QUOTED_SPREAD
    return a * mid + b * spot


def window(
    px: np.ndarray, iv: np.ndarray, fc: np.ndarray, kalshi: np.ndarray, start: int, v: Variant
) -> tuple[float, bool, int]:
    """One three-month life from START_CAPITAL at session ``start``: (final multiple, died, bets opened)."""
    cash, pos, bets = 1.0, None, 0
    end = min(start + WINDOW, len(px))
    for i in range(start, end):
        spot, vol = px[i], iv[i]
        if not (math.isfinite(spot) and math.isfinite(vol) and vol > 0.05):
            continue
        direction = 0
        if math.isfinite(fc[i]):
            direction = 1 if fc[i] >= THRESHOLD else (-1 if fc[i] <= -THRESHOLD else 0)
        value = 0.0
        if pos is not None:
            left = pos["expiry"] - i
            years = max(left, 0) / 252.0
            mid = option_mid(pos["right"], spot, pos["k"], years, vol)
            if left <= ROLL_LEFT or direction != pos["dir"]:
                sale = max(0.0, mid - FILL * v.cost * quoted_spread(mid, spot))
                cash += pos["n"] * (sale - FEE_PER_SHARE)
                pos = None
            else:
                value = pos["n"] * mid
        equity = cash + value
        if equity <= FLOOR:
            return 0.0, True, bets
        if pos is None and direction != 0 and i + LIFE < len(px):
            agree = True
            if v.kalshi:
                change = kalshi[i]
                agree = math.isfinite(change) and ((change < 0) if direction > 0 else (change > 0))
            if agree:
                years = LIFE / 252.0
                right = "C" if direction > 0 else "P"
                if right == "C":
                    k = call_strike_for_delta(spot, years, vol, v.delta)
                else:
                    k = put_strike_for_delta(spot, years, vol * _smile(SMILES[SMILE], v.delta), v.delta)
                mid = option_mid(right, spot, k, years, vol)
                fill = mid + FILL * v.cost * quoted_spread(mid, spot) + FEE_PER_SHARE
                if mid > 0 and fill > 0:
                    n = v.stake * equity / fill
                    n = min(n, MAX_DELTA_LEVERAGE * equity / (v.delta * spot))  # the 10x rule on the delta notional
                    cash -= n * fill
                    pos = {"dir": direction, "right": right, "k": k, "n": n, "expiry": i + LIFE}
                    bets += 1
    i = end - 1
    value = 0.0
    if pos is not None:
        mid = option_mid(pos["right"], px[i], pos["k"], max(pos["expiry"] - i, 0) / 252.0, iv[i])
        value = pos["n"] * max(0.0, mid - FILL * v.cost * quoted_spread(mid, px[i]))
    final = cash + value
    return final, final <= FLOOR, bets


def odds(px: np.ndarray, iv: np.ndarray, fc: np.ndarray, kalshi: np.ndarray, starts: range, v: Variant) -> dict:
    out = [window(px, iv, fc, kalshi, s, v) for s in starts]
    m = np.array([o[0] for o in out])
    dead = np.array([o[1] for o in out])
    n = len(m)
    return {
        "windows": n,
        "p_10x": round(float((m >= 10).mean()), 4) if n else None,
        "p_2x": round(float((m >= 2).mean()), 4) if n else None,
        "p_half": round(float((m <= 0.5).mean()), 4) if n else None,
        "p_dead": round(float(dead.mean()), 4) if n else None,
        "median": round(float(np.median(m)), 3) if n else None,
        "best": round(float(m.max()), 2) if n else None,
        "bets_per_window": round(float(np.mean([o[2] for o in out])), 2) if n else None,
        "_multiples": m,
    }


def kalshi_changes(state: Path, index: pd.DatetimeIndex) -> np.ndarray:
    readings = oracle.archive_readings(StateStore(state / "desk"))
    out = np.full(len(index), np.nan)
    for i, ts in enumerate(index):
        day = ts.date()
        if day < KALSHI_FROM:
            continue
        verdict = oracle.forecast_on(readings, day)
        if verdict.change is not None and math.isfinite(verdict.change):
            out[i] = verdict.change
    return out


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
    forecast = data.series["BNO"].forecasts["combined"]
    index = price.index.intersection(forecast.dropna().index).intersection(data.ovx.dropna().index)
    index = pd.DatetimeIndex(index).sort_values()
    px = price.reindex(index).to_numpy(dtype="float64")
    iv = (data.ovx / 100.0).reindex(index).to_numpy(dtype="float64")
    fc = forecast.reindex(index).to_numpy(dtype="float64")
    kc = kalshi_changes(state, index)
    print(
        f"sessions {len(index)}: {index[0].date()} - {index[-1].date()}; Kalshi readings {int(np.isfinite(kc).sum())}"
    )

    all_starts = range(0, len(index) - WINDOW, STEP)
    k0 = int(np.searchsorted(index, pd.Timestamp(KALSHI_FROM)))
    kalshi_starts = range(k0, len(index) - WINDOW, STEP)
    variants = [
        Variant("the rule (half the account, delta 0.30)"),
        Variant("a quarter of the account", stake=0.25),
        Variant("the whole account", stake=1.0),
        Variant("delta 0.15", delta=0.15),
        Variant("delta 0.50", delta=0.50),
        Variant("double costs", cost=2.0),
    ]
    results: dict[str, Any] = {"model": "approx", "sessions": [str(index[0].date()), str(index[-1].date())]}
    print(f"\n{'variant':40s} {'period':22s} {'win':>4s} {'10x':>6s} {'2x':>6s} {'half':>6s} {'dead':>6s} "
          f"{'median':>7s} {'best':>6s} {'bets':>5s}")  # fmt: skip
    for v in variants:
        for label, starts, use_kalshi in (
            ("2011-2026, Dynamic only", all_starts, False),
            ("2026, with Kalshi", kalshi_starts, True),
            ("2026, Dynamic only", kalshi_starts, False),
        ):
            vv = Variant(v.name, v.stake, v.delta, v.cost, use_kalshi)
            r = odds(px, iv, fc, kc, starts, vv)
            ten = [str(index[s].date()) for s, m in zip(starts, r.pop("_multiples"), strict=True) if m >= 10]
            r["ten_x_starts"] = ten
            results.setdefault(v.name, {})[label] = r
            print(f"{v.name:40s} {label:22s} {r['windows']:4d} {r['p_10x']:6.1%} {r['p_2x']:6.1%} {r['p_half']:6.1%} "
                  f"{r['p_dead']:6.1%} {r['median']:7.2f} {r['best']:6.1f} {r['bets_per_window']:5.1f}")  # fmt: skip
    rule = results["the rule (half the account, delta 0.30)"]["2011-2026, Dynamic only"]
    print("\n10x windows of the rule without Kalshi start on:", rule["ten_x_starts"])
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
