"""Data-quality checks and the health rules derived from them.

Every function here is pure and works on a plain pandas object, so the checks can run in a backtest (on a
truncated, point-in-time view) exactly as they run in the live job. Nothing repairs data: a check only reports
:class:`QualityIssue` records, and the caller decides (``no NEW risk`` on red, size unchanged on yellow).

Health rules (:func:`health_from_issues`)
-----------------------------------------
======  =========================================================================================================
RED     the source is unavailable (``available=False``) **or** any issue has severity ``error``. An ``error``
        means the datum cannot be used for new risk: data stale beyond the tolerance, a non-positive price, a
        cross-source divergence above the absolute/relative limit, or duplicate observation timestamps.
YELLOW  no error, but a fallback adapter was used (``fallback_used``) or at least one ``warning`` exists
        (outliers, business-day gaps, mild staleness).
GREEN   no issue at all and the primary source answered.
======  =========================================================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal

import numpy as np
import pandas as pd

from engine.core.calendar import is_business_day
from engine.data.base import Health, SourceHealth

IssueKind = Literal["stale", "outlier", "gap", "divergence", "duplicate", "nonpositive"]
Severity = Literal["warning", "error"]

KINDS: tuple[str, ...] = ("stale", "outlier", "gap", "divergence", "duplicate", "nonpositive")
SEVERITIES: tuple[str, ...] = ("warning", "error")
MAD_TO_SIGMA = 1.4826  # scale factor making the MAD a consistent estimator of sigma for normal data


@dataclass
class QualityIssue:
    """One finding of one check. ``asof`` is the timestamp of the datum the issue is about (UTC when known)."""

    table: str
    column: str
    kind: IssueKind
    severity: Severity
    message: str
    asof: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown issue kind {self.kind!r} (expected one of {KINDS})")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r} (expected one of {SEVERITIES})")

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "column": self.column,
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "asof": None if self.asof is None else self.asof.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            **({"extra": self.extra} if self.extra else {}),
        }


# --------------------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------------------
def _utc(ts: Any) -> datetime | None:
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    if pd.isna(t):
        return None
    t = t.tz_localize(UTC) if t.tzinfo is None else t.tz_convert(UTC)
    return t.to_pydatetime()


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _label(series: pd.Series, column: str | None) -> str:
    if column:
        return column
    return str(series.name) if series.name is not None else "value"


# --------------------------------------------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------------------------------------------
def check_stale(
    series: pd.Series,
    now: datetime,
    max_age: timedelta,
    table: str = "",
    column: str | None = None,
    error_factor: float = 2.0,
) -> list[QualityIssue]:
    """Age of the newest non-null observation against ``max_age``.

    Warning beyond ``max_age``, error beyond ``error_factor * max_age`` (stale beyond tolerance: no new risk).
    An empty series is an error: there is nothing to trade on.
    """
    name = _label(series, column)
    clean = series.dropna()
    now_utc = _utc(now)
    assert now_utc is not None
    if clean.empty:
        return [QualityIssue(table, name, "stale", "error", "nessuna osservazione disponibile", None)]
    last = _utc(pd.DatetimeIndex(clean.index).max())
    if last is None:
        return [QualityIssue(table, name, "stale", "error", "indice temporale non interpretabile", None)]
    age = now_utc - last
    if age <= max_age:
        return []
    severity: Severity = "error" if age > error_factor * max_age else "warning"
    hours = age.total_seconds() / 3600.0
    return [
        QualityIssue(
            table,
            name,
            "stale",
            severity,
            f"ultimo dato {last.date().isoformat()} ({hours:.1f} h, limite {max_age.total_seconds() / 3600:.1f} h)",
            last,
            {"age_hours": round(hours, 2), "max_age_hours": max_age.total_seconds() / 3600.0},
        )
    ]


def check_outliers(
    returns: pd.Series,
    z: float = 6.0,
    window: int = 60,
    table: str = "",
    column: str | None = None,
) -> list[QualityIssue]:
    """Robust MAD z-score of ``returns`` on a trailing ``window`` (the point itself excluded).

    ``|value - median| / (1.4826 * MAD) > z`` is a warning: a real market can move 6 robust sigmas (2020-04-20),
    so this never blocks trading on its own; it flags the datum for review.
    """
    name = _label(returns, column)
    ser = _numeric(returns).dropna()
    if len(ser) < max(5, window // 4):
        return []
    prior = ser.shift(1)
    median = prior.rolling(window, min_periods=max(5, window // 4)).median()
    mad = (prior - median).abs().rolling(window, min_periods=max(5, window // 4)).median()
    scale = MAD_TO_SIGMA * mad
    score = (ser - median).abs() / scale.where(scale > 0)
    flagged = score[score > z].dropna()
    issues: list[QualityIssue] = []
    raw = ser.reindex(flagged.index).to_numpy(dtype=float)
    for ts, value, observed in zip(list(flagged.index), flagged.to_numpy(dtype=float), raw, strict=True):
        issues.append(
            QualityIssue(
                table,
                name,
                "outlier",
                "warning",
                f"variazione anomala: z robusto {float(value):.1f} (soglia {z:g})",
                _utc(ts),
                {"z": round(float(value), 2), "value": float(observed)},
            )
        )
    return issues


def check_gaps(
    daily_index: pd.Index,
    max_business_gap: int = 3,
    table: str = "",
    column: str = "index",
    market: str = "ICE",
) -> list[QualityIssue]:
    """Runs of missing business days (``engine.core.calendar``) longer than ``max_business_gap``.

    Warning: a long market holiday or an exchange outage is not a reason to halt, but it must be visible.
    """
    idx = pd.DatetimeIndex(daily_index).dropna().unique().sort_values()
    if len(idx) < 2:
        return []
    dates = [ts.date() for ts in idx]
    issues: list[QualityIssue] = []
    for prev, cur in pairwise(dates):
        missing = 0
        day = prev + timedelta(days=1)
        while day < cur:
            if is_business_day(day, market):
                missing += 1
            day += timedelta(days=1)
        if missing > max_business_gap:
            issues.append(
                QualityIssue(
                    table,
                    column,
                    "gap",
                    "warning",
                    f"{missing} giorni lavorativi mancanti tra {prev.isoformat()} e {cur.isoformat()}",
                    _utc(pd.Timestamp(cur)),
                    {"missing_business_days": missing, "from": prev.isoformat(), "to": cur.isoformat()},
                )
            )
    return issues


def check_divergence(
    a: pd.Series,
    b: pd.Series,
    max_abs: float,
    max_rel: float,
    table: str = "",
    column: str | None = None,
    names: tuple[str, str] = ("a", "b"),
) -> list[QualityIssue]:
    """Cross-source price check on the dates both series cover.

    A date is an error when ``|a - b| > max_abs`` **and** ``|a - b| / |a| > max_rel`` (both limits must be
    exceeded, so a 0.5 $ difference on a 110 $ barrel is fine and a 10 % difference on a 2 $ spread is not an
    automatic error). Only the most recent 20 offending dates are reported.
    """
    name = _label(a, column)
    sa, sb = _numeric(a).dropna(), _numeric(b).dropna()
    common = sa.index.intersection(sb.index)
    if len(common) == 0:
        return [
            QualityIssue(
                table,
                name,
                "divergence",
                "warning",
                f"nessuna data in comune tra {names[0]} e {names[1]}: confronto impossibile",
                None,
            )
        ]
    diff = (sa.loc[common] - sb.loc[common]).abs()
    base = sa.loc[common].abs().replace(0.0, np.nan)
    rel = diff / base
    bad = diff[(diff > max_abs) & (rel > max_rel)].dropna()
    issues: list[QualityIssue] = []
    for ts in list(bad.index)[-20:]:
        issues.append(
            QualityIssue(
                table,
                name,
                "divergence",
                "error",
                f"{names[0]} {sa.loc[ts]:.4f} vs {names[1]} {sb.loc[ts]:.4f} "
                f"(diff {float(bad.loc[ts]):.4f} > {max_abs:g} e {float(rel.loc[ts]) * 100:.2f} % > "
                f"{max_rel * 100:g} %)",
                _utc(ts),
                {"a": float(sa.loc[ts]), "b": float(sb.loc[ts]), "abs": float(bad.loc[ts])},
            )
        )
    return issues


def check_nonpositive(series: pd.Series, table: str = "", column: str | None = None) -> list[QualityIssue]:
    """Non-positive prices: an error.

    Scope matters. WTI really settled at **-37.63 $ on 2020-04-20** (and the EIA spot at -36.98 $), so running
    this over a full history flags a genuine print; callers pass the recent tail (see
    :data:`engine.data.fetch.NONPOSITIVE_TAIL_ROWS`), where a zero or a negative number is a parsing accident.
    """
    name = _label(series, column)
    ser = _numeric(series).dropna()
    bad = ser[ser <= 0]
    issues: list[QualityIssue] = []
    for ts in list(bad.index)[-20:]:
        issues.append(
            QualityIssue(
                table,
                name,
                "nonpositive",
                "error",
                f"valore non positivo {float(bad.loc[ts]):.4f}",
                _utc(ts),
                {"value": float(bad.loc[ts])},
            )
        )
    return issues


def check_duplicates(index: pd.Index, table: str = "", column: str = "index") -> list[QualityIssue]:
    """Duplicate observation timestamps: an error, because a duplicate silently doubles a weight or a position."""
    idx = pd.Index(index)
    dup_mask = idx.duplicated(keep="first")
    if not bool(dup_mask.any()):
        return []
    values = list(pd.Index(idx[dup_mask]).unique())
    shown = ", ".join(str(v) for v in values[:5])
    return [
        QualityIssue(
            table,
            column,
            "duplicate",
            "error",
            f"{int(dup_mask.sum())} righe duplicate ({len(values)} indici: {shown})",
            _utc(values[-1]) if values else None,
            {"n_duplicates": int(dup_mask.sum())},
        )
    ]


# --------------------------------------------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------------------------------------------
def health_from_issues(
    source: str,
    issues: list[QualityIssue],
    checked_at: datetime,
    data_asof: datetime | None = None,
    fallback_used: str | None = None,
    available: bool = True,
    rows: int = 0,
    latency_ms: int | None = None,
    message: str = "",
    last_success_at: datetime | None = None,
) -> SourceHealth:
    """Aggregate the issues of one source into a :class:`SourceHealth` (rules in the module docstring)."""
    errors = [i for i in issues if i.is_error]
    warnings = [i for i in issues if not i.is_error]
    if not available or errors:
        status = Health.RED
    elif fallback_used or warnings:
        status = Health.YELLOW
    else:
        status = Health.GREEN
    parts: list[str] = []
    if message:
        parts.append(message)
    if fallback_used:
        parts.append(f"fallback: {fallback_used}")
    for issue in (*errors[:3], *warnings[:3]):
        parts.append(f"[{issue.severity}] {issue.column}: {issue.message}")
    return SourceHealth(
        source=source,
        status=status,
        checked_at=_utc(checked_at) or datetime.now(tz=UTC),
        last_success_at=_utc(last_success_at),
        data_asof=_utc(data_asof),
        latency_ms=latency_ms,
        message=" | ".join(parts)[:1000],
        fallback_used=fallback_used,
        rows=rows,
    )


def overall_health(healths: list[SourceHealth]) -> Health:
    """Worst status across sources (empty -> RED: we know nothing)."""
    if not healths:
        return Health.RED
    statuses = {h.status for h in healths}
    if Health.RED in statuses:
        return Health.RED
    if Health.YELLOW in statuses:
        return Health.YELLOW
    return Health.GREEN
