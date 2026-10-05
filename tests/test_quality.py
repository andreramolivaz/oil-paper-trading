"""engine.data.quality: the pure checks and the documented health rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.data.base import Health
from engine.data.quality import (
    KINDS,
    QualityIssue,
    check_divergence,
    check_duplicates,
    check_gaps,
    check_nonpositive,
    check_outliers,
    check_stale,
    health_from_issues,
    overall_health,
)

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)


def daily(values: list[float], start: str = "2026-09-01") -> pd.Series:
    idx = pd.DatetimeIndex(pd.date_range(start, periods=len(values), freq="B"), name="date")
    return pd.Series(values, index=idx, dtype="float64", name="close")


# ------------------------------------------------------------------------------------------------- QualityIssue
def test_issue_validates_kind_and_severity():
    issue = QualityIssue("prices", "close", "stale", "error", "vecchio", NOW)
    assert issue.is_error and issue.to_dict()["asof"] == "2026-10-05T18:00:00Z"
    assert set(KINDS) == {"stale", "outlier", "gap", "divergence", "duplicate", "nonpositive"}
    with pytest.raises(ValueError, match="unknown issue kind"):
        QualityIssue("t", "c", "nonsense", "error", "x")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown severity"):
        QualityIssue("t", "c", "stale", "fatal", "x")  # type: ignore[arg-type]


# -------------------------------------------------------------------------------------------------------- stale
def test_check_stale_tiers():
    series = daily([100.0] * 5, start="2026-09-28")  # last business day 2026-10-02
    assert check_stale(series, NOW, timedelta(days=5)) == []
    warn = check_stale(series, NOW, timedelta(days=2))
    assert len(warn) == 1 and warn[0].severity == "warning" and warn[0].kind == "stale"
    err = check_stale(series, NOW, timedelta(days=1))
    assert err[0].severity == "error"  # beyond 2x the tolerance
    assert "2026-10-02" in err[0].message


def test_check_stale_on_an_empty_series_is_an_error():
    issues = check_stale(pd.Series(dtype="float64"), NOW, timedelta(days=1), "prices", "close")
    assert len(issues) == 1 and issues[0].severity == "error"
    assert "nessuna osservazione" in issues[0].message


def test_check_stale_ignores_trailing_nans():
    series = daily([100.0, 101.0, np.nan, np.nan], start="2026-10-01")
    issues = check_stale(series, NOW, timedelta(days=10))
    assert issues == []
    assert check_stale(series, NOW, timedelta(hours=1))[0].severity == "error"


# ------------------------------------------------------------------------------------------------------ outlier
def test_check_outliers_flags_a_jump_but_not_normal_noise():
    rng = np.random.default_rng(7)
    values = list(rng.normal(0.0, 0.01, 200))
    assert check_outliers(daily(values)) == []
    values[150] = 0.9  # a 90 % daily return
    issues = check_outliers(daily(values))
    assert len(issues) == 1
    assert issues[0].severity == "warning" and issues[0].kind == "outlier"
    assert issues[0].extra["value"] == pytest.approx(0.9)
    assert issues[0].extra["z"] > 6


def test_check_outliers_needs_enough_history():
    assert check_outliers(daily([0.01, -0.5, 0.02])) == []


def test_check_outliers_survives_a_flat_window():
    assert check_outliers(daily([0.0] * 100)) == []


# ---------------------------------------------------------------------------------------------------------- gap
def test_check_gaps_uses_the_ice_business_calendar():
    # a weekend is not a gap
    idx = pd.DatetimeIndex(["2026-10-02", "2026-10-05"])
    assert check_gaps(idx) == []
    # 2026-10-05 -> 2026-10-13 skips 5 business days
    idx = pd.DatetimeIndex(["2026-10-05", "2026-10-13"])
    issues = check_gaps(idx, max_business_gap=3)
    assert len(issues) == 1 and issues[0].kind == "gap" and issues[0].severity == "warning"
    assert issues[0].extra["missing_business_days"] == 5
    assert check_gaps(idx, max_business_gap=9) == []
    assert check_gaps(pd.DatetimeIndex(["2026-10-05"])) == []


def test_check_gaps_skips_uk_bank_holidays():
    # Christmas 2026 (Fri 25 Dec) and Boxing Day (Mon 28 Dec observed) are ICE holidays
    idx = pd.DatetimeIndex(["2026-12-24", "2026-12-29"])
    assert check_gaps(idx, max_business_gap=0) == []


# -------------------------------------------------------------------------------------------------- divergence
def test_check_divergence_requires_both_limits():
    a = daily([100.0, 101.0, 102.0])
    b = a + 0.5  # 0.5 $ on a 100 $ barrel: absolute limit not exceeded
    assert check_divergence(a, b, max_abs=1.0, max_rel=0.001) == []
    c = a + 2.0  # 2 % and 2 $: both limits exceeded
    issues = check_divergence(a, c, max_abs=1.0, max_rel=0.01)
    assert len(issues) == 3 and all(i.severity == "error" and i.kind == "divergence" for i in issues)
    assert issues[0].extra["abs"] == pytest.approx(2.0)
    # the relative limit alone is not enough
    assert check_divergence(a, c, max_abs=5.0, max_rel=0.01) == []


def test_check_divergence_without_common_dates_warns():
    a = daily([100.0], start="2026-09-01")
    b = daily([100.0], start="2025-09-01")
    issues = check_divergence(a, b, 1.0, 0.01, names=("eia", "fred"))
    assert len(issues) == 1 and issues[0].severity == "warning"
    assert "nessuna data in comune" in issues[0].message


def test_check_divergence_on_real_brent_values():
    """EIA and FRED serve the same EIA series: the 2026-09-29 print must agree to the cent."""
    eia = pd.Series([119.97, 113.96], index=pd.DatetimeIndex(["2026-09-28", "2026-09-29"]))
    fred = pd.Series([119.97, 113.96], index=pd.DatetimeIndex(["2026-09-28", "2026-09-29"]))
    assert check_divergence(eia, fred, max_abs=0.01, max_rel=0.0001) == []


# ----------------------------------------------------------------------------------- nonpositive / duplicates
def test_check_nonpositive():
    series = daily([100.0, 0.0, -37.63])
    issues = check_nonpositive(series, "prices", "close")
    assert len(issues) == 2 and all(i.severity == "error" and i.kind == "nonpositive" for i in issues)
    assert issues[-1].extra["value"] == pytest.approx(-37.63)
    assert check_nonpositive(daily([100.0, 101.0])) == []


def test_check_duplicates():
    idx = pd.DatetimeIndex(["2026-10-01", "2026-10-01", "2026-10-02"])
    issues = check_duplicates(idx, "cot")
    assert len(issues) == 1 and issues[0].severity == "error" and issues[0].kind == "duplicate"
    assert issues[0].extra["n_duplicates"] == 1
    assert check_duplicates(pd.DatetimeIndex(["2026-10-01", "2026-10-02"])) == []


# ------------------------------------------------------------------------------------------------------- health
def test_health_rules():
    green = health_from_issues("brent_front", [], NOW, data_asof=NOW, rows=10)
    assert green.status is Health.GREEN and green.fallback_used is None

    warn = QualityIssue("prices", "close", "outlier", "warning", "z 7")
    yellow = health_from_issues("brent_front", [warn], NOW, rows=10)
    assert yellow.status is Health.YELLOW and "[warning]" in yellow.message

    # a fallback alone is enough for yellow
    fallback = health_from_issues("brent_spot", [], NOW, fallback_used="eia_xls", rows=10)
    assert fallback.status is Health.YELLOW and "fallback: eia_xls" in fallback.message

    err = QualityIssue("prices", "close", "stale", "error", "ultimo dato 2026-09-01")
    red = health_from_issues("brent_front", [err, warn], NOW, rows=10)
    assert red.status is Health.RED

    unavailable = health_from_issues("vix", [], NOW, available=False, message="HTTP 429")
    assert unavailable.status is Health.RED and "HTTP 429" in unavailable.message


def test_health_serialises_and_aggregates():
    healths = [
        health_from_issues("a", [], NOW, rows=1),
        health_from_issues("b", [], NOW, fallback_used="x", rows=1),
    ]
    assert overall_health(healths) is Health.YELLOW
    healths.append(health_from_issues("c", [], NOW, available=False))
    assert overall_health(healths) is Health.RED
    assert overall_health([health_from_issues("a", [], NOW)]) is Health.GREEN
    assert overall_health([]) is Health.RED  # no information is not good news
    d = healths[0].to_dict()
    assert d["status"] == "green" and d["checked_at"] == "2026-10-05T18:00:00Z"


def test_health_accepts_naive_timestamps_as_utc():
    h = health_from_issues("a", [], datetime(2026, 10, 5, 18, 0), data_asof=datetime(2026, 10, 5))
    assert h.checked_at.tzinfo is not None
    assert h.to_dict()["data_asof"] == "2026-10-05T00:00:00Z"
