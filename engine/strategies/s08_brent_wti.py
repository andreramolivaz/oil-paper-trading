"""S8 - Brent-WTI relative value (docs/STRATEGIES.md).

Thesis: the differential is anchored by US export arbitrage (Cushing-coast-Europe transport cost) and widens with
extreme Cushing stocks, logistic bottlenecks and a Middle-East geopolitical premium (which hits Brent more than
WTI). Cointegration residual as a z-score with regime-dependent thresholds.

Signal (exactly as documented):
  * brent_wti_z beyond +-2.0, or +-2.5 when the regime label is a geopolitical one -> mean reversion on the
    spread (sell the spread when Brent is rich, buy it when Brent is cheap);
  * exit band 0.5: inside |z| <= 0.5 the strategy emits a FLAT signal with meta['exit_band'] = True, i.e. the
    actionable "close the spread" instruction, instead of a bare None which would lose the information
    (set `emit_exit_signal` to False to get None there instead);
  * between the exit band and the entry threshold: no opinion (None).

The traded instrument is Instrument.brent_wti(front Brent from ctx.curve_codes['M1'], front WTI from the NYMEX
calendar with a 3-business-day roll buffer). brent_wti is quoted in USD/bbl, so its own volatility is converted
into a fraction of the flat price for `expected_vol`, `expected_return` and the stop (documented departure from
the ATR convention of the flat-price strategies: a USD spread has no meaningful percentage ATR).
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from engine.core.calendar import front_month
from engine.core.events import Direction, Signal
from engine.core.instruments import Future, Instrument
from engine.features.catalog import BRENT_WTI, BRENT_WTI_Z, RV_YZ_21
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, fmt_num, fmt_pct, prob_from_z, vol_scale

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting)


def _horizon_sigma(series: pd.Series, horizon_days: int) -> float:
    d = series.dropna().diff().dropna()
    if len(d) < 20:
        return float("nan")
    sd = float(d.std())
    if not math.isfinite(sd) or sd <= 0:
        return float("nan")
    return sd * math.sqrt(max(1, horizon_days))


class S8BrentWti(Strategy):
    id = "S8"
    name = "Brent-WTI (cointegrazione)"
    family = Family.RELATIVE_VALUE
    horizon_days = 10
    warmup_days = 300
    requires = (BRENT_WTI, BRENT_WTI_Z, RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "entry_z": 2.0,  # docs: beyond +-2
            "entry_z_geo": 2.5,  # docs: +-2.5 in a geopolitical regime
            "exit_z": 0.5,  # docs: exit at 0.5
            "geo_label_token": "geopolitico",
            "z_window": 252,
            "wti_roll_buffer_days": 3,
            "target_vol": 0.15,
            "stop_sigma_mult": 2.0,  # stop in spread sigmas (see module docstring)
            "prob_z_scale": 1.5,
            "emit_exit_signal": True,
        }

    def entry_threshold(self, ctx: MarketContext) -> float:
        token = str(self.params["geo_label_token"]).lower()
        if token in ctx.regime.label.lower():
            return float(self.params["entry_z_geo"])
        return float(self.params["entry_z"])

    def _instrument(self, ctx: MarketContext) -> Instrument | None:
        try:
            brent = Future.from_code(ctx.curve_codes.get("M1", ctx.instrument))
            year, month = front_month("CL", ctx.ts.date(), int(self.params["wti_roll_buffer_days"]))
            return Instrument.brent_wti(brent, Future("CL", year, month))
        except (ValueError, KeyError, IndexError):
            return None

    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None
        spread_instr = self._instrument(ctx)
        if spread_instr is None:
            return None

        z = ctx.f(BRENT_WTI_Z)
        level = ctx.f(BRENT_WTI)
        rv = ctx.f(RV_YZ_21)
        if math.isnan(z) or math.isnan(level) or math.isnan(rv) or rv <= 0 or ctx.price <= 0:
            return None

        sigma_usd = _horizon_sigma(ctx.hist(BRENT_WTI, int(self.params["z_window"])), self.horizon_days)
        if math.isnan(sigma_usd):
            return None
        sigma_h = sigma_usd / float(ctx.price)  # fraction of the flat price, comparable to a return
        if sigma_h <= 0:
            return None

        exit_z = float(self.params["exit_z"])
        entry_z = self.entry_threshold(ctx)
        meta: dict[str, Any] = {
            "multi_leg": True,
            "legs": [(leg.future.code, leg.ratio) for leg in spread_instr.legs],
            "brent_wti": level,
            "brent_wti_z": z,
            "entry_z": entry_z,
            "spread_sigma_horizon": sigma_h,
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
                    f"Brent-WTI rientrato a {fmt_num(z)} sigma (banda di uscita {fmt_num(exit_z)}, "
                    f"livello {fmt_num(level)} $/bbl): chiudo {spread_instr.symbol}."
                ),
                instrument=spread_instr.symbol,
                strength=0.0,
                meta=meta,
            )

        if abs(z) < entry_z:
            return None

        direction = Direction.SHORT if z > 0 else Direction.LONG
        prob = prob_from_z(abs(z), scale=float(self.params["prob_z_scale"]))
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv), 0.0, 1.0)
        stop_pct = float(self.params["stop_sigma_mult"]) * sigma_h
        verso = "long" if direction is Direction.LONG else "short"
        soglia = (
            f"soglia {fmt_num(entry_z)} (regime {ctx.regime.label})"
            if entry_z != float(self.params["entry_z"])
            else f"soglia {fmt_num(entry_z)}"
        )
        rationale = (
            f"Residuo Brent-WTI a {fmt_num(z)} sigma oltre la {soglia}, spread {fmt_num(level)} $/bbl: "
            f"{verso} spread {spread_instr.symbol} per mean reversion, stop {fmt_pct(stop_pct)}."
        )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * abs(z) * sigma_h * SHRINK,
            expected_vol=sigma_h,
            rationale=rationale,
            instrument=spread_instr.symbol,
            stop_pct=stop_pct,
            strength=strength,
            meta=meta,
        )
