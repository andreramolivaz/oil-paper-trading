"""The weekend measurement of docs/RESEARCH.md chapter 17 E, as registered.

A research script, not a job: it reads a download of Kalshi's HOURLY candlesticks (``research_fetch.py --only
kalshi-hourly``) and the Brent fund's daily bars from a copy of the data branch, and writes nothing there.

From Friday 17:00 New York to Sunday 18:00 crude futures do not trade and Kalshi's markets do. For every weekend
the de-escalation index is read at Friday 17:00 and at Sunday 17:00 exactly as the Oracle reads it (three deadline
ladders, a 90-day horizon, markets quoted two-sided within ten cents), on the last hourly candle of each market at
or before the instant and no older than 48 hours; ``W`` is the change over the components both instants have. The
Brent fund gives the gap ``G`` (Monday's open over Friday's close) and the continuation ``C`` (Monday's close over
its open). H1: the slope of G on W is negative. H2: W against C.

    PYTHONPATH=. python scripts/research_weekend.py --hourly out/research/kalshi/history_hourly.json \\
        --state /path/to/data-branch/state [--until 2026-11-08] [--out weekend.json]

The numbers it printed on 15 November 2026 are in chapter 17 M.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy import stats

from engine.desk import oracle
from engine.desk.prediction import LOG_COLUMNS

NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
MARK_TIME = time(17, 0)  # Friday and Sunday, New York
MONDAY_TIME = time(9, 0)  # the neighbour: up to Monday morning
MAX_QUOTE_AGE = timedelta(hours=48)
MIN_COMPONENTS = 2
PAGE_MIN_T = 2.0  # 17 E 7: what the page may say
PAGE_MIN_N = 25


@dataclass
class Market:
    series: str
    event: str
    ticker: str
    title: str
    label: str
    opened: pd.Timestamp | None
    closed: pd.Timestamp | None
    ends: list[int]  # the candles' end times (Unix), sorted
    bids: list[float | None]
    asks: list[float | None]


def _when(value: Any) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(str(value))
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def load_markets(history: dict[str, Any]) -> list[Market]:
    out: list[Market] = []
    for series, block in (history.get("series") or {}).items():
        by_ticker = {str(m.get("ticker")): m for m in block.get("markets") or []}
        for ticker, candles in (block.get("candles") or {}).items():
            m = by_ticker.get(str(ticker))
            if m is None:
                continue
            rows = sorted((c for c in candles if c.get("end_ts")), key=lambda c: int(c["end_ts"]))
            out.append(
                Market(
                    series=str(series),
                    event=str(m.get("event_ticker") or ticker),
                    ticker=str(ticker),
                    title=str(m.get("title") or "")[:160],
                    label=str(m.get("yes_sub_title") or ""),
                    opened=_when(m.get("open_time")),
                    closed=_when(m.get("close_time")),
                    ends=[int(c["end_ts"]) for c in rows],
                    bids=[_num(c.get("yes_bid_close")) for c in rows],
                    asks=[_num(c.get("yes_ask_close")) for c in rows],
                )
            )
    return out


def records_at(markets: list[Market], t: pd.Timestamp) -> list[dict[str, Any]]:
    """The archive's records (one per event) as a reading taken at ``t`` would have written them: each market at
    the close of its last hourly candle at or before ``t``, if that candle is not older than 48 hours."""
    stamp = int(t.timestamp())
    oldest = stamp - int(MAX_QUOTE_AGE.total_seconds())
    by_event: dict[tuple[str, str], dict[str, Any]] = {}
    for m in markets:
        if (m.opened is not None and t < m.opened) or (m.closed is not None and t > m.closed):
            continue
        i = bisect.bisect_right(m.ends, stamp) - 1
        if i < 0 or m.ends[i] < oldest:
            continue
        rec = by_event.setdefault(
            (m.series, m.event),
            {
                "day": t.tz_convert("UTC").strftime("%Y-%m-%d"),
                "ts": t.isoformat(),
                "series": m.series,
                "event": m.event,
                "title": m.title,
                "columns": list(LOG_COLUMNS),
                "markets": [],
            },
        )
        row: dict[str, Any] = dict.fromkeys(LOG_COLUMNS)
        row.update({"ticker": m.ticker, "label": m.label, "yes_bid": m.bids[i], "yes_ask": m.asks[i], "volume": 0.0})
        rec["markets"].append([row[c] for c in LOG_COLUMNS])
    return list(by_event.values())


def index_at(markets: list[Market], t: pd.Timestamp) -> oracle.IndexReading | None:
    readings = oracle.index_readings(records_at(markets, t))
    return readings[-1] if readings else None


def weekend_change(markets: list[Market], t0: pd.Timestamp, t1: pd.Timestamp) -> tuple[float, int] | None:
    """(the index's change from ``t0`` to ``t1`` over the components both have, how many those are)."""
    a, b = index_at(markets, t0), index_at(markets, t1)
    if a is None or b is None:
        return None
    now, before = oracle.common_values(b, a, b.day, oracle.DEESCALATION)
    if len(now) < MIN_COMPONENTS:
        return None
    change = oracle.DEESCALATION.combine(now.values()), oracle.DEESCALATION.combine(before.values())
    if change[0] is None or change[1] is None:
        return None
    return float(change[0] - change[1]), len(now)


def at_ny(day: date, clock: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, clock, tzinfo=NEW_YORK)).tz_convert("UTC")


def ols(x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """y on x with an intercept; the slope's t with heteroskedasticity-robust (HC1) errors."""
    n = len(x)
    if n < 4 or float(np.std(x)) == 0.0:
        return {"n": n, "slope": float("nan"), "t": float("nan"), "r2": float("nan"), "resid_sd": float("nan")}
    design = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ beta
    bread = np.linalg.inv(design.T @ design)
    meat = design.T @ (design * (resid**2)[:, None])
    cov = bread @ meat @ bread * n / (n - 2)
    total = float(np.sum((y - y.mean()) ** 2))
    return {
        "n": n,
        "intercept": float(beta[0]),
        "slope": float(beta[1]),
        "t": float(beta[1] / math.sqrt(cov[1, 1])) if cov[1, 1] > 0 else float("nan"),
        "r2": 1.0 - float(np.sum(resid**2)) / total if total > 0 else float("nan"),
        "resid_sd": float(np.std(resid, ddof=2)),
    }


def _line(label: str, x: pd.Series, y: pd.Series) -> dict[str, Any]:
    frame = pd.concat([x, y], axis=1).dropna()
    xs, ys = frame.iloc[:, 0].to_numpy(dtype="float64"), frame.iloc[:, 1].to_numpy(dtype="float64")
    fit = ols(xs * 100.0, ys * 100.0)  # W in points, the returns in per cent
    rho = stats.spearmanr(xs, ys) if len(xs) >= 4 else None
    fit["spearman"] = float(rho.statistic) if rho is not None else float("nan")
    fit["spearman_p"] = float(rho.pvalue) if rho is not None else float("nan")
    print(
        f"{label:<58} n {fit['n']:>2}  slope {fit['slope']:+.3f} %/pt  t {fit['t']:+.2f}  R2 {fit['r2']:.3f}  "
        f"rank corr {fit['spearman']:+.2f} (p {fit['spearman_p']:.2f})  resid sd {fit['resid_sd']:.2f} %"
    )
    return fit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hourly", required=True, help="a kalshi-hourly download (history_hourly.json)")
    parser.add_argument("--state", required=True, help="a copy of the data branch's state/ (the raw store)")
    parser.add_argument("--until", default="2026-11-08", help="the Sunday of the last weekend measured")
    parser.add_argument("--out", default=None, help="write the rows and the fits as JSON")
    args = parser.parse_args()

    markets = load_markets(json.loads(Path(args.hourly).read_text(encoding="utf-8")))
    bno = pd.read_parquet(Path(args.state) / "raw" / "bno_daily" / "yahoo" / "latest.parquet")
    bno.index = pd.DatetimeIndex(pd.to_datetime(bno.index)).tz_localize(None).normalize()
    bno = bno.sort_index()
    sessions = [d.date() for d in bno.index]
    until = date.fromisoformat(args.until)

    rows: list[dict[str, Any]] = []
    friday = date(2026, 3, 6)
    while friday + timedelta(days=2) <= until:
        sunday = friday + timedelta(days=2)
        before = [d for d in sessions if d <= friday]
        after = [d for d in sessions if d > sunday]
        w = weekend_change(markets, at_ny(friday, MARK_TIME), at_ny(sunday, MARK_TIME))
        w_monday = weekend_change(markets, at_ny(friday, MARK_TIME), at_ny(sunday + timedelta(days=1), MONDAY_TIME))
        if w is not None and before and after:
            last, first = pd.Timestamp(before[-1]), pd.Timestamp(after[0])
            close_before = float(bno.loc[last, "close"])
            open_after, close_after = float(bno.loc[first, "open"]), float(bno.loc[first, "close"])
            rows.append(
                {
                    "friday": friday.isoformat(),
                    "session_before": before[-1].isoformat(),
                    "session_after": after[0].isoformat(),
                    "w": w[0],
                    "components": w[1],
                    "w_to_monday": None if w_monday is None else w_monday[0],
                    "gap": open_after / close_before - 1.0,
                    "continuation": close_after / open_after - 1.0,
                    "close_to_close": close_after / close_before - 1.0,
                }
            )
        friday += timedelta(days=7)

    df = pd.DataFrame(rows)
    if df.empty:
        print("no weekend with two components at both instants")
        return 1
    print(f"weekends {len(df)}, {df['friday'].iloc[0]} to {df['friday'].iloc[-1]}; "
          f"mean |W| {df['w'].abs().mean() * 100:.2f} points (median {df['w'].abs().median() * 100:.2f}, "
          f"max {df['w'].abs().max() * 100:.1f}); |W| >= 5 points on {int((df['w'].abs() >= 0.05).sum())}; "
          f"mean |gap| {df['gap'].abs().mean():.2%} (max {df['gap'].abs().max():.1%})")  # fmt: skip
    print()
    for _, r in df.iterrows():
        print(f"  {r['friday']}  W {r['w'] * 100:+6.2f} pt ({int(r['components'])} comp.)  gap {r['gap']:+7.2%}  "
              f"Monday open->close {r['continuation']:+7.2%}")  # fmt: skip
    print()
    out: dict[str, Any] = {"rows": rows}
    out["h1"] = _line("H1  gap on W (Fri 17:00 -> Sun 17:00 New York)", df["w"], df["gap"])
    out["h2"] = _line("H2  Monday open->close on W", df["w"], df["continuation"])
    half = len(df) // 2
    out["h1_first"] = _line("    H1, first half", df["w"].iloc[:half], df["gap"].iloc[:half])
    out["h1_second"] = _line("    H1, second half", df["w"].iloc[half:], df["gap"].iloc[half:])
    out["h2_first"] = _line("    H2, first half", df["w"].iloc[:half], df["continuation"].iloc[:half])
    out["h2_second"] = _line("    H2, second half", df["w"].iloc[half:], df["continuation"].iloc[half:])
    print("neighbours (they do not decide):")
    out["monday"] = _line("    gap on W up to Monday 09:00 (crude trades overnight)", df["w_to_monday"], df["gap"])
    out["placebo"] = _line("    gap on the PREVIOUS weekend's W (placebo)", df["w"].shift(1), df["gap"])
    out["close"] = _line("    Friday close -> Monday close on W", df["w"], df["close_to_close"])
    h1 = out["h1"]
    shows = abs(h1["t"]) >= PAGE_MIN_T and h1["n"] >= PAGE_MIN_N
    print()
    says = "SHOWS the implied gap" if shows else "shows the move alone, and says it has told nothing reliable"
    print(f"PAGE RULE (17 E 7): |t| {abs(h1['t']):.2f} on {h1['n']} weekends -> the page {says}")
    out["page_shows_gap"] = bool(shows)
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
