"""S5 - Calendar spread M1-M3 (docs/STRATEGIES.md).

Thesis: M1-M2 / M1-M3 / Dec-Dec spreads price the prompt physical tension. When the squeeze consolidates (spread
widening with falling stocks and a spot premium) the spread has momentum; after an event-driven spike the front
gives back more than the deferred: mean reversion.

Signal (exactly as documented):
  * momentum branch: spread_chg_5 > 0 AND crude_stocks_vs_5y < 0 AND spot_front_premium > 0 -> LONG the spread
    (long M1 / short M3);
  * mean-reversion branch: spread z-score (252d) beyond +2.5 AND geo_spike == 1 AND stocks not falling
    -> SHORT the spread. The doc is explicit and one-sided here (the mirror case is not traded).
  * nothing at all while the curve is a proxy: no trade until there are at least `min_real_curve_days` real
    Brent curve observations (the strategy starts in incubation).

Branches are evaluated in the order of the doc (momentum first) so the output is deterministic.

Spread conventions: the traded series is slope_m1_m3 = (M1-M3)/M1, a fraction of the front price, so its own
realized volatility (std of daily changes scaled to the horizon) is used for `expected_vol`, `expected_return`
and the stop. ATR_14 of the flat price is NOT a meaningful risk scale for a near-zero-priced spread; this is a
documented, deliberate departure from the flat-price stop convention of S1-S4.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from engine.core.events import Direction, Signal
from engine.core.instruments import Future, Instrument
from engine.features.catalog import (
    CRUDE_STOCKS_VS_5Y,
    CURVE_APPROX,
    GEO_SPIKE,
    RV_YZ_21,
    SLOPE_M1_M3,
    SPOT_FRONT_PREMIUM,
    SPREAD_CHG_5,
)
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, fmt_num, fmt_pct, prob_from_z, vol_scale, zscore

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting)


def _last_z(series: pd.Series, window: int) -> float:
    s = series.dropna()
    if len(s) < max(10, window // 2):
        return float("nan")
    value = zscore(s, window).iloc[-1]
    return float("nan") if pd.isna(value) else float(value)


def _horizon_sigma(series: pd.Series, horizon_days: int) -> float:
    """1-sigma expected absolute move of a spread series over the horizon (same units as the series)."""
    d = series.dropna().diff().dropna()
    if len(d) < 20:
        return float("nan")
    sd = float(d.std())
    if not math.isfinite(sd) or sd <= 0:
        return float("nan")
    return sd * math.sqrt(max(1, horizon_days))


class S5CalendarSpread(Strategy):
    id = "S5"
    name = "Spread di calendario M1-M3"
    family = Family.CARRY
    horizon_days = 10
    warmup_days = 300
    requires = (SLOPE_M1_M3, SPREAD_CHG_5, SPOT_FRONT_PREMIUM, RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "min_real_curve_days": 120,  # docs: no trade without 120 days of real Brent curve
            "z_window": 252,  # docs: 1y z-score of the spread
            "mr_entry_z": 2.5,  # docs: z > 2.5 with geo_spike -> short the spread
            "stocks_level_max": 0.0,  # docs: crude_stocks_vs_5y < 0 for the momentum branch
            "spot_premium_min": 0.0,  # docs: spot_front_premium > 0 for the momentum branch
            "stocks_chg_window": 21,  # window used to decide whether stocks are "not falling"
            "target_vol": 0.15,
            "stop_sigma_mult": 2.0,  # stop in spread sigmas (see module docstring)
            "prob_z_scale": 1.5,
        }

    def _real_curve_ok(self, ctx: MarketContext) -> bool:
        if ctx.f(CURVE_APPROX, 1.0) >= 0.5:
            return False
        need = int(self.params["min_real_curve_days"])
        hist = ctx.hist(CURVE_APPROX, need).dropna()
        return int((hist < 0.5).sum()) >= need

    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None
        if not self._real_curve_ok(ctx):
            return None

        code_m1 = ctx.curve_codes.get("M1")
        code_m3 = ctx.curve_codes.get("M3")
        if not code_m1 or not code_m3:
            return None
        try:
            spread = Instrument.calendar_spread(Future.from_code(code_m1), Future.from_code(code_m3))
        except (ValueError, KeyError, IndexError):
            return None

        rv = ctx.f(RV_YZ_21)
        chg5 = ctx.f(SPREAD_CHG_5)
        premium = ctx.f(SPOT_FRONT_PREMIUM)
        if math.isnan(rv) or math.isnan(chg5) or math.isnan(premium) or rv <= 0:
            return None

        z_window = int(self.params["z_window"])
        spread_hist = ctx.hist(SLOPE_M1_M3, z_window)
        sigma_h = _horizon_sigma(spread_hist, self.horizon_days)
        if math.isnan(sigma_h):
            return None

        stocks = ctx.f(CRUDE_STOCKS_VS_5Y)
        chg_window = int(self.params["stocks_chg_window"])
        stock_series = ctx.hist(CRUDE_STOCKS_VS_5Y, chg_window + 1).dropna()
        stocks_delta = (
            float(stock_series.iloc[-1]) - float(stock_series.iloc[0]) if len(stock_series) >= 2 else float("nan")
        )

        direction: Direction | None = None
        branch = ""
        z = float("nan")

        # --- branch 1: momentum of the spread while the physical squeeze consolidates --------------------
        if (
            chg5 > 0
            and not math.isnan(stocks)
            and stocks < float(self.params["stocks_level_max"])
            and premium > float(self.params["spot_premium_min"])
        ):
            z = _last_z(ctx.hist(SPREAD_CHG_5, z_window), z_window)
            if not math.isnan(z):
                direction = Direction.LONG
                branch = "momentum"

        # --- branch 2: mean reversion after an event spike -----------------------------------------------
        if direction is None:
            z_spread = _last_z(spread_hist, z_window)
            geo_spike = ctx.f(GEO_SPIKE)
            stocks_not_falling = not math.isnan(stocks_delta) and stocks_delta >= 0.0
            if (
                not math.isnan(z_spread)
                and z_spread > float(self.params["mr_entry_z"])
                and not math.isnan(geo_spike)
                and geo_spike >= 0.5
                and stocks_not_falling
            ):
                z = z_spread
                direction = Direction.SHORT
                branch = "mean_reversion"

        if direction is None:
            return None

        prob = prob_from_z(abs(z), scale=float(self.params["prob_z_scale"]))
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv), 0.0, 1.0)
        stop_pct = float(self.params["stop_sigma_mult"]) * sigma_h

        if branch == "momentum":
            rationale = (
                f"Stretta fisica in consolidamento: spread M1-M6 +{fmt_pct(chg5)} in 5 giorni (z {fmt_num(z)}), "
                f"scorte {fmt_pct(stocks)} sotto la media 5 anni e premio spot {fmt_pct(premium)}: "
                f"long spread {spread.symbol}."
            )
        else:
            rationale = (
                f"Picco da evento: spread M1-M3 a {fmt_num(z)} sigma con geo_spike attivo e scorte non in calo "
                f"({fmt_pct(stocks_delta)} in {chg_window} giorni): short spread {spread.symbol} per mean reversion."
            )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * abs(z) * sigma_h * SHRINK,
            expected_vol=sigma_h,
            rationale=rationale,
            instrument=spread.symbol,
            stop_pct=stop_pct,
            strength=strength,
            meta={
                "branch": branch,
                "multi_leg": True,
                "legs": [(leg.future.code, leg.ratio) for leg in spread.legs],
                "z": z,
                "spread_sigma_horizon": sigma_h,
                "spread_chg_5": chg5,
                "crude_stocks_vs_5y": stocks,
                "stocks_delta": None if math.isnan(stocks_delta) else stocks_delta,
                "spot_front_premium": premium,
            },
        )
