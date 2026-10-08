"""Replay the desk over history with the same ``Desk`` the live tick drives, and measure what came out.

One daily bar per day. An order decided at the close of day t fills at the OPEN of day t+1 (the broker's rule:
the first price strictly after the decision), with the vehicle's real commission and a half-spread that widens
with volatility. Interest is charged on a fund held above 1x. A futures position rolls five business days
before the last trading day and pays the roll cost. An account that falls to 5 % of its starting capital is
dead and stays dead: the replay does not quietly revive it.

What is reported, per book and for the sum of the books:

* the equity curve and the usual numbers on it, with the t-statistic next to the Sharpe ratio because a Sharpe
  of 0.5 over sixteen years is two standard errors from zero, not a certainty;
* each calendar year, so three bad years in a row are visible instead of being averaged away;
* the leverage actually used (median, 95th percentile, maximum, share of days above 1x);
* what the costs took;
* a stationary block bootstrap of the book's own daily returns: the probability of losing half the account
  and of reaching the 5 % floor within one year.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from engine.core.calendar import add_business_days
from engine.core.config import RiskConfig
from engine.core.events import AccountStatus, Bar
from engine.desk.book import Book, BookConfig
from engine.desk.data import DeskData, VehicleSeries, held_contract
from engine.desk.engine import Desk
from engine.desk.vehicles import get_vehicle

log = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
TRADING_DAYS = 252
CLOSE_NY = {"us_regular": time(16, 0), "globex": time(14, 30)}
WARMUP_DAYS = 300  # the slowest trend filter needs 256 days before it says anything


def close_ts(day: date, session: str) -> datetime:
    """UTC instant of the daily close (fund) or settlement (futures) of a trading date."""
    return datetime.combine(day, CLOSE_NY.get(session, time(16, 0)), tzinfo=NEW_YORK).astimezone(UTC)


@dataclass
class BookResult:
    book: str
    vehicle: str
    equity: pd.Series
    leverage: pd.Series
    exposure: pd.Series  # signed, multiple of equity
    stats: dict[str, Any] = field(default_factory=dict)
    yearly: dict[int, float] = field(default_factory=dict)
    costs: dict[str, float] = field(default_factory=dict)
    ruin: dict[str, Any] = field(default_factory=dict)
    died: str | None = None
    n_fills: int = 0

    def to_dict(self, curve_points: int = 400) -> dict[str, Any]:
        return {
            "book": self.book,
            "vehicle": self.vehicle,
            "start": None if self.equity.empty else self.equity.index[0].date().isoformat(),
            "end": None if self.equity.empty else self.equity.index[-1].date().isoformat(),
            "stats": self.stats,
            "yearly": {str(k): v for k, v in self.yearly.items()},
            "costs": self.costs,
            "ruin": self.ruin,
            "died": self.died,
            "n_fills": self.n_fills,
            "curve": downsample(self.equity, curve_points),
        }


@dataclass
class BacktestResult:
    books: dict[str, BookResult]
    benchmarks: dict[str, dict[str, Any]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "books": {k: v.to_dict() for k, v in self.books.items()},
            "benchmarks": self.benchmarks,
        }


# ----------------------------------------------------------------------------------------------- metrics
def downsample(series: pd.Series, points: int) -> list[dict[str, Any]]:
    if series.empty:
        return []
    step = max(1, len(series) // points)
    positions = list(range(0, len(series), step))
    if positions[-1] != len(series) - 1:
        positions.append(len(series) - 1)
    stamps = pd.DatetimeIndex(series.index)
    values = series.to_numpy(dtype="float64")
    return [{"t": stamps[i].date().isoformat(), "v": round(float(values[i]), 2)} for i in positions]


def performance(equity: pd.Series) -> dict[str, Any]:
    """Annualised numbers of a daily equity curve. Returns {} when there is too little to say anything."""
    eq = equity.dropna()
    if len(eq) < 60:
        return {}
    ret = eq.pct_change().dropna()
    years = len(ret) / TRADING_DAYS
    if years <= 0:
        return {}
    mean, std = float(ret.mean()), float(ret.std())
    vol = std * math.sqrt(TRADING_DAYS)
    sharpe = mean * TRADING_DAYS / vol if vol > 0 else float("nan")
    final = float(eq.iloc[-1] / eq.iloc[0])
    cagr = final ** (1.0 / years) - 1.0 if final > 0 else -1.0
    peak = eq.cummax()
    dd = eq / peak - 1.0
    under = (dd < 0).astype(int)
    longest = int((under.groupby((under == 0).cumsum()).cumsum()).max()) if len(under) else 0
    weekly = eq.resample("W-FRI").last().pct_change().dropna()
    return {
        "years": round(years, 1),
        "cagr": round(cagr, 4),
        "vol": round(vol, 4),
        "sharpe": None if not math.isfinite(sharpe) else round(sharpe, 2),
        # standard error of a Sharpe ratio is about 1/sqrt(years): t = Sharpe * sqrt(years)
        "t_stat": None if not math.isfinite(sharpe) else round(sharpe * math.sqrt(years), 2),
        "max_drawdown": round(float(dd.min()), 4),
        "longest_drawdown_days": longest,
        "worst_day": round(float(ret.min()), 4),
        "worst_week": None if weekly.empty else round(float(weekly.min()), 4),
        "best_day": round(float(ret.max()), 4),
        "hit_rate": round(float((ret > 0).mean()), 3),
        "final_multiple": round(final, 3),
    }


def yearly_returns(equity: pd.Series) -> dict[int, float]:
    eq = equity.dropna()
    if eq.empty:
        return {}
    years = pd.DatetimeIndex(eq.index).year
    last = eq.groupby(years).last()
    first = eq.groupby(years).first()
    prev = last.shift(1)
    base = prev.where(prev.notna(), first)
    ratio = last / base - 1.0
    return {int(y): round(float(v), 4) for y, v in zip(ratio.index.tolist(), ratio.tolist(), strict=True)}


def ruin_probabilities(
    returns: pd.Series,
    horizon: int = TRADING_DAYS,
    n_paths: int = 4000,
    block: int = 10,
    seed: int = 20261008,
    floor: float = 0.05,
) -> dict[str, Any]:
    """Stationary block bootstrap (Politis-Romano) of the book's own daily returns over one year.

    The paths keep the clustering of bad days (mean block of ``block`` days). They cannot contain a day worse
    than the worst one in the sample, which for a levered book is the caveat that matters: the number is a
    floor on the risk, not a ceiling.
    """
    r = returns.dropna().to_numpy()
    if len(r) < 250:
        return {}
    rng = np.random.default_rng(seed)
    n = len(r)
    starts = rng.integers(0, n, size=(n_paths, horizon))
    restart = rng.random((n_paths, horizon)) < 1.0 / block
    restart[:, 0] = True
    idx = np.empty((n_paths, horizon), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for j in range(1, horizon):
        idx[:, j] = np.where(restart[:, j], starts[:, j], (idx[:, j - 1] + 1) % n)
    paths = np.cumprod(1.0 + np.clip(r[idx], -0.999, None), axis=1)
    running_max = np.maximum.accumulate(np.maximum(paths, 1.0), axis=1)
    drawdown = (paths / running_max - 1.0).min(axis=1)
    final = paths[:, -1]
    return {
        "horizon_days": horizon,
        "paths": n_paths,
        "p_lose_half": round(float((drawdown <= -0.5).mean()), 4),
        "p_lose_quarter": round(float((drawdown <= -0.25).mean()), 4),
        "p_dead": round(float((paths.min(axis=1) <= floor).mean()), 4),
        "median_final": round(float(np.median(final)), 3),
        "p5_final": round(float(np.percentile(final, 5)), 3),
        "p95_final": round(float(np.percentile(final, 95)), 3),
        "method": "bootstrap a blocchi stazionari sui rendimenti giornalieri del libro",
    }


# ----------------------------------------------------------------------------------------------- the replay
def _next_symbol(series: VehicleSeries, i: int, vehicle_kind: str, root: str) -> str:
    """Symbol the book must hold for the NEXT bar (the roll is decided the evening before)."""
    if vehicle_kind != "future":
        return str(series.bars["symbol"].iloc[i])
    if i + 1 < len(series.bars):
        return str(series.bars["symbol"].iloc[i + 1])
    day = pd.Timestamp(series.bars.index[i]).date()
    return held_contract(root, add_business_days(day, 1, "US"))


FILL_NEXT_OPEN = "next_open"
FILL_SAME_CLOSE = "same_close"


def run_vehicle(
    desk: Desk,
    series: VehicleSeries,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    root: str = "CL",
    fill: str = FILL_NEXT_OPEN,
) -> dict[str, dict[str, list[Any]]]:
    """Drive every book on one vehicle through its daily bars. Returns per-book daily records.

    ``fill`` brackets the one thing a daily replay cannot know, which is how long after the decision the order
    fills. Live, a book decides half an hour before the fund's close (or just after the futures settlement)
    and fills on the next half-hour bar. ``next_open`` fills at the NEXT session's open - for the fund that is
    seventeen hours later, across the overnight gap - and is the headline because it can only understate.
    ``same_close`` fills at the close the decision was taken on, with the same costs: it is the optimistic end
    of the bracket and is reported as a sensitivity, never as the result.
    """
    vehicle = get_vehicle(series.vehicle)
    books = desk.books_on(series.vehicle)
    records: dict[str, dict[str, list[Any]]] = {b.id: {"t": [], "equity": [], "lev": [], "expo": []} for b in books}
    bars = series.bars
    first_usable = max(WARMUP_DAYS, 0)
    for i in range(len(bars)):
        ts_day = pd.Timestamp(bars.index[i])
        if i < first_usable or (start is not None and ts_day < start):
            continue
        if end is not None and ts_day > end:
            break
        row = bars.iloc[i]
        day = ts_day.date()
        ts = close_ts(day, vehicle.session)
        close = float(row["close"])
        if not math.isfinite(close) or close <= 0:
            continue
        symbol = str(row["symbol"])
        vol = series.vol.get(ts_day)
        vol_f = float(vol) if vol is not None and math.isfinite(float(vol)) else None
        bar = Bar(
            symbol=symbol,
            ts=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=close,
            interval="1d",
            source="backtest",
            is_settlement=True,
        )
        desk.on_bar(series.vehicle, bar, vol_annual=vol_f)
        desk.accrue_financing(series.vehicle, day, ts)
        for book in books:
            snap = book.broker.snapshot(ts)
            rec = records[book.id]
            rec["t"].append(ts_day)
            rec["equity"].append(float(snap.equity))
            rec["lev"].append(float(snap.leverage))
            rec["expo"].append(float(snap.net_notional) / float(snap.equity) if snap.equity > 0 else 0.0)
        nxt = _next_symbol(series, i, vehicle.kind, root)
        if nxt != symbol:
            desk.roll(series.vehicle, nxt, {symbol: close, nxt: close}, ts)
        frow = series.forecasts.loc[ts_day]
        forecast = {k: (None if pd.isna(frow[k]) else float(frow[k])) for k in frow.index}
        if fill == FILL_SAME_CLOSE:
            # decide a second before the close and execute on a one-minute bar that opens AT the close: the
            # broker's "first price strictly after the decision" rule is kept, the price is the close itself
            desk.decide(series.vehicle, ts - timedelta(seconds=1), day, nxt, close, forecast, vol_f)
            execution = Bar(
                symbol=nxt, ts=ts + timedelta(minutes=1), open=close, high=close, low=close, close=close,
                interval="1m", source="backtest (stessa chiusura)",
            )  # fmt: skip
            desk.on_bar(series.vehicle, execution, vol_annual=vol_f)
        else:
            desk.decide(series.vehicle, ts, day, nxt, close, forecast, vol_f)
    return records


def run_backtest(
    data: DeskData,
    configs: list[BookConfig],
    base_risk: RiskConfig,
    start: date | None = None,
    end: date | None = None,
    cost_multiplier: float = 1.0,
    ruin: bool = True,
    fill: str = FILL_NEXT_OPEN,
) -> BacktestResult:
    books = [Book(cfg, base_risk, store=None, cost_multiplier=cost_multiplier) for cfg in configs]
    desk = Desk(books, store=None)
    start_ts = None if start is None else pd.Timestamp(start)
    end_ts = None if end is None else pd.Timestamp(end)
    results: dict[str, BookResult] = {}
    for vehicle_id in desk.vehicles():
        series = data.series.get(vehicle_id)
        if series is None or series.bars.empty:
            log.warning("backtest: no data for vehicle %s, its books are skipped", vehicle_id)
            continue
        records = run_vehicle(desk, series, start_ts, end_ts, fill=fill)
        for book in desk.books_on(vehicle_id):
            rec = records[book.id]
            if not rec["t"]:
                continue
            idx = pd.DatetimeIndex(rec["t"])
            equity = pd.Series(rec["equity"], index=idx, dtype="float64")
            lev = pd.Series(rec["lev"], index=idx, dtype="float64")
            expo = pd.Series(rec["expo"], index=idx, dtype="float64")
            st = book.broker.state
            died = None
            if st.status == AccountStatus.DEAD and st.epoch_ended_ts is not None:
                died = st.epoch_ended_ts.date().isoformat()
            stats = performance(equity)
            stats.update(
                {
                    "leverage_median": round(float(lev.median()), 3),
                    "leverage_p95": round(float(lev.quantile(0.95)), 3),
                    "leverage_max": round(float(lev.max()), 3),
                    "share_days_above_1x": round(float((lev > 1.0 + 1e-9).mean()), 3),
                    "share_days_invested": round(float((lev > 1e-9).mean()), 3),
                    "share_days_short": round(float((expo < -1e-9).mean()), 3),
                }
            )
            years = max(1e-9, len(equity) / TRADING_DAYS)
            mean_equity = max(1e-9, float(equity.mean()))
            total_cost = st.total_commission + st.total_slippage_usd + st.total_financing
            results[book.id] = BookResult(
                book=book.id,
                vehicle=vehicle_id,
                equity=equity,
                leverage=lev,
                exposure=expo,
                stats=stats,
                yearly=yearly_returns(equity),
                costs={
                    "commission": round(st.total_commission, 2),
                    "spread_and_slippage": round(st.total_slippage_usd, 2),
                    "financing": round(st.total_financing, 2),
                    # the account compounds, so dollars of cost are compared with the average equity
                    "per_year_of_mean_equity": round(total_cost / mean_equity / years, 4),
                },
                ruin=ruin_probabilities(equity.pct_change()) if ruin else {},
                died=died,
                n_fills=int(st.n_fills),
            )
    benchmarks: dict[str, dict[str, Any]] = {}
    for vehicle_id, series in data.series.items():
        ret = series.returns
        if start_ts is not None:
            ret = ret.loc[start_ts:]
        if end_ts is not None:
            ret = ret.loc[:end_ts]
        ret = ret.iloc[WARMUP_DAYS:] if start_ts is None else ret
        if len(ret) > 60:
            eq = (1.0 + ret).cumprod() * float(base_risk.initial_capital)
            benchmarks[vehicle_id] = {
                "name": f"{vehicle_id} comprato e tenuto (1x)",
                "stats": performance(eq),
                "yearly": {str(k): v for k, v in yearly_returns(eq).items()},
                "curve": downsample(eq, 400),
            }
    meta = {
        "generated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "cost_multiplier": cost_multiplier,
        "initial_capital": float(base_risk.initial_capital),
        "fill": fill,
        "fill_rule": (
            "ordine deciso alla chiusura, eseguito all'apertura della seduta successiva"
            if fill == FILL_NEXT_OPEN
            else "ordine eseguito alla stessa chiusura su cui è deciso (estremo ottimistico)"
        ),
        "missing": list(data.meta.get("missing", [])),
    }
    return BacktestResult(books=results, benchmarks=benchmarks, meta=meta)
