"""Execution cost model of the paper broker (brief §10: "costi veri, esecuzioni prudenti").

Every parameter comes from ``config/risk.yaml`` -> ``costs`` (``RiskConfig.costs``). Calibration notes:

* **Half-spread.** The ICE Brent front-month screen is normally 1-2 ticks wide (tick = 0.01 $/bbl), i.e. a full
  spread of ~1-2 cents, or ~1-2 bps of a 100 $ price; the half-spread paid by a marketable order is therefore
  ~0.5-1 bp. We charge ``base_spread_bps`` (default 1.5 bps, ~1.5 cents at 100 $) per side: deliberately 1.5-3x
  the screen to account for the fact that we trade on delayed data and never improve on the touch. The
  half-spread widens linearly with realized volatility above the ``NORMAL_VOL_ANNUAL`` reference (25 % annualised,
  roughly the long-run Brent median): at 50 % vol and ``spread_vol_multiplier`` 1.0 the half-spread doubles. In an
  event window (EIA/OPEC+/FOMC, see ``config/events.yaml``) it is multiplied by ``event_spread_multiplier``.
* **Slippage / market impact.** ``slippage_bps_per_turn`` bps per "equity-turn" of notional traded
  (notional / equity). A 10 000 $ account buying 100 bbl at 100 $ (one turn) pays 1 bp; at 10x leverage 10 bps.
  This is a conservative linear proxy (Brent front-month liquidity is deep: ~1 M bbl per tick at the touch, so
  real impact for a retail-size order is ~0); it is capped at ``max_slippage_bps`` so that a near-zero equity
  does not produce absurd prices. Forced liquidations add ``margin.stop_out_extra_slippage_bps`` on top, gap fills
  through a stop add ``gap_extra_slippage_bps``.
* **Commission.** ``commission_per_lot_usd`` per 100-bbl lot per side (default 1.20 $, scaled from an all-in
  ~12 $/contract retail rate on a 1 000-bbl ICE Brent contract: exchange + clearing + broker).
* **Roll.** ``roll_cost_bps`` is the all-in cost of executing the roll as a calendar-spread trade (tighter than
  two outright crossings). It is applied as adverse slippage split evenly across the two legs; commissions are
  charged on each leg.

A ``multiplier`` (default 1.0) scales every cost component, used by the validation module for the
"double costs" sensitivity run (``costs.double_costs_sensitivity``).
"""

from __future__ import annotations

import math

from engine.core.config import RiskConfig
from engine.core.events import Direction

NORMAL_VOL_ANNUAL = 0.25  # realized vol (annualised) at which the base half-spread applies
BPS = 1e-4


def _sign(x: float | int | str | Direction) -> int:
    """Normalise a side given as signed quantity, Direction, or 'buy'/'sell' string to +1 / -1 / 0."""
    if isinstance(x, Direction):
        return x.sign
    if isinstance(x, str):
        s = x.lower()
        if s in {"buy", "long", "+", "b"}:
            return 1
        if s in {"sell", "short", "-", "s"}:
            return -1
        return 0
    return (x > 0) - (x < 0)


class CostModel:
    """Spread, slippage, commission and roll cost of the paper broker; see module docstring for calibration."""

    def __init__(self, risk: RiskConfig, multiplier: float = 1.0) -> None:
        c = dict(risk.costs or {})
        self.lot_bbl = float(risk.lot_bbl)
        self.commission_per_lot_usd = float(c.get("commission_per_lot_usd", 1.20))
        self.base_spread_bps = float(c.get("base_spread_bps", 1.5))
        self.spread_vol_multiplier = float(c.get("spread_vol_multiplier", 1.0))
        self.event_spread_multiplier = float(c.get("event_spread_multiplier", 2.0))
        self.slippage_bps_per_turn = float(c.get("slippage_bps_per_turn", 1.0))
        self.max_slippage_bps = float(c.get("max_slippage_bps", 50.0))
        self.roll_cost_bps = float(c.get("roll_cost_bps", 2.0))
        self.gap_extra_slippage_bps = float(c.get("gap_extra_slippage_bps", 10.0))
        self.stop_out_extra_slippage_bps = float(
            (risk.raw.get("margin", {}) if isinstance(risk.raw, dict) else {}).get("stop_out_extra_slippage_bps", 25.0)
        )
        if multiplier <= 0:
            raise ValueError("cost multiplier must be positive")
        self.multiplier = float(multiplier)

    # ------------------------------------------------------------------ spread
    def half_spread_bps(self, vol_annual: float | None, event_window: bool = False) -> float:
        """Half-spread in bps of the reference (first-leg) price paid by a marketable order.

        ``vol_annual`` is the annualised realized volatility (fraction, e.g. 0.35). NaN/None/below-normal vol
        gives the base half-spread; above ``NORMAL_VOL_ANNUAL`` the spread widens linearly:
        ``base * (1 + spread_vol_multiplier * (vol / 0.25 - 1))``. Event windows multiply the result.
        """
        scale = 1.0
        if vol_annual is not None and math.isfinite(vol_annual) and vol_annual > NORMAL_VOL_ANNUAL:
            scale = 1.0 + self.spread_vol_multiplier * (vol_annual / NORMAL_VOL_ANNUAL - 1.0)
        bps = self.base_spread_bps * scale
        if event_window:
            bps *= self.event_spread_multiplier
        return bps * self.multiplier

    # ---------------------------------------------------------------- slippage
    def slippage_bps(self, qty_bbl: float, notional: float, equity: float) -> float:
        """Market-impact proxy in bps: ``slippage_bps_per_turn * notional / equity``, capped at ``max_slippage_bps``.

        With non-positive equity (account effectively wiped out) the cap is charged: there is no meaningful
        equity-turn and the liquidation will be as bad as the model allows.
        """
        if qty_bbl == 0 or notional <= 0:
            return 0.0
        if equity <= 0:
            return self.max_slippage_bps * self.multiplier
        turns = abs(notional) / equity
        return min(self.max_slippage_bps, self.slippage_bps_per_turn * turns) * self.multiplier

    # -------------------------------------------------------------- commission
    def commission(self, qty_bbl: float) -> float:
        """Commission in USD for one side: ``commission_per_lot_usd`` per ``lot_bbl`` barrels (pro rata)."""
        if self.lot_bbl <= 0:
            return 0.0
        return abs(qty_bbl) / self.lot_bbl * self.commission_per_lot_usd * self.multiplier

    # -------------------------------------------------------------------- roll
    def roll_cost(self, qty_bbl: float, price: float) -> float:
        """Total USD cost of rolling ``qty_bbl`` (both legs) at a reference price: ``|qty| * |price| * roll_bps``."""
        return abs(qty_bbl) * abs(price) * self.roll_cost_bps * BPS * self.multiplier

    # ------------------------------------------------------------- convenience
    def total_cost_per_bbl(
        self,
        side: float | str | Direction,
        price: float,
        vol: float | None = None,
        event_window: bool = False,
    ) -> float:
        """Size-independent one-way cost in USD/bbl: half-spread on ``price`` plus commission per barrel.

        Market impact depends on size vs equity and is excluded; use ``slippage_bps`` for it. ``side`` is
        accepted for API symmetry (costs are symmetric for buys and sells) and validated only.
        """
        if _sign(side) == 0:
            return 0.0
        spread = abs(price) * self.half_spread_bps(vol, event_window) * BPS
        comm = self.commission_per_lot_usd * self.multiplier / self.lot_bbl if self.lot_bbl > 0 else 0.0
        return spread + comm

    @staticmethod
    def adverse_price(side: float | str | Direction, reference_price: float, cost_per_bbl: float) -> float:
        """Execution price after moving ``cost_per_bbl`` against the trader: buys pay more, sells receive less."""
        return reference_price + _sign(side) * abs(cost_per_bbl)

    def execution_bps(
        self,
        qty_bbl: float,
        reference_price: float,
        equity: float,
        vol: float | None = None,
        event_window: bool = False,
        extra_bps: float = 0.0,
        impact: bool = True,
    ) -> dict[str, float]:
        """All adverse components (bps of the reference price) for one execution; ``extra_bps`` is NOT scaled."""
        notional = abs(qty_bbl) * abs(reference_price)
        hs = self.half_spread_bps(vol, event_window)
        sl = self.slippage_bps(qty_bbl, notional, equity) if impact else 0.0
        return {
            "half_spread_bps": hs,
            "slippage_bps": sl,
            "extra_bps": float(extra_bps),
            "total_bps": hs + sl + extra_bps,
        }
