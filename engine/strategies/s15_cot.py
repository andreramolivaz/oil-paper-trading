"""S15 - COT positioning (docs/STRATEGIES.md §S15).

Thesis: managed money at an extreme of net length (3y percentile > 90%) with an exhausting trend signals crowding:
the imbalance has to be unwound. The mirror case holds at the bottom of the distribution. It also works as a
FILTER that shrinks the trend strategies when they are crowded (`crowding_filter`, used by S1 and the allocator).

Signal:
  * SHORT (contrarian) when `COT_MM_NET_BRENT_PCTL` > `upper_pctl` (0.9) AND `TSMOM_21` is FALLING versus
    `tsmom_lookback` (5) sessions ago;
  * LONG (mirrored) when the percentile is < `lower_pctl` (0.1) AND `TSMOM_21` is RISING versus 5 sessions ago;
  * the Brent percentile is the primary input; `COT_MM_NET_WTI_PCTL` is the documented fallback when the Brent one
    is NaN (ICE Brent COT starts later than the CFTC WTI series);
  * horizon `horizon_days` (10 sessions ~ 2 weeks, docs: "short contrarian a 2 settimane").

Conviction: the percentile thresholds at 0.9 / 0.1 correspond to roughly a 2-sigma positioning extreme, so
z = `base_z` (2.0) at the threshold and grows linearly to `base_z` + `z_span` (3.0) at the 0/100th percentile.
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, prob_from_z, vol_scale

CROWDING_FLOOR = 0.7  # below this the crowd is not crowded enough to matter
CROWDING_SPAN = 0.3  # 0.7 -> 1.0 maps to a multiplier 1.0 -> 0.0


class S15CotPositioning(Strategy):
    id = "S15"
    name = "Posizionamento COT"
    family = Family.FUNDAMENTAL
    horizon_days = 10
    warmup_days = 300
    requires = (cat.TSMOM_21, cat.RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "upper_pctl": 0.9,  # docs: cot_mm_net_brent_pctl > 0,9
            "lower_pctl": 0.1,  # docs: specchio sotto 0,1
            "tsmom_lookback": 5,  # "tsmom_21 in calo" = rispetto a 5 sedute prima
            "horizon_days": 10,  # docs: 2 settimane
            "base_z": 2.0,
            "z_span": 1.0,
            "crowding_floor": CROWDING_FLOOR,
            "crowding_span": CROWDING_SPAN,
            "target_vol": 0.15,
            "stop_atr": 2.0,
            "stop_sigma": 2.0,
            "allow_wti_fallback": True,
        }

    # ------------------------------------------------------------------ crowding filter (used by S1 / allocator)
    @staticmethod
    def crowding_filter(ctx: MarketContext, direction: Direction) -> float:
        """Size multiplier in [0, 1] for a trade in `direction` given the COT crowding.

        Returns ``1 - max(0, COT_CROWDING - 0.7) / 0.3`` when the crowd is positioned on the SAME side as
        `direction` (so a fully crowded book, crowding = 1.0, zeroes the size), and 1.0 otherwise. The crowd's side
        is read from `COT_MM_NET_BRENT_PCTL` (fallback `COT_MM_NET_WTI_PCTL`): above 0.5 the crowd is long, below
        0.5 it is short. Missing positioning data returns 1.0 - a neutral filter, never an invented reduction.

        The signature is part of S15's public contract (S1 and the allocator call it); keep it stable.
        """
        crowding = ctx.f(cat.COT_CROWDING)
        pctl = ctx.f(cat.COT_MM_NET_BRENT_PCTL)
        if math.isnan(pctl):
            pctl = ctx.f(cat.COT_MM_NET_WTI_PCTL)
        if math.isnan(crowding) or math.isnan(pctl) or direction is Direction.FLAT:
            return 1.0
        crowd_side = 1 if pctl > 0.5 else (-1 if pctl < 0.5 else 0)
        if crowd_side == 0 or crowd_side != direction.sign:
            return 1.0
        return clamp(1.0 - max(0.0, crowding - CROWDING_FLOOR) / CROWDING_SPAN, 0.0, 1.0)

    # ------------------------------------------------------------------ helpers
    def _percentile(self, ctx: MarketContext) -> tuple[float, str]:
        pctl = ctx.f(cat.COT_MM_NET_BRENT_PCTL)
        if not math.isnan(pctl):
            return pctl, cat.COT_MM_NET_BRENT_PCTL
        if bool(self.params["allow_wti_fallback"]):
            return ctx.f(cat.COT_MM_NET_WTI_PCTL), cat.COT_MM_NET_WTI_PCTL
        return float("nan"), cat.COT_MM_NET_BRENT_PCTL

    def _tsmom_change(self, ctx: MarketContext) -> float:
        look = max(1, int(self.params["tsmom_lookback"]))
        h = ctx.hist(cat.TSMOM_21, look + 1).dropna()
        if len(h) < 2:
            return float("nan")
        return float(h.iloc[-1] - h.iloc[0])

    # ------------------------------------------------------------------ entry point
    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        pctl, source = self._percentile(ctx)
        rv = ctx.f(cat.RV_YZ_21)
        if math.isnan(pctl) or math.isnan(rv):
            return None
        tsmom_chg = self._tsmom_change(ctx)
        if math.isnan(tsmom_chg):
            return None

        upper, lower = float(p["upper_pctl"]), float(p["lower_pctl"])
        if pctl > upper and tsmom_chg < 0.0:
            side, excess = -1, (pctl - upper) / max(1e-9, 1.0 - upper)
            testo = f"net length dei managed money al percentile {fmt_num(pctl, 2)} (sopra {fmt_num(upper, 2)})"
        elif pctl < lower and tsmom_chg > 0.0:
            side, excess = 1, (lower - pctl) / max(1e-9, lower)
            testo = f"net length dei managed money al percentile {fmt_num(pctl, 2)} (sotto {fmt_num(lower, 2)})"
        else:
            return None

        excess = clamp(excess, 0.0, 1.0)
        horizon = max(1, int(p["horizon_days"]))
        em = expected_move(rv, horizon)
        z = float(p["base_z"]) + float(p["z_span"]) * excess
        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        verso = "short" if side < 0 else "long"
        rationale = (
            f"Affollamento COT: {testo} con trend 21g in "
            f"{'esaurimento' if side < 0 else 'ripresa'} ({fmt_num(tsmom_chg)} su "
            f"{int(p['tsmom_lookback'])} sedute); {verso} contrarian a {horizon} giorni."
        )
        return self.make_signal(
            ctx,
            Direction.from_sign(side),
            prob=prob_from_z(z),
            expected_return=side * z * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=vol_scale(float(p["target_vol"]), rv) * clamp(0.5 + 0.5 * excess, 0.0, 1.0),
            horizon_days=horizon,
            meta={
                "pctl": pctl,
                "pctl_source": source,
                "tsmom_21_change": tsmom_chg,
                "crowding": ctx.f(cat.COT_CROWDING),
                "upper_pctl": upper,
                "lower_pctl": lower,
            },
        )
