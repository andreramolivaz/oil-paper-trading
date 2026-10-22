"""Kalshi's oil price ladders in the Dynamic's direction (docs/RESEARCH.md 14 F), measured once as registered.

A research script, not a job: it reads a Kalshi history download (the ``research-data`` workflow's ``kalshi-history``
artifact for the oil ladders: KXBRENTW, KXWTIW, KXBRENTMON, KXWTIMONTHLY, and the daily KXBRENTD, KXWTI) and a copy
of the data branch for the Dynamic's forecast, and writes nothing. For every settled "above X" market and every day
its candle was quoted, a bet in the Dynamic's direction of that day (YES at the ask for a long, NO at one minus the
bid for a short), taker fee on top (round up(0.07 x P x (1 - P)) a contract, to the cent), held to the settlement:
the share that wins, the return per dollar and the multiple, by price bucket. Beside it the same bets with no signal
(every day, both sides: the favourite-longshot bias on these data), and three-month lives from 1 000 $ that bet half
the account on the ladder's contract priced nearest 25 cents, one at a time, in the signal's direction. Every number
is what the candles say, not a model; the candle is the day's close, a real order fills elsewhere.

    PYTHONPATH=. python scripts/research_rocket_kalshi.py --history history.json --state /path/to/data-branch/state
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.data.raw_store import RawStore
from engine.desk.data import build_desk_data

THRESHOLD = 5.0
BUCKETS = ((0.0, 0.15), (0.15, 0.40), (0.40, 0.70), (0.70, 1.0))
TARGET_PRICE = 0.25  # the lives' contract: the strike whose price in the bet's direction is nearest this
STAKE = 0.5
CAPITAL = 1000.0
WINDOW_DAYS = 91
STEP_DAYS = 7
FLOOR = 0.05


def fee(price: float, contracts: int = 1) -> float:
    """Kalshi's taker fee on an order of ``contracts`` at ``price``, rounded up to the cent per ORDER (the published
    formula, checked 10 October 2026): 7 % of P x (1 - P) a contract, so 1 cent on one contract at 10 cents."""
    return math.ceil(0.07 * contracts * price * (1.0 - price) * 100.0 - 1e-9) / 100.0


def fee_rate(price: float) -> float:
    """The same fee per contract before the rounding: what a large order pays, per dollar of price."""
    return 0.07 * price * (1.0 - price)


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    ts = pd.Timestamp(str(value))
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.to_pydatetime()


def load(history: dict[str, Any]) -> list[dict[str, Any]]:
    """Every settled 'above X' market with its result and its daily candles (one row a day, the day in New York)."""
    out: list[dict[str, Any]] = []
    for series, block in (history.get("series") or {}).items():
        candles = block.get("candles") or {}
        for m in block.get("markets") or []:
            result = str(m.get("result") or "").lower()
            if result not in ("yes", "no") or str(m.get("strike_type") or "") != "greater":
                continue
            closed = _when(m.get("close_time"))
            rows = []
            for c in candles.get(str(m.get("ticker")), []) or []:
                end = datetime.fromtimestamp(int(c["end_ts"]), tz=UTC)
                if closed is not None and end >= closed:
                    continue  # a candle that ends after the close is not a quote one could have taken
                bid, ask = c.get("yes_bid_close"), c.get("yes_ask_close")
                if bid is None or ask is None:
                    continue
                bid, ask = float(bid), float(ask)
                if not (0.0 <= bid <= ask <= 1.0) or ask - bid > 0.10:
                    continue  # one-sided or wider than the desk's own limit for a readable quote
                day = end.astimezone(pd.Timestamp(end).tz_convert("America/New_York").tzinfo).date()
                rows.append({"day": day, "bid": bid, "ask": ask, "volume": float(c.get("volume") or 0.0)})
            if rows:
                out.append(
                    {
                        "series": series,
                        "ticker": str(m.get("ticker")),
                        "strike": float(m.get("floor_strike") or 0.0),
                        "result": result,
                        "closed": closed,
                        "candles": rows,
                    }
                )
    return out


def bets(markets: list[dict[str, Any]], direction: dict[date, int], signal: bool) -> pd.DataFrame:
    """One row per (market, day) bet: the price paid with the fee, whether it won, the return per dollar."""
    rows: list[dict[str, Any]] = []
    for m in markets:
        won_yes = m["result"] == "yes"
        for c in m["candles"]:
            sides = []
            if signal:
                d = direction.get(c["day"], 0)
                if d == 0:
                    continue
                sides.append(d)
            else:
                sides = [1, -1]
            for side in sides:
                price = c["ask"] if side > 0 else 1.0 - c["bid"]
                if not (0.0 < price < 1.0):
                    continue
                cost = price + fee_rate(price)
                won = won_yes if side > 0 else not won_yes
                rows.append(
                    {
                        "series": m["series"],
                        "ticker": m["ticker"],
                        "day": c["day"],
                        "side": side,
                        "price": price,
                        "cost": cost,
                        "won": won,
                        "ret": (1.0 - cost) / cost if won else -1.0,
                        "days_to_close": (m["closed"].date() - c["day"]).days if m["closed"] else None,
                    }
                )
    return pd.DataFrame(rows)


def by_bucket(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for lo, hi in BUCKETS:
        part = frame[(frame["price"] > lo) & (frame["price"] <= hi)]
        if part.empty:
            out.append({"bucket": f"{lo:.2f}-{hi:.2f}", "bets": 0})
            continue
        out.append(
            {
                "bucket": f"{lo:.2f}-{hi:.2f}",
                "bets": len(part),
                "markets": int(part["ticker"].nunique()),
                "won": round(float(part["won"].mean()), 4),
                "price_mean": round(float(part["price"].mean()), 4),
                "ret_per_dollar": round(float(part["ret"].mean()), 4),
                "se": round(float(part["ret"].std() / math.sqrt(len(part))), 4),
                "multiple_won": round(float((1.0 / part.loc[part["won"], "cost"]).mean()), 2)
                if part["won"].any()
                else None,
            }
        )
    return out


def lives(
    markets: list[dict[str, Any]],
    direction: dict[date, int],
    start: date,
    end: date,
    window_days: int = WINDOW_DAYS,
    target_price: float = TARGET_PRICE,
) -> dict[str, Any]:
    """Lives of ``window_days`` from 1 000 $: on each day with a direction and no open bet, buy the ladder's contract
    priced nearest ``target_price`` in that direction (whole contracts, half the equity), hold to the settlement."""
    # the quotes by day: every market quoted that day
    by_day: dict[date, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for m in markets:
        for c in m["candles"]:
            by_day.setdefault(c["day"], []).append((m, c))
    finals, touched, dead, stuck, nbets = [], [], [], [], []
    s = start
    while s + timedelta(days=window_days) <= end:
        cash, pos, n, peak, day, was_stuck = CAPITAL, None, 0, 1.0, s, False
        stop = s + timedelta(days=window_days)
        while day < stop:
            if pos is not None and pos["closed"] is not None and day >= pos["closed"].date():
                cash += pos["contracts"] * (1.0 if pos["won"] else 0.0)
                pos = None
            if pos is None:
                d = direction.get(day, 0)
                quotes = by_day.get(day, [])
                if d != 0 and quotes:
                    best = None
                    for m, c in quotes:
                        price = c["ask"] if d > 0 else 1.0 - c["bid"]
                        if not (0.02 <= price <= 0.98):
                            continue
                        if best is None or abs(price - target_price) < abs(best[2] - target_price):
                            best = (m, c, price)
                    if best is not None:
                        m, c, price = best
                        k = int((STAKE * cash) // (price + fee_rate(price)))
                        while k >= 1 and k * price + fee(price, k) > STAKE * cash:
                            k -= 1
                        if k >= 1:
                            cash -= k * price + fee(price, k)
                            won = (m["result"] == "yes") if d > 0 else (m["result"] == "no")
                            pos = {"contracts": k, "won": won, "closed": m["closed"]}
                            n += 1
                            was_stuck = False
                        else:
                            was_stuck = True
            value = cash + (pos["contracts"] * (1.0 if pos["won"] else 0.0) if pos else 0.0)
            peak = max(peak, value / CAPITAL)
            day += timedelta(days=1)
        final = (cash + (pos["contracts"] * (1.0 if pos["won"] else 0.0) if pos else 0.0)) / CAPITAL
        finals.append(final)
        touched.append(peak >= 10.0)
        dead.append(final <= FLOOR)
        stuck.append(was_stuck and pos is None)
        nbets.append(n)
        s += timedelta(days=STEP_DAYS)
    m = np.array(finals)
    return {
        "windows": len(m),
        "p_10x": round(float((m >= 10).mean()), 4) if len(m) else None,
        "p_touch_10x": round(float(np.mean(touched)), 4) if len(m) else None,
        "p_2x": round(float((m >= 2).mean()), 4) if len(m) else None,
        "p_half": round(float((m <= 0.5).mean()), 4) if len(m) else None,
        "p_dead": round(float(np.mean(dead)), 4) if len(m) else None,
        "p_stuck": round(float(np.mean(stuck)), 4) if len(m) else None,
        "median": round(float(np.median(m)), 3) if len(m) else None,
        "best": round(float(m.max()), 2) if len(m) else None,
        "bets_per_window": round(float(np.mean(nbets)), 1) if len(m) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--history", required=True, action="append", help="a kalshi-history download (repeatable)")
    ap.add_argument("--state", required=True, help="a copy of the data branch's state folder")
    ap.add_argument("--out", default=None)
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS, help="the lives' length (91 as registered)")
    args = ap.parse_args()
    markets: list[dict[str, Any]] = []
    for path in args.history:
        markets += load(json.loads(Path(path).read_text(encoding="utf-8")))
    data = build_desk_data(RawStore(Path(args.state)), None)
    fc = data.series["BNO"].forecasts["combined"].dropna()
    direction = {
        pd.Timestamp(t).date(): (1 if v >= THRESHOLD else (-1 if v <= -THRESHOLD else 0)) for t, v in fc.items()
    }
    n_days = sum(len(m["candles"]) for m in markets)
    first = min(c["day"] for m in markets for c in m["candles"])
    last = max(c["day"] for m in markets for c in m["candles"])
    print(f"{len(markets)} settled 'above X' markets, {n_days} quoted days, {first} - {last}")
    print(pd.Series([m["series"] for m in markets]).value_counts().to_string())
    results: dict[str, Any] = {"markets": len(markets), "first": str(first), "last": str(last)}
    signal = bets(markets, direction, signal=True)
    noise = bets(markets, direction, signal=False)
    for label, frame in (("in the Dynamic's direction", signal), ("every day, both sides (no signal)", noise)):
        print(f"\n{label}: {len(frame)} bets")
        table = by_bucket(frame)
        results[label] = table
        for row in table:
            if row["bets"]:
                print(
                    f"  {row['bucket']}: {row['bets']:5d} bets on {row['markets']:4d} markets, won {row['won']:.1%}, "
                    f"price {row['price_mean']:.3f}, return per dollar {row['ret_per_dollar']:+.3f} "
                    f"(se {row['se']:.3f}), multiple when won {row['multiple_won']}"
                )
            else:
                print(f"  {row['bucket']}: no bet")
        for series, part in frame.groupby("series"):
            results[f"{label} / {series}"] = by_bucket(part)
            won, ret = part["won"].mean(), part["ret"].mean()
            print(f"  {series}: {len(part)} bets, won {won:.1%}, return per dollar {ret:+.3f}")
    for target in (TARGET_PRICE, 0.55):  # the registered 25 cents, and the bucket that showed an edge (after looking)
        for series in [*sorted({m["series"] for m in markets}), "all"]:
            part = [m for m in markets if series == "all" or m["series"] == series]
            life = lives(part, direction, first, last, args.window_days, target)
            results[f"lives {args.window_days}d / nearest {target:.2f} / {series}"] = life
            print(f"\nlives of {args.window_days} days on {series}, nearest {target:.2f} $, half the account: {life}")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
