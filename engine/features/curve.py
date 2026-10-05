"""Term-structure features from md.curve (real ICE Brent settlements by rank, M1..Mn, plus M1_code).

Definitions (all relative to the front, > 0 = backwardation):
  SLOPE_M1_Mk      = (M1 - Mk) / M1
  ROLL_YIELD_ANN   = (M1/M2 - 1) * 12 / months_between, months_between = contract months between M1 and M2
                     (1 for consecutive Brent months; derived from M1_code / M2_code or engine.core.calendar)
  ROLL_YIELD_PCTL  = trailing 5y (1260 rows) percentile rank of ROLL_YIELD_ANN
  BUTTERFLY_1_3_6  = (M1 - 2*M3 + M6) / M1
  DEC_DEC_SPREAD   = (nearest Dec - following Dec) / nearest Dec, ranks derived from the M1 contract month
  SPOT_FRONT_PREMIUM = (SPOT - M1) / M1 of the latest date whose spot print was PUBLISHED by the settlement of t
                     (EIA spot is released with a lag: aligned through md.published_at['brent_spot'|'spot'] when
                     present, otherwise one trading day of lag is assumed and flagged in attrs)
  SPREAD_CHG_5     = 5-day change of SLOPE_M1_M6
  CURVE_APPROX     = 0 on rows with real Brent M1/M2, 1 on rows that only have the WTI proxy, NaN otherwise

WTI proxy (approximation, documented): the historical Brent forward curve is not freely available before the
archive started, so the assembler may add the EIA NYMEX WTI continuous contracts 'WTI_C1'..'WTI_C4' to md.curve.
Where the real Brent M1/M2 are missing but the proxy exists, SLOPE_M1_M2, SLOPE_M1_M3 and ROLL_YIELD_ANN are
computed from WTI C1..C3 (consecutive months, so 12 months/year) and CURVE_APPROX = 1. The proxy assumes the
Brent and WTI curves share the same *shape* (contango/backwardation), which fails when the Cushing bottleneck
distorts the WTI front (2011-13, April 2020); levels (M1..M12), butterfly, Dec-Dec and the spot premium are never
taken from the proxy. Strategies halve size or stay out when CURVE_APPROX = 1 (docs/STRATEGIES.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.core.calendar import listed_months, parse_contract_code
from engine.core.timeutil import ensure_utc
from engine.data.market_data import MarketData
from engine.features import catalog as cat
from engine.features.builder import align_released, to_utc_series, trading_dates

COLUMNS: list[str] = [
    cat.M1,
    cat.M2,
    cat.M3,
    cat.M6,
    cat.M12,
    cat.SLOPE_M1_M2,
    cat.SLOPE_M1_M3,
    cat.SLOPE_M1_M6,
    cat.SLOPE_M1_M12,
    cat.ROLL_YIELD_ANN,
    cat.ROLL_YIELD_PCTL,
    cat.BUTTERFLY_1_3_6,
    cat.DEC_DEC_SPREAD,
    cat.CURVE_APPROX,
    cat.SPOT_FRONT_PREMIUM,
    cat.SPREAD_CHG_5,
]
PROXY_COLUMNS: tuple[str, ...] = ("WTI_C1", "WTI_C2", "WTI_C3", "WTI_C4")
MAX_RANK = 36
PCTL_WINDOW = 5 * 252
SPOT_PUB_KEYS: tuple[str, ...] = ("brent_spot", "spot", "prices.brent_spot")


def _nan(index: pd.Index) -> pd.Series:
    return pd.Series(np.nan, index=index, dtype="float64")


def curve_frame(md: MarketData, asof: datetime, index: pd.Index) -> pd.DataFrame | None:
    """md.curve restricted to the base dates and to rows published at or before asof (None when absent).

    With md.published_at['curve'] the row filter is published_at <= asof; without it the table is treated as known
    at 23:00 UTC of its date, exactly like MarketData.truncate, so build(md) == build(md.truncate(asof)).
    """
    if md.curve is None or md.curve.empty:
        return None
    asof_utc = ensure_utc(asof)
    cv = md.curve.copy()
    cv.index = trading_dates(cv.index)
    cv = cv[~cv.index.duplicated(keep="last")].sort_index()
    pub = md.published_at.get("curve")
    if pub is not None and len(pub):
        pub_s = to_utc_series(pub)
        pub_s.index = trading_dates(pub_s.index)
        pub_s = pub_s[~pub_s.index.duplicated(keep="last")]
        keep = (pub_s.reindex(cv.index) <= asof_utc).fillna(False).to_numpy()
    else:
        # no publication info: mirror MarketData.truncate (a daily table is known at 23:00 UTC of its date)
        known = pd.DatetimeIndex(cv.index).tz_localize(UTC) + pd.Timedelta(hours=23)
        keep = np.asarray(known <= pd.Timestamp(asof_utc), dtype=bool)
    cv = cv.loc[keep]
    return cv.reindex(index)


def _rank(cv: pd.DataFrame, k: int) -> pd.Series:
    col = f"M{k}"
    if col in cv.columns:
        return pd.to_numeric(cv[col], errors="coerce").astype("float64")
    return _nan(cv.index)


def _contract_months(
    cv: pd.DataFrame, index: pd.Index, code_col: str, fallback_rank: int | None
) -> tuple[np.ndarray, int]:
    """Contract month index (year*12 + month) of the contract in `code_col`; NaN where unknown.

    When the code column is missing/NaN and `fallback_rank` is given, the ICE Brent listing rule from
    engine.core.calendar supplies the rank-th listed month as of the date. Returns (months, n_fallback_rows).
    """
    n = len(index)
    out = np.full(n, np.nan, dtype="float64")
    if code_col in cv.columns:
        codes = cv[code_col].astype("object")
        cache: dict[str, float] = {}
        for i, code in enumerate(codes.to_numpy()):
            if not isinstance(code, str) or len(code) < 4:
                continue
            if code not in cache:
                try:
                    _, y, m = parse_contract_code(code.strip().upper())
                    cache[code] = float(y * 12 + m)
                except (KeyError, ValueError):
                    cache[code] = np.nan
            out[i] = cache[code]
    n_fallback = 0
    if fallback_rank is not None:
        missing = np.isnan(out)
        n_fallback = int(missing.sum())
        if missing.any():
            dates = pd.DatetimeIndex(index)
            for j in np.flatnonzero(missing):
                y, m = listed_months("BZ", dates[int(j)].date(), fallback_rank)[fallback_rank - 1]
                out[int(j)] = float(y * 12 + m)
    return out, n_fallback


def roll_yield_annualised(m1: pd.Series, m2: pd.Series, months_between: np.ndarray | float) -> pd.Series:
    """(M1/M2 - 1) * 12 / months_between."""
    mb = pd.Series(np.asarray(months_between, dtype="float64") * np.ones(len(m1)), index=m1.index)
    mb = mb.where(mb > 0)
    return (m1 / m2.where(m2 > 0) - 1.0) * 12.0 / mb


def dec_dec_spread(cv: pd.DataFrame, m1_month: np.ndarray) -> pd.Series:
    """(nearest Dec - following Dec) / nearest Dec, ranks implied by the M1 contract month."""
    n = len(cv.index)
    out = np.full(n, np.nan, dtype="float64")
    ranks_avail = [k for k in range(1, MAX_RANK + 1) if f"M{k}" in cv.columns]
    if not ranks_avail or np.isnan(m1_month).all():
        return pd.Series(out, index=cv.index)
    mat = np.full((n, MAX_RANK + 1), np.nan, dtype="float64")
    for k in ranks_avail:
        mat[:, k] = pd.to_numeric(cv[f"M{k}"], errors="coerce").to_numpy(dtype="float64")
    month = np.where(np.isnan(m1_month), 1, (m1_month - 1) % 12 + 1).astype(int)  # 1..12
    near = 13 - month  # rank of the nearest December (1 when the front is a December)
    nxt = near + 12
    ok = ~np.isnan(m1_month) & (nxt <= MAX_RANK)
    rows = np.flatnonzero(ok)
    a = mat[rows, near[rows]]
    b = mat[rows, nxt[rows]]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[rows] = np.where(a > 0, (a - b) / a, np.nan)
    return pd.Series(out, index=cv.index)


def spot_front_premium(md: MarketData, base: pd.DataFrame, m1: pd.Series) -> tuple[pd.Series, str]:
    """(SPOT - M1)/M1 of the latest date whose spot print was published by the settlement of each date."""
    spot = base[cat.SPOT].astype("float64") if cat.SPOT in base.columns else _nan(base.index)
    raw = (spot - m1) / m1.where(m1 > 0)
    for key in SPOT_PUB_KEYS:
        pub = md.published_at.get(key)
        if pub is not None and len(pub):
            pub_s = to_utc_series(pub)
            pub_s.index = trading_dates(pub_s.index)
            pub_s = pub_s[~pub_s.index.duplicated(keep="last")].reindex(base.index)
            return align_released(raw, pub_s, base.index), f"published_at:{key}"
    return raw.shift(1), "lag_1_trading_day_assumed"


def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
    index = base.index
    attrs: dict[str, Any] = {}
    cv = curve_frame(md, asof, index)
    out: dict[str, pd.Series] = {c: _nan(index) for c in COLUMNS}
    if cv is None:
        attrs["curve_available"] = False
        frame = pd.DataFrame(out, index=index).astype("float64")
        frame.attrs.update(attrs)
        frame.attrs["approx_columns"] = []
        return frame
    attrs["curve_available"] = True

    m = {k: _rank(cv, k) for k in (1, 2, 3, 6, 12)}
    m1, m2, m3, m6, m12 = m[1], m[2], m[3], m[6], m[12]
    real = m1.notna() & m2.notna() & (m1 > 0)
    out[cat.M1], out[cat.M2], out[cat.M3], out[cat.M6], out[cat.M12] = m1, m2, m3, m6, m12
    out[cat.SLOPE_M1_M6] = (m1 - m6) / m1.where(m1 > 0)
    out[cat.SLOPE_M1_M12] = (m1 - m12) / m1.where(m1 > 0)
    out[cat.BUTTERFLY_1_3_6] = (m1 - 2.0 * m3 + m6) / m1.where(m1 > 0)
    out[cat.SPREAD_CHG_5] = out[cat.SLOPE_M1_M6].diff(5)

    m1_month, attrs["m1_code_fallback_rows"] = _contract_months(cv, index, "M1_code", fallback_rank=1)
    if "M2_code" in cv.columns:
        m2_month, _ = _contract_months(cv, index, "M2_code", fallback_rank=None)
        months_between = np.where(np.isnan(m2_month), 1.0, m2_month - m1_month)
    else:
        months_between = np.ones(len(index))  # ICE Brent lists every consecutive month
    real_ry = roll_yield_annualised(m1, m2, months_between)
    real_s12 = (m1 - m2) / m1.where(m1 > 0)
    real_s13 = (m1 - m3) / m1.where(m1 > 0)
    out[cat.DEC_DEC_SPREAD] = dec_dec_spread(cv, m1_month)

    # --- WTI proxy where the real curve is missing -------------------------------------------------------------
    approx = pd.Series(np.where(real, 0.0, np.nan), index=index, dtype="float64")
    has_proxy = all(c in cv.columns for c in PROXY_COLUMNS[:2])
    proxy_rows = 0
    if has_proxy:
        c1 = pd.to_numeric(cv["WTI_C1"], errors="coerce").astype("float64")
        c2 = pd.to_numeric(cv["WTI_C2"], errors="coerce").astype("float64")
        c3 = pd.to_numeric(cv["WTI_C3"], errors="coerce").astype("float64") if "WTI_C3" in cv.columns else _nan(index)
        use_proxy = ~real & c1.notna() & c2.notna() & (c1 > 0)
        proxy_rows = int(use_proxy.sum())
        approx = approx.where(~use_proxy, 1.0)
        out[cat.SLOPE_M1_M2] = real_s12.where(real, ((c1 - c2) / c1).where(use_proxy))
        out[cat.SLOPE_M1_M3] = real_s13.where(real, ((c1 - c3) / c1).where(use_proxy))
        out[cat.ROLL_YIELD_ANN] = real_ry.where(real, roll_yield_annualised(c1, c2, 1.0).where(use_proxy))
    else:
        out[cat.SLOPE_M1_M2] = real_s12.where(real)
        out[cat.SLOPE_M1_M3] = real_s13.where(real)
        out[cat.ROLL_YIELD_ANN] = real_ry.where(real)
    if len(md.curve_approx):
        flagged = md.curve_approx.copy()
        flagged.index = trading_dates(flagged.index)
        flagged = flagged[~flagged.index.duplicated(keep="last")].reindex(index).fillna(False).astype(bool)
        approx = approx.where(~(flagged & approx.notna()), 1.0)
    out[cat.CURVE_APPROX] = approx
    out[cat.ROLL_YIELD_PCTL] = out[cat.ROLL_YIELD_ANN].rolling(PCTL_WINDOW, min_periods=252).rank(pct=True)
    out[cat.SPOT_FRONT_PREMIUM], attrs["spot_alignment"] = spot_front_premium(md, base, m1)

    frame = pd.DataFrame(out, index=index).astype("float64")
    attrs["proxy_rows"] = proxy_rows
    attrs["approx_columns"] = (
        [cat.SLOPE_M1_M2, cat.SLOPE_M1_M3, cat.ROLL_YIELD_ANN, cat.ROLL_YIELD_PCTL]
        if proxy_rows or bool((frame[cat.CURVE_APPROX] == 1.0).any())
        else []
    )
    frame.attrs.update(attrs)
    return frame
