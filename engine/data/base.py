"""Data-layer contracts: adapters, fetch results, source health, HTTP client with retries.

Every adapter returns a FetchResult whose frame carries two time notions:
  * the observation period (index, as a date or timestamp of the market datum), and
  * `published_at` (UTC): the earliest moment the datum was knowable. Features filter on published_at.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

import pandas as pd
import requests

from engine.core.errors import DataUnavailable
from engine.core.events import Record

log = logging.getLogger(__name__)

USER_AGENT = "oil-paper-trading/0.1 (research; +https://github.com/andreramolivaz/oil-paper-trading)"


class Health(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"  # degraded: fallback used, or data older than expected but usable
    RED = "red"  # unavailable or stale beyond tolerance: no new risk


@dataclass
class SourceHealth(Record):
    source: str
    status: Health
    checked_at: datetime
    last_success_at: datetime | None = None
    data_asof: datetime | None = None  # timestamp of the newest datum
    latency_ms: int | None = None
    message: str = ""
    fallback_used: str | None = None
    rows: int = 0


@dataclass
class FetchResult:
    """Normalised output of an adapter."""

    source: str  # adapter name actually used (e.g. "eia", "fred_csv")
    frame: pd.DataFrame  # index: pd.DatetimeIndex (UTC or date-like); must include column `published_at`
    fetched_at: datetime
    meta: dict[str, Any] = field(default_factory=dict)
    approx: bool = False

    @property
    def asof(self) -> datetime | None:
        if self.frame.empty:
            return None
        idx = self.frame.index.max()
        ts = pd.Timestamp(idx)
        if ts.tzinfo is None:
            ts = ts.tz_localize(UTC)
        return ts.to_pydatetime()


class Adapter(Protocol):
    name: str

    def fetch(self, **kwargs: Any) -> FetchResult: ...


class HttpClient:
    """Thin requests wrapper: timeouts, retries with exponential backoff, rate-limit awareness."""

    def __init__(self, timeout: float = 30.0, retries: int = 3, backoff: float = 1.5, min_interval: float = 0.0):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.min_interval = min_interval
        self._last_call = 0.0

    def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_call = time.monotonic()
                r = self.session.get(url, params=params, timeout=self.timeout, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    raise DataUnavailable(f"{url} -> HTTP {r.status_code}")
                r.raise_for_status()
                return r
            except (requests.RequestException, DataUnavailable) as e:
                last_exc = e
                sleep = self.backoff ** (attempt + 1)
                log.warning("GET %s failed (%s); retry in %.1fs", url, e, sleep)
                if attempt < self.retries:
                    time.sleep(sleep)
        raise DataUnavailable(f"GET {url} failed after {self.retries + 1} attempts: {last_exc}")

    def get_json(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        return self.get(url, params=params, **kw).json()

    def get_bytes(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> bytes:
        return self.get(url, params=params, **kw).content


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def ensure_published_at(frame: pd.DataFrame, default_lag: pd.Timedelta | None = None) -> pd.DataFrame:
    """Guarantee a `published_at` column (UTC). Default: index + lag (daily settlement known same day 23:00 UTC)."""
    if "published_at" in frame.columns:
        return frame
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is None:
        idx = idx.tz_localize(UTC)
    lag = default_lag if default_lag is not None else pd.Timedelta(hours=23)
    out = frame.copy()
    out["published_at"] = idx + lag
    return out
