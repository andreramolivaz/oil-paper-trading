"""S1 - Multi-horizon momentum filtered by carry (docs/STRATEGIES.md).

Thesis: hedging flows and fundamental news diffuse into prices over weeks, so time-series momentum over
1-12 months captures the drift. The curve is the filter: an uptrend in backwardation is *paid* (positive roll
yield, low inventories), an uptrend in contango fights the carry.

Signal (exactly as documented):
  * weighted mean of the signs of tsmom_10/21/63/126/252, longer horizons weighted more -> direction;
  * vol-scaled size (vol_scale(target_vol, rv_yz_21));
  * full size when sign(trend) == sign(slope_m1_m6), halved when they diverge;
  * skipped entirely when cot_crowding > 0.9 on the same side as the trade.

`expected_return` uses the deliberate 0.5 shrinkage factor applied everywhere in the engine: the raw
|z| * sigma_horizon estimate is optimistic in sample, so we halve it before it reaches the allocator.
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features.catalog import (
    ATR_14,
    COT_CROWDING,
    COT_MM_NET_BRENT_PCTL,
    RV_YZ_21,
    SLOPE_M1_M6,
    TSMOM_10,
    TSMOM_21,
    TSMOM_63,
    TSMOM_126,
    TSMOM_252,
)
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, fmt_pct, prob_from_z, sign, vol_scale

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting, see module docstring)

_TSMOM = {10: TSMOM_10, 21: TSMOM_21, 63: TSMOM_63, 126: TSMOM_126, 252: TSMOM_252}


class S1MomentumCarry(Strategy):
    id = "S1"
    name = "Momentum multi-orizzonte filtrato dal carry"
    family = Family.TREND
    horizon_days = 21
    warmup_days = 300
    requires = (TSMOM_10, TSMOM_21, TSMOM_63, TSMOM_126, TSMOM_252, RV_YZ_21, SLOPE_M1_M6, ATR_14)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            # longer horizons weighted more (1..5); keys are the tsmom lookbacks of the catalogue
            "horizon_weights": {10: 1.0, 21: 2.0, 63: 3.0, 126: 4.0, 252: 5.0},
            "min_trend_score": 0.2,  # |weighted mean of signs| below this = horizons disagree, stay flat
            "cot_crowding_max": 0.9,  # docs: zero size when cot_crowding > 0.9 in the same direction
            "divergence_size_mult": 0.5,  # halved when trend and curve slope diverge
            "target_vol": 0.15,
            "stop_atr_mult": 2.0,
            "prob_z_scale": 1.5,  # conservative mapping of the trend z into a probability
        }

    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None
        weights: dict[int, float] = {int(k): float(v) for k, v in self.params["horizon_weights"].items()}
        total_w = sum(weights.values())
        if total_w <= 0:
            return None

        score = 0.0
        z_raw = 0.0
        for lookback, weight in weights.items():
            name = _TSMOM.get(lookback)
            if name is None:
                return None
            value = ctx.f(name)
            if math.isnan(value):
                return None  # never guess a missing horizon
            score += weight * sign(value)
            z_raw += weight * value
        score /= total_w
        z_raw /= total_w

        if abs(score) < float(self.params["min_trend_score"]):
            return None
        if sign(score) != sign(z_raw):
            return None  # signs and magnitudes disagree: no honest direction
        direction = Direction.from_sign(score)

        rv = ctx.f(RV_YZ_21)
        atr = ctx.f(ATR_14)
        slope = ctx.f(SLOPE_M1_M6)
        if math.isnan(rv) or math.isnan(atr) or math.isnan(slope) or rv <= 0 or atr <= 0:
            return None

        # crowding veto: COT_CROWDING is |pctl-0.5|*2, the crowded side comes from the percentile itself
        crowding = ctx.f(COT_CROWDING)
        crowded_side = 0
        if not math.isnan(crowding) and crowding > float(self.params["cot_crowding_max"]):
            pctl = ctx.f(COT_MM_NET_BRENT_PCTL)
            if math.isnan(pctl):
                return None  # crowded but we cannot tell on which side: stay out
            crowded_side = sign(pctl - 0.5)
            if crowded_side == direction.sign:
                return None

        carry_agrees = sign(slope) == direction.sign and sign(slope) != 0
        carry_mult = 1.0 if carry_agrees else float(self.params["divergence_size_mult"])
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv) * carry_mult, 0.0, 1.0)

        z = abs(z_raw)
        horizon = self.horizon_days
        sigma_h = expected_move(rv, horizon)
        prob = prob_from_z(z, scale=float(self.params["prob_z_scale"]))
        stop_pct = float(self.params["stop_atr_mult"]) * atr

        verso = "long" if direction is Direction.LONG else "short"
        curva = "backwardation" if slope > 0 else "contango"
        accordo = "concorde" if carry_agrees else "discorde (size dimezzata)"
        rationale = (
            f"Momentum multi-orizzonte {fmt_num(score)} (z {fmt_num(z)} sigma) {accordo} "
            f"con la curva in {curva} (M1-M6 {fmt_pct(slope)}): {verso}, stop {fmt_pct(stop_pct)}."
        )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * z * sigma_h * SHRINK,
            expected_vol=sigma_h,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=strength,
            horizon_days=horizon,
            meta={
                "trend_score": score,
                "trend_z": z_raw,
                "slope_m1_m6": slope,
                "carry_agrees": carry_agrees,
                "cot_crowding": None if math.isnan(crowding) else crowding,
                "crowded_side": crowded_side,
            },
        )
