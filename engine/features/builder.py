"""FullFeatureBuilder: the daily point-in-time feature frame consumed by regime, strategies and forecasts.

Contract for feature modules (engine/features/<name>.py)::

    def compute(md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame

`base` is the daily frame with PX, PX_FRONT, SPOT and the front OHLC aliases ('open', 'high', 'low', 'close'),
index = tz-naive London trading dates <= asof (ascending), already restricted to rows published at or before
`asof`. The module returns a frame with the SAME index and only new columns named with
engine.features.catalog constants (NaN where unknown). Each module may expose `COLUMNS` (its expected output) and
may set `result.attrs['approx_columns']` for columns that are approximations. Modules are imported lazily; one
that is missing or raises is logged and its columns stay NaN -- the builder never crashes because of one module.

Point-in-time rules implemented here and shared with the modules:
  * a trading date t enters the frame only when settlement_ts(t) <= asof and, when md.published_at['prices']
    exists, when that row's published_at <= asof;
  * `align_released` maps a released series (weekly tables, lagged spot) onto trading dates with
    merge_asof(published_at <= settlement_ts(t)) -- never on the observation period;
  * nothing is forward-filled; rolling statistics are trailing-only.
"""

from __future__ import annotations

import importlib
import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.core.timeutil import ICE_SETTLEMENT_LONDON, LONDON, ensure_utc, iso
from engine.data.market_data import MarketData
from engine.features import catalog as cat

log = logging.getLogger(__name__)

DEFAULT_MODULES: list[str] = [
    "price",
    "volatility",
    "trend",
    "curve",
    "macro",
    "fundamentals",
    "positioning",
    "news",
    "events",
    "intermarket",
]
OHLC_ALIASES: dict[str, str] = {
    "open": "brent_front_open",
    "high": "brent_front_high",
    "low": "brent_front_low",
    "close": "brent_front_close",
}
BASE_COLUMNS: list[str] = [cat.PX, cat.PX_FRONT, cat.SPOT, *OHLC_ALIASES]
PX_CANDIDATES: tuple[str, ...] = ("brent_cont", "brent_front_close", "brent_spot")
STRING_FEATURES: frozenset[str] = frozenset({cat.REGIME_LABEL, cat.NEXT_EVENT_ID})
DEFAULT_N_REGIMES = 4

# Expected output of every module: used to materialise NaN columns when a module is missing or fails.
EXPECTED_COLUMNS: dict[str, list[str]] = {
    "price": [
        cat.RET_1,
        cat.RET_5,
        cat.RET_21,
        cat.RET_63,
        cat.RET_126,
        cat.RET_252,
        cat.GAP_1,
        cat.ATR_14,
        cat.DONCHIAN_POS_20,
        cat.DONCHIAN_POS_55,
        cat.TSMOM_10,
        cat.TSMOM_21,
        cat.TSMOM_63,
        cat.TSMOM_126,
        cat.TSMOM_252,
        cat.EMA_FAST_SLOW,
        cat.MONTH,
        cat.DOY_SIN,
        cat.DOY_COS,
    ],
    "volatility": [
        cat.RV_YZ_10,
        cat.RV_YZ_21,
        cat.RV_YZ_63,
        cat.RV_CC_21,
        cat.OVX,
        cat.VRP,
        cat.VOL_OF_VOL,
        cat.VOL_PCTL_1Y,
        cat.GARCH_VOL,
    ],
    "trend": [cat.HURST_100, cat.VR_5, cat.VR_20],
    "curve": [
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
    ],
    "macro": [
        cat.DXY,
        cat.DXY_RET_21,
        cat.VIX,
        cat.SPX_RET_21,
        cat.COPPER_RET_21,
        cat.US10Y,
        cat.BREAKEVEN,
        cat.MACRO_FV_RESID,
        cat.MACRO_FV_Z,
    ],
    "fundamentals": [
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
    ],
    "positioning": [
        cat.COT_MM_NET_BRENT,
        cat.COT_MM_NET_BRENT_PCTL,
        cat.COT_MM_NET_WTI_PCTL,
        cat.COT_MM_NET_CHG_4W,
        cat.COT_CROWDING,
    ],
    "news": [
        cat.GPR,
        cat.GPR_Z,
        cat.GPR_THREAT_Z,
        cat.GEO_INDEX,
        cat.GEO_SPIKE,
        cat.GDELT_TONE,
        cat.GDELT_VOLUME_Z,
        cat.DEESCALATION_FLAG,
    ],
    "events": [cat.HOURS_TO_EVENT, cat.NEXT_EVENT_ID, cat.DAYS_TO_EXPIRY, cat.IS_PRE_WEEKEND, cat.HURRICANE_SEASON],
    "intermarket": [
        cat.BRENT_WTI,
        cat.BRENT_WTI_Z,
        cat.CRACK_321,
        cat.CRACK_321_Z,
        cat.DIESEL_CRACK,
        cat.GASOLINE_CRACK,
        cat.PRODUCT_LEAD,
    ],
}


# ----------------------------------------------------------------------------------------------------------------
# shared point-in-time helpers
# ----------------------------------------------------------------------------------------------------------------
def regime_columns(n_regimes: int = DEFAULT_N_REGIMES) -> list[str]:
    """REGIME_* placeholder columns (filled later by the regime model)."""
    return [
        cat.REGIME_ID,
        cat.REGIME_LABEL,
        cat.REGIME_CONF,
        *[f"{cat.REGIME_P_PREFIX}{i}" for i in range(n_regimes)],
        cat.BOCPD_CP_PROB,
    ]


def catalog_order(n_regimes: int = DEFAULT_N_REGIMES) -> list[str]:
    """Every feature column in catalogue order (regime_p_i expanded after REGIME_CONF)."""
    out: list[str] = []
    for key, value in vars(cat).items():
        if not (key.isupper() and isinstance(value, str)):
            continue
        if key == "REGIME_P_PREFIX":
            out.extend(f"{value}{i}" for i in range(n_regimes))
            continue
        out.append(value)
    return out


def trading_dates(index: pd.Index) -> pd.DatetimeIndex:
    """Normalise an index of trading dates to tz-naive midnight Timestamps (the MarketData contract)."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert(LONDON).tz_localize(None)
    return pd.DatetimeIndex(idx.normalize(), name="date")


def settlement_index(dates: pd.Index) -> pd.DatetimeIndex:
    """UTC timestamp of the ICE settlement (19:30 London) for each tz-naive trading date."""
    idx = trading_dates(dates)
    local = idx + pd.Timedelta(hours=ICE_SETTLEMENT_LONDON.hour, minutes=ICE_SETTLEMENT_LONDON.minute)
    return pd.DatetimeIndex(local.tz_localize(LONDON).tz_convert(UTC))


def to_utc_series(s: pd.Series) -> pd.Series:
    """Series of timestamps -> tz-aware UTC (naive values are assumed UTC)."""
    vals = pd.to_datetime(s)
    if getattr(vals.dt, "tz", None) is None:
        vals = vals.dt.tz_localize(UTC)
    else:
        vals = vals.dt.tz_convert(UTC)
    return vals


def align_released(values: pd.Series, published_at: pd.Series, dates: pd.Index) -> pd.Series:
    """Latest value whose publication time is <= the ICE settlement of each trading date (point-in-time).

    `values` and `published_at` share an index (observation period or release id); rows with a NaN value or
    NaT publication are ignored. Never aligns on the observation period.
    """
    idx = trading_dates(dates)
    pub = to_utc_series(published_at).reindex(values.index)
    right = pd.DataFrame({"published_at": pub.to_numpy(), "value": pd.to_numeric(values, errors="coerce").to_numpy()})
    right = right.dropna().sort_values("published_at", kind="stable")
    if right.empty or len(idx) == 0:
        return pd.Series(np.nan, index=idx, dtype="float64")
    left = pd.DataFrame({"cutoff": settlement_index(idx), "date": idx})
    merged = pd.merge_asof(left, right, left_on="cutoff", right_on="published_at", direction="backward")
    return pd.Series(merged["value"].to_numpy(dtype="float64"), index=idx)


def log_series(s: pd.Series) -> pd.Series:
    """Natural log of a positive series, as a float Series (NaN where <= 0 or missing)."""
    vals = pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(vals > 0, np.log(np.where(vals > 0, vals, 1.0)), np.nan)
    return pd.Series(out, index=s.index, dtype="float64")


def prices_at(md: MarketData, column: str, index: pd.Index) -> pd.Series:
    """One md.prices column re-indexed to the base dates (float, NaN where missing or absent)."""
    if column not in md.prices.columns or md.prices.empty:
        return pd.Series(np.nan, index=index, dtype="float64")
    s = pd.to_numeric(md.prices[column], errors="coerce").astype("float64")
    s.index = trading_dates(s.index)
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(index)


# ----------------------------------------------------------------------------------------------------------------
# builder
# ----------------------------------------------------------------------------------------------------------------
class FullFeatureBuilder:
    """Builds the full daily feature frame at `asof` from a MarketData container.

    config keys (all optional): modules, n_regimes, px_backfill_spot (default True: before the first PX
    observation, PX is the EIA spot rescaled to the futures level at the junction -- documented in attrs),
    plus per-module sections ('garch', 'macro', 'curve', ...) forwarded through base.attrs['config'].
    """

    def __init__(self, config: dict[str, Any] | None = None, modules: list[str] | None = None):
        self.config: dict[str, Any] = dict(config or {})
        self.modules: list[str] = list(modules or self.config.get("modules") or DEFAULT_MODULES)
        self.n_regimes: int = int(self.config.get("n_regimes", DEFAULT_N_REGIMES))

    # ------------------------------------------------------------------ base frame
    def base_frame(self, md: MarketData, asof: datetime) -> pd.DataFrame:
        asof_utc = ensure_utc(asof)
        prices = md.prices
        attrs: dict[str, Any] = {"asof": iso(asof_utc), "px_source": None, "config": dict(self.config)}
        if prices is None or prices.empty:
            base = pd.DataFrame(np.nan, index=pd.DatetimeIndex([], name="date"), columns=BASE_COLUMNS, dtype="float64")
            base.attrs.update(attrs)
            return base

        px_df = prices.copy()
        px_df.index = trading_dates(px_df.index)
        px_df = px_df[~px_df.index.duplicated(keep="last")].sort_index()

        keep = np.asarray(settlement_index(px_df.index) <= pd.Timestamp(asof_utc), dtype=bool)
        pub = md.published_at.get("prices")
        if pub is not None and len(pub):
            pub_s = to_utc_series(pub)
            pub_s.index = trading_dates(pub_s.index)
            pub_s = pub_s[~pub_s.index.duplicated(keep="last")]
            pub_aligned = pub_s.reindex(px_df.index)
            keep &= (pub_aligned <= asof_utc).fillna(False).to_numpy()
        px_df = px_df.loc[keep]

        px_source = next((c for c in PX_CANDIDATES if c in px_df.columns and px_df[c].notna().any()), None)
        px = (
            pd.to_numeric(px_df[px_source], errors="coerce").astype("float64")
            if px_source
            else pd.Series(np.nan, index=px_df.index, dtype="float64")
        )
        attrs["px_source"] = px_source
        if px_source and px_source != "brent_spot" and self.config.get("px_backfill_spot", True):
            px, backfill = self._backfill_with_spot(px, px_df)
            if backfill:
                attrs["px_backfill"] = backfill

        cols: dict[str, pd.Series] = {cat.PX: px}
        cols[cat.PX_FRONT] = self._col(px_df, "brent_front_close")
        cols[cat.SPOT] = self._col(px_df, "brent_spot")
        for alias, src in OHLC_ALIASES.items():
            cols[alias] = self._col(px_df, src)
        base = pd.DataFrame(cols, index=px_df.index).astype("float64")
        base.index.name = "date"
        base.attrs.update(attrs)
        return base

    @staticmethod
    def _col(df: pd.DataFrame, name: str) -> pd.Series:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").astype("float64")
        return pd.Series(np.nan, index=df.index, dtype="float64")

    @staticmethod
    def _backfill_with_spot(px: pd.Series, px_df: pd.DataFrame) -> tuple[pd.Series, dict[str, Any] | None]:
        """Before the first PX observation use the EIA spot rescaled at the junction (a roll-adjustment-like splice).

        Only rows strictly before the first PX observation are touched; values are real spot prints times one
        constant, never interpolated or filled. Returns the new series and a description for attrs.
        """
        if "brent_spot" not in px_df.columns:
            return px, None
        spot = pd.to_numeric(px_df["brent_spot"], errors="coerce").astype("float64")
        valid_px = np.flatnonzero(px.notna().to_numpy())
        if len(valid_px) == 0:
            return px, None
        pos = int(valid_px[0])
        if pos == 0 or not spot.iloc[:pos].notna().any():
            return px, None
        both = np.flatnonzero((px.notna() & spot.notna() & (spot > 0)).to_numpy()[pos:])
        if len(both) == 0:
            return px, None
        jpos = pos + int(both[0])
        ratio = float(px.iloc[jpos] / spot.iloc[jpos])
        out = px.copy()
        out.iloc[:pos] = spot.iloc[:pos] * ratio
        info = {
            "column": "brent_spot",
            "until": iso(pd.Timestamp(px.index[pos - 1]).to_pydatetime()),
            "junction": iso(pd.Timestamp(px.index[jpos]).to_pydatetime()),
            "ratio": ratio,
        }
        return out, info

    # ------------------------------------------------------------------ modules
    def _run_module(self, name: str, md: MarketData, asof: datetime, base: pd.DataFrame) -> pd.DataFrame:
        mod = importlib.import_module(f"engine.features.{name}")
        out = mod.compute(md, asof, base)
        if not isinstance(out, pd.DataFrame):
            raise TypeError(f"module {name} returned {type(out).__name__}, expected DataFrame")
        if not out.index.equals(base.index):
            log.warning("feature module %s returned a different index; re-indexing to base", name)
            out = out.reindex(base.index)
        return out

    def build(self, md: MarketData, asof: datetime) -> pd.DataFrame:
        asof_utc = ensure_utc(asof)
        base = self.base_frame(md, asof_utc)
        allowed = set(catalog_order(self.n_regimes))
        parts: dict[str, pd.Series] = {c: base[c] for c in BASE_COLUMNS}
        approx: set[str] = set()
        ok: list[str] = []
        failed: dict[str, str] = {}
        module_attrs: dict[str, dict[str, Any]] = {}

        for name in self.modules:
            try:
                out = self._run_module(name, md, asof_utc, base)
            except Exception as e:
                log.warning("feature module %s failed: %s: %s", name, type(e).__name__, e)
                failed[name] = f"{type(e).__name__}: {e}"
                continue
            ok.append(name)
            approx |= {str(c) for c in out.attrs.get("approx_columns", [])}
            module_attrs[name] = {str(k): v for k, v in out.attrs.items() if k != "approx_columns"}
            for col in out.columns:
                if col in BASE_COLUMNS:
                    continue
                if col not in allowed:
                    log.warning("feature module %s produced non-catalogue column %r (dropped)", name, col)
                    continue
                if col in parts:
                    log.warning("feature column %r produced twice (module %s); keeping the first", col, name)
                    continue
                parts[col] = out[col]

        for col in catalog_order(self.n_regimes):
            if col not in parts:
                dtype = "object" if col in STRING_FEATURES else "float64"
                parts[col] = pd.Series(np.nan, index=base.index, dtype=dtype)
        # regime placeholders are always NaN here: the regime model fills them later
        for col in regime_columns(self.n_regimes):
            dtype = "object" if col in STRING_FEATURES else "float64"
            parts[col] = pd.Series(np.nan, index=base.index, dtype=dtype)

        order = [*BASE_COLUMNS, *[c for c in catalog_order(self.n_regimes) if c not in BASE_COLUMNS]]
        frame = pd.DataFrame({c: parts[c] for c in order}, index=base.index)
        for col in order:
            if col not in STRING_FEATURES:
                frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
        frame.index.name = "date"
        frame.attrs.update(
            {
                "asof": iso(asof_utc),
                "px_source": base.attrs.get("px_source"),
                "px_backfill": base.attrs.get("px_backfill"),
                "approx_columns": sorted(approx),
                "modules_ok": ok,
                "modules_failed": failed,
                "module_attrs": module_attrs,
                "n_regimes": self.n_regimes,
            }
        )
        return frame


def compute_all(md: MarketData, asof: datetime, config: dict[str, Any] | None = None) -> pd.DataFrame:
    """Convenience: FullFeatureBuilder(config).build(md, asof)."""
    return FullFeatureBuilder(config).build(md, asof)


def _json_scalar(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float | np.floating):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, int | np.integer):
        return int(v)
    if isinstance(v, pd.Timestamp | datetime):
        return iso(v.to_pydatetime() if isinstance(v, pd.Timestamp) else v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def describe(frame: pd.DataFrame) -> dict[str, Any]:
    """JSON-able summary used by the dashboard export: last values, last valid date and NaN share per column."""
    cols: dict[str, dict[str, Any]] = {}
    n = len(frame)
    for col in frame.columns:
        s = frame[col]
        last_valid = s.last_valid_index()
        cols[str(col)] = {
            "last": _json_scalar(s.iloc[-1]) if n else None,
            "last_valid": _json_scalar(last_valid) if last_valid is not None else None,
            "last_valid_value": _json_scalar(s.loc[last_valid]) if last_valid is not None else None,
            "nan_share": float(s.isna().mean()) if n else 1.0,
        }
    attrs = frame.attrs
    return {
        "asof": attrs.get("asof"),
        "rows": int(n),
        "first_date": _json_scalar(frame.index[0]) if n else None,
        "last_date": _json_scalar(frame.index[-1]) if n else None,
        "px_source": attrs.get("px_source"),
        "px_backfill": attrs.get("px_backfill"),
        "approx_columns": list(attrs.get("approx_columns", [])),
        "modules_ok": list(attrs.get("modules_ok", [])),
        "modules_failed": dict(attrs.get("modules_failed", {})),
        "columns": cols,
    }
