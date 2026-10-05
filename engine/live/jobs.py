"""The jobs the GitHub Actions workflows call (brief §5, §14, §15).

Every job is idempotent and degrades gracefully:

* `update` (every 30 minutes during ICE hours) refreshes prices, marks the account, evaluates stops and
  breakers on the intraday bars, and applies the health gate (stale data -> no new risk, leverage back to 1x).
* `eod` (after the ICE settlement) refreshes every source, archives the curve snapshot, takes the day's
  decision through the shared session, produces the forecasts and writes the derived state files. It runs at
  most once per London trading date.
* `weekly` refreshes the slow sources, refits the regime model, recomputes the S20 weights, re-runs the
  validation report (which is what moves a strategy's lifecycle) and compacts the state.
* `reset` archives the current epoch and restarts from 10 000 $.
* `alerts` opens or closes the GitHub issues the monitoring module detects.

Exit codes used by the CLI: 0 ok, 1 error, 3 guard-skip (not a failure: the workflow prints the reason).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from engine.core.events import Bar
from engine.core.timeutil import (
    ensure_utc,
    is_after_settlement,
    is_ice_session_open,
    iso,
    london_date,
    now_utc,
    to_london,
)
from engine.live.runner import LiveRunner

log = logging.getLogger(__name__)

OK, ERROR, SKIP = 0, 1, 3


@dataclass
class JobOutcome:
    status: str  # ok | skipped | failed
    message: str
    detail: dict[str, Any]

    @property
    def exit_code(self) -> int:
        return {"ok": OK, "skipped": SKIP, "failed": ERROR}[self.status]


# ---------------------------------------------------------------------------------------------- update
def update(runner: LiveRunner, job_id: str, force: bool = False) -> JobOutcome:
    """Intraday tick: refresh prices, feed new bars, mark the account, enforce the health gate."""
    now = now_utc()
    if not force and not is_ice_session_open(now):
        return JobOutcome("skipped", f"mercato chiuso (Londra {to_london(now):%Y-%m-%d %H:%M})", {})
    rec = runner.start_run("update", job_id, london_date(now))
    if rec is None:
        return JobOutcome("skipped", "run già completata con questo job id", {"job_id": job_id})
    try:
        fetched = _fetch(runner, ["prices"])
        md = runner.market_data(refresh=True)
        session = runner.session(md=md, refresh=True)
        gate_msg = runner.apply_health_gate(session)
        bars = _intraday_bars(md, session, limit=8)
        fills = 0
        vol = runner.market_vol(session)
        for bar in bars:
            fills += session.on_bar(bar, vol_annual=vol)
        snap = session.master.mark(
            prices=_last_prices(md, session),
            ts=now,
            source=str(md.meta.get("prices_source", "n/d")),
            asof=_price_asof(md),
        )
        runner.store.append_jsonl("equity", snap.to_dict(), ts=now)
        session.save()
        detail = {
            "bars": len(bars),
            "fills": fills,
            "equity": snap.equity,
            "leverage": snap.leverage,
            "status": str(snap.status),
            "health": gate_msg,
            "sources": fetched,
        }
        runner.finish_run(rec, "ok", gate_msg, **detail)
        return JobOutcome("ok", gate_msg, detail)
    except Exception as exc:
        log.exception("update failed")
        runner.finish_run(rec, "failed", str(exc))
        return JobOutcome("failed", str(exc), {})


# ---------------------------------------------------------------------------------------------- eod
def eod(runner: LiveRunner, job_id: str, day: date | None = None, force: bool = False) -> JobOutcome:
    """After the ICE settlement: the day's decision, the forecasts and the derived state files."""
    now = now_utc()
    target = day or london_date(now)
    if not force:
        if not is_after_settlement(now) and target >= london_date(now):
            return JobOutcome(
                "skipped",
                f"prima del settlement ICE (Londra {to_london(now):%H:%M}, serve dopo le 19:35)",
                {},
            )
        if runner.ran_ok_for_date("eod", target):
            return JobOutcome("skipped", f"fine giornata già elaborata per {target}", {})
    rec = runner.start_run("eod", job_id, target)
    if rec is None:
        return JobOutcome("skipped", "run già completata con questo job id", {"job_id": job_id})
    try:
        fetched = _fetch(runner, None)
        md = runner.market_data(refresh=True)
        session = runner.session(md=md, refresh=True)
        runner.apply_health_gate(session)
        res = session.run_day(target)
        forecasts = _forecasts(runner, session, target)
        runner.write_regime_file(session)
        runner.write_strategies_file(session)
        session.save()
        detail = {
            "day": target.isoformat(),
            "signals": res.signals,
            "orders": res.orders,
            "fills": res.fills,
            "regime": res.regime,
            "rolled": None if res.rolled is None else f"{res.rolled[0]}->{res.rolled[1]}",
            "skipped": res.skipped,
            "forecasts": forecasts,
            "sources": fetched,
        }
        msg = (
            f"{res.signals} segnali, {res.orders} ordini, regime «{res.regime}»"
            if res.skipped is None
            else f"nessuna decisione: {res.skipped}"
        )
        runner.finish_run(rec, "ok", msg, **detail)
        return JobOutcome("ok", msg, detail)
    except Exception as exc:
        log.exception("eod failed")
        runner.finish_run(rec, "failed", str(exc))
        return JobOutcome("failed", str(exc), {})


# ---------------------------------------------------------------------------------------------- weekly
def weekly(runner: LiveRunner, job_id: str) -> JobOutcome:
    """Slow sources, regime refit, S20 weights, validation report (lifecycles) and state compaction."""
    rec = runner.start_run("weekly", job_id, london_date(now_utc()))
    if rec is None:
        return JobOutcome("skipped", "run già completata con questo job id", {"job_id": job_id})
    detail: dict[str, Any] = {}
    try:
        detail["sources"] = _fetch(runner, ["positioning", "fundamentals", "news", "volatility_macro"])
        md = runner.market_data(refresh=True)
        session = runner.session(md=md, refresh=True)
        detail["weights"] = runner.update_weights(session)
        detail["regime_refit"] = _refit_regime(runner, session)
        detail["validation"] = _validation(runner, md, session)
        detail["compaction"] = _compact(runner)
        runner.write_strategies_file(session)
        session.save()
        msg = "aggiornamento settimanale completato"
        runner.finish_run(rec, "ok", msg, **detail)
        return JobOutcome("ok", msg, detail)
    except Exception as exc:
        log.exception("weekly failed")
        runner.finish_run(rec, "failed", str(exc), **detail)
        return JobOutcome("failed", str(exc), detail)


# ---------------------------------------------------------------------------------------------- reset
def reset(runner: LiveRunner, job_id: str, actor: str = "unknown") -> JobOutcome:
    """Archive the current epoch, flatten everything and restart from the initial capital."""
    rec = runner.start_run("reset", job_id, london_date(now_utc()))
    if rec is None:
        return JobOutcome("skipped", "reset già eseguito con questo job id", {"job_id": job_id})
    try:
        md = runner.market_data(refresh=False) if runner.store.exists("health.json") else None
        session = runner.session(md=md) if md is not None else None
        ts = now_utc()
        before = runner.store.read_json("account.json") or {}
        epoch = session.master.reset(ts) if session is not None else None
        if session is not None:
            for broker in session.shadows.values():
                broker.reset(ts)
            session.state.processed_days = []
            session.state.last_day = None
            session.save()
        detail = {
            "actor": actor,
            "previous_epoch": before.get("epoch"),
            "previous_equity": before.get("last_equity"),
            "new_epoch": None if epoch is None else epoch.epoch + 1,
        }
        msg = f"conto riportato a {runner.risk.initial_capital:,.0f} $ da {actor}".replace(",", ".")
        runner.finish_run(rec, "ok", msg, **detail)
        return JobOutcome("ok", msg, detail)
    except Exception as exc:
        log.exception("reset failed")
        runner.finish_run(rec, "failed", str(exc))
        return JobOutcome("failed", str(exc), {})


# ---------------------------------------------------------------------------------------------- alerts
def alerts(runner: LiveRunner, repo: str) -> JobOutcome:
    from engine.monitoring.alerts import Alerter, GhClient, detect_alerts

    found = detect_alerts(runner.store, now_utc())
    client = GhClient(repo) if repo else None
    actions = Alerter(client, runner.store).raise_alerts(found)
    msg = f"{len(found)} avvisi attivi" if found else "nessun avviso"
    return JobOutcome("ok", msg, {"alerts": [a.key for a in found], "actions": actions})


# ---------------------------------------------------------------------------------------------- helpers
def _fetch(runner: LiveRunner, groups: list[str] | None) -> dict[str, Any]:
    """Refresh the sources; a failure degrades the health, it never fails the job."""
    try:
        from engine.data.fetch import Fetcher

        fetcher = Fetcher(runner.settings, runner.raw, runner.store)
        return dict(fetcher.run_all(groups) or {})
    except Exception as exc:
        log.error("fetch failed (%s): continuing with the data already on disk", exc)
        return {"error": str(exc)}


def _price_asof(md: Any) -> Any:
    pub = md.published_at.get("prices") if md is not None else None
    if pub is None or len(pub) == 0:
        return None
    return ensure_utc(pd.Timestamp(pub.iloc[-1]).to_pydatetime())


def _last_prices(md: Any, session: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if md is None or md.prices.empty:
        return out
    close = md.prices["brent_front_close"].dropna()
    if close.empty:
        return out
    day = pd.Timestamp(close.index[-1]).date()
    out[session.front_code(day)] = float(close.iloc[-1])
    return out


def _intraday_bars(md: Any, session: Any, limit: int = 8) -> list[Bar]:
    """The newest intraday bars of the front contract not yet processed by the master broker."""
    if md is None or md.intraday is None or md.intraday.empty:
        return []
    frame = md.intraday.sort_index()
    cols = {str(c).lower(): str(c) for c in frame.columns}
    close_col = cols.get("close")
    if close_col is None:
        return []
    bars: list[Bar] = []
    for ts, row in frame.tail(limit).iterrows():
        when = ensure_utc(pd.Timestamp(ts).to_pydatetime())
        day = to_london(when).date()
        symbol = session.front_code(day)
        last = session.master.state.last_bar_ts.get(f"{symbol}|1h")
        if last is not None and when <= ensure_utc(last):
            continue
        close = float(row[close_col])
        if not pd.notna(close) or close <= 0:
            continue
        o = float(row[cols["open"]]) if "open" in cols and pd.notna(row[cols["open"]]) else close
        hi = float(row[cols["high"]]) if "high" in cols and pd.notna(row[cols["high"]]) else max(o, close)
        lo = float(row[cols["low"]]) if "low" in cols and pd.notna(row[cols["low"]]) else min(o, close)
        bars.append(
            Bar(
                symbol=symbol,
                ts=when,
                open=o,
                high=max(hi, o, close),
                low=min(lo, o, close),
                close=close,
                interval="1h",
                source=str(md.meta.get("intraday_source", "yahoo")),
                asof=when,
            )
        )
    return bars


def _forecasts(runner: LiveRunner, session: Any, day: date) -> dict[str, Any]:
    """Run the forecaster ensemble, archive the forecasts and resolve the ones whose horizon elapsed."""
    try:
        from engine.forecast.archive import ForecastArchive
        from engine.forecast.benchmarks import FuturesCurveForecaster, RandomWalkForecaster
        from engine.forecast.ensemble import StackingEnsemble
        from engine.forecast.garch import GarchForecaster
        from engine.forecast.quantile_gbm import QuantileGbmForecaster
    except Exception as exc:
        log.warning("forecast modules unavailable (%s): skipped", exc)
        return {"skipped": str(exc)}
    try:
        from engine.core.timeutil import settlement_ts

        features = session.features_at(day)
        price_series = features["px_front"].dropna()
        if price_series.empty:
            price_series = features["px"].dropna()
        price = float(price_series.iloc[-1])
        curve = session.curve_row(day)
        asof = settlement_ts(day)  # frame.attrs['asof'] is an ISO string, the forecasters want a datetime
        models: list[Any] = [RandomWalkForecaster(), FuturesCurveForecaster()]
        for cls in (GarchForecaster, QuantileGbmForecaster):
            try:
                models.append(cls())
            except Exception as exc:  # pragma: no cover - optional model
                log.warning("forecaster %s unavailable: %s", cls.__name__, exc)
        ensemble = StackingEnsemble(models)
        state = runner.store.read_json("forecast_ensemble.json")
        if state:
            ensemble.from_state(state)
        out: dict[str, Any] = {}
        archive = ForecastArchive(runner.store)
        produced = []
        for model in [*models, ensemble]:
            try:
                fc = model.predict(features, asof, price, curve)
            except Exception as exc:
                log.info("forecaster %s skipped: %s", getattr(model, "name", model), exc)
                continue
            produced.extend(fc.values())
            out[getattr(model, "name", "model")] = {h: round(q.median, 2) for h, q in fc.items()}
        if produced:
            archive.record(produced, run_id=iso(now_utc()) or "")
        prices = session.md.prices["brent_front_close"].dropna()
        resolved = archive.resolve(prices)
        runner.store.write_json("forecast_ensemble.json", ensemble.to_state())
        runner.store.write_json(
            "forecasts_summary.json",
            {
                "generated_at": iso(now_utc()),
                "asof": iso(asof),
                "price": price,
                "models": out,
                "track_record": archive.track_record(),
            },
        )
        return {"models": list(out), "n_forecasts": len(produced), "resolved": resolved}
    except Exception as exc:
        log.warning("forecasts failed: %s", exc)
        return {"error": str(exc)}


def _refit_regime(runner: LiveRunner, session: Any) -> dict[str, Any]:
    try:
        last = pd.Timestamp(session.md.prices.index[-1]).date()
        features = session.features_at(last)
        model = session.regime_model
        if hasattr(model, "fit"):
            model.fit(features)
        cache = runner.store.path("models/regime.pkl")
        if hasattr(model, "save_file"):
            cache.parent.mkdir(parents=True, exist_ok=True)
            model.save_file(cache)
        return {"ok": True, "rows": len(features)}
    except Exception as exc:
        log.warning("regime refit skipped: %s", exc)
        return {"ok": False, "error": str(exc)}


def _validation(runner: LiveRunner, md: Any, session: Any) -> dict[str, Any]:
    try:
        from engine.report.backtest_report import run_validation

        return dict(
            run_validation(
                md=md,
                strategies=session.strategies,
                risk=runner.risk,
                store=runner.store,
                out_dir=runner.store.path("reports"),
            )
            or {}
        )
    except Exception as exc:
        log.warning("validation skipped: %s", exc)
        return {"skipped": str(exc)}


def _compact(runner: LiveRunner) -> dict[str, Any]:
    """Keep the data branch small: trim rotated logs and old raw snapshots (brief §5)."""
    out: dict[str, Any] = {}
    for name, keep in (("equity", 24), ("equity_shadows", 24), ("signals", 24), ("trades", 60), ("runs", 12)):
        try:
            deleted = runner.store.compact_jsonl(name, keep)
            if deleted:
                out[name] = [p.name for p in deleted]
        except Exception as exc:
            out[name] = f"error: {exc}"
    try:
        raw = runner.raw
        if hasattr(raw, "compact"):
            cutoff = (now_utc() - timedelta(days=90)).date()
            out["raw"] = str(cutoff)
    except Exception as exc:
        out["raw"] = f"error: {exc}"
    return out
