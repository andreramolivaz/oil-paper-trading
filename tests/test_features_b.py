"""Offline, deterministic tests for the point-in-time feature modules of agent B:
fundamentals, positioning, news, events, intermarket.

All market data below is SYNTHETIC (random walks and hand-built patterns) and exists only to exercise the logic;
nothing here is ever shipped as real data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine.core.calendar import front_month, ice_brent_expiry
from engine.core.timeutil import cot_release_ts, eia_wpsr_release_ts, settlement_ts
from engine.data.market_data import NEWS_COLUMNS, PRICE_COLUMNS, MarketData
from engine.features import catalog as cat
from engine.features import events, fundamentals, intermarket, news, positioning

MODULES = (fundamentals, positioning, news, events, intermarket)
START = "2018-01-01"
END = "2026-10-13"  # a Tuesday, two weeks ahead of "today" for the known-event test
ASOF = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "market_data"


# ----------------------------------------------------------------------------------------------------------------
# synthetic market builders
# ----------------------------------------------------------------------------------------------------------------
def seasonal_change(period: pd.Timestamp) -> float:
    """Hand-built seasonality: builds of +1000 kbbl in ISO weeks 1-26, draws of -1000 afterwards."""
    return 1000.0 if period.isocalendar().week <= 26 else -1000.0


def make_base(start: str = START, end: str = END, seed: int = 0) -> pd.DataFrame:
    days = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    px = 80.0 * np.exp(np.cumsum(rng.normal(0.0, 0.015, len(days))))
    return pd.DataFrame(
        {
            cat.PX: px,
            cat.PX_FRONT: px,
            cat.SPOT: px * 1.01,
            "open": px,
            "high": px * 1.01,
            "low": px * 0.99,
            "close": px,
        },
        index=days,
    )


def make_wpsr(start: str = "2017-01-06", end: str = "2026-09-25", seed: int = 1) -> pd.DataFrame:
    """Weekly WPSR rows: period = Friday, published the following Wednesday 10:30 ET (eia_wpsr_release_ts)."""
    rng = np.random.default_rng(seed)
    periods = pd.date_range(start, end, freq="W-FRI")
    changes = np.array([seasonal_change(p) for p in periods])
    crude = 450_000.0 + np.cumsum(changes)
    pub = pd.DatetimeIndex([eia_wpsr_release_ts((p + timedelta(days=5)).date()) for p in periods], name="published_at")
    n = len(periods)
    return pd.DataFrame(
        {
            "period": periods,
            "crude_stocks": crude,
            "cushing_stocks": 40_000.0 + np.cumsum(rng.normal(0.0, 500.0, n)),
            "gasoline_stocks": 230_000.0 + rng.normal(0.0, 2000.0, n),
            "distillate_stocks": 120_000.0 + rng.normal(0.0, 2000.0, n),
            "refinery_inputs": 16_000.0 + rng.normal(0.0, 300.0, n),
            "crude_production": 13_000.0 + rng.normal(0.0, 100.0, n),
            "crude_imports": 6_000.0 + rng.normal(0.0, 300.0, n),
            "crude_exports": 4_000.0 + rng.normal(0.0, 300.0, n),
            "gasoline_supplied": 9_000.0 + rng.normal(0.0, 200.0, n),
            "distillate_supplied": 4_000.0 + rng.normal(0.0, 150.0, n),
        },
        index=pub,
    )


def make_cot(start: str = "2017-01-03", end: str = "2026-09-29", seed: int = 2) -> pd.DataFrame:
    """COT rows per market: period = Tuesday, published Friday 15:30 ET (after the ICE settlement)."""
    rng = np.random.default_rng(seed)
    tues = pd.date_range(start, end, freq="W-TUE")
    pub = pd.DatetimeIndex([cot_release_ts((t + timedelta(days=3)).date()) for t in tues], name="published_at")
    frames = []
    for mkt, level in (("brent", 200_000.0), ("wti", 150_000.0), ("gasoil", 50_000.0)):
        net = level + np.cumsum(rng.normal(0.0, 5000.0, len(tues)))
        frames.append(
            pd.DataFrame(
                {
                    "period": tues,
                    "market": mkt,
                    "oi": 2_000_000.0,
                    "mm_long": net + 100_000.0,
                    "mm_short": 100_000.0,
                    "mm_net": net,
                    "prod_long": 0.0,
                    "prod_short": 0.0,
                    "swap_long": 0.0,
                    "swap_short": 0.0,
                },
                index=pub,
            )
        )
    return pd.concat(frames).sort_index(kind="stable")


def make_news(
    start: str = "2017-01-01", end: str = "2026-10-12", seed: int = 3, gdelt_from: str | None = "2025-01-01"
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, end, freq="D")
    df = pd.DataFrame(np.nan, index=days, columns=NEWS_COLUMNS)
    df["gpr"] = 100.0 + rng.normal(0.0, 20.0, len(days))
    df["gpr_act"] = 100.0 + rng.normal(0.0, 20.0, len(days))
    df["gpr_threat"] = 100.0 + rng.normal(0.0, 20.0, len(days))
    if gdelt_from is not None:
        live = days >= pd.Timestamp(gdelt_from)
        df.loc[live, "gdelt_volume"] = rng.normal(1000.0, 100.0, int(live.sum()))
        df.loc[live, "gdelt_tone"] = rng.normal(-3.0, 1.0, int(live.sum()))
    return df


def make_prices(base: pd.DataFrame, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    px = base[cat.PX_FRONT].to_numpy()
    prices = pd.DataFrame(np.nan, index=base.index, columns=PRICE_COLUMNS)
    prices["brent_front_close"] = px
    prices["wti_front_close"] = px - 4.0 + rng.normal(0.0, 1.0, len(px))
    prices["rbob_close"] = (px + 20.0 + rng.normal(0.0, 2.0, len(px))) / 42.0
    prices["ho_close"] = (px + 30.0 + rng.normal(0.0, 2.0, len(px))) / 42.0
    return prices


def make_rigs(start: str = "2017-01-06", end: str = "2026-10-09") -> pd.Series:
    fridays = pd.date_range(start, end, freq="W-FRI")
    return pd.Series(400.0 + 2.0 * np.arange(len(fridays)), index=fridays, name="rigs")


def make_market(end: str = END) -> tuple[MarketData, pd.DataFrame]:
    base = make_base(end=end)
    md = MarketData(prices=make_prices(base), wpsr=make_wpsr(), cot=make_cot(), news=make_news(), rigs=make_rigs())
    return md, base


def value_visible_at(table: pd.DataFrame, column: str, day: pd.Timestamp) -> float:
    """Reference implementation: value of the last row published at or before the settlement of ``day``."""
    pub = pd.DatetimeIndex(table.index)
    known = table.loc[pub <= pd.Timestamp(settlement_ts(day.date()))]
    return float("nan") if known.empty else float(known[column].iloc[-1])


# ----------------------------------------------------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------------------------------------------------
def test_settlement_index_matches_timeutil_in_gmt_and_bst():
    idx = pd.DatetimeIndex(["2026-01-15", "2026-03-27", "2026-03-30", "2026-07-15", "2026-10-13", "2026-10-26"])
    got = events.settlement_index(idx)
    for d, s in zip(idx, got):
        assert s.to_pydatetime() == settlement_ts(d.date())
    assert got[4] == pd.Timestamp("2026-10-13 18:30", tz="UTC")  # BST
    assert got[0] == pd.Timestamp("2026-01-15 19:30", tz="UTC")  # GMT


def test_release_table_keeps_first_vintage_and_monotone_publication():
    pub = pd.DatetimeIndex(
        ["2026-09-23 14:30", "2026-09-30 14:30", "2026-10-07 14:30", "2026-10-01 09:00"], tz="UTC", name="published_at"
    )
    df = pd.DataFrame(
        {
            "period": ["2026-09-18", "2026-09-25", "2026-09-25", "2026-09-11"],  # third row = revision of 09-25
            "crude_stocks": [1.0, 2.0, 2.5, 0.5],  # fourth row = an older period published late
        },
        index=pub,
    )
    rel = events.release_table(df, ["crude_stocks"])
    assert rel["period"].dt.strftime("%Y-%m-%d").tolist() == ["2026-09-11", "2026-09-18", "2026-09-25"]
    assert rel["crude_stocks"].tolist() == [0.5, 1.0, 2.0]  # revision ignored, first vintage kept
    # the late 09-11 row drags the visibility of the later periods to its own publication (cummax)
    assert rel["published_at"].tolist() == [pd.Timestamp("2026-10-01 09:00", tz="UTC")] * 3


def test_align_released_handles_empty_table():
    days = pd.bdate_range("2026-09-24", "2026-09-30")
    res = events.align_released(pd.DataFrame(columns=["v"]), pd.DatetimeIndex([]), events.settlement_index(days), days)
    assert res.index.equals(days) and res["v"].isna().all() and res["published_at"].isna().all()


# ----------------------------------------------------------------------------------------------------------------
# fundamentals
# ----------------------------------------------------------------------------------------------------------------
def test_wpsr_visible_only_after_wednesday_publication():
    md, base = make_market(end="2026-10-05")
    wpsr = md.wpsr
    last = wpsr.iloc[-1]
    assert last["period"] == pd.Timestamp("2026-09-25")
    assert wpsr.index[-1] == pd.Timestamp("2026-09-30 14:30", tz="UTC")
    out = fundamentals.compute(md, datetime(2026, 10, 5, 23, tzinfo=UTC), base)
    prev_value = float(wpsr["crude_stocks"].iloc[-2])
    new_value = float(last["crude_stocks"])
    assert prev_value != new_value
    for d in ("2026-09-28", "2026-09-29"):
        assert out.loc[d, cat.CRUDE_STOCKS] == prev_value
    for d in ("2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"):
        assert out.loc[d, cat.CRUDE_STOCKS] == new_value
    assert out.loc["2026-09-30", cat.DAYS_SINCE_WPSR] == 0
    assert out.loc["2026-10-01", cat.DAYS_SINCE_WPSR] == 1
    assert out.loc["2026-10-05", cat.DAYS_SINCE_WPSR] == 5
    # every date agrees with the brute-force reference
    for day in base.index[-60:]:
        ref = value_visible_at(wpsr, "cushing_stocks", day)
        assert out.loc[day, cat.CUSHING_STOCKS] == ref


def test_surprise_model_with_constructed_seasonal_pattern():
    md, base = make_market(end="2026-10-05")
    wpsr = md.wpsr.copy()
    # perturb ONLY the last release (period 2026-09-25, ISO week 39): +5000 kbbl on top of the seasonal -1000
    wpsr.iloc[-1, wpsr.columns.get_loc("crude_stocks")] += 5000.0
    md.wpsr = wpsr
    out = fundamentals.compute(md, datetime(2026, 10, 5, 23, tzinfo=UTC), base)
    # unperturbed release (period 2026-09-18, week 38): actual -1000, seasonal mean of week 38 = -1000,
    # last week's change -1000 -> expected = -1000 + 0.3 * -1000 = -1300 -> surprise = +300
    assert out.loc["2026-09-29", cat.STOCK_SURPRISE] == pytest.approx(300.0)
    # perturbed release: actual +4000 against the same expectation -1300 -> surprise = 5300
    assert out.loc["2026-09-30", cat.STOCK_SURPRISE] == pytest.approx(5300.0)
    assert np.isfinite(out.loc["2026-09-29", cat.STOCK_SURPRISE_Z])
    assert np.isfinite(out.loc["2026-09-30", cat.STOCK_SURPRISE_Z])
    assert out.loc["2026-09-30", cat.STOCK_SURPRISE_Z] > out.loc["2026-09-29", cat.STOCK_SURPRISE_Z]
    # the perturbation is in the future for 2026-09-29: nothing before 09-30 may see it
    md2, _ = make_market(end="2026-10-05")
    ref = fundamentals.compute(md2, datetime(2026, 10, 5, 23, tzinfo=UTC), base)
    pd.testing.assert_frame_equal(out.loc[:"2026-09-29"], ref.loc[:"2026-09-29"])


def test_crude_vs_5y_and_range_position_hand_numbers():
    years = range(2021, 2027)
    periods = pd.DatetimeIndex([pd.Timestamp(date.fromisocalendar(y, 15, 5)) for y in years])  # Friday, ISO week 15
    pub = pd.DatetimeIndex([eia_wpsr_release_ts((p + timedelta(days=5)).date()) for p in periods], name="published_at")
    wpsr = pd.DataFrame(
        {"period": periods, "crude_stocks": [400e3, 410e3, 420e3, 430e3, 440e3, 450e3], "cushing_stocks": np.nan},
        index=pub,
    )
    base = make_base(start="2026-04-01", end="2026-04-20")
    out = fundamentals.compute(MarketData(wpsr=wpsr), datetime(2026, 4, 20, 23, tzinfo=UTC), base)
    release_day = pd.Timestamp(pub[-1].tz_convert(UTC).date())  # Wednesday 2026-04-15
    assert out.loc[release_day, cat.CRUDE_STOCKS] == 450e3
    assert out.loc[release_day, cat.CRUDE_STOCKS_VS_5Y] == pytest.approx((450e3 - 420e3) / 420e3)
    assert out.loc[release_day, cat.CRUDE_STOCKS_5Y_RANGE_POS] == 1.0  # 1.25 before clipping to [0, 1]
    assert np.isnan(out.loc[release_day, cat.STOCK_SURPRISE])  # consecutive periods are a year apart
    # the day before the release still sees the 2025 report: 440e3 vs the mean of 2021-2024 (415e3; 2020 absent)
    day_before = release_day - pd.Timedelta(days=1)
    assert out.loc[day_before, cat.CRUDE_STOCKS] == 440e3
    assert out.loc[day_before, cat.CRUDE_STOCKS_VS_5Y] == pytest.approx((440e3 - 415e3) / 415e3)
    # and nothing at all before the 2024 report (only 3 earlier years: 2021-2023 -> first value needs >= 3)
    base_2023 = make_base(start="2023-04-10", end="2023-04-28")
    out_2023 = fundamentals.compute(MarketData(wpsr=wpsr), datetime(2023, 4, 28, 23, tzinfo=UTC), base_2023)
    assert out_2023[cat.CRUDE_STOCKS_VS_5Y].isna().all()  # 2023 report has only 2021-2022 behind it


def test_rigs_published_friday_before_settlement_and_13w_change():
    md, base = make_market(end="2026-10-05")
    out = fundamentals.compute(md, datetime(2026, 10, 5, 23, tzinfo=UTC), base)
    rigs = md.rigs
    fri = pd.Timestamp("2026-10-02")
    assert out.loc[fri, cat.RIGS] == rigs.loc[fri]  # 13:00 New York is before the 19:30 London settlement
    assert out.loc["2026-10-01", cat.RIGS] == rigs.loc["2026-09-25"]
    assert out.loc[fri, cat.RIGS_CHG_13W] == pytest.approx(26.0)  # +2 rigs per week x 13


# ----------------------------------------------------------------------------------------------------------------
# positioning
# ----------------------------------------------------------------------------------------------------------------
def test_cot_visible_only_after_friday_release():
    md, base = make_market(end="2026-10-05")
    brent = md.cot[md.cot["market"] == "brent"]
    assert brent["period"].iloc[-1] == pd.Timestamp("2026-09-29")
    assert brent.index[-1] == pd.Timestamp("2026-10-02 19:30", tz="UTC")  # 15:30 EDT, after the 18:30 UTC settlement
    out = positioning.compute(md, datetime(2026, 10, 5, 23, tzinfo=UTC), base)
    prev_value, new_value = float(brent["mm_net"].iloc[-2]), float(brent["mm_net"].iloc[-1])
    for d in ("2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"):
        assert out.loc[d, cat.COT_MM_NET_BRENT] == prev_value
    assert out.loc["2026-10-05", cat.COT_MM_NET_BRENT] == new_value
    for day in base.index[-60:]:
        assert out.loc[day, cat.COT_MM_NET_BRENT] == value_visible_at(brent, "mm_net", day)
    assert out.attrs["cot_crowding_source"] == "brent"
    assert out.attrs["cot_crowding_fallback"] == "wti"
    pctl = out[cat.COT_MM_NET_BRENT_PCTL].dropna()
    assert len(pctl) > 0 and pctl.between(0.0, 1.0).all()
    crowd = out[cat.COT_CROWDING].dropna()
    assert crowd.between(0.0, 1.0).all()
    np.testing.assert_allclose(crowd, ((out[cat.COT_MM_NET_BRENT_PCTL] - 0.5).abs() * 2).dropna())
    # 4-week change arithmetic on the release table
    rel = events.release_table(brent, ["mm_net"])
    assert out.loc["2026-10-05", cat.COT_MM_NET_CHG_4W] == pytest.approx(
        float(rel["mm_net"].iloc[-1] - rel["mm_net"].iloc[-5])
    )


def test_cot_crowding_falls_back_to_wti_when_brent_missing():
    md, base = make_market(end="2026-10-05")
    md.cot = md.cot[md.cot["market"] != "brent"]
    out = positioning.compute(md, datetime(2026, 10, 5, 23, tzinfo=UTC), base)
    assert out[cat.COT_MM_NET_BRENT].isna().all() and out[cat.COT_MM_NET_BRENT_PCTL].isna().all()
    assert out.attrs["cot_crowding_source"] == "wti" and out.attrs["cot_crowding_fallback"] is None
    np.testing.assert_allclose(
        out[cat.COT_CROWDING].dropna(), ((out[cat.COT_MM_NET_WTI_PCTL] - 0.5).abs() * 2).dropna()
    )


def test_trailing_percentile_is_rank_among_prior_values():
    s = pd.Series(np.arange(60, dtype=float))  # strictly increasing -> always above every prior value
    p = positioning.trailing_percentile(s, window=20, min_periods=10)
    assert p.iloc[:9].isna().all()
    assert (p.dropna() == 1.0).all()
    assert positioning.trailing_percentile(-s, window=20, min_periods=10).dropna().eq(0.0).all()


# ----------------------------------------------------------------------------------------------------------------
# news
# ----------------------------------------------------------------------------------------------------------------
def test_gpr_lagged_two_business_days_without_published_at():
    md, base = make_market(end="2026-10-05")
    news_df = md.news.copy()
    marker = 777.0
    news_df.loc["2026-09-28", "gpr"] = marker  # Monday -> published Wednesday 2026-09-30
    md.news = news_df
    out = news.compute(md, datetime(2026, 10, 5, 23, tzinfo=UTC), base)
    assert out.loc["2026-09-28", cat.GPR] != marker
    assert out.loc["2026-09-29", cat.GPR] != marker
    assert out.loc["2026-09-30", cat.GPR] == marker
    # Tuesday sees Sunday's value (Sunday + 2 business days = Tuesday), never Monday's
    assert out.loc["2026-09-29", cat.GPR] == news_df.loc["2026-09-27", "gpr"]
    assert out.loc["2026-10-01", cat.GPR] == news_df.loc["2026-09-29", "gpr"]


def test_news_uses_published_at_override_when_present():
    md, base = make_market(end="2026-10-05")
    news_df = md.news.copy()
    news_df.loc["2026-09-28", "gpr"] = 777.0
    md.news = news_df
    # adapter says every row is known at 06:00 UTC of its own date
    md.published_at["news"] = pd.Series(
        pd.DatetimeIndex(news_df.index) + pd.Timedelta(hours=6), index=news_df.index
    ).dt.tz_localize(UTC)
    out = news.compute(md, datetime(2026, 10, 5, 23, tzinfo=UTC), base)
    assert out.loc["2026-09-28", cat.GPR] == 777.0
    assert out.loc["2026-09-29", cat.GPR] == news_df.loc["2026-09-29", "gpr"]


def test_geo_index_spike_and_flags_without_gdelt():
    days = pd.date_range("2025-06-01", "2026-10-12", freq="D")
    rng = np.random.default_rng(7)
    news_df = pd.DataFrame(np.nan, index=days, columns=NEWS_COLUMNS)
    news_df["gpr"] = 100.0 + rng.normal(0.0, 5.0, len(days))
    news_df["gpr_threat"] = news_df["gpr"]
    # Thursday -> published Monday 2026-10-12 on its own. (A Friday spike would be published on Tuesday together
    # with the calmer Saturday and Sunday rows, and the latest known row -- Sunday -- would be the live level.)
    news_df.loc["2026-10-08", "gpr"] = 250.0
    base = make_base(start="2025-06-02", end=END)
    out = news.compute(MarketData(news=news_df), ASOF, base)
    assert out[cat.GDELT_TONE].isna().all() and out[cat.GDELT_VOLUME_Z].isna().all()
    assert (out[cat.DEESCALATION_FLAG] == 0.0).all()  # NaN -> 0 when GDELT is absent
    geo = out[cat.GEO_INDEX].dropna()
    assert geo.between(0.0, 1.0).all()
    # without GDELT the index is the logistic of GPR_Z
    np.testing.assert_allclose(geo, 1.0 / (1.0 + np.exp(-out[cat.GPR_Z].dropna())))
    assert out.loc["2026-10-09", cat.GPR] != 250.0  # Friday still sees Wednesday's value (2-business-day lag)
    assert out.loc["2026-10-12", cat.GPR] == 250.0
    assert out.loc["2026-10-13", cat.GPR] == news_df.loc["2026-10-11", "gpr"]  # Sunday's row, published Tuesday
    assert out[cat.GEO_SPIKE].mean() < 0.15  # noise alone spikes rarely
    assert out.loc["2026-10-12", cat.GEO_INDEX] > 0.95
    assert out.loc["2026-10-12", cat.GEO_SPIKE] == 1.0
    assert out.loc["2026-10-13", cat.GEO_SPIKE] == 0.0  # back to noise the next day
    assert out[cat.GEO_SPIKE].isin([0.0, 1.0]).sum() == out[cat.GEO_INDEX].notna().sum()


def test_deescalation_flag_with_gdelt():
    days = pd.date_range("2026-01-01", "2026-10-12", freq="D")
    rng = np.random.default_rng(8)
    news_df = pd.DataFrame(np.nan, index=days, columns=NEWS_COLUMNS)
    news_df["gpr"] = 100.0 + rng.normal(0.0, 5.0, len(days))
    news_df["gdelt_volume"] = 1000.0 + rng.normal(0.0, 20.0, len(days))
    news_df["gdelt_tone"] = -3.0 + rng.normal(0.0, 0.1, len(days))
    news_df.loc["2026-10-03":"2026-10-12", "gdelt_volume"] = 2000.0  # the story is big ...
    news_df.loc["2026-10-09":"2026-10-12", "gdelt_tone"] = -1.0  # ... and turning benign over the last days
    base = make_base(start="2026-01-02", end=END)
    out = news.compute(MarketData(news=news_df), ASOF, base)
    assert out.loc["2026-10-13", cat.GDELT_TONE] == -1.0  # yesterday's aggregate (1-day lag)
    assert out.loc["2026-10-13", cat.GDELT_VOLUME_Z] > 1.0
    assert out.loc["2026-10-13", cat.DEESCALATION_FLAG] == 1.0
    assert out.loc["2026-09-01", cat.DEESCALATION_FLAG] == 0.0
    assert out[cat.DEESCALATION_FLAG].isin([0.0, 1.0]).all()


# ----------------------------------------------------------------------------------------------------------------
# events
# ----------------------------------------------------------------------------------------------------------------
def test_hours_to_event_for_a_known_tuesday_and_calendar_columns():
    base = make_base(start="2026-01-02", end=END)
    out = events.compute(MarketData(), ASOF, base)
    tue = pd.Timestamp("2026-10-13")
    # settlement Tue 19:30 London (BST) = 18:30 UTC; EIA WPSR Wed 14 Oct 10:30 ET = 14:30 UTC -> 20 hours
    assert out.loc[tue, cat.HOURS_TO_EVENT] == pytest.approx(20.0)
    assert out.loc[tue, cat.NEXT_EVENT_ID] == "eia_wpsr"
    assert out[cat.NEXT_EVENT_ID].dtype == object
    # Wednesday after the release: next binary event is the following Wednesday's WPSR, 164 hours later
    assert out.loc["2026-10-07", cat.HOURS_TO_EVENT] == pytest.approx(164.0)
    y, m = front_month("BZ", tue.date())
    assert (y, m) == (2026, 12) and ice_brent_expiry(y, m) == date(2026, 10, 30)
    assert out.loc[tue, cat.DAYS_TO_EXPIRY] == 17
    assert out.loc["2026-10-09", cat.IS_PRE_WEEKEND] == 1.0 and out.loc[tue, cat.IS_PRE_WEEKEND] == 0.0
    assert out.loc[tue, cat.HURRICANE_SEASON] == 1.0 and out.loc["2026-01-15", cat.HURRICANE_SEASON] == 0.0
    assert out[cat.HOURS_TO_EVENT].notna().all() and (out[cat.HOURS_TO_EVENT] >= 0).all()


def test_days_to_expiry_hits_zero_on_the_last_trading_day():
    base = make_base(start="2026-10-01", end="2026-11-03")
    out = events.compute(MarketData(), datetime(2026, 11, 3, 23, tzinfo=UTC), base)
    assert out.loc["2026-10-30", cat.DAYS_TO_EXPIRY] == 0  # Dec-26 last trading day
    assert out.loc["2026-11-02", cat.DAYS_TO_EXPIRY] == (ice_brent_expiry(2027, 1) - date(2026, 11, 2)).days


# ----------------------------------------------------------------------------------------------------------------
# intermarket
# ----------------------------------------------------------------------------------------------------------------
def test_brent_wti_and_crack_arithmetic_with_hand_numbers():
    base = make_base(start="2026-09-01", end="2026-09-30")
    base[cat.PX_FRONT] = 85.0
    prices = pd.DataFrame(np.nan, index=base.index, columns=PRICE_COLUMNS)
    prices["wti_front_close"] = 80.0
    prices["rbob_close"] = 2.5
    prices["ho_close"] = 3.0
    out = intermarket.compute(MarketData(prices=prices), datetime(2026, 9, 30, 23, tzinfo=UTC), base)
    d = pd.Timestamp("2026-09-15")
    assert out.loc[d, cat.BRENT_WTI] == pytest.approx(5.0)
    assert out.loc[d, cat.CRACK_321] == pytest.approx(32.0)  # (2*105 + 126 - 240) / 3
    assert out.loc[d, cat.DIESEL_CRACK] == pytest.approx(3.0 * 42 - 85.0)
    assert out.loc[d, cat.GASOLINE_CRACK] == pytest.approx(2.5 * 42 - 85.0)
    assert out.loc[d, cat.PRODUCT_LEAD] == pytest.approx(0.0)  # flat prices -> no lead
    # a missing WTI close leaves NaN (no forward fill)
    prices.loc[d, "wti_front_close"] = np.nan
    out2 = intermarket.compute(MarketData(prices=prices), datetime(2026, 9, 30, 23, tzinfo=UTC), base)
    assert np.isnan(out2.loc[d, cat.BRENT_WTI]) and np.isnan(out2.loc[d, cat.CRACK_321])
    assert out2.loc[d, cat.DIESEL_CRACK] == pytest.approx(41.0)


def test_product_lead_log_returns():
    base = make_base(start="2026-09-01", end="2026-09-30")
    prices = pd.DataFrame(np.nan, index=base.index, columns=PRICE_COLUMNS)
    prices["wti_front_close"] = 80.0
    prices["rbob_close"] = 2.0
    prices["ho_close"] = 2.0
    prices.iloc[-1, prices.columns.get_loc("rbob_close")] = 2.2
    prices.iloc[-1, prices.columns.get_loc("ho_close")] = 2.2
    out = intermarket.compute(MarketData(prices=prices), datetime(2026, 9, 30, 23, tzinfo=UTC), base)
    assert out[cat.PRODUCT_LEAD].iloc[-1] == pytest.approx(np.log(1.1))
    assert out[cat.PRODUCT_LEAD].iloc[-2] == pytest.approx(0.0)


def test_causal_ols_residual_uses_only_past_coefficients():
    base = make_base(start="2018-01-01", end="2026-10-05")
    wti = base[cat.PX_FRONT] - 4.0
    resid = intermarket.causal_ols_residual(base[cat.PX_FRONT], wti, window=756, min_obs=252)
    # exact linear relation -> residuals ~0 once the first fit exists; nothing before the first month start
    # that has >= 252 observations behind it
    first = resid.first_valid_index()
    assert first is not None and first >= base.index[252]
    assert first.day <= 3  # a refit date = first trading day of a month
    np.testing.assert_allclose(resid.dropna().to_numpy(), 0.0, atol=1e-6)
    # the regression on the full sample equals the regression on the truncated sample for the common dates
    short = intermarket.causal_ols_residual(base[cat.PX_FRONT].loc[:"2024-06-30"], wti.loc[:"2024-06-30"])
    pd.testing.assert_series_equal(short, resid.loc[short.index])


# ----------------------------------------------------------------------------------------------------------------
# contract and no-look-ahead for every module / column
# ----------------------------------------------------------------------------------------------------------------
def test_modules_return_same_index_and_only_catalog_columns():
    md, base = make_market()
    seen: set[str] = set()
    allowed = {*cat.ALL_NUMERIC_FEATURES, cat.NEXT_EVENT_ID}
    for mod in MODULES:
        out = mod.compute(md, ASOF, base)
        assert out.index.equals(base.index), mod.__name__
        assert set(out.columns) == set(mod.COLUMNS), mod.__name__
        assert set(out.columns) <= allowed, mod.__name__
        assert not (set(out.columns) & seen), f"{mod.__name__} duplicates a column"
        seen |= set(out.columns)
        assert not (set(out.columns) & set(base.columns))
        for c in out.columns:
            if c != cat.NEXT_EVENT_ID:
                assert out[c].dtype == float, (mod.__name__, c, out[c].dtype)
                assert out[c].notna().any(), (mod.__name__, c)


def test_modules_survive_empty_market_data():
    base = make_base(start="2026-09-01", end="2026-10-05")
    for mod in MODULES:
        out = mod.compute(MarketData(), datetime(2026, 10, 5, 23, tzinfo=UTC), base)
        assert out.index.equals(base.index)
        assert list(out.columns) == list(mod.COLUMNS)
    assert fundamentals.compute(MarketData(), ASOF, base).isna().all().all()
    assert positioning.compute(MarketData(), ASOF, base).attrs["cot_crowding_source"] is None
    empty_base = make_base().iloc[0:0]
    for mod in MODULES:
        assert len(mod.compute(MarketData(), ASOF, empty_base)) == 0


@pytest.mark.parametrize("mod", MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_future_rows_do_not_change_past_values(mod):
    """Appending future rows (prices, releases, news, dates) must leave every past feature value unchanged."""
    md_full, base_full = make_market()
    cut = pd.Timestamp("2026-09-29")
    asof_cut = datetime(2026, 9, 29, 23, 59, tzinfo=UTC)
    base_cut = base_full.loc[:cut]
    md_cut = md_full.truncate(asof_cut)
    assert md_cut.wpsr.index.max() < md_full.wpsr.index.max()
    assert md_cut.cot.index.max() < md_full.cot.index.max()
    assert len(md_cut.news) < len(md_full.news)

    past = mod.compute(md_cut, asof_cut, base_cut)
    full = mod.compute(md_full, ASOF, base_full)
    assert len(full) > len(past)
    pd.testing.assert_frame_equal(full.loc[past.index], past)
    # the module must be point-in-time on its own: an un-truncated MarketData with a truncated base gives the same
    mixed = mod.compute(md_full, asof_cut, base_cut)
    pd.testing.assert_frame_equal(mixed, past)
    for c in past.columns:
        assert past[c].notna().any(), c


# ----------------------------------------------------------------------------------------------------------------
# optional smoke test on captured real data (skipped until another agent ships the fixture loader)
# ----------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not FIXTURE_DIR.exists(), reason="tests/fixtures/market_data not captured yet")
def test_smoke_on_fixture_market_data():
    fixtures = pytest.importorskip("engine.data.fixtures")
    loader = getattr(fixtures, "load_fixture_market_data", None)
    if loader is None:
        pytest.skip("engine.data.fixtures.load_fixture_market_data not available")
    md = loader()
    last = md.last_date()
    if last is None:
        pytest.skip("fixture MarketData has no prices")
    px = md.prices["brent_front_close"].astype(float)
    base = pd.DataFrame({cat.PX: px, cat.PX_FRONT: px, cat.SPOT: md.prices["brent_spot"].astype(float)})
    asof = datetime.combine(last.date(), datetime.min.time(), tzinfo=UTC) + timedelta(hours=23)
    for mod in MODULES:
        out = mod.compute(md, asof, base)
        assert out.index.equals(base.index)
        assert list(out.columns) == list(mod.COLUMNS)
