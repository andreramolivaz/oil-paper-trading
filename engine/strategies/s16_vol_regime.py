"""S16 - Volatility regime (docs/STRATEGIES.md §S16).

Thesis: the variance risk premium (OVX minus realized) is high when the market fears an event; with no event on the
calendar, large moves tend to be reabsorbed (1-5 day mean reversion). When volatility is compressed (OVX and
realized both low) breakouts are more reliable, so the regime decides which family to favour. S16 trades the
future directly - no options.

Branch A (MEAN REVERSION, tradeable):
    percentile of `VRP` over `ctx.hist(VRP, pctl_window)` > `vrp_pctl_high` (0.7)
    AND `HOURS_TO_EVENT` > `min_hours_to_event` (72)
    AND |RET_1| > `ret_sigma_mult` (1.5) * sigma, sigma = RV_YZ_21 / sqrt(252)
    -> contrarian to RET_1 over `horizon_days` (clamped to 1..5).

Branch B (INFORMATIONAL, not tradeable):
    VRP percentile < `vrp_pctl_low` (0.3) AND `VOL_PCTL_1Y` < `vol_pctl_low` (0.2)
    -> a signal with direction FLAT, prob 0.5, strength 0 and `meta['enable_breakout'] = True`: a hint to upweight
    S2 (compression breakout). Direction FLAT means no position and the allocator skips it.

Returns None when OVX or VRP is NaN (docs: "OVX non disponibile -> il conto ombra non opera", i.e. before 2007).
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import TRADING_DAYS, clamp, expected_move, fmt_num, prob_from_z, sign, vol_scale


class S16VolRegime(Strategy):
    id = "S16"
    name = "Regime di volatilità"
    family = Family.VOLATILITY
    horizon_days = 3
    warmup_days = 300
    requires = (cat.OVX, cat.VRP, cat.RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "vrp_pctl_high": 0.7,  # docs: vrp oltre il 70° percentile
            "vrp_pctl_low": 0.3,  # docs: vrp sotto il 30°
            "vol_pctl_low": 0.2,  # docs: vol_pctl_1y < 0,2
            "min_hours_to_event": 72.0,  # docs: hours_to_event > 72
            "ret_sigma_mult": 1.5,  # docs: ret_1 oltre ±1,5 sigma
            "pctl_window": 252,
            "min_pctl_obs": 60,
            "horizon_days": 3,  # 1..5 (docs: mean reversion di 1-5 giorni)
            "target_vol": 0.15,
            "stop_atr": 2.0,
            "stop_sigma": 2.0,
            "z_cap": 3.0,
            "info_strength": 0.0,  # informational branch: no size at all
        }

    # ------------------------------------------------------------------ helpers
    def _vrp_percentile(self, ctx: MarketContext) -> float:
        window = max(2, int(self.params["pctl_window"]))
        h = ctx.hist(cat.VRP, window).dropna()
        if len(h) < max(2, int(self.params["min_pctl_obs"])):
            return float("nan")
        cur = float(h.iloc[-1])
        past = h.iloc[:-1].to_numpy()
        return float((past < cur).mean())

    # ------------------------------------------------------------------ entry point
    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        ovx = ctx.f(cat.OVX)
        vrp = ctx.f(cat.VRP)
        rv = ctx.f(cat.RV_YZ_21)
        if math.isnan(ovx) or math.isnan(vrp) or math.isnan(rv):
            return None  # no implied vol: the shadow account does not operate
        pctl = self._vrp_percentile(ctx)
        if math.isnan(pctl):
            return None

        horizon = int(clamp(float(p["horizon_days"]), 1.0, 5.0))
        em = expected_move(rv, horizon)
        hours = ctx.f(cat.HOURS_TO_EVENT)
        ret_1 = ctx.f(cat.RET_1)
        sigma_1d = rv / math.sqrt(TRADING_DAYS)

        # --- branch A: mean reversion on an oversized move with a rich VRP and no event in sight
        if (
            pctl > float(p["vrp_pctl_high"])
            and hours > float(p["min_hours_to_event"])
            and not math.isnan(ret_1)
            and sigma_1d > 0.0
            and abs(ret_1) > float(p["ret_sigma_mult"]) * sigma_1d
        ):
            side = sign(-ret_1)
            if side != 0:
                z = abs(ret_1) / sigma_1d
                z_abs = min(z, float(p["z_cap"]))
                atr = ctx.f(cat.ATR_14)
                stop_pct = float(p["stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
                rationale = (
                    f"Premio per il rischio di volatilità al percentile {fmt_num(pctl, 2)} e nessun evento entro "
                    f"{fmt_num(hours, 0)} ore: movimento di {fmt_num(ret_1 * 100)}% pari a {fmt_num(z)} sigma, "
                    f"rientro atteso su {horizon} giorni."
                )
                return self.make_signal(
                    ctx,
                    Direction.from_sign(side),
                    prob=prob_from_z(z_abs),
                    expected_return=side * z_abs * em * 0.5,
                    expected_vol=em,
                    rationale=rationale,
                    stop_pct=stop_pct,
                    strength=vol_scale(float(p["target_vol"]), rv) * clamp(z_abs / float(p["z_cap"]), 0.0, 1.0),
                    horizon_days=horizon,
                    meta={
                        "branch": "mean_reversion",
                        "vrp": vrp,
                        "vrp_pctl": pctl,
                        "ovx": ovx,
                        "ret_1": ret_1,
                        "ret_1_sigmas": z,
                        "hours_to_event": hours,
                    },
                )

        # --- branch B: compressed volatility -> informational hint to upweight the breakout family (S2)
        vol_pctl = ctx.f(cat.VOL_PCTL_1Y)
        if pctl < float(p["vrp_pctl_low"]) and not math.isnan(vol_pctl) and vol_pctl < float(p["vol_pctl_low"]):
            rationale = (
                f"Volatilità compressa: premio di volatilità al percentile {fmt_num(pctl, 2)} e vol realizzata al "
                f"percentile {fmt_num(vol_pctl, 2)}; nessuna posizione, si favorisce la famiglia breakout (S2)."
            )
            return self.make_signal(
                ctx,
                Direction.FLAT,
                prob=0.5,
                expected_return=0.0,
                expected_vol=em,
                rationale=rationale,
                strength=float(p["info_strength"]),
                horizon_days=horizon,
                meta={
                    "branch": "informational",
                    "enable_breakout": True,
                    "informational": True,
                    "vrp": vrp,
                    "vrp_pctl": pctl,
                    "ovx": ovx,
                    "vol_pctl_1y": vol_pctl,
                },
            )
        return None
