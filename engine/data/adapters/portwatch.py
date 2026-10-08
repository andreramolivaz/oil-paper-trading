"""IMF PortWatch: daily transits through the world's maritime chokepoints, counted from AIS ship positions.

Source: the public ArcGIS feature service behind https://portwatch.imf.org ("Daily Chokepoint Transit Calls and
Trade Volume Estimates", IMF and University of Oxford). No key. Verified from this project on 2026-10-08: the
Strait of Hormuz series runs from 2019-01-01, is updated on Tuesdays with data up to the previous Sunday, and
shows tanker transits falling from about 50 a day to about 1 a day after 2026-02-28.

Why the engine reads it: in the 2026 regime the price of Brent is a bet on whether this strait is open, and
this is the one free series that measures the thing itself rather than headlines about it. It is shown on the
terminal as context and NO rule reads it: there is exactly one closure in the sample, and nothing can be
validated on one episode.

Frame contract: index ``date`` (tz-naive midnight), columns ``portid``, ``portname``, ``n_tanker``, ``n_cargo``,
``n_total``, ``capacity_tanker``, ``capacity`` and ``published_at``.

``published_at`` is the following Tuesday at 15:00 UTC of the week (Monday-Sunday) the day belongs to: the
service publishes a week at a time, so a Wednesday is not knowable until six days later. That lag is the
honest one; pretending the series is daily-fresh would be look-ahead.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from engine.core.errors import DataUnavailable
from engine.data.base import FetchResult, HttpClient, utc_now

log = logging.getLogger(__name__)

SERVICE_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
)
PAGE = 1000  # the service's maxRecordCount
FIELDS = "date,portid,portname,n_tanker,n_cargo,n_total,capacity_tanker,capacity"
COLUMNS = ["portid", "portname", "n_tanker", "n_cargo", "n_total", "capacity_tanker", "capacity"]
PUBLISH_LAG = pd.Timedelta(days=2, hours=15)  # Sunday that closes the week + 2 days, 15:00 UTC


def published_at(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Tuesday 15:00 UTC after the Sunday that closes each day's week."""
    days = pd.DatetimeIndex(index).normalize()
    sunday = days + pd.to_timedelta((6 - days.weekday) % 7, unit="D")
    return pd.DatetimeIndex(sunday + PUBLISH_LAG).tz_localize("UTC")


def parse_features(features: list[dict[str, Any]]) -> pd.DataFrame:
    """ArcGIS feature rows -> the frame contract of this module (empty frame when there is nothing usable)."""
    rows = [f.get("attributes", {}) for f in features if isinstance(f, dict)]
    if not rows:
        return pd.DataFrame(columns=[*COLUMNS, "published_at"], index=pd.DatetimeIndex([], name="date"))
    frame = pd.DataFrame(rows)
    raw = frame["date"]
    # the service has served this field both as epoch milliseconds and as an ISO date string
    dates = (
        pd.to_datetime(raw, unit="ms", errors="coerce")
        if pd.api.types.is_numeric_dtype(raw)
        else pd.to_datetime(raw, errors="coerce")
    )
    frame = frame.assign(date=pd.DatetimeIndex(dates).normalize()).dropna(subset=["date"])
    frame = frame.set_index("date").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    for col in COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    for col in ("n_tanker", "n_cargo", "n_total", "capacity_tanker", "capacity"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    out = frame[COLUMNS].copy()
    out["published_at"] = published_at(pd.DatetimeIndex(out.index))
    out.index = pd.DatetimeIndex(out.index, name="date")
    return out


class PortWatchAdapter(HttpClient):
    name = "portwatch"

    def __init__(self, *, timeout: float = 60.0, retries: int = 2, backoff: float = 2.0) -> None:
        super().__init__(timeout=timeout, retries=retries, backoff=backoff, min_interval=0.3)

    def fetch(self, chokepoint: str = "Strait of Hormuz") -> FetchResult:
        """Every archived day of one chokepoint (the whole history is a few thousand rows)."""
        features: list[dict[str, Any]] = []
        offset = 0
        where = "portname='{}'".format(chokepoint.replace("'", "''"))
        while True:
            payload = self.get_json(
                SERVICE_URL,
                params={
                    "where": where,
                    "outFields": FIELDS,
                    "orderByFields": "date ASC",
                    "resultOffset": offset,
                    "resultRecordCount": PAGE,
                    "returnGeometry": "false",
                    "f": "json",
                },
            )
            if not isinstance(payload, dict) or "error" in payload:
                raise DataUnavailable(f"portwatch {chokepoint}: {str(payload)[:200]}")
            page = payload.get("features") or []
            features += page
            offset += len(page)
            if not page or not payload.get("exceededTransferLimit"):
                break
        frame = parse_features(features)
        if frame.empty:
            raise DataUnavailable(f"portwatch {chokepoint}: no rows")
        return FetchResult(
            source=self.name,
            frame=frame,
            fetched_at=utc_now(),
            meta={
                "chokepoint": chokepoint,
                "rows": len(frame),
                "published_at_rule": "martedì 15:00 UTC dopo la domenica",
            },
        )
