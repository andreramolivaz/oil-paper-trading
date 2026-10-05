"""Calendar and scheduled-event features, plus the point-in-time alignment helpers shared by the released-table
feature modules (fundamentals, positioning, news).

Columns produced by :func:`compute` (names from :mod:`engine.features.catalog`):

* ``HOURS_TO_EVENT``   hours from the ICE settlement of the trading date to the next *binary* scheduled event
  (EIA WPSR, OPEC+, FOMC ... from ``config/events.yaml``). NaN when no binary event lies within
  ``EVENT_HORIZON_DAYS``.
* ``NEXT_EVENT_ID``    id of that event (object dtype, ``None`` when unknown).
* ``DAYS_TO_EXPIRY``   calendar days from the trading date to the last trading day of the front ICE Brent contract
  (``engine.core.calendar.front_month`` with no roll buffer: on its last trading day the expiring contract is still
  the front and the value is 0).
* ``IS_PRE_WEEKEND``   1.0 on Fridays, else 0.0.
* ``HURRICANE_SEASON`` 1.0 inside the Atlantic hurricane season configured in ``events.yaml`` (1 Jun - 30 Nov).

Everything here is deterministic given the calendar, so the features are point-in-time by construction.

Alignment helpers
-----------------
``settlement_index`` turns trading dates into their ICE settlement timestamps (UTC) and ``align_released`` picks,
for each trading date, the last row of a released table whose ``published_at`` is at or before that settlement
(``pandas.merge_asof`` backward). Weekly tables (WPSR, COT, rigs) are therefore joined on *publication* time,
never on the observation ``period``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from engine.core.calendar import front_month, ice_brent_expiry
from engine.core.eventcal import EventCalendar, ScheduledEvent
from engine.core.timeutil import ICE_SETTLEMENT_LONDON, LONDON
from engine.data.market_data import MarketData
from engine.features import catalog as cat

EVENT_HORIZON_DAYS = 31

COLUMNS: list[str] = [
    cat.HOURS_TO_EVENT,
    cat.NEXT_EVENT_ID,
    cat.DAYS_TO_EXPIRY,
    cat.IS_PRE_WEEKEND,
    cat.HURRICANE_SEASON,
]

_NS_PER_HOUR = 3_600_000_000_000


# ----------------------------------------------------------------------------------------------------------------
# shared point-in-time helpers
# ----------------------------------------------------------------------------------------------------------------
def trading_dates(index: pd.Index) -> pd.DatetimeIndex:
    """Normalise a feature-frame index to tz-naive London trading dates (midnight)."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert(LONDON).tz_localize(None)
    return idx.normalize()


def to_utc_ns(values: pd.Index | pd.Series | np.ndarray | list[datetime]) -> pd.DatetimeIndex:
    """UTC-aware ``datetime64[ns, UTC]`` index; tz-naive input is assumed to be UTC (engine convention)."""
    idx = pd.DatetimeIndex(values)
    idx = idx.tz_localize(UTC) if idx.tz is None else idx.tz_convert(UTC)
    return idx.as_unit("ns")


def settlement_index(index: pd.Index) -> pd.DatetimeIndex:
    """Vectorised :func:`engine.core.timeutil.settlement_ts` for every trading date of ``index`` (UTC, ns)."""
    days = trading_dates(index)
    local = days + pd.Timedelta(hours=ICE_SETTLEMENT_LONDON.hour, minutes=ICE_SETTLEMENT_LONDON.minute)
    # 19:30 never falls inside a DST transition (01:00/02:00 London), so no ambiguity handling is needed.
    return local.tz_localize(LONDON).tz_convert(UTC).as_unit("ns")


def align_released(
    table: pd.DataFrame,
    published_at: pd.Index | pd.Series | np.ndarray,
    cutoffs: pd.DatetimeIndex,
    index: pd.Index,
) -> pd.DataFrame:
    """Point-in-time join of a released table onto trading dates.

    For each ``cutoffs[i]`` (UTC settlement of ``index[i]``) the row of ``table`` with the largest
    ``published_at <= cutoffs[i]`` is selected. The result is indexed by ``index`` and carries the columns of
    ``table`` plus ``published_at`` (NaN/NaT where nothing was known yet). Rows with a missing publication time
    are dropped: a datum without a known publication time is never used.
    """
    cols = [*[c for c in table.columns if c != "published_at"], "published_at"]
    if table.empty or len(index) == 0:
        empty = pd.DataFrame(index=index, columns=cols, dtype=float)
        empty["published_at"] = pd.Series(pd.NaT, index=index, dtype="datetime64[ns, UTC]")
        return empty
    right = table.drop(columns=[c for c in table.columns if c == "published_at"]).reset_index(drop=True)
    right["published_at"] = to_utc_ns(published_at)
    right = right.dropna(subset=["published_at"]).sort_values("published_at", kind="stable")
    left = pd.DataFrame({"_cutoff": to_utc_ns(cutoffs)})
    merged = pd.merge_asof(left, right, left_on="_cutoff", right_on="published_at", direction="backward")
    merged.index = index
    return merged.drop(columns="_cutoff")[cols]


def release_table(df: pd.DataFrame, value_cols: Sequence[str]) -> pd.DataFrame:
    """Normalise a released table (index = ``published_at`` UTC, column ``period``) to one row per period.

    * Only the **first-published vintage** of each period is kept (``drop_duplicates(period, keep="first")`` after
      sorting by publication time): it is the figure the market traded on and it is known at every later
      instant, so no later revision can leak backwards. Revisions are tracked by the raw store, not here.
    * Rows are ordered by ``period`` and ``published_at`` is replaced by its running maximum: a release that
      arrives out of period order only becomes visible once every earlier period is also known (conservative).
    * Missing value columns are added as NaN; values are coerced to float.
    """
    cols = ["period", "published_at", *value_cols]
    if df.empty or "period" not in df.columns:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols})
    period = pd.DatetimeIndex(pd.to_datetime(df["period"], errors="coerce"))
    if period.tz is not None:
        period = period.tz_convert(UTC).tz_localize(None)
    rel = pd.DataFrame({"period": period.normalize(), "published_at": to_utc_ns(df.index)})
    for c in value_cols:
        rel[c] = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float) if c in df.columns else np.nan
    rel = rel.dropna(subset=["period", "published_at"])
    rel = rel.sort_values(["published_at", "period"], kind="stable").drop_duplicates("period", keep="first")
    rel = rel.sort_values("period", kind="stable").reset_index(drop=True)
    rel["published_at"] = rel["published_at"].cummax()
    return rel


def rolling_zscore(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """Trailing z-score (the window ends at the current observation; nothing centred, nothing from the future)."""
    mp = min_periods if min_periods is not None else max(10, window // 2)
    mu = series.rolling(window, min_periods=mp).mean()
    sd = series.rolling(window, min_periods=mp).std()
    return (series - mu) / sd.where(sd > 0)


# ----------------------------------------------------------------------------------------------------------------
# event calendar features
# ----------------------------------------------------------------------------------------------------------------
def binary_events(cal: EventCalendar, start: datetime, end: datetime) -> list[ScheduledEvent]:
    """All binary scheduled events with ``start <= ts <= end``, sorted by time.

    The calendar is queried one calendar month at a time: its ICE-expiry rule only enumerates the four contract
    months listed at the start of a query window, so a single multi-year query would miss later expiries.
    """
    seen: dict[tuple[str, datetime], ScheduledEvent] = {}
    cur = datetime(start.year, start.month, 1, tzinfo=UTC)
    while cur <= end:
        nxt = datetime(cur.year + (cur.month // 12), cur.month % 12 + 1, 1, tzinfo=UTC)
        for ev in cal.events_between(max(cur, start), min(nxt, end)):
            if ev.binary:
                seen[(ev.event_id, ev.ts)] = ev
        cur = nxt
    return sorted(seen.values(), key=lambda e: e.ts)


def _epoch_ns(idx: pd.DatetimeIndex) -> np.ndarray:
    """Nanoseconds since the epoch (UTC) as int64."""
    return to_utc_ns(idx).tz_convert(None).to_numpy(dtype="datetime64[ns]").astype(np.int64)


def _hours_to_next_event(
    cutoffs: pd.DatetimeIndex, events: list[ScheduledEvent], horizon_days: int
) -> tuple[np.ndarray, list[str | None]]:
    n = len(cutoffs)
    hours = np.full(n, np.nan)
    ids: list[str | None] = [None] * n
    if not events or n == 0:
        return hours, ids
    ev_ns = _epoch_ns(to_utc_ns([e.ts for e in events]))
    cut_ns = _epoch_ns(cutoffs)
    pos = np.searchsorted(ev_ns, cut_ns, side="left")  # first event at or after the settlement
    horizon_ns = horizon_days * 24 * _NS_PER_HOUR
    for i in range(n):
        p = int(pos[i])
        if p >= len(events):
            continue
        delta = int(ev_ns[p] - cut_ns[i])
        if delta <= horizon_ns:
            hours[i] = delta / _NS_PER_HOUR
            ids[i] = events[p].event_id
    return hours, ids


def _days_to_front_expiry(days: pd.DatetimeIndex) -> np.ndarray:
    out = np.full(len(days), np.nan)
    cache: dict[date, int] = {}
    for i, ts in enumerate(days):
        d = ts.date()
        if d not in cache:
            y, m = front_month("BZ", d)
            cache[d] = (ice_brent_expiry(y, m) - d).days
        out[i] = cache[d]
    return out


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    """Calendar/event features for every trading date of ``base`` (same index, catalog column names only)."""
    out = pd.DataFrame(index=base.index)
    for c in COLUMNS:
        out[c] = np.nan
    out[cat.NEXT_EVENT_ID] = pd.Series([None] * len(base.index), index=base.index, dtype=object)
    if len(base.index) == 0:
        return out

    days = trading_dates(base.index)
    cutoffs = settlement_index(base.index)
    cal = EventCalendar()

    start = cutoffs[0].to_pydatetime()
    end = cutoffs[-1].to_pydatetime() + timedelta(days=EVENT_HORIZON_DAYS)
    hours, ids = _hours_to_next_event(cutoffs, binary_events(cal, start, end), EVENT_HORIZON_DAYS)
    out[cat.HOURS_TO_EVENT] = hours
    out[cat.NEXT_EVENT_ID] = pd.Series(ids, index=base.index, dtype=object)
    out[cat.DAYS_TO_EXPIRY] = _days_to_front_expiry(days)
    out[cat.IS_PRE_WEEKEND] = (days.weekday == 4).astype(float)
    out[cat.HURRICANE_SEASON] = np.array([1.0 if cal.is_hurricane_season(d.date()) else 0.0 for d in days])
    return out
