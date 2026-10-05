"""HAR-RV forecaster (brief §11: "HAR-RV se ci sono dati intraday").

Realised variance per London trading day is computed from intraday (1h) bars of the front future: the sum of
squared log returns between consecutive bar closes, INCLUDING the overnight/weekend gap between the last bar of a
day and the first bar of the next (so RV approximates the total close-to-close variance, which is what the price
distribution needs). A day whose bar count is below 60% of the typical count is dropped as incomplete; the last
day is kept and scaled by typical_count/count when it is at least 60% complete (e.g. a forecast at the 19:30
London settlement while bars run to 23:00).

HAR (Corsi 2009) in logs: log(mean RV over the next h days) = b0 + b1 log RV_d + b2 log RV_w + b3 log RV_m, with
RV_w/RV_m the 5- and 22-day means. One OLS per horizon, estimated at `asof` on all the (t, t+h) pairs whose
target window is fully realised at asof (embargo of h days). The forecast daily variance is the lognormal mean
exp(x'b + s^2/2). The h-day return distribution is a unit-variance Student-t(5) with that variance, no drift
(p_up = 0.5): like GARCH, this is a volatility model, not a directional one.

With no intraday data (neither the constructor argument nor `features.attrs['intraday']`) `fit`/`predict`
raise NotFitted and the ensemble skips the model; a HAR-RV without intraday data would be pretending.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.core.timeutil import LONDON, ensure_utc
from engine.forecast.base import (
    HORIZONS,
    TRADING_DAYS,
    ForecastQuantiles,
    NotFitted,
    asof_date,
    horizon_sigma,
    make_forecast,
    quantiles_from_sigma,
    slice_asof,
)

RV_FLOOR = 1e-10
MIN_BARS_PER_DAY = 3
COMPLETE_FRACTION = 0.6
# A last day holding at least this share of a typical session is treated as complete: the bars still missing
# are the thin late-session hours, and scaling them up would add more noise than it removes. Below this share
# the day is materially truncated (a forecast run mid-session) and is scaled to a full-day equivalent.
SCALE_FRACTION = 0.8


@dataclass
class _HorizonFit:
    beta: np.ndarray  # (4,) intercept, daily, weekly, monthly
    resid_var: float
    n_obs: int


@dataclass
class _HarState:
    asof: pd.Timestamp
    last_rv_day: pd.Timestamp
    x_now: np.ndarray  # (4,) regressors at the last complete day
    rv_now: tuple[float, float, float]  # daily, weekly, monthly RV (variance per day)
    fits: dict[str, _HorizonFit]
    n_days: int
    last_day_scaled: bool


class HarRvForecaster:
    """HAR-RV on 1h bars; raises NotFitted without intraday data."""

    name = "har_rv"

    def __init__(self, intraday: pd.DataFrame | None = None, min_days: int = 120, df: float = 5.0):
        self.intraday = intraday
        self.min_days = min_days
        self.df = df
        self._states: dict[pd.Timestamp, _HarState] = {}

    # ------------------------------------------------------------------------------------------------------
    def _bars(self, features: pd.DataFrame) -> pd.DataFrame:
        src: Any = self.intraday if self.intraday is not None else features.attrs.get("intraday")
        if not isinstance(src, pd.DataFrame) or src.empty:
            raise NotFitted("HAR-RV: nessun dato intraday disponibile")
        return src

    @staticmethod
    def _close_column(bars: pd.DataFrame) -> str:
        lower = {str(c).lower(): str(c) for c in bars.columns}
        for key in ("close", "brent_front_close", "settle", "price"):
            if key in lower:
                return lower[key]
        num = bars.select_dtypes(include="number").columns
        if len(num) == 0:
            raise NotFitted("HAR-RV: nessuna colonna di prezzo nelle barre intraday")
        return str(num[0])

    def daily_rv(self, bars: pd.DataFrame, asof: datetime) -> tuple[pd.Series, bool]:
        """Realised variance per London trading day from bars with timestamp <= asof. (series, last_day_scaled)."""
        col = self._close_column(bars)
        idx = pd.DatetimeIndex(bars.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        asof_ts = pd.Timestamp(ensure_utc(asof))
        close = pd.to_numeric(bars[col], errors="coerce").to_numpy(dtype=float)
        order = np.argsort(idx.to_numpy(), kind="stable")
        idx, close = idx[order], close[order]
        keep = (idx <= asof_ts) & np.isfinite(close) & (close > 0)
        idx, close = idx[keep], close[keep]
        if len(close) < 2:
            raise NotFitted("HAR-RV: barre intraday insufficienti")
        lr = np.diff(np.log(close))
        days = idx[1:].tz_convert(LONDON).tz_localize(None).normalize()
        frame = pd.DataFrame({"r2": lr * lr}, index=days)
        grouped = frame.groupby(level=0)["r2"]
        rv = grouped.sum()
        counts = grouped.size()
        typical = float(counts.median())
        ok = counts >= max(MIN_BARS_PER_DAY, math.ceil(COMPLETE_FRACTION * typical))
        scaled = False
        if len(counts) and bool(ok.iloc[-1]):
            last_n = float(counts.iloc[-1])
            if last_n < SCALE_FRACTION * typical:
                # materially truncated last day: scale up assuming constant intraday variance
                rv.iloc[-1] = float(rv.iloc[-1]) * typical / last_n
                scaled = True
        rv = rv.loc[ok]
        return rv.astype(float), scaled

    @staticmethod
    def _regressors(rv: pd.Series) -> pd.DataFrame:
        lrv = np.log(rv.clip(lower=RV_FLOOR))
        d = lrv
        w = np.log(rv.rolling(5, min_periods=5).mean().clip(lower=RV_FLOOR))
        m = np.log(rv.rolling(22, min_periods=22).mean().clip(lower=RV_FLOOR))
        return pd.DataFrame({"d": d, "w": w, "m": m}, index=rv.index)

    def _fit_state(self, bars: pd.DataFrame, asof: datetime) -> _HarState:
        rv, scaled = self.daily_rv(bars, asof)
        if len(rv) < self.min_days:
            raise NotFitted(f"HAR-RV: solo {len(rv)} giorni di varianza realizzata (< {self.min_days})")
        x = self._regressors(rv)
        rv_np = rv.to_numpy(dtype=float)
        fits: dict[str, _HorizonFit] = {}
        for hname, h in HORIZONS.items():
            # target: log mean RV over (t+1 .. t+h); known at asof only when t+h <= last complete day (embargo)
            fwd = np.array([rv_np[t + 1 : t + 1 + h].mean() for t in range(len(rv_np) - h)])
            y = np.log(np.maximum(fwd, RV_FLOOR))
            xt = x.iloc[: len(rv_np) - h]
            mask = xt.notna().all(axis=1).to_numpy()
            if int(mask.sum()) < max(30, 4 * 5):
                raise NotFitted("HAR-RV: osservazioni insufficienti per la regressione")
            a = np.column_stack([np.ones(int(mask.sum())), xt.to_numpy(dtype=float)[mask]])
            beta, *_ = np.linalg.lstsq(a, y[mask], rcond=None)
            resid = y[mask] - a @ beta
            dof = max(len(resid) - a.shape[1], 1)
            fits[hname] = _HorizonFit(beta, float(resid @ resid) / dof, int(mask.sum()))
        last = x.iloc[-1]
        if last.isna().any():
            raise NotFitted("HAR-RV: regressori non disponibili all'ultimo giorno")
        x_now = np.array([1.0, float(last["d"]), float(last["w"]), float(last["m"])])
        rv_now = (math.exp(float(last["d"])), math.exp(float(last["w"])), math.exp(float(last["m"])))
        return _HarState(asof_date(asof), pd.Timestamp(rv.index[-1]), x_now, rv_now, fits, len(rv), scaled)

    def _state_for(self, features: pd.DataFrame, asof: datetime) -> _HarState:
        key = asof_date(asof)
        st = self._states.get(key)
        if st is None:
            st = self._fit_state(self._bars(features), asof)
            self._states[key] = st
        return st

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        self._state_for(features, asof)

    # ------------------------------------------------------------------------------------------------------
    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        pit = slice_asof(features, asof)
        if pit.empty:
            raise NotFitted("nessuna feature disponibile alla data richiesta")
        st = self._state_for(features, asof)
        rv_d, rv_w, rv_m = (math.sqrt(v * TRADING_DAYS) for v in st.rv_now)
        out: dict[str, ForecastQuantiles] = {}
        for hname, h in HORIZONS.items():
            f = st.fits[hname]
            log_rv = float(st.x_now @ f.beta) + 0.5 * f.resid_var  # lognormal mean correction
            rv_h = math.exp(log_rv)  # forecast mean daily variance over the next h days
            ann_vol = math.sqrt(rv_h * TRADING_DAYS)
            sigma = horizon_sigma(ann_vol, h)
            qs = quantiles_from_sigma(math.log(price), sigma, df=self.df)
            qs[0.5] = float(price)
            drivers = [
                f"HAR-RV su barre 1h: RV giornaliera {rv_d * 100:.0f}%, settimanale {rv_w * 100:.0f}%, "
                f"mensile {rv_m * 100:.0f}% (annue)",
                f"Vol attesa a {h}g: {ann_vol * 100:.0f}% annua",
                "Nessuna deriva: p_up = 0,5 (modello di volatilità)",
            ]
            meta: dict[str, Any] = {
                "beta": [float(b) for b in f.beta],
                "resid_var": f.resid_var,
                "n_obs_fit": f.n_obs,
                "n_days": st.n_days,
                "last_rv_day": st.last_rv_day.date().isoformat(),
                "last_day_scaled": st.last_day_scaled,
                "sigma_h": sigma,
                "t_df": self.df,
            }
            out[hname] = make_forecast(hname, asof, price, qs, 0.5, ann_vol, drivers, self.name, False, meta)
        return out
