"""Build `state/risk.json`: what the Rischio page of the dashboard shows (brief §13).

Everything here is derived from state the engine already produced, plus the risk modules:

* VaR and Expected Shortfall of the master's own daily returns (historical) and of the current exposure
  (parametric Student-t, the same tail the leverage budget uses);
* the leverage history and the margin situation;
* the ruin probability estimated by Monte Carlo on REAL Brent returns under the engine's own leverage policy;
* historical analogs (Gulf War, Abqaiq, Covid, Russia, the 2026 truce) and the synthetic shock scenarios.

Anything that cannot be computed from real data is reported as unavailable with the reason, never estimated.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from engine.core.config import RiskConfig
from engine.core.store import StateStore
from engine.core.timeutil import iso, now_utc

log = logging.getLogger(__name__)

RISK_FILE = "risk.json"


@dataclass
class RiskInputs:
    equity: pd.Series
    leverage: pd.Series
    prices: pd.Series
    account: dict[str, Any]
    last_snapshot: dict[str, Any]


def _series_from_equity(store: StateStore) -> tuple[pd.Series, pd.Series, dict[str, Any]]:
    rows = store.read_jsonl("equity")
    if not rows:
        return pd.Series(dtype=float), pd.Series(dtype=float), {}
    frame = pd.DataFrame(rows)
    if "ts" not in frame.columns:
        return pd.Series(dtype=float), pd.Series(dtype=float), {}
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["ts"]).sort_values("ts")
    if frame.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float), {}
    index = pd.DatetimeIndex(frame["ts"])
    equity = (
        pd.Series(pd.to_numeric(frame["equity"], errors="coerce").to_numpy(dtype=float), index=index)
        if "equity" in frame.columns
        else pd.Series(dtype=float)
    )
    lev = (
        pd.Series(pd.to_numeric(frame["leverage"], errors="coerce").to_numpy(dtype=float), index=index)
        if "leverage" in frame.columns
        else pd.Series(dtype=float)
    )
    return equity.dropna(), lev.dropna(), dict(frame.iloc[-1])


def build_risk_payload(
    store: StateStore,
    risk: RiskConfig,
    prices: pd.Series | None = None,
    run_monte_carlo: bool = True,
    run_stress: bool = True,
) -> dict[str, Any]:
    """Assemble risk.json. `prices` is the real Brent daily series used for the ruin and stress estimates."""
    equity, leverage, last = _series_from_equity(store)
    account = store.read_json("account.json") or {}
    unavailable: list[str] = []
    payload: dict[str, Any] = {
        "generated_at": iso(now_utc()),
        "available": True,
        "source": "engine/monitoring/risk_report.py",
        "initial_capital": risk.initial_capital,
    }

    # ---- VaR / ES ---------------------------------------------------------------------------------------
    from engine.portfolio.var import historical_var_es, parametric_var_es

    rets = equity.pct_change().dropna() if len(equity) > 2 else pd.Series(dtype=float)
    var_block: dict[str, float | None] = {}
    es_block: dict[str, float | None] = {}
    if len(rets) >= 30:
        for level, label in ((0.95, "95% 1g"), (0.99, "99% 1g")):
            v, e = historical_var_es(rets, level)
            var_block[label] = float(v)
            es_block[label] = float(e)
        payload["var_source"] = f"storico del conto ({len(rets)} giorni)"
    else:
        unavailable.append(f"VaR storico non calcolabile: servono almeno 30 giorni di equity, ce ne sono {len(rets)}")
        # fall back on the parametric tail of the CURRENT exposure, which is real information even on day one
        snapshot_lev = float(last.get("leverage") or 0.0)
        vol = _current_vol(store)
        if vol is not None and snapshot_lev > 0:
            for level, label in ((0.95, "95% 1g"), (0.99, "99% 1g")):
                v, e = parametric_var_es(vol * snapshot_lev, level)
                var_block[label] = float(v)
                es_block[label] = float(e)
            payload["var_source"] = "parametrica Student-t sull'esposizione corrente (storico insufficiente)"
    payload["var"] = var_block or None
    payload["es"] = es_block or None

    # ---- leverage and margin ----------------------------------------------------------------------------
    if len(leverage):
        payload["leverage_history"] = [
            {"t": iso(pd.Timestamp(str(t)).to_pydatetime()), "v": float(v)} for t, v in leverage.tail(500).items()
        ]
        payload["leverage_stats"] = {
            "mean": float(leverage.mean()),
            "max": float(leverage.max()),
            "p95": float(leverage.quantile(0.95)),
            "days_above_1x": int((leverage > 1.0 + 1e-9).sum()),
            "cap": risk.max_leverage,
        }
    else:
        unavailable.append("Storico della leva non disponibile: nessuna marcatura del conto registrata")
    payload["margin"] = {
        "rate": risk.margin_rate,
        "stop_out_level": risk.stop_out_margin_level,
        "used": _f(last.get("margin_used")),
        "level": _f(last.get("margin_level")),
        "gross_notional": _f(last.get("gross_notional")),
        "liquidation_price": _f(last.get("liquidation_price")),
    }
    payload["breakers"] = {
        "daily_loss": risk.daily_loss_breaker,
        "dead_equity_fraction": risk.dead_equity_fraction,
        "status": account.get("status"),
    }

    # ---- Monte Carlo ruin -------------------------------------------------------------------------------
    if run_monte_carlo and prices is not None and len(prices.dropna()) > 500:
        try:
            from engine.portfolio.montecarlo import cost_bps_from_risk, policy_from_risk, simulate_ruin

            log_returns = np.diff(np.log(pd.to_numeric(prices, errors="coerce").dropna().to_numpy()))
            mc = risk.raw.get("monte_carlo", {}) if isinstance(risk.raw, dict) else {}
            ruin_report = simulate_ruin(
                log_returns,
                policy_from_risk(risk, gate_pass_rate=0.1),
                n_paths=int(mc.get("n_paths", 20_000)),
                horizon_days=int(mc.get("horizon_days", 250)),
                initial_capital=risk.initial_capital,
                ruin_threshold=float(mc.get("ruin_threshold", 0.5)),
                cost_bps_per_turn=cost_bps_from_risk(risk),
                dead_fraction=risk.dead_equity_fraction,
                hard_cap=risk.max_leverage,
            )
            payload["ruin"] = ruin_report.to_dict()
            payload["ruin"]["max_ruin_probability"] = float(mc.get("max_ruin_probability", 0.05))
            payload["ruin"]["within_budget"] = bool(
                ruin_report.prob_ruin <= float(mc.get("max_ruin_probability", 0.05))
            )
        except Exception as exc:
            log.warning("ruin simulation failed: %s", exc)
            unavailable.append(f"Rischio di rovina non stimato: {exc}")
    elif run_monte_carlo:
        unavailable.append("Rischio di rovina non stimato: serve una storia di prezzi reali sufficiente")

    # ---- stress -----------------------------------------------------------------------------------------
    if run_stress and prices is not None and len(prices.dropna()) > 100:
        try:
            from engine.portfolio.stress import PositionSpec, StressReport, historical_analogs, synthetic_scenarios

            clean = pd.to_numeric(prices, errors="coerce").dropna()
            lev_now = float(last.get("leverage") or 0.0)
            positions = account.get("positions") or []
            qty = float(positions[0].get("qty_bbl", 0.0) or 0.0) if positions else 0.0
            spec = PositionSpec(
                leverage=lev_now if lev_now > 0 else 1.0,
                direction=1 if qty >= 0 else -1,
                initial_capital=float(last.get("equity") or risk.initial_capital),
                margin_rate=risk.margin_rate,
                stop_out_level=risk.stop_out_margin_level,
                dead_fraction=risk.dead_equity_fraction,
            )
            stress_report = StressReport(
                position=spec,
                episodes=historical_analogs(clean, spec),
                scenarios=synthetic_scenarios(float(clean.iloc[-1]), spec),
                price_source="EIA/Yahoo (serie reale)",
                price_asof=str(pd.Timestamp(clean.index[-1]).date()),
                reference_price=float(clean.iloc[-1]),
            )
            payload["stress"] = stress_report.to_dict()
        except Exception as exc:
            log.warning("stress report failed: %s", exc)
            unavailable.append(f"Stress test non prodotti: {exc}")
    elif run_stress:
        unavailable.append("Stress test non prodotti: serie di prezzi insufficiente")

    payload["unavailable"] = unavailable
    return payload


def write_risk_file(
    store: StateStore,
    risk: RiskConfig,
    prices: pd.Series | None = None,
    run_monte_carlo: bool = True,
    run_stress: bool = True,
) -> dict[str, Any]:
    payload = build_risk_payload(store, risk, prices, run_monte_carlo, run_stress)
    store.write_json(RISK_FILE, payload)
    return payload


def _f(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _current_vol(store: StateStore) -> float | None:
    """The volatility the last decision used, so a day-one account still gets a real tail estimate."""
    rows = store.read_jsonl("decisions")
    for row in reversed(rows[-50:]):
        vol = row.get("vol_used")
        if vol is not None:
            try:
                value = float(vol)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                return value
    return None
