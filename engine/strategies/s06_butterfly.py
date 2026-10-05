"""S6 - Curve butterfly M1 - 2*M3 + M6 (docs/STRATEGIES.md).

Thesis: curvature is dominated by technical flows (index rolls, refinery hedges on specific maturities) and
returns to its equilibrium shape: relative value with a low beta to the flat price.

Signal (exactly as documented):
  * 1y z-score of butterfly_1_3_6; enter beyond +-2, exit at 0.5;
  * the OU half-life estimated on the butterfly must be < 20 days (otherwise no trade);
  * three legs with ratios +1, -2, +1 on M1, M3, M6;
  * same proxy-curve suppression as S5: nothing until there are `min_real_curve_days` real curve observations.

Exit band: inside |z| <= exit_z the strategy emits a FLAT signal carrying meta['exit_band'] = True, i.e. the
actionable "close the fly" instruction, instead of a bare None which would lose the information. Set the
parameter `emit_exit_signal` to False to get None there instead.

butterfly_1_3_6 is a fraction of M1, so its own realized volatility (std of daily changes scaled to the
horizon) is used for `expected_vol`, `expected_return` and the stop: ATR_14 of the flat price is not a
meaningful risk scale for a three-leg, near-zero-priced structure (deliberate, documented departure).
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from engine.core.events import Direction, Signal
from engine.features.catalog import BUTTERFLY_1_3_6, CURVE_APPROX, RV_YZ_21
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, fmt_num, fmt_pct, ou_half_life, prob_from_z, vol_scale, zscore

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting)
FLY_RATIOS = (1.0, -2.0, 1.0)  # M1, M3, M6


def _last_z(series: pd.Series, window: int) -> float:
    s = series.dropna()
    if len(s) < max(10, window // 2):
        return float("nan")
    value = zscore(s, window).iloc[-1]
    return float("nan") if pd.isna(value) else float(value)


def _horizon_sigma(series: pd.Series, horizon_days: int) -> float:
    d = series.dropna().diff().dropna()
    if len(d) < 20:
        return float("nan")
    sd = float(d.std())
    if not math.isfinite(sd) or sd <= 0:
        return float("nan")
    return sd * math.sqrt(max(1, horizon_days))


class S6CurveButterfly(Strategy):
    id = "S6"
    name = "Butterfly di curva 1-3-6"
    family = Family.CARRY
    horizon_days = 10
    warmup_days = 300
    requires = (BUTTERFLY_1_3_6, RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "z_window": 252,  # docs: 1y z-score
            "entry_z": 2.0,  # docs: enter beyond +-2
            "exit_z": 0.5,  # docs: exit at 0.5
            "max_half_life_days": 20.0,  # docs: OU half-life must be < 20 days
            "ou_min_obs": 60,
            "min_real_curve_days": 120,  # docs: starts in incubation until 120 days of real curve
            "target_vol": 0.15,
            "stop_sigma_mult": 2.0,  # stop in butterfly sigmas (see module docstring)
            "prob_z_scale": 1.5,
            "emit_exit_signal": True,
        }

    def _real_curve_ok(self, ctx: MarketContext) -> bool:
        if ctx.f(CURVE_APPROX, 1.0) >= 0.5:
            return False
        need = int(self.params["min_real_curve_days"])
        hist = ctx.hist(CURVE_APPROX, need).dropna()
        return int((hist < 0.5).sum()) >= need

    @staticmethod
    def _symbol(ctx: MarketContext) -> tuple[str, list[tuple[str, float]]] | None:
        codes = [ctx.curve_codes.get(k) for k in ("M1", "M3", "M6")]
        if any(not c for c in codes):
            return None
        legs = [(str(c), r) for c, r in zip(codes, FLY_RATIOS)]
        return "FLY-" + "-".join(str(c) for c in codes), legs

    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None
        if not self._real_curve_ok(ctx):
            return None
        built = self._symbol(ctx)
        if built is None:
            return None
        symbol, legs = built

        rv = ctx.f(RV_YZ_21)
        if math.isnan(rv) or rv <= 0:
            return None

        z_window = int(self.params["z_window"])
        fly_hist = ctx.hist(BUTTERFLY_1_3_6, z_window)
        z = _last_z(fly_hist, z_window)
        sigma_h = _horizon_sigma(fly_hist, self.horizon_days)
        if math.isnan(z) or math.isnan(sigma_h):
            return None

        half_life = ou_half_life(fly_hist, min_obs=int(self.params["ou_min_obs"]))
        if half_life is None or half_life >= float(self.params["max_half_life_days"]):
            return None

        exit_z = float(self.params["exit_z"])
        entry_z = float(self.params["entry_z"])
        meta: dict[str, Any] = {
            "multi_leg": True,
            "legs": legs,
            "z": z,
            "half_life_days": half_life,
            "fly_sigma_horizon": sigma_h,
        }

        if abs(z) <= exit_z:
            if not bool(self.params["emit_exit_signal"]):
                return None
            meta["exit_band"] = True
            return self.make_signal(
                ctx,
                Direction.FLAT,
                prob=0.5,
                expected_return=0.0,
                expected_vol=sigma_h,
                rationale=(
                    f"Butterfly 1-3-6 rientrata a {fmt_num(z)} sigma (banda di uscita {fmt_num(exit_z)}, "
                    f"half-life {fmt_num(half_life)} giorni): chiudo {symbol}."
                ),
                instrument=symbol,
                strength=0.0,
                meta=meta,
            )

        if abs(z) < entry_z:
            return None

        # mean reversion: rich curvature (high z) is sold, cheap curvature is bought
        direction = Direction.SHORT if z > 0 else Direction.LONG
        prob = prob_from_z(abs(z), scale=float(self.params["prob_z_scale"]))
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv), 0.0, 1.0)
        stop_pct = float(self.params["stop_sigma_mult"]) * sigma_h
        verso = "long" if direction is Direction.LONG else "short"
        rationale = (
            f"Curvatura M1-2*M3+M6 a {fmt_num(z)} sigma su 1 anno (livello {fmt_pct(ctx.f(BUTTERFLY_1_3_6))}, "
            f"half-life OU {fmt_num(half_life)} giorni): {verso} butterfly {symbol}, stop {fmt_pct(stop_pct)}."
        )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * abs(z) * sigma_h * SHRINK,
            expected_vol=sigma_h,
            rationale=rationale,
            instrument=symbol,
            stop_pct=stop_pct,
            strength=strength,
            meta=meta,
        )
