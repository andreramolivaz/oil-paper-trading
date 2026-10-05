"""S18 - Synthetic options (APPROXIMATION, docs/STRATEGIES.md §S18).

Thesis: ahead of binary events (OPEC+, big reports) implied vol is often below the vol realized on the event day,
so a long straddle wins when the move exceeds the premium; when the premium is rich (OVX well above the GARCH
forecast) a defined-risk short structure collects it.

WHY THIS IS INFORMATIONAL ONLY
------------------------------
We have NO real ICE Brent option quotes in `docs/DATA_SOURCES.md`. The structures here are priced with Black-76 on
the front future using OVX/100 as the at-the-money volatility and the declared put skew of
`engine/strategies/black76.py` (+2 vol points per 10% out-of-the-money). The paper broker cannot hold options at
all, so EVERY signal from this strategy:
  * is emitted on the synthetic instrument ``SYNOPT-<front contract code>``, never on the tradeable future;
  * has `direction = FLAT` (delta-neutral) and carries `meta['approx'] = True` and `meta['multi_leg'] = True`;
  * is therefore skipped by the allocators (`engine/portfolio/base.py` drops FLAT and `approx` signals), i.e. it
    ALWAYS has weight 0 in the master account and can never open a position.
The rationale always contains the word "approssimazione", and the dashboard renders these numbers with "≈".

Structures:
  * `long_straddle` when `HOURS_TO_EVENT` < `max_hours_straddle` (48) AND OVX/100 < `GARCH_VOL`
    (implied cheaper than the forecast): long ATM call + long ATM put at K = F.
  * `short_strangle_defined_risk` when OVX/100 > `GARCH_VOL` * `rich_vol_mult` (1.4) AND `HOURS_TO_EVENT` >
    `min_hours_strangle` (72): short put and short call at +-`strangle_width` (10%) with long wings at
    +-`wing_width` (20%) - an iron condor, so the risk is defined.

Probability and expected return come from the VOL GAP, not from a directional z-score (the position is
delta-neutral, so the project's directional `expected_return` formula does not apply):
  * `vol_gap`   = GARCH_VOL - OVX/100 for the straddle, OVX/100 - GARCH_VOL for the short structure;
  * `prob`      = prob_from_z(vol_gap / `vol_gap_scale`) (conservative, shrunk toward 0.5);
  * E[|move|]   = sqrt(2/pi) * GARCH_VOL * sqrt(T)  (mean absolute move of a driftless normal over the tenor);
  * long straddle:  expected_return = E[|move|] - premium_pct;
  * short condor:   expected_return = premium_pct - E[|move|], capped at the collected premium.
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.strategies import black76
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, fmt_pct, prob_from_z, vol_scale

SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)
SYNTHETIC_PREFIX = "SYNOPT-"


class S18SyntheticOptions(Strategy):
    """Synthetic option structures on the front Brent future - APPROXIMATION, always weight 0 in the master.

    The paper broker cannot hold options: these signals exist to show, on the dashboard, what an option structure
    would be worth given OVX and the GARCH forecast. They are delta-neutral (`direction = FLAT`), labelled
    `approx=True` and skipped by every allocator, so the master weight is structurally zero.
    """

    id = "S18"
    name = "Opzioni sintetiche (approssimazione)"
    family = Family.VOLATILITY
    horizon_days = 5
    warmup_days = 300
    requires = (cat.OVX, cat.GARCH_VOL, cat.RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "max_hours_straddle": 48.0,  # docs: hours_to_event < 48
            "min_hours_strangle": 72.0,  # docs: nessun evento in vista
            "rich_vol_mult": 1.4,  # docs: OVX > GARCH * 1,4
            "tenor_days": 30.0,  # OVX is a 30-day implied vol
            "strangle_width": 0.10,  # short strikes at ±10% from the future
            "wing_width": 0.20,  # long wings at ±20%: defined risk
            "skew_per_10pct": black76.DEFAULT_SKEW_PER_10PCT,
            "rate": 0.0,  # Black-76 discount rate (0 = undiscounted premium approximation)
            "vol_gap_scale": 0.05,  # 5 vol points of gap ~ 1 sigma of conviction
            "straddle_horizon_days": 2,
            "strangle_horizon_days": 21,
            "target_vol": 0.15,
        }

    # ------------------------------------------------------------------ helpers
    def _instrument(self, ctx: MarketContext) -> str:
        front = ctx.curve_codes.get("M1") or ctx.instrument
        return f"{SYNTHETIC_PREFIX}{front}"

    def _leg(self, f: float, k: float, t: float, atm_vol: float, qty: float, is_call: bool) -> dict[str, Any]:
        vol = black76.skew_vol(f, k, atm_vol, float(self.params["skew_per_10pct"]))
        px = black76.price(f, k, t, vol, float(self.params["rate"]), is_call)
        return {
            "type": "call" if is_call else "put",
            "strike": round(k, 4),
            "qty": qty,  # +1 long, -1 short (one synthetic contract per leg)
            "vol": round(vol, 6),
            "price": round(px, 6),
            "delta": round(black76.delta(f, k, t, vol, float(self.params["rate"]), is_call), 6),
            "vega": round(black76.vega(f, k, t, vol, float(self.params["rate"])), 6),
            "approx": True,
        }

    # ------------------------------------------------------------------ entry point
    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        ovx = ctx.f(cat.OVX)
        garch = ctx.f(cat.GARCH_VOL)
        rv = ctx.f(cat.RV_YZ_21)
        f = float(ctx.price)
        if math.isnan(ovx) or math.isnan(garch) or math.isnan(rv) or not math.isfinite(f) or f <= 0.0:
            return None
        iv = ovx / 100.0
        if iv <= 0.0 or garch <= 0.0:
            return None

        hours = ctx.f(cat.HOURS_TO_EVENT)
        t = float(p["tenor_days"]) / 365.0
        e_abs_move = SQRT_2_OVER_PI * garch * math.sqrt(t)

        if hours < float(p["max_hours_straddle"]) and iv < garch:
            structure = "long_straddle"
            legs = [self._leg(f, f, t, iv, 1.0, True), self._leg(f, f, t, iv, 1.0, False)]
            premium_pct = float(sum(leg["qty"] * leg["price"] for leg in legs)) / f
            vol_gap = garch - iv
            expected_return = e_abs_move - premium_pct
            horizon = max(1, int(p["straddle_horizon_days"]))
            testo = (
                f"evento tra {fmt_num(hours, 0)} ore con volatilità implicita {fmt_pct(iv)} sotto la previsione "
                f"GARCH {fmt_pct(garch)}: straddle sintetico long, premio {fmt_pct(premium_pct)}"
            )
        elif iv > garch * float(p["rich_vol_mult"]) and hours > float(p["min_hours_strangle"]):
            structure = "short_strangle_defined_risk"
            width = float(p["strangle_width"])
            wing = float(p["wing_width"])
            legs = [
                self._leg(f, f * (1.0 - width), t, iv, -1.0, False),
                self._leg(f, f * (1.0 + width), t, iv, -1.0, True),
                self._leg(f, f * (1.0 - wing), t, iv, 1.0, False),
                self._leg(f, f * (1.0 + wing), t, iv, 1.0, True),
            ]
            premium_pct = -float(sum(leg["qty"] * leg["price"] for leg in legs)) / f  # positive = collected
            vol_gap = iv - garch
            expected_return = clamp(premium_pct - e_abs_move, -abs(premium_pct), abs(premium_pct))
            horizon = max(1, int(p["strangle_horizon_days"]))
            testo = (
                f"volatilità implicita {fmt_pct(iv)} oltre {fmt_num(float(p['rich_vol_mult']))}x la previsione "
                f"GARCH {fmt_pct(garch)} e nessun evento entro {fmt_num(hours, 0)} ore: strangle sintetico corto "
                f"a rischio definito, premio incassato {fmt_pct(premium_pct)}"
            )
        else:
            return None

        z = vol_gap / float(p["vol_gap_scale"])
        em = expected_move(rv, horizon)
        rationale = f"Approssimazione (nessuna opzione reale disponibile): {testo}."
        return self.make_signal(
            ctx,
            Direction.FLAT,
            prob=prob_from_z(abs(z)),
            expected_return=float(expected_return),
            expected_vol=em,
            rationale=rationale,
            instrument=self._instrument(ctx),
            strength=vol_scale(float(p["target_vol"]), rv) * clamp(abs(z), 0.0, 1.0),
            horizon_days=horizon,
            meta={
                "structure": structure,
                "legs": legs,
                "premium_pct": float(premium_pct),
                "approx": True,
                "multi_leg": True,
                "informational": True,
                "master_weight": 0.0,
                "future": f,
                "tenor_days": float(p["tenor_days"]),
                "implied_vol": iv,
                "garch_vol": garch,
                "vol_gap": float(vol_gap),
                "expected_abs_move_pct": float(e_abs_move),
                "hours_to_event": hours,
                "skew_per_10pct": float(p["skew_per_10pct"]),
            },
        )
