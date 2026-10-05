"""S9 - Crack spread and product lead (docs/STRATEGIES.md).

Thesis: products lead crude when final demand surprises - a rally in the cracks (RBOB/HO vs crude) anticipates
higher runs and crude demand. Extreme cracks revert towards refining economics because margins attract or expel
capacity quickly.

Signal (exactly as documented):
  * branch 1: product_lead > 1 sigma -> long crude (ctx.instrument) for about one week. The doc is explicit and
    one-sided here, so the mirror (products lagging -> short crude) is NOT traded;
  * branch 2: |crack_321_z| > 2.5 -> trade the 3-2-1 crack itself: short the crack when the z is high, long when
    it is low (multi-leg position).
  * nothing at all when the RBOB/HO derived features (product_lead, crack_321, crack_321_z) are NaN.

Branches are evaluated in the order of the doc (product lead first) so the output is deterministic.

crack_321 is quoted in USD/bbl, so for branch 2 its own volatility is converted into a fraction of the flat
price for `expected_vol`, `expected_return` and the stop (documented departure from the ATR convention, which
only makes sense for the flat-price leg of branch 1).
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from engine.core.calendar import front_month
from engine.core.events import Direction, Signal
from engine.core.instruments import Future, Instrument
from engine.features.catalog import ATR_14, CRACK_321, CRACK_321_Z, PRODUCT_LEAD, RV_YZ_21
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, fmt_pct, prob_from_z, vol_scale

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting)


def _horizon_sigma(series: pd.Series, horizon_days: int) -> float:
    d = series.dropna().diff().dropna()
    if len(d) < 20:
        return float("nan")
    sd = float(d.std())
    if not math.isfinite(sd) or sd <= 0:
        return float("nan")
    return sd * math.sqrt(max(1, horizon_days))


class S9CrackSpread(Strategy):
    id = "S9"
    name = "Crack spread 3-2-1"
    family = Family.RELATIVE_VALUE
    horizon_days = 5
    warmup_days = 300
    requires = (PRODUCT_LEAD, CRACK_321, CRACK_321_Z, RV_YZ_21, ATR_14)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "product_lead_sigma_mult": 1.0,  # docs: product_lead > 1 sigma
            "lead_sigma_window": 252,  # window for the product_lead sigma
            "lead_horizon_days": 5,  # docs: long crude for about one week
            "crack_entry_z": 2.5,  # docs: |crack_321_z| > 2.5
            "crack_horizon_days": 10,
            "roll_buffer_days": 3,
            "target_vol": 0.15,
            "stop_atr_mult": 2.0,  # branch 1 (flat price)
            "stop_sigma_mult": 2.0,  # branch 2 (crack, in crack sigmas)
            "prob_z_scale": 1.5,
        }

    def _crack_instrument(self, ctx: MarketContext) -> Instrument | None:
        buf = int(self.params["roll_buffer_days"])
        try:
            day = ctx.ts.date()
            crude = Future("CL", *front_month("CL", day, buf))
            rbob = Future("RB", *front_month("RB", day, buf))
            ho = Future("HO", *front_month("HO", day, buf))
            return Instrument.crack_321(crude, rbob, ho)
        except (ValueError, KeyError, IndexError):
            return None

    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None
        lead = ctx.f(PRODUCT_LEAD)
        crack = ctx.f(CRACK_321)
        crack_z = ctx.f(CRACK_321_Z)
        rv = ctx.f(RV_YZ_21)
        atr = ctx.f(ATR_14)
        # RBOB/HO derived features: no trade at all when they are missing
        if any(math.isnan(v) for v in (lead, crack, crack_z, rv, atr)) or rv <= 0 or atr <= 0:
            return None

        # ---------------------------------------------- branch 1: products lead crude -> long crude 1 week
        lead_window = int(self.params["lead_sigma_window"])
        lead_hist = ctx.hist(PRODUCT_LEAD, lead_window).dropna()
        lead_sigma = float(lead_hist.std()) if len(lead_hist) >= 20 else float("nan")
        if not math.isnan(lead_sigma) and lead_sigma > 0:
            z_lead = lead / lead_sigma
            if z_lead > float(self.params["product_lead_sigma_mult"]):
                horizon = int(self.params["lead_horizon_days"])
                sigma_h = expected_move(rv, horizon)
                prob = prob_from_z(abs(z_lead), scale=float(self.params["prob_z_scale"]))
                stop_pct = float(self.params["stop_atr_mult"]) * atr
                strength = clamp(vol_scale(float(self.params["target_vol"]), rv), 0.0, 1.0)
                rationale = (
                    f"I prodotti guidano il greggio: product_lead {fmt_pct(lead)} a 5 giorni, "
                    f"{fmt_num(z_lead)} sigma: long greggio a {horizon} giorni, stop {fmt_pct(stop_pct)}."
                )
                return self.make_signal(
                    ctx,
                    Direction.LONG,
                    prob=prob,
                    expected_return=z_lead * sigma_h * SHRINK,
                    expected_vol=sigma_h,
                    rationale=rationale,
                    stop_pct=stop_pct,
                    strength=strength,
                    horizon_days=horizon,
                    meta={
                        "branch": "product_lead",
                        "product_lead": lead,
                        "product_lead_z": z_lead,
                        "product_lead_sigma": lead_sigma,
                        "crack_321_z": crack_z,
                    },
                )

        # ---------------------------------------------- branch 2: extreme crack -> trade the crack ------
        if abs(crack_z) <= float(self.params["crack_entry_z"]):
            return None
        instr = self._crack_instrument(ctx)
        if instr is None or ctx.price <= 0:
            return None
        horizon = int(self.params["crack_horizon_days"])
        sigma_usd = _horizon_sigma(ctx.hist(CRACK_321, int(self.params["lead_sigma_window"])), horizon)
        if math.isnan(sigma_usd):
            return None
        sigma_h = sigma_usd / float(ctx.price)
        if sigma_h <= 0:
            return None

        direction = Direction.SHORT if crack_z > 0 else Direction.LONG
        prob = prob_from_z(abs(crack_z), scale=float(self.params["prob_z_scale"]))
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv), 0.0, 1.0)
        stop_pct = float(self.params["stop_sigma_mult"]) * sigma_h
        verso = "short" if direction is Direction.SHORT else "long"
        rationale = (
            f"Crack 3-2-1 a {fmt_num(crack_z)} sigma ({fmt_num(crack)} $/bbl), oltre la soglia "
            f"{fmt_num(float(self.params['crack_entry_z']))}: {verso} crack {instr.symbol}, "
            f"stop {fmt_pct(stop_pct)}."
        )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * abs(crack_z) * sigma_h * SHRINK,
            expected_vol=sigma_h,
            rationale=rationale,
            instrument=instr.symbol,
            stop_pct=stop_pct,
            strength=strength,
            horizon_days=horizon,
            meta={
                "branch": "crack_reversion",
                "multi_leg": True,
                "legs": [(leg.future.code, leg.ratio) for leg in instr.legs],
                "crack_321": crack,
                "crack_321_z": crack_z,
                "crack_sigma_horizon": sigma_h,
            },
        )
