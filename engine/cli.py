"""Command-line entry point: ``python -m engine.cli <comando>``.

Implemented here (phase 2, data layer):

* ``fetch [--snapshot] [--groups a,b]`` - run every source of ``config/data_sources.yaml``, archive the raw
  point-in-time snapshots and write ``state/health.json``;
* ``health`` - print the last ``state/health.json``;
* ``assemble`` - build the :class:`engine.data.market_data.MarketData` from the archived snapshots and print the
  table shapes, last dates and health summary.

Every other subcommand (``update``, ``eod``, ``weekly``, ``reset``, ``backtest``, ``export-site``, ``alerts``) is
WIRED but not implemented: it prints ``non ancora implementato (fase N)`` and exits with code 2. The wiring lives
in one place, :func:`build_parser`, and the bodies in :data:`NOT_IMPLEMENTED` - another phase replaces those
bodies without touching the parser.

All state is read from and written under ``$OPT_STATE_DIR`` (default ``./state``); user-facing text is Italian,
code and comments English (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from engine.core.config import Settings
from engine.core.store import StateStore
from engine.core.timeutil import iso

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2
EXIT_SKIP = 3
EXIT_UNHEALTHY = 3

# subcommand -> (phase number, one-line help). Another agent replaces these bodies; keep the wiring in one place.
NOT_IMPLEMENTED: dict[str, tuple[int, str]] = {
    "update": (4, "tick intraday: prezzi, stop, mark-to-market (ogni 30 minuti)"),
    "eod": (4, "dopo il settlement ICE: segnali, regime, previsioni, ordini"),
    "weekly": (5, "COT, rig count, retraining, backtest completo, report di validazione"),
    "reset": (5, "archivia l'epoca, va flat, riporta l'equity a 10 000 $"),
    "backtest": (5, "backtest completo sull'intero storico"),
    "export-site": (6, "costruisce i JSON compatti per la dashboard"),
    "alerts": (6, "apre/aggiorna le issue GitHub di allerta"),
}


def _settings() -> Settings:
    return Settings.from_env()


def _state(settings: Settings) -> StateStore:
    return StateStore(settings.state_dir)


def _raw_store(settings: Settings) -> Any:
    from engine.data.raw_store import RawStore

    return RawStore(settings.state_dir)


# --------------------------------------------------------------------------------------------------------------
# implemented commands
# --------------------------------------------------------------------------------------------------------------
def cmd_fetch(args: argparse.Namespace) -> int:
    """Run the data sources (optionally only some groups) and archive their snapshots."""
    from engine.data.fetch import Fetcher

    settings = _settings()
    if settings.offline:
        print("OPT_OFFLINE=1: fetch disabilitato (nessuna chiamata di rete)")
        return EXIT_UNHEALTHY
    groups = [g.strip() for g in str(args.groups).split(",") if g.strip()] if args.groups else None
    fetcher = Fetcher(settings, _raw_store(settings), _state(settings))
    report = fetcher.run_all(groups)
    started = datetime.now(tz=UTC)
    print(f"fetch {iso(report['checked_at'])}  gruppi: {', '.join(report['groups']) or '-'}")
    for group, entries in report["groups"].items():
        print(f"\n[{group}]")
        for entry, info in entries.items():
            adapters = ", ".join(
                f"{a['adapter']}:{a['status']}" for a in info.get("attempts", []) if a.get("status") != "skipped"
            )
            skipped = ", ".join(a["adapter"] for a in info.get("attempts", []) if a.get("status") == "skipped")
            print(
                f"  {entry:<18} {str(info['status']).upper():<6} righe={info.get('rows', 0):<7}"
                f" dato={info.get('data_asof') or '-'}"
            )
            if adapters:
                print(f"      tentativi: {adapters}" + (f" | saltati (chiave): {skipped}" if skipped else ""))
            if info.get("message"):
                print(f"      {info['message']}")
    print(
        f"\nstato complessivo: {str(report['overall']).upper()}"
        f"  (ok {report['ok']}, non disponibili {report['failed']})"
    )
    if args.snapshot:
        print(f"snapshot archiviati sotto {settings.state_dir / 'raw'}")
    print(f"health.json aggiornato ({(datetime.now(tz=UTC) - started).total_seconds():.1f}s per il report)")
    return 0 if report["overall"] != "red" else EXIT_UNHEALTHY


def cmd_health(args: argparse.Namespace) -> int:
    """Print the last health report written by ``fetch``."""
    settings = _settings()
    health = _state(settings).read_json("health.json")
    if not health:
        print("nessun health.json: esegui prima `python -m engine.cli fetch --snapshot`")
        return EXIT_UNHEALTHY
    print(f"controllato: {health.get('checked_at')}   stato complessivo: {str(health.get('overall')).upper()}")
    for source in health.get("sources", []):
        print(
            f"  {source.get('source')!s:<18} {str(source.get('status')).upper():<6}"
            f" righe={source.get('rows', 0):<7} dato={source.get('data_asof') or '-'}"
        )
        if args.verbose and source.get("message"):
            print(f"      {source['message']}")
    return 0 if health.get("overall") != "red" else EXIT_UNHEALTHY


def cmd_assemble(args: argparse.Namespace) -> int:
    """Build the MarketData from the archived snapshots and print a summary."""
    from engine.data.assemble import build_market_data, describe, last_rows

    settings = _settings()
    md = build_market_data(_raw_store(settings), settings)
    print("MarketData:")
    print(describe(md))
    if md.meta.get("missing"):
        print(f"  tabelle assenti: {', '.join(md.meta['missing'])}")
    if args.verbose:
        print("\nprezzi (ultime righe):")
        print(last_rows(md.prices, ["brent_front_close", "brent_cont", "brent_spot", "wti_spot", "ovx"], 5))
        if not md.curve.empty:
            print("\ncurva (ultima riga):")
            print(md.curve.tail(1).T.dropna().to_string())
        if not md.wpsr.empty:
            print("\nWPSR (ultima riga):")
            print(md.wpsr.tail(1).T.to_string())
        if not md.cot.empty:
            print("\nCOT (ultime righe per mercato):")
            print(md.cot.groupby("market").tail(1).to_string())
        if not md.news.empty:
            print("\nnotizie (ultime righe):")
            print(md.news.tail(3).to_string())
        if len(md.rigs):
            print(f"\nrig count: {md.rigs.iloc[-1]:.0f} ({md.rigs.index[-1].date()})")
    print("\nfonti per colonna:")
    for column, source in sorted(md.meta.get("source", {}).items()):
        print(f"  {column:<22} {source}")
    return 0


# --------------------------------------------------------------------------------------------------------------
# live jobs (phase 7) and reporting (phases 6 and 8)
# --------------------------------------------------------------------------------------------------------------
def _job_id(args: argparse.Namespace, name: str) -> str:
    """A stable id per run: the workflow passes --job-id, a manual run gets the current minute."""
    given = getattr(args, "job_id", None)
    if given:
        return str(given)
    from engine.core.timeutil import now_utc

    return f"{name}-{now_utc():%Y%m%dT%H%M}"


def _runner() -> Any:
    from engine.live.runner import LiveRunner

    return LiveRunner()


def _report(outcome: Any) -> int:
    """One line per job plus a compact detail block; nested dictionaries are summarised, never dumped."""
    print(f"[{outcome.status}] {outcome.message}")
    for key, value in (outcome.detail or {}).items():
        if key == "sources" and isinstance(value, dict):
            srcs = value.get("sources") or []
            by_status: dict[str, int] = {}
            for entry in srcs:
                by_status[str(entry.get("status"))] = by_status.get(str(entry.get("status")), 0) + 1
            degraded = [
                f"{entry.get('source')} ({entry.get('message', '')[:60]})"
                for entry in srcs
                if entry.get("status") != "green"
            ]
            counts = ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items()))
            print(f"  fonti: {counts} | complessivo {value.get('overall')}")
            for line in degraded:
                print(f"    - {line}")
            continue
        if isinstance(value, dict | list) and len(str(value)) > 200:
            print(f"  {key}: {type(value).__name__} con {len(value)} elementi")
            continue
        print(f"  {key}: {value}")
    return int(outcome.exit_code)


def cmd_update(args: argparse.Namespace) -> int:
    from engine.live import jobs

    return _report(jobs.update(_runner(), _job_id(args, "update"), force=bool(getattr(args, "force", False))))


def cmd_eod(args: argparse.Namespace) -> int:
    from datetime import date as _date

    from engine.live import jobs

    day = _date.fromisoformat(args.date) if getattr(args, "date", None) else None
    return _report(jobs.eod(_runner(), _job_id(args, "eod"), day=day, force=bool(getattr(args, "force", False))))


def cmd_weekly(args: argparse.Namespace) -> int:
    from engine.live import jobs

    return _report(jobs.weekly(_runner(), _job_id(args, "weekly")))


def cmd_reset(args: argparse.Namespace) -> int:
    from engine.live import jobs

    if getattr(args, "confirm", None) != "RESET":
        print("reset: serve --confirm RESET (nulla è stato modificato)")
        return EXIT_ERROR
    return _report(jobs.reset(_runner(), _job_id(args, "reset"), actor=str(getattr(args, "actor", None) or "cli")))


def cmd_alerts(args: argparse.Namespace) -> int:
    from engine.live import jobs

    repo = getattr(args, "repo", None) or ""
    return _report(jobs.alerts(_runner(), repo))


def cmd_export_site(args: argparse.Namespace) -> int:
    from pathlib import Path as _Path

    from engine.report.export_site import export_all

    runner = _runner()
    md = None
    try:
        md = runner.market_data()
    except Exception as exc:  # the site must still build (with nulls) when the data layer is unavailable
        print(f"attenzione: MarketData non disponibile ({exc}); esporto solo lo stato")
    res = export_all(runner.store, _Path(args.out), md=md, risk=runner.risk, settings=runner.settings)
    print(f"scritti {len(res.files)} file in {args.out}: {', '.join(res.files)}")
    for w in res.warnings:
        print(f"  attenzione: {w}")
    return EXIT_OK


def cmd_backtest(args: argparse.Namespace) -> int:
    from datetime import date as _date
    from pathlib import Path as _Path

    runner = _runner()
    md = runner.market_data()
    strategies = runner.strategies()
    start = _date.fromisoformat(args.start) if getattr(args, "start", None) else None
    end = _date.fromisoformat(args.end) if getattr(args, "end", None) else None
    out = _Path(args.report) if getattr(args, "report", None) else _Path("out/backtest")
    try:
        from engine.report.backtest_report import run_backtest_report

        summary = run_backtest_report(
            md=md, strategies=strategies, risk=runner.risk, start=start, end=end, out_dir=out, store=runner.store
        )
        print(f"report scritto in {out}")
        for key, value in (summary or {}).items():
            if not isinstance(value, dict | list):
                print(f"  {key}: {value}")
        return EXIT_OK
    except ImportError as exc:
        print(f"report completo non disponibile ({exc}); eseguo il backtest di base")
    from engine.backtest.runner import run_backtest

    res = run_backtest(md, strategies, runner.risk, start=start, end=end)
    for name, perf in sorted(res.summary.items()):
        print(f"  {name:<10} Sharpe {perf.sharpe:>6.2f}  CAGR {perf.cagr:>7.1%}  maxDD {perf.max_drawdown:>7.1%}")
    return EXIT_OK


# --------------------------------------------------------------------------------------------------------------
# placeholders (only for commands no phase implements yet)
# --------------------------------------------------------------------------------------------------------------
def _placeholder(name: str, phase: int) -> Callable[[argparse.Namespace], int]:
    def run(_args: argparse.Namespace) -> int:
        print(f"{name}: non ancora implementato (fase {phase})")
        return EXIT_NOT_IMPLEMENTED

    return run


# --------------------------------------------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """The single place where every subcommand is wired."""
    parser = argparse.ArgumentParser(prog="engine.cli", description="Brent paper-trading engine (solo carta)")
    parser.add_argument("--log-level", default="WARNING", help="DEBUG, INFO, WARNING (default), ERROR")
    sub = parser.add_subparsers(dest="command", required=True, metavar="comando")

    p_fetch = sub.add_parser("fetch", help="scarica tutte le fonti e archivia gli snapshot point-in-time")
    p_fetch.add_argument("--snapshot", action="store_true", help="archivia gli snapshot grezzi (comportamento base)")
    p_fetch.add_argument("--groups", default=None, help="elenco separato da virgole (es. prices,news)")
    p_fetch.set_defaults(func=cmd_fetch)

    p_health = sub.add_parser("health", help="mostra l'ultimo state/health.json")
    p_health.add_argument("-v", "--verbose", action="store_true", help="mostra anche i messaggi per fonte")
    p_health.set_defaults(func=cmd_health)

    p_asm = sub.add_parser("assemble", help="costruisce MarketData dagli snapshot e stampa un riepilogo")
    p_asm.add_argument("-v", "--verbose", action="store_true", help="stampa anche le ultime righe di ogni tabella")
    p_asm.set_defaults(func=cmd_assemble)

    # ---- wired but not implemented (another phase fills these in) --------------------------------------------
    p_update = sub.add_parser("update", help=NOT_IMPLEMENTED["update"][1])
    p_update.add_argument("--job-id", default=None)
    p_eod = sub.add_parser("eod", help=NOT_IMPLEMENTED["eod"][1])
    p_eod.add_argument("--job-id", default=None)
    p_weekly = sub.add_parser("weekly", help=NOT_IMPLEMENTED["weekly"][1])
    p_weekly.add_argument("--job-id", default=None)
    p_reset = sub.add_parser("reset", help=NOT_IMPLEMENTED["reset"][1])
    p_reset.add_argument("--confirm", default=None, help="deve valere RESET")
    p_reset.add_argument("--job-id", default=None)
    p_reset.add_argument("--actor", default=None)
    p_back = sub.add_parser("backtest", help=NOT_IMPLEMENTED["backtest"][1])
    p_back.add_argument("--start", default=None)
    p_back.add_argument("--end", default=None)
    p_back.add_argument("--report", default=None)
    p_export = sub.add_parser("export-site", help=NOT_IMPLEMENTED["export-site"][1])
    p_export.add_argument("--out", default="site-data")
    p_alerts = sub.add_parser("alerts", help=NOT_IMPLEMENTED["alerts"][1])
    p_alerts.add_argument("--repo", default=None)
    p_update.add_argument("--force", action="store_true", help="esegui anche fuori orario di mercato")
    p_eod.add_argument("--date", default=None, help="data di negoziazione londinese (YYYY-MM-DD)")
    p_eod.add_argument("--force", action="store_true", help="esegui anche prima del settlement")
    p_update.set_defaults(func=cmd_update)
    p_eod.set_defaults(func=cmd_eod)
    p_weekly.set_defaults(func=cmd_weekly)
    p_reset.set_defaults(func=cmd_reset)
    p_back.set_defaults(func=cmd_backtest)
    p_export.set_defaults(func=cmd_export_site)
    p_alerts.set_defaults(func=cmd_alerts)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
