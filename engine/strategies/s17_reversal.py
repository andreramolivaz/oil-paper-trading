"""S17 - Short-term reversal (docs/STRATEGIES.md §S17).

Thesis: in noisy, high-volatility regimes (Hurst < 0.5) part of the 1-5 day move is liquidity and stop hunting,
and it comes back. An OU process fitted on the 5-day returns gives the half-life and therefore the horizon. The
trade is taken ONLY without fundamental confirmation (no EIA surprise, no news spike) - otherwise the move is
information, not noise.

Entry (all conditions required):
  * `HURST_100` < `max_hurst` (0.5) - the series is anti-persistent;
  * |RET_5| > `ret_sigma_mult` (2.0) * sigma_5, with sigma_5 = expected_move(RV_YZ_21, 5) = RV * sqrt(5/252);
  * |`STOCK_SURPRISE_Z`| < `max_stock_surprise_z` (1.0) OR the surprise is NaN (no release in the window);
  * `GEO_SPIKE` == 0.

Direction: contrarian to RET_5. Horizon: `round(ou_half_life(ctx.hist(RET_5, ou_window)))` clamped to
[`min_horizon_days`, `max_horizon_days`] = [1, 5]; `default_horizon_days` (3) when the OU fit returns None (not
mean reverting or too short a sample). Stop: `stop_sigma` (1.5) * expected_move(RV_YZ_21, horizon).

Size is halved (`shock_strength`) when the regime label is the shock/crash one: docs put this regime among the
favourable ones but explicitly "con size ridotta".
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.regime.base import LABEL_SHOCK_CRASH
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, ou_half_life, prob_from_z, sign, vol_scale


def _giorni(n: int) -> str:
    return "1 giorno" if n == 1 else f"{n} giorni"


class S17ShortReversal(Strategy):
    id = "S17"
    name = "Reversal di breve"
    family = Family.VOLATILITY
    horizon_days = 3
    warmup_days = 300
    requires = (cat.HURST_100, cat.RET_5, cat.RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "max_hurst": 0.5,  # docs: hurst_100 < 0,5
            "ret_sigma_mult": 2.0,  # docs: ret_5 oltre ±2 sigma
            "max_stock_surprise_z": 1.0,  # docs: stock_surprise_z neutro
            "ou_window": 252,
            "min_horizon_days": 1,
            "max_horizon_days": 5,  # docs: half-life OU con cap 5 giorni
            "default_horizon_days": 3,
            "stop_sigma": 1.5,  # docs: stop 1,5 sigma
            "shock_strength": 0.5,  # size ridotta in "Shock/crash"
            "target_vol": 0.15,
            "z_cap": 4.0,
        }

    def _horizon(self, ctx: MarketContext) -> tuple[int, float | None]:
        p = self.params
        hl = ou_half_life(ctx.hist(cat.RET_5, max(2, int(p["ou_window"]))))
        lo, hi = int(p["min_horizon_days"]), int(p["max_horizon_days"])
        if hl is None or not math.isfinite(hl):
            return int(clamp(float(p["default_horizon_days"]), float(lo), float(hi))), None
        return int(clamp(float(round(hl)), float(lo), float(hi))), float(hl)

    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        hurst = ctx.f(cat.HURST_100)
        ret_5 = ctx.f(cat.RET_5)
        rv = ctx.f(cat.RV_YZ_21)
        geo_spike = ctx.f(cat.GEO_SPIKE)
        if math.isnan(hurst) or math.isnan(ret_5) or math.isnan(rv) or math.isnan(geo_spike):
            return None
        if hurst >= float(p["max_hurst"]):
            return None  # persistent regime: reversal does not belong here
        if geo_spike != 0.0:
            return None  # a news spike is information, not noise

        surprise = ctx.f(cat.STOCK_SURPRISE_Z)
        if not math.isnan(surprise) and abs(surprise) >= float(p["max_stock_surprise_z"]):
            return None  # the move is explained by the EIA report

        sigma_5 = expected_move(rv, 5)
        if sigma_5 <= 0.0:
            return None
        z = abs(ret_5) / sigma_5
        if z <= float(p["ret_sigma_mult"]):
            return None
        side = sign(-ret_5)
        if side == 0:
            return None

        horizon, half_life = self._horizon(ctx)
        em = expected_move(rv, horizon)
        z_abs = min(z, float(p["z_cap"]))
        stop_pct = float(p["stop_sigma"]) * em
        strength = vol_scale(float(p["target_vol"]), rv) * clamp(z_abs / float(p["z_cap"]), 0.0, 1.0)
        is_shock = ctx.regime.label == LABEL_SHOCK_CRASH
        if is_shock:
            strength *= float(p["shock_strength"])

        hl_txt = f"half-life OU {fmt_num(half_life)} giorni" if half_life is not None else "half-life OU non stimabile"
        verso = "rimbalzo" if side > 0 else "rientro"
        rationale = (
            f"Hurst {fmt_num(hurst, 2)} sotto {fmt_num(float(p['max_hurst']), 2)} e movimento 5g di "
            f"{fmt_num(ret_5 * 100)}% pari a {fmt_num(z)} sigma senza conferma fondamentale: "
            f"{verso} atteso su {_giorni(horizon)} ({hl_txt})."
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
                "hurst_100": hurst,
                "ret_5": ret_5,
                "ret_5_sigmas": z,
                "ou_half_life": half_life,
                "stock_surprise_z": None if math.isnan(surprise) else surprise,
                "shock_regime": is_shock,
            },
        )
