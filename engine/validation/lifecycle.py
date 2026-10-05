"""Strategy lifecycle decisions (docs/STRATEGIES.md intro; brief §12).

    ricerca → incubazione (solo conto ombra, peso zero) → attiva (peso nel master) → ritirata

Rules (pure function of the validation record, nothing is promoted on a hunch):

* ``research`` until a validation report has produced the numbers (``record["validated"] is True``).  A record
  without that flag is never promoted, whatever numbers it carries.
* ``retired`` when the CUSUM filter on the live shadow performance raised an alarm (``record["cusum_alarm"]``);
  once retired a strategy stays retired (``record["status"] == "retired"``) until someone resets the record.
* ``active`` when ALL of: DSR probability > 0.95, PBO < 0.5, out-of-sample Sharpe after doubled costs > 0, and
  at least 100 trades or at least 3 years of history.
* ``incubation`` otherwise (shadow account only, weight zero in the master).

Direction of the DSR test: ``dsr_prob`` is the Deflated Sharpe Ratio of Bailey & López de Prado (2014), i.e. the
probability that the true Sharpe is **above zero** after deflating for the number of trials.  "DSR > 0" in
docs/STRATEGIES.md is read as "DSR probability > 0.95": the deflated Sharpe is significantly positive, not merely
positive.  Thresholds live in :class:`Thresholds`.

Record fields: ``validated`` (bool), ``dsr_prob``, ``pbo``, ``oos_sharpe_double_cost``, ``n_trades``,
``years``, ``cusum_alarm`` (bool), ``status`` (current state, optional).  Missing numbers count as failed checks.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["STATES", "Lifecycle", "Thresholds", "checks", "decide_lifecycle", "explain"]

Lifecycle = Literal["research", "incubation", "active", "retired"]
STATES: tuple[Lifecycle, ...] = ("research", "incubation", "active", "retired")


@dataclass(frozen=True)
class Thresholds:
    dsr_min: float = 0.95  # DSR probability must exceed this
    pbo_max: float = 0.5  # PBO must stay below this
    oos_sharpe_min: float = 0.0  # OOS Sharpe after doubled costs must exceed this
    min_trades: int = 100
    min_years: float = 3.0


DEFAULT = Thresholds()


def _num(record: Mapping[str, Any], key: str) -> float | None:
    v = record.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def checks(record: Mapping[str, Any], thresholds: Thresholds = DEFAULT) -> dict[str, bool]:
    """Outcome of each promotion check (``True`` = passed).  Missing values fail."""
    dsr, pbo = _num(record, "dsr_prob"), _num(record, "pbo")
    oos = _num(record, "oos_sharpe_double_cost")
    n_trades, years = _num(record, "n_trades"), _num(record, "years")
    return {
        "dsr": dsr is not None and dsr > thresholds.dsr_min,
        "pbo": pbo is not None and pbo < thresholds.pbo_max,
        "oos_double_cost": oos is not None and oos > thresholds.oos_sharpe_min,
        "history": (n_trades is not None and n_trades >= thresholds.min_trades)
        or (years is not None and years >= thresholds.min_years),
    }


def decide_lifecycle(record: Mapping[str, Any], thresholds: Thresholds = DEFAULT) -> Lifecycle:
    """Pure decision; see the module docstring for the rules."""
    if record.get("status") == "retired" or bool(record.get("cusum_alarm", False)):
        return "retired"
    if record.get("validated") is not True:
        return "research"
    if all(checks(record, thresholds).values()):
        return "active"
    return "incubation"


def _it(x: float | None, nd: int = 2) -> str:
    """Italian number formatting (decimal comma); ``n/d`` when missing."""
    if x is None:
        return "n/d"
    return f"{x:.{nd}f}".replace(".", ",")


def _thr(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _gt(x: float, thr: float) -> str:
    return ">" if x > thr else "="


def _reasons(record: Mapping[str, Any], thresholds: Thresholds, passed: bool) -> list[str]:
    c = checks(record, thresholds)
    dsr, pbo = _num(record, "dsr_prob"), _num(record, "pbo")
    oos = _num(record, "oos_sharpe_double_cost")
    n_trades, years = _num(record, "n_trades"), _num(record, "years")
    out: list[str] = []
    if c["dsr"] == passed:
        if passed:
            out.append(f"DSR {_it(dsr)} > {_thr(thresholds.dsr_min)}")
        elif dsr is None:
            out.append("DSR n/d")
        else:
            out.append(f"DSR {_it(dsr)} {'<' if dsr < thresholds.dsr_min else '='} {_thr(thresholds.dsr_min)}")
    if c["pbo"] == passed:
        if passed:
            out.append(f"PBO {_it(pbo)} < {_thr(thresholds.pbo_max)}")
        elif pbo is None:
            out.append("PBO n/d")
        else:
            out.append(f"PBO {_it(pbo)} {_gt(pbo, thresholds.pbo_max)} {_thr(thresholds.pbo_max)}")
    if c["oos_double_cost"] == passed:
        if passed:
            out.append(f"Sharpe OOS a costi doppi {_it(oos)} > {_thr(thresholds.oos_sharpe_min)}")
        elif oos is None:
            out.append("Sharpe OOS a costi doppi n/d")
        else:
            rel = "<" if oos < thresholds.oos_sharpe_min else "="
            out.append(f"Sharpe OOS a costi doppi {_it(oos)} {rel} {_thr(thresholds.oos_sharpe_min)}")
    if c["history"] == passed:
        trades_s = "n/d" if n_trades is None else f"{int(n_trades)}"
        years_s = _it(years, 1)
        if passed:
            out.append(f"{trades_s} operazioni, {years_s} anni di storia")
        else:
            out.append(
                f"{trades_s} operazioni < {thresholds.min_trades} e {years_s} anni < {_thr(thresholds.min_years)}"
            )
    return out


def explain(record: Mapping[str, Any], thresholds: Thresholds = DEFAULT) -> str:
    """One Italian sentence for the dashboard, e.g. ``'Peso zero: PBO 0,62 > 0,5'``."""
    state = decide_lifecycle(record, thresholds)
    if state == "retired":
        if bool(record.get("cusum_alarm", False)):
            return "Ritirata: allarme CUSUM sulla performance live (decadimento statisticamente rilevato)"
        return "Ritirata: ritiro precedente confermato, serve una nuova validazione manuale"
    if state == "research":
        return "Ricerca: nessun report di validazione, nessuna promozione automatica"
    if state == "active":
        return "Attiva: " + "; ".join(_reasons(record, thresholds, passed=True))
    return "Peso zero: " + "; ".join(_reasons(record, thresholds, passed=False))
