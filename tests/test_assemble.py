"""engine.data.assemble + engine.data.fetch: the MarketData contract, point-in-time truncation, orchestration.

The raw store is built here from small snapshots that have the EXACT shape the real adapters return (the shapes
are pinned by tests/test_yahoo.py, test_eia.py, test_cot.py, test_news.py and test_rigs.py), so the assembler is
exercised without the network. One test additionally runs on the captured real fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from engine.core.config import CONFIG_DIR, Settings
from engine.core.store import StateStore
from engine.data.assemble import Loader, build_market_data, describe, last_rows, stale_columns
from engine.data.base import FetchResult, Health
from engine.data.fetch import HEALTH_FILE, Fetcher, quality_issues
from engine.data.market_data import COT_COLUMNS, NEWS_COLUMNS, PRICE_COLUMNS, WPSR_COLUMNS
from engine.data.raw_store import RawStore

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
DATES = pd.DatetimeIndex(pd.date_range("2026-09-28", periods=6, freq="B"), name="date")  # Mon 28/9 .. Mon 5/10
BRENT_CLOSES = [105.28, 102.59, 103.53, 102.31, 102.25, 101.07]  # real BZ=F settlements, 2026-09-28..10-05


def settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path / "state",
        config_dir=CONFIG_DIR,
        eia_api_key=None,
        fred_api_key=None,
        github_repo="test/test",
        offline=True,
    )


def _save(store: RawStore, entry: str, adapter: str, frame: pd.DataFrame, fetched_at: datetime = NOW) -> None:
    store.save(entry, adapter, FetchResult(source=adapter, frame=frame, fetched_at=fetched_at, meta={}))


def daily_frame(values: list[float], column: str = "value", dates: pd.DatetimeIndex = DATES) -> pd.DataFrame:
    frame = pd.DataFrame({column: values}, index=dates[: len(values)])
    frame["published_at"] = pd.DatetimeIndex(frame.index).tz_localize(UTC) + pd.Timedelta(hours=18)
    return frame


def yahoo_bars(closes: list[float], dates: pd.DatetimeIndex = DATES) -> pd.DataFrame:
    idx = dates[: len(closes)]
    close = pd.Series(closes, index=idx, dtype="float64")
    frame = pd.DataFrame(
        {
            "open": close - 0.3,
            "high": close + 0.5,
            "low": close - 0.6,
            "close": close,
            "volume": 100_000.0,
            "open_interest": np.nan,
        }
    )
    frame["published_at"] = pd.DatetimeIndex(idx).tz_localize("Europe/London") + pd.Timedelta(hours=23)
    frame["published_at"] = pd.DatetimeIndex(frame["published_at"]).tz_convert(UTC)
    return frame


def curve_snapshot(asof: str, closes: dict[str, tuple[str, float]]) -> pd.DataFrame:
    rows = [
        {
            "code": code,
            "rank": rank,
            "close": close,
            "date": pd.Timestamp(asof),
            "published_at": pd.Timestamp(f"{asof} 22:00:00+00:00"),
        }
        for rank, (code, close) in closes.items()
    ]
    return pd.DataFrame(rows).set_index("code")


def wpsr_frame() -> pd.DataFrame:
    """The real WPSR week 2026-09-25, released Wednesday 2026-09-30 14:30 UTC."""
    frame = pd.DataFrame(
        {
            "crude_stocks": [427320.0],
            "cushing_stocks": [np.nan],  # the key-less table1.csv has no Cushing column
            "gasoline_stocks": [204362.0],
            "distillate_stocks": [105180.0],
            "refinery_inputs": [16257.0],
            "crude_production": [13955.0],
            "crude_imports": [5698.0],
            "crude_exports": [3570.0],
            "gasoline_supplied": [8689.0],
            "distillate_supplied": [3948.0],
        },
        index=pd.DatetimeIndex(["2026-09-25"], name="period"),
    )
    frame["published_at"] = pd.DatetimeIndex(["2026-09-30 14:30:00+00:00"])
    return frame


def cot_frame(market: str, mm_long: int, mm_short: int, oi: int) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "market": [market],
            "oi": [oi],
            "mm_long": [mm_long],
            "mm_short": [mm_short],
            "mm_net": [mm_long - mm_short],
            "prod_long": [1],
            "prod_short": [2],
            "swap_long": [3],
            "swap_short": [4],
        },
        index=pd.DatetimeIndex(["2026-09-29"], name="period"),
    )
    frame["published_at"] = pd.DatetimeIndex(["2026-10-02 19:30:00+00:00"])
    return frame


def gpr_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex(["2026-10-03", "2026-10-04", "2026-10-05"], name="date")
    frame = pd.DataFrame(
        {
            "gpr": [160.736404, 145.607376, 141.491577],
            "gpr_act": [264.747070, 163.304077, 165.948303],
            "gpr_threat": [116.223793, 151.346359, 166.100739],
            "n10d": [474.0, 364.0, 398.0],
        },
        index=idx,
    )
    frame["published_at"] = idx.tz_localize(UTC) + pd.Timedelta(days=2, hours=12)
    return frame


def gdelt_daily_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex(["2026-10-05"], name="date")
    frame = pd.DataFrame(
        {
            "gdelt_volume": [1153.0],
            "gdelt_tone": [-3.709586],
            "gdelt_goldstein": [-0.655507],
            "gdelt_conflict_share": [0.239437],
            "gdelt_matched_events": [71.0],
            "gdelt_total_events": [963.0],
        },
        index=idx,
    )
    frame["published_at"] = idx.tz_localize(UTC) + pd.Timedelta(days=1)
    return frame


def rigs_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex(["2026-09-25", "2026-10-02"], name="week")
    frame = pd.DataFrame({"rigs_oil": [455.0, 456.0], "rigs_gas": [135.0, 133.0], "rigs_total": [599.0, 598.0]}, idx)
    frame["published_at"] = idx.tz_localize("America/New_York") + pd.Timedelta(hours=13)
    frame["published_at"] = pd.DatetimeIndex(frame["published_at"]).tz_convert(UTC)
    return frame


@pytest.fixture
def archive(tmp_path: Path) -> tuple[RawStore, Settings]:
    """A raw store holding one snapshot per table, with real values and real adapter shapes."""
    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    _save(store, "brent_spot", "eia_xls", daily_frame([119.97, 113.96], dates=DATES[:2]))
    _save(store, "wti_spot", "fred_csv", daily_frame([99.37, 96.16], dates=DATES[:2]))
    _save(store, "brent_front", "yahoo", yahoo_bars(BRENT_CLOSES))
    _save(store, "wti_front", "yahoo", yahoo_bars([x - 6.0 for x in BRENT_CLOSES]))
    _save(store, "rbob_front", "yahoo", yahoo_bars([2.9] * 6))
    _save(store, "ho_front", "yahoo", yahoo_bars([3.4] * 6))
    _save(
        store,
        "brent_curve",
        "yahoo",
        curve_snapshot(
            "2026-10-05",
            {
                "M1": ("BZZ26", 101.07),
                "M2": ("BZF27", 98.07),
                "M3": ("BZG27", 96.13),
                "M6": ("BZK27", 90.24),
                "M12": ("BZZ27", 82.78),
            },
        ),
    )
    _save(store, "ovx", "yahoo", yahoo_bars([56.11, 53.74, 52.24, 51.69, 51.0, 49.68]))
    _save(store, "wpsr", "eia_wpsr_csv", wpsr_frame())
    _save(store, "cot_wti", "cftc_socrata", cot_frame("wti", 209028, 129436, 1878576))
    _save(store, "cot_brent", "ice_cot", cot_frame("brent", 311738, 116275, 2578636))
    _save(store, "gpr", "gpr", gpr_frame())
    _save(store, "gdelt_daily", "gdelt_files", gdelt_daily_frame())
    _save(store, "rig_count", "baker_hughes", rigs_frame())
    return store, cfg


# -------------------------------------------------------------------------------------------- the MarketData shape
def test_build_market_data_follows_the_contract(archive):
    store, cfg = archive
    md = build_market_data(store, cfg)
    assert list(md.prices.columns) == PRICE_COLUMNS
    assert md.prices.index.name == "date" and md.prices.index.tz is None
    assert md.prices.index.is_monotonic_increasing and not md.prices.index.has_duplicates
    assert list(md.wpsr.columns) == WPSR_COLUMNS and md.wpsr.index.name == "published_at"
    assert list(md.cot.columns) == COT_COLUMNS and md.cot.index.name == "published_at"
    assert list(md.news.columns) == NEWS_COLUMNS and md.news.index.name == "date"
    assert md.rigs.name == "rigs"
    assert md.intraday is None  # nothing archived for the intraday table
    assert {"prices", "curve", "news", "rigs"} <= set(md.published_at)
    # real values travel through untouched
    assert md.prices.loc["2026-09-29", "brent_spot"] == pytest.approx(113.96)
    assert md.prices.loc["2026-09-29", "wti_spot"] == pytest.approx(96.16)
    assert md.prices.loc["2026-10-05", "brent_front_close"] == pytest.approx(101.07)
    assert md.prices.loc["2026-10-05", "ovx"] == pytest.approx(49.68)
    assert md.curve.loc["2026-10-05", "M1_code"] == "BZZ26"
    assert md.curve.loc["2026-10-05", "M2"] == pytest.approx(98.07)
    assert md.wpsr["crude_stocks"].iloc[0] == pytest.approx(427320.0)
    assert md.cot.loc[md.cot["market"] == "brent", "mm_long"].iloc[0] == 311738
    assert md.cot.loc[md.cot["market"] == "brent", "mm_short"].iloc[0] == 116275
    assert md.news.loc["2026-10-05", "gpr"] == pytest.approx(141.491577)
    assert md.news.loc["2026-10-05", "gdelt_volume"] == pytest.approx(1153.0)
    assert md.rigs.loc["2026-10-02"] == pytest.approx(456.0)
    assert md.meta["source"]["brent_spot"] == "eia_xls"
    assert md.meta["table_asof"]["prices"].date().isoformat() == "2026-10-05"


def test_prices_are_the_union_of_the_sources_and_are_never_filled(archive):
    store, cfg = archive
    md = build_market_data(store, cfg)
    # the spot series only covers the first two dates: the rest must stay NaN, not be carried forward
    assert md.prices["brent_spot"].notna().sum() == 2
    assert bool(pd.isna(md.prices.loc["2026-10-05", "brent_spot"]))
    # a column no source provides is present and entirely NaN
    assert md.prices["breakeven10y"].isna().all()
    assert len(md.prices) == 6


def test_continuous_series_is_built_and_anchored(archive):
    store, cfg = archive
    md = build_market_data(store, cfg)
    cont = md.prices["brent_cont"].dropna()
    assert len(cont) == 6
    # no roll date falls inside this 6-day window, so cont == close
    assert cont.iloc[-1] == pytest.approx(101.07)
    pd.testing.assert_series_equal(cont, md.prices["brent_front_close"].dropna(), check_names=False, check_dtype=False)
    assert md.meta["approx"]["brent_cont_rolls"]["curve"] == 0


def test_curve_snapshot_regression(archive):
    """A curve snapshot carries a 'date' column and a contract-code index: assembling it must not raise."""
    store, cfg = archive
    md = build_market_data(store, cfg)
    assert len(md.curve) == 1
    assert md.curve.index[0] == pd.Timestamp("2026-10-05")
    assert md.published_at["curve"].iloc[0] == pd.Timestamp("2026-10-05 22:00:00+00:00")
    assert bool(pd.isna(md.curve.loc["2026-10-05", "M4"]))  # a rank Yahoo has no bar for stays NaN
    assert len(md.curve_approx) == 1 and not bool(md.curve_approx.iloc[0])


def test_wti_proxy_goes_into_separate_columns(tmp_path):
    """The EIA NYMEX WTI history is a proxy: it must never be written into M1..M4."""
    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    idx = pd.DatetimeIndex(["2024-04-04", "2024-04-05"], name="date")
    proxy = pd.DataFrame(
        {"RCLC1": [86.59, 86.91], "RCLC2": [86.12, 86.45], "RCLC3": [85.6, 85.9], "RCLC4": [85.0, 85.3]}, index=idx
    )
    proxy["published_at"] = idx.tz_localize(UTC) + pd.Timedelta(days=1)
    _save(store, "wti_curve_hist", "eia", proxy)
    _save(store, "brent_front", "yahoo", yahoo_bars(BRENT_CLOSES))
    md = build_market_data(store, cfg)
    assert md.curve.loc["2024-04-05", "WTI_C1"] == pytest.approx(86.91)
    assert bool(pd.isna(md.curve.loc["2024-04-05", "M1"]))  # no Brent curve that far back: honest NaN
    assert bool(md.curve_approx.loc["2024-04-05"])  # flagged as proxy-only
    assert md.meta["approx"]["curve_wti_proxy"] is True
    assert "approx" in md.meta["source"]["curve_wti_proxy"]


def test_fallback_order_comes_from_the_config(tmp_path):
    """brent_spot prefers EIA over FRED even when both snapshots exist (config/data_sources.yaml order)."""
    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    _save(store, "brent_spot", "fred_csv", daily_frame([999.0, 999.0], dates=DATES[:2]))
    _save(store, "brent_spot", "eia_xls", daily_frame([119.97, 113.96], dates=DATES[:2]))
    md = build_market_data(store, cfg)
    assert md.prices.loc["2026-09-29", "brent_spot"] == pytest.approx(113.96)
    assert md.meta["source"]["brent_spot"] == "eia_xls"
    assert md.meta["fallbacks"].get("brent_spot") == "eia_xls"  # not the first entry of the chain (eia needs a key)


def test_missing_tables_are_reported_not_invented(tmp_path):
    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    _save(store, "brent_front", "yahoo", yahoo_bars(BRENT_CLOSES))
    md = build_market_data(store, cfg)
    assert md.wpsr.empty and md.cot.empty and md.news.empty and md.rigs.empty
    assert "wpsr" in md.meta["missing"] and "cot_brent" in md.meta["missing"]
    reds = {h.source for h in md.health if h.status is Health.RED}
    assert {"wpsr", "cot_brent", "gpr"} <= reds
    assert "nessuno snapshot" in next(h for h in md.health if h.source == "wpsr").message


def test_asof_reads_the_archived_vintage(tmp_path):
    """A later snapshot (a revision) must not leak into an as-of read of an earlier instant."""
    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    _save(store, "gpr", "gpr", gpr_frame(), fetched_at=NOW - timedelta(hours=2))
    revised = gpr_frame()
    revised["gpr"] = [1.0, 2.0, 3.0]
    _save(store, "gpr", "gpr", revised, fetched_at=NOW)
    latest = build_market_data(store, cfg)
    assert latest.news.loc["2026-10-05", "gpr"] == pytest.approx(3.0)
    old = build_market_data(store, cfg, asof=NOW - timedelta(hours=1))
    assert old.news.loc["2026-10-05", "gpr"] == pytest.approx(141.491577)


# -------------------------------------------------------------------------------------------- point-in-time cut
def test_truncate_drops_a_wpsr_released_after_the_cut(archive):
    """The WPSR week ends 2026-09-25 but is released 2026-09-30: it must be invisible on 2026-09-29."""
    store, cfg = archive
    md = build_market_data(store, cfg)
    assert len(md.wpsr) == 1
    assert md.wpsr["period"].iloc[0] == pd.Timestamp("2026-09-25")
    cut = md.truncate(datetime(2026, 9, 29, 23, 59, tzinfo=UTC))
    assert cut.wpsr.empty  # the week it describes is in the past, the release is not
    after = md.truncate(datetime(2026, 9, 30, 15, 0, tzinfo=UTC))
    assert len(after.wpsr) == 1


def test_truncate_cuts_cot_news_rigs_and_prices_by_publication(archive):
    store, cfg = archive
    md = build_market_data(store, cfg)
    cut = md.truncate(datetime(2026, 10, 1, 12, 0, tzinfo=UTC))
    # the COT of 2026-09-29 is released on 2026-10-02 19:30 UTC
    assert cut.cot.empty
    # the earliest GPR row of the archive (2026-10-03) is published on 2026-10-05: nothing is known yet
    assert cut.news.empty
    # the rig count of 2026-10-02 is released that Friday 17:00 UTC
    assert len(cut.rigs) == 1 and cut.rigs.index[-1] == pd.Timestamp("2026-09-25")
    # Brent settlements are known at 22:00 UTC of their date
    assert cut.prices.index.max() == pd.Timestamp("2026-09-30")
    assert cut.curve.empty


# ------------------------------------------------------------------------------------------------ the Fetcher
class Boom:
    """An adapter whose fetch always explodes with a programming error (not DataUnavailable)."""

    name = "boom"

    def fetch(self, **kwargs: Any) -> FetchResult:
        raise RuntimeError("adapter interno rotto")


def test_fetcher_skips_key_adapters_and_reports_yellow(tmp_path):
    cfg = settings(tmp_path)
    state = StateStore(cfg.state_dir)
    fetcher = Fetcher(cfg, RawStore(cfg.state_dir), state)
    chain = [{"adapter": "eia_steo", "needs_key": True}]
    outcome = fetcher.run_entry("fundamentals", "steo_brent", chain, NOW)
    assert outcome.health.status is Health.YELLOW
    assert "EIA_API_KEY" in outcome.health.message
    assert outcome.attempts == [{"adapter": "eia_steo", "status": "skipped", "message": "EIA_API_KEY assente"}]
    assert not outcome.ok


def test_fetcher_marks_an_unknown_adapter_red_without_crashing(tmp_path):
    cfg = settings(tmp_path)
    fetcher = Fetcher(cfg, RawStore(cfg.state_dir), StateStore(cfg.state_dir))
    outcome = fetcher.run_entry("prices", "mystery", [{"adapter": "does_not_exist"}], NOW)
    assert outcome.health.status is Health.RED
    assert "adapter non registrato" in outcome.health.message


def test_fetcher_survives_a_broken_adapter_class(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    fetcher = Fetcher(cfg, RawStore(cfg.state_dir), StateStore(cfg.state_dir))
    monkeypatch.setitem(fetcher._instances, ("engine.data.adapters.news", "GprAdapter"), Boom())
    outcome = fetcher.run_entry("news", "gpr", [{"adapter": "gpr"}], NOW)
    assert outcome.health.status is Health.RED
    assert "RuntimeError" in outcome.health.message or "has no method" in outcome.health.message


def test_fetcher_missing_module_is_red(tmp_path, monkeypatch):
    from engine.data import fetch as fetch_mod

    cfg = settings(tmp_path)
    fetcher = Fetcher(cfg, RawStore(cfg.state_dir), StateStore(cfg.state_dir))
    monkeypatch.setitem(
        fetch_mod.ADAPTERS, "ghost", fetch_mod.AdapterSpec("engine.data.adapters.ghost", "GhostAdapter", "fetch")
    )
    outcome = fetcher.run_entry("prices", "ghost_entry", [{"adapter": "ghost"}], NOW)
    assert outcome.health.status is Health.RED
    assert "ModuleNotFoundError" in outcome.health.message


def test_fetcher_uses_the_second_entry_when_the_first_fails(tmp_path, monkeypatch):
    class Dead:
        name = "dead"

        def fetch_spot_xls(self, **kwargs: Any) -> FetchResult:
            from engine.core.errors import DataUnavailable

            raise DataUnavailable("HTTP 503")

    class Alive:
        name = "fred_csv"

        def fetch_series(self, series_id: str) -> FetchResult:
            return FetchResult(source="fred_csv", frame=daily_frame([113.96]), fetched_at=NOW, meta={})

    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    fetcher = Fetcher(cfg, store, StateStore(cfg.state_dir))
    monkeypatch.setitem(fetcher._instances, ("engine.data.adapters.eia", "EiaFallbackAdapter"), Dead())
    monkeypatch.setitem(fetcher._instances, ("engine.data.adapters.fred", "FredCsvAdapter"), Alive())
    chain = [
        {"adapter": "eia_xls", "url": "x"},
        {"adapter": "fred_csv", "series": "DCOILBRENTEU"},
    ]
    outcome = fetcher.run_entry("prices", "brent_spot", chain, NOW)
    assert outcome.ok and outcome.health.status is Health.YELLOW
    assert outcome.health.fallback_used == "fred_csv"
    assert [a["status"] for a in outcome.attempts] == ["failed", "ok"]
    assert store.load_latest("brent_spot", "fred_csv") is not None


def test_fetcher_writes_and_merges_health_json(tmp_path):
    cfg = settings(tmp_path)
    state = StateStore(cfg.state_dir)
    fetcher = Fetcher(cfg, RawStore(cfg.state_dir), state)
    fetcher.write_health(
        {
            "checked_at": NOW - timedelta(days=1),
            "sources": [{"source": "cot_brent", "status": "green", "rows": 822}],
            "overall": "green",
            "groups": {"positioning": {}},
        }
    )
    fetcher.write_health(
        {
            "checked_at": NOW,
            "sources": [{"source": "brent_front", "status": "yellow", "rows": 4775}],
            "overall": "yellow",
            "groups": {"prices": {}},
        }
    )
    health = state.read_json(HEALTH_FILE)
    assert [s["source"] for s in health["sources"]] == ["brent_front", "cot_brent"]
    assert health["overall"] == "yellow"  # recomputed over the merged set
    assert health["groups_checked"] == ["prices"]


def test_fetcher_groups_and_config(tmp_path):
    cfg = settings(tmp_path)
    fetcher = Fetcher(cfg, RawStore(cfg.state_dir), StateStore(cfg.state_dir))
    assert {"prices", "volatility_macro", "fundamentals", "positioning", "news"} <= set(fetcher.groups())
    chain = fetcher._chain("prices", "brent_spot")
    assert [e["adapter"] for e in chain] == ["eia", "eia_xls", "fred", "fred_csv"]


def test_quality_issues_scope_nonpositive_to_the_tail():
    """WTI really printed -37.63 in April 2020: an old negative must not make the source red today."""
    idx = pd.DatetimeIndex(pd.date_range("2020-04-01", periods=200, freq="B"), name="date")
    values = [50.0] * len(idx)
    values[13] = -37.63
    frame = pd.DataFrame({"close": values}, index=idx)
    frame["published_at"] = idx.tz_localize(UTC)
    issues = quality_issues("wti_front", frame, pd.Timestamp(idx[-1]).tz_localize(UTC).to_pydatetime())
    assert [i.kind for i in issues] == []
    # a negative print inside the tail is still an error
    values[-2] = -1.0
    frame = pd.DataFrame({"close": values}, index=idx)
    frame["published_at"] = idx.tz_localize(UTC)
    issues = quality_issues("wti_front", frame, pd.Timestamp(idx[-1]).tz_localize(UTC).to_pydatetime())
    assert [i.kind for i in issues] == ["nonpositive"]


def test_quality_issues_flag_a_duplicate_index():
    idx = pd.DatetimeIndex(["2026-10-05", "2026-10-05"], name="date")
    frame = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    issues = quality_issues("brent_front", frame, NOW)
    assert "duplicate" in {i.kind for i in issues}


# ----------------------------------------------------------------------------------------------------- helpers
def test_loader_candidates_include_unlisted_adapters(tmp_path):
    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    _save(store, "brent_spot", "legacy_adapter", daily_frame([100.0]))
    loader = Loader(store, cfg)
    assert loader.candidates("prices", "brent_spot")[-1] == "legacy_adapter"
    assert loader.load("prices", "brent_spot") is not None


def test_describe_and_helpers(archive):
    store, cfg = archive
    md = build_market_data(store, cfg)
    text = describe(md)
    assert "prices" in text and "salute:" in text
    rows = last_rows(md.prices, ["brent_front_close", "brent_spot"], 2)
    assert len(rows) == 2 and list(rows.columns) == ["brent_front_close", "brent_spot"]
    stale = stale_columns(md.prices, datetime(2026, 10, 6, tzinfo=UTC), timedelta(days=3))
    assert "brent_spot" in stale  # last print 2026-09-29
    assert "brent_front_close" not in stale
    assert "vix" in stale  # no source at all


def test_build_market_data_on_the_real_fixture():
    """The captured real snapshot must satisfy the same contract (and carry the known values)."""
    from engine.data.fixtures import load_fixture_market_data

    md = load_fixture_market_data()
    assert list(md.prices.columns) == PRICE_COLUMNS
    assert md.prices.index.is_monotonic_increasing
    assert md.prices.loc["2026-09-29", "brent_spot"] == pytest.approx(113.96)
    assert md.prices.loc["2026-09-29", "wti_spot"] == pytest.approx(96.16)
    assert md.wpsr["crude_stocks"].iloc[-1] == pytest.approx(427320.0)
    brent = md.cot[md.cot["market"] == "brent"]
    assert brent["mm_long"].iloc[-1] == 311738 and brent["mm_short"].iloc[-1] == 116275
    assert md.news.loc["2026-10-01", "gpr"] == pytest.approx(176.278549, abs=1e-5)
    assert md.rigs.iloc[-1] == pytest.approx(456.0)
    assert md.prices["brent_cont"].notna().sum() > 4000
    # point-in-time: nothing published after the last price date leaks in
    cut = md.truncate(datetime(2026, 9, 29, 23, 0, tzinfo=UTC))
    assert cut.wpsr.empty and cut.prices.index.max() <= pd.Timestamp("2026-09-29")


def test_cross_source_divergence_turns_the_entry_red(tmp_path):
    """EIA and FRED serve the same EIA spot series: a real disagreement is an error, not a rounding difference."""
    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    _save(store, "brent_spot", "eia_xls", daily_frame([119.97, 113.96], dates=DATES[:2]))
    _save(store, "brent_spot", "fred_csv", daily_frame([119.97, 113.96], dates=DATES[:2]))
    md = build_market_data(store, cfg)
    assert next(h for h in md.health if h.source == "brent_spot").status is Health.YELLOW  # only the fallback flag

    _save(store, "brent_spot", "fred_csv", daily_frame([119.97, 111.10], dates=DATES[:2]))
    md = build_market_data(store, cfg)
    health = next(h for h in md.health if h.source == "brent_spot")
    assert health.status is Health.RED
    assert "eia_xls 113.9600 vs fred_csv 111.1000" in health.message


def test_outlier_and_gap_checks_run_on_the_recent_tail(tmp_path):
    cfg = settings(tmp_path)
    store = RawStore(cfg.state_dir)
    idx = pd.DatetimeIndex(pd.date_range("2026-07-01", periods=60, freq="B"), name="date")
    closes = [100.0 + 0.1 * i for i in range(60)]
    closes[-3] = 300.0  # an impossible print in the recent window
    frame = pd.DataFrame({"close": closes}, index=idx)
    frame["published_at"] = idx.tz_localize(UTC) + pd.Timedelta(hours=22)
    _save(store, "brent_front", "yahoo", frame, fetched_at=datetime(2026, 9, 22, 18, tzinfo=UTC))
    md = build_market_data(store, cfg, asof=datetime(2026, 9, 22, 18, tzinfo=UTC))
    health = next(h for h in md.health if h.source == "brent_front")
    assert health.status is Health.YELLOW
    assert "variazione anomala" in health.message
