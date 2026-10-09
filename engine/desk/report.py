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
from engine.desk.vehicles import INVERSE_FUNDS, VEHICLES

log = logging.getLogger(__name__)

BACKTEST_FILE = "backtest.json"
SLEEVE_VOL_TARGET = 0.15
SLEEVE_LABELS = {
    "trend": "Trend (EWMAC)",
    "accel": "Accelerazione del trend",
    "skew": "Asimmetria dei rendimenti (skew)",
    "carry": "Carry (pendenza della curva)",
    "carry_momentum": "Carry-momentum",
    "copper": "Trend del rame",
    "dollar": "Trend del dollaro (invertito)",
}
SOURCE_LABELS = {
    "prezzo": "Fonte 1 - il prezzo del greggio (trend, accelerazione, skew)",
    "curva": "Fonte 2 - la curva dei future (carry, carry-momentum)",
    "macro": "Fonte 3 - altri mercati (rame, dollaro)",
}
# the three sleeves the books read until phase 11: kept in the table so the upgrade can be judged on one page
BEFORE_ID = "prima"
BEFORE_SLEEVES = ("trend", "carry", "carry_momentum")


def sleeve_configs(vehicle: str, long_only: bool, short_via: str | None = None) -> list[BookConfig]:
    """Reference books at the same volatility target and without a tight cap: every sleeve alone, every source
    alone, the three sleeves the desk read before the other four were added, and all seven together.
    ``short_via`` gives them the short leg of the fund book they stand for."""
    prefix = vehicle if short_via is None else f"{vehicle}+{short_via}"

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

    configs = [reference(sleeve, SLEEVE_LABELS[sleeve], {sleeve: 1.0}) for sleeve in sg.SLEEVES]
    for source, members in sg.SOURCES.items():
        configs.append(reference(source, SOURCE_LABELS[source], dict.fromkeys(members, 1.0)))
    configs.append(reference(BEFORE_ID, "Prima: trend, carry, carry-momentum", dict.fromkeys(BEFORE_SLEEVES, 1.0)))
    configs.append(reference("tutte", "Adesso: le sette insieme, un terzo per fonte", None))
    return configs


MODEL_VERSION = 2  # bump when the replay changes in a way that the rules below do not show


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


def model_signature(books: list[BookConfig], options_cfg: Any | None = None, risk: RiskConfig | None = None) -> str:
    """A short fingerprint of what the backtest numbers depend on besides the data: every constant of the
    modules that compute them (signals, series, replay, books, options), the books, the vehicles, the options
    book and the account rules (capital, costs, margin) the replay is run with.

    The backtest on file carries the fingerprint it was computed with (``meta.model``). The tick recomputes one
    whose fingerprint is not the current one, so a change of rules shows on the page at the next tick instead
    of waiting for the weekly job with the old numbers under the new rules.
    """
    from engine.desk import backtest as replay
    from engine.desk import book as sizing
    from engine.desk import data as series
    from engine.desk import options as option_book

    def constants(module: Any) -> list[tuple[str, str]]:
        """Upper-case module constants of plain types (numbers, text, tuples and dicts of them)."""
        plain = (int, float, str, bool, tuple, dict, frozenset)
        return sorted((n, canonical(v)) for n, v in vars(module).items() if n.isupper() and isinstance(v, plain))

    def rows(items: dict[str, Any]) -> list[Any]:
        return [sorted(dataclasses.asdict(v).items(), key=lambda kv: kv[0]) for _, v in sorted(items.items())]

    spec = [
        MODEL_VERSION,
        constants(sg),
        constants(series),  # roll day, far contract, how old a macro close may be, which tables are read
        constants(replay),  # warm-up, fill rules
        constants(sizing),  # the hard cap
        constants(option_book),  # the smiles and the costs of the options replay
        [sorted(dataclasses.asdict(b).items(), key=lambda kv: kv[0]) for b in books],
        rows(VEHICLES),
        rows(INVERSE_FUNDS),
        None if options_cfg is None else sorted(dataclasses.asdict(options_cfg).items(), key=lambda kv: kv[0]),
        None if risk is None else sorted(dataclasses.asdict(risk).items(), key=lambda kv: kv[0]),
    ]
    return hashlib.sha1(canonical(spec).encode("utf-8")).hexdigest()[:12]


def attribution_variants(books: list[BookConfig]) -> list[tuple[str, str, bool, str | None]]:
    """The ways the books on file trade a vehicle, each of which gets its own attribution table:
    ``(label, vehicle, long_only, short_via)``. A fund held long only and the same fund with its short side in
    an inverse fund are two different tables: the second shows what the short side adds, sleeve by sleeve."""
    out: list[tuple[str, str, bool, str | None]] = []
    for vehicle in sorted({b.vehicle for b in books}):
        on_vehicle = [b for b in books if b.vehicle == vehicle]
        sells = VEHICLES[vehicle].allow_short
        long_only = any(b.long_only or (not sells and b.short_via is None) for b in on_vehicle)
        if long_only:
            out.append((vehicle, vehicle, True, None))
        if sells and any(not b.long_only for b in on_vehicle):
            out.append((f"{vehicle} (long e short)" if long_only else vehicle, vehicle, False, None))
        for fund in sorted({b.short_via for b in on_vehicle if b.short_via is not None}):
            out.append((f"{vehicle} + {fund}", vehicle, False, fund))
    return out


def _row_kind(key: str) -> str:
    """What a row of the attribution table is: one sleeve, one source, the old mix or the current one."""
    if key in sg.SLEEVES:
        return "sleeve"
    if key in sg.SOURCES:
        return "source"
    return "before" if key == BEFORE_ID else "all"


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
    for label, vehicle, long_only, short_via in attribution_variants(books):
        # Attribution is run on a large account so that one lot is a rounding error: with ten thousand dollars
        # a single micro contract is most of the position and the sleeves could not be told apart.
        big = dataclasses.replace(risk, initial_capital=risk.initial_capital * 1000.0)
        configs = sleeve_configs(vehicle, long_only, short_via)
        res = run_backtest(data, configs, big, start=start, end=end, ruin=False)
        res_close = run_backtest(data, configs, big, start=start, end=end, ruin=False, fill=FILL_SAME_CLOSE)
        sleeves[label] = {
            "vehicle": vehicle,
            "vol_target": SLEEVE_VOL_TARGET,
            "long_only": long_only,
            "short_via": short_via,
            "note": "conto di riferimento mille volte più grande, per togliere l'effetto del lotto minimo",
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
    payload.setdefault("meta", {})["model"] = model_signature(books, options_cfg, risk)
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
