"""The fetch orchestrator: walks ``config/data_sources.yaml``, runs the fallback chains, archives raw snapshots
and writes ``state/health.json``.

Design rules
------------
* **Adapters are resolved by name, lazily.** A name in the YAML maps to ``(module, class, method)`` in
  :data:`ADAPTERS`; the module is imported only when that entry runs. A missing module, class or method does not
  crash the run: the entry gets health RED with the import error as its message.
* **Groups are independent.** One exploding group never stops the others; every exception (including
  programming errors) is caught per entry and per group.
* **Fallback chains.** The entries of a block are tried in the YAML order until one returns rows. The adapter
  that answered is recorded; when it is not the first listed one, ``fallback_used`` is set and the entry is
  YELLOW at best.
* **Keys are optional.** An entry marked ``needs_key: true`` is SKIPPED (not failed) when the matching key is
  missing from :class:`engine.core.config.Settings`, and the next entry of the chain runs. The engine never
  falls back to EIA's ``DEMO_KEY``: it is rate-limited after ~8 calls and would make the run non-deterministic.
* **Two built-in steps** complete the YAML: ``gdelt_accumulate`` (appends the latest 15-minute GDELT slot to the
  accumulating raw table and refreshes the daily aggregate - the raw files cannot be backfilled far, so the
  history has to be accumulated from the go-live) and ``brent_intraday`` (1h bars for HAR-RV).
* **Nothing is invented.** A failing chain leaves the table absent; it is never filled with a placeholder.
"""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from engine.core.config import Settings, load_config
from engine.core.errors import DataUnavailable
from engine.core.store import StateStore
from engine.data.base import FetchResult, Health, SourceHealth, utc_now
from engine.data.quality import (
    QualityIssue,
    check_duplicates,
    check_gaps,
    check_nonpositive,
    check_outliers,
    check_stale,
    health_from_issues,
    overall_health,
)
from engine.data.raw_store import RawStore

log = logging.getLogger(__name__)

HEALTH_FILE = "health.json"
GDELT_SLOT_KEY = "gdelt_15min"
GDELT_DAILY_KEY = "gdelt_daily"
INTRADAY_KEY = "brent_intraday_1h"
INTRADAY_SYMBOL = "BZ=F"
INTRADAY_INTERVAL = "1h"
INTRADAY_LOOKBACK_DAYS = 730
MAX_ACCUMULATED_SLOTS = 96 * 400  # ~1 year of 15-minute slots kept in the accumulating GDELT table


# Yahoo rate-limits a long burst with HTTP 429 and retrying does not clear it (the quota is per IP), so the
# shared adapter paces requests 1.2 s apart and gives up after two attempts with a long backoff: a full fetch
# makes ~60 Yahoo calls and must not spend half a minute retrying a non-critical index.
YAHOO_KWARGS: dict[str, Any] = {"min_interval": 1.2, "retries": 1, "backoff": 4.0}


def front_contract_symbol(asof: datetime, root: str = "BZ", roll_buffer_days: int = 3) -> str:
    """Yahoo symbol of the contract the session calls the front (``BZZ26.NYM``), never ``BZ=F``.

    The continuous symbol is not one contract: in the last days before an expiry its intraday bars jump between
    the expiring and the next month, and with the 2026 backwardation that is a fake 5-7 $ move inside one hour -
    enough to trigger a stop that no real price ever touched. The contract's own bars cannot do that. Falls
    back to the continuous symbol only if the calendar cannot name a contract.
    """
    try:
        from engine.core.calendar import front_month
        from engine.core.instruments import Future

        year, month = front_month(root, asof.date(), roll_buffer_days)
        return Future(root, year, month).yahoo_symbol
    except Exception as exc:  # pragma: no cover - the calendar covers every date we can meet
        log.warning("front contract symbol unavailable (%s): using %s", exc, INTRADAY_SYMBOL)
        return INTRADAY_SYMBOL


@dataclass(frozen=True)
class AdapterSpec:
    """How to build and call one adapter named in the YAML."""

    module: str
    cls: str
    method: str
    key: str | None = None  # "eia" | "fred" | "alphavantage": the Settings field required by `needs_key`
    ctor_takes_key: bool = False
    ctor_kwargs: dict[str, Any] = field(default_factory=dict)


ADAPTERS: dict[str, AdapterSpec] = {
    "yahoo": AdapterSpec("engine.data.adapters.yahoo", "YahooAdapter", "fetch_daily", ctor_kwargs=YAHOO_KWARGS),
    "yahoo_curve": AdapterSpec("engine.data.adapters.yahoo", "YahooAdapter", "fetch_curve", ctor_kwargs=YAHOO_KWARGS),
    "yahoo_intraday": AdapterSpec(
        "engine.data.adapters.yahoo", "YahooAdapter", "fetch_intraday", ctor_kwargs=YAHOO_KWARGS
    ),
    "eia": AdapterSpec("engine.data.adapters.eia", "EiaAdapter", "fetch_spot", "eia", True),
    "eia_futures": AdapterSpec("engine.data.adapters.eia", "EiaAdapter", "fetch_futures_hist", "eia", True),
    "eia_wpsr": AdapterSpec("engine.data.adapters.eia", "EiaAdapter", "fetch_wpsr", "eia", True),
    "eia_steo": AdapterSpec("engine.data.adapters.eia", "EiaAdapter", "fetch_steo_brent", "eia", True),
    "eia_xls": AdapterSpec("engine.data.adapters.eia", "EiaFallbackAdapter", "fetch_spot_xls"),
    "eia_wpsr_csv": AdapterSpec("engine.data.adapters.eia", "EiaFallbackAdapter", "fetch_wpsr_table1"),
    "fred": AdapterSpec("engine.data.adapters.fred", "FredAdapter", "fetch_series", "fred", True),
    "fred_csv": AdapterSpec("engine.data.adapters.fred", "FredCsvAdapter", "fetch_series"),
    "cftc_socrata": AdapterSpec("engine.data.adapters.cot", "CftcAdapter", "fetch"),
    "cftc_file": AdapterSpec("engine.data.adapters.cot", "CftcFileAdapter", "fetch_current"),
    "ice_cot": AdapterSpec("engine.data.adapters.cot", "IceCotAdapter", "fetch"),
    "baker_hughes": AdapterSpec("engine.data.adapters.rigs", "BakerHughesAdapter", "fetch"),
    "gpr": AdapterSpec("engine.data.adapters.news", "GprAdapter", "fetch"),
    "gdelt_files": AdapterSpec("engine.data.adapters.news", "GdeltFilesAdapter", "fetch_latest"),
    "gdelt_doc_api": AdapterSpec("engine.data.adapters.news", "GdeltDocApiAdapter", "fetch_timeline"),
    "yahoo_contracts": AdapterSpec(
        "engine.data.adapters.yahoo", "YahooAdapter", "fetch_contracts", ctor_kwargs=YAHOO_KWARGS
    ),
    "yahoo_intraday_contracts": AdapterSpec(
        "engine.data.adapters.yahoo", "YahooAdapter", "fetch_intraday_contracts", ctor_kwargs=YAHOO_KWARGS
    ),
    "portwatch": AdapterSpec("engine.data.adapters.portwatch", "PortWatchAdapter", "fetch"),
    "cboe_chain": AdapterSpec("engine.data.adapters.cboe", "CboeAdapter", "fetch_chain"),
    # S21 shipped with its adapter never registered here: the entry answered "adapter non registrato", went RED,
    # and one red source was enough to halt the whole account. Registered now, and optional like every key.
    "alphavantage_insider": AdapterSpec(
        "engine.data.adapters.insider", "InsiderAdapter", "fetch", "alphavantage", True
    ),
}

# freshness tolerance per entry (warning beyond it, error beyond twice it). EIA refreshes its daily spot series
# weekly and the WPSR/COT/rig releases are weekly, hence the generous values.
FRESHNESS: dict[str, timedelta] = {
    "brent_spot": timedelta(days=10),
    "wti_spot": timedelta(days=10),
    "brent_front": timedelta(days=4),
    "wti_front": timedelta(days=4),
    "rbob_front": timedelta(days=4),
    "ho_front": timedelta(days=4),
    "brent_curve": timedelta(days=4),
    "wti_curve": timedelta(days=4),
    "wti_curve_hist": timedelta(days=365 * 5),  # the NYMEX series ended 2024-04-05: historical proxy only
    "ovx": timedelta(days=5),
    "vix": timedelta(days=5),
    "dxy": timedelta(days=6),
    "spx": timedelta(days=4),
    "copper": timedelta(days=4),
    "us10y": timedelta(days=6),
    "breakeven10y": timedelta(days=6),
    "wpsr": timedelta(days=12),
    "steo_brent": timedelta(days=60),
    "rig_count": timedelta(days=12),
    "cot_wti": timedelta(days=12),
    "cot_brent": timedelta(days=12),
    "cot_gasoil": timedelta(days=12),
    "gpr": timedelta(days=6),
    "gdelt_events": timedelta(hours=6),
    INTRADAY_KEY: timedelta(hours=36),
    "bno_daily": timedelta(days=4),
    "uso_daily": timedelta(days=4),
    "sco_daily": timedelta(days=4),
    "copper_daily": timedelta(days=4),
    "dxy_daily": timedelta(days=4),
    "hormuz": timedelta(days=12),  # published weekly, a week at a time
    "bab_el_mandeb": timedelta(days=12),
    "insider_form4": timedelta(days=45),
}
# Tables whose index is not unique BY DESIGN (several insider transactions on one day; one row per contract and
# day): the duplicate-index check would flag every one of them.
NON_UNIQUE_INDEX = frozenset(
    {"insider_form4", "cl_contracts", "bz_contracts", "cl_intraday", "bz_intraday", "bno_options", "uso_options"}
)
# Tables where a "price" of zero is information, not an error: three quarters of the Form 4 rows are grants,
# vestings and option exercises, which the filing itself prints at a price of 0.
NO_PRICE_CHECK = frozenset({"insider_form4"})
# Intraday tables: the gap between two sessions is not an outlier and a weekend is not a missing business day,
# so the checks written for daily series (return outliers, calendar gaps) do not apply to them.
INTRADAY_ENTRIES = frozenset({"bno_intraday", "sco_intraday", "cl_intraday", "bz_intraday"})
# Per-contract tables are CUMULATIVE: Yahoo forgets a contract the day it expires, so each new download is merged
# with what was already archived. Without this the history of every contract the book ever held would vanish.
CUMULATIVE_CONTRACTS = frozenset({"cl_contracts", "bz_contracts"})
CONTRACT_KEYS = {"cl_contracts": ["date", "code"], "bz_contracts": ["date", "code"]}
# Sources whose failure must stop new risk. Everything else degrades the features that read it (a strategy
# that cannot see its input stays silent) but never the account: an optional alternative-data table going red
# is what kept this account flat from 2026-10-07.
DEFAULT_CRITICAL = frozenset({"brent_front", "wti_front", "bno_daily"})
DEFAULT_FRESHNESS = timedelta(days=10)
# columns that must be strictly positive. Only the TAIL is checked: WTI really settled at -37.63 $ on
# 2020-04-20 and that historical print must not turn the source red forever - what matters for new risk is
# whether the CURRENT quotes make sense.
PRICE_LIKE = ("value", "close", "M1", "settle", "price")
NONPOSITIVE_TAIL_ROWS = 30
# the outlier and business-day-gap checks also run on the recent tail only: a 19-year history always contains
# some exchange closure the UK/US calendars do not know, and a 2008 outlier says nothing about today's feed.
RECENT_TAIL_ROWS = 60
OUTLIER_Z = 6.0
MAX_BUSINESS_GAP = 3


@dataclass
class EntryResult:
    """Outcome of one YAML block (one logical table) after its fallback chain ran."""

    group: str
    entry: str
    health: SourceHealth
    result: FetchResult | None = None
    path: str | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None

    def to_dict(self) -> dict[str, Any]:
        d = self.health.to_dict() if hasattr(self.health, "to_dict") else dict(self.health.__dict__)
        d.update({"group": self.group, "entry": self.entry, "path": self.path, "attempts": self.attempts})
        return d


def _asof_of(frame: pd.DataFrame) -> datetime | None:
    """Newest observation timestamp of a fetched frame.

    The index is the natural answer for a time-indexed table; the curve snapshots are indexed by CONTRACT CODE
    (``BZZ26``), so ``published_at`` is used there. Anything unparseable returns None rather than raising: this
    is metadata for the health report, never a trading input.
    """
    if frame.empty:
        return None
    for candidate in (frame.index, frame.get("published_at")):
        if candidate is None or not pd.api.types.is_datetime64_any_dtype(candidate):
            continue  # a non-temporal index is normal (the curve is indexed by contract code)
        try:
            idx = pd.DatetimeIndex(pd.to_datetime(pd.Index(candidate), errors="raise", utc=True))
        except Exception:  # an unparseable timestamp is metadata we can live without
            continue
        ts = pd.Timestamp(idx.max())
        if pd.isna(ts):
            continue
        return ts.tz_convert(UTC).to_pydatetime() if ts.tzinfo else ts.tz_localize(UTC).to_pydatetime()
    return None


def quality_issues(entry: str, frame: pd.DataFrame, now: datetime) -> list[QualityIssue]:
    """Generic checks every fetched table goes through (staleness, duplicates, non-positive prices)."""
    issues: list[QualityIssue] = []
    max_age = FRESHNESS.get(entry, DEFAULT_FRESHNESS)
    index = pd.Index(frame.index)
    if isinstance(index, pd.DatetimeIndex) or pd.api.types.is_datetime64_any_dtype(index):
        probe = frame.iloc[:, 0] if frame.shape[1] else pd.Series(dtype="float64", index=index)
        issues += check_stale(pd.Series(probe.to_numpy(), index=index), now, max_age, entry, "index")
    if entry not in NON_UNIQUE_INDEX:
        issues += check_duplicates(index, entry)
    tail = frame.tail(NONPOSITIVE_TAIL_ROWS)
    recent = frame.tail(RECENT_TAIL_ROWS)
    daily = (
        isinstance(index, pd.DatetimeIndex)
        and len(index) > 5
        and str(index.dtype).startswith("datetime")
        and entry not in NON_UNIQUE_INDEX
        and entry not in INTRADAY_ENTRIES
    )
    for col in frame.columns:
        if str(col) not in PRICE_LIKE or entry in NO_PRICE_CHECK:
            continue
        issues += check_nonpositive(tail[col], entry, str(col))
        if daily:
            values = pd.to_numeric(recent[col], errors="coerce").astype("float64")
            issues += check_outliers(values.diff() / values.shift(1), OUTLIER_Z, 30, entry, f"{col} (ret)")
    if daily and entry not in {"wpsr", "rig_count", "cot_wti", "cot_brent", "cot_gasoil", "steo_brent"}:
        issues += check_gaps(pd.DatetimeIndex(recent.index), MAX_BUSINESS_GAP, entry, "index")
    return issues


def overall_from_sources(sources: list[dict[str, Any]], critical: frozenset[str]) -> str:
    """RED only when a CRITICAL source is red; any other problem is YELLOW.

    The old rule (worst status of anything) let a missing optional key halt trading. A feature whose source is
    down goes NaN and the strategy that needs it stays silent, which is the degradation the brief asks for; the
    account itself stops only when it cannot see a price.
    """
    red_critical = any(str(s.get("status")) == "red" and str(s.get("source")) in critical for s in sources)
    if red_critical or not sources:
        return "red"
    return "green" if all(str(s.get("status")) == "green" for s in sources) else "yellow"


class Fetcher:
    """Runs every data source of ``config/data_sources.yaml`` and reports health."""

    def __init__(self, settings: Settings, store: RawStore, state: StateStore | None = None):
        self.settings = settings
        self.store = store
        self.state = state
        self.config: dict[str, Any] = load_config("data_sources", settings.config_dir)
        self._instances: dict[tuple[str, str], Any] = {}

    # ---- adapter plumbing -------------------------------------------------------------------------------------
    def _instance(self, name: str, spec: AdapterSpec) -> Any:
        """Instantiate (and cache) an adapter class. Cached per (class, key) so pacing is shared across calls."""
        cache_key = (spec.module, spec.cls)
        if cache_key in self._instances:
            return self._instances[cache_key]
        module = importlib.import_module(spec.module)  # ImportError -> caught by the caller
        cls = getattr(module, spec.cls, None)
        if cls is None:
            raise DataUnavailable(f"adapter {name!r}: {spec.module} has no class {spec.cls}")
        kwargs: dict[str, Any] = dict(spec.ctor_kwargs)
        if spec.ctor_takes_key:
            kwargs["api_key"] = {
                "eia": self.settings.eia_api_key,
                "fred": self.settings.fred_api_key,
                "alphavantage": self.settings.alphavantage_api_key,
            }.get(str(spec.key))
        instance = cls(**kwargs)
        self._instances[cache_key] = instance
        return instance

    @staticmethod
    def _call_kwargs(name: str, entry: dict[str, Any], asof: datetime) -> tuple[str, dict[str, Any]]:
        """Entry from the YAML -> (method name, call kwargs) for the adapter named ``name``."""
        series = entry.get("series")
        if name == "yahoo":
            return "fetch_daily", {"symbol": str(entry["symbol"])}
        if name == "yahoo_curve":
            return "fetch_curve", {
                "root": str(entry.get("root", "BZ")),
                "asof": asof.date(),
                "n_months": int(entry.get("months", 12)),
            }
        if name == "yahoo_intraday":
            return "fetch_intraday", {
                "symbol": str(entry.get("symbol", INTRADAY_SYMBOL)),
                "interval": str(entry.get("interval", INTRADAY_INTERVAL)),
                "lookback_days": int(entry.get("lookback_days", INTRADAY_LOOKBACK_DAYS)),
            }
        if name == "yahoo_contracts":
            return "fetch_contracts", {
                "root": str(entry.get("root", "CL")),
                "asof": asof.date(),
                "n_months": int(entry.get("months", 14)),
                "far_decembers": int(entry.get("far_decembers", 3)),
            }
        if name == "yahoo_intraday_contracts":
            return "fetch_intraday_contracts", {
                "root": str(entry.get("root", "CL")),
                "asof": asof.date(),
                "n": int(entry.get("contracts", 2)),
                "interval": str(entry.get("interval", "30m")),
                "lookback_days": int(entry.get("lookback_days", 10)),
            }
        if name == "portwatch":
            return "fetch", {"chokepoint": str(entry.get("chokepoint", "Strait of Hormuz"))}
        if name == "cboe_chain":
            return "fetch_chain", {"symbol": str(entry.get("symbol", "BNO"))}
        if name == "alphavantage_insider":
            return "fetch", {}
        if name == "eia":
            if isinstance(series, list):  # RCLC1..RCLC4: the discontinued NYMEX futures proxy
                return "fetch_futures_hist", {}
            return "fetch_spot", {"series": str(series or "RBRTE")}
        if name in {"eia_wpsr", "eia_steo"}:
            return ADAPTERS[name].method, {}
        if name == "eia_xls":
            return "fetch_spot_xls", ({"url": str(entry["url"])} if entry.get("url") else {})
        if name == "eia_wpsr_csv":
            return "fetch_wpsr_table1", ({"url": str(entry["url"])} if entry.get("url") else {})
        if name in {"fred", "fred_csv"}:
            return ADAPTERS[name].method, {"series_id": str(series)}
        if name == "cftc_socrata":
            return "fetch", {"market_code": str(entry.get("market_code", "067651"))}
        if name == "cftc_file":
            return "fetch_current", {"market_code": str(entry.get("market_code", "067651"))}
        if name == "ice_cot":
            kwargs: dict[str, Any] = {"market_name": str(entry.get("market", "brent"))}
            if entry.get("first_year"):
                kwargs["first_year"] = int(entry["first_year"])
            return "fetch", kwargs
        if name == "baker_hughes":
            return "fetch", ({"url": str(entry["url"])} if entry.get("url") else {})
        if name == "gpr":
            return "fetch", ({"url": str(entry["url"])} if entry.get("url") else {})
        if name == "gdelt_files":
            return "fetch_latest", {}
        if name == "gdelt_doc_api":
            return "fetch_timeline", {
                "query": str(entry.get("query", "(oil OR hormuz OR opec) sourcelang:english")),
                "mode": str(entry.get("mode", "timelinevol")),
                "timespan": str(entry.get("timespan", "3months")),
            }
        return ADAPTERS[name].method, {}

    def _missing_key(self, entry: dict[str, Any], spec: AdapterSpec) -> str | None:
        if not entry.get("needs_key"):
            return None
        if spec.key == "eia" and not self.settings.eia_api_key:
            return "EIA_API_KEY"
        if spec.key == "fred" and not self.settings.fred_api_key:
            return "FRED_API_KEY"
        if spec.key == "alphavantage" and not self.settings.alphavantage_api_key:
            return "ALPHAVANTAGE_API_KEY"
        return None

    # ---- one entry --------------------------------------------------------------------------------------------
    def run_entry(self, group: str, entry_name: str, chain: list[dict[str, Any]], asof: datetime) -> EntryResult:
        """Run the fallback chain of one YAML block and return its outcome (never raises)."""
        attempts: list[dict[str, Any]] = []
        skipped_for_key: list[str] = []
        for position, raw_entry in enumerate(chain):
            cfg = dict(raw_entry)
            name = str(cfg.get("adapter", ""))
            spec = ADAPTERS.get(name)
            if spec is None:
                attempts.append({"adapter": name, "status": "unknown", "message": "adapter non registrato"})
                continue
            missing = self._missing_key(cfg, spec)
            if missing:
                attempts.append({"adapter": name, "status": "skipped", "message": f"{missing} assente"})
                skipped_for_key.append(f"{name} ({missing})")
                continue
            started = time.monotonic()
            try:
                instance = self._instance(name, spec)
                method_name, kwargs = self._call_kwargs(name, cfg, asof)
                method = getattr(instance, method_name, None)
                if method is None:
                    raise DataUnavailable(f"adapter {name!r}: {spec.cls} has no method {method_name}")
                result = method(**kwargs)
            except Exception as e:
                latency = int((time.monotonic() - started) * 1000)
                attempts.append(
                    {"adapter": name, "status": "failed", "message": f"{type(e).__name__}: {e}"[:400], "ms": latency}
                )
                log.warning("%s/%s: adapter %s failed: %s", group, entry_name, name, e)
                continue
            latency = int((time.monotonic() - started) * 1000)
            if result.frame is None or result.frame.empty:
                attempts.append({"adapter": name, "status": "empty", "message": "nessuna riga", "ms": latency})
                continue
            if entry_name in CUMULATIVE_CONTRACTS:
                result.frame = self._merge_cumulative(entry_name, result)
            if entry_name == "insider_form4":
                result.frame = self._merge_by_ticker(entry_name, result)
            attempts.append({"adapter": name, "status": "ok", "rows": len(result.frame), "ms": latency})
            fallback = None if position == 0 else result.source
            path = self.store.save(entry_name, result.source, result)
            issues = quality_issues(entry_name, result.frame, asof)
            health = health_from_issues(
                entry_name,
                issues,
                checked_at=asof,
                data_asof=_asof_of(result.frame),
                fallback_used=fallback,
                available=True,
                rows=len(result.frame),
                latency_ms=latency,
                message=f"adapter {result.source}" + (" (approx)" if result.approx else ""),
                last_success_at=result.fetched_at,
            )
            return EntryResult(group, entry_name, health, result, str(path), attempts)
        # nothing worked
        only_keys = bool(skipped_for_key) and not any(a["status"] in {"failed", "empty"} for a in attempts)
        message = (
            f"chiave assente: {', '.join(skipped_for_key)}"
            if only_keys
            else "; ".join(f"{a['adapter']}: {a.get('message', a['status'])}" for a in attempts)[:600]
        )
        health = SourceHealth(
            source=entry_name,
            status=Health.YELLOW if only_keys else Health.RED,
            checked_at=asof,
            message=message or "nessun adapter configurato",
        )
        return EntryResult(group, entry_name, health, None, None, attempts)

    def _merge_cumulative(self, entry_name: str, result: FetchResult) -> pd.DataFrame:
        """New per-contract rows on top of the archived ones (new wins on the same contract and day)."""
        previous = self.store.load_latest(entry_name, result.source)
        fresh = result.frame
        if previous is None or previous.empty:
            return fresh
        keys = CONTRACT_KEYS[entry_name]
        old = previous[[c for c in fresh.columns if c in previous.columns]]
        if not set(keys) <= set(old.columns):
            return fresh
        merged = pd.concat([old, fresh], ignore_index=True)
        merged = merged.drop_duplicates(subset=keys, keep="last").sort_values([keys[1], keys[0]], kind="stable")
        return merged.reset_index(drop=True)

    def _merge_by_ticker(self, entry_name: str, result: FetchResult) -> pd.DataFrame:
        """Fresh rows for the tickers that answered, archived rows for the ones that did not.

        The vendor throttles per call, so a download can return eight of twelve names. Scoring "how many
        insiders bought" on eight names one week and twelve the next would move the score for a reason that
        has nothing to do with insiders; the missing names keep what was last known about them instead.
        """
        fresh = result.frame
        previous = self.store.load_latest(entry_name, result.source)
        if previous is None or previous.empty or "ticker" not in previous.columns or "ticker" not in fresh.columns:
            return fresh
        kept = previous[~previous["ticker"].isin(set(fresh["ticker"].unique()))]
        if kept.empty:
            return fresh
        kept = kept[[c for c in fresh.columns if c in kept.columns]]
        return pd.concat([kept, fresh]).sort_index(kind="stable")

    def critical_sources(self) -> frozenset[str]:
        listed = self.config.get("critical_sources")
        if isinstance(listed, list) and listed:
            return frozenset(str(x) for x in listed)
        return DEFAULT_CRITICAL

    # ---- built-in steps ---------------------------------------------------------------------------------------
    def accumulate_gdelt(self, asof: datetime) -> EntryResult:
        """``gdelt_accumulate``: append the latest 15-minute slot to the raw table, refresh the daily aggregate."""
        entry_name = "gdelt_events"
        chain = [dict(e) for e in self._chain("news", entry_name)]
        outcome = self.run_entry("news", entry_name, chain, asof)
        if outcome.result is None:
            return outcome
        from engine.data.adapters.news import aggregate_rows  # lazy: keeps the import graph light

        new_rows = outcome.result.frame
        previous = self.store.load_latest(GDELT_SLOT_KEY, outcome.result.source)
        frames = [f for f in (previous, new_rows) if f is not None and not f.empty]
        combined = pd.concat(frames) if len(frames) > 1 else frames[0]
        combined = combined[[c for c in combined.columns if c not in {"observed_at", "source"}]]
        combined.index = pd.DatetimeIndex(combined.index, name="slot")
        combined = combined[~combined.index.duplicated(keep="last")].sort_index().iloc[-MAX_ACCUMULATED_SLOTS:]
        slots = FetchResult(
            source=outcome.result.source,
            frame=combined,
            fetched_at=outcome.result.fetched_at,
            meta={"slots": len(combined), "step": "gdelt_accumulate"},
        )
        self.store.save(GDELT_SLOT_KEY, outcome.result.source, slots)
        daily = aggregate_rows(combined)
        self.store.save(
            GDELT_DAILY_KEY,
            outcome.result.source,
            FetchResult(
                source=outcome.result.source,
                frame=daily,
                fetched_at=outcome.result.fetched_at,
                meta={"days": len(daily), "slots": len(combined), "step": "gdelt_accumulate"},
            ),
        )
        outcome.attempts.append(
            {"adapter": "gdelt_accumulate", "status": "ok", "slots": len(combined), "days": len(daily)}
        )
        outcome.health.message = f"{outcome.health.message} | slot accumulati {len(combined)}, giorni {len(daily)}"
        return outcome

    def fetch_intraday(self, asof: datetime) -> EntryResult:
        """``brent_intraday``: 1h bars of the Brent front (HAR-RV input, Yahoo's 730-day limit)."""
        chain = [{"adapter": "yahoo_intraday", "symbol": front_contract_symbol(asof), "interval": INTRADAY_INTERVAL}]
        return self.run_entry("prices", INTRADAY_KEY, chain, asof)

    # ---- the whole run ----------------------------------------------------------------------------------------
    def _chain(self, group: str, entry: str) -> list[dict[str, Any]]:
        block = self.config.get(group) or {}
        chain = block.get(entry) or []
        return [dict(e) for e in chain if isinstance(e, dict)]

    def groups(self) -> list[str]:
        return [str(g) for g, block in self.config.items() if isinstance(block, dict)]

    def run_all(self, groups: list[str] | None = None, entries: set[str] | None = None) -> dict[str, Any]:
        """Run every group (or the ones named), archive snapshots, write ``health.json``.

        ``entries`` adds single tables by name, from whichever group defines them, without running the rest of
        that group: a caller that needs three tables of a group of sixty requests asks for those three. With
        ``entries`` given and no ``groups``, only those tables are downloaded.

        Returns ``{"checked_at", "overall", "groups": {group: {entry: {...}}}, "sources": [health...]}``.
        """
        asof = utc_now()
        named = set(entries or ())
        full = list(groups or ([] if entries is not None else self.groups()))
        unknown = [g for g in full if g not in self.config]
        wanted = [g for g in full if g in self.config]
        wanted += [g for g in self.groups() if g not in wanted and named & {str(e) for e in self.config.get(g) or {}}]
        outcomes: list[EntryResult] = []
        report: dict[str, Any] = {"checked_at": asof, "groups": {}, "unknown_groups": unknown}
        for group in wanted:
            block = self.config.get(group) or {}
            whole = group in full
            group_report: dict[str, Any] = {}
            for entry_name in block:
                if not whole and str(entry_name) not in named:
                    continue
                try:
                    if group == "news" and entry_name == "gdelt_events":
                        outcome = self.accumulate_gdelt(asof)
                    else:
                        outcome = self.run_entry(group, str(entry_name), self._chain(group, str(entry_name)), asof)
                except Exception as e:
                    log.exception("%s/%s crashed", group, entry_name)
                    outcome = EntryResult(
                        group,
                        str(entry_name),
                        SourceHealth(
                            source=str(entry_name),
                            status=Health.RED,
                            checked_at=asof,
                            message=f"errore interno: {type(e).__name__}: {e}"[:400],
                        ),
                    )
                outcomes.append(outcome)
                group_report[str(entry_name)] = outcome.to_dict()
            if group == "prices" and whole:
                intraday = self.fetch_intraday(asof)
                outcomes.append(intraday)
                group_report[INTRADAY_KEY] = intraday.to_dict()
            report["groups"][group] = group_report
        healths = [o.health for o in outcomes]
        report["sources"] = [h.to_dict() if hasattr(h, "to_dict") else dict(h.__dict__) for h in healths]
        report["overall_strict"] = str(overall_health(healths))
        report["overall"] = overall_from_sources(report["sources"], self.critical_sources())
        report["ok"] = sum(1 for o in outcomes if o.ok)
        report["failed"] = sum(1 for o in outcomes if not o.ok)
        self.write_health(report)
        return report

    def write_health(self, report: dict[str, Any]) -> None:
        """Persist ``state/health.json`` (``checked_at``, ``sources``, ``overall``).

        A group-filtered run MERGES its sources into the existing file instead of truncating it: the intraday
        job fetches prices only, and the dashboard must still see the status of the weekly sources it did not
        touch. Entries this run checked replace the old ones; untouched entries keep their previous record (and
        their older ``checked_at``), and ``overall`` is recomputed over the merged set.
        """
        if self.state is None:
            return
        fresh: list[dict[str, Any]] = list(report["sources"])
        names = {str(s.get("source")) for s in fresh}
        previous = self.state.read_json(HEALTH_FILE) or {}
        # a failed download does not erase the memory of the last good one: "red since when" needs it
        last_ok = {str(s.get("source")): s.get("last_success_at") for s in previous.get("sources", [])}
        for record in fresh:
            if not record.get("last_success_at") and last_ok.get(str(record.get("source"))):
                record["last_success_at"] = last_ok[str(record.get("source"))]
        kept = [s for s in previous.get("sources", []) if str(s.get("source")) not in names]
        merged = sorted([*fresh, *kept], key=lambda s: str(s.get("source")))
        critical = self.critical_sources()
        overall = overall_from_sources(merged, critical)
        self.state.write_json(
            HEALTH_FILE,
            {
                "checked_at": report["checked_at"],
                "sources": merged,
                "overall": overall,
                "critical": sorted(critical),
                "groups_checked": sorted(report["groups"]),
            },
        )
        report["overall_merged"] = overall
