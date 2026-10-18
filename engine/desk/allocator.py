"""The allocator (phase 13): how the desk recalibrates itself "depending on how it goes".

Every rule here is a SCALE between 0 and 1 that multiplies a book's volatility target (``risk``) or its
ceiling before a market closure (``weekend``). Nothing here raises a position above what the book's own rules
allow, and nothing here touches the forecast: the allocator only decides how much of it a book buys today.
The same functions run in the daily replay and on the live path, so the backtest shows what the rules did.

The rules, from ``config/allocator.yaml`` (measured in ``docs/RESEARCH.md``, chapter 9, before they were on):

* **The bubble switch** (Greenwood, Shleifer & You 2019): after the vehicle's investable price has doubled over
  two years, half the volatility target for a year. On by default: it raised the Sharpe ratio and cut the
  drawdown of every portfolio in the replay, in both halves of the sample.
* **Drawdown control** (Grossman & Zhou 1993), per book: ``m = max(floor, 1 - drawdown / max_drawdown)``.
  Off by default: it cuts the maximum loss by a third at the price of 0.05-0.14 of Sharpe, which a lower risk
  target achieves for free. The owner's lever.
* **A closed Strait of Hormuz**: tanker transits below a tenth of their normal halve the weekend ceilings. A
  policy, not a measurement. The count is IMF PortWatch's, from AIS, so it sees only the ships that cross with
  their transponder on: since March 2026 most of them do not (Kpler, Drewry), and 1% of normal there is a lower
  bound of the traffic, not the traffic (docs/RESEARCH.md 13 E).
* **Promotion and retirement**, on a book's own live record: a candidate with twelve months of fills, a
  positive realised Sharpe and a drawdown within its backtest's becomes core; any book with twenty-four months
  and a realised Sharpe below -0.5 is retired (scale 0: flat, no new risk) until the owner reopens it.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.config import load_config

log = logging.getLogger(__name__)

ALLOCATOR_CONFIG = "allocator"
TRADING_DAYS = 252
MONTH_DAYS = 21  # trading days in a month, for the lifecycle's "months live"
ROLE_CORE = "core"
ROLE_CANDIDATE = "candidate"
ROLE_RETIRED = "retired"


@dataclass(frozen=True)
class AllocatorConfig:
    runup_enabled: bool = True
    runup_lookback_days: int = 504
    runup_threshold: float = 1.0
    runup_horizon_days: int = 252
    runup_scale: float = 0.5
    drawdown_enabled: bool = False
    drawdown_max: float = 0.20
    drawdown_floor: float = 0.25
    hormuz_enabled: bool = True
    hormuz_closed_share: float = 0.10
    hormuz_weekend_scale: float = 0.5
    hormuz_lag_days: int = 5
    lifecycle_enabled: bool = True
    promote_after_months: int = 12
    promote_min_sharpe: float = 0.0
    retire_after_months: int = 24
    retire_sharpe: float = -0.5

    def __post_init__(self) -> None:
        for name in ("runup_scale", "drawdown_floor", "hormuz_weekend_scale"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"allocator: {name} must be within [0, 1], got {value}")
        if self.runup_lookback_days <= 0 or self.runup_horizon_days <= 0 or self.runup_threshold <= 0:
            raise ValueError("allocator: the run-up rule needs positive lookback, horizon and threshold")
        if not 0.0 < self.drawdown_max <= 1.0:
            raise ValueError("allocator: max_drawdown must be within (0, 1]")
        if not 0.0 < self.hormuz_closed_share < 1.0:
            raise ValueError("allocator: hormuz closed_share must be within (0, 1)")

    @classmethod
    def load(cls, config_dir: Path | None = None) -> AllocatorConfig:
        raw = load_config(ALLOCATOR_CONFIG, config_dir)
        r, d, h, lc = (raw.get(k) or {} for k in ("runup", "drawdown_control", "hormuz", "lifecycle"))
        return cls(
            runup_enabled=bool(r.get("enabled", True)),
            runup_lookback_days=int(r.get("lookback_days", 504)),
            runup_threshold=float(r.get("threshold", 1.0)),
            runup_horizon_days=int(r.get("horizon_days", 252)),
            runup_scale=float(r.get("scale", 0.5)),
            drawdown_enabled=bool(d.get("enabled", False)),
            drawdown_max=float(d.get("max_drawdown", 0.20)),
            drawdown_floor=float(d.get("floor", 0.25)),
            hormuz_enabled=bool(h.get("enabled", True)),
            hormuz_closed_share=float(h.get("closed_share", 0.10)),
            hormuz_weekend_scale=float(h.get("weekend_scale", 0.5)),
            hormuz_lag_days=int(h.get("publication_lag_days", 5)),
            lifecycle_enabled=bool(lc.get("enabled", True)),
            promote_after_months=int(lc.get("promote_after_months", 12)),
            promote_min_sharpe=float(lc.get("promote_min_sharpe", 0.0)),
            retire_after_months=int(lc.get("retire_after_months", 24)),
            retire_sharpe=float(lc.get("retire_sharpe", -0.5)),
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Scale:
    """What the allocator hands a book for one decision day."""

    risk: float = 1.0  # multiplies the volatility target
    weekend: float = 1.0  # multiplies the ceiling before a closure
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"risk": round(self.risk, 4), "weekend": round(self.weekend, 4), "reasons": list(self.reasons)}


# ------------------------------------------------------------------------------------------ the bubble switch
def runup_scales(index: pd.Series, cfg: AllocatorConfig) -> pd.Series:
    """One value per day of ``index`` (an investable total-return index): ``cfg.runup_scale`` on the days within
    ``horizon`` trading days AFTER a day on which the index stood at least ``threshold`` above its level
    ``lookback`` days before, else 1. The day the run-up is measured on counts (its close is known at the
    decision), so a decision taken on it already runs at the reduced scale."""
    if not cfg.runup_enabled or index.empty:
        return pd.Series(1.0, index=index.index)
    level = index.astype("float64")
    runup = level / level.shift(cfg.runup_lookback_days) - 1.0
    flag = (runup >= cfg.runup_threshold).astype("float64")
    recent = flag.rolling(cfg.runup_horizon_days, min_periods=1).max().fillna(0.0)
    return pd.Series(1.0 - (1.0 - cfg.runup_scale) * recent, index=index.index)


def runup_table(index: pd.Series, cfg: AllocatorConfig) -> pd.DataFrame:
    """Per day of ``index``: ``active`` (the switch is on for a decision taken that day) and ``since`` (the last
    day the run-up held, NaT before any). The replay reads this once per vehicle instead of calling
    ``runup_state`` day by day."""
    scales = runup_scales(index, cfg)
    level = index.astype("float64")
    runup = level / level.shift(cfg.runup_lookback_days) - 1.0
    held = runup >= cfg.runup_threshold
    days = pd.Series(pd.DatetimeIndex(level.index), index=level.index)
    since = days.where(held.to_numpy()).ffill()
    return pd.DataFrame({"active": scales < 1.0, "since": since}, index=level.index)


def runup_state(index: pd.Series, cfg: AllocatorConfig, day: date) -> tuple[bool, date | None]:
    """Whether the switch is on for a decision taken on ``day`` (index rows after ``day`` ignored), and the
    last day the run-up itself held."""
    if not cfg.runup_enabled or index.empty:
        return False, None
    level = index.astype("float64").loc[: pd.Timestamp(day)]
    if len(level) <= cfg.runup_lookback_days:
        return False, None
    runup = level / level.shift(cfg.runup_lookback_days) - 1.0
    held = runup[runup >= cfg.runup_threshold]
    if held.empty:
        return False, None
    last = pd.Timestamp(held.index[-1])
    since = len(level.loc[last:]) - 1  # trading days since the last day the run-up held
    return since < cfg.runup_horizon_days, last.date()


# ------------------------------------------------------------------------------------------ drawdown control
def drawdown_scale(equity: float, peak: float, cfg: AllocatorConfig) -> float:
    """Grossman-Zhou: the scale falls linearly with the drawdown and never below the floor."""
    if not cfg.drawdown_enabled or peak <= 0 or equity <= 0:
        return 1.0
    dd = max(0.0, 1.0 - equity / peak)
    return max(cfg.drawdown_floor, 1.0 - dd / cfg.drawdown_max)


# ------------------------------------------------------------------------------------------ the strait
def transit_ratios(hormuz: pd.DataFrame, cfg: AllocatorConfig) -> pd.Series:
    """The 7-day mean of tanker transits over its long-run median, indexed by the day the reading can be ACTED
    on (its date plus the publication lag). The median is taken over everything up to 60 days before each
    reading, as ``data.hormuz_summary`` does for the latest one, so a closure never becomes its own baseline."""
    if hormuz is None or hormuz.empty or "n_tanker" not in hormuz.columns:
        return pd.Series(dtype="float64")
    rows = hormuz
    if "portname" in rows.columns:
        rows = rows[rows["portname"] == "Strait of Hormuz"]
    series = rows["n_tanker"].astype("float64").sort_index()
    series = series[~series.index.duplicated(keep="last")]
    if len(series) < 260:
        return pd.Series(dtype="float64")
    weekly = series.rolling(7, min_periods=5).mean()
    # the baseline of day t: the median of the weekly mean up to t - 60 days, needing 200 readings
    lagged = weekly.copy()
    lagged.index = pd.DatetimeIndex(lagged.index) + pd.Timedelta(days=60)
    baseline = lagged.expanding(min_periods=200).median().reindex(weekly.index, method="ffill")
    ratio = (weekly / baseline).where(baseline > 0)
    ratio.index = pd.DatetimeIndex(ratio.index) + pd.Timedelta(days=cfg.hormuz_lag_days)
    return ratio.dropna()


def hormuz_weekend_scale(ratio: float | None, cfg: AllocatorConfig) -> float:
    if not cfg.hormuz_enabled or ratio is None or not math.isfinite(ratio):
        return 1.0
    return cfg.hormuz_weekend_scale if ratio < cfg.hormuz_closed_share else 1.0


def ratio_known_on(ratios: pd.Series, day: date) -> float | None:
    """The latest transit ratio that can be acted on by ``day`` (none older than 45 days: a stale reading is no
    reading)."""
    if ratios.empty:
        return None
    known = ratios.loc[: pd.Timestamp(day)]
    if known.empty:
        return None
    if (pd.Timestamp(day) - pd.Timestamp(known.index[-1])).days > 45:
        return None
    return float(known.iloc[-1])


# ------------------------------------------------------------------------------------------ the lifecycle
@dataclass(frozen=True)
class LiveRecord:
    """A book's own live record: daily closes of its equity since its epoch began."""

    days: int
    sharpe: float | None
    max_drawdown: float | None
    start: date | None
    end: date | None

    @property
    def months(self) -> float:
        return self.days / MONTH_DAYS


def live_record(daily_equity: pd.Series) -> LiveRecord:
    """Realised Sharpe and drawdown of one equity curve (one close per day), ``sharpe`` None under 60 days."""
    eq = daily_equity.dropna()
    eq = eq[eq > 0]
    if len(eq) < 2:
        return LiveRecord(
            len(eq), None, None, None if eq.empty else eq.index[0].date(), None if eq.empty else eq.index[-1].date()
        )
    ret = eq.pct_change().dropna()
    dd = float((eq / eq.cummax() - 1.0).min())
    if len(ret) < 60 or float(ret.std()) == 0.0:
        sharpe = None
    else:
        sharpe = float(ret.mean() / ret.std() * math.sqrt(TRADING_DAYS))
    return LiveRecord(len(eq), sharpe, dd, eq.index[0].date(), eq.index[-1].date())


def lifecycle_verdict(role: str, record: LiveRecord, backtest_max_drawdown: float | None, cfg: AllocatorConfig) -> str:
    """``role`` is the book's current role; returns the role it should have given its live record.

    A promotion compares the live drawdown with the backtest's, so it needs one: a book without a backtest (the
    speculative Kalshi book of phase 14, or any book while the replay is missing) is never promoted, whatever
    its live Sharpe. Retirement reads the live record alone.
    """
    if not cfg.lifecycle_enabled or role == ROLE_RETIRED:
        return role
    months = record.months
    if months >= cfg.retire_after_months and record.sharpe is not None and record.sharpe < cfg.retire_sharpe:
        return ROLE_RETIRED
    if role == ROLE_CANDIDATE and months >= cfg.promote_after_months and record.sharpe is not None:
        within = backtest_max_drawdown is not None and (record.max_drawdown or 0.0) >= backtest_max_drawdown - 1e-9
        if record.sharpe > cfg.promote_min_sharpe and within:
            return ROLE_CORE
    return role


def daily_closes(rows: list[dict[str, Any]], epoch: int | None = None) -> pd.Series:
    """The last equity snapshot of each day from a broker's equity log (``equity-*.jsonl`` records)."""
    if not rows:
        return pd.Series(dtype="float64")
    frame = pd.DataFrame(rows)
    if "ts" not in frame.columns or "equity" not in frame.columns:
        return pd.Series(dtype="float64")
    if epoch is not None and "epoch" in frame.columns:
        frame = frame[frame["epoch"] == epoch]
    ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    frame = frame.assign(day=ts.dt.normalize().dt.tz_localize(None)).dropna(subset=["day"])
    frame = frame.sort_values("day")
    out = frame.groupby("day")["equity"].last().astype("float64")
    out.index = pd.DatetimeIndex(out.index)
    return out


# ------------------------------------------------------------------------------------------ the summary
def describe(cfg: AllocatorConfig) -> dict[str, Any]:
    """The rules in force, for the page and the state."""
    runup = (
        f"+{cfg.runup_threshold:.0%} over {cfg.runup_lookback_days} trading days: "
        f"risk x {cfg.runup_scale:g} for {cfg.runup_horizon_days} trading days"
    )
    drawdown = f"risk x max({cfg.drawdown_floor:g}, 1 - drawdown / {cfg.drawdown_max:.0%}), per book"
    hormuz = (
        f"AIS-visible tanker transits below {cfg.hormuz_closed_share:.0%} of normal: "
        f"weekend ceilings x {cfg.hormuz_weekend_scale:g}"
    )
    lifecycle = (
        f"core after {cfg.promote_after_months} months live with Sharpe > {cfg.promote_min_sharpe:g} and the "
        f"drawdown within the backtest's (a book without a backtest is never promoted); retired after "
        f"{cfg.retire_after_months} months with Sharpe < {cfg.retire_sharpe:g}"
    )
    return {
        "runup": {"enabled": cfg.runup_enabled, "rule": runup},
        "drawdown_control": {"enabled": cfg.drawdown_enabled, "rule": drawdown},
        "hormuz": {"enabled": cfg.hormuz_enabled, "rule": hormuz},
        "lifecycle": {"enabled": cfg.lifecycle_enabled, "rule": lifecycle},
    }
