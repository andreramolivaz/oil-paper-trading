"""Regime-conditioned stacking ensemble (brief §11: "ensemble per stacking regime-condizionato").

Combination rule (per horizon)
  * each component model's quantiles are averaged level by level (Vincentization) with weights inversely
    proportional to the model's trailing pinball loss: w_i ∝ 1 / mean_loss_i over the last `window` (252) scored
    forecast dates on which ALL the candidate models have a score (fair comparison);
  * the loss is the mean pinball loss over the 5 levels divided by price_now (relative, so that dates with
    different price levels are comparable);
  * weights shrink toward equal weights: w = (1 - lambda) * w_loss + lambda / n with lambda = 0.5; with no
    scores (or fewer than `min_obs`) the weights are equal;
  * regime_aware: when the features carry REGIME_LABEL a separate score table per label is kept and used when
    it holds >= `min_regime_obs` (30) common dates, otherwise the global table is used;
  * p_up and expected_vol are the weighted averages; approx is True when any component is approximate;
  * drivers = union of the top drivers of the two highest-weight models (<= 4 items); model = "ensemble".

Scoring is walk-forward and point-in-time: `predict` stores each component forecast as pending; `update`
resolves it when the realised price h trading days after the forecast date is known and appends the loss to the
score table; a score is used for the weights at `asof` only when its target date is <= asof. The tables live in
memory; `to_state()`/`from_state()` make them persistable across cron runs (state/forecast_scores.json).
A component that raises NotFitted (or fails) is skipped and reported in meta['skipped']; never silently invented.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from engine.features import catalog as cat
from engine.forecast.base import (
    HORIZONS,
    QUANTILE_LEVELS,
    Forecaster,
    ForecastQuantiles,
    NotFitted,
    asof_date,
    make_forecast,
    naive_index,
    pinball_loss,
    slice_asof,
)

log = logging.getLogger(__name__)

ENSEMBLE_NAME = "ensemble"


@dataclass
class _Pending:
    asof_date: pd.Timestamp
    model: str
    horizon: str
    qs: dict[float, float]
    price_now: float
    regime: str | None


@dataclass
class _Score:
    asof_date: pd.Timestamp
    target_date: pd.Timestamp
    model: str
    horizon: str
    regime: str | None
    loss: float  # pinball loss / price_now
    realized: float


class StackingEnsemble:
    """Weighted Vincentization of component quantiles with inverse-pinball-loss weights, regime-conditioned."""

    name = ENSEMBLE_NAME

    def __init__(
        self,
        models: Sequence[Forecaster],
        regime_aware: bool = True,
        window: int = 252,
        min_obs: int = 20,
        min_regime_obs: int = 30,
        shrink: float = 0.5,
        max_pending_days: int = 400,
        record_pending: bool = True,
    ):
        if not models:
            raise ValueError("StackingEnsemble needs at least one component model")
        if not (0.0 <= shrink <= 1.0):
            raise ValueError("shrink must be in [0, 1]")
        self.models = list(models)
        self.regime_aware = regime_aware
        self.window = window
        self.min_obs = min_obs
        self.min_regime_obs = min_regime_obs
        self.shrink = shrink
        self.max_pending_days = max_pending_days
        self.record_pending = record_pending
        self._pending: list[_Pending] = []
        self._scores: list[_Score] = []
        self._keys: set[tuple[str, str, str]] = set()  # (asof iso, model, horizon) already pending/scored
        self.skipped: dict[str, str] = {}
        self.last_weights: dict[str, dict[str, float]] = {}

    # ------------------------------------------------------------------------------------------------------
    @property
    def model_names(self) -> list[str]:
        return [m.name for m in self.models]

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        for m in self.models:
            try:
                m.fit(features, asof)
                self.skipped.pop(m.name, None)
            except NotFitted as e:
                self.skipped[m.name] = str(e)
            except Exception as e:  # one broken component must not take the ensemble down
                log.warning("ensemble: fit of %s failed: %s", m.name, e)
                self.skipped[m.name] = f"errore: {type(e).__name__}: {e}"

    def _regime_label(self, pit: pd.DataFrame) -> str | None:
        if not self.regime_aware or cat.REGIME_LABEL not in pit.columns or pit.empty:
            return None
        v = pit[cat.REGIME_LABEL].iloc[-1]
        if v is None or (isinstance(v, float) and math.isnan(v)) or str(v) == "":
            return None
        return str(v)

    # ------------------------------------------------------------------------------------------------------
    def _mean_losses(
        self, horizon: str, names: Sequence[str], regime: str | None, asof_d: pd.Timestamp
    ) -> tuple[dict[str, float], int]:
        """Mean relative pinball loss per model over the last `window` dates scored for ALL `names`."""
        by_date: dict[pd.Timestamp, dict[str, float]] = {}
        for s in self._scores:
            if s.horizon != horizon or s.model not in names or s.target_date > asof_d:
                continue
            if regime is not None and s.regime != regime:
                continue
            by_date.setdefault(s.asof_date, {})[s.model] = s.loss
        common = sorted(d for d, ml in by_date.items() if all(n in ml for n in names))[-self.window :]
        if not common:
            return {}, 0
        means = {n: sum(by_date[d][n] for d in common) / len(common) for n in names}
        return means, len(common)

    def weights(
        self, horizon: str, names: Sequence[str], regime: str | None, asof: datetime
    ) -> tuple[dict[str, float], str, int]:
        """(weights, table used: 'regime:<label>' | 'global' | 'equal', number of common scored dates)."""
        asof_d = asof_date(asof)
        n = len(names)
        if n == 0:
            return {}, "equal", 0
        eq = 1.0 / n
        means: dict[str, float] = {}
        table, nobs = "equal", 0
        if regime is not None:
            means, nobs = self._mean_losses(horizon, names, regime, asof_d)
            if nobs >= self.min_regime_obs:
                table = f"regime:{regime}"
            else:
                means = {}
        if not means:
            means, nobs = self._mean_losses(horizon, names, None, asof_d)
            if nobs >= self.min_obs:
                table = "global"
            else:
                means = {}
        if not means:
            return dict.fromkeys(names, eq), "equal", nobs
        inv = {m: 1.0 / max(loss, 1e-12) for m, loss in means.items()}
        tot = sum(inv.values())
        w = {m: (1.0 - self.shrink) * inv[m] / tot + self.shrink * eq for m in names}
        return w, table, nobs

    # ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _union_drivers(ranked: Sequence[ForecastQuantiles], limit: int = 4) -> list[str]:
        out: list[str] = []
        lists = [list(fq.drivers) for fq in ranked[:2]]
        depth = max((len(x) for x in lists), default=0)
        for i in range(depth):
            for lst in lists:
                if i < len(lst) and lst[i] not in out:
                    out.append(lst[i])
                if len(out) >= limit:
                    return out
        return out

    def _remember(
        self, asof_d: pd.Timestamp, model: str, horizon: str, fq: ForecastQuantiles, regime: str | None
    ) -> None:
        key = (asof_d.date().isoformat(), model, horizon)
        if key in self._keys:
            return  # idempotent: re-running the same decision never double-counts
        self._keys.add(key)
        self._pending.append(_Pending(asof_d, model, horizon, fq.quantiles(), fq.price_now, regime))

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        pit = slice_asof(features, asof)
        if pit.empty:
            raise NotFitted("nessuna feature disponibile alla data richiesta")
        asof_d = asof_date(asof)
        regime = self._regime_label(pit)
        preds: dict[str, dict[str, ForecastQuantiles]] = {}
        skipped: dict[str, str] = {}
        for m in self.models:
            try:
                preds[m.name] = m.predict(features, asof, price, curve)
            except NotFitted as e:
                skipped[m.name] = str(e)
            except Exception as e:
                log.warning("ensemble: predict of %s failed at %s: %s", m.name, asof_d.date(), e)
                skipped[m.name] = f"errore: {type(e).__name__}: {e}"
        self.skipped = skipped
        if not preds:
            detail = "; ".join(f"{k}: {v}" for k, v in skipped.items())
            raise NotFitted(f"ensemble: nessun modello disponibile ({detail})")
        out: dict[str, ForecastQuantiles] = {}
        for hname in HORIZONS:
            avail = [n for n in preds if hname in preds[n]]
            if not avail:
                continue
            w, table, nobs = self.weights(hname, avail, regime, asof)
            comps = {n: preds[n][hname] for n in avail}
            qs = {a: sum(w[n] * comps[n].quantiles()[a] for n in avail) for a in QUANTILE_LEVELS}
            p_up = sum(w[n] * comps[n].p_up for n in avail)
            vol = sum(w[n] * comps[n].expected_vol for n in avail)
            approx = any(comps[n].approx for n in avail)
            ranked = sorted(avail, key=lambda n: (-w[n], n))
            drivers = self._union_drivers([comps[n] for n in ranked])
            meta: dict[str, Any] = {
                "weights": {n: float(w[n]) for n in avail},
                "weight_table": table,
                "n_scores": nobs,
                "regime": regime,
                "shrink": self.shrink,
                "skipped": dict(skipped),
                "components": {
                    n: {
                        "median": comps[n].median,
                        "q05": comps[n].q05,
                        "q95": comps[n].q95,
                        "p_up": comps[n].p_up,
                        "expected_vol": comps[n].expected_vol,
                        "approx": comps[n].approx,
                    }
                    for n in avail
                },
            }
            fq = make_forecast(hname, asof, price, qs, p_up, vol, drivers, self.name, approx, meta)
            out[hname] = fq
            self.last_weights[hname] = dict(meta["weights"])
            if self.record_pending:
                for n in avail:
                    self._remember(asof_d, n, hname, comps[n], regime)
                self._remember(asof_d, self.name, hname, fq, regime)
        if not out:
            raise NotFitted("ensemble: nessun orizzonte disponibile")
        return out

    # ------------------------------------------------------------------------------------------------------
    def update(self, realized_price_by_date: pd.Series | Mapping[Any, float]) -> int:
        """Resolve pending forecasts whose target (h trading days after asof) is in the realised series.

        The realised series is indexed by trading date; the target is the h-th date strictly after the forecast
        date, so no exchange calendar is needed. Returns the number of forecasts scored.
        """
        s = (
            realized_price_by_date
            if isinstance(realized_price_by_date, pd.Series)
            else pd.Series(dict(realized_price_by_date))
        )
        if s.empty:
            return 0
        idx = naive_index(s.index).normalize()
        s = pd.Series(pd.to_numeric(s, errors="coerce").to_numpy(dtype=float), index=idx).dropna().sort_index()
        s = s[~s.index.duplicated(keep="last")]
        dates = pd.DatetimeIndex(s.index)
        vals = s.to_numpy(dtype=float)
        if len(dates) == 0:
            return 0
        resolved = 0
        still: list[_Pending] = []
        for p in self._pending:
            pos = int(dates.searchsorted(p.asof_date, side="right"))
            tpos = pos + HORIZONS[p.horizon] - 1
            if tpos < len(dates):
                y = float(vals[tpos])
                loss = pinball_loss(p.qs, y) / p.price_now
                self._scores.append(
                    _Score(p.asof_date, pd.Timestamp(dates[tpos]), p.model, p.horizon, p.regime, loss, y)
                )
                resolved += 1
            elif (pd.Timestamp(dates[-1]) - p.asof_date).days > self.max_pending_days:
                continue  # stale: realised price never arrived (gap in data); drop rather than guess
            else:
                still.append(p)
        self._pending = still
        return resolved

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def score_table(self) -> pd.DataFrame:
        cols = ["asof", "target", "model", "horizon", "regime", "loss", "realized"]
        rows = [(s.asof_date, s.target_date, s.model, s.horizon, s.regime, s.loss, s.realized) for s in self._scores]
        out = pd.DataFrame(rows, columns=cols)
        # normalise the datetime resolution: timestamps rebuilt from a state snapshot (ISO dates) would
        # otherwise carry a different unit than the in-memory ones and break equality comparisons.
        for col in ("asof", "target"):
            out[col] = pd.to_datetime(out[col]).astype("datetime64[us]")
        return out

    # ------------------------------------------------------------------------------------------------------
    def to_state(self) -> dict[str, Any]:
        """JSON-able snapshot of the pending forecasts and the score table."""
        return {
            "pending": [
                {
                    "asof": p.asof_date.date().isoformat(),
                    "model": p.model,
                    "horizon": p.horizon,
                    "qs": {str(a): v for a, v in p.qs.items()},
                    "price_now": p.price_now,
                    "regime": p.regime,
                }
                for p in self._pending
            ],
            "scores": [
                {
                    "asof": s.asof_date.date().isoformat(),
                    "target": s.target_date.date().isoformat(),
                    "model": s.model,
                    "horizon": s.horizon,
                    "regime": s.regime,
                    "loss": s.loss,
                    "realized": s.realized,
                }
                for s in self._scores
            ],
        }

    def from_state(self, state: Mapping[str, Any]) -> None:
        self._pending = [
            _Pending(
                pd.Timestamp(p["asof"]),
                str(p["model"]),
                str(p["horizon"]),
                {float(a): float(v) for a, v in p["qs"].items()},
                float(p["price_now"]),
                p.get("regime"),
            )
            for p in state.get("pending", [])
        ]
        self._scores = [
            _Score(
                pd.Timestamp(s["asof"]),
                pd.Timestamp(s["target"]),
                str(s["model"]),
                str(s["horizon"]),
                s.get("regime"),
                float(s["loss"]),
                float(s["realized"]),
            )
            for s in state.get("scores", [])
        ]
        self._keys = {(p.asof_date.date().isoformat(), p.model, p.horizon) for p in self._pending}
        self._keys |= {(s.asof_date.date().isoformat(), s.model, s.horizon) for s in self._scores}
