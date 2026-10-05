"""engine.data.fixtures: the captured real MarketData must load, satisfy the contract and keep its real values.

The fixture was captured on 2026-10-05 with no API key (tests/fixtures/market_data/README.md lists every source
and the cross-checks). These tests are the guard that a future refactor does not silently change the contract the
feature, regime and strategy modules consume.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from engine.data.fixtures import (
    FIXTURE_DIR,
    TABLE_FILES,
    load_fixture_market_data,
    save_fixture_market_data,
)
from engine.data.market_data import COT_COLUMNS, CURVE_COLUMNS, NEWS_COLUMNS, PRICE_COLUMNS, WPSR_COLUMNS

pytestmark = pytest.mark.skipif(not FIXTURE_DIR.exists(), reason="tests/fixtures/market_data not captured")
MAX_BYTES = 8 * 1024 * 1024


@pytest.fixture(scope="module")
def md():
    return load_fixture_market_data()


def test_fixture_files_and_size():
    files = sorted(p.name for p in FIXTURE_DIR.glob("*"))
    for name in TABLE_FILES.values():
        assert name in files, name
    assert "README.md" in files
    total = sum(p.stat().st_size for p in FIXTURE_DIR.glob("*"))
    assert total < MAX_BYTES, f"fixture troppo grande: {total / 1024 / 1024:.1f} MB"
    readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")
    assert "2026-10-05" in readme and "113,96" in readme  # capture time + a real cross-checked value
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["sources"]["brent_spot"] == "eia_xls"
    assert meta["captured_at"].startswith("2026-10-05")


def test_contract(md):
    assert list(md.prices.columns) == PRICE_COLUMNS
    assert md.prices.index.name == "date" and md.prices.index.tz is None
    assert md.prices.index.is_monotonic_increasing and not md.prices.index.has_duplicates
    assert list(md.curve.columns)[: len(CURVE_COLUMNS) + 1] == [*CURVE_COLUMNS, "M1_code"]
    assert list(md.wpsr.columns) == WPSR_COLUMNS
    assert list(md.cot.columns) == COT_COLUMNS
    assert list(md.news.columns) == NEWS_COLUMNS
    assert md.rigs.name == "rigs" and md.rigs.dtype == "float64"
    assert md.intraday is None
    assert md.wpsr.index.name == "published_at" and md.wpsr.index.tz is not None
    assert md.cot.index.name == "published_at" and md.cot.index.tz is not None
    assert {"prices", "brent_spot", "wti_spot", "news", "rigs"} <= set(md.published_at)
    for key, series in md.published_at.items():
        assert pd.DatetimeIndex(series.dropna()).tz is not None, key


def test_real_values(md):
    # prices
    assert md.prices.loc["2026-09-29", "brent_spot"] == pytest.approx(113.96)
    assert md.prices.loc["2026-09-29", "wti_spot"] == pytest.approx(96.16)
    assert md.prices.loc["2026-09-28", "brent_spot"] == pytest.approx(119.97)
    assert 95.0 < md.prices["brent_front_close"].dropna().iloc[-1] < 110.0
    assert md.prices.index[0] == pd.Timestamp("1987-05-20")  # first EIA Brent spot print
    assert md.prices.index[-1] == pd.Timestamp("2026-10-05")
    # curve: steep backwardation on the capture date
    assert not md.curve.empty
    row = md.curve.iloc[-1]
    assert row["M1_code"] == "BZZ26"
    assert float(row["M1"]) > float(row["M6"]) > float(row["M12"])
    # WPSR week 2026-09-25 (Cushing needs EIA_API_KEY: honest NaN)
    assert md.wpsr["period"].iloc[-1] == pd.Timestamp("2026-09-25")
    assert md.wpsr["crude_stocks"].iloc[-1] == pytest.approx(427320.0)
    assert bool(pd.isna(md.wpsr["cushing_stocks"].iloc[-1]))
    # COT
    for market, (long_, short_, oi) in {
        "brent": (311738, 116275, 2578636),
        "wti": (209028, 129436, 1878576),
        "gasoil": (93584, 24239, 749721),
    }.items():
        last = md.cot[md.cot["market"] == market].iloc[-1]
        assert last["period"] == pd.Timestamp("2026-09-29")
        assert int(last["mm_long"]) == long_ and int(last["mm_short"]) == short_
        assert int(last["oi"]) == oi
        assert int(last["mm_net"]) == long_ - short_
    # news: GPR vintage of 2026-10-05 (the index is revised, see the README)
    assert md.news.loc["2026-10-01", "gpr"] == pytest.approx(176.278549, abs=1e-5)
    assert md.news.loc["2026-10-05", "gdelt_volume"] == pytest.approx(1153.0)
    assert md.news["gdelt_volume"].notna().sum() == 1  # GDELT starts at the go-live
    # rigs
    assert md.rigs.loc["2026-10-02"] == pytest.approx(456.0)
    assert md.rigs.index[0] == pd.Timestamp("1987-07-17")


def test_nothing_is_forward_filled(md):
    """The EIA spot series is published weekly: the days in between must be NaN, not carried forward."""
    tail = md.prices.loc["2026-09-30":, "brent_spot"]
    assert tail.isna().all()
    assert md.prices["brent_spot"].notna().sum() < len(md.prices)
    # the roll-adjusted series exists wherever the front close exists and nowhere else
    front = md.prices["brent_front_close"].notna()
    cont = md.prices["brent_cont"].notna()
    assert bool((cont == front).all())


def test_continuous_series_only_differs_on_roll_days(md):
    """The adjustment must touch the roll days and NOTHING else, and must leave today's level untouched."""
    from engine.data.continuous import roll_dates_from_calendar

    cont = md.prices["brent_cont"].dropna()
    front = md.prices["brent_front_close"].dropna()
    assert cont.index.equals(front.index)
    assert cont.iloc[-1] == pytest.approx(front.iloc[-1])  # anchored at the last front close
    rolls = [
        pd.Timestamp(d)
        for d in roll_dates_from_calendar("BZ", front.index[0].date(), front.index[-1].date(), 2)
        if pd.Timestamp(d) in front.index
    ]
    assert len(rolls) > 200  # ~12 rolls a year since 2007
    off_roll_front = front.diff().drop(rolls)
    off_roll_cont = cont.diff().drop(rolls)
    pd.testing.assert_series_equal(off_roll_front, off_roll_cont, check_names=False)
    # the levels are shifted by the cumulative roll adjustment (steep 2026 backwardation: tens of dollars)
    assert abs(float((cont - front).iloc[0])) > 1.0
    assert float((cont - front).iloc[-1]) == pytest.approx(0.0)
    # every roll of this fixture was adjusted with the Brent spot return (no archived curve history yet)
    rolls_meta = md.meta.get("approx", {}).get("brent_cont_rolls", {})
    assert rolls_meta.get("none", 0) == 0


def test_published_at_is_consistent_with_the_index(md):
    pub = md.published_at["prices"].dropna()
    idx = pd.DatetimeIndex(pub.index).tz_localize(UTC)
    assert bool((pd.DatetimeIndex(pub) >= idx).all())  # a row is never known before its own date
    spot_pub = md.published_at["brent_spot"].dropna()
    spot_idx = pd.DatetimeIndex(spot_pub.index).tz_localize(UTC)
    assert bool((pd.DatetimeIndex(spot_pub) > spot_idx).all())  # EIA publishes the daily spot series with a lag


def test_truncate_is_point_in_time(md):
    cut = md.truncate(datetime(2026, 9, 29, 23, 0, tzinfo=UTC))
    assert cut.prices.index.max() <= pd.Timestamp("2026-09-29")
    assert cut.wpsr.empty  # week 2026-09-25, released 2026-09-30
    assert cut.cot.index.max() <= pd.Timestamp("2026-09-29 23:00:00+00:00")
    assert cut.rigs.index.max() <= pd.Timestamp("2026-09-29")
    # truncating twice is idempotent
    again = cut.truncate(datetime(2026, 9, 29, 23, 0, tzinfo=UTC))
    assert len(again.prices) == len(cut.prices)


def test_roundtrip_save_and_load(tmp_path: Path, md):
    save_fixture_market_data(md, tmp_path, meta={"captured_at": "2026-10-05T16:30:00Z", "sources": {}})
    back = load_fixture_market_data(tmp_path)
    pd.testing.assert_frame_equal(back.prices, md.prices, check_dtype=False)
    pd.testing.assert_frame_equal(back.wpsr, md.wpsr, check_dtype=False)
    pd.testing.assert_series_equal(back.rigs, md.rigs, check_dtype=False)
    assert set(back.published_at) == set(md.published_at)


def test_missing_fixture_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_fixture_market_data(tmp_path / "nope")
