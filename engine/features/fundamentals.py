"""Fundamental (EIA WPSR, Baker Hughes) features, point-in-time by publication.

Inputs
------
``md.wpsr``  index = release timestamp (UTC), columns ``period`` (week ending Friday) plus the WPSR series in
kbbl / kbbl/d. ``md.rigs`` Series indexed by release date (US oil rigs, Baker Hughes, published Friday 13:00 ET).

Point-in-time rules
-------------------
* Every per-release statistic is computed on the release table (one row per ``period``, first-published vintage,
  see :func:`engine.features.events.release_table`) using only rows of **earlier periods**; the table is then
  joined to trading dates with ``merge_asof`` on ``published_at <= settlement_ts(date)``. A release never shows
  up before its publication, so a WPSR published Wednesday 10:30 ET is first visible on Wednesday's settlement.
* Seasonal references use the **same ISO week** of the previous five years (week 53 is folded into week 52).
  At least ``MIN_SEASONAL_YEARS`` of them must exist, otherwise the feature is NaN. Those weeks were published
  one to five years earlier, hence always known.
* Scaling windows (``Z_WINDOW_RELEASES`` prior releases) exclude the current release: a release never enters its
  own normalisation.
* The rig count is dated by its release Friday and published at 13:00 New York, before the ICE settlement, so it
  is visible on the same trading date; ``md.published_at["rigs"]`` overrides this when the adapter provides it.

Columns
-------
``CRUDE_STOCKS``, ``CUSHING_STOCKS``        latest known level (kbbl).
``CRUDE_STOCKS_VS_5Y``, ``CUSHING_VS_5Y``   (level - 5y same-week mean) / 5y same-week mean, fraction.
``CRUDE_STOCKS_5Y_RANGE_POS``                (level - 5y min) / (5y max - 5y min), clipped to [0, 1].
``STOCK_SURPRISE``                            actual weekly change minus the seasonal expectation (kbbl), where
    expected change = mean change of the same ISO week over the previous 5 years
                      + ``SURPRISE_PERSISTENCE`` (0.3) * last week's change.
    Nothing is fitted: the coefficients are fixed a priori, so there is no in-sample leakage.
``STOCK_SURPRISE_Z``, ``CUSHING_SURPRISE_Z``  surprise standardised by the mean/std of the surprises of the previous
    ``Z_WINDOW_RELEASES`` releases (crude and Cushing stocks respectively).
``IMPLIED_DEMAND_Z``                          (gasoline_supplied + distillate_supplied) minus its 5y same-week mean,
    scaled by the trailing std of that deviation (previous releases only).
``REFINERY_INPUTS_Z``                         same construction on refinery crude inputs.
``DAYS_SINCE_WPSR``                           calendar days from the publication date of the latest known WPSR.
``RIGS``, ``RIGS_CHG_13W``                    latest known rig count and its change over 13 releases.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import numpy as np
import pandas as pd

from engine.core.timeutil import NEW_YORK
from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.events import align_released, release_table, settlement_index, to_utc_ns, trading_dates

SEASONAL_YEARS = 5
MIN_SEASONAL_YEARS = 3
SURPRISE_PERSISTENCE = 0.3
Z_WINDOW_RELEASES = 104
Z_MIN_RELEASES = 26
RIGS_CHG_RELEASES = 13
RIGS_RELEASE_NY = time(13, 0)

WPSR_VALUE_COLS: list[str] = [
    "crude_stocks",
    "cushing_stocks",
    "gasoline_supplied",
    "distillate_supplied",
    "refinery_inputs",
]

COLUMNS: list[str] = [
    cat.CRUDE_STOCKS,
    cat.CRUDE_STOCKS_VS_5Y,
    cat.CRUDE_STOCKS_5Y_RANGE_POS,
    cat.CUSHING_STOCKS,
    cat.CUSHING_VS_5Y,
    cat.STOCK_SURPRISE,
    cat.STOCK_SURPRISE_Z,
    cat.CUSHING_SURPRISE_Z,
    cat.IMPLIED_DEMAND_Z,
    cat.REFINERY_INPUTS_Z,
    cat.DAYS_SINCE_WPSR,
    cat.RIGS,
    cat.RIGS_CHG_13W,
]


# ----------------------------------------------------------------------------------------------------------------
# seasonal helpers (operate on the per-release table, ordered by period)
# ----------------------------------------------------------------------------------------------------------------
def iso_keys(period: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """(ISO year, ISO week) per period; week 53 is folded into week 52 so every year has a counterpart."""
    iso = pd.DatetimeIndex(period).isocalendar()
    year = iso["year"].to_numpy(dtype=np.int64)
    week = iso["week"].to_numpy(dtype=np.int64)
    return year, np.where(week == 53, 52, week)


def seasonal_stack(values: pd.Series, year: np.ndarray, week: np.ndarray) -> pd.DataFrame:
    """Column k (1..SEASONAL_YEARS) holds the value of the same ISO week k years earlier (NaN when absent).

    Only earlier years are looked up, so each row sees nothing published after its own release.
    """
    keyed = pd.Series(values.to_numpy(dtype=float), index=pd.MultiIndex.from_arrays([year, week]))
    keyed = keyed.groupby(level=[0, 1]).mean()  # folds week 53 into 52
    cols = {
        k: keyed.reindex(pd.MultiIndex.from_arrays([year - k, week])).to_numpy() for k in range(1, SEASONAL_YEARS + 1)
    }
    return pd.DataFrame(cols, index=values.index)


def _enough(stack: pd.DataFrame) -> pd.Series:
    return stack.notna().sum(axis=1) >= MIN_SEASONAL_YEARS


def seasonal_mean(stack: pd.DataFrame) -> pd.Series:
    return stack.mean(axis=1).where(_enough(stack))


def weekly_change(values: pd.Series, period: pd.Series) -> pd.Series:
    """Change versus the previous release; NaN unless the previous period is exactly one week earlier."""
    gap = pd.Series(pd.DatetimeIndex(period), index=period.index).diff().dt.days
    return values.diff().where(gap == 7)


def _trailing_prior(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    prior = series.shift(1)
    mu = prior.rolling(Z_WINDOW_RELEASES, min_periods=Z_MIN_RELEASES).mean()
    sd = prior.rolling(Z_WINDOW_RELEASES, min_periods=Z_MIN_RELEASES).std()
    return mu, sd.where(sd > 0)


def surprise(values: pd.Series, period: pd.Series, year: np.ndarray, week: np.ndarray) -> tuple[pd.Series, pd.Series]:
    """(surprise, surprise_z): actual weekly change minus the fixed-coefficient seasonal expectation."""
    chg = weekly_change(values, period)
    expected = seasonal_mean(seasonal_stack(chg, year, week)) + SURPRISE_PERSISTENCE * chg.shift(1)
    s = chg - expected
    mu, sd = _trailing_prior(s)
    return s, (s - mu) / sd


def seasonal_z(values: pd.Series, year: np.ndarray, week: np.ndarray) -> pd.Series:
    """Deviation from the 5y same-week mean, scaled by the trailing std of that deviation (prior releases)."""
    dev = values - seasonal_mean(seasonal_stack(values, year, week))
    _, sd = _trailing_prior(dev)
    return dev / sd


# ----------------------------------------------------------------------------------------------------------------
def _wpsr_release_features(wpsr: pd.DataFrame) -> pd.DataFrame:
    rel = release_table(wpsr, WPSR_VALUE_COLS)
    feat = pd.DataFrame({"published_at": rel["published_at"]})
    if rel.empty:
        for c in COLUMNS:
            feat[c] = pd.Series(dtype=float)
        return feat
    year, week = iso_keys(rel["period"])
    crude = rel["crude_stocks"]
    cushing = rel["cushing_stocks"]

    crude_stack = seasonal_stack(crude, year, week)
    crude_mean = seasonal_mean(crude_stack)
    lo = crude_stack.min(axis=1).where(_enough(crude_stack))
    hi = crude_stack.max(axis=1).where(_enough(crude_stack))
    span = (hi - lo).where(hi > lo)
    cushing_mean = seasonal_mean(seasonal_stack(cushing, year, week))

    feat[cat.CRUDE_STOCKS] = crude
    feat[cat.CRUDE_STOCKS_VS_5Y] = (crude - crude_mean) / crude_mean.where(crude_mean != 0)
    feat[cat.CRUDE_STOCKS_5Y_RANGE_POS] = ((crude - lo) / span).clip(0.0, 1.0)
    feat[cat.CUSHING_STOCKS] = cushing
    feat[cat.CUSHING_VS_5Y] = (cushing - cushing_mean) / cushing_mean.where(cushing_mean != 0)
    feat[cat.STOCK_SURPRISE], feat[cat.STOCK_SURPRISE_Z] = surprise(crude, rel["period"], year, week)
    _, feat[cat.CUSHING_SURPRISE_Z] = surprise(cushing, rel["period"], year, week)
    feat[cat.IMPLIED_DEMAND_Z] = seasonal_z(rel["gasoline_supplied"] + rel["distillate_supplied"], year, week)
    feat[cat.REFINERY_INPUTS_Z] = seasonal_z(rel["refinery_inputs"], year, week)
    return feat


def _rigs_release_features(md: MarketData) -> pd.DataFrame:
    rigs = pd.to_numeric(md.rigs, errors="coerce").dropna() if len(md.rigs) else pd.Series(dtype=float)
    if rigs.empty:
        return pd.DataFrame(
            {"published_at": pd.Series(dtype="datetime64[ns, UTC]"), cat.RIGS: [], cat.RIGS_CHG_13W: []}
        )
    idx = pd.DatetimeIndex(rigs.index)
    override = md.published_at.get("rigs")
    if override is not None and len(override):
        pub = to_utc_ns(pd.Series(override).reindex(rigs.index))
    else:
        local_days = (idx.tz_convert(NEW_YORK).tz_localize(None) if idx.tz is not None else idx).normalize()
        pub = (
            (local_days + pd.Timedelta(hours=RIGS_RELEASE_NY.hour, minutes=RIGS_RELEASE_NY.minute))
            .tz_localize(NEW_YORK)
            .tz_convert(UTC)
            .as_unit("ns")
        )
    rel = pd.DataFrame({"release": trading_dates(idx), "published_at": pub, cat.RIGS: rigs.to_numpy(dtype=float)})
    rel = rel.dropna(subset=["published_at"]).sort_values(["release", "published_at"], kind="stable")
    rel = rel.drop_duplicates("release", keep="first").reset_index(drop=True)
    rel["published_at"] = rel["published_at"].cummax()
    rel[cat.RIGS_CHG_13W] = rel[cat.RIGS] - rel[cat.RIGS].shift(RIGS_CHG_RELEASES)
    return rel[["published_at", cat.RIGS, cat.RIGS_CHG_13W]]


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    """Fundamental features for every trading date of ``base`` (same index, catalog column names only)."""
    out = pd.DataFrame(index=base.index)
    for c in COLUMNS:
        out[c] = np.nan
    if len(base.index) == 0:
        return out
    cutoffs = settlement_index(base.index)
    days = pd.Series(trading_dates(base.index), index=base.index)

    wpsr_feat = _wpsr_release_features(md.wpsr)
    wpsr_cols = [c for c in COLUMNS if c in wpsr_feat.columns]
    daily = align_released(wpsr_feat[wpsr_cols], wpsr_feat["published_at"], cutoffs, base.index)
    for c in wpsr_cols:
        out[c] = daily[c].astype(float)
    pub_day = daily["published_at"].dt.tz_convert(UTC).dt.normalize().dt.tz_localize(None)
    out[cat.DAYS_SINCE_WPSR] = (days - pub_day).dt.days.astype(float)

    rigs_feat = _rigs_release_features(md)
    rig_cols = [cat.RIGS, cat.RIGS_CHG_13W]
    daily_rigs = align_released(rigs_feat[rig_cols], rigs_feat["published_at"], cutoffs, base.index)
    for c in rig_cols:
        out[c] = daily_rigs[c].astype(float)
    return out
