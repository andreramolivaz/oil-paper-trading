"""Corporate-insider transactions (SEC Form 4) for the oil complex.

Source: Alpha Vantage ``INSIDER_TRANSACTIONS`` (``https://www.alphavantage.co/query?function=
INSIDER_TRANSACTIONS&symbol=<ticker>``), which republishes SEC Form 4 filings. Verified from this container on
2026-10-05: HTTP 200, ConocoPhillips returned 2 951 rows covering 2008-2026, Occidental 1 637 rows.

**This is public, legally disclosed data.** A Form 4 is the filing an officer, director or 10% owner must make
when they trade their own company's stock. Nothing here is non-public information and nothing here is a
recommendation; the thesis is simply that people who run oil production have an informed view of its economics.

Three properties of the real data decide the whole design, and each was measured, not assumed:

1. **There is no filing date in the feed.** Every row carries ``transaction_date`` only. A Form 4 is due within
   **two business days** of the transaction (17 CFR 240.16a-3(g)), so treating the transaction date as the
   moment the market learned of it would be look-ahead - the exact failure ``tests/test_no_lookahead.py``
   exists to catch. ``published_at`` is therefore ``transaction_date + 2 business days`` at 22:00 UTC, which is
   the LATEST the filing can legally appear. Real filings are often same-day, so this is conservative: the
   engine never sees a transaction earlier than it could have. It is an inference, not an observation, so the
   adapter reports ``approx=True`` and the dashboard labels the feature with "≈".
2. **Three quarters of the rows are not trades.** On ConocoPhillips only 729 of 2 951 rows (25%) are
   open-market transactions in common stock at a real price. The rest are grants, vesting, option exercises,
   phantom stock and tax withholding, which carry no view: they are compensation mechanics, and most of them
   print ``share_price = 0``. January director awards of exactly 2 500 shares at price 0, repeated every year,
   are the clearest example.
3. **A dollar-weighted score would be one investor.** On Occidental 146 of the 258 open-market purchases are by
   a "10% Owner" with a median size of 17.2 M$ and a maximum of 564 M$ - Berkshire Hathaway accumulating a
   stake. That is a view on an equity, not on crude. 10% owners are tagged and excluded from the score by
   default; officers and directors are what the thesis is about.

What is left after (2) and (3) is sparse: roughly 9 open-market purchases per name per year. This is a
**weekly** factor and the `weekly` job refreshes it. Anyone calling it real-time is describing something the
data cannot support - the statutory filing window alone is two business days.

Frame contract: index ``transaction_date`` (tz-naive midnight), columns ``ticker``, ``executive``, ``role``,
``security_type``, ``side`` (``A`` acquisition / ``D`` disposal), ``shares``, ``price``, ``value_usd``,
``open_market`` (bool), ``ten_percent_owner`` (bool) and ``published_at``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC
from datetime import time as dtime
from typing import Any

import pandas as pd

from engine.core.errors import DataUnavailable
from engine.data.base import FetchResult, HttpClient, utc_now

log = logging.getLogger(__name__)

API_URL = "https://www.alphavantage.co/query"
FUNCTION = "INSIDER_TRANSACTIONS"

#: Statutory Form 4 deadline: two business days after the transaction (17 CFR 240.16a-3(g)).
FILING_LAG_BUSINESS_DAYS = 2
#: EDGAR accepts filings until 22:00 Eastern; 22:00 UTC is earlier than that, so it never claims a filing
#: was visible before it could have been.
PUBLICATION_TIME_UTC = dtime(22, 0)
PUBLISHED_AT_RULE = "transaction_date + 2 business days at 22:00 UTC (SEC Form 4 statutory deadline)"

COLUMNS = [
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

#: Crude-LONG names only. A refiner's insiders profit from CHEAP crude, so folding VLO/MPC/PSX into the same
#: score would cancel the signal it is meant to carry. Services sit in between but their order books track
#: upstream capex, which tracks the forward curve.
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "XOM",
    "CVX",
    "COP",
    "EOG",
    "OXY",
    "DVN",
    "FANG",
    "APA",
    "HES",
    "SLB",
    "HAL",
    "BKR",
)


def _as_float(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if pd.notna(f) else 0.0


def parse_insider_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Pure parser: raw vendor rows -> the frame contract. No value is ever invented or interpolated.

    A row is ``open_market`` when it is common stock AND carries a real price. Everything else is
    compensation mechanics (grants, vesting, option exercises, tax withholding) and is kept in the frame -
    so the quality checks can see it - but flagged so the score can ignore it.
    """
    records: list[dict[str, Any]] = []
    for raw in rows:
        day = str(raw.get("transaction_date") or "").strip()
        if len(day) < 10:
            continue
        ts = pd.to_datetime(day, errors="coerce", utc=False)
        if pd.isna(ts):
            continue
        shares = _as_float(raw.get("shares"))
        price = _as_float(raw.get("share_price"))
        security = str(raw.get("security_type") or "")
        role = str(raw.get("executive_title") or "")
        side = str(raw.get("acquisition_or_disposal") or "").strip().upper()
        if side not in {"A", "D"} or shares <= 0:
            continue
        common = "common stock" in security.lower()
        records.append(
            {
                "transaction_date": pd.Timestamp(ts).normalize(),
                "ticker": str(raw.get("ticker") or "").strip().upper(),
                "executive": str(raw.get("executive") or "").strip(),
                "role": role,
                "security_type": security,
                "side": side,
                "shares": shares,
                "price": price,
                "value_usd": shares * price,
                "open_market": bool(common and price > 0.0),
                "ten_percent_owner": "10% owner" in role.lower(),
            }
        )
    if not records:
        return pd.DataFrame(columns=[*COLUMNS, "published_at"]).rename_axis("transaction_date")
    frame = pd.DataFrame.from_records(records).set_index("transaction_date").sort_index()
    frame["published_at"] = _published_at(pd.DatetimeIndex(frame.index))
    return frame[[*COLUMNS, "published_at"]]


def _published_at(index: pd.DatetimeIndex) -> pd.Series:
    """The earliest instant the engine is allowed to know about a transaction."""
    days = pd.DatetimeIndex(index).normalize()
    filed = days + pd.tseries.offsets.BusinessDay(FILING_LAG_BUSINESS_DAYS)
    stamped = filed + pd.Timedelta(hours=PUBLICATION_TIME_UTC.hour, minutes=PUBLICATION_TIME_UTC.minute)
    return pd.Series(stamped.tz_localize(UTC), index=index)


class InsiderAdapter:
    """Fetches Form 4 transactions for a universe of oil names.

    The API key is optional, exactly like ``EIA_API_KEY`` and ``FRED_API_KEY``: without it the adapter raises
    :class:`DataUnavailable` and the Fetcher degrades to yellow. It never falls back to invented data.
    """

    name = "insider_form4"

    def __init__(
        self,
        client: HttpClient | None = None,
        api_key: str | None = None,
        universe: tuple[str, ...] = DEFAULT_UNIVERSE,
    ) -> None:
        self.client = client or HttpClient()
        self.api_key = api_key
        self.universe = universe

    def fetch(self, start: str | None = None) -> FetchResult:
        if not self.api_key:
            raise DataUnavailable("chiave assente: alphavantage (ALPHAVANTAGE_API_KEY)")
        frames: list[pd.DataFrame] = []
        failed: list[str] = []
        for ticker in self.universe:
            try:
                rows = self._fetch_one(ticker, start)
            except DataUnavailable as exc:
                log.warning("insider: %s non disponibile (%s)", ticker, exc)
                failed.append(ticker)
                continue
            if rows:
                frames.append(parse_insider_rows(rows))
        if not frames:
            raise DataUnavailable(f"nessun ticker disponibile ({len(failed)} falliti)")
        frame = pd.concat(frames).sort_index()
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={
                "universe": list(self.universe),
                "failed": failed,
                "published_at_rule": PUBLISHED_AT_RULE,
                "n_open_market": int(frame["open_market"].sum()),
                "n_rows": len(frame),
            },
            # published_at is INFERRED from the statutory deadline, never observed: that makes every row an
            # approximation and the whole series must be labelled as one.
            approx=True,
        )

    def _fetch_one(self, ticker: str, start: str | None) -> list[dict[str, Any]]:
        params = {"function": FUNCTION, "symbol": ticker, "apikey": self.api_key}
        if start:
            params["from_date"] = start
        try:
            payload = self.client.get_json(API_URL, params=params)
        except Exception as exc:
            raise DataUnavailable(f"alphavantage {ticker}: {exc}") from exc
        if not isinstance(payload, dict):
            raise DataUnavailable(f"alphavantage {ticker}: risposta inattesa")
        # The vendor signals both throttling and plan limits in prose fields rather than HTTP status codes.
        for key in ("Note", "Information", "Error Message"):
            if key in payload:
                raise DataUnavailable(f"alphavantage {ticker}: {str(payload[key])[:140]}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise DataUnavailable(f"alphavantage {ticker}: campo 'data' assente")
        return [row for row in data if isinstance(row, dict)]


def load_fixture(path: str) -> pd.DataFrame:
    """Reads a captured snapshot through the same parser the live adapter uses."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = payload["data"] if isinstance(payload, dict) else payload
    return parse_insider_rows(rows)


__all__ = [
    "COLUMNS",
    "DEFAULT_UNIVERSE",
    "FILING_LAG_BUSINESS_DAYS",
    "PUBLISHED_AT_RULE",
    "InsiderAdapter",
    "load_fixture",
    "parse_insider_rows",
]
