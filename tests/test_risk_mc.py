"""VaR/ES, Monte Carlo risk of ruin and stress tests.

Data policy of this file: the return samples are **SYNTHETIC** (seeded numpy draws, labelled as such in every
test name/comment) because the point is the mechanics — monotonicity in leverage, the effect of the injected
scenarios, determinism, runtime.  The two tests that make a *market* claim (the Gulf War one-day crash) run only
when a real fixture is available and are skipped otherwise; they are never asserted on synthetic data.
"""

from __future__ import annotations

import dataclasses
import math
import time
from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.core.config import RiskConfig
from engine.portfolio.leverage import compute_leverage, es99_one_day, student_t_es
from engine.portfolio.montecarlo import (
    SCENARIO_SPECS,
    calibrate,
    calibration_markdown,
    constant_policy,
    policy_from_risk,
    simulate_ruin,
    stationary_block_bootstrap,
)
from engine.portfolio.stress import (
    PositionSpec,
    apply_position,
    episodes_from_config,
    historical_analogs,
    stress_report,
    synthetic_scenarios,
)
from engine.portfolio.var import (
    historical_var_es,
    liquidation_distance,
    parametric_var_es,
    portfolio_var,
    student_t_var_multiple,
)
from tests.synthetic import make_market_data

SEED = 20_261_005

# Student-t(4) constants.  For nu = 4 the density is exactly  f(x) = 3/8 * (1 + x^2/4)^(-5/2)
# (Gamma(5/2) / (sqrt(4 pi) Gamma(2)) = 3/8), so the tail can be integrated without scipy.stats:
#   q99 = 3.7469473879792  ;  unit-variance scale = sqrt((4 - 2)/4) = 0.7071067811865476
#   VaR multiple = q99 * scale                       = 2.6494919068
#   ES  multiple = E[X | X > q99] * scale            = 3.6915104857
# test_student_t_multiples_match_hand_computed_values re-derives both by quadrature, so these are not
# constants copied from the implementation under test.
T4_Q99 = 3.7469473879792
T4_VAR_MULTIPLE = 2.6494919068
T4_ES_MULTIPLE = 3.6915104857


def _t4_pdf(x: float) -> float:
    """Student-t(4) density, written out by hand (no scipy.stats)."""
    return 0.375 * (1.0 + x * x / 4.0) ** -2.5


def _fatt_tailed_sample(n: int = 1500, scale: float = 0.02, seed: int = SEED) -> np.ndarray:
    """SYNTHETIC fat-tailed daily log returns: Student-t(4) scaled to ~45 % annualised volatility."""
    return np.asarray(np.random.default_rng(seed).standard_t(4, n) * scale, dtype=float)


def _low_vol_sample(n: int = 2000, seed: int = SEED) -> np.ndarray:
    """SYNTHETIC stationary low-volatility sample: Gaussian, ~8 % annualised."""
    return np.asarray(np.random.default_rng(seed).normal(0.0, 0.005, n), dtype=float)


def _real_market_data():  # noqa: ANN202
    """Real Brent fixture if another module already captured it, else None (tests skip)."""
    try:
        from engine.data import fixtures as fx
    except ImportError:
        return None
    loader = getattr(fx, "load_fixture_market_data", None)
    if loader is None:
        return None
    try:
        return loader()
    except (FileNotFoundError, OSError, ValueError, KeyError):
        return None


REAL_MD = _real_market_data()
needs_real = pytest.mark.skipif(REAL_MD is None, reason="fixture reale (engine.data.fixtures) non disponibile")


# ---------------------------------------------------------------------------------------------------------------
# VaR / ES
# ---------------------------------------------------------------------------------------------------------------
def test_student_t_multiples_match_hand_computed_values():
    from scipy import integrate, optimize

    # independent derivation: integrate the hand-written density instead of trusting the closed forms
    tail = lambda q: integrate.quad(_t4_pdf, q, math.inf, limit=400)[0]  # noqa: E731
    q99 = optimize.brentq(lambda x: tail(x) - 0.01, 0.5, 50.0, xtol=1e-13)
    es_raw = integrate.quad(lambda x: x * _t4_pdf(x), q99, math.inf, limit=400)[0] / 0.01
    scale = math.sqrt(0.5)
    assert q99 == pytest.approx(T4_Q99, abs=1e-9)
    assert q99 * scale == pytest.approx(T4_VAR_MULTIPLE, abs=1e-8)
    assert es_raw * scale == pytest.approx(T4_ES_MULTIPLE, abs=1e-8)

    assert student_t_var_multiple(0.99, 4.0) == pytest.approx(T4_VAR_MULTIPLE, abs=1e-8)
    assert student_t_es(0.99, 4.0) == pytest.approx(T4_ES_MULTIPLE, abs=1e-8)
    # the VaR must sit inside the ES: the average loss beyond a quantile is worse than the quantile
    assert student_t_var_multiple(0.99, 4.0) < student_t_es(0.99, 4.0)


def test_parametric_var_es_is_consistent_with_leverage_es99():
    vol = 0.50  # annualised, Brent's current order of magnitude
    var, es = parametric_var_es(vol, 0.99)
    daily = vol / math.sqrt(252)
    assert var == pytest.approx(daily * T4_VAR_MULTIPLE, rel=1e-8)
    assert es == pytest.approx(daily * T4_ES_MULTIPLE, rel=1e-8)
    # the sizing rule and the dashboard number must be literally the same quantity
    assert es == es99_one_day(vol, 0.99)
    # the horizon scales with sqrt(t)
    var_10, es_10 = parametric_var_es(vol, 0.99, horizon_days=10)
    assert es_10 == pytest.approx(es * math.sqrt(10), rel=1e-12)
    assert var_10 == pytest.approx(var * math.sqrt(10), rel=1e-12)
    # an unusable volatility must never look safe
    assert parametric_var_es(0.0) == (math.inf, math.inf)


def test_historical_var_es_from_the_empirical_distribution():
    # SYNTHETIC hand-built sample of 20 daily returns, sorted tail: -0.10, -0.06, -0.03, ...
    r = np.array(
        [-0.10, -0.06, -0.03, -0.02, -0.015, -0.01, -0.008, -0.005, -0.002, 0.0]
        + [0.002, 0.004, 0.006, 0.008, 0.01, 0.012, 0.015, 0.02, 0.03, 0.05]
    )
    var, es = historical_var_es(r, 0.90)
    q = float(np.quantile(r, 0.10))
    assert var == pytest.approx(-q)
    assert es == pytest.approx(-float(r[r <= q].mean()))
    assert var > 0 and es >= var  # positive losses, ES at least as bad as VaR
    assert all(math.isnan(x) for x in historical_var_es([0.01], 0.99))


def test_portfolio_var_uses_the_covariance_and_lists_what_it_excluded():
    rng = np.random.default_rng(7)
    n = 800
    base = rng.normal(0, 0.02, n)  # SYNTHETIC correlated pair
    rets = pd.DataFrame({"BZF26": base, "CLF26": base * 0.9 + rng.normal(0, 0.006, n)})
    out = portfolio_var(
        {"BZF26": 1000.0, "CLF26": -500.0, "GASOIL": 50.0}, {"BZF26": 100.0, "CLF26": 96.0}, rets, equity=10_000.0
    )
    assert out["method"] == "parametric"
    assert out["var"] > 0 and out["es"] > out["var"]
    # contributions add up to the total (Euler decomposition)
    total = sum(c["var_contribution"] for c in out["contributions"].values())
    assert total == pytest.approx(out["var"], rel=1e-9)
    # the symbol without a return series is excluded AND listed, never proxied
    excluded = {e["symbol"]: e["reason"] for e in out["excluded"]}
    assert "GASOIL" in excluded and "prezzo" in excluded["GASOIL"]
    assert set(out["contributions"]) == {"BZF26", "CLF26"}
    # a single position reduces to the parametric formula on its own volatility
    one = portfolio_var({"BZF26": 1000.0}, {"BZF26": 100.0}, rets)
    vol_ann = float(rets["BZF26"].std(ddof=1) * math.sqrt(252))
    assert one["es"] == pytest.approx(100_000.0 * parametric_var_es(vol_ann, 0.99)[1], rel=1e-9)
    # historical method on the same book stays in the same ballpark and is still a positive loss
    hist = portfolio_var({"BZF26": 1000.0}, {"BZF26": 100.0}, rets, method="historical")
    assert hist["var"] > 0 and hist["es"] >= hist["var"]


def test_liquidation_distance_matches_the_broker_formula():
    price, pct = liquidation_distance(1000.0, 100.0, 10_000.0, 0.10, 0.50)
    assert price == pytest.approx(94.7368421, rel=1e-6)  # the worked example in engine/broker/margin.py
    assert pct == pytest.approx(-0.0526316, rel=1e-5)  # a long: the price must FALL
    short_price, short_pct = liquidation_distance(-1000.0, 100.0, 10_000.0, 0.10, 0.50)
    assert short_price == pytest.approx(104.7619048, rel=1e-6)
    assert short_pct is not None and short_pct > 0  # a short: the price must RISE
    assert liquidation_distance(0.0, 100.0, 10_000.0, 0.10, 0.50) == (None, None)


# ---------------------------------------------------------------------------------------------------------------
# Monte Carlo: bootstrap and ruin
# ---------------------------------------------------------------------------------------------------------------
def test_block_bootstrap_keeps_blocks_contiguous():
    rng = np.random.default_rng(3)
    idx = stationary_block_bootstrap(500, 200, 120, block_len=10, rng=rng)
    assert idx.shape == (200, 120)
    assert idx.min() >= 0 and idx.max() < 500
    steps = (idx[:, 1:] - idx[:, :-1]) % 500
    contiguous = float((steps == 1).mean())
    # with a mean block length of 10 about 90 % of the steps continue the previous block
    assert 0.80 < contiguous < 0.95
    iid = stationary_block_bootstrap(500, 200, 120, block_len=1, rng=np.random.default_rng(3))
    assert float((((iid[:, 1:] - iid[:, :-1]) % 500) == 1).mean()) < 0.05


def test_ruin_probability_is_monotone_in_leverage_synthetic_sample():
    r = _fatt_tailed_sample()
    probs = []
    for lev in (1.0, 5.0, 10.0):
        rep = simulate_ruin(r, constant_policy(lev), n_paths=4000, horizon_days=250, seed=SEED)
        probs.append(rep.prob_ruin)
    p1, p5, p10 = probs
    assert p1 < p5 < p10, f"non monotone: {probs}"
    assert p1 < 0.25 and p5 > 0.5 and p10 > 0.9
    # the account-death rule only bites at high leverage
    dead_1 = simulate_ruin(r, constant_policy(1.0), n_paths=4000, seed=SEED).prob_dead
    dead_10 = simulate_ruin(r, constant_policy(10.0), n_paths=4000, seed=SEED).prob_dead
    assert dead_1 < 0.01 < dead_10


def test_no_ruin_at_1x_on_a_stationary_low_vol_sample():
    rep = simulate_ruin(_low_vol_sample(), constant_policy(1.0), n_paths=5000, horizon_days=250, seed=SEED)
    assert rep.prob_ruin < 1e-3
    assert rep.prob_dead == 0.0
    assert rep.max_drawdown["p99"] < 0.5


def test_full_kelly_ruins_far_more_often_than_quarter_kelly():
    r = _fatt_tailed_sample()
    base = RiskConfig.load()
    # es99_budget is widened for both runs so that the ES cap does not mask the Kelly component under test
    quarter = dataclasses.replace(base, kelly_fraction=0.25, es99_budget=1.0)
    full = dataclasses.replace(base, kelly_fraction=1.0, es99_budget=1.0)
    kw = {"n_paths": 4000, "horizon_days": 250, "seed": SEED}
    rep_q = simulate_ruin(r, policy_from_risk(quarter, 1.0, edge_sharpe_annual=1.0), **kw)
    rep_f = simulate_ruin(r, policy_from_risk(full, 1.0, edge_sharpe_annual=1.0), **kw)
    # full Kelly takes about twice the leverage (the drawdown ladder and the realised vol clip the rest)
    assert rep_f.leverage_mean > 1.8 * rep_q.leverage_mean
    assert rep_f.prob_ruin > 5 * max(rep_q.prob_ruin, 1e-4)
    assert rep_f.prob_ruin > 0.2 and rep_q.prob_ruin < 0.05
    # and full Kelly is not rewarded for it: worse tail, worse median growth (variance drag)
    assert rep_f.p05_terminal < rep_q.p05_terminal
    assert rep_f.geometric_growth < rep_q.geometric_growth


def test_policy_from_risk_reproduces_compute_leverage():
    risk = RiskConfig.load()
    edge = 0.7
    pol = policy_from_risk(risk, gate_pass_rate=1.0, edge_sharpe_annual=edge)
    pol_gated = policy_from_risk(risk, gate_pass_rate=0.0, edge_sharpe_annual=edge)
    vols = [0.10, 0.15, 0.25, 0.40, 0.60, 0.90]
    dds = [0.0, 0.05, 0.12, 0.25, 0.35]
    for vol in vols:
        for dd in dds:
            state = {
                "day": 0,
                "equity": np.array([10_000.0]),
                "peak": np.array([10_000.0]),
                "drawdown": np.array([dd]),
                "vol_annual": np.array([vol]),
                "alive": np.array([True]),
                "u": np.array([0.5]),
                "leverage": np.array([0.0]),
                "initial_capital": 10_000.0,
                "n_paths": 1,
                "horizon_days": 1,
            }
            expected = compute_leverage(
                gate_passed=True,
                risk=risk,
                expected_return=edge * vol / 252.0,
                expected_vol=vol / math.sqrt(252),
                horizon_days=1,
                vols={"realized": vol},
                drawdown=dd,
            )
            assert float(pol(state)[0]) == pytest.approx(expected.chosen, rel=1e-9), (vol, dd)
            gated = compute_leverage(
                gate_passed=False,
                risk=risk,
                expected_return=edge * vol / 252.0,
                expected_vol=vol / math.sqrt(252),
                horizon_days=1,
                vols={"realized": vol},
                drawdown=dd,
            )
            assert float(pol_gated(state)[0]) == pytest.approx(gated.chosen, rel=1e-9), (vol, dd)


def test_injected_scenarios_move_the_terminal_distribution_by_the_expected_magnitude():
    r = _low_vol_sample()
    kw = {"n_paths": 3000, "horizon_days": 250, "seed": SEED}
    base = simulate_ruin(r, constant_policy(1.0), **kw)
    # the bootstrap is drawn before the scenario randomness, so the paths are the SAME: a paired comparison
    reopening = simulate_ruin(r, constant_policy(1.0), scenarios={"reopening": 1.0}, **kw)
    escalation = simulate_ruin(r, constant_policy(1.0), scenarios={"escalation": 1.0}, **kw)
    ratio_down = float(np.median(reopening.terminal / base.terminal))
    ratio_up = float(np.median(escalation.terminal / base.terminal))
    # reopening: -20 % to -30 %, uniform -> median multiplier about 0.75
    assert 0.70 <= ratio_down <= 0.80, ratio_down
    # escalation: +10 % to +15 % in one session
    assert 1.10 <= ratio_up <= 1.15, ratio_up
    assert reopening.prob_ruin >= base.prob_ruin

    mixed = simulate_ruin(r, constant_policy(1.0), scenarios={"reopening": 0.3, "escalation": 0.2}, **kw)
    assert set(mixed.by_scenario) == {"reopening", "escalation", "none"}
    assert 0.25 < mixed.by_scenario["reopening"]["share"] < 0.35
    assert 0.15 < mixed.by_scenario["escalation"]["share"] < 0.25
    # each path's membership is recorded, and the groups behave as their shocks imply
    assert mixed.scenario_paths["reopening"].sum() == mixed.by_scenario["reopening"]["n_paths"]
    assert mixed.by_scenario["reopening"]["median_terminal"] < mixed.by_scenario["none"]["median_terminal"]
    assert mixed.by_scenario["escalation"]["median_terminal"] > mixed.by_scenario["none"]["median_terminal"]
    d = mixed.to_dict()
    assert d["synthetic_scenarios"] is True
    assert "scenario sintetico" in d["by_scenario"]["reopening"]["label"]
    assert d["params"]["resampling"].startswith("stationary block bootstrap")


def test_simulation_is_deterministic_with_a_fixed_seed():
    r = _fatt_tailed_sample(600)
    kw = {"n_paths": 1500, "horizon_days": 120, "scenarios": {"reopening": 0.2}}
    a = simulate_ruin(r, constant_policy(2.0), seed=99, **kw)
    b = simulate_ruin(r, constant_policy(2.0), seed=99, **kw)
    c = simulate_ruin(r, constant_policy(2.0), seed=100, **kw)
    assert a.prob_ruin == b.prob_ruin and a.median_terminal == b.median_terminal
    assert np.array_equal(a.terminal, b.terminal)
    assert a.to_dict() == b.to_dict()
    assert not np.array_equal(a.terminal, c.terminal)


def test_a_scalar_only_policy_is_applied_path_by_path():
    """The documented fallback for a policy that cannot be vectorised (``policy.per_path = True``)."""
    r = _low_vol_sample(400)

    def scalar_policy(state: dict) -> float:
        assert isinstance(state["day"], int)
        return 0.5 if float(state["drawdown"]) > 0.02 else 2.0

    scalar_policy.per_path = True
    rep = simulate_ruin(r, scalar_policy, n_paths=50, horizon_days=40, seed=SEED)
    assert 0.5 <= rep.leverage_mean <= 2.0
    assert rep.n_paths == 50


@pytest.mark.slow
def test_twenty_thousand_paths_by_250_days_runs_in_under_a_minute():
    r = _fatt_tailed_sample()
    policy = policy_from_risk(RiskConfig.load(), gate_pass_rate=0.3)
    t0 = time.perf_counter()
    rep = simulate_ruin(r, policy, n_paths=20_000, horizon_days=250, seed=SEED)
    elapsed = time.perf_counter() - t0
    assert rep.n_paths == 20_000
    assert elapsed < 60.0, f"20 000 x 250 ha richiesto {elapsed:.1f}s"


def test_calibration_returns_the_whole_grid_and_respects_the_constraint():
    r = _fatt_tailed_sample()
    risk = RiskConfig.load()
    best, table = calibrate(
        r,
        risk,
        kelly_grid=(0.25, 1.0),
        es_budget_grid=(0.12, 1.0),
        n_paths=1200,
        horizon_days=250,
        gate_pass_rate=1.0,
        seed=SEED,
    )
    assert len(table) == 4  # nothing dropped: the full grid is returned
    assert set(table.columns) >= {"kelly_fraction", "es99_budget", "prob_ruin", "geometric_growth", "feasible"}
    assert bool(table["is_current"].any())  # the live configuration is one of the rows
    if best["feasible"]:
        assert best["prob_ruin"] <= best["max_ruin_probability"]
        feasible = table[table["feasible"]]
        assert best["geometric_growth"] == pytest.approx(feasible["geometric_growth"].max())
    md = calibration_markdown(best, table)
    assert "Calibrazione Monte Carlo" in md and "P(rovina)" in md
    assert md.count("|") > 20


# ---------------------------------------------------------------------------------------------------------------
# stress tests
# ---------------------------------------------------------------------------------------------------------------
def test_episode_slicing_returns_the_configured_date_ranges():
    episodes = {e["id"]: e for e in episodes_from_config()}
    assert str(episodes["gulf_war_1990"]["start"]) == "1990-08-02"
    assert str(episodes["gulf_war_1990"]["end"]) == "1991-03-01"
    assert str(episodes["covid_2020"]["start"]) == "2020-02-20"
    assert episodes["iran_war_2026"]["end"] is None  # still open

    # SYNTHETIC prices covering 2019-2021: the 2019/2020 episodes are sliced, the others declared unavailable
    md = make_market_data(n_days=700, start=date(2019, 1, 2), seed=5)
    analogs = {a.id: a for a in historical_analogs(md.prices["brent_front_close"], PositionSpec(leverage=2.0))}
    assert analogs["abqaiq_2019"].available
    assert analogs["abqaiq_2019"].result["start"] >= "2019-09-13"
    assert analogs["abqaiq_2019"].result["end"] <= "2019-10-15"
    assert analogs["covid_2020"].available
    for ident in ("gulf_war_1990", "gfc_2008", "glut_2014"):
        assert not analogs[ident].available
        assert "nessun dato" in (analogs[ident].reason or "")
        assert "total_return" not in analogs[ident].to_dict()  # never invented


def test_stress_position_mechanics_on_synthetic_prices():
    # SYNTHETIC straight-line crash: -10 % a day for 6 sessions, 3x long, 10 % margin, stop-out at 0.5
    prices = pd.Series(
        [100.0 * 0.9**i for i in range(7)],
        index=pd.DatetimeIndex([pd.Timestamp("2026-01-05") + pd.Timedelta(days=i) for i in range(7)]),
    )
    out = apply_position(prices, PositionSpec(leverage=3.0, direction=1))
    assert out["available"] and out["qty_bbl"] == pytest.approx(300.0)
    # a 3x long loses 30 % of equity on a -10 % price day
    assert out["worst_day_price"] == pytest.approx(-0.10, abs=1e-9)
    assert out["worst_day"] <= -0.30
    # the margin level collapses and the broker liquidates; the liquidation price solves level = 0.5
    assert out["margin_call"] and out["liquidated"]
    assert out["days_to_liquidation"] is not None and 1 <= out["days_to_liquidation"] <= 5
    assert out["liquidation_price"] == pytest.approx(70.1754386, rel=1e-6)
    # the equity is frozen once liquidated: no further losses on a position that no longer exists
    tail = [p["equity"] for p in out["equity_path"][out["days_to_liquidation"] :]]
    assert len(set(tail)) == 1
    assert out["equity_end"] >= 0.0  # an account cannot go negative: the broker closes it first
    assert out["dead"] is True and out["total_return"] == pytest.approx(-1.0, abs=1e-9)

    short = apply_position(prices, PositionSpec(leverage=3.0, direction=-1))
    assert short["total_return"] > 0 and not short["liquidated"]


def test_synthetic_scenarios_are_labelled_and_sized_as_the_brief_asks():
    scenarios = {s.id: s for s in synthetic_scenarios(100.0, PositionSpec(leverage=1.0))}
    assert set(scenarios) == set(
        {
            "reopening_20_3d",
            "reopening_30_3d",
            "escalation_10_1d",
            "escalation_15_1d",
            "weekend_gap_up_8",
            "weekend_gap_down_8",
        }
    )
    assert scenarios["reopening_20_3d"].result["price_return"] == pytest.approx(-0.20, abs=0.005)
    assert scenarios["reopening_30_3d"].result["price_return"] == pytest.approx(-0.30, abs=0.005)
    assert scenarios["escalation_15_1d"].result["total_return"] == pytest.approx(0.15, abs=1e-6)
    assert scenarios["weekend_gap_down_8"].result["total_return"] == pytest.approx(-0.08, abs=1e-6)
    for s in scenarios.values():
        d = s.to_dict()
        assert d["synthetic"] is True
        assert d["label"].startswith("scenario sintetico")
    # the Monte Carlo specs cover the same two shocks of brief §4.5
    assert SCENARIO_SPECS["reopening"].min_move <= -0.30 and SCENARIO_SPECS["reopening"].max_move >= -0.20  # noqa: E501
    assert SCENARIO_SPECS["escalation"].min_days == SCENARIO_SPECS["escalation"].max_days == 1


def test_stress_report_declares_unavailable_episodes_and_never_simulates_them():
    md = make_market_data(n_days=300, start=date(2024, 1, 2))
    rep = stress_report(md.prices["brent_front_close"], PositionSpec(leverage=2.0), price_source="synthetic")
    d = rep.to_dict()
    assert d["n_unavailable"] == len(d["episodes"]) >= 8
    assert all(not e["available"] and e.get("reason") for e in d["episodes"])
    assert all(e["synthetic"] is False for e in d["episodes"])
    assert d["scenarios"] and all(s["synthetic"] for s in d["scenarios"])
    assert d["labels"]["title"] == "Stress test"
    # with no prices at all nothing is invented: no scenarios, every episode unavailable
    empty = stress_report(pd.Series(dtype=float)).to_dict()
    assert empty["scenarios"] == [] and empty["reference_price"] is None
    assert empty["n_available"] == 0


@needs_real
def test_gulf_war_slice_contains_the_one_day_crash_on_real_data():
    md = REAL_MD
    col = next((c for c in ("brent_spot", "brent_front_close", "brent_cont") if c in md.prices.columns), None)
    assert col is not None, "la fixture reale non contiene una serie di prezzi Brent"
    prices = pd.to_numeric(md.prices[col], errors="coerce").dropna()
    analogs = {a.id: a for a in historical_analogs(prices, PositionSpec(leverage=1.0))}
    gulf = analogs["gulf_war_1990"]
    if not gulf.available:
        pytest.skip(f"la fixture reale non copre l'episodio: {gulf.reason}")
    assert gulf.result["start"] <= "1990-08-10" and gulf.result["end"] >= "1991-02-20"
    # 17 January 1991, start of Desert Storm: about -33 % in one session
    assert gulf.result["worst_day_price"] <= -0.25, gulf.result["worst_day_price"]
    assert gulf.fact_check is not None and gulf.fact_check["matches"], gulf.fact_check
    assert gulf.fact_check["observed_worst_date"] == "1991-01-17"
    # the Abqaiq reopening of 16 September 2019 is the mirror image: a jump of about +15 %
    abqaiq = analogs["abqaiq_2019"]
    if abqaiq.available and abqaiq.fact_check is not None:
        assert abqaiq.fact_check["matches"], abqaiq.fact_check


@needs_real
def test_monte_carlo_on_real_brent_returns():
    md = REAL_MD
    col = next((c for c in ("brent_front_close", "brent_spot", "brent_cont") if c in md.prices.columns), None)
    prices = pd.to_numeric(md.prices[col], errors="coerce").dropna()
    logret = np.diff(np.log(prices.to_numpy(dtype=float)))
    if logret.size < 250:
        pytest.skip("la fixture reale ha meno di 250 rendimenti giornalieri")
    rep = simulate_ruin(logret, constant_policy(1.0), n_paths=2000, horizon_days=250, seed=SEED)
    assert 0.0 <= rep.prob_ruin <= 1.0
    assert rep.params["n_return_obs"] == int(logret.size)
    assert rep.median_terminal > 0
