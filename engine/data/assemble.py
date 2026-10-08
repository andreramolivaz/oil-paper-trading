"""Assemble the archived raw snapshots into a :class:`engine.data.market_data.MarketData`.

What this module guarantees
--------------------------
* **The exact contract of** :mod:`engine.data.market_data`: column names, index types and table shapes are taken
  from that module's constants, never re-spelled here.
* **No invented value.** ``prices`` is indexed by the UNION of the dates the sources provide and stays NaN where
  a source has no observation: nothing is forward-filled, interpolated or carried over.
* **Point-in-time by construction.** Every table keeps its adapter's ``published_at``; ``wpsr`` and ``cot`` are
  indexed BY the release timestamp, so ``MarketData.truncate(T)`` drops a WPSR published after T even when the
  week it describes ended before T (``tests/test_assemble.py`` tests exactly that).
* **Approximations stay labelled.** The historical NYMEX WTI curve (EIA ``RCLC1..RCLC4``, 1983 -> 2024-04-05)
  goes into the SEPARATE columns ``WTI_C1``..``WTI_C4`` of ``md.curve`` - it is never written into ``M1``..``M4``
  as if it were Brent - and the rows where the only curve information is that proxy are flagged in
  ``md.curve_approx``. The continuous series records the spread source of each roll
  (:mod:`engine.data.continuous`).
* **Fallback order comes from the config**, so ``brent_spot`` prefers EIA (API, then the key-less XLS) and only
  then FRED, exactly as ``config/data_sources.yaml`` lists them.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from engine.core.config import Settings, load_config
from engine.data.base import Health, SourceHealth
from engine.data.continuous import build_curve_table, build_roll_adjusted, curve_spreads_from_table
from engine.data.continuous import roll_dates_from_calendar as _roll_dates
from engine.data.fetch import FRESHNESS, GDELT_DAILY_KEY, INTRADAY_KEY, quality_issues
from engine.data.market_data import (
    COT_COLUMNS,
    CURVE_COLUMNS,
    INSIDER_COLUMNS,
    NEWS_COLUMNS,
    PRICE_COLUMNS,
    WPSR_COLUMNS,
    MarketData,
)
from engine.data.quality import QualityIssue, check_divergence, health_from_issues
from engine.data.raw_store import OBSERVED_AT_COLUMN, SOURCE_COLUMN, RawStore

log = logging.getLogger(__name__)

META_COLUMNS = (OBSERVED_AT_COLUMN, SOURCE_COLUMN)
VALUE_CANDIDATES = ("value", "close")
WTI_PROXY_COLUMNS = ["WTI_C1", "WTI_C2", "WTI_C3", "WTI_C4"]
EIA_FUTURES_SERIES = ["RCLC1", "RCLC2", "RCLC3", "RCLC4"]
ROLL_BUFFER_DAYS = 2  # roll two business days before expiry (config/risk.yaml owns the trading rule)
CURVE_HISTORY_SNAPSHOTS = 400  # how many archived curve snapshots to read at most

# price column -> (config group, config entry, which column of the source frame)
PRICE_SOURCES: list[tuple[str, str, str, str]] = [
    ("brent_spot", "prices", "brent_spot", "value"),
    ("wti_spot", "prices", "wti_spot", "value"),
    ("brent_front_open", "prices", "brent_front", "open"),
    ("brent_front_high", "prices", "brent_front", "high"),
    ("brent_front_low", "prices", "brent_front", "low"),
    ("brent_front_close", "prices", "brent_front", "close"),
    ("brent_front_volume", "prices", "brent_front", "volume"),
    ("brent_front_oi", "prices", "brent_front", "open_interest"),
    ("wti_front_close", "prices", "wti_front", "close"),
    ("rbob_close", "prices", "rbob_front", "close"),
    ("ho_close", "prices", "ho_front", "close"),
    ("ovx", "volatility_macro", "ovx", "value"),
    ("vix", "volatility_macro", "vix", "value"),
    ("dxy", "volatility_macro", "dxy", "value"),
    ("spx", "volatility_macro", "spx", "value"),
    ("copper", "volatility_macro", "copper", "value"),
    ("us10y", "volatility_macro", "us10y", "value"),
    ("breakeven10y", "volatility_macro", "breakeven10y", "value"),
]


class Loader:
    """Reads the newest snapshot of each configured table, honouring the config's fallback order."""

    def __init__(self, store: RawStore, settings: Settings, asof: datetime | None = None):
        self.store = store
        self.settings = settings
        self.config: dict[str, Any] = load_config("data_sources", settings.config_dir)
        self.asof = asof
        self.used: dict[str, str] = {}  # entry -> adapter whose snapshot was loaded
        self.fallbacks: dict[str, str | None] = {}
        self.missing: list[str] = []

    def candidates(self, group: str, entry: str) -> list[str]:
        chain = (self.config.get(group) or {}).get(entry) or []
        ordered = [str(e["adapter"]) for e in chain if isinstance(e, dict) and e.get("adapter")]
        # a snapshot written by an adapter that is no longer listed must still be readable
        return [*ordered, *[k for k in self.store.keys(entry) if k not in ordered]]

    def load(self, group: str, entry: str) -> pd.DataFrame | None:
        """Newest snapshot of ``entry``: the first adapter of the chain that has one."""
        if entry in self.used:
            adapter = self.used[entry]
            frame = self._read(entry, adapter)
            return frame
        for position, adapter in enumerate(self.candidates(group, entry)):
            frame = self._read(entry, adapter)
            if frame is None or frame.empty:
                continue
            self.used[entry] = adapter
            self.fallbacks[entry] = None if position == 0 else adapter
            return frame
        self.missing.append(entry)
        return None

    def _read(self, entry: str, adapter: str) -> pd.DataFrame | None:
        frame = (
            self.store.load_latest(entry, adapter)
            if self.asof is None
            else self.store.load_asof(entry, adapter, self.asof)
        )
        if frame is None:
            return None
        return frame.drop(columns=[c for c in META_COLUMNS if c in frame.columns])

    def snapshots(self, entry: str, limit: int = CURVE_HISTORY_SNAPSHOTS) -> list[tuple[str, pd.DataFrame]]:
        """Every archived snapshot of ``entry`` across adapters, oldest first (for the curve archive)."""
        out: list[tuple[str, pd.DataFrame]] = []
        for adapter in self.store.keys(entry):
            for observed_at, frame in self.store.load_all(entry, adapter):
                if self.asof is not None and observed_at > self.asof:
                    continue
                out.append((adapter, frame.drop(columns=[c for c in META_COLUMNS if c in frame.columns])))
        return out[-limit:]


# --------------------------------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------------------------------
def _dates(index: pd.Index) -> pd.DatetimeIndex:
    """Any index of a daily source -> tz-naive midnight Timestamps (the project's trading-date index)."""
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Index(index), errors="coerce", utc=False))
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    return pd.DatetimeIndex(idx.normalize(), name="date")


def _column(frame: pd.DataFrame, wanted: str) -> pd.Series | None:
    """``wanted`` (or the generic ``value``/``close``) as a float Series indexed by trading date."""
    names = [wanted, *[c for c in VALUE_CANDIDATES if c != wanted]]
    for name in names:
        if name in frame.columns:
            s = pd.to_numeric(frame[name], errors="coerce").astype("float64")
            s.index = _dates(frame.index)
            s = s[~s.index.duplicated(keep="last")].sort_index()
            return s
    return None


def _published(frame: pd.DataFrame) -> pd.Series | None:
    if "published_at" not in frame.columns:
        return None
    pub = pd.to_datetime(frame["published_at"], errors="coerce", utc=True)
    pub.index = _dates(frame.index)
    return pub[~pub.index.duplicated(keep="last")].sort_index()


def _utc_index(frame: pd.DataFrame, column: str = "published_at") -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(frame[column], errors="coerce", utc=True))


def _default_published(index: pd.DatetimeIndex) -> pd.Series:
    """The project-wide fallback rule: a daily row is known at 23:00 UTC of its date (same as ``truncate``)."""
    return pd.Series(pd.DatetimeIndex(index).tz_localize(UTC) + pd.Timedelta(hours=23), index=index)


def _asof(frame: pd.DataFrame | None, column: str | None = None) -> datetime | None:
    """Newest timestamp of a table: its index, or ``published_at`` when the index is not temporal (the curve
    snapshots are indexed by contract code)."""
    if frame is None or frame.empty:
        return None
    if column:
        candidates: list[pd.Index] = [pd.Index(_utc_index(frame, column))]
    else:
        candidates = [pd.Index(frame.index)]
        if "published_at" in frame.columns:
            candidates.append(pd.Index(frame["published_at"]))
    for candidate in candidates:
        if not pd.api.types.is_datetime64_any_dtype(candidate):
            continue  # a contract-code index is not a timestamp: try published_at instead
        idx = pd.DatetimeIndex(pd.to_datetime(candidate, errors="coerce", utc=True))
        ts = pd.Timestamp(idx.max())
        if pd.isna(ts):
            continue
        return ts.tz_convert(UTC).to_pydatetime() if ts.tzinfo else ts.tz_localize(UTC).to_pydatetime()
    return None


# --------------------------------------------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------------------------------------------
def _prices_table(loader: Loader) -> tuple[pd.DataFrame, dict[str, str], dict[str, pd.Series]]:
    columns: dict[str, pd.Series] = {}
    sources: dict[str, str] = {}
    published: dict[str, pd.Series] = {}
    frames: dict[str, pd.DataFrame] = {}
    for column, group, entry, field in PRICE_SOURCES:
        frame = frames.get(entry)
        if frame is None:
            loaded = loader.load(group, entry)
            if loaded is None:
                continue
            frames[entry] = frame = loaded
        series = _column(frame, field)
        if series is None:
            continue
        columns[column] = series
        sources[column] = loader.used.get(entry, "?")
        pub = _published(frame)
        if pub is not None:
            published[column] = pub
    index = pd.DatetimeIndex([], name="date")
    for series in columns.values():
        index = index.union(pd.DatetimeIndex(series.index))
    index = pd.DatetimeIndex(index.sort_values(), name="date")
    prices = pd.DataFrame(index=index, columns=PRICE_COLUMNS, dtype="float64")
    for column, series in columns.items():
        prices[column] = series.reindex(index)  # NaN where the source has no row: never filled

    # continuous series
    brent_front = frames.get("brent_front")
    if brent_front is not None and "close" in brent_front.columns:
        bars = brent_front.copy()
        bars.index = _dates(bars.index)
        bars = bars[~bars.index.duplicated(keep="last")].sort_index()
        curve_table = _curve_archive(loader)[0]
        rolls = _roll_dates("BZ", bars.index[0].date(), bars.index[-1].date(), ROLL_BUFFER_DAYS)
        spreads = curve_spreads_from_table(curve_table, rolls)
        cont = build_roll_adjusted(bars, rolls, spreads, columns.get("brent_spot"))
        prices["brent_cont"] = cont["cont"].reindex(index)
        sources["brent_cont"] = f"{loader.used.get('brent_front', '?')} + roll adj"
        prices.attrs["brent_roll"] = cont.attrs
    wti_front = frames.get("wti_front")
    if wti_front is not None and "close" in wti_front.columns:
        bars = wti_front.copy()
        bars.index = _dates(bars.index)
        bars = bars[~bars.index.duplicated(keep="last")].sort_index()
        rolls = _roll_dates("CL", bars.index[0].date(), bars.index[-1].date(), ROLL_BUFFER_DAYS)
        cont = build_roll_adjusted(bars, rolls, None, columns.get("wti_spot"))
        prices["wti_cont"] = cont["cont"].reindex(index)
        sources["wti_cont"] = f"{loader.used.get('wti_front', '?')} + roll adj"
        prices.attrs["wti_roll"] = cont.attrs

    pub_prices = published.get("brent_front_close")
    row_pub = pub_prices.reindex(index) if pub_prices is not None else pd.Series(index=index, dtype="object")
    missing_pub = row_pub.isna()
    if bool(missing_pub.any()):
        fallback = _default_published(index)
        row_pub = row_pub.where(~missing_pub, fallback)
    out_published = {"prices": pd.to_datetime(row_pub, utc=True)}
    for column in ("brent_spot", "wti_spot"):
        if column in published:
            out_published[column] = published[column]
    return prices, sources, out_published


def _curve_archive(loader: Loader) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Archived Yahoo Brent curve snapshots -> the curve table (``M1``..``M36``, ``M1_code``)."""
    from engine.data.base import FetchResult  # local import: only needed to re-wrap archived frames

    results: list[FetchResult] = []
    for adapter, frame in loader.snapshots("brent_curve"):
        if frame.empty or "rank" not in frame.columns:
            continue
        asof = None
        if "date" in frame.columns:
            # Index.notna() returns a numpy array (unlike Series.notna()): dropna() keeps this readable
            dates = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="coerce")).dropna()
            if len(dates):
                asof = pd.Timestamp(dates.max()).date().isoformat()
        results.append(
            FetchResult(
                source=adapter,
                frame=frame,
                fetched_at=datetime.now(tz=UTC),
                meta={"asof": asof} if asof else {},
            )
        )
    # make the curve visible to the health report (it is read through snapshots(), not through load())
    if results:
        loader.used.setdefault("brent_curve", results[-1].source)
        loader.fallbacks.setdefault("brent_curve", None)
    elif "brent_curve" not in loader.missing:
        loader.missing.append("brent_curve")
    table = build_curve_table(results)
    return table, {"snapshots": len(results)}


def _curve_table(loader: Loader) -> tuple[pd.DataFrame, pd.Series, dict[str, str], pd.Series | None]:
    table, info = _curve_archive(loader)
    columns = [*CURVE_COLUMNS, "M1_code"]
    sources: dict[str, str] = {}
    if not table.empty:
        sources["curve"] = f"{loader.used.get('brent_curve', 'yahoo')} ({info['snapshots']} snapshot)"
    published = table["published_at"] if "published_at" in table.columns else None
    curve = table.reindex(columns=columns) if not table.empty else pd.DataFrame(columns=columns)
    # historical WTI proxy: SEPARATE columns, never M1..M4
    proxy = loader.load("prices", "wti_curve_hist")
    if proxy is not None and not proxy.empty:
        proxy_frame = proxy.copy()
        proxy_frame.index = _dates(proxy_frame.index)
        proxy_frame = proxy_frame[~proxy_frame.index.duplicated(keep="last")].sort_index()
        mapped = {
            out: pd.to_numeric(proxy_frame[src], errors="coerce").astype("float64")
            for out, src in zip(WTI_PROXY_COLUMNS, EIA_FUTURES_SERIES, strict=True)
            if src in proxy_frame.columns
        }
        if mapped:
            proxy_table = pd.DataFrame(mapped)
            index = pd.DatetimeIndex(curve.index).union(pd.DatetimeIndex(proxy_table.index))
            index = pd.DatetimeIndex(index.sort_values(), name="date")
            curve = curve.reindex(index)
            for col, series in proxy_table.items():
                curve[str(col)] = series.reindex(index)
            if published is not None:
                published = published.reindex(index)
            proxy_pub = _published(proxy)
            if proxy_pub is not None:
                base = published if published is not None else pd.Series(index=index, dtype="object")
                published = base.where(base.notna(), proxy_pub.reindex(index))
            sources["curve_wti_proxy"] = f"{loader.used.get('wti_curve_hist', 'eia')} RCLC1..RCLC4 (approx)"
    if curve.empty:
        return curve, pd.Series(dtype=bool), sources, published
    curve.index = pd.DatetimeIndex(curve.index, name="date")
    has_real = curve["M1"].notna() if "M1" in curve.columns else pd.Series(False, index=curve.index)
    has_proxy = (
        curve[[c for c in WTI_PROXY_COLUMNS if c in curve.columns]].notna().any(axis=1)
        if any(c in curve.columns for c in WTI_PROXY_COLUMNS)
        else pd.Series(False, index=curve.index)
    )
    approx = (~has_real.fillna(False)) & has_proxy
    if published is not None:
        published = pd.to_datetime(published, errors="coerce", utc=True)
        missing = published.isna()
        if bool(missing.any()):
            published = published.where(~missing, _default_published(pd.DatetimeIndex(curve.index)))
    return curve, approx.astype(bool), sources, published


def _wpsr_table(loader: Loader) -> tuple[pd.DataFrame, dict[str, str]]:
    frame = loader.load("fundamentals", "wpsr")
    if frame is None or frame.empty:
        return pd.DataFrame(columns=WPSR_COLUMNS, index=pd.DatetimeIndex([], name="published_at")), {}
    out = frame.copy()
    period = _dates(out.index)
    out = out.reset_index(drop=True)
    out["period"] = period
    index = _utc_index(out)
    out = out.drop(columns=["published_at"])
    out.index = pd.DatetimeIndex(index, name="published_at")
    out = out.reindex(columns=WPSR_COLUMNS)
    out = out[out.index.notna()].sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out, {"wpsr": loader.used.get("wpsr", "?")}


def _cot_table(loader: Loader) -> tuple[pd.DataFrame, dict[str, str]]:
    parts: list[pd.DataFrame] = []
    sources: dict[str, str] = {}
    for entry in ("cot_wti", "cot_brent", "cot_gasoil"):
        frame = loader.load("positioning", entry)
        if frame is None or frame.empty:
            continue
        block = frame.copy()
        period = _dates(block.index)
        block = block.reset_index(drop=True)
        block["period"] = period
        index = _utc_index(block)
        block = block.drop(columns=["published_at"])
        block.index = pd.DatetimeIndex(index, name="published_at")
        parts.append(block.reindex(columns=COT_COLUMNS))
        sources[entry] = loader.used.get(entry, "?")
    if not parts:
        return pd.DataFrame(columns=COT_COLUMNS, index=pd.DatetimeIndex([], name="published_at")), sources
    cot = pd.concat(parts)
    cot = cot[cot.index.notna()]
    cot = cot.sort_values(["period", "market"], kind="stable").sort_index(kind="stable")
    cot = cot[~cot.duplicated(subset=["period", "market"], keep="last")]
    cot.index = pd.DatetimeIndex(cot.index, name="published_at")
    return cot, sources


def _news_table(loader: Loader) -> tuple[pd.DataFrame, pd.Series, dict[str, str]]:
    sources: dict[str, str] = {}
    gpr = loader.load("news", "gpr")
    gdelt = None
    for adapter in loader.store.keys(GDELT_DAILY_KEY):
        candidate = loader.store.load_latest(GDELT_DAILY_KEY, adapter)
        if candidate is not None and not candidate.empty:
            gdelt = candidate.drop(columns=[c for c in META_COLUMNS if c in candidate.columns])
            sources["gdelt"] = adapter
            break
    blocks: list[pd.DataFrame] = []
    pubs: list[pd.Series] = []
    if gpr is not None and not gpr.empty:
        block = pd.DataFrame(index=_dates(gpr.index))
        for col in ("gpr", "gpr_act", "gpr_threat"):
            if col in gpr.columns:
                block[col] = pd.to_numeric(gpr[col], errors="coerce").astype("float64").to_numpy()
        block = block[~block.index.duplicated(keep="last")].sort_index()
        blocks.append(block)
        pub = _published(gpr)
        if pub is not None:
            pubs.append(pub)
        sources["gpr"] = loader.used.get("gpr", "?")
    if gdelt is not None and not gdelt.empty:
        block = pd.DataFrame(index=_dates(gdelt.index))
        for col in NEWS_COLUMNS:
            if col.startswith("gdelt_") and col in gdelt.columns:
                block[col] = pd.to_numeric(gdelt[col], errors="coerce").astype("float64").to_numpy()
        block = block[~block.index.duplicated(keep="last")].sort_index()
        blocks.append(block)
        pub = _published(gdelt)
        if pub is not None:
            pubs.append(pub)
    if not blocks:
        empty = pd.DataFrame(columns=NEWS_COLUMNS, index=pd.DatetimeIndex([], name="date"))
        return empty, pd.Series(dtype="datetime64[ns, UTC]"), sources
    index = pd.DatetimeIndex([], name="date")
    for block in blocks:
        index = index.union(pd.DatetimeIndex(block.index))
    index = pd.DatetimeIndex(index.sort_values(), name="date")
    news = pd.DataFrame(index=index, columns=NEWS_COLUMNS, dtype="float64")
    for block in blocks:
        for col in block.columns:
            news[col] = block[col].reindex(index)
    # one published_at per row for every column: the LATEST of the contributing rules (never leak a column)
    published = pd.Series(pd.NaT, index=index, dtype="datetime64[ns, UTC]")
    for pub in pubs:
        aligned = pd.to_datetime(pub.reindex(index), utc=True)
        published = published.where(published >= aligned, aligned) if published.notna().any() else aligned
    missing = published.isna()
    if bool(missing.any()):
        published = published.where(~missing, _default_published(index))
    return news, published, sources


def _rigs_series(loader: Loader) -> tuple[pd.Series, pd.Series, dict[str, str]]:
    frame = loader.load("fundamentals", "rig_count")
    if frame is None or frame.empty or "rigs_oil" not in frame.columns:
        return pd.Series(dtype="float64", name="rigs"), pd.Series(dtype="datetime64[ns, UTC]"), {}
    index = _dates(frame.index)
    rigs = pd.Series(
        pd.to_numeric(frame["rigs_oil"], errors="coerce").astype("float64").to_numpy(), index=index, name="rigs"
    )
    rigs = rigs[~rigs.index.duplicated(keep="last")].sort_index().dropna()
    pub = _published(frame)
    published = (
        pd.to_datetime(pub.reindex(rigs.index), utc=True)
        if pub is not None
        else _default_published(pd.DatetimeIndex(rigs.index))
    )
    return rigs, published, {"rigs": loader.used.get("rig_count", "?")}


def _insider_table(loader: Loader) -> tuple[pd.DataFrame, dict[str, str]]:
    """SEC Form 4 transactions for S21. The table was fetched and archived but never read back: the strategy
    saw an empty frame and stayed silent whatever the key said. Rows keep their ``published_at`` (the filing
    deadline), which is what :meth:`MarketData.truncate` cuts on."""
    empty = pd.DataFrame(columns=[*INSIDER_COLUMNS, "published_at"], index=pd.DatetimeIndex([], name="published_at"))
    frame = loader.load("weekly_alt", "insider_form4")
    if frame is None or frame.empty or "published_at" not in frame.columns:
        return empty, {}
    missing = [c for c in INSIDER_COLUMNS if c not in frame.columns]
    if missing:
        log.warning("insider table without columns %s: ignored", missing)
        return empty, {}
    table = frame[[*INSIDER_COLUMNS, "published_at"]].copy()
    table.index = pd.DatetimeIndex(_dates(frame.index), name="transaction_date")
    table["published_at"] = pd.to_datetime(table["published_at"], utc=True, errors="coerce")
    usable = table.index.notna() & table["published_at"].notna().to_numpy()
    table = table[usable].sort_index(kind="stable")
    return table, {"insider": loader.used.get("insider_form4", "?")}


def _intraday(loader: Loader) -> tuple[pd.DataFrame | None, dict[str, str]]:
    for adapter in loader.store.keys(INTRADAY_KEY):
        frame = loader.store.load_latest(INTRADAY_KEY, adapter)
        if frame is None or frame.empty:
            continue
        bars = frame.drop(columns=[c for c in META_COLUMNS if c in frame.columns])
        bars.index = pd.DatetimeIndex(pd.to_datetime(bars.index, errors="coerce", utc=True), name="ts")
        bars = bars[bars.index.notna()]
        bars = bars[~bars.index.duplicated(keep="last")].sort_index()
        return bars, {"intraday": adapter}
    return None, {}


CROSS_CHECK_LIMITS = {"brent_spot": (0.05, 0.001), "wti_spot": (0.05, 0.001)}


def _cross_source_issues(loader: Loader, entry: str, used: str, frame: pd.DataFrame) -> list[QualityIssue]:
    """Compare the chosen source against any OTHER archived source of the same series.

    EIA and FRED serve the same EIA spot series, so they must agree to the cent; a real disagreement means one
    of the two parsers (or the source) is wrong, and that is an error, not a rounding difference.
    """
    limits = CROSS_CHECK_LIMITS.get(entry)
    if limits is None:
        return []
    chosen = _column(frame, "value")
    if chosen is None:
        return []
    issues: list[QualityIssue] = []
    for adapter in loader.store.keys(entry):
        if adapter == used:
            continue
        other = loader.store.load_latest(entry, adapter)
        if other is None or other.empty:
            continue
        series = _column(other.drop(columns=[c for c in META_COLUMNS if c in other.columns]), "value")
        if series is None:
            continue
        issues += check_divergence(chosen, series, limits[0], limits[1], entry, "value", names=(used, adapter))
    return issues


def _health(loader: Loader, now: datetime) -> list[SourceHealth]:
    """Health of each loaded table, recomputed from the archived snapshot (fresh quality checks)."""
    healths: list[SourceHealth] = []
    for entry, adapter in sorted(loader.used.items()):
        frame = loader.store.load_latest(entry, adapter)
        if frame is None:
            continue
        clean = frame.drop(columns=[c for c in META_COLUMNS if c in frame.columns])
        issues = quality_issues(entry, clean, now)
        issues += _cross_source_issues(loader, entry, adapter, clean)
        observed = frame[OBSERVED_AT_COLUMN].max() if OBSERVED_AT_COLUMN in frame.columns else None
        healths.append(
            health_from_issues(
                entry,
                issues,
                checked_at=now,
                data_asof=_asof(frame),
                fallback_used=loader.fallbacks.get(entry),
                available=True,
                rows=len(frame),
                message=f"adapter {adapter}",
                last_success_at=None
                if observed is None or pd.isna(observed)
                else pd.Timestamp(observed).to_pydatetime(),
            )
        )
    for entry in sorted(set(loader.missing)):
        healths.append(
            SourceHealth(
                source=entry,
                status=Health.RED,
                checked_at=now,
                message="nessuno snapshot archiviato (fonte non disponibile o chiave assente)",
            )
        )
    return healths


# --------------------------------------------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------------------------------------------
def build_market_data(store: RawStore, settings: Settings, asof: datetime | None = None) -> MarketData:
    """Build the aligned :class:`MarketData` from the raw snapshot store.

    ``asof`` (optional) reads the snapshot *vintage* that was archived at or before that instant, so a backtest
    can replay what the engine really had. The returned object is still a full history: use
    :meth:`MarketData.truncate` for the row-level point-in-time cut.
    """
    now = asof or datetime.now(tz=UTC)
    loader = Loader(store, settings, asof)
    prices, price_sources, published = _prices_table(loader)
    curve, curve_approx, curve_sources, curve_pub = _curve_table(loader)
    wpsr, wpsr_sources = _wpsr_table(loader)
    cot, cot_sources = _cot_table(loader)
    news, news_pub, news_sources = _news_table(loader)
    rigs, rigs_pub, rigs_sources = _rigs_series(loader)
    intraday, intraday_sources = _intraday(loader)
    insider, insider_sources = _insider_table(loader)

    if curve_pub is not None and len(curve_pub):
        published["curve"] = pd.to_datetime(curve_pub, utc=True)
    if len(news_pub):
        published["news"] = news_pub
    if len(rigs_pub):
        published["rigs"] = rigs_pub

    md = MarketData(
        prices=prices,
        curve=curve,
        curve_approx=curve_approx,
        wpsr=wpsr,
        cot=cot,
        news=news,
        rigs=rigs,
        insider=insider,
        intraday=intraday,
        published_at=published,
        health=_health(loader, now),
    )
    md.meta = {
        "built_at": now,
        "asof": asof,
        "source": {
            **price_sources,
            **curve_sources,
            **wpsr_sources,
            **cot_sources,
            **news_sources,
            **rigs_sources,
            **intraday_sources,
            **insider_sources,
        },
        "adapters": dict(loader.used),
        "fallbacks": {k: v for k, v in loader.fallbacks.items() if v},
        "missing": sorted(set(loader.missing)),
        "table_asof": {
            "prices": _asof(prices),
            "curve": _asof(curve),
            "wpsr": _asof(wpsr),
            "cot": _asof(cot),
            "news": _asof(news),
            "rigs": None if rigs.empty else _asof(rigs.to_frame()),
            "intraday": _asof(intraday) if intraday is not None else None,
        },
        "approx": {
            "curve_wti_proxy": bool(len(curve_approx) and bool(curve_approx.any())),
            "brent_cont_rolls": prices.attrs.get("brent_roll", {}).get("roll_sources", {}),
            "wti_cont_rolls": prices.attrs.get("wti_roll", {}).get("roll_sources", {}),
        },
        "freshness_tolerance_hours": {k: v.total_seconds() / 3600.0 for k, v in FRESHNESS.items()},
    }
    return md


def describe(md: MarketData) -> str:
    """Human-readable (Italian) summary used by ``python -m engine.cli assemble``."""
    lines: list[str] = []
    tables: list[tuple[str, pd.DataFrame | pd.Series | None]] = [
        ("prices", md.prices),
        ("curve", md.curve),
        ("wpsr", md.wpsr),
        ("cot", md.cot),
        ("news", md.news),
        ("rigs", md.rigs.to_frame() if len(md.rigs) else None),
        ("intraday", md.intraday),
    ]
    for name, table in tables:
        if table is None or len(table) == 0:
            lines.append(f"  {name:<9} vuota")
            continue
        shape = table.shape if isinstance(table, pd.DataFrame) else (len(table), 1)
        first = pd.Timestamp(table.index.min())
        last = pd.Timestamp(table.index.max())
        lines.append(f"  {name:<9} {shape[0]:>6} righe x {shape[1]:>2} col   {first.date()} -> {last.date()}")
    counts = {str(h.status): 0 for h in md.health}
    for h in md.health:
        counts[str(h.status)] += 1
    lines.append("  salute: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return "\n".join(lines)


def last_rows(frame: pd.DataFrame, columns: list[str], n: int = 5) -> pd.DataFrame:
    """The last ``n`` rows of ``columns`` that are not entirely NaN (for the CLI report)."""
    available = [c for c in columns if c in frame.columns]
    if not available:
        return pd.DataFrame()
    sub = frame[available]
    sub = sub[sub.notna().any(axis=1)]
    return sub.tail(n)


def stale_columns(prices: pd.DataFrame, now: datetime, max_age: timedelta = timedelta(days=5)) -> list[str]:
    """Price columns whose newest observation is older than ``max_age`` (CLI warning, no invented fill)."""
    out: list[str] = []
    for col in prices.columns:
        series = pd.to_numeric(prices[col], errors="coerce").dropna()
        if series.empty:
            out.append(col)
            continue
        last = pd.Timestamp(series.index.max())
        last_utc = last.tz_localize(UTC) if last.tzinfo is None else last.tz_convert(UTC)
        if now - last_utc.to_pydatetime() > max_age:
            out.append(col)
    return out


__all__ = ["Loader", "build_market_data", "describe", "last_rows", "stale_columns"]
