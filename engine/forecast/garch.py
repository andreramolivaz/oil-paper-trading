"""GARCH-family volatility forecaster (brief §11): GARCH(1,1), GJR-GARCH(1,1,1) and EGARCH(1,1), Student-t errors.

Walk-forward protocol
  * daily log returns of the continuous price in PERCENT (arch's preferred scale), zero mean (no drift);
  * at each monthly anchor every specification is re-estimated on the trailing `window` (2000) observations
    <= anchor; between anchors the parameters are frozen (`arch_model(...).fix(params)`) and the conditional
    variance is filtered through the returns available at `asof`;
  * the h-day distribution is obtained by simulation: `n_sims` (2000) paths per specification over MAX_HORIZON
    days, pooled across the three specifications (equal weights) -> cumulative log-return quantiles -> price
    quantiles price * exp(q);
  * p_up is the share of simulated paths ending above zero. The mean return is zero by construction, so p_up is
    ~0.5 up to simulation noise: this model forecasts the WIDTH of the distribution, not its direction;
  * expected_vol = sqrt(mean of the forecast daily variance over the h days) * sqrt(252);
  * `forecast_vol_path(h)` exposes the per-day annualised vol path (averaged across specifications) for the
    risk module (VaR/ES scaling);
  * the simulation seed is derived from the asof date, so a forecast is reproducible and independent of any
    data appended after asof.
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

from engine.forecast.base import (
    HORIZONS,
    MAX_HORIZON,
    QUANTILE_LEVELS,
    TRADING_DAYS,
    ForecastQuantiles,
    NotFitted,
    asof_date,
    log_returns,
    make_forecast,
    refit_anchor,
    slice_asof,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GarchSpec:
    label: str
    vol: Literal["GARCH", "EGARCH"]  # arch 'vol' argument
    p: int
    o: int
    q: int


DEFAULT_SPECS: tuple[GarchSpec, ...] = (
    GarchSpec("GARCH(1,1)", "GARCH", 1, 0, 1),
    GarchSpec("GJR-GARCH(1,1,1)", "GARCH", 1, 1, 1),
    GarchSpec("EGARCH(1,1)", "EGARCH", 1, 0, 1),
)


@dataclass
class _SpecFit:
    spec: GarchSpec
    params: np.ndarray
    converged: bool
    loglik: float


@dataclass
class _SimResult:
    cum_returns: np.ndarray  # (n_paths_total, MAX_HORIZON) cumulative log returns (fraction)
    var_path: np.ndarray  # (MAX_HORIZON,) mean forecast daily variance across specs (fraction^2)
    cond_vol_now: dict[str, float]  # annualised conditional vol at asof per spec
    anchor: pd.Timestamp
    n_obs: int


class GarchForecaster:
    """GARCH / GJR / EGARCH with Student-t innovations; monthly refit; simulated h-day quantiles; no drift."""

    name = "garch"

    def __init__(
        self,
        specs: tuple[GarchSpec, ...] = DEFAULT_SPECS,
        window: int = 2000,
        n_sims: int = 2000,
        min_obs: int = 250,
        dist: Literal["t", "normal", "skewt"] = "t",
    ):
        self.specs = specs
        self.window = window
        self.n_sims = n_sims
        self.min_obs = min_obs
        self.dist = dist
        self._fits: dict[pd.Timestamp, list[_SpecFit]] = {}
        self._last: _SimResult | None = None

    # ------------------------------------------------------------------------------------------------------
    def _returns_pct(self, pit: pd.DataFrame, upto: pd.Timestamp | None = None) -> pd.Series:
        r = log_returns(pit)
        if upto is not None:
            r = r.loc[:upto]
        r = r.iloc[-self.window :] * 100.0
        if len(r) < self.min_obs:
            raise NotFitted(f"storico insufficiente per GARCH ({len(r)} < {self.min_obs} rendimenti)")
        if float(r.std(ddof=1)) <= 0:
            raise NotFitted("rendimenti a varianza nulla: GARCH non stimabile")
        return r.astype(float)

    def _model(self, r: pd.Series, spec: GarchSpec, seed: int | None = None) -> Any:
        """arch model with a zero mean. `seed` seeds the innovation distribution used by the simulation forecast
        (arch draws innovations from the distribution's own generator, not from forecast(random_state=...))."""
        from arch import arch_model
        from arch.univariate import Normal, SkewStudent, StudentsT

        am = arch_model(r, mean="Zero", vol=spec.vol, p=spec.p, o=spec.o, q=spec.q, dist=self.dist, rescale=False)
        if seed is not None:
            if self.dist == "t":
                am.distribution = StudentsT(seed=seed)
            elif self.dist == "skewt":
                am.distribution = SkewStudent(seed=seed)
            else:
                am.distribution = Normal(seed=seed)
        return am

    def _fit_at(self, pit: pd.DataFrame, anchor: pd.Timestamp) -> list[_SpecFit]:
        r = self._returns_pct(pit, upto=anchor)
        fits: list[_SpecFit] = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for spec in self.specs:
                try:
                    res = self._model(r, spec).fit(disp="off", show_warning=False)
                    params = np.asarray(res.params, dtype=float)
                    if not np.all(np.isfinite(params)):
                        raise ValueError("non-finite parameters")
                    fits.append(_SpecFit(spec, params, int(res.convergence_flag) == 0, float(res.loglikelihood)))
                except Exception as e:  # a failed specification is skipped, never invented
                    log.warning("%s fit failed at %s: %s", spec.label, anchor.date(), e)
        if not fits:
            raise NotFitted("nessuna specifica GARCH stimabile a questo rifit")
        return fits

    def _fits_for(self, pit: pd.DataFrame, asof: datetime) -> tuple[pd.Timestamp, list[_SpecFit]]:
        anchor = refit_anchor(pit.index, asof, "M")
        fits = self._fits.get(anchor)
        if fits is None:
            fits = self._fit_at(pit, anchor)
            self._fits[anchor] = fits
        return anchor, fits

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        self._fits_for(slice_asof(features, asof), asof)

    # ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _seed(asof: datetime) -> int:
        d = asof_date(asof)
        return int(d.year * 10000 + d.month * 100 + d.day)

    def _simulate(self, pit: pd.DataFrame, asof: datetime) -> _SimResult:
        anchor, fits = self._fits_for(pit, asof)
        r = self._returns_pct(pit)
        cums: list[np.ndarray] = []
        var_paths: list[np.ndarray] = []
        cond_vol: dict[str, float] = {}
        seed = self._seed(asof)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for k, f in enumerate(fits):
                fixed = self._model(r, f.spec, seed=seed + k).fix(f.params)
                fc = fixed.forecast(horizon=MAX_HORIZON, method="simulation", simulations=self.n_sims, reindex=False)
                sims = np.asarray(fc.simulations.values, dtype=float)[-1]  # (n_sims, H) daily returns in percent
                cums.append(np.cumsum(sims, axis=1) / 100.0)
                var_paths.append(np.asarray(fc.variance.values, dtype=float)[-1] / 1e4)
                cv = float(fixed.conditional_volatility.iloc[-1]) / 100.0
                cond_vol[f.spec.label] = cv * math.sqrt(TRADING_DAYS)
        res = _SimResult(np.vstack(cums), np.mean(np.vstack(var_paths), axis=0), cond_vol, anchor, len(r))
        self._last = res
        return res

    def forecast_vol_path(
        self, h: int, features: pd.DataFrame | None = None, asof: datetime | None = None
    ) -> np.ndarray:
        """Annualised vol for each of the next `h` days (mean variance across specs). Uses the last predict when
        `features`/`asof` are omitted."""
        if h < 1 or h > MAX_HORIZON:
            raise ValueError(f"h must be in [1, {MAX_HORIZON}]")
        if features is not None and asof is not None:
            res = self._simulate(slice_asof(features, asof), asof)
        elif self._last is not None:
            res = self._last
        else:
            raise NotFitted("nessuna previsione GARCH disponibile: chiamare predict prima")
        return np.sqrt(res.var_path[:h] * TRADING_DAYS)

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        pit = slice_asof(features, asof)
        if pit.empty:
            raise NotFitted("nessuna feature disponibile alla data richiesta")
        res = self._simulate(pit, asof)
        fits = self._fits[res.anchor]
        labels = [f.spec.label for f in fits]
        cond_txt = ", ".join(f"{lab} {v * 100:.0f}%" for lab, v in res.cond_vol_now.items())
        out: dict[str, ForecastQuantiles] = {}
        for hname, h in HORIZONS.items():
            dist = res.cum_returns[:, h - 1]
            qr = np.quantile(dist, QUANTILE_LEVELS)
            qs = {a: float(price * math.exp(v)) for a, v in zip(QUANTILE_LEVELS, qr)}
            p_up = float(np.mean(dist > 0.0))
            expected_vol = float(math.sqrt(max(float(np.mean(res.var_path[:h])), 1e-12) * TRADING_DAYS))
            drivers = [
                f"Vol condizionata annua: {cond_txt}",
                f"Vol attesa a {h}g: {expected_vol * 100:.0f}% annua (media di {len(fits)} modelli, t di Student)",
                "Nessuna deriva: p_up ≈ 0,5 per costruzione (modello di volatilità)",
            ]
            meta: dict[str, Any] = {
                "anchor": res.anchor.date().isoformat(),
                "specs": labels,
                "converged": {f.spec.label: f.converged for f in fits},
                "n_sims_total": int(res.cum_returns.shape[0]),
                "n_obs": res.n_obs,
                "cond_vol_now": dict(res.cond_vol_now),
                "sim_mean_return": float(np.mean(dist)),
                "seed": self._seed(asof),
            }
            out[hname] = make_forecast(hname, asof, price, qs, p_up, expected_vol, drivers, self.name, False, meta)
        return out
