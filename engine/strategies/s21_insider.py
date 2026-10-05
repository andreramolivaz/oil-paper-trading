"""S21 - Insider conviction in the oil complex (docs/STRATEGIES.md §S21).

Thesis: officers and directors of exploration-and-production and oilfield-services companies know their own
marginal cost, decline rates and hedge book. When several of them buy their own shares on the open market, with
their own money, after tax, they are expressing a view on forward oil economics that no published series
carries yet. The mirror case is weaker by construction: an insider sells for a house, a divorce or a
diversification rule, so sales enter the score at a fraction of the weight.

Who is on the other side: investors who read the same filings as a governance footnote rather than as a
commodity forecast, and systematic flows that price crude off the curve and inventories alone.

Favourable regimes: ranges and turning points, where the curve and momentum say nothing. It is the opposite of
a shock strategy — a geopolitical gap moves crude faster than anyone can file a Form 4.

Invalidation: the signal fails when insider flow is driven by equity-specific events (a merger, a buyback
window, a stake build by a 10% owner) rather than by the commodity. The feature excludes 10% owners and the
strategy requires BREADTH — several distinct companies agreeing — precisely so a single company's story cannot
move the master.

Signal:
  * LONG when ``INSIDER_SCORE`` > ``buy_threshold`` (0.5, i.e. the top quartile of its own three-year history)
    AND at least ``min_breadth`` (3) distinct companies bought in the window;
  * SHORT when the score < ``sell_threshold`` (-0.7). The bar is deliberately higher than for longs because
    the sell side carries less information;
  * nothing at all below ``min_tx`` (6) qualifying transactions in the window: with roughly nine open-market
    purchases per name per year, a quiet quarter is normal and silence must not be read as a flat view.

Horizon: ``horizon_days`` (42 sessions, two months). This is a slow factor. The filing deadline alone is two
business days, and the flow needs weeks to accumulate; anyone trading it intraday is trading noise.

The score is an APPROXIMATION in the sense the project uses the word: the filing timestamp is inferred from the
statutory deadline rather than observed, so every signal carries ``meta["approx"] = True`` and the dashboard
shows "≈".
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import clamp, expected_move, fmt_num, prob_from_z, vol_scale


class S21InsiderConviction(Strategy):
    id = "S21"
    name = "Convinzione degli insider (Form 4)"
    family = Family.FUNDAMENTAL
    horizon_days = 42
    warmup_days = 400
    # The score decides the DIRECTION here, so a missing score means no signal at all — the project's rule for
    # a feature that carries the thesis rather than the size.
    requires = (cat.INSIDER_SCORE, cat.RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "buy_threshold": 0.5,
            "sell_threshold": -0.7,  # higher bar: insider sales are much less informative than purchases
            "min_breadth": 3,  # distinct companies buying; one company alone is a company story
            "min_tx": 6,  # data sufficiency: a quiet quarter is normal, not a flat view
            "horizon_days": 42,
            "base_z": 1.2,  # a weak, slow factor: conviction stays modest by design
            "z_span": 0.8,
            "target_vol": 0.15,
            "stop_atr": 2.5,
            "stop_sigma": 2.5,
        }

    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        score = ctx.f(cat.INSIDER_SCORE)
        rv = ctx.f(cat.RV_YZ_21)
        if math.isnan(score) or math.isnan(rv):
            return None

        n_tx = ctx.f(cat.INSIDER_N_TX)
        if math.isnan(n_tx) or n_tx < float(p["min_tx"]):
            return None
        breadth = ctx.f(cat.INSIDER_BREADTH)
        buy_ratio = ctx.f(cat.INSIDER_BUY_RATIO)

        buy_th, sell_th = float(p["buy_threshold"]), float(p["sell_threshold"])
        if score > buy_th:
            need = float(p["min_breadth"])
            if math.isnan(breadth) or breadth < need:
                return None
            side = 1
            excess = (score - buy_th) / max(1e-9, 1.0 - buy_th)
            testo = (
                f"punteggio insider {fmt_num(score, 2)} sopra {fmt_num(buy_th, 2)} "
                f"con {int(breadth)} societa che hanno comprato"
            )
        elif score < sell_th:
            side = -1
            excess = (sell_th - score) / max(1e-9, 1.0 + sell_th)
            testo = f"punteggio insider {fmt_num(score, 2)} sotto {fmt_num(sell_th, 2)} (vendite nette diffuse)"
        else:
            return None

        excess = clamp(excess, 0.0, 1.0)
        horizon = max(1, int(p["horizon_days"]))
        em = expected_move(rv, horizon)
        z = float(p["base_z"]) + float(p["z_span"]) * excess
        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        verso = "long" if side > 0 else "short"
        ratio_txt = "" if math.isnan(buy_ratio) else f", {fmt_num(buy_ratio * 100, 0)}% delle operazioni in acquisto"
        rationale = (
            f"≈ Form 4 dell'ultimo trimestre: {testo}{ratio_txt} su {int(n_tx)} operazioni di mercato; "
            f"{verso} a {horizon} giorni. Data di deposito stimata dalla scadenza di legge (2 giorni "
            f"lavorativi), non osservata."
        )
        return self.make_signal(
            ctx,
            Direction.from_sign(side),
            prob=prob_from_z(z),
            expected_return=side * z * em * 0.5,
            expected_vol=em,
            horizon_days=horizon,
            strength=vol_scale(rv, float(p["target_vol"])),
            stop_pct=stop_pct,
            rationale=rationale,
            meta={
                "approx": True,
                "insider_score": round(float(score), 4),
                "insider_n_tx": int(n_tx),
                "insider_breadth": None if math.isnan(breadth) else int(breadth),
                "insider_buy_ratio": None if math.isnan(buy_ratio) else round(float(buy_ratio), 4),
                "published_at_rule": "transaction_date + 2 business days (SEC Form 4 deadline)",
            },
        )
