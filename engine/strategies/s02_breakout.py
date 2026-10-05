"""S2 - Compression breakout (docs/STRATEGIES.md).

Thesis: volatility is cyclical. Compression (realized vol in the bottom decile/quartile of the year) is followed
by expansion; when price leaves a Donchian channel while vol is compressed the new information is not priced yet
and the stops of the range traders feed the move.

Signal (exactly as documented):
  * vol_pctl_1y < 0.25 over the 10 days PRECEDING the breakout (today excluded: the breakout day itself expands
    vol, so constraining it would veto every real breakout);
  * donchian_pos_55 outside [0, 1] (close beyond the channel) -> long above, short below;
  * volume confirmation (> 20d mean) only when a `brent_front_volume` column is present in ctx.features;
    when the column is absent, or present but unusable (NaN mean), the confirmation is skipped, never faked;
  * stop 2 ATR, trailing 3 ATR.

The breakout z is the size of the breakout day's move in ATR units (|ret_1| / atr_14), which is the only honest
scale available from the catalogue: donchian_pos_55 gives the position in the channel, not the channel width.
`expected_return` carries the deliberate 0.5 shrinkage factor used across the engine.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from engine.core.events import Direction, Signal
from engine.features.catalog import ATR_14, DONCHIAN_POS_55, RET_1, RV_YZ_21, VOL_PCTL_1Y
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, fmt_pct, prob_from_z, vol_scale

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting)


class S2CompressionBreakout(Strategy):
    id = "S2"
    name = "Breakout da compressione"
    family = Family.TREND
    horizon_days = 10
    warmup_days = 300
    requires = (VOL_PCTL_1Y, DONCHIAN_POS_55, RET_1, ATR_14, RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "vol_pctl_max": 0.25,  # docs: vol_pctl_1y < 0.25
            "compression_lookback_days": 10,  # docs: "nei 10 giorni precedenti"
            "donchian_upper": 1.0,  # close outside [0, 1] of the 55d channel
            "donchian_lower": 0.0,
            "volume_column": "brent_front_volume",
            "volume_window": 20,  # docs: volume > 20d mean
            "volume_mult": 1.0,
            "stop_atr_mult": 2.0,  # docs: stop 2 ATR
            "trailing_atr_mult": 3.0,  # docs: trailing 3 ATR
            "target_vol": 0.15,
            "z_cap": 4.0,
            "min_strength_mult": 0.3,
            "prob_z_scale": 1.5,
        }

    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None

        lookback = int(self.params["compression_lookback_days"])
        window = ctx.hist(VOL_PCTL_1Y, lookback + 1)
        prior = window.iloc[:-1] if len(window) == lookback + 1 else pd.Series(dtype=float)
        if len(prior) < lookback or bool(prior.isna().any()):
            return None  # not enough point-in-time history to assert compression
        vol_pctl_max = float(self.params["vol_pctl_max"])
        if float(prior.max()) >= vol_pctl_max:
            return None
        compression = float(prior.mean())

        pos = ctx.f(DONCHIAN_POS_55)
        if math.isnan(pos):
            return None
        if pos >= float(self.params["donchian_upper"]):
            direction = Direction.LONG
        elif pos <= float(self.params["donchian_lower"]):
            direction = Direction.SHORT
        else:
            return None

        ret1 = ctx.f(RET_1)
        atr = ctx.f(ATR_14)
        rv = ctx.f(RV_YZ_21)
        if math.isnan(ret1) or math.isnan(atr) or math.isnan(rv) or atr <= 0 or rv <= 0:
            return None

        # optional volume confirmation; None = unavailable (skipped), False = rejected
        confirmed: bool | None = None
        col = str(self.params["volume_column"])
        if col in ctx.features.columns:
            vol_window = int(self.params["volume_window"])
            vseries = ctx.hist(col, vol_window + 1)
            if len(vseries) >= 2 and not pd.isna(vseries.iloc[-1]):
                base = vseries.iloc[:-1].mean()
                if not pd.isna(base) and float(base) > 0:
                    confirmed = float(vseries.iloc[-1]) > float(base) * float(self.params["volume_mult"])
        if confirmed is False:
            return None

        z = clamp(abs(ret1) / atr, 0.0, float(self.params["z_cap"]))
        horizon = self.horizon_days
        sigma_h = expected_move(rv, horizon)
        prob = prob_from_z(z, scale=float(self.params["prob_z_scale"]))
        stop_pct = float(self.params["stop_atr_mult"]) * atr
        trailing_pct = float(self.params["trailing_atr_mult"]) * atr
        size_mult = clamp(z / 2.0, float(self.params["min_strength_mult"]), 1.0)
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv) * size_mult, 0.0, 1.0)

        verso = "long" if direction is Direction.LONG else "short"
        lato = "sopra" if direction is Direction.LONG else "sotto"
        conferma = {True: ", volume confermato", False: "", None: ", volume non disponibile"}[confirmed]
        rationale = (
            f"Vol compressa (percentile medio {fmt_num(compression)} su {lookback} giorni) e chiusura {lato} "
            f"il canale Donchian 55 (pos {fmt_num(pos)}, movimento {fmt_num(z)} ATR){conferma}: {verso}, "
            f"stop {fmt_pct(stop_pct)}, trailing {fmt_pct(trailing_pct)}."
        )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * z * sigma_h * SHRINK,
            expected_vol=sigma_h,
            rationale=rationale,
            stop_pct=stop_pct,
            trailing_pct=trailing_pct,
            strength=strength,
            horizon_days=horizon,
            meta={
                "donchian_pos_55": pos,
                "vol_pctl_mean_prior": compression,
                "breakout_atr": z,
                "volume_confirmed": confirmed,
            },
        )
