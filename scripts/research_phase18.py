"""Phase 18 (docs/RESEARCH.md chapter 13): two published rules measured once on the desk's replay, as registered.

A research script, not a job: it reads a copy of the data branch's ``state`` folder and writes nothing there. For
each variant of chapter 13 - A: Goulding, Harvey and Mazzoleni's turning points in place of the trend sleeve; B:
Carver's vol attenuation of every sleeve; and the neighbours registered with them - it changes the sleeves of the
desk's series, replays the thirteen replayable books with the desk's own engine (real costs, next-open fills, the
allocator on) and reports the two portfolios of the decision rule (the three oil books, all thirteen) over their
whole window and in each half, at real and at double costs, beside every book alone. Nothing is chosen here: the
verdict is the rule of 13 D applied to the primary variants; the neighbours are printed beside them.

    PYTHONPATH=. python scripts/research_phase18.py --state /path/to/data-branch/state [--out results.json]
    PYTHONPATH=. python scripts/research_phase18.py --state ... --check     # the causality check alone
"""

from __future__ import annotations

import argparse
import bisect
import copy
import json
import math
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.core.config import RiskConfig
from engine.data.raw_store import RawStore
from engine.desk import report
from engine.desk import signals as sg
from engine.desk.allocator import AllocatorConfig
from engine.desk.backtest import BacktestResult, run_backtest
from engine.desk.book import load_books
from engine.desk.data import DeskData, build_desk_data

OIL_VEHICLES = ("BNO", "MCL")
OIL_BOOKS = ("prudente", "dinamico", "spinto")
WTI = "MCL"  # the desk's long WTI investable series is the MCL vehicle's return series (the skew sleeve's input)

# 13 A: the paper's main specification
GHM_SLOW = 12
GHM_FAST = 2
GHM_EVERY = 30
GHM_MIN_PER_STATE = 12
STATES = ("bull", "bear", "correction", "rebound")

# 13 B: Carver's attenuation
ATT_WINDOW = 2500
ATT_MIN_PERIODS = 10
ATT_BLEND = 0.3
ATT_SPAN = 10
ATT_EXPECTED = 1.25  # E[2 - 1.5 Q] with Q uniform on [0, 1]


# ------------------------------------------------------------------------------------- A. turning points
def monthly_returns(daily: pd.Series) -> pd.Series:
    """Calendar-month returns, last close to last close, indexed by period. The first month is dropped: the series
    starts inside it, so its 'return' covers a few days."""
    r = daily.dropna().astype("float64")
    if r.empty:
        return pd.Series(dtype="float64")
    periods = pd.DatetimeIndex(r.index).to_period("M")
    out = (1.0 + r).groupby(periods).prod() - 1.0
    return out.iloc[1:]


def _state(slow: float, fast: float) -> str:
    if slow >= 0 and fast >= 0:
        return "bull"
    if slow < 0 and fast < 0:
        return "bear"
    return "correction" if slow >= 0 else "rebound"


def ghm_mixing(states: list[str], returns: np.ndarray, min_per_state: int) -> dict[str, float] | None:
    """The positions in a correction and in a rebound from equations (8)-(10), written as positions (13 A.3):
    w_s = AVG[r|s] / (C * AVG[r^2|s]), clipped to [-1, 1]; C from the bull and bear months. None until every state
    has ``min_per_state`` months."""
    groups = {s: returns[[i for i, x in enumerate(states) if x == s]] for s in STATES}
    if any(len(groups[s]) < min_per_state for s in STATES):
        return None
    trend_months = np.concatenate([groups["bull"], groups["bear"]])
    second = float(np.mean(trend_months**2))
    f_bull = len(groups["bull"]) / len(trend_months)
    c = (f_bull * float(groups["bull"].mean()) - (1.0 - f_bull) * float(groups["bear"].mean())) / second
    out: dict[str, float] = {}
    for s in ("correction", "rebound"):
        g = groups[s]
        if not math.isfinite(c) or c <= 0:
            out[s] = 0.0
        else:
            out[s] = float(np.clip(float(g.mean()) / (c * float(np.mean(g**2))), -1.0, 1.0))
    return out


def ghm_positions(
    monthly: pd.Series,
    fast: int = GHM_FAST,
    slow: int = GHM_SLOW,
    every: int = GHM_EVERY,
    min_per_state: int = GHM_MIN_PER_STATE,
    dynamic: bool = True,
) -> tuple[pd.Series, list[dict[str, Any]]]:
    """The position of every month (known at its start) and the log of the estimates. ``dynamic`` False is the
    static 12-month sign (the paper's baseline, neighbour 13 A c)."""
    r = monthly.to_numpy(dtype="float64")
    n = len(r)
    pos = np.full(n, np.nan)
    states: list[str] = []
    for k in range(slow, n):
        states.append(_state(float(r[k - slow : k].mean()), float(r[k - fast : k].mean())))
    weights: dict[str, float] | None = None
    log: list[dict[str, Any]] = []
    for j, k in enumerate(range(slow, n)):
        if dynamic and j % every == 0:
            estimate = ghm_mixing(states[:j], r[slow:k], min_per_state)  # months strictly before k
            if estimate is not None:
                weights = estimate
                log.append({"month": str(monthly.index[k]), **{s: round(v, 3) for s, v in estimate.items()}})
        s = states[j]
        if s == "bull":
            pos[k] = 1.0
        elif s == "bear":
            pos[k] = -1.0
        elif weights is None:
            pos[k] = 1.0 if s == "correction" else -1.0
        else:
            pos[k] = weights[s]
    return pd.Series(pos, index=monthly.index), log


def monthly_to_daily(positions: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """Forecast = 10 x the position of the day's month: every decision of month m reads the position known at its
    start (computed from the months before m)."""
    mapped = positions.reindex(pd.DatetimeIndex(days).to_period("M"))
    return pd.Series(mapped.to_numpy(dtype="float64") * sg.FORECAST_NORMAL, index=days)


# ------------------------------------------------------------------------------------- B. vol attenuation
def share_below(values: pd.Series) -> pd.Series:
    """For each value, the share of the values up to it that are strictly lower (QuantConnect's expanding rank:
    0 for the lowest ever seen, (n-1)/n for the highest)."""
    v = values.dropna()
    seen: list[float] = []
    out = np.empty(len(v))
    for i, x in enumerate(v.to_numpy(dtype="float64")):
        out[i] = bisect.bisect_left(seen, x) / (i + 1)
        bisect.insort(seen, x)
    return pd.Series(out, index=v.index)


def attenuation(returns: pd.Series, normalise: bool = True) -> pd.Series:
    """M_t of 13 B: the EWMA (span 10) of 2 - 1.5 Q, Q the rank of today's relative vol in its own history;
    divided by 1.25 unless ``normalise`` is False (neighbour a)."""
    r = returns.dropna().astype("float64")
    sigma = r.ewm(span=sg.VOL_SPAN, min_periods=20).std() * math.sqrt(sg.TRADING_DAYS)
    blend = ATT_BLEND * sigma.rolling(ATT_WINDOW, min_periods=ATT_MIN_PERIODS).mean() + (1.0 - ATT_BLEND) * sigma
    relative = blend / blend.rolling(ATT_WINDOW, min_periods=ATT_MIN_PERIODS).mean()
    m = (2.0 - 1.5 * share_below(relative)).ewm(span=ATT_SPAN).mean()
    return m / ATT_EXPECTED if normalise else m


# ------------------------------------------------------------------------------------- the variants
def _with_forecasts(data: DeskData, frames: dict[str, pd.DataFrame]) -> DeskData:
    out = copy.copy(data)
    out.series = {k: (replace(v, forecasts=frames[k]) if k in frames else v) for k, v in data.series.items()}
    return out


def _recombine(frame: pd.DataFrame) -> pd.DataFrame:
    frame["combined"] = sg.combine({name: frame[name] for name in sg.SLEEVES})
    return frame


def variant_ghm(
    data: DeskData, fast: int = GHM_FAST, every: int = GHM_EVERY, dynamic: bool = True, bno_own: bool = False
) -> tuple[DeskData, dict[str, Any]]:
    frames: dict[str, pd.DataFrame] = {}
    info: dict[str, Any] = {}
    wti_positions = None
    for vid, series in data.series.items():
        source = series.returns
        if vid in OIL_VEHICLES and not (vid == "BNO" and bno_own):
            if wti_positions is None:
                wti_positions = ghm_positions(monthly_returns(data.series[WTI].returns), fast=fast, every=every,
                                              dynamic=dynamic)  # fmt: skip
            positions, log = wti_positions
            label = "WTI"
        else:
            positions, log = ghm_positions(monthly_returns(source), fast=fast, every=every, dynamic=dynamic)
            label = vid
        frame = series.forecasts.copy()
        frame["trend"] = monthly_to_daily(positions, pd.DatetimeIndex(frame.index))
        frames[vid] = _recombine(frame)
        info[vid] = {"series": label, "estimates": log[-3:], "n_estimates": len(log)}
    return _with_forecasts(data, frames), info


def variant_attenuation(
    data: DeskData, normalise: bool = True, sleeves: tuple[str, ...] = sg.SLEEVES, bno_own: bool = False
) -> tuple[DeskData, dict[str, Any]]:
    frames: dict[str, pd.DataFrame] = {}
    info: dict[str, Any] = {}
    wti_m = attenuation(data.series[WTI].returns, normalise)
    for vid, series in data.series.items():
        if vid in OIL_VEHICLES and not (vid == "BNO" and bno_own):
            m, label = wti_m, "WTI"
        else:
            m, label = attenuation(series.returns, normalise), vid
        frame = series.forecasts.copy()
        mult = m.reindex(pd.DatetimeIndex(frame.index), method="ffill", limit=3).fillna(1.0)
        for name in sleeves:
            frame[name] = (frame[name] * mult).clip(-sg.FORECAST_CAP, sg.FORECAST_CAP)
        frames[vid] = _recombine(frame)
        info[vid] = {"series": label, "m_mean": round(float(mult.mean()), 3), "m_last": round(float(mult.iloc[-1]), 3)}
    return _with_forecasts(data, frames), info


VARIANTS: dict[str, tuple[str, Callable[[DeskData], tuple[DeskData, dict[str, Any]]]]] = {
    "A": ("turning points, as registered", variant_ghm),
    "A.a": ("turning points, fast 1 month", lambda d: variant_ghm(d, fast=1)),
    "A.b": ("turning points, BNO books on the fund's series", lambda d: variant_ghm(d, bno_own=True)),
    "A.c": ("static 12-month sign (the paper's baseline)", lambda d: variant_ghm(d, dynamic=False)),
    "A.d12": ("turning points, estimated every 12 months", lambda d: variant_ghm(d, every=12)),
    "A.d60": ("turning points, estimated every 60 months", lambda d: variant_ghm(d, every=60)),
    "B": ("vol attenuation, as registered", variant_attenuation),
    "B.a": ("vol attenuation, not divided by 1.25", lambda d: variant_attenuation(d, normalise=False)),
    "B.b": ("vol attenuation, trend sleeve only", lambda d: variant_attenuation(d, sleeves=("trend",))),
    "B.c": ("vol attenuation, BNO books on the fund's vol", lambda d: variant_attenuation(d, bno_own=True)),
}
PRIMARY = ("A", "B")


# ------------------------------------------------------------------------------------- measuring
def _sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    sd = float(r.std())
    return float(r.mean()) / sd * math.sqrt(sg.TRADING_DAYS) if sd > 0 and len(r) > 60 else float("nan")


def portfolio_returns(result: BacktestResult, ids: tuple[str, ...]) -> pd.Series:
    """Equal capital in every book, rebalanced daily, over the days all of them have (as report.portfolio_stats)."""
    curves = [result.books[i].equity for i in ids if i in result.books]
    frame = pd.concat(curves, axis=1).dropna()
    return frame.pct_change().dropna().mean(axis=1)


def windows(returns: pd.Series) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    start, end = pd.Timestamp(returns.index[0]), pd.Timestamp(returns.index[-1])
    mid = start + (end - start) / 2
    return {"whole": (start, end), "first": (start, mid), "second": (mid + pd.Timedelta(days=1), end)}


def measure(result: BacktestResult, ids: tuple[str, ...], cuts: dict[str, tuple[pd.Timestamp, pd.Timestamp]]) -> dict:
    r = portfolio_returns(result, ids)
    out: dict[str, Any] = {}
    for name, (a, b) in cuts.items():
        part = r.loc[a:b]
        eq = (1.0 + part).cumprod()
        out[name] = {
            "sharpe": round(_sharpe(part), 4),
            "cagr": round(float(eq.iloc[-1]) ** (sg.TRADING_DAYS / len(part)) - 1.0, 4),
            "max_drawdown": round(float((eq / eq.cummax() - 1.0).min()), 4),
        }
    return out


def book_rows(result: BacktestResult) -> dict[str, dict[str, Any]]:
    rows = {}
    for bid, br in result.books.items():
        r = br.equity.pct_change().dropna()
        half = r.index[0] + (r.index[-1] - r.index[0]) / 2
        rows[bid] = {
            "sharpe": round(_sharpe(r), 4),
            "first": round(_sharpe(r.loc[:half]), 4),
            "second": round(_sharpe(r.loc[half:]), 4),
            "cagr": br.stats.get("cagr"),
            "max_drawdown": br.stats.get("max_drawdown"),
            "n_fills": br.n_fills,
        }
    return rows


_BASE: dict[str, Any] = {}


def _init(state: str) -> None:
    _BASE["data"] = build_desk_data(RawStore(Path(state)), None)
    _BASE["books"] = report.replayed_books(load_books())
    _BASE["risk"] = RiskConfig.load()
    _BASE["allocator"] = AllocatorConfig.load()


def _run(job: tuple[str, float]) -> tuple[str, float, BacktestResult, dict[str, Any], float]:
    name, cost = job
    t0 = time.time()
    data, info = (_BASE["data"], {}) if name == "baseline" else VARIANTS[name][1](_BASE["data"])
    result = run_backtest(data, _BASE["books"], _BASE["risk"], cost_multiplier=cost, ruin=False,
                          allocator=_BASE["allocator"])  # fmt: skip
    return name, cost, result, info, time.time() - t0


def causality_check(data: DeskData, cuts: int = 6, seed: int = 7) -> list[str]:
    """Truncating the input at T must not change any value before T (the desk's no-look-ahead rule)."""
    rng = np.random.default_rng(seed)
    problems: list[str] = []
    for vid in ("BNO", "MCL", "GLD", "SPY"):
        if vid not in data.series:
            continue
        r = data.series[vid].returns.dropna()
        full_m = attenuation(r)
        full_p, _ = ghm_positions(monthly_returns(r))
        full_f = monthly_to_daily(full_p, pd.DatetimeIndex(r.index))
        for i in rng.integers(len(r) // 3, len(r) - 5, size=cuts):
            t = r.index[int(i)]
            cut = r.loc[:t]
            m = attenuation(cut)
            p, _ = ghm_positions(monthly_returns(cut))
            f = monthly_to_daily(p, pd.DatetimeIndex(cut.index))
            before = r.index[r.index < t]
            if not np.allclose(m.reindex(before), full_m.reindex(before), equal_nan=True):
                problems.append(f"{vid} attenuation changes before {t.date()}")
            if not np.allclose(f.reindex(before), full_f.reindex(before), equal_nan=True):
                problems.append(f"{vid} turning-point forecast changes before {t.date()}")
    return problems


def verdict(base: dict[str, Any], var: dict[str, Any], base2: dict[str, Any], var2: dict[str, Any]) -> dict[str, Any]:
    """13 D: +0.05 on all thirteen in both halves; the oil three never down more than 0.05; positive at double costs."""
    gain_all = {h: var["all"][h]["sharpe"] - base["all"][h]["sharpe"] for h in ("first", "second", "whole")}
    gain_oil = {h: var["oil"][h]["sharpe"] - base["oil"][h]["sharpe"] for h in ("first", "second", "whole")}
    gain_all_2x = var2["all"]["whole"]["sharpe"] - base2["all"]["whole"]["sharpe"]
    checks = {
        "all13 +0.05 in the first half": gain_all["first"] >= 0.05,
        "all13 +0.05 in the second half": gain_all["second"] >= 0.05,
        "oil3 not down more than 0.05 in the first half": gain_oil["first"] >= -0.05,
        "oil3 not down more than 0.05 in the second half": gain_oil["second"] >= -0.05,
        "all13 gain positive at double costs": gain_all_2x > 0,
    }
    return {
        "enters": all(checks.values()),
        "checks": checks,
        "gain_all13": {k: round(v, 4) for k, v in gain_all.items()},
        "gain_oil3": {k: round(v, 4) for k, v in gain_oil.items()},
        "gain_all13_double_costs": round(gain_all_2x, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state", required=True, help="a copy of the data branch's state folder")
    ap.add_argument("--out", default=None, help="write every number to this JSON file")
    ap.add_argument("--check", action="store_true", help="run the causality check and stop")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    _init(args.state)
    data: DeskData = _BASE["data"]
    problems = causality_check(data)
    print("causality check:", problems if problems else "clean")
    if args.check or problems:
        return 0 if not problems else 1

    books = [b.id for b in _BASE["books"]]
    print(f"{len(books)} replayed books: {', '.join(books)}")
    jobs = [("baseline", 1.0), ("baseline", 2.0)] + [(p, 2.0) for p in PRIMARY] + [(v, 1.0) for v in VARIANTS]
    results: dict[tuple[str, float], tuple[BacktestResult, dict[str, Any]]] = {}
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init, initargs=(args.state,)) as pool:
        for name, cost, result, info, seconds in pool.map(_run, jobs):
            results[name, cost] = (result, info)
            print(f"  {name:9s} costs x{cost:g}: {seconds:5.0f} s")

    base = results["baseline", 1.0][0]
    cuts = {
        "oil": windows(portfolio_returns(base, OIL_BOOKS)),
        "all": windows(portfolio_returns(base, tuple(books))),
    }

    def portfolios(result: BacktestResult) -> dict[str, Any]:
        return {"oil": measure(result, OIL_BOOKS, cuts["oil"]), "all": measure(result, tuple(books), cuts["all"])}

    spans = {k: {h: [a.date().isoformat(), b.date().isoformat()] for h, (a, b) in v.items()} for k, v in cuts.items()}
    out: dict[str, Any] = {
        "registered": "docs/RESEARCH.md chapter 13 (commit 231d504d, pushed before this script ran)",
        "windows": spans,
        "runs": {},
    }
    for (name, cost), (result, info) in results.items():
        out["runs"][f"{name}@{cost:g}"] = {
            "label": "the desk as it is" if name == "baseline" else VARIANTS[name][0],
            "portfolios": portfolios(result),
            "books": book_rows(result),
            "info": info,
        }
    b1, b2 = out["runs"]["baseline@1"]["portfolios"], out["runs"]["baseline@2"]["portfolios"]
    out["verdicts"] = {
        p: verdict(b1, out["runs"][f"{p}@1"]["portfolios"], b2, out["runs"][f"{p}@2"]["portfolios"]) for p in PRIMARY
    }
    if all(v["enters"] for v in out["verdicts"].values()):
        out["note"] = "both enter: the pair must now be measured together (13 D) - not done by this run"

    print()
    print(f"{'run':10s} {'oil3 whole':>10s} {'1st':>6s} {'2nd':>6s} {'all13 whole':>11s} {'1st':>6s} {'2nd':>6s} "
          f"{'dinamico':>8s}")  # fmt: skip
    for key, run in out["runs"].items():
        o, a = run["portfolios"]["oil"], run["portfolios"]["all"]
        d = run["books"].get("dinamico", {})
        print(f"{key:10s} {o['whole']['sharpe']:10.3f} {o['first']['sharpe']:6.3f} {o['second']['sharpe']:6.3f} "
              f"{a['whole']['sharpe']:11.3f} {a['first']['sharpe']:6.3f} {a['second']['sharpe']:6.3f} "
              f"{d.get('sharpe', float('nan')):8.3f}")  # fmt: skip
    for p, v in out["verdicts"].items():
        print(f"\n{p}: {'ENTERS' if v['enters'] else 'does not enter'}")
        for check, ok in v["checks"].items():
            print(f"   {'yes' if ok else 'no ':3s} {check}")
        print(f"   gain all13 {v['gain_all13']}, oil3 {v['gain_oil3']}, all13 at double costs "
              f"{v['gain_all13_double_costs']}")  # fmt: skip
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        print(f"\nwritten {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
