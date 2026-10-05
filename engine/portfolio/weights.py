"""S20: regime-conditioned online allocator weights.

Hedge (multiplicative weights) on the out-of-sample Sharpe each strategy achieved IN THE CURRENT REGIME,
shrunk toward inverse-volatility risk parity and penalised for correlation:

    raw_i   = exp(eta * Sharpe_i|regime)                      (Hedge / exponential weights)
    rp_i    = (1 / vol_i) / sum_j (1 / vol_j)                 (risk parity, the humble prior)
    blend_i = (1 - lambda) * raw_i / sum(raw) + lambda * rp_i
    pen_i   = blend_i * prod over j != i of (1 - max(0, |rho_ij| - rho0) )   (diversification penalty)
    w_i     = pen_i / sum(pen)                                 over ACTIVE strategies only

Only strategies whose lifecycle is "active" can carry weight; research/incubation/retired get exactly zero
(brief §12 lifecycle). Weights move weekly and only when the change is material (hysteresis), so turnover
stays low. Every weight carries an Italian explanation for the dashboard.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252
ACTIVE = "active"


@dataclass
class WeightSet:
    weights: dict[str, float] = field(default_factory=dict)
    explanations: dict[str, str] = field(default_factory=dict)
    regime: str = ""
    n_obs: dict[str, int] = field(default_factory=dict)
    conditional: dict[str, bool] = field(default_factory=dict)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "updated_at": self.updated_at,
            "weights": {k: round(float(v), 5) for k, v in sorted(self.weights.items())},
            "explanations": self.explanations,
            "n_obs": self.n_obs,
            "regime_conditional": self.conditional,
        }


def sharpe_of(returns: pd.Series) -> float:
    r = pd.to_numeric(returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(r) < 20:
        return float("nan")
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return float("nan")
    return float(r.mean() / sd * math.sqrt(TRADING_DAYS))


def ann_vol_of(returns: pd.Series) -> float:
    r = pd.to_numeric(returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(r) < 20:
        return float("nan")
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))


class RegimeConditionedWeights:
    """Holds the weight state between runs; `update` is called weekly by the live job."""

    name = "S20"

    def __init__(
        self,
        eta: float = 1.0,
        shrink_lambda: float = 0.5,
        corr_threshold: float = 0.5,
        min_regime_obs: int = 60,
        lookback_days: int = 504,
        hysteresis: float = 0.05,
        max_weight: float = 0.35,
    ):
        self.eta = float(eta)
        self.shrink_lambda = float(shrink_lambda)
        self.corr_threshold = float(corr_threshold)
        self.min_regime_obs = int(min_regime_obs)
        self.lookback_days = int(lookback_days)
        self.hysteresis = float(hysteresis)
        self.max_weight = float(max_weight)
        self.current = WeightSet()

    # ------------------------------------------------------------------
    def update(
        self,
        returns: pd.DataFrame,
        lifecycles: dict[str, str],
        regime_label: str,
        regime_by_day: pd.Series | None = None,
        asof: str | None = None,
    ) -> WeightSet:
        """Recompute the weights. `returns` has one column per strategy id (daily out-of-sample returns)."""
        active = [s for s, lc in sorted(lifecycles.items()) if lc == ACTIVE]
        if not active:
            self.current = WeightSet(
                weights=dict.fromkeys(sorted(lifecycles), 0.0),
                explanations=dict.fromkeys(
                    sorted(lifecycles), "Peso zero: la strategia non è attiva (vedi validazione)."
                ),
                regime=regime_label,
                updated_at=asof,
            )
            return self.current

        window = returns.tail(self.lookback_days) if not returns.empty else returns
        cond_mask = None
        if regime_by_day is not None and not window.empty:
            aligned = regime_by_day.reindex(window.index)
            cond_mask = (aligned == regime_label).fillna(False).to_numpy()

        sharpes: dict[str, float] = {}
        vols: dict[str, float] = {}
        n_obs: dict[str, int] = {}
        conditional: dict[str, bool] = {}
        for sid in active:
            col = window[sid] if sid in window.columns else pd.Series(dtype=float)
            sub = col[cond_mask] if cond_mask is not None and len(col) == len(cond_mask) else col
            use_conditional = bool(len(sub.dropna()) >= self.min_regime_obs)
            series = sub if use_conditional else col
            sharpes[sid] = sharpe_of(series)
            vols[sid] = ann_vol_of(col)
            n_obs[sid] = len(series.dropna())
            conditional[sid] = use_conditional

        # Hedge weights on the available Sharpes (a missing Sharpe is treated as 0: no evidence, no tilt)
        raw = {s: math.exp(self.eta * (0.0 if not math.isfinite(sharpes[s]) else sharpes[s])) for s in active}
        raw_sum = sum(raw.values()) or 1.0
        hedge = {s: raw[s] / raw_sum for s in active}

        inv_vol = {s: (1.0 / vols[s] if math.isfinite(vols[s]) and vols[s] > 0 else 0.0) for s in active}
        inv_sum = sum(inv_vol.values())
        rp = {s: (inv_vol[s] / inv_sum if inv_sum > 0 else 1.0 / len(active)) for s in active}

        blend = {s: (1.0 - self.shrink_lambda) * hedge[s] + self.shrink_lambda * rp[s] for s in active}

        corr = None
        if not window.empty:
            cols = [s for s in active if s in window.columns]
            if len(cols) >= 2:
                corr = window[cols].corr().abs()
        penalised = dict(blend)
        if corr is not None:
            for s in active:
                if s not in corr.columns:
                    continue
                factor = 1.0
                for other in active:
                    if other == s or other not in corr.columns:
                        continue
                    rho = float(np.asarray(corr.loc[s, other], dtype=float))
                    if math.isfinite(rho) and rho > self.corr_threshold:
                        factor *= max(0.0, 1.0 - (rho - self.corr_threshold))
                penalised[s] = blend[s] * factor

        total = sum(penalised.values())
        weights = {s: penalised[s] / total for s in active} if total > 0 else {s: 1.0 / len(active) for s in active}
        weights = self._cap_and_renormalise(weights)
        weights = self._apply_hysteresis(weights)

        explanations: dict[str, str] = {}
        for s in active:
            sh = sharpes[s]
            scope = "nel regime corrente" if conditional[s] else "su tutto il campione"
            sh_txt = "n/d" if not math.isfinite(sh) else f"{sh:.2f}".replace(".", ",")
            explanations[s] = (
                f"Peso {weights.get(s, 0.0):.0%}: Sharpe out-of-sample {sh_txt} {scope} "
                f"su {n_obs[s]} giorni, con shrinkage verso il risk parity."
            )
        for s, lc in sorted(lifecycles.items()):
            if lc != ACTIVE:
                weights[s] = 0.0
                explanations[s] = f"Peso zero: ciclo di vita «{lc}» (solo conto ombra)."

        self.current = WeightSet(
            weights=weights,
            explanations=explanations,
            regime=regime_label,
            n_obs=n_obs,
            conditional=conditional,
            updated_at=asof,
        )
        return self.current

    # ------------------------------------------------------------------
    def _cap_and_renormalise(self, weights: dict[str, float]) -> dict[str, float]:
        """No single strategy above `max_weight`; the excess is spread over the others."""
        w = dict(weights)
        for _ in range(10):
            over = {s: v for s, v in w.items() if v > self.max_weight + 1e-12}
            if not over:
                break
            excess = sum(v - self.max_weight for v in over.values())
            for s in over:
                w[s] = self.max_weight
            room = {s: v for s, v in w.items() if s not in over}
            room_total = sum(room.values())
            if room_total <= 0:
                break
            for s, value in room.items():
                w[s] += excess * value / room_total
        total = sum(w.values()) or 1.0
        return {s: v / total for s, v in w.items()}

    def _apply_hysteresis(self, new: dict[str, float]) -> dict[str, float]:
        old = self.current.weights
        if not old:
            return new
        out = {}
        for s, v in new.items():
            prev = float(old.get(s, 0.0))
            out[s] = prev if abs(v - prev) < self.hysteresis else v
        total = sum(out.values()) or 1.0
        return {s: v / total for s, v in out.items()}

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "params": {
                "eta": self.eta,
                "shrink_lambda": self.shrink_lambda,
                "corr_threshold": self.corr_threshold,
                "min_regime_obs": self.min_regime_obs,
                "lookback_days": self.lookback_days,
                "hysteresis": self.hysteresis,
                "max_weight": self.max_weight,
            },
            "current": self.current.to_dict(),
        }

    def load(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        cur = data.get("current") or {}
        self.current = WeightSet(
            weights={k: float(v) for k, v in (cur.get("weights") or {}).items()},
            explanations=dict(cur.get("explanations") or {}),
            regime=str(cur.get("regime") or ""),
            n_obs={k: int(v) for k, v in (cur.get("n_obs") or {}).items()},
            conditional={k: bool(v) for k, v in (cur.get("regime_conditional") or {}).items()},
            updated_at=cur.get("updated_at"),
        )
