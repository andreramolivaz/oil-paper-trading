"""S3 - Adaptive trend with a local linear trend Kalman filter (docs/STRATEGIES.md).

Thesis: a Kalman filter on the level and the slope of the (log) price estimates the trend with an observation
noise that grows with volatility: in noisy regimes it reacts slowly, in orderly regimes quickly. That reduces the
whipsaws of fixed-window trend followers.

Signal (exactly as documented):
  * state [level, slope] with F = [[1, 1], [0, 1]], run CAUSALLY over ctx.hist(PX, window) - one pass, no
    smoothing, no future data;
  * observation noise R = obs_noise_mult * rv_yz_21^2 / 252 (daily log-return variance), inflated by
    (1 + inflation * (1 - regime.confidence)) when the regime label is the transition one;
  * enter when |slope| / sd(slope) > slope_z_threshold;
  * size proportional to the normalised slope, vol-scaled.

`expected_return` carries the deliberate 0.5 shrinkage factor used across the engine.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from engine.core.events import Direction, Signal
from engine.features.catalog import ATR_14, PX, RV_YZ_21
from engine.regime.base import LABEL_TRANSITION
from engine.strategies.base import Family, MarketContext, Strategy
from engine.strategies.common import TRADING_DAYS, clamp, expected_move, fmt_num, fmt_pct, prob_from_z, vol_scale

SHRINK = 0.5  # deliberate shrinkage of the expected return (anti-overfitting)


def _local_linear_trend(log_px: np.ndarray, r: float, q_level: float, q_slope: float) -> tuple[float, float]:
    """One causal forward pass of a local linear trend filter; returns (slope, sd(slope))."""
    x = np.array([float(log_px[0]), 0.0], dtype=float)
    p = np.array([[1e-2, 0.0], [0.0, 1e-4]], dtype=float)
    f = np.array([[1.0, 1.0], [0.0, 1.0]], dtype=float)
    q = np.array([[q_level, 0.0], [0.0, q_slope]], dtype=float)
    h = np.array([1.0, 0.0], dtype=float)
    for obs in log_px[1:]:
        x = f @ x
        p = f @ p @ f.T + q
        resid = float(obs) - float(h @ x)
        s = float(h @ p @ h) + r
        if s <= 0:
            return float("nan"), float("nan")
        k = (p @ h) / s
        x = x + k * resid
        p = p - np.outer(k, h @ p)
    return float(x[1]), float(math.sqrt(max(float(p[1, 1]), 1e-18)))


class S3KalmanTrend(Strategy):
    id = "S3"
    name = "Trend adattivo (Kalman)"
    family = Family.TREND
    horizon_days = 21
    warmup_days = 300
    requires = (PX, RV_YZ_21, ATR_14)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "window": 300,  # length of the causal pass
            "min_obs": 120,
            # process noise, log-price variance per day; kept below the observation noise so that the Q/R ratio
            # (and therefore the responsiveness) is really driven by rv_yz_21, as the thesis requires
            "q_level": 1e-5,
            "q_slope": 1e-8,  # slope random walk: smaller = smoother trend, tighter slope confidence band
            "obs_noise_mult": 1.0,  # R = obs_noise_mult * rv_yz_21^2 / 252
            "transition_inflation": 1.0,  # R *= 1 + inflation * (1 - regime.confidence) in "Transizione"
            "slope_z_threshold": 1.5,  # docs: filtered slope / its sd above a threshold
            "slope_z_full_size": 3.0,  # normalised slope at which the size is full
            "target_vol": 0.15,
            "stop_atr_mult": 2.5,
            "prob_z_scale": 1.5,
        }

    def generate(self, ctx: MarketContext) -> Signal | None:
        if not self.ready(ctx):
            return None
        rv = ctx.f(RV_YZ_21)
        atr = ctx.f(ATR_14)
        if math.isnan(rv) or math.isnan(atr) or rv <= 0 or atr <= 0:
            return None

        series = ctx.hist(PX, int(self.params["window"])).dropna()
        if len(series) < int(self.params["min_obs"]) or float(series.min()) <= 0:
            return None
        log_px = np.log(series.to_numpy(dtype=float))

        r = float(self.params["obs_noise_mult"]) * (rv**2) / TRADING_DAYS
        inflated = False
        if ctx.regime.label == LABEL_TRANSITION:
            r *= 1.0 + float(self.params["transition_inflation"]) * (1.0 - float(ctx.regime.confidence))
            inflated = True
        if r <= 0:
            return None

        slope, slope_sd = _local_linear_trend(log_px, r, float(self.params["q_level"]), float(self.params["q_slope"]))
        if math.isnan(slope) or math.isnan(slope_sd) or slope_sd <= 0:
            return None

        z = slope / slope_sd
        threshold = float(self.params["slope_z_threshold"])
        if abs(z) <= threshold:
            return None
        direction = Direction.from_sign(z)
        if direction is Direction.FLAT:
            return None

        horizon = self.horizon_days
        sigma_h = expected_move(rv, horizon)
        prob = prob_from_z(abs(z), scale=float(self.params["prob_z_scale"]))
        stop_pct = float(self.params["stop_atr_mult"]) * atr
        size_mult = clamp(abs(z) / float(self.params["slope_z_full_size"]), 0.0, 1.0)
        strength = clamp(vol_scale(float(self.params["target_vol"]), rv) * size_mult, 0.0, 1.0)

        verso = "long" if direction is Direction.LONG else "short"
        nota = " (rumore gonfiato in transizione)" if inflated else ""
        rationale = (
            f"Pendenza Kalman {fmt_pct(slope)} al giorno, {fmt_num(abs(z))} volte la sua deviazione standard"
            f"{nota}: {verso} con size {fmt_num(strength)}, stop {fmt_pct(stop_pct)}."
        )

        return self.make_signal(
            ctx,
            direction,
            prob=prob,
            expected_return=direction.sign * abs(z) * sigma_h * SHRINK,
            expected_vol=sigma_h,
            rationale=rationale,
            stop_pct=stop_pct,
            strength=strength,
            horizon_days=horizon,
            meta={
                "slope_log_per_day": slope,
                "slope_sd": slope_sd,
                "slope_z": z,
                "obs_noise": r,
                "noise_inflated": inflated,
                "n_obs": len(series),
            },
        )
