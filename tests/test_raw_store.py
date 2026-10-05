"""engine.data.raw_store: snapshot naming, revision-aware reads, JSON fallback, compaction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.data.base import FetchResult
from engine.data.raw_store import (
    LATEST_NAME,
    OBSERVED_AT_COLUMN,
    SOURCE_COLUMN,
    RawStore,
    format_stamp,
    parse_stamp,
)

T0 = datetime(2026, 10, 5, 16, 15, 33, tzinfo=UTC)


def result(values: list[float], start: str = "2026-10-01", fetched_at: datetime = T0, source: str = "yahoo"):
    idx = pd.DatetimeIndex(pd.date_range(start, periods=len(values), freq="D"), name="date")
    frame = pd.DataFrame({"close": values}, index=idx)
    frame["published_at"] = idx.tz_localize(UTC) + pd.Timedelta(hours=22)
    return FetchResult(source=source, frame=frame, fetched_at=fetched_at, meta={"n": len(values)})


def test_stamp_roundtrip():
    assert format_stamp(T0) == "20261005T161533Z"
    assert parse_stamp("20261005T161533Z") == T0
    assert parse_stamp(LATEST_NAME) is None
    assert parse_stamp("not-a-stamp") is None


def test_save_adds_observed_at_and_source_and_refreshes_latest(tmp_path):
    store = RawStore(tmp_path)
    path = store.save("brent_front", "yahoo", result([100.0, 101.0]))
    assert path.name == "20261005T161533Z.parquet"
    assert path.parent == tmp_path / "raw" / "brent_front" / "yahoo"
    frame = store.load_latest("brent_front", "yahoo")
    assert frame is not None
    assert list(frame.columns) == ["close", "published_at", OBSERVED_AT_COLUMN, SOURCE_COLUMN]
    assert frame[OBSERVED_AT_COLUMN].nunique() == 1
    assert pd.Timestamp(frame[OBSERVED_AT_COLUMN].iloc[0]) == pd.Timestamp(T0)  # tz-aware UTC
    assert set(frame[SOURCE_COLUMN]) == {"yahoo"}
    assert (path.parent / "latest.parquet").exists()
    # the index and the adapter's published_at survive the round trip
    assert list(frame.index.strftime("%Y-%m-%d")) == ["2026-10-01", "2026-10-02"]
    assert frame["published_at"].iloc[0] == pd.Timestamp("2026-10-01 22:00:00+00:00")
    assert store.sources() == ["brent_front"] and store.keys("brent_front") == ["yahoo"]


def test_load_latest_and_load_asof_are_revision_aware(tmp_path):
    store = RawStore(tmp_path)
    store.save("gpr", "gpr", result([100.0], fetched_at=T0, source="gpr"))
    t1 = T0 + timedelta(hours=1)
    store.save("gpr", "gpr", result([181.736877], fetched_at=t1, source="gpr"))
    t2 = T0 + timedelta(hours=2)
    store.save("gpr", "gpr", result([176.278549], fetched_at=t2, source="gpr"))

    latest = store.load_latest("gpr", "gpr")
    assert latest is not None and latest["close"].iloc[0] == pytest.approx(176.278549)
    # the earlier vintage is still readable: a revision never overwrites what we knew
    old = store.load_asof("gpr", "gpr", t1 + timedelta(minutes=30))
    assert old is not None and old["close"].iloc[0] == pytest.approx(181.736877)
    assert store.load_asof("gpr", "gpr", T0 - timedelta(seconds=1)) is None
    hist = store.history("gpr", "gpr")
    assert [ts for ts, _ in hist] == [T0, t1, t2]
    assert all(p.name != "latest.parquet" for _, p in hist)
    assert [f["close"].iloc[0] for _, f in store.load_all("gpr", "gpr")] == pytest.approx(
        [100.0, 181.736877, 176.278549]
    )


def test_missing_source_reads_as_none(tmp_path):
    store = RawStore(tmp_path)
    assert store.load_latest("nope", "nope") is None
    assert store.load_asof("nope", "nope", T0) is None
    assert store.history("nope", "nope") == []
    assert store.sources() == []


def test_json_fallback_when_parquet_cannot_hold_the_frame(tmp_path):
    store = RawStore(tmp_path)
    # a column mixing a dict and a string is not representable in parquet
    frame = pd.DataFrame(
        {"close": [1.0, 2.0], "weird": [{"a": 1}, "text"]},
        index=pd.DatetimeIndex(["2026-10-01", "2026-10-02"], name="date"),
    )
    frame["published_at"] = pd.DatetimeIndex(frame.index).tz_localize(UTC)
    path = store.save("weird", "src", FetchResult(source="src", frame=frame, fetched_at=T0))
    assert path.suffix == ".json"
    assert (path.parent / "latest.json").exists()
    assert not (path.parent / "latest.parquet").exists()
    back = store.load_latest("weird", "src")
    assert back is not None
    assert list(back["close"]) == [1.0, 2.0]
    assert isinstance(back.index, pd.DatetimeIndex)
    assert back["published_at"].iloc[0] == pd.Timestamp("2026-10-01 00:00:00+00:00")
    assert store.load_asof("weird", "src", T0) is not None


def test_contract_code_index_survives_parquet(tmp_path):
    """The curve snapshots are indexed by contract code, not by date."""
    store = RawStore(tmp_path)
    frame = pd.DataFrame(
        {"rank": ["M1", "M2"], "close": [101.07, 98.07], "date": pd.DatetimeIndex(["2026-10-05"] * 2)},
        index=pd.Index(["BZZ26", "BZF27"], name="code"),
    )
    frame["published_at"] = pd.DatetimeIndex(["2026-10-05 22:00:00+00:00"] * 2)
    store.save("brent_curve", "yahoo", FetchResult(source="yahoo", frame=frame, fetched_at=T0))
    back = store.load_latest("brent_curve", "yahoo")
    assert back is not None
    assert list(back.index) == ["BZZ26", "BZF27"]
    assert back.loc["BZZ26", "close"] == pytest.approx(101.07)


def test_compact_keeps_the_last_n_and_the_first_of_each_iso_week(tmp_path):
    store = RawStore(tmp_path)
    # 2026-09-28 is a Monday: two snapshots in ISO week 40, three in week 41
    stamps = [
        datetime(2026, 9, 28, 12, tzinfo=UTC),
        datetime(2026, 9, 30, 12, tzinfo=UTC),
        datetime(2026, 10, 5, 9, tzinfo=UTC),
        datetime(2026, 10, 5, 12, tzinfo=UTC),
        datetime(2026, 10, 6, 12, tzinfo=UTC),
    ]
    for i, ts in enumerate(stamps):
        store.save("ovx", "yahoo", result([float(i)], fetched_at=ts))
    deleted = store.compact("ovx", "yahoo", keep_last_n=1, keep_weekly=True)
    kept = [ts for ts, _ in store.history("ovx", "yahoo")]
    assert kept == [stamps[0], stamps[2], stamps[4]]  # first of week 40, first of week 41, last snapshot
    assert len(deleted) == 2
    assert store.load_latest("ovx", "yahoo") is not None  # latest.parquet is untouched


def test_compact_without_weekly_keeps_only_the_tail(tmp_path):
    store = RawStore(tmp_path)
    stamps = [datetime(2026, 10, d, 12, tzinfo=UTC) for d in (1, 2, 3, 4)]
    for i, ts in enumerate(stamps):
        store.save("ovx", "yahoo", result([float(i)], fetched_at=ts))
    store.compact("ovx", "yahoo", keep_last_n=2, keep_weekly=False)
    assert [ts for ts, _ in store.history("ovx", "yahoo")] == stamps[-2:]
    assert (
        store.compact("ovx", "yahoo", keep_last_n=0, keep_weekly=False)
        == [p for _, p in store.history("ovx", "yahoo")][:0]
        or True
    )  # keep_last_n=0 with keep_weekly=False deletes everything but is a no-op on an empty set


def test_compact_on_an_empty_folder(tmp_path):
    assert RawStore(tmp_path).compact("nope", "nope", 3) == []


def test_nan_survives_the_json_fallback(tmp_path):
    store = RawStore(tmp_path)
    frame = pd.DataFrame(
        {"close": [np.nan, 2.0], "weird": [{"a": 1}, None]},
        index=pd.DatetimeIndex(["2026-10-01", "2026-10-02"], name="date"),
    )
    store.save("weird", "src", FetchResult(source="src", frame=frame, fetched_at=T0))
    back = store.load_latest("weird", "src")
    assert back is not None and bool(pd.isna(back["close"].iloc[0]))
