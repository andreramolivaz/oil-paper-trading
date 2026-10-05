"""MarketData: the aligned, point-in-time-safe container every downstream module consumes.

Column/attribute contract (daily frequency, index = London trading date as tz-naive midnight Timestamp):
  prices      : DataFrame  columns ['brent_spot','wti_spot','brent_front_open','brent_front_high','brent_front_low',
                           'brent_front_close','brent_front_volume','brent_front_oi','brent_cont' (roll-adjusted close),
                           'wti_front_close','wti_cont','rbob_close','ho_close','ovx','vix','dxy','spx','copper',
                           'us10y','breakeven10y']  (NaN where unknown; never filled with invented values)
  curve       : DataFrame  columns ['M1'..'M36'] Brent settlement by contract rank, plus 'M1_code'; NaN where unknown.
                           Attribute curve_approx (bool Series): True where the slope comes from the WTI proxy.
  wpsr        : DataFrame  index = release timestamp (UTC), columns ['period','crude_stocks','cushing_stocks',
                           'gasoline_stocks','distillate_stocks','refinery_inputs','crude_production','crude_imports',
                           'crude_exports','gasoline_supplied','distillate_supplied'] (kbbl or kbbl/d)
  cot         : DataFrame  index = release timestamp (UTC), columns ['period','market','oi','mm_long','mm_short',
                           'mm_net','prod_long','prod_short','swap_long','swap_short'] market in {brent,wti,gasoil}
  news        : DataFrame  index = date, columns ['gpr','gpr_act','gpr_threat','gdelt_volume','gdelt_tone',
                           'gdelt_goldstein','gdelt_conflict_share'] (GDELT NaN before go-live)
  rigs        : Series     index = release date, US oil rig count
  intraday    : DataFrame  optional: 1h bars of the front future for HAR-RV (UTC index)

truncate(asof) returns a copy with every row whose published_at > asof removed. Used by the no-look-ahead tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from engine.data.base import SourceHealth

PRICE_COLUMNS = [
    "brent_spot",
    "wti_spot",
    "brent_front_open",
    "brent_front_high",
    "brent_front_low",
    "brent_front_close",
    "brent_front_volume",
    "brent_front_oi",
    "brent_cont",
    "wti_front_close",
    "wti_cont",
    "rbob_close",
    "ho_close",
    "ovx",
    "vix",
    "dxy",
    "spx",
    "copper",
    "us10y",
    "breakeven10y",
]
CURVE_COLUMNS = [f"M{i}" for i in range(1, 37)]
WPSR_COLUMNS = [
    "period",
    "crude_stocks",
    "cushing_stocks",
    "gasoline_stocks",
    "distillate_stocks",
    "refinery_inputs",
    "crude_production",
    "crude_imports",
    "crude_exports",
    "gasoline_supplied",
    "distillate_supplied",
]
COT_COLUMNS = [
    "period",
    "market",
    "oi",
    "mm_long",
    "mm_short",
    "mm_net",
    "prod_long",
    "prod_short",
    "swap_long",
    "swap_short",
]
NEWS_COLUMNS = [
    "gpr",
    "gpr_act",
    "gpr_threat",
    "gdelt_volume",
    "gdelt_tone",
    "gdelt_goldstein",
    "gdelt_conflict_share",
]


def _empty(columns: list[str], index_name: str = "date") -> pd.DataFrame:
    df = pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], name=index_name))
    return df


INSIDER_COLUMNS = [
    "ticker",
    "executive",
    "role",
    "security_type",
    "side",
    "shares",
    "price",
    "value_usd",
    "open_market",
    "ten_percent_owner",
]


@dataclass
class MarketData:
    prices: pd.DataFrame = field(default_factory=lambda: _empty(PRICE_COLUMNS))
    curve: pd.DataFrame = field(default_factory=lambda: _empty([*CURVE_COLUMNS, "M1_code"]))
    curve_approx: pd.Series = field(default_factory=lambda: pd.Series(dtype=bool))
    wpsr: pd.DataFrame = field(default_factory=lambda: _empty(WPSR_COLUMNS, "published_at"))
    cot: pd.DataFrame = field(default_factory=lambda: _empty(COT_COLUMNS, "published_at"))
    news: pd.DataFrame = field(default_factory=lambda: _empty(NEWS_COLUMNS))
    insider: pd.DataFrame = field(default_factory=lambda: _empty(INSIDER_COLUMNS, "published_at"))
    rigs: pd.Series = field(default_factory=lambda: pd.Series(dtype=float, name="rigs"))
    intraday: pd.DataFrame | None = None
    published_at: dict[str, pd.Series] = field(default_factory=dict)  # table -> published_at per row (UTC)
    health: list[SourceHealth] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def last_date(self) -> pd.Timestamp | None:
        return None if self.prices.empty else pd.Timestamp(self.prices.index.max())

    def truncate(self, asof: datetime) -> MarketData:
        """Point-in-time view: drop everything published after `asof`."""
        asof_ts = pd.Timestamp(asof)
        if asof_ts.tzinfo is None:
            asof_ts = asof_ts.tz_localize(UTC)

        def cut_by_pub(df: pd.DataFrame, key: str) -> pd.DataFrame:
            pub = self.published_at.get(key)
            if pub is None or df.empty:
                # fall back: daily tables are known by 23:00 UTC of their date
                idx = pd.DatetimeIndex(df.index)
                if idx.tz is None:
                    idx = idx.tz_localize(UTC)
                keep = (idx + pd.Timedelta(hours=23)) <= asof_ts
                return df.loc[keep]
            pub_aligned = pub.reindex(df.index)
            return df.loc[(pub_aligned <= asof_ts).to_numpy()]

        def cut_insider(df: pd.DataFrame) -> pd.DataFrame:
            """Form 4 rows are indexed by TRANSACTION date but become knowable at the FILING deadline, which
            the adapter stores per row. Cutting on the index would hand the engine a transaction days before
            anyone could have read the filing."""
            if df.empty or "published_at" not in df.columns:
                return df
            pub = pd.to_datetime(df["published_at"], utc=True)
            return df.loc[(pub <= asof_ts).to_numpy()]

        def cut_release(df: pd.DataFrame) -> pd.DataFrame:
            if df.empty:
                return df
            idx = pd.DatetimeIndex(df.index)
            if idx.tz is None:
                idx = idx.tz_localize(UTC)
            return df.loc[idx <= asof_ts]

        out = MarketData(
            prices=cut_by_pub(self.prices, "prices"),
            curve=cut_by_pub(self.curve, "curve"),
            curve_approx=self.curve_approx.reindex(cut_by_pub(self.curve, "curve").index)
            if len(self.curve_approx)
            else self.curve_approx,
            wpsr=cut_release(self.wpsr),
            cot=cut_release(self.cot),
            news=cut_by_pub(self.news, "news"),
            insider=cut_insider(self.insider),
            rigs=self.rigs.loc[cut_release(self.rigs.to_frame()).index] if len(self.rigs) else self.rigs,
            intraday=None if self.intraday is None else cut_release(self.intraday),
            published_at={k: v.loc[v <= asof_ts] for k, v in self.published_at.items()},
            health=list(self.health),
            meta=dict(self.meta),
        )
        return out
