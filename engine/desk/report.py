"""Run the desk backtest and write it where the terminal and the docs read it.

``state/desk/backtest.json`` carries, for every book: the replay with the configured costs, the same replay with
every cost doubled, each sleeve on its own (so nobody has to take the combination on trust), and buying and
holding the vehicle as the benchmark. The numbers are whatever came out; nothing is selected.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date
from pathlib import Path
from typing import Any

from engine.core.config import RiskConfig, Settings
from engine.core.store import StateStore
from engine.data.raw_store import RawStore
from engine.desk import signals as sg
from engine.desk.backtest import FILL_SAME_CLOSE, BacktestResult, run_backtest
from engine.desk.book import BookConfig, load_books
from engine.desk.data import DeskData, build_desk_data
from engine.desk.engine import _clean
from engine.desk.live import desk_store
from engine.desk.options import load_options_config, replay_payload

log = logging.getLogger(__name__)

BACKTEST_FILE = "backtest.json"
SLEEVE_VOL_TARGET = 0.15
SLEEVE_LABELS = {"trend": "Trend (EWMAC)", "carry": "Carry (pendenza della curva)", "carry_momentum": "Carry-momentum"}


def sleeve_configs(vehicle: str, long_only: bool) -> list[BookConfig]:
    """One reference book per sleeve, plus all three, at the same volatility target and without a tight cap."""
    configs = []
    for sleeve in sg.SLEEVES:
        configs.append(
            BookConfig(
                id=f"{vehicle}:{sleeve}",
                name=SLEEVE_LABELS[sleeve],
                vehicle=vehicle,
                vol_target=SLEEVE_VOL_TARGET,
                max_leverage=2.0 if vehicle == "BNO" else 4.0,
                long_only=long_only,
                sleeves={sleeve: 1.0},
                daily_loss_breaker=0.5,
            )
        )
    configs.append(
        BookConfig(
            id=f"{vehicle}:tutte",
            name="Le tre insieme",
            vehicle=vehicle,
            vol_target=SLEEVE_VOL_TARGET,
            max_leverage=2.0 if vehicle == "BNO" else 4.0,
            long_only=long_only,
            daily_loss_breaker=0.5,
        )
    )
    return configs


def _stats_only(result: BacktestResult) -> dict[str, Any]:
    return {
        k: {"stats": v.stats, "died": v.died, "yearly": {str(y): r for y, r in v.yearly.items()}}
        for k, v in result.books.items()
    }


def desk_backtest_payload(
    data: DeskData,
    books: list[BookConfig],
    risk: RiskConfig,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    base = run_backtest(data, books, risk, start=start, end=end)
    doubled = run_backtest(data, books, risk, start=start, end=end, cost_multiplier=2.0, ruin=False)
    same_close = run_backtest(data, books, risk, start=start, end=end, ruin=False, fill=FILL_SAME_CLOSE)
    payload = base.to_dict()
    payload["double_cost"] = _stats_only(doubled)
    payload["same_close"] = _stats_only(same_close)
    sleeves: dict[str, Any] = {}
    for vehicle in sorted({b.vehicle for b in books}):
        long_only = all(b.long_only for b in books if b.vehicle == vehicle)
        # Attribution is run on a large account so that one lot is a rounding error: with ten thousand dollars
        # a single micro contract is most of the position and the sleeves could not be told apart.
        big = dataclasses.replace(risk, initial_capital=risk.initial_capital * 1000.0)
        res = run_backtest(data, sleeve_configs(vehicle, long_only), big, start=start, end=end, ruin=False)
        res_close = run_backtest(
            data, sleeve_configs(vehicle, long_only), big, start=start, end=end, ruin=False, fill=FILL_SAME_CLOSE
        )
        sleeves[vehicle] = {
            "vol_target": SLEEVE_VOL_TARGET,
            "long_only": long_only,
            "note": "conto di riferimento mille volte più grande, per togliere l'effetto del lotto minimo",
            "rows": [
                {
                    "id": k.split(":", 1)[1],
                    "name": cfg.name,
                    "stats": res.books[k].stats,
                    "stats_same_close": res_close.books[k].stats if k in res_close.books else {},
                }
                for k, cfg in ((c.id, c) for c in sleeve_configs(vehicle, long_only))
                if k in res.books
            ],
        }
    payload["sleeves"] = sleeves
    payload["books_config"] = [dataclasses.asdict(b) for b in books]
    payload["data"] = {
        vehicle: {
            "start": s.bars.index[0].date().isoformat(),
            "end": s.bars.index[-1].date().isoformat(),
            "days": len(s.bars),
            "return_sources": {str(k): int(v) for k, v in s.return_source.value_counts().items()},
            "slope_approx_share": round(float(s.slope_approx.mean()), 3),
        }
        for vehicle, s in data.series.items()
    }
    return _clean(payload)


def summary_lines(payload: dict[str, Any]) -> list[str]:
    lines = ["libro       veicolo  anni   CAGR    vol  Sharpe     t   maxDD  leva p95  leva max  P(-50% in 1a)"]
    for book_id, b in payload.get("books", {}).items():
        s = b.get("stats", {})
        ruin = b.get("ruin", {})
        lines.append(
            f"{book_id:<11} {b.get('vehicle', ''):<7} {s.get('years', 0):>5.1f} {s.get('cagr', 0):>6.1%} "
            f"{s.get('vol', 0):>6.1%} {s.get('sharpe') or 0:>6.2f} {s.get('t_stat') or 0:>5.2f} "
            f"{s.get('max_drawdown', 0):>7.1%} {s.get('leverage_p95', 0):>8.2f}x {s.get('leverage_max', 0):>7.2f}x "
            f"{ruin.get('p_lose_half', 0):>10.1%}"
        )
    for vehicle, b in payload.get("benchmarks", {}).items():
        s = b.get("stats", {})
        lines.append(
            f"{'compra e tieni':<11} {vehicle:<5} {s.get('years', 0):>5.1f} {s.get('cagr', 0):>6.1%} "
            f"{s.get('vol', 0):>6.1%} {s.get('sharpe') or 0:>6.2f} {s.get('t_stat') or 0:>5.2f} "
            f"{s.get('max_drawdown', 0):>7.1%}"
        )
    return lines


def run_desk_backtest(
    raw: RawStore,
    settings: Settings,
    risk: RiskConfig,
    store: StateStore,
    start: date | None = None,
    end: date | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the data, replay, persist ``desk/backtest.json``. The live books are never touched: the replay
    runs on brokers of its own with no store attached."""
    del store  # the live account store is deliberately unused: a backtest must never write into live state
    data = build_desk_data(raw, settings)
    books = load_books(settings.config_dir)
    payload = desk_backtest_payload(data, books, risk, start=start, end=end)
    options_cfg = load_options_config(settings.config_dir)
    if options_cfg is not None:
        try:
            payload["options"] = _clean(replay_payload(data, raw, options_cfg, float(risk.initial_capital)))
        except Exception as exc:  # the replay is a model on the side: it must never cost the real backtest
            log.warning("options replay skipped: %s", exc)
            payload["options"] = {"approx": True, "available": False, "reason": str(exc)[:200]}
    target = desk_store(settings.state_dir)
    target.write_json(BACKTEST_FILE, payload, indent=None)
    lines = summary_lines(payload)
    opt = payload.get("options") or {}
    if opt.get("available"):
        st = opt["headline"]["stats"]
        lines.append(
            f"opzioni (≈ modello) USO  {st.get('years', 0):>5.1f} {st.get('cagr', 0):>6.1%} {st.get('vol', 0):>6.1%} "
            f"{st.get('sharpe') or 0:>6.2f} {st.get('t_stat') or 0:>5.2f} {st.get('max_drawdown', 0):>7.1%}   "
            f"Sharpe fra {opt['sharpe_range'][0]:.2f} e {opt['sharpe_range'][1]:.2f} secondo smile e costi"
        )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "desk_backtest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"lines": lines, "books": list(payload.get("books", {}))}
