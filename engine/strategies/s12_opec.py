"""S12 - OPEC+ playbook (docs/STRATEGIES.md §S12).

Thesis: before an OPEC+ meeting implied vol rises and the market positions itself; after the decision the drift
lasts 2-5 days while quotas, compliance and statements are digested. The pre-event direction follows positioning
and the curve, but with a reduced size because the event is binary.

Two branches, both keyed on `ctx.next_events` entries whose `event_id` starts with `opec` (case-insensitive):

PRE-EVENT  - an OPEC event within `pre_hours` (48 h) ahead (`hours_away` in [0, 48]):
    direction = sign(TSMOM_21), taken ONLY when it agrees with sign(SLOPE_M1_M6) (fallback SLOPE_M1_M2 when the
    M1-M6 slope is NaN). Reduced size (`pre_strength`, 0.5), `meta['pre_event'] = True`.

POST-EVENT - an OPEC event in the last `post_hours` (72 h) (`hours_away` in [-72, 0)):
    follow the sign of RET_1 OF THE EVENT DAY when |RET_1| > `post_sigma_mult` * sigma, with
    sigma = RV_YZ_21 / sqrt(252) (the 1-day vol). `meta['post_event'] = True`.

The event-day return is read from `ctx.hist(RET_1, ...)` by taking the last row dated on or before the event date
(if the event fell on a non-trading day we use the previous settlement). No OPEC event in [-72 h, +48 h] -> None.

PRECEDENCE: when both windows overlap (two meetings close together) the PRE-EVENT branch wins, because the binary
risk ahead dominates the risk budget. Controlled by `pre_event_wins`.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from engine.core.events import Direction, Signal
from engine.features import catalog as cat
from engine.strategies.base import EventInfo, Family, MarketContext, Strategy
from engine.strategies.common import TRADING_DAYS, clamp, expected_move, fmt_num, prob_from_z, sign, vol_scale


class S12OpecPlaybook(Strategy):
    id = "S12"
    name = "Playbook OPEC+"
    family = Family.EVENT
    horizon_days = 3
    warmup_days = 120
    requires = (cat.TSMOM_21, cat.RV_YZ_21)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "event_prefix": "opec",
            "pre_hours": 48.0,  # docs: nei 2 giorni prima
            "post_hours": 72.0,  # docs: nei 3 giorni dopo
            "pre_strength": 0.5,  # leva ridotta per il rischio binario
            "post_strength": 1.0,
            "pre_horizon_days": 2,
            "post_horizon_days": 3,
            "post_sigma_mult": 1.0,  # docs: rendimento del giorno dell'evento > 1 sigma
            "pre_event_wins": True,
            "target_vol": 0.15,
            "stop_atr": 2.0,
            "stop_sigma": 2.0,
            "z_cap": 3.0,
            "pre_z": 1.0,  # documented conviction prior for the pre-event (binary) leg
            "lookback_rows": 25,  # rows scanned to locate the event-day RET_1
        }

    # ------------------------------------------------------------------ helpers
    def _opec_events(self, ctx: MarketContext) -> list[EventInfo]:
        prefix = str(self.params["event_prefix"]).lower()
        return [e for e in ctx.next_events if str(e.event_id).lower().startswith(prefix)]

    def _event_day_return(self, ctx: MarketContext, event: EventInfo) -> float:
        """RET_1 of the event day: the last feature row dated on or before the event date."""
        h = ctx.hist(cat.RET_1, max(2, int(self.params["lookback_rows"])))
        if h.empty:
            return float("nan")
        ev_day = event.ts.date()
        out = float("nan")
        for idx, val in zip(h.index, h.to_numpy()):
            try:
                row_day = pd.Timestamp(idx).date()
            except (TypeError, ValueError):
                return float("nan")
            if row_day <= ev_day:
                out = float(val)
        return out

    def _curve_agreement_slope(self, ctx: MarketContext) -> tuple[float, str]:
        slope = ctx.f(cat.SLOPE_M1_M6)
        if not math.isnan(slope):
            return slope, cat.SLOPE_M1_M6
        return ctx.f(cat.SLOPE_M1_M2), cat.SLOPE_M1_M2

    # ------------------------------------------------------------------ branches
    def _pre_event(self, ctx: MarketContext, event: EventInfo) -> Signal | None:
        p = self.params
        tsmom = ctx.f(cat.TSMOM_21)
        rv = ctx.f(cat.RV_YZ_21)
        if math.isnan(tsmom) or math.isnan(rv):
            return None
        slope, slope_name = self._curve_agreement_slope(ctx)
        if math.isnan(slope):
            return None  # no curve: no pre-event position
        side = sign(tsmom)
        if side == 0 or side != sign(slope):
            return None  # trend and carry disagree: stay flat into a binary event

        horizon = max(1, int(p["pre_horizon_days"]))
        em = expected_move(rv, horizon)
        z = float(p["pre_z"])
        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        verso = "rialzista" if side > 0 else "ribassista"
        rationale = (
            f"Riunione OPEC+ tra {fmt_num(event.hours_away, 0)} ore: posizione ridotta {verso} "
            f"con trend 21g {fmt_num(tsmom)} concorde allo slope {fmt_num(slope)}; "
            f"size al {fmt_num(float(p['pre_strength']) * 100, 0)}% per il rischio binario."
        )
        return self.make_signal(
            ctx,
            Direction.from_sign(side),
            prob=prob_from_z(z),
            expected_return=side * z * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=float(p["pre_strength"]) * vol_scale(float(p["target_vol"]), rv),
            horizon_days=horizon,
            meta={
                "pre_event": True,
                "event_id": event.event_id,
                "event_ts": event.ts.isoformat(),
                "hours_away": event.hours_away,
                "tsmom_21": tsmom,
                "slope_used": slope_name,
                "slope": slope,
            },
        )

    def _post_event(self, ctx: MarketContext, event: EventInfo) -> Signal | None:
        p = self.params
        rv = ctx.f(cat.RV_YZ_21)
        if math.isnan(rv):
            return None
        ret_event = self._event_day_return(ctx, event)
        if math.isnan(ret_event):
            return None
        sigma_1d = rv / math.sqrt(TRADING_DAYS)
        if sigma_1d <= 0.0:
            return None
        z = abs(ret_event) / sigma_1d
        if z <= float(p["post_sigma_mult"]):
            return None  # reaction too small to be a decision drift
        side = sign(ret_event)
        if side == 0:
            return None

        horizon = max(1, int(p["post_horizon_days"]))
        em = expected_move(rv, horizon)
        z_abs = min(z, float(p["z_cap"]))
        atr = ctx.f(cat.ATR_14)
        stop_pct = float(p["stop_atr"]) * atr if not math.isnan(atr) else float(p["stop_sigma"]) * em
        verso = "rialzista" if side > 0 else "ribassista"
        rationale = (
            f"Dopo la riunione OPEC+ ({fmt_num(-event.hours_away, 0)} ore): reazione del giorno dell'evento "
            f"{fmt_num(ret_event * 100)}% pari a {fmt_num(z)} sigma, si segue il drift {verso} "
            f"per {horizon} giorni."
        )
        return self.make_signal(
            ctx,
            Direction.from_sign(side),
            prob=prob_from_z(z_abs),
            expected_return=side * z_abs * em * 0.5,
            expected_vol=em,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=float(p["post_strength"])
            * vol_scale(float(p["target_vol"]), rv)
            * clamp(z_abs / float(p["z_cap"]), 0.0, 1.0),
            horizon_days=horizon,
            meta={
                "post_event": True,
                "event_id": event.event_id,
                "event_ts": event.ts.isoformat(),
                "hours_away": event.hours_away,
                "event_day_ret_1": ret_event,
                "event_day_sigmas": z,
            },
        )

    # ------------------------------------------------------------------ entry point
    def generate(self, ctx: MarketContext) -> Signal | None:
        p = self.params
        events = self._opec_events(ctx)
        if not events:
            return None
        pre_hours = float(p["pre_hours"])
        post_hours = float(p["post_hours"])
        pre = [e for e in events if 0.0 <= e.hours_away <= pre_hours]
        post = [e for e in events if -post_hours <= e.hours_away < 0.0]
        pre.sort(key=lambda e: e.hours_away)
        post.sort(key=lambda e: -e.hours_away)  # most recent first

        order: list[Signal | None] = []
        if bool(p["pre_event_wins"]):
            order = [self._pre_event(ctx, pre[0]) if pre else None, self._post_event(ctx, post[0]) if post else None]
        else:
            order = [self._post_event(ctx, post[0]) if post else None, self._pre_event(ctx, pre[0]) if pre else None]
        for s in order:
            if s is not None:
                return s
        return None
