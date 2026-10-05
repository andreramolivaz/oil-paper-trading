"""Tests for engine.validation (brief §12). All data here is SYNTHETIC (seeded numpy generators) and offline."""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from engine.validation import (
    PurgedKFold,
    TrialLog,
    breakeven_cost,
    cost_sensitivity,
    cpcv_plan,
    cusum_filter,
    decide_lifecycle,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    explain,
    haircut_sharpe,
    min_live_sample_size,
    n_backtest_paths,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    structural_break_test,
    walk_forward_splits,
)
from engine.validation import metrics as m
from engine.validation.dsr import EULER_GAMMA
from engine.validation.lifecycle import Thresholds, checks


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------------------------------------------
def test_sharpe_vol_cagr_by_hand():
    r = [0.01, -0.01, 0.02]
    mean = 0.02 / 3
    var = ((0.01 - mean) ** 2 + (-0.01 - mean) ** 2 + (0.02 - mean) ** 2) / 2
    assert m.sharpe(r) == pytest.approx(mean / math.sqrt(var) * math.sqrt(252))
    assert m.annualised_vol(r) == pytest.approx(math.sqrt(var) * math.sqrt(252))
    total = 1.01 * 0.99 * 1.02
    assert m.cagr(r) == pytest.approx(total ** (252 / 3) - 1)
    assert math.isnan(m.sharpe([0.01, 0.01, 0.01]))  # zero variance
    assert math.isnan(m.sharpe([0.01]))
    assert m.sharpe([0.01, np.nan, -0.01, 0.02]) == m.sharpe(r)  # NaN dropped, never filled


def test_sortino_by_hand():
    r = np.array([0.02, -0.01, 0.03, -0.02])
    downside = math.sqrt(np.mean(np.minimum(r, 0.0) ** 2))
    assert m.sortino(r) == pytest.approx(r.mean() / downside * math.sqrt(252))
    assert m.sortino([0.01, 0.02]) == math.inf


def test_drawdown_depth_duration_and_recovery():
    dd = m.max_drawdown([0.1, -0.5, 0.2, 0.5])  # equity 1, 1.1, 0.55, 0.66, 0.99: never back above 1.1
    assert dd.depth == pytest.approx(-0.5)
    assert (dd.peak_idx, dd.trough_idx, dd.recovery_idx) == (1, 2, None)
    assert dd.duration == 3 and dd.longest_underwater == 3 and not dd.recovered
    dd2 = m.max_drawdown([0.1, -0.5, 0.2, 0.5, 0.2])  # 0.99 * 1.2 = 1.188 >= 1.1: recovered at point 5
    assert dd2.recovery_idx == 5 and dd2.duration == 4 and dd2.longest_underwater == 4
    flat = m.max_drawdown([0.01, 0.02, 0.0])
    assert flat.depth == 0.0 and flat.duration == 0
    # the longest underwater spell need not be the deepest one
    r = [-0.01] + [0.0005] * 30 + [-0.3, 0.6]
    dd3 = m.max_drawdown(r)
    assert dd3.depth == pytest.approx(-0.3) and dd3.longest_underwater > dd3.duration
    assert m.calmar([0.1, -0.5, 0.2, 0.5]) == pytest.approx(m.cagr([0.1, -0.5, 0.2, 0.5]) / 0.5)


def test_trade_statistics_and_turnover():
    trades = [10, -5, 20, -5]
    assert m.hit_rate(trades) == 0.5
    assert m.profit_factor(trades) == pytest.approx(3.0)
    assert m.profit_factor([1.0, 2.0]) == math.inf
    assert math.isnan(m.profit_factor([]))
    # positions 0 -> 1 -> -1 -> -1 -> 0 : |Δ| = 1 + 2 + 0 + 1 = 4 over 4 periods, annualised
    assert m.turnover([1, -1, -1, 0]) == pytest.approx(4 / 4 * 252)


def test_skew_kurtosis_conventions():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(20_000)
    assert abs(m.skew(x)) < 0.1
    assert m.kurtosis(x) == pytest.approx(3.0, abs=0.15)  # Pearson: 3 for a Gaussian
    assert m.kurtosis(x, excess=True) == pytest.approx(m.kurtosis(x) - 3.0)


def test_psr_hand_example_sr_1585_over_24_months():
    # Bailey & López de Prado (2012) style example: annualised SR 1.585 observed on 24 monthly returns,
    # Gaussian returns (skew 0, kurtosis 3). Monthly SR = 1.585 / sqrt(12).
    sr = 1.585 / math.sqrt(12)
    z = sr * math.sqrt(24 - 1) / math.sqrt(1 - 0 * sr + (3 - 1) / 4 * sr**2)
    assert probabilistic_sharpe_ratio(sr, 0.0, 24, 0.0, 3.0) == pytest.approx(_phi(z), abs=1e-12)
    assert probabilistic_sharpe_ratio(sr, 0.0, 24, 0.0, 3.0) == pytest.approx(0.9816, abs=5e-4)
    # negative skew and fat tails (the paper's point) lower the PSR; a longer track record raises it
    assert probabilistic_sharpe_ratio(sr, 0.0, 24, -2.448, 10.164) < probabilistic_sharpe_ratio(sr, 0.0, 24)
    assert probabilistic_sharpe_ratio(sr, 0.0, 48) > probabilistic_sharpe_ratio(sr, 0.0, 24)
    assert probabilistic_sharpe_ratio(0.3, 0.3, 100) == pytest.approx(0.5)
    assert math.isnan(probabilistic_sharpe_ratio(0.3, 0.0, 1))


def test_min_track_record_length_consistent_with_psr():
    sr, sk, ku = 0.1, -0.5, 5.0
    n_min = m.min_track_record_length(sr, 0.0, sk, ku, prob=0.95)
    assert n_min == pytest.approx(1 + (1 - sk * sr + (ku - 1) / 4 * sr**2) * (stats.norm.ppf(0.95) / sr) ** 2)
    assert probabilistic_sharpe_ratio(sr, 0.0, math.ceil(n_min), sk, ku) >= 0.95
    assert probabilistic_sharpe_ratio(sr, 0.0, math.floor(n_min) - 1, sk, ku) < 0.95
    assert m.min_track_record_length(0.0, 0.0) == math.inf


def test_summary_keys_and_values():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0005, 0.01, 500)  # synthetic daily returns
    s = m.summary(r, trades=[10, -5, 20, -5], positions=np.ones(500))
    for key in (
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown",
        "max_drawdown_duration",
        "psr",
        "min_trl",
        "skew",
        "kurtosis",
    ):
        assert key in s
    assert s["n_obs"] == 500 and s["n_trades"] == 4 and s["profit_factor"] == pytest.approx(3.0)
    assert s["sharpe"] == pytest.approx(m.sharpe(r))
    assert s["turnover"] == pytest.approx(252 / 500)  # one entry, never changed
    df = pd.DataFrame({"pnl": [1.0, -2.0]})
    assert m.summary(r, trades=df)["n_trades"] == 2


# ---------------------------------------------------------------------------------------------------------------
# purged k-fold / walk-forward
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("n", "n_splits", "h", "embargo_pct"), [(100, 5, 3, 0.05), (257, 7, 10, 0.02), (60, 3, 0, 0.0)]
)
def test_purged_kfold_no_label_overlap_and_embargo(n, n_splits, h, embargo_pct):
    cv = PurgedKFold(n_splits=n_splits, embargo_pct=embargo_pct, label_horizon=h)
    embargo = math.ceil(embargo_pct * n)
    folds = list(cv.split(n))
    assert len(folds) == n_splits == cv.get_n_splits()
    covered = np.concatenate([t for _, t in folds])
    assert np.array_equal(np.sort(covered), np.arange(n))  # test folds partition the sample
    for train, test in folds:
        assert train.dtype == np.int64 and test.dtype == np.int64
        assert np.intersect1d(train, test).size == 0
        # label window of observation i is [i, i+h]: no train window may contain a test index and vice versa
        for i in train:
            assert not np.any((test >= i) & (test <= i + h)), f"train label {i} overlaps test"
            assert not np.any((test <= i) & (test + h >= i)), f"test label overlaps train {i}"
        # embargo: the first training index after the test block is at least h + embargo + 1 past its end
        after = train[train > test.max()]
        if after.size:
            assert after.min() >= test.max() + h + embargo + 1
        if embargo > 0 and test.max() + h + embargo + 1 < n:
            assert after.min() == test.max() + h + embargo + 1  # embargo is not larger than required either
        before = train[train < test.min()]
        if before.size:
            assert before.max() <= test.min() - h - 1


def test_purged_kfold_validation():
    with pytest.raises(ValueError):
        PurgedKFold(n_splits=1)
    with pytest.raises(ValueError):
        PurgedKFold(embargo_pct=1.0)
    with pytest.raises(ValueError):
        list(PurgedKFold(n_splits=10).split(5))


def test_walk_forward_expanding_and_purged():
    splits = list(walk_forward_splits(n=100, train_min=40, test_len=20, step=20, label_horizon=5))
    assert len(splits) == 3
    starts = [t[0] for _, t in splits]
    assert starts == [40, 60, 80]
    for train, test in splits:
        assert train[0] == 0  # expanding window is anchored
        assert train.max() == test.min() - 5 - 1  # last h training labels purged
        assert test.size == 20
    rolling = list(walk_forward_splits(n=100, train_min=40, test_len=20, expanding=False))
    assert all(tr.size == 40 for tr, _ in rolling)
    partial = list(walk_forward_splits(n=95, train_min=40, test_len=20))
    assert partial[-1][1].tolist() == list(range(80, 95))  # last block may be shorter
    assert list(walk_forward_splits(n=30, train_min=40, test_len=10)) == []


# ---------------------------------------------------------------------------------------------------------------
# CPCV plan and PBO (CSCV)
# ---------------------------------------------------------------------------------------------------------------
def test_cpcv_plan_counts_paths_and_purging():
    plan = cpcv_plan(n=120, n_groups=6, k_test=2, label_horizon=3, embargo_pct=0.05)
    assert plan.n_splits == math.comb(6, 2) == 15
    assert plan.n_paths == n_backtest_paths(6, 2) == 5
    # a path draws each group from a split that tests it; a split may fill up to k groups of the same path
    # (fig. 12.1 in López de Prado), and every split feeds exactly k path-groups overall
    assert all(math.ceil(6 / 2) <= len(set(row)) <= 6 for row in plan.paths.tolist())
    assert np.bincount(plan.paths.ravel(), minlength=15).tolist() == [2] * 15
    # each group g is in the test set of the split that fills it
    for p in range(plan.n_paths):
        for g in range(6):
            assert g in plan.combos[plan.paths[p, g]]
    embargo = math.ceil(0.05 * 120)
    for train, test, groups in plan.iter_splits():
        assert len(groups) == 2 and np.intersect1d(train, test).size == 0
        for i in train:
            assert np.all(np.abs(test - i) > 3)
        after = train[train > test.max()]
        if after.size:
            assert after.min() >= test.max() + 3 + embargo + 1


def test_cpcv_stitch_paths():
    plan = cpcv_plan(n=30, n_groups=5, k_test=2)
    oos = {s: np.full(plan.test_indices(s).size, float(s)) for s in range(plan.n_splits)}
    paths = plan.stitch(oos)
    assert paths.shape == (plan.n_paths, 30) and not np.isnan(paths).any()
    for p in range(plan.n_paths):
        for g in range(5):
            assert np.all(paths[p, plan.groups[g]] == plan.paths[p, g])
    partial = plan.stitch({0: oos[0]})
    assert np.isnan(partial).any()


def test_pbo_near_half_for_noise_and_near_zero_for_dominant_variant():
    rng = np.random.default_rng(1)
    noise = rng.normal(0.0, 0.01, size=(2000, 50))  # 50 synthetic variants of pure noise
    res = probability_of_backtest_overfitting(noise, n_partitions=16)
    assert res.n_combinations == math.comb(16, 8) == 12870 and res.logits.shape == (12870,)
    assert 0.3 <= res.pbo <= 0.7
    assert res.selected.min() >= 0 and res.selected.max() < 50
    planted = noise.copy()
    planted[:, 7] += 0.002  # daily SR ~0.2: a genuinely dominant variant
    res2 = probability_of_backtest_overfitting(planted, n_partitions=16)
    assert res2.pbo < 0.1
    assert np.bincount(res2.selected).argmax() == 7
    assert res2.dominance["first_order"] and res2.dominance["second_order"]
    assert res2.prob_oos_loss < 0.05
    d = res2.to_dict()
    assert set(d) >= {"pbo", "slope", "intercept", "logit_quantiles", "dominance", "n_combinations"}


def test_pbo_rejects_nan_and_bad_shapes():
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(np.full((100, 3), np.nan))
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(np.zeros((100, 3)), n_partitions=7)
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(np.zeros((100, 1)))
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting(np.zeros(100))


def test_pbo_full_scale_under_time_budget():
    rng = np.random.default_rng(7)
    big = rng.normal(0.0, 0.01, size=(6000, 200))  # T=6000, M=200 synthetic
    t0 = time.perf_counter()
    res = probability_of_backtest_overfitting(big, n_partitions=16)
    elapsed = time.perf_counter() - t0
    assert elapsed < 60.0, f"PBO took {elapsed:.1f}s"
    assert res.n_combinations == 12870 and 0.0 <= res.pbo <= 1.0


# ---------------------------------------------------------------------------------------------------------------
# Deflated Sharpe Ratio and the trials registry
# ---------------------------------------------------------------------------------------------------------------
def test_expected_max_sharpe_formula():
    n, var = 100, 0.04
    z1 = stats.norm.ppf(1 - 1 / n)
    z2 = stats.norm.ppf(1 - 1 / (n * math.e))
    assert expected_max_sharpe(n, var) == pytest.approx(0.2 * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))
    assert expected_max_sharpe(1, 1.0) == 0.0 and expected_max_sharpe(10, 0.0) == 0.0
    assert expected_max_sharpe(1000, var) > expected_max_sharpe(100, var)


def test_dsr_noise_trials_fail_and_strong_strategy_passes():
    rng = np.random.default_rng(2)
    trials = rng.normal(0.0, 0.01, size=(1000, 100))  # 100 synthetic noise trials, 1000 days each
    srs = trials.mean(axis=0) / trials.std(axis=0, ddof=1)
    selected = float(srs.max())
    assert probabilistic_sharpe_ratio(selected, 0.0, 1000) > 0.95  # naive PSR is fooled by the selection ...
    prob, sr0 = deflated_sharpe_ratio(srs, selected, n_obs=1000, skew=0.0, kurt=3.0)
    assert prob < 0.95 and sr0 > 0  # ... the DSR is not
    strong, sr0_single = deflated_sharpe_ratio([2.0], 2.0, n_obs=2000, skew=0.0, kurt=3.0, periods_per_year=252)
    assert strong > 0.95 and sr0_single == 0.0
    # annualised and per-observation inputs give the same answer
    daily = 2.0 / math.sqrt(252)
    assert deflated_sharpe_ratio([daily], daily, 2000)[0] == pytest.approx(strong)
    # a larger registry (unknown Sharpes) can only deflate further
    prob_more, _ = deflated_sharpe_ratio(srs, selected, 1000, n_trials=500)
    assert prob_more < prob
    with pytest.raises(ValueError):
        deflated_sharpe_ratio([], 1.0, 100)


def test_haircut():
    h = haircut_sharpe(1.0, 0.4)
    assert h.adjusted == pytest.approx(0.6) and h.haircut == pytest.approx(0.4)
    assert haircut_sharpe(0.3, 0.5).adjusted == 0.0 and haircut_sharpe(0.3, 0.5).haircut == 1.0


def test_trial_log_registers_every_variant(tmp_store):
    log = TrialLog(tmp_store)
    asof = datetime(2026, 10, 5, 12, tzinfo=UTC)
    log.log("S1", {"lookback": 20, "vol": 0.1}, sharpe=0.8, n_obs=1500, skew=-0.2, kurt=4.1, asof=asof)
    log.log("S1", {"vol": 0.1, "lookback": 20}, sharpe=0.81, n_obs=1500, skew=-0.2, kurt=4.1, asof=asof)  # re-run
    log.log("S1", {"lookback": 60}, sharpe=0.3, n_obs=1500, skew=0.0, kurt=3.0, asof=asof, note="wide")
    log.log("S2", {"k": 1}, sharpe=1.5, n_obs=800, skew=0.1, kurt=3.2, asof=asof)
    assert tmp_store.path("trials.jsonl").exists()
    assert log.n_trials("S1") == 2  # distinct parameter sets (key order does not matter)
    assert log.n_trials("S1", distinct=False) == 3  # nothing is ever deleted
    assert log.n_trials() == 3
    assert log.sharpes("S1").tolist() == [0.81, 0.3]  # latest value per variant
    rec = log.records("S2")[0]
    assert rec["asof"] == "2026-10-05T12:00:00Z" and rec["params_hash"] == TrialLog.params_hash({"k": 1})
    assert log.records("S1")[2]["note"] == "wide"
    prob, sr0 = log.deflate("S1", selected_sharpe=0.81, n_obs=1500, skew=-0.2, kurt=4.1, periods_per_year=252)
    expected = deflated_sharpe_ratio([0.81, 0.3], 0.81, 1500, -0.2, 4.1, periods_per_year=252)
    assert (prob, sr0) == pytest.approx(expected)


# ---------------------------------------------------------------------------------------------------------------
# CUSUM and structural break
# ---------------------------------------------------------------------------------------------------------------
def test_cusum_recursion_by_hand():
    # standardised z = (r - mu)/sigma with mu=0, sigma=1; k=0.5: S = max(0, S - z - k)
    z = [0.0, -1.0, -1.0, 0.5, -2.0, -2.0, -2.0, -2.0]
    res = cusum_filter(z, mu_bt=0.0, sigma_bt=1.0, k=0.5, h=5.0)
    assert res.statistic.tolist() == pytest.approx([0.0, 0.5, 1.0, 0.0, 1.5, 3.0, 4.5, 6.0])
    assert res.alarms == [7] and res.triggered and res.first_alarm == 7
    idx = pd.date_range("2026-01-01", periods=8, freq="D")
    res_dt = cusum_filter(pd.Series(z, index=idx), 0.0, 1.0)
    assert res_dt.alarms == [pd.Timestamp("2026-01-08")]
    assert res_dt.to_dict()["alarms"] == ["2026-01-08 00:00:00"]
    # reset after alarm: the statistic restarts from 0, no reset keeps climbing
    z2 = [*z, -2.0]
    assert cusum_filter(z2, 0.0, 1.0).statistic.iloc[-1] == pytest.approx(1.5)
    assert cusum_filter(z2, 0.0, 1.0, reset_on_alarm=False).statistic.iloc[-1] == pytest.approx(7.5)


def test_cusum_fires_on_planted_break_not_on_stationary_data():
    mu, sigma = 0.0005, 0.01
    rng = np.random.default_rng(3)
    live = np.concatenate([rng.normal(mu, sigma, 300), rng.normal(mu - 1.5 * sigma, sigma, 100)])  # break at 300
    res = cusum_filter(live, mu, sigma)
    assert res.triggered and 300 <= res.first_alarm <= 330
    assert all(a >= 300 for a in res.alarms)
    rng = np.random.default_rng(1)
    stationary = rng.normal(mu, sigma, 250)  # same distribution as the backtest
    assert not cusum_filter(stationary, mu, sigma).triggered
    with pytest.raises(ValueError):
        cusum_filter(stationary, mu, 0.0)


def test_min_live_sample_size_by_hand():
    z = stats.norm.ppf(0.95) + stats.norm.ppf(0.8)
    assert min_live_sample_size(0.5) == math.ceil((z / 0.5) ** 2) == 25
    assert min_live_sample_size(0.5, n_bt=5000) >= 25
    assert min_live_sample_size(0.5, n_bt=10) == math.inf  # backtest too short to ever detect the effect


def test_structural_break_test():
    mu, sigma, n_bt = 0.001, 0.01, 2000
    rng = np.random.default_rng(4)
    decayed = rng.normal(-0.001, sigma, 1000)  # synthetic live P&L whose edge has turned negative
    bt = structural_break_test(decayed, mu, sigma, n_bt)
    assert bt.n_live == 1000 and bt.effect_size == pytest.approx(0.1)
    assert bt.n_required == min_live_sample_size(0.1, n_bt=n_bt) == 895
    assert bt.p_value < 0.05 and bt.t_stat < 0 and bt.sufficient and bt.significant
    # significance requires the power-based sample size: the same evidence on 20 days is "insufficient"
    short = structural_break_test(decayed[:20], mu, sigma, n_bt)
    assert not short.sufficient and not short.significant
    same = structural_break_test(rng.normal(mu, sigma, 1500), mu, sigma, n_bt)
    assert same.sufficient and not same.significant and same.p_value > 0.05
    assert set(bt.to_dict()) >= {"t_stat", "dof", "p_value", "n_required", "significant"}
    tiny = structural_break_test([0.01], mu, sigma, n_bt)
    assert math.isnan(tiny.p_value) and not tiny.significant


# ---------------------------------------------------------------------------------------------------------------
# costs
# ---------------------------------------------------------------------------------------------------------------
def test_cost_sensitivity_arithmetic_by_hand():
    gross = [0.01, -0.005, 0.02]
    turn = [1.0, 0.0, 2.0]
    table = cost_sensitivity(gross, cost_per_turn=0.001, turnover_series=turn, multipliers=(0, 1, 2))
    assert table.index.tolist() == [0.0, 1.0, 2.0]
    net1 = [0.009, -0.005, 0.018]
    net2 = [0.008, -0.005, 0.016]
    assert table.loc[1.0, "total_cost"] == pytest.approx(0.003)
    assert table.loc[2.0, "total_cost"] == pytest.approx(0.006)
    assert table.loc[1.0, "cost_per_turn_bps"] == pytest.approx(10.0)
    assert table.loc[0.0, "sharpe"] == pytest.approx(m.sharpe(gross))
    assert table.loc[1.0, "sharpe"] == pytest.approx(m.sharpe(net1))
    assert table.loc[2.0, "cagr"] == pytest.approx(m.cagr(net2))
    assert table.loc[2.0, "max_drawdown"] == pytest.approx(m.max_drawdown(net2).depth)
    assert table.loc[2.0, "sharpe"] < table.loc[1.0, "sharpe"] < table.loc[0.0, "sharpe"]
    assert breakeven_cost(gross, turn) == pytest.approx(0.025 / 3 * 1e4)  # 83.33 bps per unit turnover
    assert breakeven_cost([0.01, 0.01], [0.0, 0.0]) == math.inf
    assert breakeven_cost([-0.01, 0.0], [1.0, 1.0]) < 0
    with pytest.raises(ValueError):
        cost_sensitivity(gross, 0.001, [1.0, 2.0])


# ---------------------------------------------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------------------------------------------
GOOD = {
    "validated": True,
    "dsr_prob": 0.97,
    "pbo": 0.31,
    "oos_sharpe_double_cost": 0.42,
    "n_trades": 143,
    "years": 2.0,
    "cusum_alarm": False,
}


def test_lifecycle_branches():
    assert decide_lifecycle(GOOD) == "active"
    assert decide_lifecycle({**GOOD, "n_trades": 50, "years": 3.0}) == "active"  # 3 years is enough
    assert decide_lifecycle({**GOOD, "n_trades": 50, "years": 2.9}) == "incubation"
    assert decide_lifecycle({**GOOD, "dsr_prob": 0.95}) == "incubation"  # strict >
    assert decide_lifecycle({**GOOD, "pbo": 0.5}) == "incubation"  # strict <
    assert decide_lifecycle({**GOOD, "pbo": 0.62}) == "incubation"
    assert decide_lifecycle({**GOOD, "oos_sharpe_double_cost": 0.0}) == "incubation"
    assert decide_lifecycle({**GOOD, "oos_sharpe_double_cost": float("nan")}) == "incubation"  # missing fails
    assert decide_lifecycle({**GOOD, "validated": False}) == "research"  # never promote without the report
    assert decide_lifecycle({k: v for k, v in GOOD.items() if k != "validated"}) == "research"
    assert decide_lifecycle({**GOOD, "cusum_alarm": True}) == "retired"
    assert decide_lifecycle({**GOOD, "status": "retired"}) == "retired"  # sticky until reset by hand
    assert decide_lifecycle({"validated": True}) == "incubation"
    assert decide_lifecycle({}) == "research"
    strict = Thresholds(dsr_min=0.99)
    assert decide_lifecycle(GOOD, strict) == "incubation"
    assert checks({**GOOD, "pbo": 0.62}) == {"dsr": True, "pbo": False, "oos_double_cost": True, "history": True}


def test_lifecycle_explain_italian():
    assert explain({**GOOD, "pbo": 0.62}) == "Peso zero: PBO 0,62 > 0,5"
    assert explain({**GOOD, "pbo": 0.5}) == "Peso zero: PBO 0,50 = 0,5"
    s = explain({**GOOD, "dsr_prob": 0.9, "n_trades": 50})
    assert s.startswith("Peso zero: DSR 0,90 < 0,95; 50 operazioni < 100 e 2,0 anni < 3")
    assert explain({**GOOD, "oos_sharpe_double_cost": -0.1}) == "Peso zero: Sharpe OOS a costi doppi -0,10 < 0"
    assert (
        explain({"validated": True})
        == "Peso zero: DSR n/d; PBO n/d; Sharpe OOS a costi doppi n/d; n/d operazioni < 100 e n/d anni < 3"
    )
    active = explain(GOOD)
    assert active.startswith(
        "Attiva: DSR 0,97 > 0,95; PBO 0,31 < 0,5; Sharpe OOS a costi doppi 0,42 > 0; 143 operazioni"
    )
    assert explain({**GOOD, "validated": False}).startswith("Ricerca")
    assert explain({**GOOD, "cusum_alarm": True}).startswith("Ritirata: allarme CUSUM")
    assert explain({**GOOD, "status": "retired"}).startswith("Ritirata")
