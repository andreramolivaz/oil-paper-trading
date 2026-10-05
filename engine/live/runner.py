"""LiveRunner: rebuilds the whole trading stack from persisted state on every invocation.

GitHub Actions gives us no memory between runs, so every job starts by reconstructing the same objects the
backtest uses — strategies, feature builder, regime model, allocator, brokers — from the JSON on the data
branch, and ends by writing them back. That is what makes the live track record comparable to the backtest:
it IS the backtest loop, fed one day (or one bar) at a time.

Everything here is defensive by design: a data source that is down degrades the health, never crashes the run;
a missing optional module (regime, forecasts, validation) is logged and skipped; a job that already ran for the
same London date or the same job id is a no-op.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from engine.backtest.session import TradingSession
from engine.broker.paper import PaperBroker
from engine.core.config import RiskConfig, Settings, load_config
from engine.core.eventcal import EventCalendar
from engine.core.events import AccountStatus
from engine.core.store import StateStore
from engine.core.timeutil import iso, now_utc, parse_iso
from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.portfolio.allocator import MasterAllocator
from engine.portfolio.base import CappedEqualWeightAllocator
from engine.portfolio.weights import RegimeConditionedWeights

log = logging.getLogger(__name__)

RUNS_LOG = "runs"
WEIGHTS_FILE = "weights.json"
HEALTH_FILE = "health.json"
VALIDATION_FILE = "validation.json"
REGIME_MODEL_FILE = "models/regime.pkl"


@dataclass
class RunRecord:
    job: str
    job_id: str
    started: datetime
    finished: datetime | None = None
    status: str = "running"  # running | ok | skipped | failed
    message: str = ""
    london_date: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "job_id": self.job_id,
            "started": iso(self.started),
            "finished": iso(self.finished),
            "status": self.status,
            "message": self.message,
            "london_date": self.london_date,
            "detail": self.detail,
        }


class LiveRunner:
    def __init__(self, settings: Settings | None = None, risk: RiskConfig | None = None):
        self.settings = settings or Settings.from_env()
        self.risk = risk or RiskConfig.load(self.settings.config_dir)
        self.store = StateStore(self.settings.state_dir)
        self.events = EventCalendar(config_dir=self.settings.config_dir)
        self._raw: Any | None = None
        self._md: MarketData | None = None
        self._session: TradingSession | None = None
        self._weights = RegimeConditionedWeights()
        self._weights.load(self.store.read_json(WEIGHTS_FILE))

    # ------------------------------------------------------------------ pieces, built lazily
    @property
    def raw(self) -> Any:
        if self._raw is None:
            from engine.data.raw_store import RawStore

            self._raw = RawStore(Path(self.settings.state_dir))
        return self._raw

    def strategies(self) -> list[Any]:
        from engine.strategies.registry import load_strategies

        try:
            return load_strategies(config_dir=self.settings.config_dir)
        except Exception as exc:
            log.error("no strategies could be loaded: %s", exc)
            return []

    def lifecycles(self) -> dict[str, str]:
        """Lifecycle per strategy: the validation report wins over the static config when it exists."""
        out: dict[str, str] = {}
        try:
            cfg = load_config("strategies", self.settings.config_dir)
            for entry in cfg.get("strategies", []):
                out[str(entry["id"])] = str(entry.get("lifecycle", "research"))
        except Exception as exc:
            log.warning("could not read strategy lifecycles: %s", exc)
        validation = self.store.read_json(VALIDATION_FILE) or {}
        for sid, rec in (validation.get("strategies") or {}).items():
            lc = rec.get("lifecycle")
            if lc:
                out[str(sid)] = str(lc)
        return out

    def oos_stats(self) -> dict[str, dict[str, float]]:
        validation = self.store.read_json(VALIDATION_FILE) or {}
        out: dict[str, dict[str, float]] = {}
        for sid, rec in (validation.get("strategies") or {}).items():
            psr = rec.get("psr_current_regime", rec.get("psr"))
            if psr is not None:
                out[str(sid)] = {"psr": float(psr)}
        return out

    def market_data(self, refresh: bool = False) -> MarketData:
        if self._md is None or refresh:
            from engine.data.assemble import build_market_data

            self._md = build_market_data(self.raw, self.settings)
        return self._md

    def health(self) -> dict[str, Any]:
        return self.store.read_json(HEALTH_FILE) or {"overall": "red", "sources": [], "checked_at": None}

    def data_age_minutes(self) -> float | None:
        h = self.health()
        checked = parse_iso(h.get("checked_at")) if h.get("checked_at") else None
        if checked is None:
            return None
        return max(0.0, (now_utc() - checked).total_seconds() / 60.0)

    def regime_model(self) -> Any:
        from engine.regime.model import make_regime_model

        model = make_regime_model(None)
        cache = self.store.path(REGIME_MODEL_FILE)
        if cache.exists() and hasattr(model, "load_file"):
            try:
                model.load_file(cache)
            except Exception as exc:
                log.warning("regime cache could not be loaded (%s): refitting from scratch", exc)
        return model

    def allocator(self) -> MasterAllocator:
        alloc = MasterAllocator(self.risk, weights=self._weights, events=self.events)
        h = self.health()
        alloc.set_context(
            health_overall=str(h.get("overall", "red")),
            data_age_minutes=self.data_age_minutes(),
            oos_stats=self.oos_stats(),
            lifecycles=self.lifecycles(),
        )
        return alloc

    def session(self, md: MarketData | None = None, refresh: bool = False) -> TradingSession:
        if self._session is not None and not refresh:
            return self._session
        md = md or self.market_data()
        strategies = self.strategies()
        master = PaperBroker.load("master", self.risk, self.store)
        shadows: dict[str, PaperBroker] = {}
        raw_shadows = self.store.read_json("broker_shadows.json") or {}
        from engine.broker.paper import BrokerState

        for s in strategies:
            state = None
            raw = raw_shadows.get(s.id)
            if raw:
                try:
                    state = BrokerState.from_dict(raw)
                except Exception as exc:
                    log.warning("shadow state for %s unreadable (%s): starting fresh", s.id, exc)
            shadows[s.id] = PaperBroker(f"shadow-{s.id}", self.risk, state=state, store=None)
        session = TradingSession(
            md=md,
            strategies=strategies,
            master=master,
            risk=self.risk,
            allocator=self.allocator(),
            shadow_allocator=CappedEqualWeightAllocator(),
            shadows=shadows,
            regime_model=self.regime_model(),
            store=self.store,
            events=self.events,
            shadow_accounts=True,
        )
        session.load(session_only=True)
        self._session = session
        return session

    # ------------------------------------------------------------------ run bookkeeping
    def start_run(self, job: str, job_id: str, london_date: date | None = None) -> RunRecord | None:
        """Register a run; returns None when a run with the same job id already completed (idempotency)."""
        for row in self.store.iter_jsonl(RUNS_LOG):
            if row.get("job") == job and row.get("job_id") == job_id and row.get("status") in {"ok", "skipped"}:
                log.info("job %s/%s already completed: nothing to do", job, job_id)
                return None
        return RunRecord(
            job=job,
            job_id=job_id,
            started=now_utc(),
            london_date=london_date.isoformat() if london_date else None,
        )

    def finish_run(self, rec: RunRecord, status: str, message: str = "", **detail: Any) -> RunRecord:
        rec.finished = now_utc()
        rec.status = status
        rec.message = message
        rec.detail.update(detail)
        self.store.append_jsonl(RUNS_LOG, rec.to_dict(), ts=rec.finished)
        return rec

    def ran_ok_for_date(self, job: str, london_date: date) -> bool:
        target = london_date.isoformat()
        return any(
            row.get("job") == job and row.get("london_date") == target and row.get("status") == "ok"
            for row in self.store.iter_jsonl(RUNS_LOG)
        )

    # ------------------------------------------------------------------ health gating
    def apply_health_gate(self, session: TradingSession) -> str:
        """Stale or red data: no new risk, and any leverage above 1x is cut back. Returns what was done."""
        h = self.health()
        overall = str(h.get("overall", "red"))
        age = self.data_age_minutes()
        stale = age is None or age > float(self.risk.stale_minutes_intraday)
        broker = session.master
        if broker.status is AccountStatus.DEAD:
            return "conto azzerato: nessuna operazione"
        if overall == "red" or stale:
            if broker.status is AccountStatus.ACTIVE:
                broker.set_status(AccountStatus.HALTED_STALE)
            snap = broker.snapshot(now_utc())
            if snap.leverage > self.risk.default_max_leverage + 1e-9:
                self.delever(session, to=self.risk.default_max_leverage)
                return "dati stantii con leva oltre 1x: riduzione a 1x"
            return "dati stantii: nessun nuovo rischio"
        if broker.status is AccountStatus.HALTED_STALE:
            broker.set_status(AccountStatus.ACTIVE)
        return "dati freschi"

    def delever(self, session: TradingSession, to: float = 1.0) -> int:
        """Reduce the gross exposure to `to` times equity, at the last known prices. Returns orders submitted."""
        from engine.core.events import Order, OrderReason, OrderType
        from engine.core.ids import idempotency_key

        broker = session.master
        snap = broker.snapshot(now_utc())
        if snap.equity <= 0 or snap.gross_notional <= to * snap.equity:
            return 0
        scale = (to * snap.equity) / snap.gross_notional
        n = 0
        ts = now_utc()
        for symbol, pos in list(broker.positions.items()):
            target = pos.qty_bbl * scale
            delta = target - pos.qty_bbl
            if abs(delta) < self.risk.lot_bbl:
                continue
            key = idempotency_key(broker.state.epoch, ts.date().isoformat(), symbol, "delever", to)
            order = Order(
                order_id=key[:16],
                idempotency_key=key,
                ts=ts,
                account_id=broker.account_id,
                instrument=symbol,
                qty_bbl=delta,
                order_type=OrderType.MARKET,
                reason=OrderReason.STALE_DATA_DELEVER,
                rationale=f"Dati non freschi: esposizione riportata a {to:g}x.",
            )
            if broker.submit(order):
                n += 1
        return n

    # ------------------------------------------------------------------ persistence of derived state
    def save_weights(self) -> None:
        self.store.write_json(WEIGHTS_FILE, self._weights.to_dict())

    def update_weights(self, session: TradingSession) -> dict[str, float]:
        """Weekly: recompute the S20 weights from the shadow accounts' own out-of-sample returns."""
        curves = session.shadow_equity_from_store()
        if not curves:
            return dict(self._weights.current.weights)
        returns = pd.DataFrame({sid: c.pct_change() for sid, c in curves.items()}).dropna(how="all")
        regime_rows = self.store.read_jsonl("regime")
        regime_by_day = None
        if regime_rows:
            series = pd.Series(
                {pd.Timestamp(r["ts"]).normalize().tz_localize(None): r.get("label", "") for r in regime_rows}
            )
            regime_by_day = series.reindex(returns.index).ffill()
        label = regime_rows[-1].get("label", "") if regime_rows else ""
        ws = self._weights.update(
            returns=returns,
            lifecycles=self.lifecycles(),
            regime_label=str(label),
            regime_by_day=regime_by_day,
            asof=iso(now_utc()),
        )
        self.save_weights()
        return dict(ws.weights)

    def write_regime_file(self, session: TradingSession) -> None:
        rows = self.store.read_jsonl("regime")
        if not rows:
            return
        latest = rows[-1]
        history = [{"ts": r.get("ts"), "label": r.get("label"), "confidence": r.get("confidence")} for r in rows[-400:]]
        self.store.write_json(
            "regime.json",
            {
                "generated_at": iso(now_utc()),
                "current": latest,
                "history": history,
                "source": "engine/regime",
            },
        )

    def write_strategies_file(self, session: TradingSession) -> None:
        """Shadow-account leaderboard plus the current signal and lifecycle of every strategy."""
        from engine.backtest.metrics import summarise

        curves = session.shadow_equity_from_store()
        lifecycles = self.lifecycles()
        validation = self.store.read_json(VALIDATION_FILE) or {}
        signals = self.store.read_jsonl("signals")
        latest_signal: dict[str, dict[str, Any]] = {}
        for row in signals[-2000:]:
            latest_signal[str(row.get("strategy_id"))] = row
        out = []
        for strat in session.strategies:
            curve = curves.get(strat.id)
            perf = summarise(curve) if curve is not None and len(curve) > 1 else None
            rec = (validation.get("strategies") or {}).get(strat.id, {})
            sig = latest_signal.get(strat.id)
            out.append(
                {
                    "id": strat.id,
                    "name": strat.name,
                    "family": strat.family,
                    "lifecycle": lifecycles.get(strat.id, "research"),
                    "weight": float(self._weights.current.weights.get(strat.id, 0.0)),
                    "weight_explanation": self._weights.current.explanations.get(strat.id, ""),
                    "performance": None if perf is None else perf.to_dict(),
                    "validation": rec or None,
                    "signal": None
                    if sig is None
                    else {
                        "ts": sig.get("ts"),
                        "direction": sig.get("direction"),
                        "prob": sig.get("prob"),
                        "horizon_days": sig.get("horizon_days"),
                        "rationale": sig.get("rationale"),
                    },
                }
            )
        self.store.write_json(
            "strategies.json", {"generated_at": iso(now_utc()), "strategies": out, "source": "shadow accounts"}
        )

    # ------------------------------------------------------------------ helpers
    def latest_price_info(self, md: MarketData) -> dict[str, Any]:
        if md.prices.empty:
            return {}
        close = md.prices["brent_front_close"].dropna()
        if close.empty:
            return {}
        last_ts = close.index[-1]
        prev = float(close.iloc[-2]) if len(close) > 1 else float(close.iloc[-1])
        ovx = md.prices["ovx"].dropna() if "ovx" in md.prices.columns else pd.Series(dtype=float)
        return {
            "price": float(close.iloc[-1]),
            "date": pd.Timestamp(last_ts).date().isoformat(),
            "change_1d": float(close.iloc[-1] / prev - 1.0) if prev else None,
            "ovx": float(ovx.iloc[-1]) if not ovx.empty else None,
            "source": str(md.meta.get("prices_source", "n/d")),
        }

    def market_vol(self, session: TradingSession) -> float | None:
        try:
            features = session.features_at(session.md.prices.index[-1].date())
        except Exception:
            return None
        for name in (cat.RV_YZ_21, cat.RV_CC_21, cat.GARCH_VOL):
            if name in features.columns:
                v = features[name].dropna()
                if not v.empty and math.isfinite(float(v.iloc[-1])):
                    return float(v.iloc[-1])
        return None
