"""S10 - Macro fair value (docs/STRATEGIES.md §S10).

Thesis: oil carries time-varying betas to the dollar, real rates, equities and copper. A Kalman filter estimates
those betas and `MACRO_FV_Z` is the z-score of the residual (price minus macro fair value). When the price is far
from the macro fair value WITHOUT fundamental confirmation (inventories not extreme) and WITHOUT a geopolitical
spike, the residual tends to close: mean reversion toward fair value.

Entry: |MACRO_FV_Z| > `entry_z` (±2) AND `GEO_SPIKE` == 0 AND |CRUDE_STOCKS_VS_5Y| < `max_stock_dev`.
Direction: a positive residual means the price is ABOVE fair value -> short; mirrored for a negative residual.
In a geopolitical regime the residual is dominated by the risk premium, so the size is multiplied by
`geo_regime_strength` (docs: "Sfavorevole: geopolitico").

Sizing / risk (project convention):
  * strength  = vol_scale(target_vol, RV_YZ_21) * ramp in [0.5, 1] with the excess of |z| over `entry_z`;
  * prob      = prob_from_z(z) (conservative, shrunk toward 0.5);
  * exp. ret  = sign * |z| * expected_move(RV_YZ_21, horizon) * 0.5  (0.5 = documented shrinkage);
  * exp. vol  = expected_move(RV_YZ_21, horizon);
  * stop      = `stop_atr` * ATR_14, falling back to `stop_sigma` * expected_move when ATR_14 is missing;
  * target    = (|z| - `exit_z`) * expected_move * 0.5, i.e. the move implied by closing the residual to `exit_z`.
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, fmt_pct, prob_from_z, sign, vol_scale


def _is_geo_regime(label: str) -> bool:
    return "geopolitico" in label.lower()


class S10MacroFairValue(Strategy):
    id = "S10"
    name = "Fair value macro"
    family = Family.RELATIVE_VALUE
    horizon_days = 10
    warmup_days = 300
    requires = (cat.MACRO_FV_Z, cat.GEO_SPIKE, cat.CRUDE_STOCKS_VS_5Y, cat.RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "entry_z": 2.0,  # docs: residuo oltre ±2
            "exit_z": 0.5,  # residuo considerato chiuso
            "max_stock_dev": 0.08,  # |crude_stocks_vs_5y| oltre il quale le scorte sono "estreme"
            "geo_regime_strength": 0.5,  # size multiplier when the regime label contains "geopolitico"
            "horizon_days": 10,
            "target_vol": 0.15,
            "stop_atr": 2.0,
            "stop_sigma": 2.0,
            "z_cap": 4.0,  # |z| cap used for sizing/expected return
        }

    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        z = ctx.f(cat.MACRO_FV_Z)
        geo_spike = ctx.f(cat.GEO_SPIKE)
        stock_dev = ctx.f(cat.CRUDE_STOCKS_VS_5Y)
        rv = ctx.f(cat.RV_YZ_21)
        if math.isnan(z) or math.isnan(geo_spike) or math.isnan(stock_dev) or math.isnan(rv):
            return None  # required feature missing: never guess

        entry_z = float(p["entry_z"])
        if geo_spike != 0.0:
            return None  # geopolitical spike: the residual is risk premium, not mispricing
        if abs(stock_dev) >= float(p["max_stock_dev"]):
            return None  # fundamentals confirm the deviation
        if abs(z) < entry_z:
            return None

        side = sign(-z)
        if side == 0:
            return None
        horizon = max(1, int(p["horizon_days"]))
        z_abs = min(abs(z), float(p["z_cap"]))
        em = expected_move(rv, horizon)

        ramp = clamp(0.5 + 0.5 * (z_abs - entry_z) / entry_z, 0.5, 1.0)
        strength = vol_scale(float(p["target_vol"]), rv) * ramp
        if _is_geo_regime(ctx.regime.label):
            strength *= float(p["geo_regime_strength"])

        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        target_pct = max(0.0, z_abs - float(p["exit_z"])) * em * 0.5

        verso = "sopra" if z > 0 else "sotto"
        rationale = (
            f"Prezzo {verso} il fair value macro: residuo z {fmt_num(z)} oltre la soglia ±{fmt_num(entry_z)}, "
            f"scorte vicine alla norma ({fmt_pct(stock_dev)}) e nessun picco geopolitico; "
            f"ritorno atteso verso il fair value su {horizon} giorni."
        )
        return self.make_signal(
            ctx,
            Direction.from_sign(side),
            prob=prob_from_z(z_abs),
            expected_return=side * z_abs * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            target_pct=target_pct,
            strength=strength,
            horizon_days=horizon,
            meta={
                "macro_fv_z": z,
                "macro_fv_resid": ctx.f(cat.MACRO_FV_RESID),
                "crude_stocks_vs_5y": stock_dev,
                "entry_z": entry_z,
                "exit_z": float(p["exit_z"]),
                "geo_regime": _is_geo_regime(ctx.regime.label),
            },
        )
