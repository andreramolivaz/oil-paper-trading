"""Roll-adjusted continuous Brent series and the archived forward-curve table.

Why this module exists
----------------------
``BZ=F`` on Yahoo is the *front* contract: on a roll day its level jumps by the M1-M2 spread, which in the
2026 backwardation is 3-4 $ (Dec-26 101.41 vs Jan-27 97.95 on 2026-10-05). A strategy that read that jump as a
return would see a fake -3.4 % day. :func:`build_roll_adjusted` removes exactly that jump and nothing else.

Method (additive back-adjustment, anchored at the END of the sample)
-------------------------------------------------------------------
With roll dates ``d`` (the first date on which the front series quotes the NEW contract) and the roll spread
``s_d = new_contract - old_contract`` observed at the roll::

    adj[t] = sum of s_d over the roll dates d > t          cont[t] = close[t] + adj[t]

so ``cont`` equals the front close on and after the last roll (today's level is the real one, which is what the
dashboard shows) and the return across a roll becomes ``close[d] - close[d-1] - s_d`` - the move of the new
contract alone. Additive (not ratio) adjustment keeps differences in USD/bbl comparable, which is what the
volatility, HAR-RV and ATR features consume.

Where ``s_d`` comes from, in order of preference (recorded per row in ``roll_adj_source``):

======================  ====================================================================================
``curve``               the real Brent M2-M1 settlement spread of the roll day (``roll_spreads``)
``wti_proxy``           the NYMEX WTI C1-C2 spread of the roll day, passed in ``roll_spreads.attrs["wti_proxy"]``
                        (EIA ``RCLC1``/``RCLC2``, 1983 -> 2024-04-05). An approximation: labelled ``approx``
``spot_return``         no spread at all: the roll-day return is replaced by the Brent **spot** return, i.e.
                        ``s_d = (close[d] - close[d-1]) - (spot[d] - spot[d-1])``
``none``                nothing available: the day is left unadjusted and the jump stays visible (honest)
======================  ====================================================================================

``roll_adj_source`` is per row and describes the roll applied **on that date**: a non-roll date is ``none``
because no adjustment event happens there.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from engine.core.calendar import add_business_days, expiry_for, listed_months
from engine.data.base import FetchResult
from engine.data.market_data import CURVE_COLUMNS

log = logging.getLogger(__name__)

ROLL_SOURCES = ("curve", "wti_proxy", "spot_return", "none")
WTI_PROXY_ATTR = "wti_proxy"
DEFAULT_ROLL_BUFFER_DAYS = 2
CONT_COLUMN = "cont"
SOURCE_COLUMN = "roll_adj_source"
CURVE_TABLE_COLUMNS = [*CURVE_COLUMNS, "M1_code", "published_at"]


def roll_dates_from_calendar(
    root: str,
    start: date,
    end: date,
    roll_buffer_days: int = DEFAULT_ROLL_BUFFER_DAYS,
) -> list[date]:
    """Roll dates in ``[start, end]``: ``roll_buffer_days`` business days before each expiry.

    The expiry calendar is :mod:`engine.core.calendar` (ICE Brent: last business day of the second month before
    the delivery month, with the Christmas/New-Year exception). With ``roll_buffer_days=0`` the roll date is the
    expiry itself. Returned dates are unique and sorted.
    """
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    buffer_days = max(0, int(roll_buffer_days))
    # look a year past the window so a roll just after `end` is not needed, and a year before so the first
    # expiry whose roll falls inside the window is included
    months = listed_months(root, date(start.year - 1, 1, 1), 12 * ((end.year - start.year) + 4))
    out: list[date] = []
    for year, month in months:
        expiry = expiry_for(root, year, month)
        roll = add_business_days(expiry, -buffer_days) if buffer_days else expiry
        if start <= roll <= end:
            out.append(roll)
    return sorted(set(out))


def _float(value: Any) -> float:
    """Scalar from a frame cell -> float (NaN when it is not a number)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _series_on(index: pd.DatetimeIndex, series: pd.Series | None) -> pd.Series:
    if series is None or len(series) == 0:
        return pd.Series(np.nan, index=index, dtype="float64")
    s = pd.to_numeric(series, errors="coerce").astype("float64")
    s.index = pd.DatetimeIndex(s.index)
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(index)


def build_roll_adjusted(
    front_bars: pd.DataFrame,
    roll_dates: list[date],
    roll_spreads: pd.Series | None = None,
    spot: pd.Series | None = None,
) -> pd.DataFrame:
    """Additive back-adjusted continuous series from front-contract bars.

    ``front_bars``: daily frame with at least ``close`` (``open``/``high``/``low`` are shifted by the same
    adjustment when present), index = tz-naive trading date. ``roll_spreads``: ``new - old`` spread by roll date
    (the real Brent M2-M1); its ``attrs["wti_proxy"]`` may carry the WTI C1-C2 proxy spread as a second choice.
    ``spot``: Brent spot level, used for the ``spot_return`` fallback.

    Returns a frame indexed like ``front_bars`` with ``cont`` (and ``cont_open``/``cont_high``/``cont_low`` when
    the inputs exist) plus ``roll_adj_source``. ``attrs["rolls"]`` lists the applied rolls and
    ``attrs["approx"]`` is True when any roll used the WTI proxy or the spot return.
    """
    if "close" not in front_bars.columns:
        raise ValueError("front_bars must carry a 'close' column")
    index = pd.DatetimeIndex(front_bars.index)
    close = pd.to_numeric(front_bars["close"], errors="coerce").astype("float64")
    close.index = index
    spreads = _series_on(index, roll_spreads)
    proxy_raw = roll_spreads.attrs.get(WTI_PROXY_ATTR) if roll_spreads is not None else None
    proxy = _series_on(index, proxy_raw if isinstance(proxy_raw, pd.Series) else None)
    spot_s = _series_on(index, spot)

    position = {ts: i for i, ts in enumerate(index)}
    sources = pd.Series("none", index=index, dtype="object")
    applied: list[dict[str, Any]] = []
    jump = pd.Series(0.0, index=index, dtype="float64")
    for roll in sorted(set(roll_dates)):
        ts = pd.Timestamp(roll)
        i = position.get(ts)
        if i is None or i == 0:
            continue  # no bar on the roll date (holiday) or no previous bar: nothing to adjust
        prev = index[i - 1]
        s: float | None = None
        kind = "none"
        if np.isfinite(spreads.loc[ts]):
            s, kind = float(spreads.loc[ts]), "curve"
        elif np.isfinite(proxy.loc[ts]):
            s, kind = float(proxy.loc[ts]), "wti_proxy"
        elif np.isfinite(spot_s.loc[ts]) and np.isfinite(spot_s.loc[prev]) and np.isfinite(close.loc[prev]):
            spot_move = float(spot_s.loc[ts] - spot_s.loc[prev])
            s, kind = float(close.loc[ts] - close.loc[prev]) - spot_move, "spot_return"
        if s is None or not np.isfinite(s):
            log.info("roll %s: no spread available, day left unadjusted", roll)
            continue
        jump.iloc[i] = s
        sources.iloc[i] = kind
        applied.append({"date": roll.isoformat(), "spread": s, "source": kind})

    # adj[t] = sum of the jumps strictly after t  ->  reverse cumulative sum, shifted by one row
    adj = jump[::-1].cumsum()[::-1].shift(-1).fillna(0.0)
    out = pd.DataFrame(index=index)
    out[CONT_COLUMN] = close + adj
    for col, name in (("open", "cont_open"), ("high", "cont_high"), ("low", "cont_low")):
        if col in front_bars.columns:
            values = pd.to_numeric(front_bars[col], errors="coerce").astype("float64")
            values.index = index
            out[name] = values + adj
    out[SOURCE_COLUMN] = sources.to_numpy()
    out.attrs["rolls"] = applied
    out.attrs["approx"] = any(r["source"] in {"wti_proxy", "spot_return"} for r in applied)
    out.attrs["roll_sources"] = {k: sum(1 for r in applied if r["source"] == k) for k in ROLL_SOURCES}
    return out


def curve_spreads_from_table(curve: pd.DataFrame, roll_dates: list[date]) -> pd.Series:
    """``M2 - M1`` of the archived curve table on the roll dates (the real Brent roll spread).

    The spread is taken from the LAST curve row at or before the roll date (the curve snapshot of the previous
    session is what a roll executed at that session's settlement would have paid).
    """
    if curve is None or curve.empty or "M1" not in curve.columns or "M2" not in curve.columns:
        return pd.Series(dtype="float64")
    cv = curve.copy()
    cv.index = pd.DatetimeIndex(cv.index)
    cv = cv[~cv.index.duplicated(keep="last")].sort_index()
    m1 = pd.to_numeric(cv["M1"], errors="coerce").astype("float64")
    m2 = pd.to_numeric(cv["M2"], errors="coerce").astype("float64")
    spread = (m2 - m1).dropna()
    if spread.empty:
        return pd.Series(dtype="float64")
    wanted = pd.DatetimeIndex(sorted({pd.Timestamp(d) for d in roll_dates}))
    if len(wanted) == 0:
        return pd.Series(dtype="float64")
    aligned = spread.reindex(spread.index.union(wanted)).ffill().reindex(wanted)
    return aligned.dropna()


def build_curve_table(snapshots: list[FetchResult]) -> pd.DataFrame:
    """Archived :meth:`engine.data.adapters.yahoo.YahooAdapter.fetch_curve` snapshots -> one row per date.

    Index = the snapshot's as-of trading date (tz-naive midnight); columns ``M1``..``M36`` (contract settlements
    by rank), ``M1_code`` (the front contract code, e.g. ``BZZ26``) and ``published_at`` (UTC, the latest bar
    publication in the snapshot). Ranks the snapshot does not carry stay NaN - never filled across ranks.
    """
    rows: dict[pd.Timestamp, dict[str, Any]] = {}
    for result in snapshots:
        frame = result.frame
        if frame is None or frame.empty or "rank" not in frame.columns:
            continue
        asof = result.meta.get("asof")
        if asof:
            day = pd.Timestamp(str(asof)).normalize()
        elif "date" in frame.columns:
            day = pd.Timestamp(pd.DatetimeIndex(frame["date"]).max()).normalize()
        else:
            continue
        row: dict[str, Any] = rows.setdefault(day, {})
        for code, record in frame.iterrows():
            rank = str(record.get("rank", ""))
            if rank not in CURVE_COLUMNS:
                continue
            close = _float(record.get("close"))
            if not np.isfinite(close):
                continue
            row[rank] = close
            if rank == "M1":
                row["M1_code"] = str(code)
        pub = pd.DatetimeIndex(frame["published_at"]).max() if "published_at" in frame.columns else None
        if pub is not None and not pd.isna(pub):
            prev = row.get("published_at")
            row["published_at"] = max(pd.Timestamp(pub), pd.Timestamp(prev)) if prev is not None else pd.Timestamp(pub)
    if not rows:
        return pd.DataFrame(columns=CURVE_TABLE_COLUMNS, index=pd.DatetimeIndex([], name="date")).astype(
            dict.fromkeys(CURVE_COLUMNS, "float64")
        )
    table = pd.DataFrame.from_dict(rows, orient="index")
    table.index = pd.DatetimeIndex(table.index, name="date")
    table = table.sort_index()
    for col in CURVE_TABLE_COLUMNS:
        if col not in table.columns:
            table[col] = pd.Series(np.nan, index=table.index, dtype="float64") if col != "M1_code" else None
    for col in CURVE_COLUMNS:
        table[col] = pd.to_numeric(table[col], errors="coerce").astype("float64")
    table["published_at"] = pd.to_datetime(table["published_at"], errors="coerce", utc=True)
    return table[CURVE_TABLE_COLUMNS]
