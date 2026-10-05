"""No look-ahead: truncating the data at T must not change any decision taken at or before T.

This is the test the brief asks for in §12 ("troncare i dati futuri non deve cambiare i segnali passati").
Three independent angles:

1. the session run with `strict_pit=True` (MarketData.truncate at every settlement) produces the SAME signals
   and orders as the fast path, which relies on the feature builder's own publication filtering;
2. appending future rows to the MarketData does not change a decision already taken at T;
3. the real feature builder, on data that includes a WPSR release published AFTER T, does not show that
   release in the features at T.
"""

from __future__ import annotations

import pandas as pd

from engine.backtest.session import SessionState, TradingSession
from engine.broker.paper import PaperBroker
from engine.core.config import RiskConfig
from engine.core.store import StateStore
from engine.core.timeutil import settlement_ts
from engine.features import catalog as cat
from engine.features.builder import FullFeatureBuilder
from engine.portfolio.base import CappedEqualWeightAllocator
from tests.synthetic import add_wpsr, make_market_data
from tests.test_session import MiniBuilder, StubRegime, ToyLong


def _run(md, days, strict_pit: bool, tmp_path):  # noqa: ANN001, ANN202
    risk = RiskConfig.load()
    store = StateStore(tmp_path)
    session = TradingSession(
        md=md,
        strategies=[ToyLong()],
        master=PaperBroker("master", risk, store=None),
        risk=risk,
        allocator=CappedEqualWeightAllocator(),
        feature_builder=MiniBuilder(),
        regime_model=StubRegime(),
        store=store,
        strict_pit=strict_pit,
        shadow_accounts=False,
        state=SessionState(),
    )
    for day in days:
        session.run_day(day)
    signals = [{k: v for k, v in s.items() if k not in {"meta"}} for s in store.read_jsonl("signals")]
    orders = [(o["instrument"], round(float(o["qty_bbl"]), 6), o["ts"]) for o in store.read_jsonl("trades")]
    return signals, orders


def test_strict_pit_and_fast_path_agree(tmp_path):
    md = make_market_data(n_days=200, vol=0.012, drift=0.0006)
    days = [d.date() for d in pd.DatetimeIndex(md.prices.index)][:150]
    fast = _run(md, days, False, tmp_path / "fast")
    strict = _run(md, days, True, tmp_path / "strict")
    assert fast[0] == strict[0], "signals differ between the point-in-time and the fast path"
    assert fast[1] == strict[1], "fills differ between the point-in-time and the fast path"
    assert fast[0], "no signals were produced: the test would be vacuous"


def test_future_rows_do_not_change_past_decisions(tmp_path):
    full = make_market_data(n_days=260, vol=0.012, drift=0.0006)
    days = [d.date() for d in pd.DatetimeIndex(full.prices.index)]
    cut_days = days[:150]

    # the same market data truncated after the last decision day
    truncated = make_market_data(n_days=260, vol=0.012, drift=0.0006)
    keep = pd.DatetimeIndex(truncated.prices.index) <= pd.Timestamp(cut_days[-1])
    truncated.prices = truncated.prices.loc[keep]
    truncated.curve = truncated.curve.loc[keep]
    truncated.published_at = {k: v.loc[keep] for k, v in truncated.published_at.items()}

    with_future = _run(full, cut_days, False, tmp_path / "with_future")
    without_future = _run(truncated, cut_days, False, tmp_path / "without_future")
    assert with_future[0] == without_future[0]
    assert with_future[1] == without_future[1]


def test_real_builder_hides_a_release_published_after_the_decision(tmp_path):
    """A WPSR row whose period precedes T but whose publication follows T must not be visible at T."""
    md = add_wpsr(make_market_data(n_days=300, vol=0.012), n_weeks=40)
    assert not md.wpsr.empty
    published = pd.DatetimeIndex(md.wpsr.index)
    target_pub = published[-1]
    period = pd.Timestamp(md.wpsr["period"].iloc[-1])

    builder = FullFeatureBuilder()
    # a decision taken on the trading day BEFORE that publication, but after the period it refers to
    day_before = [
        d.date()
        for d in pd.DatetimeIndex(md.prices.index)
        if period < pd.Timestamp(d) and settlement_ts(d.date()) < target_pub.to_pydatetime()
    ][-1]
    frame_before = builder.build(md, settlement_ts(day_before))
    day_after = [
        d.date() for d in pd.DatetimeIndex(md.prices.index) if settlement_ts(d.date()) >= target_pub.to_pydatetime()
    ][0]
    frame_after = builder.build(md, settlement_ts(day_after))

    latest_stocks = float(md.wpsr["crude_stocks"].iloc[-1])
    before = frame_before[cat.CRUDE_STOCKS].dropna()
    after = frame_after[cat.CRUDE_STOCKS].dropna()
    if not before.empty:
        assert float(before.iloc[-1]) != latest_stocks, "a release was visible before it was published"
    assert not after.empty and float(after.iloc[-1]) == latest_stocks, "the release is missing after publication"


def test_features_for_past_rows_are_stable_when_data_grows(tmp_path):
    """The real builder: rows up to T must be identical whether or not later data exists."""
    full = make_market_data(n_days=400, vol=0.012)
    cut_ts = pd.DatetimeIndex(full.prices.index)[250]
    keep = pd.DatetimeIndex(full.prices.index) <= cut_ts
    short = make_market_data(n_days=400, vol=0.012)
    short.prices = short.prices.loc[keep]
    short.curve = short.curve.loc[keep]
    short.published_at = {k: v.loc[keep] for k, v in short.published_at.items()}

    builder = FullFeatureBuilder()
    asof = settlement_ts(cut_ts.date())
    a = builder.build(full, asof)
    b = builder.build(short, asof)
    common = [c for c in a.columns if c in b.columns]
    a2 = a.loc[:cut_ts, common].astype("float64", errors="ignore")
    b2 = b.loc[:cut_ts, common].astype("float64", errors="ignore")
    pd.testing.assert_frame_equal(a2, b2, check_dtype=False, rtol=1e-9, atol=1e-12)
