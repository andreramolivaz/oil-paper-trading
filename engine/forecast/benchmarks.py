"""Benchmark forecasters (brief §11): the random walk and the futures curve, the two bars every model must beat.

RandomWalkForecaster
    median = current price; no drift; p_up = 0.5. Quantiles from a conservative annualised vol
    max(RV_YZ_21, GARCH_VOL, OVX/100) using a unit-variance Student-t(4) scaled by sqrt(h/252).
FuturesCurveForecaster
    median = the market's own forecast: the futures curve interpolated (linearly in expiry time) at the horizon
    date, with contract expiries from engine.core.calendar. Quantiles around it with the same conservative vol.
    approx=True when the curve is a WTI proxy (CURVE_APPROX feature or curve.attrs['approx']); when the curve is
    missing it falls back to the random walk and says so in `drivers`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from engine.core.calendar import add_business_days, contract_code, expiry_for, listed_months, parse_contract_code
from engine.features import catalog as cat
from engine.forecast.base import (
    HORIZONS,
    ForecastQuantiles,
    NotFitted,
    asof_date,
    conservative_vol,
    horizon_sigma,
    make_forecast,
    quantiles_from_sigma,
    slice_asof,
)

T_DF = 4.0  # Student-t degrees of freedom for the benchmark bands (fat tails; variance exists)


def _vol_driver(vol: float, source: str) -> str:
    return f"Volatilità prudenziale {vol * 100:.0f}% annua ({source})"


class RandomWalkForecaster:
    """Median = price; symmetric fat-tailed bands from a conservative vol; p_up = 0.5."""

    name = "random_walk"

    def __init__(self, df: float = T_DF):
        self.df = df

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        # Nothing to estimate: the random walk has no parameters. Validate that a vol is available.
        conservative_vol(slice_asof(features, asof))

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        pit = slice_asof(features, asof)
        if pit.empty:
            raise NotFitted("nessuna feature disponibile alla data richiesta")
        vol, vol_src = conservative_vol(pit)
        drivers = ["Random walk: mediana = prezzo attuale, nessuna deriva", _vol_driver(vol, vol_src)]
        out: dict[str, ForecastQuantiles] = {}
        for hname, h in HORIZONS.items():
            sigma = horizon_sigma(vol, h)
            qs = quantiles_from_sigma(math.log(price), sigma, df=self.df)
            qs[0.5] = float(price)  # exact: the median of the RW is the current price
            out[hname] = make_forecast(
                hname,
                asof,
                price,
                qs,
                p_up=0.5,
                expected_vol=vol,
                drivers=drivers,
                model=self.name,
                approx=False,
                meta={"vol_source": vol_src, "sigma_h": sigma, "t_df": self.df},
            )
        return out


@dataclass
class _CurvePoint:
    rank: int
    code: str
    expiry: date
    days_to_expiry: int
    price: float


class FuturesCurveForecaster:
    """Median = futures curve interpolated at the horizon date (the market's own forecast)."""

    name = "futures_curve"

    def __init__(self, root: str = "BZ", df: float = T_DF, max_rank: int = 36):
        self.root = root
        self.df = df
        self.max_rank = max_rank
        self._rw = RandomWalkForecaster(df=df)

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        conservative_vol(slice_asof(features, asof))

    # ------------------------------------------------------------------------------------------------------
    def curve_points(self, curve: pd.Series | None, asof: datetime) -> list[_CurvePoint]:
        """Valid (rank, code, expiry, price) points of the curve at asof, ordered by expiry."""
        if curve is None or len(curve) == 0:
            return []
        d0 = asof_date(asof).date()
        code = curve.get("M1_code") if "M1_code" in curve.index else None
        months: list[tuple[int, int]]
        if isinstance(code, str) and len(code) >= 4:
            try:
                root, y, m = parse_contract_code(code)
            except (KeyError, ValueError):
                root = self.root
                y, m = listed_months(self.root, d0, 1)[0]
            months = []
            yy, mm = y, m
            for _ in range(self.max_rank):
                months.append((yy, mm))
                yy, mm = (yy + 1, 1) if mm == 12 else (yy, mm + 1)
        else:
            root = self.root
            months = listed_months(self.root, d0, self.max_rank)
        pts: list[_CurvePoint] = []
        for k, (y, m) in enumerate(months, start=1):
            key = f"M{k}"
            if key not in curve.index:
                continue
            try:
                p = float(curve[key])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(p) or p <= 0:
                continue
            exp = expiry_for(root, y, m)
            dte = (exp - d0).days
            if dte < 0:
                continue
            pts.append(_CurvePoint(k, contract_code(root, y, m), exp, dte, p))
        pts.sort(key=lambda x: x.days_to_expiry)
        return pts

    @staticmethod
    def _is_proxy(pit: pd.DataFrame, curve: pd.Series | None) -> bool:
        if curve is not None and bool(curve.attrs.get("approx", False)):
            return True
        if cat.CURVE_APPROX in pit.columns and not pit.empty:
            v = pit[cat.CURVE_APPROX].iloc[-1]
            try:
                return bool(float(v) >= 0.5)
            except (TypeError, ValueError):
                return False
        return False

    def horizon_date(self, asof: datetime, horizon_days: int) -> date:
        return add_business_days(asof_date(asof).date(), horizon_days, "ICE")

    # ------------------------------------------------------------------------------------------------------
    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        pit = slice_asof(features, asof)
        if pit.empty:
            raise NotFitted("nessuna feature disponibile alla data richiesta")
        vol, vol_src = conservative_vol(pit)
        pts = self.curve_points(curve, asof)
        if len(pts) < 2:
            # Honest fallback: without a curve the market forecast is the random walk. Labelled approx.
            rw = self._rw.predict(pit, asof, price, None)
            out: dict[str, ForecastQuantiles] = {}
            for hname, fq in rw.items():
                fq.model = self.name
                fq.approx = True
                fq.drivers = ["Curva futures non disponibile: ripiego sul random walk (≈)", _vol_driver(vol, vol_src)]
                fq.meta = {**fq.meta, "curve_fallback": "random_walk", "curve_points": len(pts)}
                out[hname] = fq
            return out
        approx = self._is_proxy(pit, curve)
        xs = np.asarray([p.days_to_expiry for p in pts], dtype=float)
        ys = np.asarray([p.price for p in pts], dtype=float)
        front, back = pts[0], pts[-1]
        slope = (front.price - back.price) / front.price if front.price > 0 else 0.0
        shape = "backwardation" if slope > 0 else "contango"
        d0 = asof_date(asof).date()
        out = {}
        for hname, h in HORIZONS.items():
            target = self.horizon_date(asof, h)
            x = float((target - d0).days)
            median = float(np.interp(x, xs, ys))  # flat beyond the last listed contract
            sigma = horizon_sigma(vol, h)
            lm = math.log(median)
            qs = quantiles_from_sigma(lm, sigma, df=self.df)
            qs[0.5] = median
            # P(price_T > price_now) under a unit-variance t centred on the curve median
            z = (math.log(price) - lm) / sigma * math.sqrt(self.df / (self.df - 2.0))
            p_up = float(1.0 - stats.t.cdf(z, self.df))
            near = min(pts, key=lambda p: abs(p.days_to_expiry - x))
            drivers = [
                f"Curva futures ({shape}): {near.code} a {near.price:.2f} $ → mediana {median:.2f} $",
                f"Pendenza M1→M{back.rank}: {slope * 100:+.1f}%",
                _vol_driver(vol, vol_src),
            ]
            if approx:
                drivers.insert(1, "Curva ≈ proxy WTI: forma del Brent approssimata")
            meta: dict[str, Any] = {
                "vol_source": vol_src,
                "sigma_h": sigma,
                "t_df": self.df,
                "target_date": target.isoformat(),
                "nearest_contract": near.code,
                "curve_points": len(pts),
                "slope_front_back": slope,
            }
            out[hname] = make_forecast(
                hname, asof, price, qs, p_up, vol, drivers[:4], model=self.name, approx=approx, meta=meta
            )
        return out
