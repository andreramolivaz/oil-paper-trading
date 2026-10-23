"""How precisely a Sharpe ratio is known, and what a reader could buy instead (docs/RESEARCH.md 15 G).

The numbers pinned here are the ones the chapter publishes for the live payload of 23 October 2026: a Sharpe of
1.23 measured over 13.7 years has a standard error of 0.358, an interval of 0.53-1.93 and an honest t of 3.44,
against the 4.55 the page used to show. The series in the benchmark tests are SYNTHETIC; only the arithmetic of
``uncertainty`` is a claim about the real payload.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.core.config import RiskConfig
from engine.desk.backtest import run_backtest
from engine.desk.book import BookConfig
from engine.desk.report import (
    benchmark_note,
    portfolio_block,
    portfolio_stats,
    uncertainty,
    window_benchmarks,
)
from tests.synthetic import make_desk_data

RISK = RiskConfig.load()


def test_the_standard_error_of_a_sharpe_is_the_published_one():
    out = uncertainty(1.23, 13.7)
    assert out["sharpe_se"] == pytest.approx(0.358, abs=0.001)
    assert out["sharpe_ci"] == [0.53, 1.93]
    assert out["t_sharpe"] == pytest.approx(3.44, abs=0.01)
    # the t the page used to show is Sharpe x sqrt(years), and it is the optimistic one
    assert 1.23 * math.sqrt(13.7) == pytest.approx(4.55, abs=0.01)
    assert out["t_sharpe"] < 1.23 * math.sqrt(13.7)


def test_a_high_sharpe_over_a_short_window_says_less_not_more():
    """The point of the whole section: 2.99 over 0.6 years is one standard error from zero (15 E)."""
    short = uncertainty(2.99, 151 / 252)
    assert short["sharpe_se"] == pytest.approx(3.02, abs=0.02)
    assert short["t_sharpe"] < 1.05  # t about 0.99: the level is not knowable
    assert short["sharpe_ci"][0] < 0 < short["sharpe_ci"][1]
    # and the error GROWS with the ratio at the same length of history
    assert uncertainty(3.0, 5.0)["sharpe_se"] > uncertainty(0.5, 5.0)["sharpe_se"]
    # ... while more years shrink it
    assert uncertainty(1.0, 40.0)["sharpe_se"] < uncertainty(1.0, 10.0)["sharpe_se"]


@pytest.mark.parametrize("args", [(None, 10.0), (1.0, None), (1.0, 0.0), (float("nan"), 10.0), (1.0, -3.0)])
def test_no_interval_without_something_to_measure(args):
    assert uncertainty(*args) == {}


def test_the_note_says_in_words_whether_the_intervals_can_be_told_apart():
    overlapping = {
        "stats": {"sharpe_ci": [0.53, 1.93]},
        "benchmarks": {
            "SPY": {"label": "Broad US equities (SPY) bought and held", "stats": {"sharpe_ci": [0.27, 1.52]}}
        },
    }
    note = benchmark_note(overlapping)
    assert note and "cannot say which Sharpe" in note and "equities" in note
    apart = {
        "stats": {"sharpe_ci": [2.0, 3.0]},
        "benchmarks": {"SPY": {"label": "Broad US equities (SPY) bought and held", "stats": {"sharpe_ci": [0.2, 1.0]}}},
    }
    assert benchmark_note(apart) == "Over this window the interval does not overlap any of the alternatives'."
    assert benchmark_note({"stats": {}, "benchmarks": {}}) is None


def _with_funds(data, seed: int = 11):
    """The synthetic desk plus two price-only series, so the benchmark block has something to read."""
    base = data.series["BNO"]
    rng = np.random.default_rng(seed)
    for vehicle, drift, vol in (("SPY", 0.0005, 0.010), ("TLT", 0.0000, 0.009)):
        rets = pd.Series(rng.normal(drift, vol, size=len(base.returns)), index=base.returns.index)
        data.series[vehicle] = dataclasses.replace(base, vehicle=vehicle, returns=rets)
    return data


def test_the_portfolio_carries_its_interval_and_the_alternatives():
    data = _with_funds(make_desk_data(n_days=800))
    books = [
        BookConfig(id="a", name="A", vehicle="BNO", vol_target=0.12, max_leverage=2.0, role="core"),
        BookConfig(id="b", name="B", vehicle="BNO", vol_target=0.25, max_leverage=2.0, role="core"),
    ]
    result = run_backtest(data, books, RISK, ruin=False)
    stats = portfolio_stats(result, ["a", "b"], "both")
    assert stats is not None
    ci = stats["stats"]["sharpe_ci"]
    assert ci[0] < stats["stats"]["sharpe"] < ci[1]
    # the corrected t is never above the one the page used to show (equal within the payload's rounding)
    assert stats["stats"]["t_sharpe"] <= stats["stats"]["t_stat"] + 0.011

    block = portfolio_block(result, books, data)
    rows = block["core"]["benchmarks"]
    assert set(rows) == {"SPY", "mix_60_40", "TLT"}
    for row in rows.values():
        assert row["stats"]["sharpe_ci"] and row["stats"]["years"] == pytest.approx(
            block["core"]["stats"]["years"], abs=0.02
        )
    assert block["core"]["benchmark_note"]
    # the 60/40 is a real mix, not a copy of either leg
    assert rows["mix_60_40"]["stats"]["vol"] < rows["SPY"]["stats"]["vol"]
    # and without the data there is nothing extra: callers that do not pass it are unaffected
    assert "benchmarks" not in portfolio_block(result, books)["core"]


def test_no_benchmarks_without_the_two_series():
    data = make_desk_data(n_days=400)
    assert window_benchmarks(data, date(2024, 1, 2), date(2025, 1, 2)) == {}
    data = _with_funds(data)
    first = data.series["SPY"].returns.index[0].date()
    assert window_benchmarks(data, first, first + pd.Timedelta(days=20).to_pytimedelta()) == {}  # under 60 days
