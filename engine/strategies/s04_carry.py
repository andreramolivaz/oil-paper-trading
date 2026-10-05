"""S4 - Carry and roll yield (docs/STRATEGIES.md).

Thesis: backwardation signals low inventories and a high convenience yield (theory of storage): buying the front
and rolling earns the roll yield, and low stocks make prices fragile to the upside. Contango signals a glut and
pays to be short. Carry is one of the few documented commodity risk premia.

Signal (exactly as documented):
  * long when roll_yield_pctl > 0.60, short when < 0.40 (5y percentile of roll_yield_ann);
  * size proportional to |percentile - 0.5|, vol-scaled;
  * size HALVED when the curve is a proxy (curve_approx == 1).

`expected_return` carries the deliberate 0.5 shrinkage factor used across the engine. The stop is wider than for
the trend strategies (3 ATR by default) because carry is a slow premium and should not be stopped out by noise.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from engine.core.events import Direction, Signal
from engine.features.catalog import ATR_14, CURVE_APPROX, ROLL_YIELD_ANN, ROLL_YIELD_PCTL, RV_YZ_21
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, fmt_pct, prob_from_z, vol_scale, zscore

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting)


def _last_z(series: pd.Series, window: int) -> float:
    s = series.dropna()
    if len(s) < max(10, window // 2):
        return float("nan")
    value = zscore(s, window).iloc[-1]
    return float("nan") if pd.isna(value) else float(value)


class S4CarryRollYield(Strategy):
    id = "S4"
    name = "Carry e roll yield"
    family = Family.CARRY
    horizon_days = 21
    warmup_days = 300
    requires = (ROLL_YIELD_ANN, ROLL_YIELD_PCTL, RV_YZ_21, ATR_14)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "long_pctl": 0.60,  # docs: long above the 60th percentile
            "short_pctl": 0.40,  # docs: short below the 40th percentile
            "z_window": 252,  # window for the roll-yield z used by prob / expected return
            "approx_size_mult": 0.5,  # docs: size halved when curve_approx == 1
            "target_vol": 0.15,
            "stop_atr_mult": 3.0,
            "prob_z_scale": 1.5,
        }

    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None
        pctl = ctx.f(ROLL_YIELD_PCTL)
        roll = ctx.f(ROLL_YIELD_ANN)
        rv = ctx.f(RV_YZ_21)
        atr = ctx.f(ATR_14)
        if math.isnan(pctl) or math.isnan(roll) or math.isnan(rv) or math.isnan(atr) or rv <= 0 or atr <= 0:
            return None

        if pctl > float(self.params["long_pctl"]):
            direction = Direction.LONG
        elif pctl < float(self.params["short_pctl"]):
            direction = Direction.SHORT
        else:
            return None

        z_window = int(self.params["z_window"])
        z = _last_z(ctx.hist(ROLL_YIELD_ANN, z_window), z_window)
        if math.isnan(z):
            return None  # no honest magnitude without enough history

        approx = ctx.f(CURVE_APPROX, 0.0) >= 0.5
        size_mult = clamp(abs(pctl - 0.5) * 2.0, 0.0, 1.0)
        if approx:
            size_mult *= float(self.params["approx_size_mult"])
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv) * size_mult, 0.0, 1.0)

        horizon = self.horizon_days
        sigma_h = expected_move(rv, horizon)
        prob = prob_from_z(abs(z), scale=float(self.params["prob_z_scale"]))
        stop_pct = float(self.params["stop_atr_mult"]) * atr

        verso = "long" if direction is Direction.LONG else "short"
        curva = "backwardation" if roll > 0 else "contango"
        nota = " curva proxy (size dimezzata, ≈)" if approx else ""
        rationale = (
            f"Roll yield annualizzato {fmt_pct(roll)} in {curva}, percentile {fmt_num(pctl)} "
            f"(z {fmt_num(z)}):{nota} {verso} con size {fmt_num(strength)}, stop {fmt_pct(stop_pct)}."
        )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * abs(z) * sigma_h * SHRINK,
            expected_vol=sigma_h,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=strength,
            horizon_days=horizon,
            meta={
                "roll_yield_ann": roll,
                "roll_yield_pctl": pctl,
                "roll_yield_z": z,
                "curve_approx": approx,
                "approx": approx,
            },
        )
