"""S7 - Physical confirmation (docs/STRATEGIES.md).

Thesis: a flat-price rally with flat spreads is a speculative/financial premium and tends to deflate. A rally
where the spreads widen with it is a real physical squeeze and should be followed. The same check is condition 4
of the leverage gate (brief section 9) for longs on geopolitical spikes.

Signal (exactly as documented):
  * sigma = rv_yz_21 * sqrt(5/252) (the 5-day 1-sigma move);
  * ret_5 > +1.5 sigma: short (fade) when spread_chg_5 <= 0 and spot_front_premium is not rising;
    long (follow) when spread_chg_5 > 0 and cushing_vs_5y < 0;
  * mirrored for sell-offs (ret_5 < -1.5 sigma): long when the spreads did not confirm the fall,
    short when spread_chg_5 < 0 and cushing_vs_5y > 0;
  * nothing at all when the curve is a proxy (curve_approx == 1): no fade without real spreads.

The fade leg uses a tight stop (1.5 ATR) and the follow leg a wider one (2.5 ATR); `expected_return` carries the
deliberate 0.5 shrinkage factor used across the engine.
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features.catalog import (
    ATR_14,
    CURVE_APPROX,
    CUSHING_VS_5Y,
    RET_5,
    RV_YZ_21,
    SPOT_FRONT_PREMIUM,
    SPREAD_CHG_5,
)
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, fmt_pct, prob_from_z, vol_scale

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting)


class S7PhysicalConfirmation(Strategy):
    id = "S7"
    name = "Conferma fisica"
    family = Family.CARRY
    horizon_days = 5
    warmup_days = 300
    requires = (RET_5, RV_YZ_21, SPREAD_CHG_5, SPOT_FRONT_PREMIUM, ATR_14)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "ret_sigma_mult": 1.5,  # docs: ret_5 beyond +-1.5 sigma
            "premium_lookback": 5,  # window used to decide whether spot_front_premium is rising
            "cushing_threshold": 0.0,  # docs: cushing_vs_5y < 0 confirms the squeeze on a rally
            "stop_atr_mult_fade": 1.5,  # docs: fade with a tight stop
            "stop_atr_mult_follow": 2.5,
            "target_vol": 0.15,
            "prob_z_scale": 1.5,
            "fade_size_mult": 0.75,  # the fade is the lower-conviction leg of the two
        }

    # ---------------------------------------------------------------- leverage gate (brief section 9, cond. 4)
    @staticmethod
    def physical_confirms(ctx: MarketContext, direction: Direction | int) -> bool:
        """True when the curve physically confirms a position in `direction`. Stateless by design.

        Imported by engine/portfolio/gate.py for condition 4 of the leverage gate, so the signature is stable:
        it takes only the point-in-time context and a direction, and needs no position or account state.
        Confirmation for a LONG = spreads widening (spread_chg_5 > 0) AND a positive spot premium over the front
        (spot_front_premium > 0); for a SHORT the mirror (spreads narrowing and the spot at a discount).
        Returns False - never True - when the curve is only a proxy or any required feature is missing.
        """
        if ctx.f(CURVE_APPROX, 1.0) >= 0.5:
            return False
        chg5 = ctx.f(SPREAD_CHG_5)
        premium = ctx.f(SPOT_FRONT_PREMIUM)
        if math.isnan(chg5) or math.isnan(premium):
            return False
        side = direction.sign if isinstance(direction, Direction) else int((direction > 0) - (direction < 0))
        if side > 0:
            return chg5 > 0.0 and premium > 0.0
        if side < 0:
            return chg5 < 0.0 and premium < 0.0
        return False

    # ---------------------------------------------------------------------------------------- signal
    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None
        if ctx.f(CURVE_APPROX, 1.0) >= 0.5:
            return None  # no fade without real spreads

        rv = ctx.f(RV_YZ_21)
        atr = ctx.f(ATR_14)
        ret5 = ctx.f(RET_5)
        chg5 = ctx.f(SPREAD_CHG_5)
        premium = ctx.f(SPOT_FRONT_PREMIUM)
        if any(math.isnan(v) for v in (rv, atr, ret5, chg5, premium)) or rv <= 0 or atr <= 0:
            return None

        sigma = expected_move(rv, self.horizon_days)  # rv_yz_21 * sqrt(5/252)
        if sigma <= 0:
            return None
        threshold = float(self.params["ret_sigma_mult"]) * sigma
        if abs(ret5) <= threshold:
            return None

        lookback = int(self.params["premium_lookback"])
        prem_series = ctx.hist(SPOT_FRONT_PREMIUM, lookback + 1).dropna()
        if len(prem_series) < 2:
            return None  # cannot tell whether the premium is rising: never guess
        prem_delta = float(prem_series.iloc[-1]) - float(prem_series.iloc[0])
        cushing = ctx.f(CUSHING_VS_5Y)
        cush_thr = float(self.params["cushing_threshold"])

        direction: Direction | None = None
        branch = ""
        if ret5 > 0:
            if chg5 <= 0 and prem_delta <= 0:
                direction, branch = Direction.SHORT, "fade"
            elif chg5 > 0 and not math.isnan(cushing) and cushing < cush_thr:
                direction, branch = Direction.LONG, "follow"
        # mirrored branches for a sell-off (ret_5 < -1.5 sigma)
        elif chg5 >= 0 and prem_delta >= 0:
            direction, branch = Direction.LONG, "fade"
        elif chg5 < 0 and not math.isnan(cushing) and cushing > cush_thr:
            direction, branch = Direction.SHORT, "follow"
        if direction is None:
            return None

        z = abs(ret5) / sigma
        prob = prob_from_z(z, scale=float(self.params["prob_z_scale"]))
        atr_mult = float(self.params["stop_atr_mult_fade"] if branch == "fade" else self.params["stop_atr_mult_follow"])
        stop_pct = atr_mult * atr
        size_mult = float(self.params["fade_size_mult"]) if branch == "fade" else 1.0
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv) * size_mult, 0.0, 1.0)

        verso = "long" if direction is Direction.LONG else "short"
        mossa = "rally" if ret5 > 0 else "sell-off"
        if branch == "fade":
            motivo = (
                f"spread M1-M6 {fmt_pct(chg5)} in 5 giorni e premio spot {fmt_pct(prem_delta)}: "
                f"premio speculativo da sfumare"
            )
        else:
            motivo = f"spread M1-M6 {fmt_pct(chg5)} e Cushing {fmt_pct(cushing)} vs media 5 anni: stretta fisica reale"
        rationale = (
            f"{mossa.capitalize()} del flat price {fmt_pct(ret5)} ({fmt_num(z)} sigma a 5 giorni), {motivo}: "
            f"{verso}, stop {fmt_pct(stop_pct)}."
        )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * z * sigma * SHRINK,
            expected_vol=sigma,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=strength,
            meta={
                "branch": branch,
                "ret_5": ret5,
                "ret_5_sigma": z,
                "spread_chg_5": chg5,
                "spot_front_premium": premium,
                "spot_premium_delta": prem_delta,
                "cushing_vs_5y": None if math.isnan(cushing) else cushing,
                "physical_confirms": self.physical_confirms(ctx, direction),
            },
        )
