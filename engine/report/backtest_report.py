"""Backtest report and validation report (brief §12, docs/SITE_DATA.md ``validation.json``).

One entry point runs the event loop and then puts every anti-overfitting instrument the repository owns on the
result, strategy by strategy:

* descriptive and risk-adjusted metrics (:mod:`engine.validation.metrics`, PSR included);
* transaction-cost sensitivity at the backtest's own costs and at **double** costs
  (:mod:`engine.validation.costs`) — the brief's "costi raddoppiati";
* the Deflated Sharpe Ratio against the **registry of every trial** evaluated here
  (:class:`engine.validation.dsr.TrialLog`): each strategy and each supplied variant is logged before anything is
  deflated, so the trial count is the real one and nothing can be cherry-picked afterwards;
* the Probability of Backtest Overfitting over the supplied ``variants`` (CSCV,
  :func:`engine.validation.cpcv.probability_of_backtest_overfitting`); with no variants the shadow strategies are
  used as the variant matrix, and with fewer than two series the PBO is reported as unavailable with a reason;
* results sliced **by regime** (the session's own regime history, or the ``regime_label`` column of a feature
  frame when one is given) and **by crisis episode** (``config/events.yaml`` ``historical_episodes``);
* the lifecycle decision and its Italian explanation (:mod:`engine.validation.lifecycle`): a strategy that fails
  any check appears with **weight zero and the reason**, which is the whole point of the exercise;
* the leverage usage histogram from the broker snapshots and, when importable, the stress report of
  :mod:`engine.portfolio.stress`.

Cost sensitivity, honestly
--------------------------
The backtest returns already include the broker's real costs (spread, slippage, commissions, roll).  Charging
``cost_per_turn`` once more on top would double-count, so the table reports the series **as run** as the "1x
costs" row and subtracts one further ``cost_per_turn * turnover`` for the "2x costs" row.  The turnover series is
the master's own change in net exposure (from the snapshots); for a shadow strategy it is derived from its signal
history.  When no turnover series can be derived the row is reported as ``null`` with a reason — never guessed,
which (by :mod:`engine.validation.lifecycle`'s rule that a missing number is a failed check) keeps the strategy at
weight zero rather than promoting it on an assumption.

Outputs
-------
``<out_dir>/backtest.md``  Italian markdown report with the tables above.
``<out_dir>/validation.json`` and, when a :class:`~engine.core.store.StateStore` is given, ``validation.json``
in the state directory (the dashboard contract of docs/SITE_DATA.md).  The JSON is **strict**: no NaN, no
Infinity — non-finite numbers are written as ``null``, so a missing number stays missing.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.backtest.runner import BacktestResult, run_backtest
from engine.core.config import RiskConfig
from engine.core.store import StateStore
from engine.data.market_data import MarketData
from engine.validation import costs as costs_mod
from engine.validation import metrics as mx
from engine.validation.lifecycle import Thresholds, decide_lifecycle, explain

log = logging.getLogger(__name__)

__all__ = ["ReportPaths", "run_backtest_report", "run_validation"]

TRADING_DAYS = 252
DISCLAIMER = "Simulazione a scopo di studio: paper trading, nessun consiglio finanziario."


@dataclass
class ReportPaths:
    out_dir: Path
    markdown: Path
    json: Path
    state_json: Path | None = None


# ---------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------
def _f(x: Any) -> float | None:
    """Finite float or None (the JSON contract never carries NaN/Infinity)."""
    if x is None or isinstance(x, bool):
        return None if x is None else float(x)
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(v) else v


def _sanitize(obj: Any) -> Any:
    """Recursively replace non-finite floats with None and numpy scalars with Python ones (strict JSON)."""
    if isinstance(obj, Mapping):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, bool | int | str) or obj is None:
        return obj
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, pd.Timestamp | datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def _it(x: Any, nd: int = 2) -> str:
    """Italian number formatting (decimal comma); ``n/d`` when the number is missing."""
    v = _f(x)
    if v is None:
        return "n/d"
    return f"{v:.{nd}f}".replace(".", ",")


def _pct(x: Any, nd: int = 1) -> str:
    v = _f(x)
    if v is None:
        return "n/d"
    return f"{100 * v:.{nd}f}%".replace(".", ",")


def _returns(curve: pd.Series) -> pd.Series:
    eq = pd.to_numeric(pd.Series(curve), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    eq = eq[eq > 0]
    return eq.pct_change().dropna() if len(eq) > 1 else pd.Series(dtype=float)


def _cost_per_turn(risk: RiskConfig) -> float:
    """Cost of one unit of exposure turnover, as a fraction of equity, from ``config/risk.yaml``."""
    c = risk.costs or {}
    bps = float(c.get("base_spread_bps", 1.5)) + float(c.get("slippage_bps_per_turn", 1.0))
    return bps / 1e4


# ---------------------------------------------------------------------------------------------------------------
# exposure / turnover
# ---------------------------------------------------------------------------------------------------------------
def _master_exposure(snapshots: pd.DataFrame) -> pd.Series:
    """Signed net exposure (net notional / equity) per day, from the broker snapshots."""
    if snapshots.empty or not {"ts", "net_notional", "equity"} <= set(snapshots.columns):
        return pd.Series(dtype=float)
    df = snapshots[["ts", "net_notional", "equity"]].copy()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
    df = df.dropna(subset=["ts"]).set_index("ts").sort_index()
    eq = pd.to_numeric(df["equity"], errors="coerce")
    net = pd.to_numeric(df["net_notional"], errors="coerce")
    exp = (net / eq.where(eq > 0)).dropna()
    exp.index = pd.DatetimeIndex([pd.Timestamp(t).tz_convert("UTC").normalize().tz_localize(None) for t in exp.index])
    return exp[~exp.index.duplicated(keep="last")]


def _signal_exposure(signals: pd.DataFrame, strategy_id: str) -> pd.Series:
    """Signed exposure implied by a strategy's own signals (direction x strength), 0 on days without a signal."""
    if signals.empty or "strategy_id" not in signals.columns:
        return pd.Series(dtype=float)
    sub = signals[signals["strategy_id"].astype(str) == str(strategy_id)]
    if sub.empty or "ts" not in sub.columns:
        return pd.Series(dtype=float)
    ts = pd.to_datetime(sub["ts"], errors="coerce", utc=True)
    sign = (
        sub["direction"].astype(str).map({"long": 1.0, "short": -1.0, "flat": 0.0})
        if "direction" in sub.columns
        else pd.Series(1.0, index=sub.index)
    )
    strength_col = sub["strength"] if "strength" in sub.columns else pd.Series(1.0, index=sub.index)
    strength = pd.to_numeric(strength_col, errors="coerce").fillna(1.0)
    exp = pd.Series(np.asarray(sign, dtype=float) * np.asarray(strength, dtype=float), index=ts).dropna()
    exp.index = pd.DatetimeIndex([pd.Timestamp(t).tz_convert("UTC").normalize().tz_localize(None) for t in exp.index])
    return exp[~exp.index.duplicated(keep="last")]


def _turnover(exposure: pd.Series, index: pd.Index) -> pd.Series:
    """``|Δ exposure|`` aligned to ``index`` (missing days = flat, so entering/leaving counts as turnover)."""
    if exposure.empty or len(index) == 0:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(t).normalize() for t in index])
    exp = exposure.reindex(idx).ffill().fillna(0.0)
    return exp.diff().abs().fillna(exp.abs().iloc[0] if len(exp) else 0.0)


# ---------------------------------------------------------------------------------------------------------------
# slices
# ---------------------------------------------------------------------------------------------------------------
def _regime_labels(res: BacktestResult, features: pd.DataFrame | None) -> pd.Series:
    """Daily regime label: the session's regime history, or the ``regime_label`` feature column as a fallback."""
    if not res.regimes.empty and {"ts", "label"} <= set(res.regimes.columns):
        ts = pd.to_datetime(res.regimes["ts"], errors="coerce", utc=True)
        lab = pd.Series(res.regimes["label"].astype(str).to_numpy(), index=ts).dropna()
        lab.index = pd.DatetimeIndex(
            [pd.Timestamp(t).tz_convert("UTC").normalize().tz_localize(None) for t in lab.index]
        )
        return lab[~lab.index.duplicated(keep="last")]
    if features is not None and not features.empty:
        from engine.features import catalog as cat

        if cat.REGIME_LABEL in features.columns:
            lab = features[cat.REGIME_LABEL].dropna().astype(str)
            lab.index = pd.DatetimeIndex([pd.Timestamp(t).normalize() for t in lab.index])
            return lab[~lab.index.duplicated(keep="last")]
    return pd.Series(dtype=object)


def _slice_by_regime(returns: pd.Series, labels: pd.Series, min_obs: int = 10) -> dict[str, Any]:
    if returns.empty or labels.empty:
        return {}
    idx = pd.DatetimeIndex([pd.Timestamp(t).normalize() for t in returns.index])
    lab = labels.reindex(idx).ffill()
    out: dict[str, Any] = {}
    for name, grp in returns.groupby(lab.to_numpy()):
        if name is None or (isinstance(name, float) and math.isnan(name)):
            continue
        out[str(name)] = {
            "n_obs": len(grp),
            "sharpe": _f(mx.sharpe(grp)) if len(grp) >= min_obs else None,
            "total_return": _f(float(np.prod(1.0 + grp.to_numpy(dtype=float)) - 1.0)),
            "ann_vol": _f(mx.annualised_vol(grp)) if len(grp) >= 2 else None,
            "max_drawdown": _f(mx.max_drawdown(grp).depth),
            "hit_rate": _f(mx.hit_rate(grp)),
            "enough_obs": bool(len(grp) >= min_obs),
        }
    return out


def _slice_by_crisis(returns: pd.Series, episodes: Sequence[Mapping[str, Any]], min_obs: int = 5) -> dict[str, Any]:
    out: dict[str, Any] = {}
    idx = pd.DatetimeIndex([pd.Timestamp(t).normalize() for t in returns.index]) if len(returns) else None
    for ep in episodes:
        ident = str(ep.get("id", ep.get("name", "?")))
        name = str(ep.get("name", ident))
        if idx is None or returns.empty:
            out[ident] = {"name": name, "available": False, "reason": "nessun rendimento nel backtest"}
            continue
        start = pd.Timestamp(str(ep.get("start"))) if ep.get("start") else None
        end = pd.Timestamp(str(ep.get("end"))) if ep.get("end") else idx.max()
        if start is None:
            out[ident] = {"name": name, "available": False, "reason": "data di inizio non valida"}
            continue
        mask = (idx >= start) & (idx <= end)
        grp = returns[mask]
        if len(grp) < min_obs:
            out[ident] = {
                "name": name,
                "available": False,
                "reason": f"solo {len(grp)} giorni di backtest nell'episodio (minimo {min_obs})",
                "start": str(start.date()),
                "end": str(pd.Timestamp(end).date()),
            }
            continue
        out[ident] = {
            "name": name,
            "available": True,
            "start": str(start.date()),
            "end": str(pd.Timestamp(end).date()),
            "n_obs": len(grp),
            "total_return": _f(float(np.prod(1.0 + grp.to_numpy(dtype=float)) - 1.0)),
            "sharpe": _f(mx.sharpe(grp)),
            "max_drawdown": _f(mx.max_drawdown(grp).depth),
            "worst_day": _f(float(grp.min())),
        }
    return out


# ---------------------------------------------------------------------------------------------------------------
# cost sensitivity, DSR, PBO
# ---------------------------------------------------------------------------------------------------------------
def _cost_table(returns: pd.Series, turnover: pd.Series, cost_per_turn: float) -> dict[str, Any]:
    """``{"x1": {...}, "x2": {...}}``: the series as run, and with one extra charge of the same costs."""
    if returns.empty or turnover.empty:
        return {"available": False, "reason": "serie di turnover non ricostruibile: costi x2 non calcolabili"}
    t = turnover.reindex(returns.index).fillna(0.0)
    table = costs_mod.cost_sensitivity(returns.to_numpy(dtype=float), cost_per_turn, t.to_numpy(dtype=float), (0, 1))
    rows = {"x1": table.loc[0.0].to_dict(), "x2": table.loc[1.0].to_dict()}
    return {
        "available": True,
        "cost_per_turn_bps": cost_per_turn * 1e4,
        "mean_turnover": _f(float(t.mean())),
        "breakeven_cost_bps": _f(costs_mod.breakeven_cost(returns.to_numpy(dtype=float), t.to_numpy(dtype=float))),
        "x1": {k: _f(v) for k, v in rows["x1"].items()},
        "x2": {k: _f(v) for k, v in rows["x2"].items()},
        "labels": {
            "x1": "costi come da backtest (1x)",
            "x2": "costi raddoppiati (2x)",
        },
    }


def _pbo(
    variants: Mapping[str, Sequence[float]] | pd.DataFrame | None,
    fallback: pd.DataFrame,
    skipped: list[str],
) -> dict[str, Any] | None:
    """CSCV PBO over the variant matrix; ``None`` with a reason appended to ``skipped`` when not computable."""
    if isinstance(variants, pd.DataFrame):
        mat = variants.copy()
        source = "variants forniti"
    elif variants:
        mat = pd.DataFrame({str(k): pd.Series(np.asarray(v, dtype=float)) for k, v in variants.items()})
        source = "variants forniti"
    else:
        mat = fallback
        source = "strategie ombra usate come varianti"
    mat = mat.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if mat.shape[1] < 2:
        skipped.append("PBO non calcolata: servono almeno due varianti di rendimenti allineate")
        return None
    n_part = 16
    while n_part > 2 and mat.shape[0] < n_part * 2:
        n_part -= 2
    if mat.shape[0] < n_part or n_part < 2:
        skipped.append(f"PBO non calcolata: solo {mat.shape[0]} osservazioni comuni")
        return None
    try:
        from engine.validation.cpcv import probability_of_backtest_overfitting
    except ImportError as exc:  # pragma: no cover - the module is part of the repo
        skipped.append(f"PBO non calcolata: {exc}")
        return None
    res = probability_of_backtest_overfitting(mat.to_numpy(dtype=float), n_partitions=n_part)
    out = dict(res.to_dict())
    out.update({"source": source, "variants": [str(c) for c in mat.columns], "n_obs": int(mat.shape[0])})
    return out


def _register_and_deflate(
    store: StateStore | None,
    out_dir: Path,
    curves: dict[str, pd.Series],
    extra_variants: Mapping[str, Sequence[float]] | pd.DataFrame | None,
    skipped: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Log every trial (strategies + variants) and deflate each strategy's Sharpe against the whole registry."""
    try:
        from engine.validation.dsr import TrialLog
    except ImportError as exc:  # pragma: no cover
        skipped.append(f"DSR non calcolato: {exc}")
        return {}, {"available": False, "reason": str(exc)}

    log_store = store or StateStore(out_dir / "trials")
    trials = TrialLog(log_store)
    asof = datetime.now(tz=UTC)
    records: list[tuple[str, pd.Series]] = [(k, _returns(v)) for k, v in curves.items()]
    if isinstance(extra_variants, pd.DataFrame):
        records += [
            (f"variant:{c}", pd.to_numeric(extra_variants[c], errors="coerce").dropna()) for c in extra_variants
        ]
    elif extra_variants:
        records += [(f"variant:{k}", pd.Series(np.asarray(v, dtype=float)).dropna()) for k, v in extra_variants.items()]

    # A trial is registered only when its Sharpe is defined: a flat or too-short series carries no information
    # and would poison both the registry and the DSR variance.  What is skipped is reported, not hidden.
    usable: list[tuple[str, pd.Series, float, float, float]] = []
    for name, r in records:
        sr = mx.sharpe(r) if len(r) >= 2 else float("nan")
        if not math.isfinite(sr):
            if not name.startswith("variant:"):
                skipped.append(f"DSR non calcolato per {name}: Sharpe non definito (serie piatta o troppo corta)")
            continue
        sk, ku = mx.skew(r), mx.kurtosis(r)
        sk = float(sk) if math.isfinite(sk) else 0.0
        ku = float(ku) if math.isfinite(ku) else 3.0
        usable.append((name, r, float(sr), sk, ku))
        trials.log(
            name,
            {"variant": name, "window": f"{len(r)}d"},
            float(sr),
            len(r),
            sk,
            ku,
            asof=asof,
            periods_per_year=TRADING_DAYS,
        )
    n_trials = trials.n_trials(None)
    if n_trials < 1:
        return {}, {"available": False, "reason": "nessuna prova con Sharpe definito: DSR non calcolabile"}
    all_sharpes = trials.sharpes(None)
    from engine.validation.dsr import deflated_sharpe_ratio

    out: dict[str, dict[str, Any]] = {}
    sr0_seen = float("nan")
    for name, r, sr, sk, ku in usable:
        if name.startswith("variant:"):
            continue
        prob, sr0 = deflated_sharpe_ratio(
            all_sharpes, sr, len(r), sk, ku, n_trials=n_trials, periods_per_year=TRADING_DAYS
        )
        sr0_seen = sr0
        out[name] = {"dsr_prob": _f(prob), "sr0": _f(sr0), "n_trials": int(n_trials)}
    return out, {
        "available": True,
        "n_trials": int(n_trials),
        "sr0": _f(sr0_seen),
        "store": str(log_store.path("trials.jsonl").parent),
        "note": "Ogni variante valutata è registrata in trials.jsonl prima del calcolo: nessun cherry picking.",
    }


# ---------------------------------------------------------------------------------------------------------------
# leverage histogram
# ---------------------------------------------------------------------------------------------------------------
LEV_BINS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)


def _leverage_histogram(snapshots: pd.DataFrame) -> dict[str, Any]:
    if snapshots.empty or "leverage" not in snapshots.columns:
        return {"available": False, "reason": "nessuno snapshot con la leva"}
    lev = pd.to_numeric(snapshots["leverage"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if lev.empty:
        return {"available": False, "reason": "nessun valore di leva utilizzabile"}
    counts, edges = np.histogram(lev.to_numpy(dtype=float), bins=list(LEV_BINS))
    total = int(counts.sum())
    bins = [
        {
            "from": float(edges[i]),
            "to": float(edges[i + 1]),
            "label": f"{edges[i]:g}-{edges[i + 1]:g}x".replace(".", ","),
            "count": int(counts[i]),
            "share": _f(counts[i] / total) if total else None,
        }
        for i in range(len(counts))
    ]
    return {
        "available": True,
        "n_obs": len(lev),
        "flat_days": int((lev <= 1e-9).sum()),
        "above_1x_days": int((lev > 1.0 + 1e-9).sum()),
        "mean": _f(float(lev.mean())),
        "median": _f(float(lev.median())),
        "p95": _f(float(lev.quantile(0.95))),
        "max": _f(float(lev.max())),
        "bins": bins,
        "label": "Istogramma della leva usata",
    }


# ---------------------------------------------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------------------------------------------
def _md_report(payload: Mapping[str, Any]) -> str:
    w = payload.get("window", {})
    lines: list[str] = [
        "# Report di backtest",
        "",
        f"Generato il {payload.get('generated_at')} · finestra {w.get('start')} → {w.get('end')} "
        f"({_it(w.get('years'), 1)} anni, {w.get('n_obs')} osservazioni) · "
        f"capitale iniziale {_it(payload.get('initial_capital'), 0)} $",
        "",
        f"_{DISCLAIMER}_",
        "",
        "## Metriche per strategia",
        "",
        "| Strategia | Sharpe | Sortino | CAGR | Vol | Max DD | Hit rate | PSR | Oper. | Peso |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = [payload.get("master", {}), *payload.get("strategies", [])]
    for s in rows:
        if not s:
            continue
        m = s.get("metrics", {}) or {}
        lines.append(
            f"| {s.get('name') or s.get('id')} | {_it(m.get('sharpe'))} | {_it(m.get('sortino'))} | "
            f"{_pct(m.get('cagr'))} | {_pct(m.get('ann_vol'))} | {_pct(m.get('max_drawdown'))} | "
            f"{_pct(m.get('hit_rate'))} | {_it(m.get('psr'))} | {m.get('n_trades', 'n/d')} | "
            f"{_it(s.get('weight'), 3)} |"
        )

    lines += ["", "## Risultati per regime", ""]
    regimes = sorted({r for s in payload.get("strategies", []) for r in (s.get("by_regime") or {})})
    regimes += [r for r in sorted(payload.get("master", {}).get("by_regime") or {}) if r not in regimes]
    if regimes:
        lines += [
            "| Strategia | " + " | ".join(regimes) + " |",
            "|---|" + "---|" * len(regimes),
        ]
        for s in rows:
            by = s.get("by_regime") or {}
            cells = [f"{_it((by.get(r) or {}).get('sharpe'))}" for r in regimes]
            lines.append(f"| {s.get('name') or s.get('id')} | " + " | ".join(cells) + " |")
        lines += ["", "_Valori: Sharpe annualizzato nel regime._"]
    else:
        lines.append("Nessuna etichetta di regime disponibile nel backtest.")

    lines += [
        "",
        "## Risultati per crisi",
        "",
        "| Episodio | Disponibile | Rend. totale | Sharpe | Max DD |",
        "|---|---|---|---|---|",
    ]
    for ident, c in (payload.get("master", {}).get("by_crisis") or {}).items():
        if c.get("available"):
            lines.append(
                f"| {c.get('name', ident)} | sì | {_pct(c.get('total_return'))} | {_it(c.get('sharpe'))} | "
                f"{_pct(c.get('max_drawdown'))} |"
            )
        else:
            lines.append(f"| {c.get('name', ident)} | no — {c.get('reason', 'dati assenti')} | — | — | — |")

    lines += [
        "",
        "## Sensibilità ai costi",
        "",
        "| Strategia | Sharpe 1x | Sharpe 2x | CAGR 2x | Pareggio (bps) |",
        "|---|---|---|---|---|",
    ]
    for s in rows:
        c = s.get("costs") or {}
        if c.get("available"):
            lines.append(
                f"| {s.get('name') or s.get('id')} | {_it((c.get('x1') or {}).get('sharpe'))} | "
                f"{_it((c.get('x2') or {}).get('sharpe'))} | {_pct((c.get('x2') or {}).get('cagr'))} | "
                f"{_it(c.get('breakeven_cost_bps'), 1)} |"
            )
        else:
            lines.append(f"| {s.get('name') or s.get('id')} | n/d — {c.get('reason', 'n/d')} | n/d | n/d | n/d |")

    pbo = payload.get("pbo")
    lines += ["", "## DSR e PBO", "", "| Strategia | Sharpe | DSR (prob.) | SR0 | Prove |", "|---|---|---|---|---|"]
    for s in rows:
        d = s.get("dsr") or {}
        lines.append(
            f"| {s.get('name') or s.get('id')} | {_it((s.get('metrics') or {}).get('sharpe'))} | "
            f"{_it(d.get('dsr_prob'), 3)} | {_it(d.get('sr0'), 3)} | {d.get('n_trials', 'n/d')} |"
        )
    if pbo:
        lines += [
            "",
            f"PBO (CSCV, {pbo.get('n_variants', 'n/d')} varianti, {pbo.get('n_combinations', 'n/d')} combinazioni, "
            f"fonte: {pbo.get('source')}): **{_it(pbo.get('pbo'), 3)}** · "
            f"pendenza IS→OOS {_it(pbo.get('slope'), 3)} · "
            f"probabilità di Sharpe OOS negativo {_it(pbo.get('prob_oos_loss'), 3)}",
        ]
    else:
        reasons = [r for r in payload.get("skipped", []) if "PBO" in r]
        lines += ["", "PBO non disponibile" + (": " + "; ".join(reasons) if reasons else ".")]

    lines += ["", "## Ciclo di vita", "", "| Strategia | Stato | Peso | Spiegazione |", "|---|---|---|---|"]
    for s in payload.get("strategies", []):
        lines.append(
            f"| {s.get('name') or s.get('id')} | {s.get('lifecycle')} | {_it(s.get('weight'), 3)} | "
            f"{s.get('explanation')} |"
        )
    zero = payload.get("zero_weight") or []
    if zero:
        lines += ["", "Strategie a peso zero:", ""]
        lines += [f"- **{z.get('name', z.get('id'))}**: {z.get('reason')}" for z in zero]

    lev = payload.get("leverage") or {}
    lines += ["", "## Istogramma della leva", ""]
    if lev.get("available"):
        lines += [
            f"Leva media {_it(lev.get('mean'))}x · mediana {_it(lev.get('median'))}x · "
            f"p95 {_it(lev.get('p95'))}x · massima {_it(lev.get('max'))}x · "
            f"giorni sopra 1x: {lev.get('above_1x_days')} su {lev.get('n_obs')}",
            "",
            "| Fascia | Giorni | Quota |",
            "|---|---|---|",
        ]
        lines += [f"| {b['label']} | {b['count']} | {_pct(b['share'])} |" for b in lev.get("bins", [])]
    else:
        lines.append(f"Non disponibile: {lev.get('reason', 'n/d')}")

    stress = payload.get("stress")
    lines += ["", "## Stress test", ""]
    if stress and stress.get("episodes"):
        lines += [
            f"Posizione testata: {stress.get('position', {}).get('label', 'n/d')} · "
            f"fonte prezzi: {stress.get('source') or 'n/d'} (asof {stress.get('asof') or 'n/d'})",
            "",
            "| Scenario | Rend. totale | Max DD | Peggior seduta | Margin call | Liquidazione |",
            "|---|---|---|---|---|---|",
        ]
        for e in stress.get("episodes", []) + stress.get("scenarios", []):
            if not e.get("available"):
                lines.append(
                    f"| {e.get('name', e.get('id'))} | non disponibile — {e.get('reason', 'n/d')} | — | — | — | — |"
                )
                continue
            lines.append(
                f"| {e.get('name', e.get('id'))} | {_pct(e.get('total_return'))} | {_pct(e.get('max_drawdown'))} | "
                f"{_pct(e.get('worst_day'))} | {'sì' if e.get('margin_call') else 'no'} | "
                f"{'sì' if e.get('liquidated') else 'no'} |"
            )
    else:
        lines.append("Stress test non disponibile: " + str((stress or {}).get("reason", "modulo o prezzi assenti")))

    if payload.get("skipped"):
        lines += ["", "## Parti non calcolate", ""] + [f"- {s}" for s in payload["skipped"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------------------------------------------
def run_backtest_report(
    md: MarketData,
    strategies: Sequence[Any],
    risk: RiskConfig,
    start: date | None = None,
    end: date | None = None,
    out_dir: str | Path = "out/backtest",
    store: StateStore | None = None,
    allocator: Any | None = None,
    feature_builder: Any | None = None,
    regime_model: Any | None = None,
    variants: Mapping[str, Sequence[float]] | pd.DataFrame | None = None,
    thresholds: Thresholds | None = None,
    stress_leverage: float = 1.0,
    write_state_json: bool = True,
) -> dict[str, Any]:
    """Run the backtest and write ``backtest.md`` + ``validation.json``; returns the JSON payload.

    Tolerates an empty strategy list and a ``MarketData`` whose columns are partly (or entirely) NaN: every piece
    that cannot be computed is reported in ``skipped`` instead of raising.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    strategy_list = list(strategies)

    # The backtest must NEVER write into the live state: its signals, decisions, equity and trade logs would
    # mix with the real paper-trading history and the dashboard would show backtest rows as live ones. The
    # session therefore logs into a store of its own under the report directory; the `store` argument is used
    # only for validation.json and the trials registry, which are the two things the live system reads back.
    session_store = StateStore(out_path / "session")
    res = run_backtest(
        md,
        strategy_list,
        risk,
        start=start,
        end=end,
        allocator=allocator,
        feature_builder=feature_builder,
        regime_model=regime_model,
        store=session_store,
    )

    equity = res.equity
    if equity.empty:
        skipped.append("Backtest senza giorni validi: nessun prezzo utilizzabile nella finestra richiesta")

    ids = [s for s in equity.columns if s not in {"master", "buy_hold", "master_1x"}] if not equity.empty else []
    curves: dict[str, pd.Series] = {}
    if not equity.empty and "master" in equity.columns:
        curves["master"] = equity["master"]
    for sid in ids:
        curves[sid] = equity[sid]

    master_ret = _returns(curves.get("master", pd.Series(dtype=float)))
    exposure_master = _master_exposure(res.snapshots)
    cost_per_turn = _cost_per_turn(risk)
    labels = _regime_labels(res, None)
    try:
        from engine.portfolio.stress import episodes_from_config

        episodes = episodes_from_config()
    except (ImportError, OSError, ValueError) as exc:
        episodes = []
        skipped.append(f"Episodi storici non caricati: {exc}")

    dsr_by_name, trials_info = _register_and_deflate(store, out_path, curves, variants, skipped)

    shadow_matrix = pd.DataFrame({sid: _returns(curves[sid]) for sid in ids}) if ids else pd.DataFrame()
    pbo = _pbo(variants, shadow_matrix, skipped)
    pbo_value = _f((pbo or {}).get("pbo"))

    name_by_id = {str(getattr(s, "id", "")): str(getattr(s, "name", "") or getattr(s, "id", "")) for s in strategy_list}
    family_by_id = {str(getattr(s, "id", "")): str(getattr(s, "family", "")) for s in strategy_list}

    def block(key: str, name: str, exposure: pd.Series) -> dict[str, Any]:
        r = _returns(curves[key])
        turn = _turnover(exposure, r.index) if not exposure.empty else pd.Series(dtype=float)
        summ = mx.summary(r, positions=exposure.to_numpy(dtype=float) if not exposure.empty else None)
        perf = res.summary.get(key)
        if perf is not None:
            summ["n_trades"] = int(perf.n_trades)
            summ["profit_factor"] = _f(perf.profit_factor)
        costs = _cost_table(r, turn, cost_per_turn)
        years = len(r) / TRADING_DAYS
        return {
            "id": key,
            "name": name,
            "metrics": {k: _f(v) if isinstance(v, float) else v for k, v in summ.items()},
            "costs": costs,
            "dsr": dsr_by_name.get(key, {"dsr_prob": None, "sr0": None, "n_trials": trials_info.get("n_trials")}),
            "pbo": pbo_value,
            "by_regime": _slice_by_regime(r, labels),
            "by_crisis": _slice_by_crisis(r, episodes),
            "years": _f(years),
            "n_obs": len(r),
        }

    master_block = block("master", "Master (portafoglio)", exposure_master) if "master" in curves else {}
    strategies_out: list[dict[str, Any]] = []
    zero_weight: list[dict[str, Any]] = []
    thr = thresholds or Thresholds()
    for sid in ids:
        exposure = _signal_exposure(res.signals, sid)
        b = block(sid, name_by_id.get(sid, sid), exposure)
        b["family"] = family_by_id.get(sid, "")
        costs_x2 = (b["costs"].get("x2") or {}) if b["costs"].get("available") else {}
        record = {
            "validated": bool(b["n_obs"] >= 2),
            "dsr_prob": (b["dsr"] or {}).get("dsr_prob"),
            "pbo": pbo_value,
            "oos_sharpe_double_cost": costs_x2.get("sharpe"),
            "n_trades": (b["metrics"] or {}).get("n_trades"),
            "years": b["years"],
            "cusum_alarm": False,
        }
        b["lifecycle_record"] = record
        b["lifecycle"] = decide_lifecycle(record, thr)
        b["explanation"] = explain(record, thr)
        strategies_out.append(b)

    active = [s for s in strategies_out if s["lifecycle"] == "active"]
    for s in strategies_out:
        is_active = any(s is a for a in active)
        s["weight"] = round(1.0 / len(active), 6) if is_active else 0.0
        if s["weight"] == 0.0:
            zero_weight.append({"id": s["id"], "name": s["name"], "reason": s["explanation"]})
    if master_block:
        master_block["weight"] = 1.0
        master_block["lifecycle"] = "master"
        master_block["explanation"] = "Portafoglio master: somma pesata delle strategie attive."

    # stress test (optional module)
    stress_payload: dict[str, Any] | None = None
    try:
        from engine.portfolio.stress import PositionSpec, stress_report

        price_col = next((c for c in ("brent_spot", "brent_front_close", "brent_cont") if c in md.prices.columns), None)
        series = (
            pd.to_numeric(md.prices[price_col], errors="coerce").dropna()
            if price_col is not None
            else pd.Series(dtype=float)
        )
        if series.empty:
            stress_payload = {"reason": "nessuna serie di prezzi reale disponibile per lo stress test"}
            skipped.append("Stress test non calcolato: nessun prezzo disponibile")
        else:
            pos = PositionSpec(
                leverage=float(stress_leverage),
                direction=1,
                initial_capital=float(risk.initial_capital),
                margin_rate=float(risk.margin_rate),
                stop_out_level=float(risk.stop_out_margin_level),
                dead_fraction=float(risk.dead_equity_fraction),
            )
            meta = md.meta or {}
            sources = meta.get("sources") or {}
            source = str(sources.get(price_col) or meta.get("prices_source") or "") or "n/d"
            stress_payload = stress_report(series, pos, price_source=f"{price_col} ({source})").to_dict()
    except ImportError as exc:
        stress_payload = {"reason": f"modulo stress non importabile: {exc}"}
        skipped.append(f"Stress test non calcolato: {exc}")

    w_start = str(pd.Timestamp(equity.index[0]).date()) if not equity.empty else (start.isoformat() if start else None)
    w_end = str(pd.Timestamp(equity.index[-1]).date()) if not equity.empty else (end.isoformat() if end else None)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "source": "backtest",
        "asof": w_end,
        "initial_capital": _f(risk.initial_capital),
        "window": {
            "start": w_start,
            "end": w_end,
            "n_obs": len(master_ret),
            "years": _f(len(master_ret) / TRADING_DAYS),
        },
        "master": master_block,
        "strategies": strategies_out,
        "zero_weight": zero_weight,
        "pbo": pbo,
        "trials": trials_info,
        "leverage": _leverage_histogram(res.snapshots),
        "stress": stress_payload,
        "regimes": sorted({str(v) for v in labels.to_numpy()}) if not labels.empty else [],
        "crises": sorted((master_block.get("by_crisis") or {}).keys()),
        "thresholds": {
            "dsr_min": thr.dsr_min,
            "pbo_max": thr.pbo_max,
            "oos_sharpe_min": thr.oos_sharpe_min,
            "min_trades": thr.min_trades,
            "min_years": thr.min_years,
        },
        "meta": res.meta,
        "skipped": skipped,
        "disclaimer": DISCLAIMER,
    }
    payload = _sanitize(payload)

    markdown = out_path / "backtest.md"
    markdown.write_text(_md_report(payload), encoding="utf-8")
    json_path = out_path / "validation.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    state_json = None
    if store is not None and write_state_json:
        store.write_json("validation.json", payload)
        state_json = store.path("validation.json")
    paths = ReportPaths(out_dir=out_path, markdown=markdown, json=json_path, state_json=state_json)
    payload["paths"] = {
        "out_dir": str(paths.out_dir),
        "markdown": str(paths.markdown),
        "json": str(paths.json),
        "state_json": None if paths.state_json is None else str(paths.state_json),
    }
    log.info(
        "backtest report written to %s (%d strategies, %d skipped parts)", markdown, len(strategies_out), len(skipped)
    )
    return payload


def run_validation(
    md: MarketData,
    strategies: Sequence[Any],
    risk: RiskConfig,
    out_dir: str | Path = "out/validation",
    years: float = 15.0,
    end: date | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """The same report over the last ``years`` of data: the window the weekly job validates on.

    Every optional piece (stress, PBO, DSR) is imported lazily by :func:`run_backtest_report` and what cannot be
    computed is listed in ``skipped``, so this runs in a job where some modules or sources are missing.
    """
    idx = pd.DatetimeIndex(md.prices.index) if not md.prices.empty else pd.DatetimeIndex([])
    last = pd.Timestamp(end) if end is not None else (idx.max() if len(idx) else None)
    if last is None:
        return run_backtest_report(md, strategies, risk, None, None, out_dir, **kwargs)
    first = last - pd.Timedelta(days=round(365.25 * float(years)))
    return run_backtest_report(md, strategies, risk, first.date(), last.date(), out_dir, **kwargs)
