"""Build the JSON the dashboard reads (contract: docs/SITE_DATA.md).

Rules that matter more than the code:

* **Nothing is invented.** A value we do not have is `null`, never a placeholder, never a stale number passed
  off as current. Every block carries `source` and `asof` so the page can show where the number came from.
* **Strict JSON.** NaN and Infinity are converted to `null` before writing (JavaScript's JSON.parse rejects
  them, and a silent NaN in a chart is worse than a gap).
* **Small files.** Long daily series are downsampled to weekly beyond two years, and the trade log is capped,
  so the data branch and the page stay light.
* **Approximations are labelled** with `approx: true`, which the UI renders as "≈".
"""

from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.config import RiskConfig, Settings
from engine.core.eventcal import EventCalendar
from engine.core.store import StateStore, dumps
from engine.core.timeutil import iso, now_utc
from engine.data.market_data import MarketData
from engine.monitoring.alerts import cron_lag_minutes

log = logging.getLogger(__name__)

DISCLAIMER = (
    "Simulazione a scopo di studio su dati reali. Nessun consiglio finanziario, nessun ordine reale, "
    "nessun broker collegato."
)
MAX_TRADES = 500
RECENT_DAILY_YEARS = 2


def clean(obj: Any) -> Any:
    """Recursively replace NaN/Infinity with None so the output is strict JSON."""
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean(v) for v in obj]
    if isinstance(obj, pd.Timestamp | datetime):
        return iso(obj if isinstance(obj, datetime) else obj.to_pydatetime())
    if isinstance(obj, date):
        return obj.isoformat()
    if obj is None or isinstance(obj, str | int | bool):
        return obj
    if hasattr(obj, "item"):  # numpy scalar
        try:
            return clean(obj.item())
        except Exception:
            return None
    if hasattr(obj, "to_dict"):
        return clean(obj.to_dict())
    return obj


@dataclass
class ExportResult:
    files: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"files": self.files, "warnings": self.warnings}


class SiteExporter:
    def __init__(
        self,
        store: StateStore,
        out_dir: Path,
        md: MarketData | None = None,
        risk: RiskConfig | None = None,
        settings: Settings | None = None,
    ):
        self.store = store
        self.out = Path(out_dir)
        self.md = md
        self.risk = risk or RiskConfig.load()
        self.settings = settings
        self.events = EventCalendar()
        self.warnings: list[str] = []
        self.written: list[str] = []
        self.generated_at = now_utc()

    # ------------------------------------------------------------------ io
    def _write(self, name: str, payload: Any, indent: int | None = None) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        body = dumps(clean(payload), indent=indent)
        (self.out / name).write_text(body + "\n", encoding="utf-8")
        self.written.append(name)

    def _stamp(self, **extra: Any) -> dict[str, Any]:
        return {"generated_at": iso(self.generated_at), **extra}

    def _warn(self, msg: str) -> None:
        log.warning(msg)
        self.warnings.append(msg)

    # ------------------------------------------------------------------ state readers
    def account(self) -> dict[str, Any]:
        return self.store.read_json("account.json") or {}

    def positions(self) -> list[dict[str, Any]]:
        return list(self.store.read_json("positions.json") or [])

    def health(self) -> dict[str, Any]:
        return self.store.read_json("health.json") or {}

    def equity_frame(self) -> pd.DataFrame:
        rows = self.store.read_jsonl("equity")
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows)
        if "ts" not in frame.columns:
            return pd.DataFrame()
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
        return frame.dropna(subset=["ts"]).sort_values("ts")

    def shadow_equity(self) -> pd.DataFrame:
        rows = self.store.read_jsonl("equity_shadows")
        if not rows:
            return pd.DataFrame()
        flat = [{"ts": r.get("ts"), **(r.get("equity") or {})} for r in rows]
        frame = pd.DataFrame(flat)
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
        return frame.dropna(subset=["ts"]).sort_values("ts")

    def last_run(self) -> dict[str, Any]:
        runs = self.store.read_jsonl("runs")
        if not runs:
            return {}
        last = runs[-1]
        return {
            "job": last.get("job"),
            "ts": last.get("finished") or last.get("started"),
            "status": last.get("status"),
            "message": last.get("message"),
            "cron_lag_minutes": cron_lag_minutes(self.store, "update", 30, self.generated_at),
        }

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _downsample(frame: pd.DataFrame, ts_col: str = "ts") -> pd.DataFrame:
        """Daily detail for the last two years, weekly before that."""
        if frame.empty:
            return frame
        cutoff = pd.Timestamp(datetime.now(tz=UTC)) - pd.DateOffset(years=RECENT_DAILY_YEARS)
        recent = frame[frame[ts_col] >= cutoff]
        old = frame[frame[ts_col] < cutoff]
        if old.empty:
            return recent
        old = old.set_index(ts_col).resample("W").last().dropna(how="all").reset_index()
        return pd.concat([old, recent], ignore_index=True)

    def _price_block(self) -> dict[str, Any]:
        if self.md is None or self.md.prices.empty:
            self._warn("nessun prezzo disponibile per summary.json")
            return {"value": None, "asof": None, "source": None, "change_1d": None, "ovx": None}
        prices = self.md.prices
        close = prices["brent_front_close"].dropna()
        if close.empty:
            return {"value": None, "asof": None, "source": None, "change_1d": None, "ovx": None}
        last_ts = pd.Timestamp(close.index[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else None
        ovx = prices["ovx"].dropna() if "ovx" in prices.columns else pd.Series(dtype=float)
        spot = prices["brent_spot"].dropna() if "brent_spot" in prices.columns else pd.Series(dtype=float)
        sources = self.md.meta.get("source") or self.md.meta.get("sources") or {}
        return {
            "value": float(close.iloc[-1]),
            "asof": last_ts.date().isoformat(),
            "source": str(sources.get("brent_front_close") or self.md.meta.get("prices_source") or "n/d"),
            "change_1d": None if prev in (None, 0) else float(close.iloc[-1] / prev - 1.0),
            "ovx": None
            if ovx.empty
            else {
                "value": float(ovx.iloc[-1]),
                "asof": pd.Timestamp(ovx.index[-1]).date().isoformat(),
                "source": str(sources.get("ovx") or "n/d"),
            },
            "spot": None
            if spot.empty
            else {
                "value": float(spot.iloc[-1]),
                "asof": pd.Timestamp(spot.index[-1]).date().isoformat(),
                "source": str(sources.get("brent_spot") or "n/d"),
            },
        }

    # ------------------------------------------------------------------ the files
    def export_summary(self) -> None:
        acct = self.account()
        positions = self.positions()
        equity = self.equity_frame()
        last_snapshot = equity.iloc[-1].to_dict() if not equity.empty else {}
        orders = self.store.read_jsonl("orders")
        last_order = orders[-1] if orders else {}
        decisions = self.store.read_jsonl("decisions")
        last_decision = decisions[-1] if decisions else {}
        regime = (self.store.read_json("regime.json") or {}).get("current") or {}
        health = self.health()
        initial = float(self.risk.initial_capital)
        equity_now = float(last_snapshot.get("equity") or acct.get("last_equity") or initial)
        dead = str(acct.get("status")) == "dead" or equity_now <= self.risk.dead_equity_fraction * initial

        pnl_total = equity_now - initial
        leverage = last_snapshot.get("leverage")
        # The allocator records its gate and leverage at EVERY settlement, order or not: a flat account still
        # has a reason for being flat, and the dashboard must be able to show it.
        lev_block = last_decision.get("leverage") or last_order.get("leverage") or {}
        gate_block = last_decision.get("gate") or last_order.get("gate") or {}
        payload = self._stamp(
            brent=self._price_block(),
            regime={
                "label": regime.get("label"),
                "confidence": regime.get("confidence"),
                "probabilities": regime.get("probabilities"),
                "change_point_prob": regime.get("change_point_prob"),
                "asof": regime.get("ts"),
                "source": "engine/regime (HMM walk-forward + BOCPD)",
                "approx": bool(regime.get("approx", False)),
            }
            if regime
            else None,
            data_status={
                "overall": health.get("overall"),
                "checked_at": health.get("checked_at"),
                "sources": health.get("sources", []),
            },
            account={
                "epoch": acct.get("epoch"),
                "status": acct.get("status"),
                "dead": dead,
                "initial_capital": initial,
                "equity": equity_now,
                "cash": acct.get("cash"),
                "pnl_day": last_snapshot.get("daily_pnl"),
                "pnl_total": pnl_total,
                "pnl_total_pct": pnl_total / initial if initial else None,
                "drawdown": last_snapshot.get("drawdown"),
                "peak_equity": acct.get("peak_equity"),
                "margin_used": last_snapshot.get("margin_used"),
                "margin_level": last_snapshot.get("margin_level"),
                "gross_notional": last_snapshot.get("gross_notional"),
                "net_notional": last_snapshot.get("net_notional"),
                "liquidation_price": last_snapshot.get("liquidation_price"),
                "leverage": {
                    "value": leverage if leverage is not None else lev_block.get("value"),
                    "limited_by": lev_block.get("limited_by"),
                    "components": lev_block.get("components"),
                },
                "gate": {
                    "passed": gate_block.get("passed"),
                    "reason": gate_block.get("reason"),
                    "conditions": gate_block.get("conditions"),
                    "families_agreeing": gate_block.get("families_agreeing"),
                    "ensemble_prob": gate_block.get("ensemble_prob"),
                },
                "positions": positions,
                "asof": last_snapshot.get("ts"),
            },
            last_run=self.last_run(),
            reset={
                "workflow_url": self._workflow_url(),
                "dispatch_api": self._dispatch_url(),
                "confirm_text": "RESET",
            },
            disclaimer=DISCLAIMER,
        )
        self._write("summary.json", payload, indent=2)

    def _repo(self) -> str:
        if self.settings is not None:
            return self.settings.github_repo
        return "andreramolivaz/oil-paper-trading"

    def _workflow_url(self) -> str:
        return f"https://github.com/{self._repo()}/actions/workflows/reset.yml"

    def _dispatch_url(self) -> str:
        return f"https://api.github.com/repos/{self._repo()}/actions/workflows/reset.yml/dispatches"

    def export_equity(self) -> None:
        equity = self.equity_frame()
        shadows = self.shadow_equity()
        if equity.empty:
            self._warn("nessuna curva equity disponibile")
            self._write("equity.json", self._stamp(series=[], shadows={}, source=None))
            return
        series = equity[["ts", "equity", "leverage", "drawdown"]].copy()
        series = self._downsample(series)
        buy_hold = None
        if self.md is not None and not self.md.prices.empty:
            close = self.md.prices["brent_front_close"].dropna()
            if not close.empty:
                idx = pd.DatetimeIndex(series["ts"]).tz_convert(None).normalize()
                aligned = close.reindex(close.index.union(idx)).ffill().reindex(idx)
                first = aligned.dropna()
                if not first.empty:
                    buy_hold = (self.risk.initial_capital * aligned / float(first.iloc[0])).to_numpy()
        rows = []
        for i, (_, r) in enumerate(series.iterrows()):
            rows.append(
                {
                    "t": iso(pd.Timestamp(r["ts"]).to_pydatetime()),
                    "master": None if pd.isna(r.get("equity")) else float(r["equity"]),
                    "leverage": None if pd.isna(r.get("leverage")) else float(r["leverage"]),
                    "drawdown": None if pd.isna(r.get("drawdown")) else float(r["drawdown"]),
                    "buy_hold_brent": None
                    if buy_hold is None or i >= len(buy_hold) or pd.isna(buy_hold[i])
                    else float(buy_hold[i]),
                }
            )
        shadow_payload: dict[str, list[dict[str, Any]]] = {}
        if not shadows.empty:
            ds = self._downsample(shadows)
            for col in [c for c in ds.columns if c != "ts"]:
                shadow_payload[str(col)] = [
                    {"t": iso(pd.Timestamp(t).to_pydatetime()), "v": None if pd.isna(v) else float(v)}
                    for t, v in zip(ds["ts"], ds[col])
                ]
        self._write(
            "equity.json",
            self._stamp(
                series=rows,
                shadows=shadow_payload,
                master_1x=None,
                note="master_1x non ancora disponibile: richiede un backtest parallelo senza leva",
                source="engine/live (paper trading)",
            ),
        )

    def export_trades(self) -> None:
        fills = self.store.read_jsonl("trades")
        orders = {o.get("order_id"): o for o in self.store.read_jsonl("orders")}
        recent = fills[-MAX_TRADES:]
        rows = []
        for f in recent:
            order = orders.get(f.get("order_id"), {})
            rows.append(
                {
                    "ts": f.get("ts"),
                    "instrument": f.get("instrument"),
                    "qty_bbl": f.get("qty_bbl"),
                    "price": f.get("price"),
                    "reference_price": f.get("reference_price"),
                    "slippage": f.get("slippage"),
                    "commission": f.get("commission"),
                    "realized_pnl": f.get("realized_pnl"),
                    "reason": f.get("reason"),
                    "price_source": f.get("price_source"),
                    "regime": order.get("regime"),
                    "rationale": order.get("rationale") or f.get("meta", {}).get("trigger"),
                    "strategies_for": order.get("strategies_for", []),
                    "strategies_against": order.get("strategies_against", []),
                    "gate_passed": (order.get("gate") or {}).get("passed"),
                    "leverage": (order.get("leverage") or {}).get("value"),
                    "leverage_limited_by": (order.get("leverage") or {}).get("limited_by"),
                }
            )
        self._write(
            "trades.json",
            self._stamp(trades=rows, n_total=len(fills), csv_url="trades.csv", source="engine/broker"),
        )
        self.out.mkdir(parents=True, exist_ok=True)
        headers = [
            ("ts", "Data e ora (UTC)"),
            ("instrument", "Strumento"),
            ("qty_bbl", "Quantità (barili)"),
            ("price", "Prezzo eseguito"),
            ("reference_price", "Prezzo di riferimento"),
            ("slippage", "Slippage"),
            ("commission", "Commissioni"),
            ("realized_pnl", "P&L realizzato"),
            ("reason", "Motivo"),
            ("regime", "Regime"),
            ("leverage", "Leva"),
            ("leverage_limited_by", "Leva limitata da"),
            ("gate_passed", "Gate superato"),
            ("rationale", "Motivazione"),
        ]
        with (self.out / "trades.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_MINIMAL)
            writer.writerow([label for _, label in headers])
            for row in rows:
                writer.writerow([clean(row.get(key)) for key, _ in headers])
        self.written.append("trades.csv")

    def export_market(self) -> None:
        payload: dict[str, Any] = self._stamp(source="engine/data")
        if self.md is None or self.md.prices.empty:
            self._warn("nessun dato di mercato per market.json")
            self._write("market.json", payload)
            return
        prices = self.md.prices
        curve_row = None
        if not self.md.curve.empty:
            curve_row = self.md.curve.iloc[-1]
        curve_points = []
        if curve_row is not None:
            for k in range(1, 37):
                col = f"M{k}"
                if col not in curve_row.index:
                    continue
                v = curve_row[col]
                if pd.isna(v):
                    continue
                curve_points.append({"rank": k, "price": float(v)})
        approx_curve = (
            bool(len(self.md.curve_approx) and bool(self.md.curve_approx.iloc[-1]))
            if len(self.md.curve_approx)
            else False
        )
        payload["curve"] = {
            "asof": None if self.md.curve.empty else pd.Timestamp(self.md.curve.index[-1]).date().isoformat(),
            "m1_code": None if curve_row is None or "M1_code" not in curve_row.index else curve_row.get("M1_code"),
            "points": curve_points,
            "approx": approx_curve,
            "source": "Yahoo Finance (scadenze singole)" if curve_points else None,
            "note": "La curva storica prima del go-live è un proxy: vedi docs/DATA_SOURCES.md.",
        }

        def series(col: str, n: int = 500) -> list[dict[str, Any]]:
            if col not in prices.columns:
                return []
            s = prices[col].dropna().tail(n)
            return [{"t": pd.Timestamp(t).date().isoformat(), "v": float(v)} for t, v in zip(s.index, s.to_numpy())]

        payload["spreads"] = {
            "brent_wti": [
                {"t": p["t"], "v": p["v"]} for p in self._diff_series(prices, "brent_front_close", "wti_front_close")
            ],
        }
        payload["prices"] = {
            "brent_front": series("brent_front_close"),
            "brent_spot": series("brent_spot"),
            "ovx": series("ovx"),
        }
        if not self.md.cot.empty:
            cot = self.md.cot.reset_index()
            brent = cot.loc[cot["market"] == "brent"] if "market" in cot.columns else cot
            brent = brent if isinstance(brent, pd.DataFrame) else brent.to_frame().T
            payload["cot"] = {"source": "ICE Futures Europe COT", "series": self._cot_series(brent)}
        if not self.md.news.empty and "gpr" in self.md.news.columns:
            gpr = self.md.news["gpr"].dropna().tail(500)
            payload["geopolitics"] = {
                "source": "Caldara-Iacoviello GPR",
                "series": [
                    {"t": pd.Timestamp(t).date().isoformat(), "v": float(v)} for t, v in zip(gpr.index, gpr.to_numpy())
                ],
            }
        if not self.md.wpsr.empty:
            payload["inventories"] = self._inventory_block()
        payload["events"] = [
            {
                "id": e.event_id,
                "name": e.name,
                "ts": iso(e.ts),
                "binary": e.binary,
                "confirmed": e.confirmed,
                "hours_away": round(e.hours_from(self.generated_at), 1),
            }
            for e in self.events.next_events(self.generated_at, horizon_days=45)[:20]
        ]
        regime_doc = self.store.read_json("regime.json") or {}
        payload["regime_history"] = regime_doc.get("history", [])
        self._write("market.json", payload)

    @staticmethod
    def _cot_series(frame: pd.DataFrame, n: int = 260) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for _, row in frame.tail(n).iterrows():
            period = row.get("period")
            out.append(
                {
                    "t": None if period is None or pd.isna(period) else pd.Timestamp(str(period)).date().isoformat(),
                    "mm_net": SiteExporter._num(row.get("mm_net")),
                    "oi": SiteExporter._num(row.get("oi")),
                }
            )
        return out

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            if value is None or pd.isna(value):
                return None
            f = float(value)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    @staticmethod
    def _diff_series(prices: pd.DataFrame, a: str, b: str, n: int = 500) -> list[dict[str, Any]]:
        if a not in prices.columns or b not in prices.columns:
            return []
        s = (prices[a] - prices[b]).dropna().tail(n)
        return [{"t": pd.Timestamp(t).date().isoformat(), "v": float(v)} for t, v in zip(s.index, s.to_numpy())]

    def _inventory_block(self) -> dict[str, Any]:
        """Crude stocks against the 5-year seasonal range, as the brief asks for."""
        wpsr = self.md.wpsr.copy() if self.md is not None else pd.DataFrame()
        if wpsr.empty or "crude_stocks" not in wpsr.columns or "period" not in wpsr.columns:
            return {}
        wpsr = wpsr.dropna(subset=["crude_stocks"])
        periods = pd.to_datetime(wpsr["period"], errors="coerce")
        frame = pd.DataFrame({"period": periods, "stocks": wpsr["crude_stocks"].astype(float)}).dropna()
        frame["week"] = pd.DatetimeIndex(frame["period"]).isocalendar().week.to_numpy()
        frame["year"] = pd.DatetimeIndex(frame["period"]).year
        latest_year = int(frame["year"].max())
        band = frame[frame["year"].between(latest_year - 5, latest_year - 1)]
        stats = band.groupby("week")["stocks"].agg(["min", "max", "mean"]).reset_index()
        current = frame[frame["year"] == latest_year]
        return {
            "source": "EIA Weekly Petroleum Status Report",
            "unit": "migliaia di barili",
            "band_years": f"{latest_year - 5}-{latest_year - 1}",
            "band": [
                {
                    "week": int(r["week"]),
                    "min": float(r["min"]),
                    "max": float(r["max"]),
                    "mean": float(r["mean"]),
                }
                for _, r in stats.iterrows()
            ],
            "current": [
                {
                    "week": int(r["week"]),
                    "v": float(r["stocks"]),
                    "t": pd.Timestamp(str(r["period"])).date().isoformat(),
                }
                for _, r in current.iterrows()
            ],
        }

    def export_forecasts(self) -> None:
        doc = self.store.read_json("forecasts_summary.json")
        if not doc:
            self._warn("nessuna previsione disponibile")
            self._write("forecasts.json", self._stamp(horizons={}, track_record=None, source=None))
            return
        rows = self.store.read_jsonl("forecasts")
        latest: dict[str, dict[str, Any]] = {}
        for r in rows[-400:]:
            if r.get("model") == "ensemble":
                latest[str(r.get("horizon"))] = r
        implied = None
        if self.md is not None and "ovx" in self.md.prices.columns:
            ovx = self.md.prices["ovx"].dropna()
            close = self.md.prices["brent_front_close"].dropna()
            if not ovx.empty and not close.empty:
                try:
                    from engine.forecast.implied import ovx_implied_range

                    low, high = ovx_implied_range(float(close.iloc[-1]), float(ovx.iloc[-1]))
                    implied = {"low": low, "high": high, "days": 21, "approx": True, "source": "OVX (CBOE)"}
                except Exception as exc:
                    self._warn(f"range implicito OVX non calcolabile: {exc}")
        self._write(
            "forecasts.json",
            self._stamp(
                asof=doc.get("asof"),
                price=doc.get("price"),
                models=doc.get("models"),
                horizons=latest,
                track_record=doc.get("track_record"),
                ovx_implied_range=implied,
                source="engine/forecast",
            ),
        )

    def export_passthrough(self) -> None:
        """Files the engine already writes in their final shape."""
        for name, default in (
            ("strategies.json", {"strategies": []}),
            ("health.json", {"overall": None, "sources": []}),
            ("validation.json", None),
            ("risk.json", None),
        ):
            doc = self.store.read_json(name)
            if doc is None:
                if default is None:
                    self._warn(f"{name} non disponibile")
                    self._write(name, self._stamp(available=False))
                    continue
                doc = default
            if isinstance(doc, dict):
                doc.setdefault("generated_at", iso(self.generated_at))
            self._write(name, doc)
        epochs = self.store.read_json("epochs.json") or []
        acct = self.account()
        current = {
            "epoch": acct.get("epoch"),
            "started_ts": acct.get("epoch_started_ts"),
            "start_equity": acct.get("epoch_start_equity"),
            "max_equity": acct.get("peak_equity"),
            "min_equity": acct.get("epoch_min_equity"),
            "end_equity": None,
            "end_reason": None,
            "n_trades": acct.get("epoch_n_fills"),
            "current": True,
        }
        self._write("epochs.json", self._stamp(epochs=[*epochs, current] if acct else epochs))

    # ------------------------------------------------------------------
    def export_all(self) -> ExportResult:
        self.export_summary()
        self.export_equity()
        self.export_trades()
        self.export_market()
        self.export_forecasts()
        self.export_passthrough()
        self._write("index.json", self._stamp(files=sorted(self.written), warnings=self.warnings))
        return ExportResult(files=sorted(self.written), warnings=self.warnings)


def export_all(
    store: StateStore,
    out_dir: Path | str,
    md: MarketData | None = None,
    risk: RiskConfig | None = None,
    settings: Settings | None = None,
) -> ExportResult:
    """Build every dashboard file from the persisted state (see docs/SITE_DATA.md)."""
    return SiteExporter(store, Path(out_dir), md=md, risk=risk, settings=settings).export_all()
