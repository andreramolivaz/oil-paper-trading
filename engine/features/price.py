"""Price features: log returns, overnight gap, ATR, Donchian channel position, vol-scaled time-series momentum,
EMA spread and calendar encodings.

Returns use PX (roll-adjusted continuous close). Channel/ATR/gap use the front OHLC rescaled by PX/close
(see volatility.adjusted_ohlc) so roll jumps do not create spurious gaps; when OHLC is missing they fall back to
PX alone (flagged in attrs['ohlc_fallback']). Donchian channels are computed on the PREVIOUS N bars, so a close
beyond the channel reads < 0 or > 1 (the breakout condition used by S2). All windows are trailing.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.builder import log_series
from engine.features.volatility import TRADING_DAYS, adjusted_ohlc, realized_vol

RET_LOOKBACKS: dict[str, int] = {
    cat.RET_1: 1,
    cat.RET_5: 5,
    cat.RET_21: 21,
    cat.RET_63: 63,
    cat.RET_126: 126,
    cat.RET_252: 252,
}
TSMOM_LOOKBACKS: dict[str, int] = {
    cat.TSMOM_10: 10,
    cat.TSMOM_21: 21,
    cat.TSMOM_63: 63,
    cat.TSMOM_126: 126,
    cat.TSMOM_252: 252,
}
COLUMNS: list[str] = [
    *RET_LOOKBACKS,
    cat.GAP_1,
    cat.ATR_14,
    cat.DONCHIAN_POS_20,
    cat.DONCHIAN_POS_55,
    *TSMOM_LOOKBACKS,
    cat.EMA_FAST_SLOW,
    cat.MONTH,
    cat.DOY_SIN,
    cat.DOY_COS,
]


def atr_fraction(px: pd.Series, adj: pd.DataFrame | None, window: int = 14) -> pd.Series:
    """Wilder ATR as a fraction of PX; true range from adjusted bars, |dPX| when OHLC is missing."""
    prev = px.shift(1)
    if adj is None:
        tr = (px - prev).abs()
    else:
        hl = adj["high"] - adj["low"]
        hc = (adj["high"] - prev).abs()
        lc = (adj["low"] - prev).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1, skipna=False)
    atr = tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    return atr / px


def donchian_position(px: pd.Series, adj: pd.DataFrame | None, window: int) -> pd.Series:
    """(close - low_N) / (high_N - low_N) with the channel over the previous N bars (excludes today)."""
    hi_src = adj["high"] if adj is not None else px
    lo_src = adj["low"] if adj is not None else px
    hi = hi_src.shift(1).rolling(window, min_periods=window).max()
    lo = lo_src.shift(1).rolling(window, min_periods=window).min()
    width = (hi - lo).where((hi - lo) > 0)
    return (px - lo) / width


def tsmom(logpx: pd.Series, rv_21: pd.Series, lookback: int) -> pd.Series:
    """Return over `lookback` days divided by the vol expected over that horizon (rv_yz_21 * sqrt(L/252))."""
    denom = (rv_21 * math.sqrt(lookback / TRADING_DAYS)).where(rv_21 > 0)
    return logpx.diff(lookback) / denom


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    px = base[cat.PX].astype("float64").where(base[cat.PX] > 0)
    logpx = log_series(px)
    adj = adjusted_ohlc(base)
    out: dict[str, pd.Series] = {}
    for name, lb in RET_LOOKBACKS.items():
        out[name] = logpx.diff(lb)
    if adj is not None:
        out[cat.GAP_1] = log_series(adj["open"] / px.shift(1))
    else:
        out[cat.GAP_1] = pd.Series(np.nan, index=base.index, dtype="float64")
    out[cat.ATR_14] = atr_fraction(px, adj, 14)
    out[cat.DONCHIAN_POS_20] = donchian_position(px, adj, 20)
    out[cat.DONCHIAN_POS_55] = donchian_position(px, adj, 55)
    rv_21, _ = realized_vol(base, 21)
    for name, lb in TSMOM_LOOKBACKS.items():
        out[name] = tsmom(logpx, rv_21, lb)
    ema_fast = px.ewm(span=20, adjust=False, min_periods=20).mean()
    ema_slow = px.ewm(span=100, adjust=False, min_periods=100).mean()
    out[cat.EMA_FAST_SLOW] = (ema_fast - ema_slow) / px
    idx = pd.DatetimeIndex(base.index)
    out[cat.MONTH] = pd.Series(idx.month.to_numpy(dtype="float64"), index=base.index)
    angle = 2.0 * np.pi * idx.dayofyear.to_numpy(dtype="float64") / 365.25
    out[cat.DOY_SIN] = pd.Series(np.sin(angle), index=base.index)
    out[cat.DOY_COS] = pd.Series(np.cos(angle), index=base.index)
    frame = pd.DataFrame(out, index=base.index).astype("float64")
    frame.attrs["ohlc_fallback"] = adj is None
    frame.attrs["approx_columns"] = (
        [cat.GAP_1, cat.ATR_14, cat.DONCHIAN_POS_20, cat.DONCHIAN_POS_55] if adj is None else []
    )
    return frame
