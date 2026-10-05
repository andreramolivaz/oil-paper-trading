"""Monte Carlo risk of ruin for the leverage policy (brief §4.5, §9 "Calibrazione", risk.json).

What this answers
-----------------
"With this leverage rule, on returns that look like the real Brent, how often does the account lose half its
equity — or die — within a year, and how fast does it grow when it survives?"  The brief makes the calibration of
``config/risk.yaml`` explicit: *maximise geometric growth subject to an estimated risk of ruin*
(``monte_carlo.max_ruin_probability``).  :func:`calibrate` does exactly that and returns the **whole** grid, so
no cell can be cherry-picked afterwards.

Resampling: stationary block bootstrap, never i.i.d.
----------------------------------------------------
``returns`` is a sample of REAL daily Brent **log** returns supplied by the caller (this module never fetches or
invents data).  Paths are drawn with the stationary bootstrap of Politis & Romano (1994): blocks of geometric
length (mean ``block_len``) are glued together, wrapping around the sample.  This matters more than it looks.
Ruin is not caused by one bad day, it is caused by a *run* of bad days while the account is already delevering —
i.e. by volatility clustering.  I.i.d. resampling destroys exactly that autocorrelation in |r|: each day is drawn
independently, long adverse sequences become exponentially unlikely, realised drawdowns shrink and the estimated
probability of ruin comes out **too low** — the one direction a risk model must never err in.  Blocks keep the
clusters (and the tails that come with them) alive.

The policy
----------
``policy`` is a callable ``policy(state) -> leverage`` evaluated once per simulated day.  ``state`` is a dict with
the day's cross-section over paths, so the same object can drive 20 000 paths at once:

===============  =========================================================================================
``day``          int, 0-based index of the day being decided
``equity``       float array (n_paths,), equity at the start of the day
``drawdown``     float array, current drawdown from the path's own peak (fraction, >= 0)
``vol_annual``   float array, trailing realised volatility of the simulated returns, annualised
``peak``         float array, running peak equity
``alive``        bool array, False once the account is dead (5 % rule) — a dead path must get leverage 0
``u``            float array in [0, 1), the path's own uniform draw for the day (gate / randomised rules)
``leverage``     float array, the leverage used the previous day (for turnover-aware policies)
``initial_capital``, ``n_paths``, ``horizon_days``: scalars
===============  =========================================================================================

The callable returns an array of leverages (or a scalar, broadcast).  A scalar-only policy that cannot be
vectorised must set ``policy.per_path = True`` and is then applied path by path (correct, but ~1 000x slower).
:func:`policy_from_risk` wraps the engine's own rule (:func:`engine.portfolio.leverage.compute_leverage`); the
leverage it returns is a **cap**, and the live allocator usually sizes below it, so the simulation is
conservative by construction (it overstates exposure, hence overstates ruin).

Costs: ``cost_bps_per_turn`` basis points of equity are charged on every change of leverage,
``|L_t - L_{t-1}|`` being the turnover of one unit of notional per unit of equity.

Performance: 20 000 paths x 250 days runs in a couple of seconds (one vectorised step per day, no Python loop
over paths); ``tests/test_risk_mc.py`` asserts the 60 s budget.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from engine.core.config import RiskConfig
from engine.portfolio.leverage import ES_DF, TRADING_DAYS, student_t_es

__all__ = [
    "SCENARIO_SPECS",
    "PolicyState",
    "RuinReport",
    "ScenarioSpec",
    "calibrate",
    "calibration_markdown",
    "constant_policy",
    "cost_bps_from_risk",
    "policy_from_risk",
    "simulate_ruin",
    "stationary_block_bootstrap",
]

PolicyState = dict[str, Any]
Policy = Callable[[PolicyState], Any]
NAN = float("nan")


# ---------------------------------------------------------------------------------------------------------------
# synthetic scenarios (brief §4.5) — explicitly requested, explicitly labelled
# ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ScenarioSpec:
    """A synthetic shock injected on top of the bootstrapped path.  Synthetic: labelled as such everywhere.

    The total move is drawn uniformly in ``[min_move, max_move]`` (simple return), converted to log and spread
    evenly over ``k`` consecutive days with ``k`` uniform in ``[min_days, max_days]``, starting on a uniformly
    drawn day.  It is ADDED to the resampled return of those days: the scenario is a shock on top of the regime,
    not a replacement for it.
    """

    id: str
    label: str  # Italian, for the dashboard
    min_move: float
    max_move: float
    min_days: int
    max_days: int

    def draw(self, rng: np.random.Generator, size: int) -> tuple[np.ndarray, np.ndarray]:
        """``(total log move, number of days)`` for ``size`` paths."""
        move = rng.uniform(self.min_move, self.max_move, size)
        days = rng.integers(self.min_days, self.max_days + 1, size)
        return np.log1p(move), days


SCENARIO_SPECS: dict[str, ScenarioSpec] = {
    "reopening": ScenarioSpec(
        id="reopening",
        label="scenario sintetico: riapertura improvvisa (-20/-30% in 3-5 giorni)",
        min_move=-0.30,
        max_move=-0.20,
        min_days=3,
        max_days=5,
    ),
    "escalation": ScenarioSpec(
        id="escalation",
        label="scenario sintetico: escalation (+10/+15% in una seduta)",
        min_move=0.10,
        max_move=0.15,
        min_days=1,
        max_days=1,
    ),
}


def _scenario_items(
    scenarios: Mapping[str, float | Mapping[str, Any]] | None,
) -> list[tuple[ScenarioSpec, float]]:
    """Normalise the ``scenarios`` argument into ``[(spec, per-path probability)]``.

    A value may be a bare probability or a mapping with ``prob`` plus any field of :class:`ScenarioSpec` to
    override (``min_move``, ``max_move``, ``min_days``, ``max_days``, ``label``).
    """
    out: list[tuple[ScenarioSpec, float]] = []
    for name, value in (scenarios or {}).items():
        base = SCENARIO_SPECS.get(name)
        spec: ScenarioSpec
        if isinstance(value, Mapping):
            prob = float(value.get("prob", 0.0))
            fields = {k: v for k, v in value.items() if k != "prob"}
            if base is not None:
                spec = dataclasses.replace(base, **fields)
            else:
                spec = ScenarioSpec(id=name, label=str(fields.pop("label", name)), **fields)
        else:
            if base is None:
                raise KeyError(f"unknown scenario {name!r}; known: {sorted(SCENARIO_SPECS)}")
            prob, spec = float(value), base
        if not 0.0 <= prob <= 1.0:
            raise ValueError(f"scenario {name!r}: probability must be in [0, 1]")
        if prob > 0.0:
            out.append((spec, prob))
    return out


# ---------------------------------------------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------------------------------------------
def stationary_block_bootstrap(
    n_obs: int, n_paths: int, horizon: int, block_len: float, rng: np.random.Generator
) -> np.ndarray:
    """``(n_paths, horizon)`` integer index matrix of a stationary (circular, geometric-block) bootstrap.

    Each day continues the previous block with probability ``1 - 1/block_len`` (index + 1, modulo ``n_obs``) and
    starts a fresh block at a uniformly drawn observation otherwise.  ``block_len <= 1`` degenerates to i.i.d.
    resampling, which understates ruin (see the module docstring) and is only there for comparison tests.
    """
    if n_obs < 2:
        raise ValueError("need at least two observations to resample")
    if n_paths < 1 or horizon < 1:
        raise ValueError("n_paths and horizon must be >= 1")
    p = 1.0 / max(float(block_len), 1.0)
    idx = np.empty((n_paths, horizon), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n_obs, n_paths)
    if horizon > 1:
        fresh = rng.random((n_paths, horizon - 1)) < p
        starts = rng.integers(0, n_obs, (n_paths, horizon - 1))
        for t in range(1, horizon):
            cont = idx[:, t - 1] + 1
            np.mod(cont, n_obs, out=cont)
            idx[:, t] = np.where(fresh[:, t - 1], starts[:, t - 1], cont)
    return idx


# ---------------------------------------------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class RuinReport:
    """Outcome distribution of the simulated policy.  All probabilities are fractions of paths.

    * ``prob_ruin``: equity below ``ruin_threshold * initial_capital`` **at any time** (not just at the end).
    * ``prob_dead``: equity at or below ``dead_fraction * initial_capital`` — the account-death rule of the brief
      (§3, §10): trading halts until a Reset, so the path is frozen from that day on.
    * ``geometric_growth``: median across paths of ``log(terminal / initial) / years`` (annualised log growth).
      The median, not the mean: a handful of ruined paths would drag an average to meaninglessness.
    * ``max_drawdown``: distribution (mean/median/p95/p99/worst) of the per-path maximum drawdown, as positive
      fractions.
    * ``by_scenario``: the same headline numbers restricted to the paths that received each injected synthetic
      scenario, plus ``"none"`` for the paths that received none.  Groups can overlap (scenarios are drawn
      independently per path).
    """

    n_paths: int
    horizon_days: int
    initial_capital: float
    ruin_threshold: float
    dead_fraction: float
    prob_ruin: float
    prob_dead: float
    median_terminal: float
    p05_terminal: float
    p95_terminal: float
    geometric_growth: float
    max_drawdown: dict[str, float] = field(default_factory=dict)
    by_scenario: dict[str, dict[str, Any]] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    terminal: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)
    ruined: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=bool), repr=False)
    dead: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=bool), repr=False)
    max_dd_paths: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)
    leverage_mean: float = NAN
    scenario_paths: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly summary for ``risk.json`` (non-finite values become ``None``)."""

        def f(x: float | None) -> float | None:
            if x is None:
                return None
            v = float(x)
            return None if not math.isfinite(v) else v

        return {
            "n_paths": int(self.n_paths),
            "horizon_days": int(self.horizon_days),
            "initial_capital": f(self.initial_capital),
            "ruin_threshold": f(self.ruin_threshold),
            "dead_fraction": f(self.dead_fraction),
            "prob_ruin": f(self.prob_ruin),
            "prob_dead": f(self.prob_dead),
            "median_terminal": f(self.median_terminal),
            "p05_terminal": f(self.p05_terminal),
            "p95_terminal": f(self.p95_terminal),
            "geometric_growth": f(self.geometric_growth),
            "leverage_mean": f(self.leverage_mean),
            "max_drawdown": {k: f(v) for k, v in self.max_drawdown.items()},
            "by_scenario": {
                k: {kk: (f(vv) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                for k, v in sorted(self.by_scenario.items())
            },
            "params": dict(self.params),
            "synthetic_scenarios": bool(self.scenario_paths),
            "labels": {
                "title": "Rischio di rovina (Monte Carlo)",
                "prob_ruin": f"Probabilità di perdere il {(1 - self.ruin_threshold):.0%} dell'equity "
                f"entro {self.horizon_days} giorni",
                "prob_dead": f"Probabilità di conto azzerato (equity <= {self.dead_fraction:.0%} "
                "del capitale iniziale)",
                "median_terminal": "Equity finale mediana",
                "p05_terminal": "Equity finale, 5° percentile",
                "p95_terminal": "Equity finale, 95° percentile",
                "geometric_growth": "Crescita geometrica annualizzata (mediana)",
                "max_drawdown": "Distribuzione del drawdown massimo per percorso",
                "by_scenario": "Dettaglio per scenario sintetico iniettato",
                "note": "Rendimenti reali ricampionati a blocchi; gli scenari sono sintetici e dichiarati.",
            },
        }


def _group_stats(
    mask: np.ndarray,
    terminal: np.ndarray,
    ruined: np.ndarray,
    dead: np.ndarray,
    max_dd: np.ndarray,
    label: str,
) -> dict[str, Any]:
    n = int(mask.sum())
    if n == 0:
        return {"label": label, "n_paths": 0}
    t = terminal[mask]
    return {
        "label": label,
        "n_paths": n,
        "share": float(n / mask.size),
        "prob_ruin": float(ruined[mask].mean()),
        "prob_dead": float(dead[mask].mean()),
        "median_terminal": float(np.median(t)),
        "p05_terminal": float(np.quantile(t, 0.05)),
        "median_max_drawdown": float(np.median(max_dd[mask])),
    }


# ---------------------------------------------------------------------------------------------------------------
# simulation
# ---------------------------------------------------------------------------------------------------------------
def _rolling_vol_cumsums(log_r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    zeros = np.zeros((log_r.shape[0], 1))
    s1 = np.concatenate([zeros, np.cumsum(log_r, axis=1)], axis=1)
    s2 = np.concatenate([zeros, np.cumsum(np.square(log_r), axis=1)], axis=1)
    return s1, s2


def simulate_ruin(
    returns: Sequence[float] | np.ndarray | pd.Series,
    policy: Policy,
    n_paths: int = 20_000,
    horizon_days: int = 250,
    initial_capital: float = 10_000.0,
    ruin_threshold: float = 0.5,
    cost_bps_per_turn: float = 2.5,
    block_len: float = 10.0,
    scenarios: Mapping[str, float | Mapping[str, Any]] | None = None,
    seed: int = 20_261_005,
    dead_fraction: float = 0.05,
    vol_window: int = 21,
    min_vol_obs: int = 5,
    hard_cap: float = 10.0,
) -> RuinReport:
    """Simulate the leverage ``policy`` on bootstrapped real returns and return the ruin statistics.

    ``returns`` must be daily **log** returns of the traded instrument (the caller passes the real sample; this
    module never sources data).  Each day: the policy sets the leverage from the path's state, the leverage change
    is charged ``cost_bps_per_turn`` basis points of equity, and the equity compounds at
    ``1 + L_t * (exp(r_t) - 1) - cost``.  Equity is floored at zero (the broker liquidates; the account can never
    go negative) and frozen once dead, which is the brief's rule, not a numerical convenience.

    The daily step deliberately applies the whole daily move before looking at the margin level: a real intraday
    stop-out would cut the loss short, so taking the full day is the conservative choice.
    """
    r = np.asarray(returns, dtype=float).ravel()
    r = r[np.isfinite(r)]
    if r.size < 30:
        raise ValueError("need at least 30 finite daily returns to bootstrap a credible path")
    if horizon_days < 1 or n_paths < 1:
        raise ValueError("n_paths and horizon_days must be >= 1")
    if not 0.0 < ruin_threshold < 1.0:
        raise ValueError("ruin_threshold must be in (0, 1)")

    rng = np.random.default_rng(seed)
    idx = stationary_block_bootstrap(r.size, n_paths, horizon_days, block_len, rng)
    log_r = r[idx]

    # --- synthetic scenarios (labelled): injected on top of the resampled path --------------------------------
    specs = _scenario_items(scenarios)
    scenario_paths: dict[str, np.ndarray] = {}
    for spec, prob in specs:
        hit = rng.random(n_paths) < prob
        rows = np.flatnonzero(hit)
        scenario_paths[spec.id] = hit
        if rows.size == 0:
            continue
        total, days = spec.draw(rng, rows.size)
        per_day = total / days
        max_start = np.maximum(horizon_days - days, 0)
        start = (rng.random(rows.size) * (max_start + 1)).astype(np.int64)
        for k in np.unique(days):
            sel = np.flatnonzero(days == k)
            offsets = np.arange(int(k))
            cols = start[sel][:, None] + offsets[None, :]
            np.clip(cols, 0, horizon_days - 1, out=cols)
            np.add.at(log_r, (rows[sel][:, None], cols), per_day[sel][:, None])

    simple = np.expm1(log_r)
    s1, s2 = _rolling_vol_cumsums(log_r)
    sample_vol = float(np.std(r, ddof=1) * math.sqrt(TRADING_DAYS))
    u = rng.random((n_paths, horizon_days))

    equity = np.full(n_paths, float(initial_capital))
    peak = equity.copy()
    max_dd = np.zeros(n_paths)
    lev_prev = np.zeros(n_paths)
    alive = np.ones(n_paths, dtype=bool)
    ruined = np.zeros(n_paths, dtype=bool)
    dead = np.zeros(n_paths, dtype=bool)
    lev_sum = np.zeros(n_paths)

    ruin_level = float(ruin_threshold) * float(initial_capital)
    dead_level = float(dead_fraction) * float(initial_capital)
    cost_rate = float(cost_bps_per_turn) / 1e4
    per_path = bool(getattr(policy, "per_path", False))

    for t in range(horizon_days):
        lo = max(0, t - int(vol_window))
        m = t - lo
        if m >= int(min_vol_obs):
            mean = (s1[:, t] - s1[:, lo]) / m
            var = (s2[:, t] - s2[:, lo]) / m - mean * mean
            vol = np.sqrt(np.maximum(var, 0.0)) * math.sqrt(TRADING_DAYS)
        else:
            vol = np.full(n_paths, sample_vol)
        drawdown = np.where(peak > 0, 1.0 - equity / np.maximum(peak, 1e-12), 0.0)
        state: PolicyState = {
            "day": t,
            "equity": equity,
            "peak": peak,
            "drawdown": drawdown,
            "vol_annual": vol,
            "alive": alive,
            "u": u[:, t],
            "leverage": lev_prev,
            "initial_capital": float(initial_capital),
            "n_paths": int(n_paths),
            "horizon_days": int(horizon_days),
        }
        if per_path:
            lev = np.array(
                [
                    float(policy({k: (v[i] if isinstance(v, np.ndarray) else v) for k, v in state.items()}))
                    for i in range(n_paths)
                ]
            )
        else:
            lev = np.asarray(policy(state), dtype=float)
            if lev.ndim == 0:
                lev = np.full(n_paths, float(lev))
        lev = np.clip(np.nan_to_num(lev, nan=0.0, posinf=hard_cap, neginf=0.0), 0.0, float(hard_cap))
        lev = np.where(alive, lev, 0.0)

        cost = cost_rate * np.abs(lev - lev_prev)
        equity = equity * (1.0 + lev * simple[:, t] - cost)
        np.maximum(equity, 0.0, out=equity)
        lev_sum += lev
        lev_prev = lev

        np.maximum(peak, equity, out=peak)
        dd = np.where(peak > 0, 1.0 - equity / np.maximum(peak, 1e-12), 0.0)
        np.maximum(max_dd, dd, out=max_dd)
        ruined |= equity <= ruin_level
        newly_dead = alive & (equity <= dead_level)
        dead |= newly_dead
        alive &= ~newly_dead

    years = horizon_days / TRADING_DAYS
    growth = np.log(np.maximum(equity, 1e-6 * initial_capital) / initial_capital) / years
    report = RuinReport(
        n_paths=int(n_paths),
        horizon_days=int(horizon_days),
        initial_capital=float(initial_capital),
        ruin_threshold=float(ruin_threshold),
        dead_fraction=float(dead_fraction),
        prob_ruin=float(ruined.mean()),
        prob_dead=float(dead.mean()),
        median_terminal=float(np.median(equity)),
        p05_terminal=float(np.quantile(equity, 0.05)),
        p95_terminal=float(np.quantile(equity, 0.95)),
        geometric_growth=float(np.median(growth)),
        max_drawdown={
            "mean": float(max_dd.mean()),
            "median": float(np.median(max_dd)),
            "p95": float(np.quantile(max_dd, 0.95)),
            "p99": float(np.quantile(max_dd, 0.99)),
            "worst": float(max_dd.max()),
        },
        params={
            "n_paths": int(n_paths),
            "horizon_days": int(horizon_days),
            "block_len": float(block_len),
            "cost_bps_per_turn": float(cost_bps_per_turn),
            "ruin_threshold": float(ruin_threshold),
            "dead_fraction": float(dead_fraction),
            "vol_window": int(vol_window),
            "hard_cap": float(hard_cap),
            "seed": int(seed),
            "n_return_obs": int(r.size),
            "sample_vol_annual": sample_vol,
            "resampling": "stationary block bootstrap (Politis-Romano)",
            "scenarios": {spec.id: {"prob": prob, "label": spec.label} for spec, prob in specs},
            "policy": getattr(policy, "name", getattr(policy, "__name__", type(policy).__name__)),
        },
        terminal=equity,
        ruined=ruined,
        dead=dead,
        max_dd_paths=max_dd,
        leverage_mean=float(lev_sum.mean() / horizon_days),
        scenario_paths=scenario_paths,
    )
    if specs:
        none = np.ones(n_paths, dtype=bool)
        for spec, _prob in specs:
            mask = scenario_paths[spec.id]
            none &= ~mask
            report.by_scenario[spec.id] = _group_stats(mask, equity, ruined, dead, max_dd, spec.label)
        report.by_scenario["none"] = _group_stats(
            none, equity, ruined, dead, max_dd, "nessuno scenario sintetico iniettato"
        )
    return report


# ---------------------------------------------------------------------------------------------------------------
# policies
# ---------------------------------------------------------------------------------------------------------------
def constant_policy(leverage: float) -> Policy:
    """A fixed leverage every day (the 1x / 5x / 10x reference cases)."""

    def policy(state: PolicyState) -> np.ndarray:
        return np.full(int(state["n_paths"]), float(leverage))

    policy.name = f"costante {leverage:g}x"  # type: ignore[attr-defined]
    return policy


def cost_bps_from_risk(risk: RiskConfig) -> float:
    """Round-trip cost of one unit of leverage turnover, in bps, from ``config/risk.yaml`` ``costs``."""
    c = risk.costs or {}
    return float(c.get("base_spread_bps", 1.5)) + float(c.get("slippage_bps_per_turn", 1.0))


def policy_from_risk(
    risk: RiskConfig,
    gate_pass_rate: float,
    edge_sharpe_annual: float = 0.5,
    vol_floor: float = 0.05,
    apply_vol_target: bool = False,
) -> Policy:
    """A vectorised approximation of the live leverage rule (:func:`engine.portfolio.leverage.compute_leverage`).

    The three components that actually move in a simulation are reproduced exactly as the engine computes them:

    * ``L_vol``: ``es99_budget / ES99(vol_trailing)`` with the Student-t(4) expected shortfall multiple of
      :func:`engine.portfolio.leverage.student_t_es` (the same constant, imported, not re-derived);
    * ``L_drawdown``: ``1 + (cap - 1) * m(dd)`` with ``m`` the ``leverage.drawdown_delever`` ladder;
    * ``L_kelly``: fractional Kelly.  A simulation has no ensemble to read an expected return from, so the signal
      is parameterised by an assumed **annual information ratio** ``edge_sharpe_annual``: with
      ``mu = IR * sigma`` the Kelly leverage ``mu / sigma^2`` collapses to ``IR / sigma``, so
      ``L_kelly = kelly_fraction * IR / vol_annual`` — which is what ``fractional_kelly_leverage`` returns for
      those inputs (``tests/test_risk_mc.py`` asserts the identity on a grid).

    The gate (brief §9) is modelled as a Bernoulli draw: with probability ``gate_pass_rate`` the day's leverage may
    exceed 1x, otherwise it is capped at ``leverage.default_cap``.  The draw uses the path's own uniform stream
    (``state["u"]``), so it is deterministic given the simulation seed.  ``L_event`` has no counterpart in a
    simulation without a calendar and is left out (it can only ever reduce leverage, so leaving it out is the
    conservative side).  With ``apply_vol_target`` the exposure is additionally capped at
    ``vol_target_annual / vol_trailing``, i.e. the allocator's volatility targeting; off by default because
    :func:`compute_leverage` returns a cap, not a target.
    """
    if not 0.0 <= gate_pass_rate <= 1.0:
        raise ValueError("gate_pass_rate must be in [0, 1]")
    cap = min(10.0, float(risk.max_leverage))
    default_cap = min(float(risk.default_max_leverage), cap)
    es_mult = student_t_es(0.99, ES_DF)
    budget = float(risk.es99_budget)
    kelly_fraction = float(risk.kelly_fraction)
    edge = float(edge_sharpe_annual)
    vol_target = float(risk.vol_target_annual)
    ladder = sorted((float(k), float(v)) for k, v in (risk.drawdown_delever or {}).items())
    thresholds = np.array([k for k, _ in ladder])
    mults = np.array([v for _, v in ladder])

    def policy(state: PolicyState) -> np.ndarray:
        vol = np.maximum(np.asarray(state["vol_annual"], dtype=float), float(vol_floor))
        l_kelly = kelly_fraction * edge / vol
        es99 = vol / math.sqrt(TRADING_DAYS) * es_mult
        l_vol = np.where(es99 > 0, budget / np.maximum(es99, 1e-12), 0.0)
        dd = np.abs(np.asarray(state["drawdown"], dtype=float))
        mult = np.ones_like(dd)
        if thresholds.size:
            # highest ladder threshold that the drawdown has reached (same rule as drawdown_multiplier)
            rank = np.searchsorted(thresholds, dd, side="right") - 1
            mult = np.where(rank >= 0, mults[np.maximum(rank, 0)], 1.0)
        l_dd = 1.0 + (cap - 1.0) * mult
        lev = np.minimum(np.minimum(l_kelly, l_vol), np.minimum(l_dd, cap))
        gate = np.asarray(state["u"], dtype=float) < gate_pass_rate
        lev = np.where(gate, lev, np.minimum(lev, default_cap))
        if apply_vol_target:
            lev = np.minimum(lev, vol_target / vol)
        return np.maximum(lev, 0.0)

    policy.name = (  # type: ignore[attr-defined]
        f"policy motore (kelly {kelly_fraction:g}, ES99 budget {budget:g}, gate {gate_pass_rate:.0%})"
    )
    return policy


# ---------------------------------------------------------------------------------------------------------------
# calibration (brief §9: maximise geometric growth subject to the ruin constraint)
# ---------------------------------------------------------------------------------------------------------------
def calibrate(
    returns: Sequence[float] | np.ndarray | pd.Series,
    risk: RiskConfig,
    kelly_grid: Sequence[float] = (0.1, 0.25, 0.5, 1.0),
    es_budget_grid: Sequence[float] = (0.03, 0.06, 0.12, 0.20),
    n_paths: int = 4_000,
    horizon_days: int | None = None,
    gate_pass_rate: float = 0.30,
    edge_sharpe_annual: float = 0.5,
    scenarios: Mapping[str, float | Mapping[str, Any]] | None = None,
    seed: int = 20_261_005,
    max_ruin_probability: float | None = None,
    **sim_kwargs: Any,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Grid-search ``(kelly_fraction, es99_budget)`` for maximum geometric growth under the ruin constraint.

    Every cell of the grid is simulated with the SAME seed (common random numbers, so the comparison is not noise)
    and the **whole** table is returned: the selected cell is one row of it, never a number presented on its own.
    The constraint is ``prob_ruin <= monte_carlo.max_ruin_probability`` from ``config/risk.yaml`` (overridable).
    When no cell satisfies it, the row with the lowest ``prob_ruin`` is returned and ``best["feasible"]`` is False.

    Returns ``(best, table)``.  ``best`` also carries ``current``: the score of the values currently in the config,
    so the owner can see what the live configuration is worth.  This function never writes the config.
    """
    mc = (risk.raw or {}).get("monte_carlo", {}) or {}
    limit = float(max_ruin_probability if max_ruin_probability is not None else mc.get("max_ruin_probability", 0.05))
    horizon = int(horizon_days if horizon_days is not None else mc.get("horizon_days", 250))
    cost_bps = float(sim_kwargs.pop("cost_bps_per_turn", cost_bps_from_risk(risk)))
    rows: list[dict[str, Any]] = []
    for kf in kelly_grid:
        for eb in es_budget_grid:
            trial = dataclasses.replace(risk, kelly_fraction=float(kf), es99_budget=float(eb))
            rep = simulate_ruin(
                returns,
                policy_from_risk(trial, gate_pass_rate, edge_sharpe_annual=edge_sharpe_annual),
                n_paths=n_paths,
                horizon_days=horizon,
                initial_capital=risk.initial_capital,
                ruin_threshold=float(mc.get("ruin_threshold", 0.5)),
                cost_bps_per_turn=cost_bps,
                scenarios=scenarios,
                seed=seed,
                dead_fraction=risk.dead_equity_fraction,
                hard_cap=min(10.0, risk.max_leverage),
                **sim_kwargs,
            )
            rows.append(
                {
                    "kelly_fraction": float(kf),
                    "es99_budget": float(eb),
                    "prob_ruin": rep.prob_ruin,
                    "prob_dead": rep.prob_dead,
                    "geometric_growth": rep.geometric_growth,
                    "median_terminal": rep.median_terminal,
                    "p05_terminal": rep.p05_terminal,
                    "median_max_drawdown": rep.max_drawdown["median"],
                    "p99_max_drawdown": rep.max_drawdown["p99"],
                    "mean_leverage": rep.leverage_mean,
                    "feasible": bool(rep.prob_ruin <= limit),
                    "is_current": bool(
                        math.isclose(float(kf), risk.kelly_fraction, rel_tol=1e-9)
                        and math.isclose(float(eb), risk.es99_budget, rel_tol=1e-9)
                    ),
                }
            )
    table = pd.DataFrame(rows).sort_values(["kelly_fraction", "es99_budget"]).reset_index(drop=True)
    feasible = table[table["feasible"]]
    pool = feasible if not feasible.empty else table
    key = "geometric_growth" if not feasible.empty else "prob_ruin"
    values = np.asarray(pool[key].to_numpy(), dtype=float)
    row = int(np.nanargmax(values) if not feasible.empty else np.nanargmin(values))
    pick = pool.iloc[row]
    current = table[table["is_current"]]
    best: dict[str, Any] = {
        "kelly_fraction": float(pick["kelly_fraction"]),
        "es99_budget": float(pick["es99_budget"]),
        "prob_ruin": float(pick["prob_ruin"]),
        "geometric_growth": float(pick["geometric_growth"]),
        "feasible": bool(not feasible.empty),
        "max_ruin_probability": limit,
        "n_feasible": len(feasible),
        "n_cells": len(table),
        "horizon_days": horizon,
        "n_paths": int(n_paths),
        "gate_pass_rate": float(gate_pass_rate),
        "edge_sharpe_annual": float(edge_sharpe_annual),
        "current": (None if current.empty else {k: _py(v) for k, v in current.iloc[0].to_dict().items()}),
        "current_in_grid": bool(not current.empty),
        "note": (
            "Cella con la crescita geometrica massima tra quelle che rispettano il vincolo sul rischio di rovina."
            if not feasible.empty
            else "Nessuna combinazione rispetta il vincolo: riportata quella con il rischio di rovina minimo."
        ),
    }
    return best, table


def _py(v: Any) -> Any:
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _it(x: Any, nd: int = 3) -> str:
    """Italian number formatting (decimal comma); ``n/d`` when missing."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/d"
    return f"{float(x):.{nd}f}".replace(".", ",")


def calibration_markdown(best: Mapping[str, Any], table: pd.DataFrame) -> str:
    """The calibration grid as an Italian markdown table, selected cell and current config marked."""
    lines = [
        "### Calibrazione Monte Carlo (crescita geometrica con vincolo sul rischio di rovina)",
        "",
        f"Vincolo: probabilità di rovina <= {_it(best.get('max_ruin_probability'), 2)} "
        f"su {best.get('horizon_days')} giorni, {best.get('n_paths')} percorsi per cella, "
        f"gate superato nel {_it(100 * float(best.get('gate_pass_rate', 0.0)), 0)}% dei giorni.",
        "",
        "| Kelly | Budget ES99 | P(rovina) | P(conto azzerato) | Crescita geom. | Equity mediana | "
        "DD mediano | Ammessa |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, row in table.iterrows():
        mark = ""
        if bool(row.get("is_current")):
            mark += " (config attuale)"
        if math.isclose(float(row["kelly_fraction"]), float(best["kelly_fraction"]), rel_tol=1e-9) and math.isclose(
            float(row["es99_budget"]), float(best["es99_budget"]), rel_tol=1e-9
        ):
            mark += " **<- scelta**"
        lines.append(
            f"| {_it(row['kelly_fraction'], 2)} | {_it(row['es99_budget'], 2)} | {_it(row['prob_ruin'], 4)} | "
            f"{_it(row['prob_dead'], 4)} | {_it(row['geometric_growth'])} | {_it(row['median_terminal'], 0)} | "
            f"{_it(row['median_max_drawdown'], 3)} | {'sì' if row['feasible'] else 'no'}{mark} |"
        )
    lines += ["", f"Esito: {best.get('note', '')}"]
    cur = best.get("current")
    if isinstance(cur, Mapping):
        lines.append(
            f"Config attuale (kelly {_it(cur.get('kelly_fraction'), 2)}, ES99 {_it(cur.get('es99_budget'), 2)}): "
            f"P(rovina) {_it(cur.get('prob_ruin'), 4)}, crescita geometrica {_it(cur.get('geometric_growth'))}, "
            f"vincolo {'rispettato' if cur.get('feasible') else 'NON rispettato'}."
        )
    lines.append(
        f"Raccomandazione: kelly_fraction {_it(best['kelly_fraction'], 2)}, "
        f"es99_budget {_it(best['es99_budget'], 2)} (config/risk.yaml non viene modificato da questo modulo)."
    )
    return "\n".join(lines)
