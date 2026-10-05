"""S14 - Serious seasonality (docs/STRATEGIES.md §S14).

Thesis: refinery maintenance (Feb-Mar, Sep-Oct), driving season (May-Aug), hurricanes (Aug-Oct) and heating demand
(Nov-Feb) leave a footprint in inventories and crack spreads. Only effects that survive a multiple-testing
correction are used, and ONLY as a TILT (+-25% of the size of other strategies) - never as a standalone position.

Estimation (causal by construction):
  * take `ctx.hist(RET_1, lookback_years * 252)` - trailing returns only, nothing beyond the last row;
  * aggregate the daily log returns into ISO (year, week) buckets -> one weekly return per ISO week;
  * DROP the ISO week of `ctx.ts` IN THE CURRENT ISO YEAR: the week being traded must not contribute to its own
    estimate (that is the look-ahead that would otherwise sneak in);
  * keep the last `lookback_years` ISO years;
  * for the CURRENT ISO week-of-year, compute the mean weekly return over the sampled years, its t-statistic
    (t = mean / (sd / sqrt(n)), df = n - 1) and the two-sided p-value;
  * BONFERRONI over 52 tests (one per week of the year): accept only when `p * 52 < alpha` AND |t| > `min_abs_t`
    (docs: "t > 2 dopo Bonferroni").

Output: a TILT signal - direction = sign of the effect, `strength` = `tilt_strength` (0.25), `prob` = `tilt_prob`
(0.55), `meta['tilt_only'] = True`. The baseline allocator (`engine/portfolio/base.py`) already skips signals
carrying `tilt_only`, so this strategy can never open a position by itself.

Disabled (returns None) when the regime label is the shock/crash one or contains "geopolitico" (docs:
"Disattivato in Shock/crash e geopolitico").
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.regime.base import LABEL_SHOCK_CRASH
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, fmt_pct, sign

WEEKS_PER_YEAR = 52  # number of simultaneous tests for the Bonferroni correction


def _disabled_regime(label: str) -> bool:
    low = label.lower()
    return label == LABEL_SHOCK_CRASH or "shock" in low or "geopolitico" in low


class S14Seasonality(Strategy):
    id = "S14"
    name = "Stagionalità seria"
    family = Family.FUNDAMENTAL
    horizon_days = 5
    warmup_days = 252 * 5  # at least 5 ISO years before any seasonal claim is made
    requires = (cat.RV_YZ_21,)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "lookback_years": 20,  # docs: 20 anni di storia
            "alpha": 0.05,  # family-wise error rate before the Bonferroni split
            "n_tests": WEEKS_PER_YEAR,  # docs: correzione per 52 test
            "min_abs_t": 2.0,  # docs: t > 2 dopo Bonferroni
            "min_years": 5,  # minimum observations (years) for the current week
            "tilt_strength": 0.25,  # docs: tilt ±25% della size
            "tilt_prob": 0.55,
            "horizon_days": 5,
            "z_cap": 3.0,
            "stop_atr": 2.0,
            "stop_sigma": 2.0,
        }

    # ------------------------------------------------------------------ estimation
    def week_effect(self, ctx: MarketContext) -> dict[str, float] | None:
        """Causal estimate of the current ISO week's effect. None when the sample is insufficient."""
        p = self.params
        years = max(1, int(p["lookback_years"]))
        h = ctx.hist(cat.RET_1, years * 252).dropna()
        if len(h) < 60:
            return None
        try:
            iso = pd.DatetimeIndex(h.index).isocalendar()
        except (TypeError, ValueError):
            return None
        iso_year = iso["year"].to_numpy().astype(int)
        iso_week = iso["week"].to_numpy().astype(int)
        key = iso_year * 100 + iso_week

        weekly = pd.DataFrame({"key": key, "week": iso_week, "ret": h.to_numpy().astype(float)})
        grouped = weekly.groupby("key", sort=True).agg(week=("week", "first"), ret=("ret", "sum"))

        cur = ctx.ts.isocalendar()
        cur_year, cur_week = int(cur.year), int(cur.week)
        grouped = grouped.drop(index=cur_year * 100 + cur_week, errors="ignore")  # exclude the week being traded
        grouped = grouped[(grouped.index // 100) >= (cur_year - years)]

        sample = grouped.loc[grouped["week"] == cur_week, "ret"].to_numpy().astype(float)
        n = int(sample.size)
        if n < max(2, int(p["min_years"])):
            return None
        mean = float(np.mean(sample))
        sd = float(np.std(sample, ddof=1))
        if not math.isfinite(sd) or sd <= 0.0:
            return None
        tstat = mean / (sd / math.sqrt(n))
        pval = float(2.0 * student_t.sf(abs(tstat), n - 1))
        return {
            "week": float(cur_week),
            "n_years": float(n),
            "mean": mean,
            "sd": sd,
            "t": float(tstat),
            "p_value": pval,
            "p_bonferroni": float(min(1.0, pval * float(p["n_tests"]))),
        }

    # ------------------------------------------------------------------ entry point
    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        if _disabled_regime(ctx.regime.label):
            return None
        rv = ctx.f(cat.RV_YZ_21)
        if math.isnan(rv):
            return None
        eff = self.week_effect(ctx)
        if eff is None:
            return None
        if eff["p_bonferroni"] >= float(p["alpha"]) or abs(eff["t"]) <= float(p["min_abs_t"]):
            return None  # not significant after Bonferroni: no seasonal claim
        side = sign(eff["mean"])
        if side == 0:
            return None

        horizon = max(1, int(p["horizon_days"]))
        em = expected_move(rv, horizon)
        z = clamp(abs(eff["t"]), 0.0, float(p["z_cap"]))
        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        verso = "rialzista" if side > 0 else "ribassista"
        rationale = (
            f"Settimana ISO {int(eff['week'])}: effetto stagionale {verso} medio {fmt_pct(eff['mean'])} "
            f"su {int(eff['n_years'])} anni, t {fmt_num(eff['t'])} significativo dopo Bonferroni "
            f"(p corretto {fmt_num(eff['p_bonferroni'], 4)}); solo inclinazione, mai posizione autonoma."
        )
        return self.make_signal(
            ctx,
            Direction.from_sign(side),
            prob=float(p["tilt_prob"]),
            expected_return=side * z * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=float(p["tilt_strength"]),
            horizon_days=horizon,
            meta={
                "tilt_only": True,
                "iso_week": int(eff["week"]),
                "n_years": int(eff["n_years"]),
                "mean_weekly_return": eff["mean"],
                "t_stat": eff["t"],
                "p_value": eff["p_value"],
                "p_bonferroni": eff["p_bonferroni"],
                "n_tests": int(p["n_tests"]),
            },
        )
