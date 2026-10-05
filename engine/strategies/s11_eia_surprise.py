"""S11 - EIA inventory surprise (docs/STRATEGIES.md §S11).

Thesis: the market trades the SURPRISE against expectations, not the level. We have no consensus feed, so the
expectation is modelled in `engine/features/fundamentals.py` (5y seasonal average of the week + recent trend +
import/run adjustment) and the surprise z-scores are point-in-time features. Funds adjust gradually, so the
surprise produces a 1-3 day drift.

RELEASE-DAY GATE: this strategy only ever fires on the WPSR release day, i.e. `DAYS_SINCE_WPSR == 0`
(Wednesday 10:30 ET, Thursday after a US holiday). On any other day it returns None - there is no stale-surprise
trade here.

COMBINED SURPRISE (sign convention, measured on a BEARISH scale - positive = bearish for crude):

    z = 0.40 * STOCK_SURPRISE_Z + 0.25 * CUSHING_SURPRISE_Z - 0.25 * IMPLIED_DEMAND_Z + 0.10 * REFINERY_INPUTS_Z

  * STOCK_SURPRISE_Z   > 0 = a bigger crude BUILD than the model expected          -> bearish  -> weight +0.40
  * CUSHING_SURPRISE_Z > 0 = a bigger build at the WTI delivery hub                -> bearish  -> weight +0.25
  * IMPLIED_DEMAND_Z   > 0 = stronger implied product demand                       -> BULLISH  -> weight -0.25
  * REFINERY_INPUTS_Z  > 0 = higher crude runs than expected                       -> bearish  -> weight +0.10
    (a crude draw explained by high runs is mechanical, not demand: it pushes barrels into products rather than
    signalling tightness, so the runs surprise enters with the same sign as the stock surprise.)

Direction = -sign(z): a bearish combined surprise (z > 0) gives a SHORT, a bullish one (z < 0) a LONG. The trade
only exists when |z| > `min_z` (default 1.0, docs: "sotto -1 -> long, sopra +1 -> short").
Horizon 3 days with `meta['ttl_days'] = 3`; the weight is halved in a geopolitical regime (docs).
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, prob_from_z, sign, vol_scale

# component weights on the bearish scale (documented above, tested in tests/test_strategies_g4.py)
W_STOCK = 0.40
W_CUSHING = 0.25
W_IMPLIED_DEMAND = -0.25
W_REFINERY_INPUTS = 0.10


def _is_geo_regime(label: str) -> bool:
    return "geopolitico" in label.lower()


class S11EiaSurprise(Strategy):
    id = "S11"
    name = "Sorpresa sulle scorte EIA"
    family = Family.FUNDAMENTAL
    horizon_days = 3
    warmup_days = 300
    requires = (
        cat.DAYS_SINCE_WPSR,
        cat.STOCK_SURPRISE_Z,
        cat.CUSHING_SURPRISE_Z,
        cat.IMPLIED_DEMAND_Z,
        cat.REFINERY_INPUTS_Z,
        cat.RV_YZ_21,
    )

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "min_z": 1.0,  # docs: |z| combinato oltre 1
            "w_stock": W_STOCK,
            "w_cushing": W_CUSHING,
            "w_implied_demand": W_IMPLIED_DEMAND,
            "w_refinery_inputs": W_REFINERY_INPUTS,
            "horizon_days": 3,
            "ttl_days": 3,
            "geo_regime_strength": 0.5,  # docs: in regime geopolitico il peso è dimezzato
            "target_vol": 0.15,
            "stop_atr": 1.5,
            "stop_sigma": 1.5,
            "z_cap": 3.0,
        }

    @staticmethod
    def combined_z(
        stock_z: float,
        cushing_z: float,
        implied_demand_z: float,
        refinery_inputs_z: float,
        weights: dict[str, float] | None = None,
    ) -> float:
        """Combined surprise on the BEARISH scale (positive = bearish for crude). See module docstring."""
        w = weights or {
            "w_stock": W_STOCK,
            "w_cushing": W_CUSHING,
            "w_implied_demand": W_IMPLIED_DEMAND,
            "w_refinery_inputs": W_REFINERY_INPUTS,
        }
        return float(
            w["w_stock"] * stock_z
            + w["w_cushing"] * cushing_z
            + w["w_implied_demand"] * implied_demand_z
            + w["w_refinery_inputs"] * refinery_inputs_z
        )

    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        days_since = ctx.f(cat.DAYS_SINCE_WPSR)
        if math.isnan(days_since) or days_since != 0.0:
            return None  # release-day gate: only the WPSR day

        stock_z = ctx.f(cat.STOCK_SURPRISE_Z)
        cushing_z = ctx.f(cat.CUSHING_SURPRISE_Z)
        demand_z = ctx.f(cat.IMPLIED_DEMAND_Z)
        runs_z = ctx.f(cat.REFINERY_INPUTS_Z)
        rv = ctx.f(cat.RV_YZ_21)
        if any(math.isnan(x) for x in (stock_z, cushing_z, demand_z, runs_z, rv)):
            return None  # the four components are required: never substitute a default

        z = self.combined_z(
            stock_z,
            cushing_z,
            demand_z,
            runs_z,
            {
                "w_stock": float(p["w_stock"]),
                "w_cushing": float(p["w_cushing"]),
                "w_implied_demand": float(p["w_implied_demand"]),
                "w_refinery_inputs": float(p["w_refinery_inputs"]),
            },
        )
        min_z = float(p["min_z"])
        if abs(z) <= min_z:
            return None

        side = sign(-z)  # bearish surprise (z > 0) -> short
        if side == 0:
            return None
        horizon = max(1, int(p["horizon_days"]))
        z_abs = min(abs(z), float(p["z_cap"]))
        em = expected_move(rv, horizon)

        strength = vol_scale(float(p["target_vol"]), rv) * clamp(z_abs / float(p["z_cap"]), 0.0, 1.0)
        if _is_geo_regime(ctx.regime.label):
            strength *= float(p["geo_regime_strength"])

        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em

        tono = "ribassista" if z > 0 else "rialzista"
        rationale = (
            f"Giorno del report EIA: sorpresa combinata z {fmt_num(z)} {tono} "
            f"(scorte {fmt_num(stock_z)}, Cushing {fmt_num(cushing_z)}, domanda implicita {fmt_num(demand_z)}, "
            f"lavorazioni {fmt_num(runs_z)}); drift atteso su {horizon} giorni."
        )
        return self.make_signal(
            ctx,
            Direction.from_sign(side),
            prob=prob_from_z(z_abs),
            expected_return=side * z_abs * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=strength,
            horizon_days=horizon,
            meta={
                "ttl_days": int(p["ttl_days"]),
                "combined_z": z,
                "components": {
                    cat.STOCK_SURPRISE_Z: stock_z,
                    cat.CUSHING_SURPRISE_Z: cushing_z,
                    cat.IMPLIED_DEMAND_Z: demand_z,
                    cat.REFINERY_INPUTS_Z: runs_z,
                },
                "weights": {
                    cat.STOCK_SURPRISE_Z: float(p["w_stock"]),
                    cat.CUSHING_SURPRISE_Z: float(p["w_cushing"]),
                    cat.IMPLIED_DEMAND_Z: float(p["w_implied_demand"]),
                    cat.REFINERY_INPUTS_Z: float(p["w_refinery_inputs"]),
                },
                "geo_regime": _is_geo_regime(ctx.regime.label),
            },
        )
