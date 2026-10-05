"""ARIMA + ETS forecaster on the log price (brief §11).

Walk-forward protocol
  * at each monthly anchor (first trading day of the month, `refit_anchor`) the ARIMA order is chosen among a
    small grid by AIC on the trailing `window` observations and the parameters are estimated; an ETS model with
    additive error and damped additive trend is fitted on the same window;
  * between anchors the fitted parameters are kept fixed and only the state is updated with the data available
    at `asof` (statsmodels `filter` / `smooth` with the stored parameters): no refit, no look-ahead;
  * the two h-step forecasts of the log price are averaged (equal-weight mixture); the mixture variance is the
    mean of the two forecast variances plus the disagreement term; the standard deviation is widened by
    WIDEN = 1.2 to account for parameter uncertainty and fat tails that the Gaussian forecast variance ignores;
  * quantiles are Normal around the mixture mean; p_up = Phi(mu_h / sd_h).

The forecast log-change is applied to the actual front settlement `price`, so the roll-adjusted continuous
series drives the dynamics while the quantiles stay in front-contract price terms.
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.forecast.base import (
    HORIZONS,
    MAX_HORIZON,
    TRADING_DAYS,
    ForecastQuantiles,
    NotFitted,
    make_forecast,
    normal_quantile,
    price_series,
    refit_anchor,
    slice_asof,
)

log = logging.getLogger(__name__)

WIDEN = 1.2  # documented widening factor applied to the Gaussian forecast standard deviation
DEFAULT_ORDERS: tuple[tuple[int, int, int], ...] = tuple((p, 1, q) for p in range(3) for q in range(3))


@dataclass
class _FitState:
    anchor: pd.Timestamp
    order: tuple[int, int, int]
    arima_params: np.ndarray
    arima_aic: float
    ets_params: np.ndarray | None  # None when the ETS fit failed at this anchor
    n_obs: int
    aic_table: dict[str, float] = field(default_factory=dict)


class ArimaEtsForecaster:
    """ARIMA(p,1,q) chosen by AIC + ETS(A,Ad,N), averaged; Gaussian quantiles widened by WIDEN."""

    name = "arima_ets"

    def __init__(
        self,
        window: int = 1000,
        orders: tuple[tuple[int, int, int], ...] = DEFAULT_ORDERS,
        widen: float = WIDEN,
        min_obs: int = 250,
    ):
        self.window = window
        self.orders = orders
        self.widen = widen
        self.min_obs = min_obs
        self._states: dict[pd.Timestamp, _FitState] = {}

    # ------------------------------------------------------------------------------------------------------
    def _log_price(self, pit: pd.DataFrame, upto: pd.Timestamp | None = None) -> np.ndarray:
        px = price_series(pit)
        if upto is not None:
            px = px.loc[:upto]
        lp = np.log(px.to_numpy(dtype=float))
        if len(lp) < self.min_obs:
            raise NotFitted(f"storico insufficiente per ARIMA/ETS ({len(lp)} < {self.min_obs} osservazioni)")
        return lp[-self.window :]

    def _fit_at(self, pit: pd.DataFrame, anchor: pd.Timestamp) -> _FitState:
        from statsmodels.tsa.arima.model import ARIMA
        from statsmodels.tsa.exponential_smoothing.ets import ETSModel

        lp = self._log_price(pit, upto=anchor)
        best: tuple[float, tuple[int, int, int], np.ndarray] | None = None
        aic_table: dict[str, float] = {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for order in self.orders:
                try:
                    res = ARIMA(lp, order=order, trend="n").fit()
                    aic = float(res.aic)
                except Exception as e:  # a failed candidate is simply skipped
                    log.debug("ARIMA%s failed at %s: %s", order, anchor.date(), e)
                    continue
                if not math.isfinite(aic):
                    continue
                aic_table[str(order)] = aic
                if best is None or aic < best[0]:
                    best = (aic, order, np.asarray(res.params, dtype=float))
            if best is None:
                # ARIMA(0,1,0) with no parameters other than sigma2 always fits: it is the random walk.
                res = ARIMA(lp, order=(0, 1, 0), trend="n").fit()
                best = (float(res.aic), (0, 1, 0), np.asarray(res.params, dtype=float))
            ets_params: np.ndarray | None
            try:
                ets = ETSModel(pd.Series(lp), error="add", trend="add", damped_trend=True).fit(disp=False)
                ets_params = np.asarray(ets.params, dtype=float)
                if not np.all(np.isfinite(ets_params)):
                    ets_params = None
            except Exception as e:
                log.warning("ETS fit failed at %s: %s", anchor.date(), e)
                ets_params = None
        return _FitState(anchor, best[1], best[2], best[0], ets_params, len(lp), aic_table)

    def _state_for(self, pit: pd.DataFrame, asof: datetime) -> _FitState:
        anchor = refit_anchor(pit.index, asof, "M")
        st = self._states.get(anchor)
        if st is None:
            st = self._fit_at(pit, anchor)
            self._states[anchor] = st
        return st

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        self._state_for(slice_asof(features, asof), asof)

    # ------------------------------------------------------------------------------------------------------
    def _forecast_paths(self, lp: np.ndarray, st: _FitState) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(mu_arima, var_arima, mu_ets, var_ets) of the log price for steps 1..MAX_HORIZON at fixed params."""
        from statsmodels.tsa.arima.model import ARIMA
        from statsmodels.tsa.exponential_smoothing.ets import ETSModel

        steps = MAX_HORIZON
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ares = ARIMA(lp, order=st.order, trend="n").filter(st.arima_params)
            afc = ares.get_forecast(steps)
            mu_a = np.asarray(afc.predicted_mean, dtype=float)
            var_a = np.asarray(afc.var_pred_mean, dtype=float)
            if st.ets_params is None:
                return mu_a, var_a, mu_a, var_a
            n = len(lp)
            eres = ETSModel(pd.Series(lp), error="add", trend="add", damped_trend=True).smooth(st.ets_params)
            pred = eres.get_prediction(start=n, end=n + steps - 1)
            mu_e = np.asarray(pred.predicted_mean, dtype=float)
            var_e = np.asarray(pred.var_pred_mean, dtype=float)
        if not (np.all(np.isfinite(mu_e)) and np.all(np.isfinite(var_e))):
            return mu_a, var_a, mu_a, var_a
        return mu_a, var_a, mu_e, var_e

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        pit = slice_asof(features, asof)
        if pit.empty:
            raise NotFitted("nessuna feature disponibile alla data richiesta")
        st = self._state_for(pit, asof)
        lp = self._log_price(pit)
        mu_a, var_a, mu_e, var_e = self._forecast_paths(lp, st)
        lp0 = float(lp[-1])
        ets_used = st.ets_params is not None
        out: dict[str, ForecastQuantiles] = {}
        for hname, h in HORIZONS.items():
            ma, me = float(mu_a[h - 1]) - lp0, float(mu_e[h - 1]) - lp0  # log changes from the last obs
            va, ve = max(float(var_a[h - 1]), 1e-12), max(float(var_e[h - 1]), 1e-12)
            mu = 0.5 * (ma + me)
            var = 0.5 * (va + ve) + 0.5 * ((ma - mu) ** 2 + (me - mu) ** 2)  # exact two-component mixture variance
            sd = math.sqrt(var) * self.widen
            center = math.log(price) + mu
            qs = {a: math.exp(center + sd * normal_quantile(a)) for a in (0.05, 0.25, 0.5, 0.75, 0.95)}
            p_up = 0.5 * (1.0 + math.erf((mu / sd) / math.sqrt(2.0)))
            expected_vol = sd / math.sqrt(h / TRADING_DAYS)
            p, _, q = st.order
            drivers = [
                f"ARIMA({p},1,{q}) su log-prezzo: variazione attesa {ma * 100:+.2f}% a {h}g",
                (
                    f"ETS trend smorzato: variazione attesa {me * 100:+.2f}% a {h}g"
                    if ets_used
                    else "ETS non stimabile a questo rifit: usato solo ARIMA"
                ),
                f"Banda gaussiana allargata ×{self.widen:.1f} (incertezza parametri)",
            ]
            meta: dict[str, Any] = {
                "anchor": st.anchor.date().isoformat(),
                "order": list(st.order),
                "aic": st.arima_aic,
                "ets_used": ets_used,
                "mu_arima": ma,
                "mu_ets": me,
                "sd_h": sd,
                "widen": self.widen,
                "n_obs_fit": st.n_obs,
            }
            out[hname] = make_forecast(hname, asof, price, qs, p_up, expected_vol, drivers, self.name, False, meta)
        return out
