"""Stress tests: historical analogs and labelled synthetic scenarios (brief §4.4-§4.5, §12, risk.json).

What a position with a given leverage and direction would have done through the crises in
``config/events.yaml`` ``historical_episodes``, and what the two synthetic shocks the brief asks for would do to
it.  The margin arithmetic is the broker's own (:mod:`engine.broker.margin`): one formula, one place, so a stress
test cannot disagree with the account it is supposed to describe.

**Real data or nothing.**  An episode the supplied price series does not cover is reported with
``available: false`` and a reason.  It is never simulated, never interpolated, never replaced by a "similar"
period.  Synthetic SCENARIOS are a different thing: the brief (§4.5) asks for them explicitly, so they exist —
and every one of them carries ``synthetic: true`` and an Italian label that says so ("scenario sintetico: ...").

Verified reference facts (what the episodes are, and what the series must show when it covers them)
-----------------------------------------------------------------------------------------------------
=============================  ============  =====================================================================
Episode                        Key date      Reference fact
=============================  ============  =====================================================================
Gulf War 1990-91               1990-08-02    Iraq invades Kuwait; Brent roughly doubles over the following weeks
                               1991-01-17    Desert Storm begins: Brent falls about **-33 % in one session**
                                             (~30 $ to ~20 $), the largest one-day drop of the modern era
Abqaiq attack 2019             2019-09-14    Drone attack on the Saudi plant (Saturday, market closed)
                               2019-09-16    Reopening about **+15 %** at the open, the largest intraday jump on
                                             record; settlement about +14.6 %
Covid / price war 2020         2020-03-09    After the OPEC+ breakdown of 2020-03-06, Brent about **-24 %** in one
                                             session; the episode continues to the April collapse (negative WTI)
Russia-Ukraine 2022            2022-02-24    Invasion: Brent above 100 $ for the first time since 2014
Truce June-July 2026           2026-06-15    Hormuz truce: Brent deflates to **71-76 $** (docs/BRIEF.md §4,
                               2026-07-20    config/events.yaml ``truce_2026``)
=============================  ============  =====================================================================

The first four rows are market history; the 2026 row comes from the repository's own brief and event config.
:func:`historical_analogs` does not take any of them on trust: where an episode carries an expected one-day move
(:data:`EXPECTED_WORST_DAY`) and the series covers it, the report includes a ``fact_check`` comparing the expected
figure with the observed one.  A mismatch is reported, not corrected.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.broker import margin as mg
from engine.core.eventcal import EventCalendar

__all__ = [
    "EXPECTED_ONE_DAY_MOVE",
    "EXPECTED_WORST_DAY",
    "SYNTHETIC_SCENARIOS",
    "HistoricalAnalog",
    "PositionSpec",
    "StressReport",
    "SyntheticOutcome",
    "apply_position",
    "episodes_from_config",
    "historical_analogs",
    "stress_report",
    "synthetic_scenarios",
]

# Expected one-day move ON A STATED DATE (simple return of the supplied series), for the fact check.
# The tolerances are wide on purpose: the reference figures are quoted on the front future and sometimes
# intraday, while the series checked here may be the Dated Brent spot, and the two differ by a few points on
# exactly these sessions (e.g. 2019-09-16: +14.6 % on the front settlement, +11.7 % on the EIA spot).
EXPECTED_ONE_DAY_MOVE: dict[str, dict[str, Any]] = {
    "gulf_war_1990": {
        "date": "1991-01-17",
        "expected": -0.33,
        "tolerance": 0.08,
        "note": "avvio di Desert Storm: circa -33% in una seduta",
    },
    "abqaiq_2019": {
        "date": "2019-09-16",
        "expected": 0.15,
        "tolerance": 0.05,
        "note": "riapertura dopo l'attacco ad Abqaiq: circa +15% in apertura",
    },
    "covid_2020": {
        "date": "2020-03-09",
        "expected": -0.24,
        "tolerance": 0.06,
        "note": "rottura OPEC+ e guerra dei prezzi: circa -24% in una seduta",
    },
}
EXPECTED_WORST_DAY = EXPECTED_ONE_DAY_MOVE  # backwards-compatible alias

# Synthetic shocks (brief §4.5 plus the weekend gap of §4.2): daily simple returns of the front price.
SYNTHETIC_SCENARIOS: dict[str, dict[str, Any]] = {
    "reopening_20_3d": {
        "label": "scenario sintetico: riapertura -20% in 3 giorni",
        "moves": [-0.0717, -0.0717, -0.0717],  # (1 + x)^3 = 0.80
    },
    "reopening_30_3d": {
        "label": "scenario sintetico: riapertura -30% in 3 giorni",
        "moves": [-0.1120, -0.1120, -0.1120],  # (1 + x)^3 = 0.70
    },
    "escalation_10_1d": {
        "label": "scenario sintetico: escalation +10% in una seduta",
        "moves": [0.10],
    },
    "escalation_15_1d": {
        "label": "scenario sintetico: escalation +15% in una seduta",
        "moves": [0.15],
    },
    "weekend_gap_up_8": {
        "label": "scenario sintetico: gap di weekend +8%",
        "moves": [0.08],
    },
    "weekend_gap_down_8": {
        "label": "scenario sintetico: gap di weekend -8%",
        "moves": [-0.08],
    },
}


@dataclass(frozen=True)
class PositionSpec:
    """The position put through the stress test: ``leverage`` x equity of notional, ``direction`` +1 long / -1 short."""

    leverage: float = 1.0
    direction: int = 1
    initial_capital: float = 10_000.0
    margin_rate: float = 0.10
    stop_out_level: float = 0.50
    dead_fraction: float = 0.05

    @property
    def label(self) -> str:
        verso = "long" if self.direction >= 0 else "short"
        return f"{verso} {self.leverage:g}x su {self.initial_capital:,.0f} $".replace(",", ".")

    def to_dict(self) -> dict[str, Any]:
        return {
            "leverage": float(self.leverage),
            "direction": int(self.direction),
            "initial_capital": float(self.initial_capital),
            "margin_rate": float(self.margin_rate),
            "stop_out_level": float(self.stop_out_level),
            "dead_fraction": float(self.dead_fraction),
            "label": self.label,
        }


def _f(x: Any) -> float | None:
    if x is None:
        return None
    v = float(x)
    return None if not math.isfinite(v) else v


# ---------------------------------------------------------------------------------------------------------------
# the position arithmetic
# ---------------------------------------------------------------------------------------------------------------
def apply_position(
    prices: pd.Series,
    position: PositionSpec,
    max_path_points: int = 400,
) -> dict[str, Any]:
    """Walk a price path with a fixed position (opened at the first price, never resized) and report the damage.

    Futures-style account, exactly as :mod:`engine.broker.margin` models it: cash is not debited, so
    ``equity(t) = capital + qty * (p_t - p_0)`` with ``qty = direction * leverage * capital / p_0`` barrels.  The
    margin requirement is ``margin_rate * |qty| * p_t`` and the margin level ``equity / margin``.

    * ``margin_call``: the margin level fell below 1.0 — the equity no longer covers the requirement.
    * ``liquidated``: it fell below ``stop_out_level`` (0.5), where the broker force-closes.  From that day the
      equity is frozen at the liquidation value: the position no longer exists, so later price action is
      irrelevant (this is why a liquidated path can show a smaller final loss than the raw price move).
    * ``liquidation_price`` / ``days_to_liquidation``: the price level from
      :func:`engine.broker.margin.liquidation_price` and the number of sessions before it was first breached.
    * ``dead``: the account fell to or below ``dead_fraction`` of the initial capital (trading halts until Reset).

    The path is never resized or rolled: this is a stress test of a static exposure, not a backtest.
    """
    px = pd.to_numeric(pd.Series(prices), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    px = px[px > 0]
    if len(px) < 2:
        return {"available": False, "reason": "meno di due prezzi utilizzabili nel periodo"}

    p0 = float(px.iloc[0])
    cap = float(position.initial_capital)
    qty = float(position.direction) * float(position.leverage) * cap / p0
    values = px.to_numpy(dtype=float)
    equity = cap + qty * (values - p0)
    margin_used = position.margin_rate * abs(qty) * values
    with np.errstate(divide="ignore", invalid="ignore"):
        level = np.where(margin_used > 0, equity / margin_used, np.inf)

    liq_price = mg.liquidation_price(qty, p0, cap, position.margin_rate, position.stop_out_level)
    breach = np.flatnonzero(level < position.stop_out_level)
    liq_idx = int(breach[0]) if breach.size else None
    if liq_idx is not None:
        # The broker force-closes at the session where the margin level breaks the stop-out (its own rule is to
        # reduce at the close), so the loss of that session is taken in full; the equity is floored at zero
        # because the account cannot go negative, and frozen afterwards: the position no longer exists.
        equity = equity.copy()
        equity[liq_idx:] = max(float(equity[liq_idx]), 0.0)

    peak = np.maximum.accumulate(np.maximum(equity, 1e-12))
    dd = 1.0 - equity / peak
    eq_ret = np.diff(equity) / np.maximum(equity[:-1], 1e-12)
    px_ret = np.diff(values) / values[:-1]
    worst_i = int(np.argmin(px_ret)) if px_ret.size else None

    step = max(1, len(px) // max_path_points)
    path = [
        {"t": str(pd.Timestamp(ts).date()), "price": round(float(p), 4), "equity": round(float(e), 2)}
        for ts, p, e in zip(px.index[::step], values[::step], equity[::step])
    ]
    if step > 1:
        path.append(
            {
                "t": str(pd.Timestamp(px.index[-1]).date()),
                "price": round(float(values[-1]), 4),
                "equity": round(float(equity[-1]), 2),
            }
        )
    return {
        "available": True,
        "n_days": len(px),
        "start": str(pd.Timestamp(px.index[0]).date()),
        "end": str(pd.Timestamp(px.index[-1]).date()),
        "price_start": _f(p0),
        "price_end": _f(values[-1]),
        "price_min": _f(values.min()),
        "price_max": _f(values.max()),
        "price_return": _f(values[-1] / p0 - 1.0),
        "qty_bbl": _f(qty),
        "equity_start": _f(cap),
        "equity_end": _f(equity[-1]),
        "equity_min": _f(equity.min()),
        "total_return": _f(equity[-1] / cap - 1.0),
        "max_drawdown": _f(dd.max()),
        "worst_day": _f(eq_ret.min()) if eq_ret.size else None,
        "worst_day_price": _f(px_ret.min()) if px_ret.size else None,
        "worst_day_date": (None if worst_i is None else str(pd.Timestamp(px.index[worst_i + 1]).date())),
        "margin_call": bool(np.any(level < 1.0)),
        "min_margin_level": _f(level.min()) if np.isfinite(level).any() else None,
        "liquidated": liq_idx is not None,
        "liquidation_price": _f(liq_price),
        "days_to_liquidation": (None if liq_idx is None else int(liq_idx)),
        "liquidation_date": (None if liq_idx is None else str(pd.Timestamp(px.index[liq_idx]).date())),
        "dead": bool(mg.is_dead(float(equity[-1]), cap, position.dead_fraction)),
        "equity_path": path,
    }


# ---------------------------------------------------------------------------------------------------------------
# historical analogs
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class HistoricalAnalog:
    """One episode of ``config/events.yaml`` applied to the position (or the reason it could not be)."""

    id: str
    name: str
    start: str
    end: str | None
    available: bool
    reason: str | None = None
    note: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    fact_check: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "available": bool(self.available),
            "synthetic": False,
        }
        if self.reason:
            d["reason"] = self.reason
        if self.note:
            d["note"] = self.note
        if self.fact_check is not None:
            d["fact_check"] = self.fact_check
        d.update(self.result)
        return d


def episodes_from_config(config_dir: Path | None = None) -> list[dict[str, Any]]:
    """``historical_episodes`` from ``config/events.yaml`` (ids, names, dates, notes)."""
    return EventCalendar(config_dir=config_dir).episodes()


def _as_date(v: Any) -> date | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(str(v)).date()
    except (ValueError, TypeError):
        return None


def _slice(
    prices: pd.Series, start: date, end: date | None, tolerance_days: int, min_days: int
) -> tuple[pd.Series, str | None]:
    """The episode window of the series, or ``(empty, reason in Italian)`` when the coverage is not good enough."""
    if prices.empty:
        return prices.iloc[:0], "serie prezzi vuota"
    idx = pd.DatetimeIndex(prices.index)
    first, last = idx.min(), idx.max()
    end_eff = pd.Timestamp(end) if end is not None else last
    window = prices.loc[pd.Timestamp(start) : end_eff]
    if window.empty:
        return window, (f"nessun dato tra {start} e {end_eff.date()} (serie da {first.date()} a {last.date()})")
    tol = pd.Timedelta(days=tolerance_days)
    if pd.DatetimeIndex(window.index).min() > pd.Timestamp(start) + tol:
        return window.iloc[:0], (
            f"copertura parziale: primo dato {pd.Timestamp(window.index[0]).date()}, inizio episodio {start}"
        )
    if end is not None and pd.DatetimeIndex(window.index).max() < end_eff - tol:
        return window.iloc[:0], (
            f"copertura parziale: ultimo dato {pd.Timestamp(window.index[-1]).date()}, fine episodio {end}"
        )
    if len(window) < min_days:
        return window.iloc[:0], f"solo {len(window)} giorni di dati nel periodo (minimo {min_days})"
    return window, None


def _fact_check(episode_id: str, window: pd.Series) -> dict[str, Any] | None:
    """Compare the reference one-day move of :data:`EXPECTED_ONE_DAY_MOVE` with what the series actually shows.

    The comparison is made ON THE STATED DATE (the episode's own worst day may be a different, later session —
    2020-04-21 rather than 2020-03-09, for instance — and is reported alongside for context).  A mismatch is
    reported as ``matches: false``, never corrected and never hidden.
    """
    spec = EXPECTED_ONE_DAY_MOVE.get(episode_id)
    if spec is None or len(window) < 2:
        return None
    ret = window.pct_change()
    worst_label: Any = ret.idxmin()
    ts = pd.Timestamp(str(spec["date"]))
    observed: float | None = None
    if ts in ret.index:
        value = float(ret.loc[ts])
        observed = value if math.isfinite(value) else None
    expected = float(spec["expected"])
    tol = float(spec["tolerance"])
    return {
        "expected_move": expected,
        "expected_date": spec["date"],
        "observed_move": observed,
        "observed_worst_day": float(ret.min()),
        "observed_worst_date": str(pd.Timestamp(worst_label).date()),
        "tolerance": tol,
        "matches": bool(observed is not None and abs(observed - expected) <= tol),
        "reason": None if observed is not None else "la data di riferimento non è nella serie fornita",
        "note": spec["note"],
    }


def historical_analogs(
    prices: pd.Series,
    position: PositionSpec | None = None,
    episodes: Sequence[Mapping[str, Any]] | None = None,
    tolerance_days: int = 10,
    min_days: int = 5,
    config_dir: Path | None = None,
) -> list[HistoricalAnalog]:
    """Apply ``position`` to every episode's REAL price path; episodes without data are marked unavailable.

    ``prices`` is a daily series of real Brent prices (spot or front settlement) with a ``DatetimeIndex``.
    ``tolerance_days`` is how far the first/last observation may sit from the episode's boundaries before the
    coverage counts as partial (weekends and holidays, not months of missing data).
    """
    pos = position or PositionSpec()
    eps = list(episodes) if episodes is not None else episodes_from_config(config_dir)
    series = pd.to_numeric(pd.Series(prices), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if not series.empty:
        series = series.sort_index()
    out: list[HistoricalAnalog] = []
    for ep in eps:
        start, end = _as_date(ep.get("start")), _as_date(ep.get("end"))
        name = str(ep.get("name", ep.get("id", "?")))
        ident = str(ep.get("id", name))
        if start is None:
            out.append(
                HistoricalAnalog(ident, name, str(ep.get("start")), None, False, reason="data di inizio non valida")
            )
            continue
        window, reason = _slice(series, start, end, tolerance_days, min_days)
        if reason is not None:
            out.append(
                HistoricalAnalog(
                    ident,
                    name,
                    start.isoformat(),
                    None if end is None else end.isoformat(),
                    False,
                    reason=reason,
                    note=ep.get("note"),
                )
            )
            continue
        result = apply_position(window, pos)
        out.append(
            HistoricalAnalog(
                ident,
                name,
                start.isoformat(),
                None if end is None else end.isoformat(),
                bool(result.get("available", False)),
                reason=result.get("reason"),
                note=ep.get("note"),
                result={k: v for k, v in result.items() if k not in {"available", "reason"}},
                fact_check=_fact_check(ident, window),
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------------------
# synthetic scenarios (labelled)
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class SyntheticOutcome:
    """A labelled synthetic shock applied to the position.  ``synthetic`` is always True."""

    id: str
    label: str
    moves: list[float]
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "name": self.label,
            "synthetic": True,
            "approx": True,
            "available": True,
            "daily_moves": [float(m) for m in self.moves],
            "n_sessions": len(self.moves),
        }
        d.update({k: v for k, v in self.result.items() if k != "available"})
        return d


def synthetic_scenarios(
    reference_price: float,
    position: PositionSpec | None = None,
    specs: Mapping[str, Mapping[str, Any]] | None = None,
    start: date | None = None,
) -> list[SyntheticOutcome]:
    """The brief's synthetic shocks (§4.5) applied to ``position`` from ``reference_price``.

    These paths are INVENTED on purpose — they are scenarios, not history — and every record says so
    (``synthetic: true``, Italian label "scenario sintetico: ...").  ``reference_price`` must be a real, current
    price: the scenario is "what happens to today's book if ...".
    """
    pos = position or PositionSpec()
    if not math.isfinite(float(reference_price)) or float(reference_price) <= 0:
        raise ValueError("reference_price must be a positive, finite real price")
    base = pd.Timestamp(start or date(2000, 1, 3))
    out: list[SyntheticOutcome] = []
    for ident, spec in (specs or SYNTHETIC_SCENARIOS).items():
        moves = [float(m) for m in spec["moves"]]
        path = [float(reference_price)]
        for m in moves:
            path.append(path[-1] * (1.0 + m))
        idx = pd.DatetimeIndex([base + pd.Timedelta(days=i) for i in range(len(path))])
        series = pd.Series(path, index=idx)
        out.append(
            SyntheticOutcome(
                id=ident, label=str(spec["label"]), moves=moves, result=apply_position(series, pos, max_path_points=50)
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class StressReport:
    """Historical analogs plus synthetic scenarios for one position, ready for ``risk.json``."""

    position: PositionSpec
    episodes: list[HistoricalAnalog] = field(default_factory=list)
    scenarios: list[SyntheticOutcome] = field(default_factory=list)
    price_source: str = ""
    price_asof: str | None = None
    reference_price: float | None = None

    @property
    def worst_episode(self) -> HistoricalAnalog | None:
        done = [e for e in self.episodes if e.available and e.result.get("total_return") is not None]
        return min(done, key=lambda e: float(e.result["total_return"])) if done else None

    def to_dict(self) -> dict[str, Any]:
        worst = self.worst_episode
        return {
            "position": self.position.to_dict(),
            "source": self.price_source or None,
            "asof": self.price_asof,
            "reference_price": _f(self.reference_price),
            "episodes": [e.to_dict() for e in self.episodes],
            "scenarios": [s.to_dict() for s in self.scenarios],
            "n_available": sum(1 for e in self.episodes if e.available),
            "n_unavailable": sum(1 for e in self.episodes if not e.available),
            "worst_episode": (None if worst is None else {"id": worst.id, "name": worst.name, **worst.result}),
            "labels": {
                "title": "Stress test",
                "episodes": "Analoghi storici (dati reali; gli episodi senza dati sono dichiarati non disponibili)",
                "scenarios": "Scenari sintetici (percorsi inventati, dichiarati come tali)",
                "total_return": "Rendimento totale della posizione",
                "max_drawdown": "Drawdown massimo",
                "worst_day": "Peggior seduta",
                "margin_call": "Richiesta di margine",
                "liquidated": "Liquidazione forzata",
                "days_to_liquidation": "Giorni fino alla liquidazione",
                "position": f"Posizione testata: {self.position.label}",
            },
        }


def stress_report(
    prices: pd.Series,
    position: PositionSpec | None = None,
    episodes: Sequence[Mapping[str, Any]] | None = None,
    reference_price: float | None = None,
    price_source: str = "",
    config_dir: Path | None = None,
    include_synthetic: bool = True,
) -> StressReport:
    """Full stress report: every configured episode plus the synthetic scenarios, for one position.

    ``reference_price`` defaults to the last real price of the series (the synthetic scenarios have to start from
    a real level).  When the series is empty every episode is reported unavailable and the synthetic scenarios are
    skipped: without a real price there is nothing honest to anchor them to.
    """
    pos = position or PositionSpec()
    series = pd.to_numeric(pd.Series(prices), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    series = series.sort_index() if not series.empty else series
    ref = reference_price
    if ref is None and not series.empty:
        ref = float(series.iloc[-1])
    asof = str(pd.Timestamp(series.index[-1]).date()) if not series.empty else None
    report = StressReport(
        position=pos,
        episodes=historical_analogs(series, pos, episodes, config_dir=config_dir),
        price_source=price_source,
        price_asof=asof,
        reference_price=ref,
    )
    if include_synthetic and ref is not None:
        report.scenarios = synthetic_scenarios(ref, pos)
    return report
