"""Run the desk backtest and write it where the terminal and the docs read it.

``state/desk/backtest.json`` carries, for every book: the replay with the configured costs, the same replay with
every cost doubled, each sleeve and each source on its own next to the mix the desk read before and the one
it reads now (so nobody has to take the combination on trust), and buying and holding the vehicle as the
benchmark. The numbers are whatever came out; nothing is selected.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.config import RiskConfig, Settings
from engine.core.store import StateStore
from engine.data.raw_store import RawStore
from engine.desk import signals as sg
from engine.desk.allocator import AllocatorConfig, describe
from engine.desk.backtest import FILL_SAME_CLOSE, BacktestResult, performance, run_backtest, yearly_returns
from engine.desk.book import FORECAST_DESK, BookConfig, load_books
from engine.desk.data import DeskData, build_desk_data
from engine.desk.engine import _clean
from engine.desk.live import desk_store
from engine.desk.options import load_options_config, replay_payload
from engine.desk.vehicles import INVERSE_FUNDS, VEHICLES

log = logging.getLogger(__name__)

BACKTEST_FILE = "backtest.json"
SLEEVE_VOL_TARGET = 0.15
SLEEVE_LABELS = {
    "trend": "Trend (EWMAC)",
    "accel": "Trend acceleration",
    "skew": "Return asymmetry (skew)",
    "carry": "Carry (curve slope)",
    "carry_momentum": "Carry-momentum",
    "copper": "Copper trend",
    "dollar": "Dollar trend (inverted)",
}
SOURCE_LABELS = {
    "prezzo": "Source 1 - the price of crude (trend, acceleration, skew)",
    "curva": "Source 2 - the futures curve (carry, carry-momentum)",
    "macro": "Source 3 - other markets (copper, dollar)",
}
# the three sleeves the books read until phase 11: kept in the table so the upgrade can be judged on one page
BEFORE_ID = "prima"
BEFORE_SLEEVES = ("trend", "carry", "carry_momentum")


def sleeve_configs(
    vehicle: str, long_only: bool, short_via: str | None = None, used: tuple[str, ...] | None = None
) -> list[BookConfig]:
    """Reference books at the same volatility target and without a tight cap: every sleeve alone, every source
    alone, the three sleeves the desk read before the other four were added, and all seven together.
    ``short_via`` gives them the short leg of the fund book they stand for. ``used`` restricts the table to the
    sleeves the books on this vehicle read (a satellite book reads the price source alone): the rows of sleeves
    it has no data for would be flat lines, and "before" means nothing to it."""
    prefix = vehicle if short_via is None else f"{vehicle}+{short_via}"
    sleeves_used = tuple(sg.SLEEVES) if used is None else tuple(name for name in sg.SLEEVES if name in used)

    def reference(key: str, name: str, sleeves: dict[str, float] | None) -> BookConfig:
        return BookConfig(
            id=f"{prefix}:{key}",
            name=name,
            vehicle=vehicle,
            vol_target=SLEEVE_VOL_TARGET,
            max_leverage=2.0 if vehicle == "BNO" else 4.0,
            long_only=long_only,
            short_via=short_via,
            sleeves=dict(sg.DEFAULT_WEIGHTS) if sleeves is None else sleeves,
            daily_loss_breaker=0.5,
        )

    configs = [reference(sleeve, SLEEVE_LABELS[sleeve], {sleeve: 1.0}) for sleeve in sleeves_used]
    for source, members in sg.SOURCES.items():
        present = [name for name in members if name in sleeves_used]
        if present:
            label = SOURCE_LABELS[source]
            if source == "prezzo" and vehicle not in ("BNO", "MCL"):
                label = label.replace("the price of crude", "the fund's own price")  # a satellite reads its own
            configs.append(reference(source, label, dict.fromkeys(present, 1.0)))
    if set(sleeves_used) == set(sg.SLEEVES):
        configs.append(reference(BEFORE_ID, "Before: trend, carry, carry-momentum", dict.fromkeys(BEFORE_SLEEVES, 1.0)))
        configs.append(reference("tutte", "Now: the seven together, a third per source", None))
    elif len(sleeves_used) > 1:
        configs.append(reference("tutte", "Together, at equal weights", dict.fromkeys(sleeves_used, 1.0)))
    return configs


def replayed_books(books: list[BookConfig]) -> list[BookConfig]:
    """The books the replay can run: those on the desk's forecast, which has a history. A book on the prediction
    markets' index (phase 14) has none - no backtest, no row in the tables, no part in the fingerprint - and is
    judged on its live record alone. Every caller that replays or fingerprints the books goes through here, so
    the tick and the weekly job agree on what the backtest is about."""
    return [b for b in books if b.forecast == FORECAST_DESK]


MODEL_VERSION = 3  # bump when the replay changes in a way that the rules below do not show (3: distributions credited)
# Fields of a book, a vehicle, an inverse fund or the account rules that are written for the reader and change
# nothing in the replay: a rename or a translation must not cost a recompute on the next tick.
COSMETIC_FIELDS = frozenset({"name", "description", "note", "robinhood", "unit", "label", "short_note"})


def canonical(value: Any) -> str:
    """``repr`` with one spelling per value: the members of a set are sorted. The order a set of strings is
    walked in changes from one process to the next (hash randomisation), and a fingerprint built on it would
    change with every tick - each of which would then recompute the backtest."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{canonical(k)}: {canonical(v)}" for k, v in value.items()) + "}"
    if isinstance(value, set | frozenset):
        return "{" + ", ".join(sorted(canonical(v) for v in value)) + "}"
    if isinstance(value, tuple | list):
        return "(" + ", ".join(canonical(v) for v in value) + ")"
    return repr(value)


# constants of the sizing module that describe the Kalshi books' forecast family (the registry of indices,
# phase 15): no number of the replay depends on them, so a new index must not cost a recomputation (found in
# review: the phase-15 fingerprint had changed for this and not, as written, for the new books)
NOT_REPLAYED = frozenset({"FORECAST_FAMILIES", "DEFAULT_WEIGHTS_OF"})


def model_signature(
    books: list[BookConfig],
    options_cfg: Any | None = None,
    risk: RiskConfig | None = None,
    allocator: AllocatorConfig | None = None,
) -> str:
    """A short fingerprint of what the backtest numbers depend on besides the data: every constant of the
    modules that compute them (signals, series, replay, books, options, the allocator), the books, the
    vehicles, the options book, the account rules (capital, costs, margin) and the allocator's rules the
    replay is run with.

    The backtest on file carries the fingerprint it was computed with (``meta.model``). The tick recomputes one
    whose fingerprint is not the current one, so a change of rules shows on the page at the next tick instead
    of waiting for the weekly job with the old numbers under the new rules. Only the books the replay runs
    enter it (``replayed_books``): the Kalshi book's rules change no number of the backtest.
    """
    books = replayed_books(books)
    from engine.desk import allocator as allocation
    from engine.desk import backtest as replay
    from engine.desk import book as sizing
    from engine.desk import data as series
    from engine.desk import options as option_book

    def constants(module: Any) -> list[tuple[str, str]]:
        """Upper-case module constants of plain types (numbers, text, tuples and dicts of them), except the
        labels written for the reader: a translated word must not cost a recompute."""
        plain = (int, float, str, bool, tuple, dict, frozenset)
        return sorted(
            (n, canonical(v))
            for n, v in vars(module).items()
            if n.isupper()
            and isinstance(v, plain)
            and not n.endswith(("_LABELS", "_LABEL", "_NOTE", "_NOTES"))
            and n not in NOT_REPLAYED
        )

    def fields(obj: Any) -> list[tuple[str, Any]]:
        """A dataclass's fields that enter the replay: names, descriptions and notes are for the reader."""
        return sorted((k, v) for k, v in dataclasses.asdict(obj).items() if k not in COSMETIC_FIELDS)

    def rows(items: dict[str, Any]) -> list[Any]:
        return [fields(v) for _, v in sorted(items.items())]

    spec = [
        MODEL_VERSION,
        constants(sg),
        constants(series),  # roll day, far contract, how old a macro close may be, which tables are read
        constants(replay),  # warm-up, fill rules
        constants(sizing),  # the hard cap
        constants(option_book),  # the smiles and the costs of the options replay
        constants(allocation),  # the allocator's own constants (trading days, the lifecycle's month)
        [fields(b) for b in books],
        rows(VEHICLES),
        rows(INVERSE_FUNDS),
        None if options_cfg is None else fields(options_cfg),
        None if risk is None else fields(risk),
        None if allocator is None else fields(allocator),
    ]
    return hashlib.sha1(canonical(spec).encode("utf-8")).hexdigest()[:12]


def attribution_variants(books: list[BookConfig]) -> list[tuple[str, str, bool, str | None, tuple[str, ...]]]:
    """The ways the books on file trade a vehicle, each of which gets its own attribution table:
    ``(label, vehicle, long_only, short_via, sleeves used)``. A fund held long only and the same fund with its
    short side in an inverse fund are two different tables: the second shows what the short side adds, sleeve
    by sleeve. The sleeves are the union of what the books on the vehicle read."""
    out: list[tuple[str, str, bool, str | None, tuple[str, ...]]] = []
    for vehicle in sorted({b.vehicle for b in books}):
        on_vehicle = [b for b in books if b.vehicle == vehicle]
        used = tuple(name for name in sg.SLEEVES if any(b.sleeves.get(name, 0.0) > 0 for b in on_vehicle))
        sells = VEHICLES[vehicle].allow_short
        long_only = any(b.long_only or (not sells and b.short_via is None) for b in on_vehicle)
        if long_only:
            out.append((vehicle, vehicle, True, None, used))
        if sells and any(not b.long_only for b in on_vehicle):
            out.append((f"{vehicle} (long and short)" if long_only else vehicle, vehicle, False, None, used))
        for fund in sorted({b.short_via for b in on_vehicle if b.short_via is not None}):
            out.append((f"{vehicle} + {fund}", vehicle, False, fund, used))
    return out


def _row_kind(key: str) -> str:
    """What a row of the attribution table is: one sleeve, one source, the old mix or the current one."""
    if key in sg.SLEEVES:
        return "sleeve"
    if key in sg.SOURCES:
        return "source"
    return "before" if key == BEFORE_ID else "all"


def portfolio_stats(
    result: BacktestResult, ids: list[str], label: str, start: date | None = None, end: date | None = None
) -> dict[str, Any] | None:
    """The books in ``ids`` held together with equal capital, rebalanced daily, over the window on which all of
    them have an equity curve: the Sharpe ratio of the sum is what a reader of several books wants, and it is
    not the average of their Sharpe ratios. None when fewer than two of the books have a curve. ``start`` and
    ``end`` cut the window, so that two portfolios can be compared over the same days."""
    curves = [result.books[i].equity for i in ids if i in result.books and not result.books[i].equity.empty]
    if len(curves) < 2:
        return None
    frame = pd.concat(curves, axis=1).dropna()
    if start is not None:
        frame = frame.loc[frame.index >= pd.Timestamp(start)]
    if end is not None:
        frame = frame.loc[frame.index <= pd.Timestamp(end)]
    if len(frame) < 60:
        return None
    returns = frame.pct_change().dropna().mean(axis=1)
    equity = (1.0 + returns).cumprod() * float(frame.iloc[0].sum())
    stats = performance(equity)
    if not stats:
        return None
    return {
        "label": label,
        "books": [i for i in ids if i in result.books],
        "start": frame.index[0].date().isoformat(),
        "end": frame.index[-1].date().isoformat(),
        "stats": stats,
        "yearly": {str(k): v for k, v in yearly_returns(equity).items()},
        "method": "equal capital in every book, rebalanced daily, over the days all of them have",
    }


def portfolio_block(result: BacktestResult, books: list[BookConfig]) -> dict[str, Any]:
    """The books together: the core books (what the desk is judged on) over their whole common history and,
    when there are candidates, everything over ITS common window and the core books again over that same window,
    so that the two rows say what adding the candidates did and not what the extra years did."""
    core = [b.id for b in books if b.role == "core"]
    everything = [b.id for b in books]
    out: dict[str, Any] = {}
    core_stats = portfolio_stats(result, core, "the core books together")
    if core_stats is not None:
        out["core"] = core_stats
    if len(everything) > len(core):
        all_stats = portfolio_stats(result, everything, "core and candidate books together")
        if all_stats is not None:
            out["all"] = all_stats
            matched = portfolio_stats(
                result,
                core,
                "the core books together, over the candidates' window",
                start=date.fromisoformat(all_stats["start"]),
                end=date.fromisoformat(all_stats["end"]),
            )
            if matched is not None:
                out["core_matched"] = matched
    return out


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
    allocator: AllocatorConfig | None = None,
) -> dict[str, Any]:
    """The replay under the rules in force: the books with the allocator (``allocator``) - and, when there is
    one, the same books without it under ``without_allocator``, so that what the allocator did is always on
    the page next to what the books did alone. A book the replay cannot run (``replayed_books``) is left out of
    every table here; ``excluded`` names it, so the page can say why it is missing."""
    excluded = [b for b in books if b not in replayed_books(books)]
    books = replayed_books(books)
    base = run_backtest(data, books, risk, start=start, end=end, allocator=allocator)
    doubled = run_backtest(
        data, books, risk, start=start, end=end, cost_multiplier=2.0, ruin=False, allocator=allocator
    )
    same_close = run_backtest(
        data, books, risk, start=start, end=end, ruin=False, fill=FILL_SAME_CLOSE, allocator=allocator
    )
    payload = base.to_dict()
    payload["double_cost"] = _stats_only(doubled)
    payload["same_close"] = _stats_only(same_close)
    payload["portfolio"] = portfolio_block(base, books)
    payload["allocator"] = None if allocator is None else {"rules": describe(allocator), "config": allocator.to_dict()}
    if allocator is not None:
        alone = run_backtest(data, books, risk, start=start, end=end, ruin=False)
        payload["without_allocator"] = {"books": _stats_only(alone), "portfolio": portfolio_block(alone, books)}
    sleeves: dict[str, Any] = {}
    for label, vehicle, long_only, short_via, used in attribution_variants(books):
        # Attribution is run on a large account so that one lot is a rounding error: with ten thousand dollars
        # a single micro contract is most of the position and the sleeves could not be told apart.
        big = dataclasses.replace(risk, initial_capital=risk.initial_capital * 1000.0)
        configs = sleeve_configs(vehicle, long_only, short_via, used)
        res = run_backtest(data, configs, big, start=start, end=end, ruin=False)
        res_close = run_backtest(data, configs, big, start=start, end=end, ruin=False, fill=FILL_SAME_CLOSE)
        sleeves[label] = {
            "vehicle": vehicle,
            "vol_target": SLEEVE_VOL_TARGET,
            "long_only": long_only,
            "short_via": short_via,
            "note": "reference account a thousand times larger, to remove the effect of the minimum lot",
            "rows": [
                {
                    "id": cfg.id.split(":", 1)[1],
                    "kind": _row_kind(cfg.id.split(":", 1)[1]),
                    "name": cfg.name,
                    "stats": res.books[cfg.id].stats,
                    "stats_same_close": res_close.books[cfg.id].stats if cfg.id in res_close.books else {},
                }
                for cfg in configs
                if cfg.id in res.books
            ],
        }
    payload["sleeves"] = sleeves
    payload["books_config"] = [dataclasses.asdict(b) for b in books]
    # the books on file that have no replay: the page lists them with the reason instead of a row of numbers
    payload["excluded"] = [
        {
            "id": b.id,
            "name": b.name,
            "vehicle": b.vehicle,
            "role": b.role,
            "forecast": b.forecast,
            "reason": "no history of the prediction markets to replay: judged on live fills only",
        }
        for b in excluded
    ]
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
    lines = ["book        vehicle  years   CAGR    vol  Sharpe     t   maxDD   lev p95  lev max  P(-50% in 1y)"]
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
            f"{'buy and hold':<11} {vehicle:<5} {s.get('years', 0):>5.1f} {s.get('cagr', 0):>6.1%} "
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
    allocator = AllocatorConfig.load(settings.config_dir)
    payload = desk_backtest_payload(data, books, risk, start=start, end=end, allocator=allocator)
    options_cfg = load_options_config(settings.config_dir)
    if options_cfg is not None:
        try:
            payload["options"] = _clean(replay_payload(data, raw, options_cfg, float(risk.initial_capital)))
        except Exception as exc:  # the replay is a model on the side: it must never cost the real backtest
            log.warning("options replay skipped: %s", exc)
            payload["options"] = {"approx": True, "available": False, "reason": str(exc)[:200]}
    payload.setdefault("meta", {})["model"] = model_signature(books, options_cfg, risk, allocator)
    target = desk_store(settings.state_dir)
    target.write_json(BACKTEST_FILE, payload, indent=None)
    lines = summary_lines(payload)
    opt = payload.get("options") or {}
    if opt.get("available"):
        st = opt["headline"]["stats"]
        lines.append(
            f"options (≈ model) USO  {st.get('years', 0):>5.1f} {st.get('cagr', 0):>6.1%} {st.get('vol', 0):>6.1%} "
            f"{st.get('sharpe') or 0:>6.2f} {st.get('t_stat') or 0:>5.2f} {st.get('max_drawdown', 0):>7.1%}   "
            f"Sharpe between {opt['sharpe_range'][0]:.2f} and {opt['sharpe_range'][1]:.2f} depending on smile and costs"
        )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "desk_backtest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"lines": lines, "books": list(payload.get("books", {}))}
