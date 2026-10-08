"""Insider (SEC Form 4) adapter, feature and strategy.

The fixture is a REAL, dated snapshot of Alpha Vantage's INSIDER_TRANSACTIONS for ConocoPhillips and
Occidental (captured 2026-10-05), trimmed to keep every open-market row plus a sample of each noise class.
Testing the filter against invented noise would prove nothing: the whole difficulty of this factor is that
three quarters of the real rows are compensation mechanics.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine.core.errors import DataUnavailable
from engine.data.adapters.insider import (
    FILING_LAG_BUSINESS_DAYS,
    InsiderAdapter,
    load_fixture,
    parse_insider_rows,
)
from engine.features import catalog as cat
from engine.features import insider as feat
from engine.strategies.s21_insider import S21InsiderConviction

FIXTURE = Path(__file__).parent / "fixtures" / "insider" / "alphavantage_form4_COP_OXY.json"
CALENDAR = pd.DatetimeIndex(pd.date_range("2018-01-01", "2026-10-05", freq="B"))


class _MD:
    """The feature only needs `insider` and a calendar."""

    def __init__(self, insider: pd.DataFrame) -> None:
        self.insider = insider
        self.prices = pd.DataFrame(index=CALENDAR)


@pytest.fixture(scope="module")
def transactions() -> pd.DataFrame:
    return load_fixture(str(FIXTURE))


@pytest.fixture(scope="module")
def features(transactions: pd.DataFrame) -> pd.DataFrame:
    return feat.build(_MD(transactions), index=CALENDAR)


# ---------------------------------------------------------------- adapter / parser


def test_fixture_is_real_and_dated(transactions: pd.DataFrame) -> None:
    assert len(transactions) > 500
    assert set(transactions["ticker"]) == {"COP", "OXY"}
    assert transactions.index.is_monotonic_increasing


def test_published_at_is_never_earlier_than_the_filing_deadline(transactions: pd.DataFrame) -> None:
    """The feed carries no filing date, so published_at is the statutory deadline. If it were the transaction
    date the engine would act on a Form 4 before anyone could have read it."""
    pub = pd.to_datetime(transactions["published_at"], utc=True).dt.tz_localize(None)
    gap_days = (pub.dt.normalize() - transactions.index.normalize()).dt.days
    # Two business days is never fewer than two calendar days, and is more across a weekend.
    assert (gap_days >= FILING_LAG_BUSINESS_DAYS).all()
    # And the filing always lands on a business day: nobody files on a Sunday.
    assert pub.dt.dayofweek.max() <= 4


def test_grants_and_option_exercises_are_not_open_market(transactions: pd.DataFrame) -> None:
    """Price 0 means a grant, a vesting or a tax withholding: compensation mechanics, not a view."""
    zero_price = transactions[transactions["price"] == 0.0]
    assert len(zero_price) > 0
    assert not zero_price["open_market"].any()
    non_common = transactions[~transactions["security_type"].str.contains("Common Stock", case=False)]
    assert len(non_common) > 0
    assert not non_common["open_market"].any()


def test_ten_percent_owners_are_tagged(transactions: pd.DataFrame) -> None:
    """On Occidental most large purchases are a fund accumulating a stake, not an officer's conviction."""
    owners = transactions[transactions["ten_percent_owner"]]
    assert len(owners) > 0
    assert owners["role"].str.contains("10% Owner", case=False).all()


def test_parser_drops_unusable_rows() -> None:
    rows = [
        {"transaction_date": "", "ticker": "X", "acquisition_or_disposal": "A", "shares": "1", "share_price": "1"},
        # unknown side, and a zero-share row: both unusable
        {
            "transaction_date": "2026-01-05",
            "ticker": "X",
            "acquisition_or_disposal": "?",
            "shares": "1",
            "share_price": "1",
        },
        {
            "transaction_date": "2026-01-05",
            "ticker": "X",
            "acquisition_or_disposal": "A",
            "shares": "0",
            "share_price": "1",
        },
        {
            "transaction_date": "2026-01-05",
            "ticker": "X",
            "executive_title": "CEO",
            "security_type": "Common Stock",
            "acquisition_or_disposal": "A",
            "shares": "100",
            "share_price": "50",
        },
    ]
    frame = parse_insider_rows(rows)
    assert len(frame) == 1
    assert frame["value_usd"].iloc[0] == pytest.approx(5000.0)


def test_adapter_without_key_is_unavailable_not_invented() -> None:
    with pytest.raises(DataUnavailable, match="chiave assente"):
        InsiderAdapter(api_key=None).fetch()


# ---------------------------------------------------------------- feature


def test_score_is_bounded_and_only_exists_with_enough_data(features: pd.DataFrame) -> None:
    score = features[cat.INSIDER_SCORE]
    assert score.dropna().between(-1.0, 1.0).all()
    # Where the score exists there must be at least MIN_TX transactions behind it.
    assert (features.loc[score.notna(), cat.INSIDER_N_TX] >= feat.MIN_TX).all()


def test_buy_ratio_is_a_share(features: pd.DataFrame) -> None:
    ratio = features[cat.INSIDER_BUY_RATIO].dropna()
    assert len(ratio) > 100
    assert ratio.between(0.0, 1.0).all()


def test_breadth_never_exceeds_the_universe(features: pd.DataFrame) -> None:
    assert features[cat.INSIDER_BREADTH].max() <= 2  # the fixture has two tickers


@pytest.mark.parametrize("cut", ["2020-09-30", "2022-03-31", "2024-06-30", "2025-12-31"])
def test_no_lookahead(transactions: pd.DataFrame, features: pd.DataFrame, cut: str) -> None:
    """Truncating the data at T must not move any value before T.

    This caught two real bugs while the feature was being written: a `bfill()` that pulled a later median onto
    an earlier transaction, and an unstable sort over published_at — which is full of ties — that reordered a
    ticker's history and with it the trailing median that normalises each transaction size.
    """
    published = pd.to_datetime(transactions["published_at"], utc=True)
    truncated = feat.build(_MD(transactions[published <= pd.Timestamp(cut, tz="UTC")]), index=CALENDAR)
    cut_ts = pd.Timestamp(cut)
    before = CALENDAR[CALENDAR.to_series().le(cut_ts).to_numpy()]
    for column in feat.COLUMNS:
        full_values = features.loc[before, column]
        cut_values = truncated.loc[before, column]
        same = (full_values.isna() & cut_values.isna()) | np.isclose(
            full_values.fillna(-999.0), cut_values.fillna(-999.0)
        )
        assert same.all(), f"{column} cambia prima del {cut}"


def test_every_default_feature_module_has_the_entry_point_the_builder_calls() -> None:
    """The insider module shipped with ``build`` and no ``compute``: the builder logged a warning at every run
    and its columns stayed NaN, so S21 never saw a score. No module may be listed without the entry point."""
    import importlib

    from engine.features.builder import DEFAULT_MODULES

    assert "insider" in DEFAULT_MODULES
    for name in DEFAULT_MODULES:
        assert callable(getattr(importlib.import_module(f"engine.features.{name}"), "compute", None)), name


def test_compute_follows_the_builder_contract_and_counts_a_filing_only_after_the_settlement(
    transactions: pd.DataFrame,
) -> None:
    base = pd.DataFrame(index=CALENDAR)
    out = feat.compute(_MD(transactions), pd.Timestamp("2026-10-05 22:00", tz="UTC").to_pydatetime(), base)
    assert out.index.equals(base.index) and list(out.columns) == feat.COLUMNS
    assert out.attrs["approx_columns"] == feat.COLUMNS and out[cat.INSIDER_SCORE].notna().any()
    # A deadline falls at 22:00 UTC, after that day's settlement: the builder's rule counts the filing from the
    # NEXT trading date, the calendar-day rule on the same one. The counts differ by exactly that shift.
    loose = feat.build(_MD(transactions), index=CALENDAR)
    assert (out[cat.INSIDER_N_TX] <= loose[cat.INSIDER_N_TX]).all()
    assert (out[cat.INSIDER_N_TX] < loose[cat.INSIDER_N_TX]).any()
    empty = feat.compute(_MD(transactions.iloc[:0]), pd.Timestamp("2026-10-05", tz="UTC").to_pydatetime(), base)
    assert empty[cat.INSIDER_SCORE].isna().all() and empty.attrs["approx_columns"] == []


def test_the_builder_runs_the_insider_module(transactions: pd.DataFrame) -> None:
    from engine.features.builder import FullFeatureBuilder
    from tests.synthetic import make_market_data

    md = make_market_data(n_days=400)
    md.insider = transactions
    asof = (pd.Timestamp(md.prices.index[-1]) + pd.Timedelta(hours=22)).tz_localize("UTC").to_pydatetime()
    frame = FullFeatureBuilder(modules=["price", "insider"]).build(md, asof)
    assert frame.attrs["modules_failed"] == {} and "insider" in frame.attrs["modules_ok"]
    assert frame[cat.INSIDER_N_TX].notna().all() and cat.INSIDER_SCORE in frame.attrs["approx_columns"]


def test_empty_input_gives_empty_features() -> None:
    out = feat.build(_MD(pd.DataFrame()), index=CALENDAR)
    assert out[cat.INSIDER_SCORE].isna().all()
    assert (out[cat.INSIDER_N_TX] == 0).all()


def test_latest_detail_is_traceable(transactions: pd.DataFrame) -> None:
    rows = feat.latest_detail(_MD(transactions), pd.Timestamp("2026-10-05", tz="UTC"), limit=5)
    assert rows
    for row in rows:
        assert row["ticker"] in {"COP", "OXY"}
        assert row["side"] in {"acquisto", "vendita"}
        assert row["published_at"] >= row["transaction_date"]


# ---------------------------------------------------------------- strategy


class _Regime:
    label = "Range a bassa volatilità"
    confidence = 0.6


class _Ctx:
    """Minimal MarketContext stand-in: the strategy only reads features and the regime label."""

    instrument = "BRENT"
    ts = pd.Timestamp("2026-10-05T19:30:00Z")
    regime = _Regime()

    def __init__(self, **values: float) -> None:
        self.values = values

    def f(self, name: str) -> float:
        return float(self.values.get(name, float("nan")))

    def hist(self, name: str, n: int) -> pd.Series:
        return pd.Series(dtype=float)


def _ctx(**over: float) -> _Ctx:
    base = {
        cat.INSIDER_SCORE: 0.8,
        cat.INSIDER_N_TX: 12.0,
        cat.INSIDER_BREADTH: 4.0,
        cat.INSIDER_BUY_RATIO: 0.7,
        cat.RV_YZ_21: 0.35,
        cat.ATR_14: 0.02,
    }
    base.update(over)
    return _Ctx(**base)


def test_strategy_is_silent_without_the_score() -> None:
    assert S21InsiderConviction().generate(_ctx(**{cat.INSIDER_SCORE: float("nan")})) is None


def test_strategy_is_silent_when_the_window_is_too_thin() -> None:
    """A quiet quarter is normal for this factor: silence is not a flat view."""
    assert S21InsiderConviction().generate(_ctx(**{cat.INSIDER_N_TX: 2.0})) is None


def test_strategy_needs_breadth_not_one_company() -> None:
    assert S21InsiderConviction().generate(_ctx(**{cat.INSIDER_BREADTH: 1.0})) is None


def test_strategy_goes_long_on_broad_buying() -> None:
    signal = S21InsiderConviction().generate(_ctx())
    assert signal is not None
    assert signal.direction.sign == 1
    assert signal.meta["approx"] is True
    assert "Form 4" in signal.rationale


def test_sell_side_bar_is_higher_than_the_buy_side() -> None:
    strategy = S21InsiderConviction()
    params = strategy.default_params()
    assert abs(params["sell_threshold"]) > params["buy_threshold"]
    # A score that would be a comfortable long, mirrored, is not yet a short.
    assert strategy.generate(_ctx(**{cat.INSIDER_SCORE: -0.6, cat.INSIDER_BREADTH: 4.0})) is None
    short = strategy.generate(_ctx(**{cat.INSIDER_SCORE: -0.9}))
    assert short is not None and short.direction.sign == -1


def test_conviction_stays_modest() -> None:
    """A weak, slow factor must not produce a strong probability."""
    signal = S21InsiderConviction().generate(_ctx(**{cat.INSIDER_SCORE: 1.0}))
    assert signal is not None
    assert signal.prob < 0.9
    assert not math.isnan(signal.expected_vol)
