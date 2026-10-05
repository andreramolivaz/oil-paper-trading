"""Backtest runner: walks the trading days through `TradingSession` and summarises the result.

The runner adds nothing to the trading logic — it only iterates days, collects the equity curves and computes
metrics — which is what makes a backtest comparable to the live track record: the same session, the same broker,
the same allocator, the same features.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from engine.backtest.metrics import PerfSummary, summarise
from engine.backtest.session import SessionState, TradingSession
from engine.broker.paper import PaperBroker
from engine.core.config import RiskConfig
from engine.core.store import StateStore
from engine.core.timeutil import iso, settlement_ts
from engine.data.market_data import MarketData
from engine.portfolio.base import Allocator
from engine.strategies.base import Strategy

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity: pd.DataFrame  # columns: master, master_1x (when available), buy_hold, one per shadow strategy
    trades: pd.DataFrame
    signals: pd.DataFrame
    snapshots: pd.DataFrame
    regimes: pd.DataFrame
    summary: dict[str, PerfSummary] = field(default_factory=dict)
    days: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {k: v.to_dict() for k, v in self.summary.items()},
            "n_days": len(self.days),
            "meta": self.meta,
        }


def trading_days(md: MarketData, start: date | None, end: date | None) -> list[date]:
    if md.prices.empty:
        return []
    idx = pd.DatetimeIndex(md.prices.index)
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    if end is not None:
        idx = idx[idx <= pd.Timestamp(end)]
    close = md.prices.get("brent_front_close")
    if close is not None:
        valid = close.reindex(idx).notna()
        idx = idx[valid.to_numpy()]
    return [d.date() for d in idx]


def run_backtest(
    md: MarketData,
    strategies: list[Strategy],
    risk: RiskConfig,
    start: date | None = None,
    end: date | None = None,
    allocator: Allocator | None = None,
    feature_builder: Any | None = None,
    regime_model: Any | None = None,
    store: StateStore | None = None,
    strict_pit: bool = False,
    shadow_accounts: bool = True,
    progress: Callable[[int, int, date], None] | None = None,
    days: Iterable[date] | None = None,
) -> BacktestResult:
    """Run the session over the trading days and return equity curves, logs and metrics."""
    day_list = list(days) if days is not None else trading_days(md, start, end)
    master = PaperBroker("master", risk, store=None)
    session = TradingSession(
        md=md,
        strategies=strategies,
        master=master,
        risk=risk,
        allocator=allocator,
        feature_builder=feature_builder,
        regime_model=regime_model,
        store=store,
        strict_pit=strict_pit,
        shadow_accounts=shadow_accounts,
        state=SessionState(),
    )

    equity_rows: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    regime_rows: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    seen_fills: set[str] = set()
    total = len(day_list)

    for i, day in enumerate(day_list, start=1):
        res = session.run_day(day)
        day_rows.append(
            {
                "day": day.isoformat(),
                "bars": res.bars,
                "fills": res.fills,
                "orders": res.orders,
                "signals": res.signals,
                "regime": res.regime,
                "rolled": None if res.rolled is None else f"{res.rolled[0]}->{res.rolled[1]}",
                "skipped": res.skipped,
            }
        )
        if res.skipped == "no price":
            continue
        asof = settlement_ts(day)
        snap = master.snapshot(asof)
        snapshot_rows.append(snap.to_dict())
        row: dict[str, Any] = {"ts": pd.Timestamp(day), "master": snap.equity}
        for sid, broker in session.shadows.items():
            row[sid] = broker.snapshot(asof).equity
        equity_rows.append(row)
        if session._regime_history:
            regime_rows.append(session._regime_history[-1].to_dict())
        if progress is not None and (i % 50 == 0 or i == total):
            progress(i, total, day)

    # trades and signals: read back from the store when one is attached, else from broker audit counters
    if store is not None:
        trade_rows = [r for r in store.read_jsonl("trades") if r.get("fill_id") not in seen_fills]
        signal_rows = store.read_jsonl("signals")

    equity = pd.DataFrame(equity_rows).set_index("ts") if equity_rows else pd.DataFrame()
    if not equity.empty:
        close = md.prices["brent_front_close"].reindex(equity.index)
        first = close.dropna()
        if not first.empty:
            equity["buy_hold"] = risk.initial_capital * (close / float(first.iloc[0]))

    trades = pd.DataFrame(trade_rows)
    summary: dict[str, PerfSummary] = {}
    if not equity.empty:
        for col in equity.columns:
            curve = equity[col].dropna()
            if len(curve) > 1:
                t = trades if col == "master" else None
                summary[col] = summarise(curve, trades=t)

    return BacktestResult(
        equity=equity,
        trades=trades,
        signals=pd.DataFrame(signal_rows),
        snapshots=pd.DataFrame(snapshot_rows),
        regimes=pd.DataFrame(regime_rows),
        summary=summary,
        days=day_rows,
        meta={
            "start": iso(day_list[0]) if day_list else None,
            "end": iso(day_list[-1]) if day_list else None,
            "n_strategies": len(strategies),
            "strict_pit": strict_pit,
            "allocator": getattr(session.allocator, "name", type(session.allocator).__name__),
            "initial_capital": risk.initial_capital,
        },
    )
