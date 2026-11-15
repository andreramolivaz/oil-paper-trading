"""Oil as a signal for equities and Treasuries (docs/RESEARCH.md chapter 17 F), measured once as registered.

A research script, not a job: it reads a copy of the data branch's ``state`` folder and writes nothing there.

Two published ideas, each written down with its filter before a number was computed:

* Driesprong, Jacobsen and Maat (JFE 2008): a rise in the oil price is followed by lower stock returns. The
  sleeve ``oil_lag``: minus the 21-session log return of the desk's WTI index in units of its own volatility,
  times 12.5, capped at +-20, read at the PREVIOUS close. Same sign for the Treasury book.
* Hamilton (2003): what matters is the NET increase, the price above its maximum of the three years before. The
  rule: after a day with a net increase, the equity book's forecast is halved for 252 sessions.

The bench is the desk's own replay (real costs, next-open fills, the allocator on) on the candidate books
``equities`` (SPY) and ``bonds`` (TLT) as they are. The oil sleeve is put in the macro source's slot those books
do not read, so that the combination is the desk's own rule by source.

    PYTHONPATH=. python scripts/research_oil_lag.py --state /path/to/data-branch/state [--out oil_lag.json]

The numbers it printed on 15 November 2026 are in chapter 17 M.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.core.config import RiskConfig
from engine.data.raw_store import RawStore
from engine.desk import signals as sg
from engine.desk.allocator import AllocatorConfig
from engine.desk.backtest import run_backtest
from engine.desk.book import BookConfig, load_books
from engine.desk.data import DeskData, build_desk_data

OIL_WINDOW = 21  # sessions: a month, the paper's horizon
OIL_SCALAR = 12.5  # 10 / 0.8, the mean absolute value of a standard normal: Carver's normalisation, a priori
SLOT = "copper"  # a sleeve of the macro source the satellite books do not read: the oil sleeve sits there
WTI = "MCL"  # the desk's long WTI investable series
BOOKS = {"equities": "SPY", "bonds": "TLT"}
PUBLISHED = pd.Timestamp("2009-01-01")  # after the paper (JFE, August 2008)
NOPI_WINDOW = 756  # three years of sessions
NOPI_HOLD = 252
NOPI_SCALE = 0.5
PRICE_SLEEVES = ("trend", "accel", "skew")


def oil_sleeve(returns: pd.Series) -> pd.Series:
    """The ``oil_lag`` forecast on the WTI calendar: its value on day t uses returns up to t."""
    r = returns.astype("float64")
    log_r = np.log1p(r.fillna(0.0))
    month = log_r.rolling(OIL_WINDOW, min_periods=OIL_WINDOW).sum()
    daily_vol = sg.ew_vol(r) / math.sqrt(sg.TRADING_DAYS)
    z = month / (daily_vol * math.sqrt(OIL_WINDOW))
    return (-OIL_SCALAR * z).clip(-sg.FORECAST_CAP, sg.FORECAST_CAP)


def read_before(series: pd.Series, index: pd.DatetimeIndex, lag: int = 1) -> pd.Series:
    """``series`` as a decision on each day of ``index`` may read it: its value ``lag`` sessions before (1: the
    previous close), carried over at most five days of the other calendar."""
    return series.shift(lag).reindex(index, method="ffill", limit=5)


def nopi_active(price: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """True on the days the Hamilton rule is on: within ``NOPI_HOLD`` sessions after a day whose price was above
    the maximum of the ``NOPI_WINDOW`` sessions before it. Read at the previous close."""
    p = price.dropna().astype("float64")
    prior_max = p.shift(1).rolling(NOPI_WINDOW, min_periods=NOPI_WINDOW).max()
    trigger = (p > prior_max).astype("float64")
    active = trigger.rolling(NOPI_HOLD, min_periods=1).max() > 0
    return read_before(active.astype("float64"), index).fillna(0.0) > 0


def with_frames(data: DeskData, frames: dict[str, pd.DataFrame]) -> DeskData:
    out = copy.copy(data)
    out.series = {k: (replace(v, forecasts=frames[k]) if k in frames else v) for k, v in data.series.items()}
    return out


def sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    sd = float(r.std())
    return float(r.mean()) / sd * math.sqrt(sg.TRADING_DAYS) if sd > 0 and len(r) > 60 else float("nan")


def rows_of(equity: pd.Series) -> dict[str, Any]:
    r = equity.pct_change().dropna()
    half = r.index[0] + (r.index[-1] - r.index[0]) / 2
    years = len(r) / sg.TRADING_DAYS
    whole = sharpe(r)
    eq = (1.0 + r).cumprod()
    return {
        "from": str(r.index[0].date()),
        "to": str(r.index[-1].date()),
        "years": round(years, 1),
        "sharpe": round(whole, 3),
        "se": round(math.sqrt((1.0 + whole * whole / 2.0) / years), 3) if years > 0 and math.isfinite(whole) else None,
        "first": round(sharpe(r.loc[:half]), 3),
        "second": round(sharpe(r.loc[half:]), 3),
        "since_2009": round(sharpe(r.loc[PUBLISHED:]), 3),
        "cagr": round(float(eq.iloc[-1]) ** (1.0 / years) - 1.0, 4) if years > 0 else None,
        "max_drawdown": round(float((eq / eq.cummax() - 1.0).min()), 4),
        "invested": round(float((equity.diff().abs() > 0).mean()), 3),
    }


def run(data: DeskData, configs: list[BookConfig], risk: RiskConfig, allocator: AllocatorConfig) -> dict[str, Any]:
    result = run_backtest(data, configs, risk, ruin=False, allocator=allocator)
    return {bid: rows_of(br.equity) | {"fills": br.n_fills} for bid, br in result.books.items()}


def _print(label: str, rows: dict[str, Any]) -> None:
    for bid, r in rows.items():
        print(
            f"{label:<44} {bid:<9} Sharpe {r['sharpe']:+.2f} (SE {r['se']})  halves {r['first']:+.2f} / "
            f"{r['second']:+.2f}  since 2009 {r['since_2009']:+.2f}  CAGR {r['cagr']:+.1%}  maxDD "
            f"{r['max_drawdown']:.0%}  fills {r['fills']}  [{r['from']} to {r['to']}]"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, help="a copy of the data branch's state/")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    state = Path(args.state)
    data = build_desk_data(RawStore(state), None)
    risk, allocator = RiskConfig.load(), AllocatorConfig.load()
    configs = {c.id: c for c in load_books() if c.id in BOOKS}
    base = [configs[b] for b in BOOKS]
    wti_returns = data.series[WTI].returns
    sleeve = oil_sleeve(wti_returns)
    print(f"oil sleeve: {sleeve.dropna().index[0].date()} to {sleeve.dropna().index[-1].date()}, mean |F| "
          f"{sleeve.abs().mean():.2f} (10 is the normalisation), "
          f"|F| = 20 on {(sleeve.abs() >= 20).mean():.1%} of days")  # fmt: skip
    for bid, vid in BOOKS.items():
        own = data.series[vid].forecasts["combined"]
        aligned = read_before(sleeve, pd.DatetimeIndex(own.index))
        print(f"  {bid}: correlation of the oil sleeve with the book's own forecast {aligned.corr(own):+.2f}")

    out: dict[str, Any] = {}

    def frames(lag: int) -> dict[str, pd.DataFrame]:
        made = {}
        for vid in BOOKS.values():
            frame = data.series[vid].forecasts.copy()
            frame[SLOT] = read_before(sleeve, pd.DatetimeIndex(frame.index), lag)
            made[vid] = frame
        return made

    out["i_book"] = run(data, base, risk, allocator)
    _print("(i) the book as it is (trend, accel, skew)", out["i_book"])
    alone = [replace(c, sleeves={SLOT: 1.0}) for c in base]
    out["ii_oil_alone"] = run(with_frames(data, frames(1)), alone, risk, allocator)
    _print("(ii) the oil sleeve alone", out["ii_oil_alone"])
    both = [replace(c, sleeves={**{n: 0.5 / len(PRICE_SLEEVES) for n in PRICE_SLEEVES}, SLOT: 0.5}) for c in base]
    out["iii_both"] = run(with_frames(data, frames(1)), both, risk, allocator)
    _print("(iii) price and oil, half each", out["iii_both"])
    out["ii_lagged"] = run(with_frames(data, frames(2)), alone, risk, allocator)
    _print("(3) the oil sleeve alone, one session late", out["ii_lagged"])

    # Hamilton's rule on the equity book: the price is the spot price (as in the paper), the WTI daily of the EIA
    spot = pd.read_parquet(state / "raw" / "wti_spot" / "eia" / "latest.parquet")["value"]
    spot.index = pd.DatetimeIndex(pd.to_datetime(spot.index)).tz_localize(None).normalize()

    def hamilton(price: pd.Series, label: str, key: str) -> None:
        vid = BOOKS["equities"]
        frame = data.series[vid].forecasts.copy()
        on = nopi_active(price, pd.DatetimeIndex(frame.index))
        for name in (*sg.SLEEVES, "combined"):
            if name in frame.columns:
                frame.loc[on, name] = frame.loc[on, name] * NOPI_SCALE
        rows = run(with_frames(data, {vid: frame}), [configs["equities"]], risk, allocator)
        rows["equities"]["share_on"] = round(float(on.mean()), 3)
        rows["equities"]["on_now"] = bool(on.iloc[-1])
        out[key] = rows
        _print(label, rows)
        print(f"    on {on.mean():.0%} of the sessions; on today: {bool(on.iloc[-1])}")

    hamilton(spot, "(H) Hamilton's rule, spot price", "hamilton_spot")
    hamilton(sg.total_return_index(wti_returns), "(H') neighbour: the investable index", "hamilton_index")

    print()
    for bid in BOOKS:
        i, ii, iii, lag = (out[k][bid] for k in ("i_book", "ii_oil_alone", "iii_both", "ii_lagged"))
        c1 = ii["sharpe"] > 0 and ii["first"] > 0 and ii["second"] > 0 and ii["since_2009"] > 0
        c2 = iii["first"] > i["first"] and iii["second"] > i["second"]
        c3 = (lag["sharpe"] > 0) == (ii["sharpe"] > 0)
        print(f"FILTER {bid}: (1) the sleeve alone positive everywhere: {c1}; (2) the combination beats the book in "
              f"both halves: {c2}; (3) one session late keeps its sign: {c3} -> "
              f"{'ENTERS' if c1 and c2 and c3 else 'REJECTED'}")  # fmt: skip
        out[f"verdict_{bid}"] = {"c1": c1, "c2": c2, "c3": c3, "enters": bool(c1 and c2 and c3)}
    i, h = out["i_book"]["equities"], out["hamilton_spot"]["equities"]
    passes = h["first"] > i["first"] and h["second"] > i["second"]
    print(f"FILTER Hamilton: raises the Sharpe in both halves: {passes} ({i['first']:+.2f} -> {h['first']:+.2f}, "
          f"{i['second']:+.2f} -> {h['second']:+.2f}) -> {'ON' if passes else 'OFF'}")  # fmt: skip
    out["verdict_hamilton"] = {"passes": bool(passes)}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
