"""S13 - Geopolitical premium (docs/STRATEGIES.md §S13).

Thesis: news spikes (Iran, Hormuz, Houthi, sanctions) create an immediate risk premium with 1-3 days of momentum;
if the curve and the physical market (calendar spreads, Dated premium) do not confirm a real supply interruption,
the premium deflates over the following 1-3 weeks. The rules are asymmetric: fast entry on the spike, immediate
exit on de-escalation news (the market falls faster than it rises).

Branches, in the order they are evaluated:

1. DE-ESCALATION - `DEESCALATION_FLAG` == 1 -> SHORT over `deesc_horizon_days` (3) with `meta['close_longs'] = True`.
   Checked FIRST (and only because `deescalation_overrides` is True): docs/STRATEGIES.md requires an immediate exit
   from longs on de-escalation news, so it must not be shadowed by a same-day spike flag.
2. SPIKE - `GEO_SPIKE` == 1 today -> LONG over `spike_horizon_days` (2), stop `spike_stop_atr` (1.5) * ATR_14,
   `meta['ttl_days'] = 2`.
3. FADE - a spike occurred between `fade_min_days_ago` (3) and `fade_max_days_ago` (15) sessions ago
   (scanning `ctx.hist(GEO_SPIKE, fade_max_days_ago)`), AND `SPREAD_CHG_5` <= 0 (the curve does not confirm),
   AND `SPOT_FRONT_PREMIUM` is NOT rising over `premium_lookback` (5) sessions -> SHORT (fade the premium) while
   `GEO_INDEX` stays ABOVE its 20-day mean (once it falls back below, the premium is gone and there is nothing to
   fade).
   APPROXIMATED CURVE (`CURVE_APPROX` == 1): the spreads are a WTI proxy, so "the curve does not confirm" cannot be
   trusted. In that case the fade requires `GEO_INDEX` to be FALLING (over the same lookback) instead of simply
   being above its 20-day mean, and the size is multiplied by `approx_strength` (0.5).
4. Otherwise -> None.

Conviction: the spike/de-escalation flags are binary, so there is no continuous z-score to calibrate on. Each
branch uses a documented conviction prior (`spike_z`, `fade_z`, `deesc_z`) which feeds `prob_from_z` and the
expected return; the sizing still goes through `vol_scale(target_vol, RV_YZ_21)`.
"""

from __future__ import annotations

import math
from typing import Any

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import expected_move, fmt_num, fmt_pct, prob_from_z, vol_scale


class S13GeopoliticalPremium(Strategy):
    id = "S13"
    name = "Premio geopolitico"
    family = Family.EVENT
    horizon_days = 2
    warmup_days = 60
    requires = (cat.GEO_INDEX, cat.RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "spike_horizon_days": 2,  # docs: long 2 giorni sul picco
            "spike_stop_atr": 1.5,  # docs: stop 1,5 ATR
            "spike_ttl_days": 2,
            "spike_z": 1.5,
            "fade_min_days_ago": 3,  # docs: dopo 3 giorni
            "fade_max_days_ago": 15,
            "fade_horizon_days": 10,  # docs: il premio si sgonfia in 1-3 settimane
            "fade_stop_atr": 2.0,
            "fade_z": 1.2,
            "geo_mean_window": 20,  # docs: media 20g del geo_index
            "premium_lookback": 5,
            "deesc_horizon_days": 3,  # docs: short a 3 giorni
            "deesc_stop_atr": 2.0,
            "deesc_z": 1.5,
            "deescalation_overrides": True,
            "approx_strength": 0.5,  # size when the curve is a proxy (curve_approx = 1)
            "target_vol": 0.15,
            "stop_sigma": 2.0,
        }

    # ------------------------------------------------------------------ branches
    def _deescalation(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        flag = ctx.f(cat.DEESCALATION_FLAG)
        if math.isnan(flag) or flag != 1.0:
            return None
        rv = ctx.f(cat.RV_YZ_21)
        geo = ctx.f(cat.GEO_INDEX)
        if math.isnan(rv) or math.isnan(geo):
            return None
        horizon = max(1, int(p["deesc_horizon_days"]))
        em = expected_move(rv, horizon)
        z = float(p["deesc_z"])
        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["deesc_stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        rationale = (
            f"Notizie di de-escalation con indice geopolitico a {fmt_num(geo)}: si chiudono i long e "
            f"si valuta uno short a {horizon} giorni sullo sgonfiamento del premio."
        )
        return self.make_signal(
            ctx,
            Direction.SHORT,
            prob=prob_from_z(z),
            expected_return=-z * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=vol_scale(float(p["target_vol"]), rv),
            horizon_days=horizon,
            meta={"close_longs": True, "branch": "deescalation", "geo_index": geo},
        )

    def _spike(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        spike = ctx.f(cat.GEO_SPIKE)
        if math.isnan(spike) or spike != 1.0:
            return None
        rv = ctx.f(cat.RV_YZ_21)
        geo = ctx.f(cat.GEO_INDEX)
        if math.isnan(rv) or math.isnan(geo):
            return None
        horizon = max(1, int(p["spike_horizon_days"]))
        em = expected_move(rv, horizon)
        z = float(p["spike_z"])
        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["spike_stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        rationale = (
            f"Picco di notizie geopolitiche (indice {fmt_num(geo)}): long {horizon} giorni sul premio al rischio "
            f"con stop a {fmt_pct(stop_pct)}."
        )
        return self.make_signal(
            ctx,
            Direction.LONG,
            prob=prob_from_z(z),
            expected_return=z * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=vol_scale(float(p["target_vol"]), rv),
            horizon_days=horizon,
            meta={"ttl_days": int(p["spike_ttl_days"]), "branch": "spike", "geo_index": geo},
        )

    def _fade(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        rv = ctx.f(cat.RV_YZ_21)
        geo = ctx.f(cat.GEO_INDEX)
        spread_chg = ctx.f(cat.SPREAD_CHG_5)
        if math.isnan(rv) or math.isnan(geo) or math.isnan(spread_chg):
            return None

        max_ago = max(1, int(p["fade_max_days_ago"]))
        min_ago = max(1, int(p["fade_min_days_ago"]))
        spikes = ctx.hist(cat.GEO_SPIKE, max_ago)
        if spikes.empty:
            return None
        n = len(spikes)
        days_ago: int | None = None
        for i, val in enumerate(spikes.to_numpy()):
            age = n - 1 - i
            if not math.isnan(float(val)) and float(val) == 1.0 and min_ago <= age <= max_ago:
                days_ago = age  # keep the most recent qualifying spike
        if days_ago is None:
            return None
        if spread_chg > 0.0:
            return None  # the curve confirms the disruption: do not fade

        look = max(1, int(p["premium_lookback"]))
        prem = ctx.hist(cat.SPOT_FRONT_PREMIUM, look + 1).dropna()
        if len(prem) < 2:
            return None  # required history missing
        prem_chg = float(prem.iloc[-1] - prem.iloc[0])
        if prem_chg > 0.0:
            return None  # the Dated premium is rising: physical tightness is real

        geo_hist = ctx.hist(cat.GEO_INDEX, max(2, int(p["geo_mean_window"]))).dropna()
        if len(geo_hist) < 2:
            return None
        geo_mean = float(geo_hist.mean())
        geo_window = ctx.hist(cat.GEO_INDEX, look + 1).dropna()
        geo_chg = float(geo_window.iloc[-1] - geo_window.iloc[0]) if len(geo_window) >= 2 else float("nan")

        approx = ctx.f(cat.CURVE_APPROX)
        is_approx = (not math.isnan(approx)) and approx == 1.0
        strength = vol_scale(float(p["target_vol"]), rv)
        if is_approx:
            if math.isnan(geo_chg) or geo_chg >= 0.0:
                return None  # proxy curve: the fade needs the geopolitical index to be actually falling
            strength *= float(p["approx_strength"])
            condition = f"curva proxy e indice geopolitico in calo ({fmt_num(geo_chg)} su {look} sedute)"
        else:
            if geo <= geo_mean:
                return None  # premium already reabsorbed: nothing left to fade
            condition = f"indice geopolitico {fmt_num(geo)} sopra la media 20g {fmt_num(geo_mean)}"

        horizon = max(1, int(p["fade_horizon_days"]))
        em = expected_move(rv, horizon)
        z = float(p["fade_z"])
        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["fade_stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        rationale = (
            f"Picco geopolitico di {days_ago} sedute fa senza conferma fisica (spread {fmt_num(spread_chg * 100)}%, "
            f"premio spot {fmt_num(prem_chg * 100)}%): short sul premio con {condition}."
        )
        return self.make_signal(
            ctx,
            Direction.SHORT,
            prob=prob_from_z(z),
            expected_return=-z * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=strength,
            horizon_days=horizon,
            meta={
                "branch": "fade",
                "spike_days_ago": days_ago,
                "spread_chg_5": spread_chg,
                "spot_front_premium_chg": prem_chg,
                "geo_index": geo,
                "geo_mean_20": geo_mean,
                "geo_chg": geo_chg,
                # NOTE: flagged as `curve_approx`, NOT as `approx`: the allocator drops signals carrying
                # meta['approx'], and docs/STRATEGIES.md wants this branch traded at half size, not dropped.
                "curve_approx": is_approx,
            },
        )

    # ------------------------------------------------------------------ entry point
    def generate(self, ctx: MarketContext) -> Signal | None:
        branches = [self._spike, self._fade, self._deescalation]
        if bool(self.params["deescalation_overrides"]):
            branches = [self._deescalation, self._spike, self._fade]
        for branch in branches:
            s = branch(ctx)
            if s is not None:
                return s
        return None
