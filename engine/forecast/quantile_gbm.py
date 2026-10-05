"""LightGBM quantile regression on the feature catalog (brief §11: "gradient boosting quantilico").

For each horizon h one booster per quantile level (0.05, 0.25, 0.5, 0.75, 0.95) predicts the h-day log return
of the continuous price from the catalog features at t.

Walk-forward protocol
  * refit at quarterly anchors (first trading day of Jan/Apr/Jul/Oct, ~63 trading days apart) on an EXPANDING
    window of rows <= anchor; fitted boosters are cached by (anchor, horizon) so a single predict only trains
    when a new anchor is reached;
  * embargo: at the anchor the labels of the last h rows are not yet realised, so those rows are excluded from
    training (rows i with i + h <= anchor only);
  * at least `min_train_rows` (1500) labelled rows are required, otherwise NotFitted (the ensemble skips it);
  * raw price/stock LEVEL columns are excluded (non-stationary; a tree cannot extrapolate them), regime label
    strings too; everything else numeric in the catalog is a candidate feature, NaN handled natively;
  * monotone fix: predicted quantiles are sorted; p_up is read from the quantile curve at zero return;
  * expected_vol is implied by the quantile spreads; drivers are the top-3 features by total gain (Italian labels).

Runtime: a fresh predict on 5000 rows trains 20 boosters (4 horizons x 5 quantiles, 200 rounds, 15 leaves) in
a few seconds (tests/test_forecast_models.py asserts < 60 s).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.features import catalog as cat
from engine.forecast.base import (
    HORIZONS,
    QUANTILE_LEVELS,
    ForecastQuantiles,
    NotFitted,
    implied_vol_from_quantiles,
    make_forecast,
    p_up_from_quantiles,
    refit_anchor,
    slice_asof,
    sort_quantiles,
)

log = logging.getLogger(__name__)

# Non-stationary level columns excluded from the design matrix (returns/spreads/z-scores stay in).
EXCLUDED_COLUMNS: frozenset[str] = frozenset(
    {
        cat.PX,
        cat.PX_FRONT,
        cat.SPOT,
        cat.M1,
        cat.M2,
        cat.M3,
        cat.M6,
        cat.M12,
        cat.CRUDE_STOCKS,
        cat.CUSHING_STOCKS,
        cat.COT_MM_NET_BRENT,
        cat.RIGS,
        cat.GPR,
        cat.DXY,
        cat.REGIME_LABEL,
        cat.NEXT_EVENT_ID,
        cat.REGIME_ID,
    }
)

# Short Italian labels for the dashboard "driver" chips (catalog constant -> label).
FEATURE_LABELS_IT: dict[str, str] = {
    cat.RET_1: "Rendimento 1g",
    cat.RET_5: "Rendimento 5g",
    cat.RET_21: "Rendimento 1m",
    cat.RET_63: "Rendimento 3m",
    cat.RET_126: "Rendimento 6m",
    cat.RET_252: "Rendimento 12m",
    cat.GAP_1: "Gap apertura",
    cat.RV_YZ_10: "Vol realizzata 10g",
    cat.RV_YZ_21: "Vol realizzata 21g",
    cat.RV_YZ_63: "Vol realizzata 63g",
    cat.RV_CC_21: "Vol close-close 21g",
    cat.OVX: "OVX",
    cat.VRP: "Premio al rischio di varianza",
    cat.VOL_OF_VOL: "Vol della vol",
    cat.VOL_PCTL_1Y: "Percentile vol 1a",
    cat.GARCH_VOL: "Vol GARCH",
    cat.HURST_100: "Hurst 100g",
    cat.VR_5: "Variance ratio 5",
    cat.VR_20: "Variance ratio 20",
    cat.TSMOM_10: "Momentum 10g",
    cat.TSMOM_21: "Momentum 1m",
    cat.TSMOM_63: "Momentum 3m",
    cat.TSMOM_126: "Momentum 6m",
    cat.TSMOM_252: "Momentum 12m",
    cat.EMA_FAST_SLOW: "EMA 20/100",
    cat.ATR_14: "ATR 14",
    cat.DONCHIAN_POS_20: "Canale Donchian 20",
    cat.DONCHIAN_POS_55: "Canale Donchian 55",
    cat.SLOPE_M1_M2: "Pendenza M1-M2",
    cat.SLOPE_M1_M3: "Pendenza M1-M3",
    cat.SLOPE_M1_M6: "Pendenza M1-M6",
    cat.SLOPE_M1_M12: "Pendenza M1-M12",
    cat.ROLL_YIELD_ANN: "Roll yield",
    cat.ROLL_YIELD_PCTL: "Percentile roll yield",
    cat.BUTTERFLY_1_3_6: "Butterfly 1-3-6",
    cat.DEC_DEC_SPREAD: "Spread Dic-Dic",
    cat.CURVE_APPROX: "Curva proxy",
    cat.SPOT_FRONT_PREMIUM: "Premio Dated/futures",
    cat.SPREAD_CHG_5: "Variazione spread 5g",
    cat.BRENT_WTI: "Brent-WTI",
    cat.BRENT_WTI_Z: "Brent-WTI (z)",
    cat.CRACK_321: "Crack 3-2-1",
    cat.CRACK_321_Z: "Crack 3-2-1 (z)",
    cat.DIESEL_CRACK: "Crack diesel",
    cat.GASOLINE_CRACK: "Crack benzina",
    cat.PRODUCT_LEAD: "Anticipo prodotti",
    cat.DXY_RET_21: "Dollaro 1m",
    cat.VIX: "VIX",
    cat.SPX_RET_21: "S&P 500 1m",
    cat.COPPER_RET_21: "Rame 1m",
    cat.US10Y: "Treasury 10a",
    cat.BREAKEVEN: "Breakeven 10a",
    cat.MACRO_FV_RESID: "Residuo fair value macro",
    cat.MACRO_FV_Z: "Fair value macro (z)",
    cat.CRUDE_STOCKS_VS_5Y: "Scorte vs 5 anni",
    cat.CRUDE_STOCKS_5Y_RANGE_POS: "Scorte nel range 5a",
    cat.CUSHING_VS_5Y: "Cushing vs 5 anni",
    cat.STOCK_SURPRISE: "Sorpresa scorte",
    cat.STOCK_SURPRISE_Z: "Sorpresa scorte (z)",
    cat.CUSHING_SURPRISE_Z: "Sorpresa Cushing (z)",
    cat.IMPLIED_DEMAND_Z: "Domanda implicita (z)",
    cat.REFINERY_INPUTS_Z: "Raffinazione (z)",
    cat.DAYS_SINCE_WPSR: "Giorni dall'EIA",
    cat.RIGS_CHG_13W: "Trivelle 13 sett.",
    cat.COT_MM_NET_BRENT_PCTL: "Posizioni speculative Brent",
    cat.COT_MM_NET_WTI_PCTL: "Posizioni speculative WTI",
    cat.COT_MM_NET_CHG_4W: "Variazione COT 4 sett.",
    cat.COT_CROWDING: "Affollamento COT",
    cat.GPR_Z: "Rischio geopolitico (z)",
    cat.GPR_THREAT_Z: "Minacce geopolitiche (z)",
    cat.GEO_INDEX: "Indice geopolitico",
    cat.GEO_SPIKE: "Picco geopolitico",
    cat.GDELT_TONE: "Tono notizie",
    cat.GDELT_VOLUME_Z: "Volume notizie (z)",
    cat.DEESCALATION_FLAG: "Segnale de-escalation",
    cat.HOURS_TO_EVENT: "Ore al prossimo evento",
    cat.DAYS_TO_EXPIRY: "Giorni alla scadenza",
    cat.IS_PRE_WEEKEND: "Pre-weekend",
    cat.MONTH: "Mese",
    cat.DOY_SIN: "Stagionalità (sin)",
    cat.DOY_COS: "Stagionalità (cos)",
    cat.HURRICANE_SEASON: "Stagione uragani",
    cat.REGIME_CONF: "Confidenza regime",
    cat.BOCPD_CP_PROB: "Prob. cambio di regime",
}


def feature_label_it(name: str) -> str:
    if name in FEATURE_LABELS_IT:
        return FEATURE_LABELS_IT[name]
    if name.startswith(cat.REGIME_P_PREFIX):
        return f"Prob. regime {name[len(cat.REGIME_P_PREFIX) :]}"
    return name


DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "quantile",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "deterministic": True,
    "force_col_wise": True,
}


@dataclass
class _HorizonFit:
    anchor: pd.Timestamp
    horizon: str
    columns: list[str]
    boosters: dict[float, Any]  # alpha -> lightgbm.Booster
    gain: dict[str, float]  # feature -> share of total gain (sums to 1)
    n_train: int


class QuantileGbmForecaster:
    """LightGBM quantile regression, quarterly walk-forward refit with embargo, monotone-sorted quantiles."""

    name = "lgbm_quantile"

    def __init__(
        self,
        min_train_rows: int = 1500,
        num_boost_round: int = 200,
        params: dict[str, Any] | None = None,
        feature_columns: list[str] | None = None,
        num_threads: int = 4,
        seed: int = 42,
    ):
        self.min_train_rows = min_train_rows
        self.num_boost_round = num_boost_round
        self.params = {**DEFAULT_PARAMS, **(params or {}), "num_threads": num_threads}
        self.feature_columns = feature_columns
        self.seed = seed
        self._fits: dict[tuple[pd.Timestamp, str], _HorizonFit] = {}

    # ------------------------------------------------------------------------------------------------------
    def design(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Numeric candidate features (level columns and string columns excluded)."""
        if self.feature_columns is not None:
            cols = [c for c in self.feature_columns if c in frame.columns]
        else:
            num = frame.select_dtypes(include=["number", "bool"]).columns
            cols = [str(c) for c in num if str(c) not in EXCLUDED_COLUMNS]
        return frame[cols].astype(float)

    @staticmethod
    def _log_price(frame: pd.DataFrame) -> pd.Series:
        col = cat.PX if cat.PX in frame.columns else cat.PX_FRONT
        if col not in frame.columns:
            raise NotFitted("prezzo continuo non disponibile nelle feature")
        px = pd.to_numeric(frame[col], errors="coerce").astype(float)
        return pd.Series(np.log(px.where(px > 0).to_numpy(dtype=float)), index=px.index)

    def _fit_at(self, pit: pd.DataFrame, anchor: pd.Timestamp, hname: str) -> _HorizonFit:
        import lightgbm as lgb

        h = HORIZONS[hname]
        sub = pit.loc[:anchor]
        lp = self._log_price(sub)
        valid = lp.notna().to_numpy()
        sub, lp = sub.loc[valid], lp.loc[valid]
        n = len(sub)
        if n - h < self.min_train_rows:
            raise NotFitted(
                f"LightGBM {hname}: solo {max(n - h, 0)} righe etichettate al rifit {anchor.date()} "
                f"(< {self.min_train_rows})"
            )
        x = self.design(sub)
        x = x.loc[:, x.notna().any(axis=0)]  # drop columns that are entirely NaN in the window
        if x.shape[1] == 0:
            raise NotFitted("LightGBM: nessuna feature numerica disponibile")
        lp_np = lp.to_numpy(dtype=float)
        y = lp_np[h:] - lp_np[:-h]  # h-day forward log return, known only for rows i with i + h <= anchor
        x_train = x.iloc[: n - h].to_numpy(dtype=float)  # embargo: the last h rows have unrealised labels
        cols = [str(c) for c in x.columns]
        boosters: dict[float, Any] = {}
        gain_tot = np.zeros(len(cols))
        for k, alpha in enumerate(QUANTILE_LEVELS):
            params = {**self.params, "alpha": alpha, "seed": self.seed + k}
            ds = lgb.Dataset(x_train, label=y, feature_name=cols, free_raw_data=True)
            booster = lgb.train(params, ds, num_boost_round=self.num_boost_round)
            boosters[alpha] = booster
            gain_tot += np.asarray(booster.feature_importance(importance_type="gain"), dtype=float)
        total = float(gain_tot.sum())
        gain = {c: (float(g) / total if total > 0 else 0.0) for c, g in zip(cols, gain_tot)}
        return _HorizonFit(anchor, hname, cols, boosters, gain, len(y))

    def _fit_for(self, pit: pd.DataFrame, asof: datetime, hname: str) -> _HorizonFit:
        anchor = refit_anchor(pit.index, asof, "Q")
        key = (anchor, hname)
        fit = self._fits.get(key)
        if fit is None:
            fit = self._fit_at(pit, anchor, hname)
            self._fits[key] = fit
        return fit

    def fit(self, features: pd.DataFrame, asof: datetime) -> None:
        pit = slice_asof(features, asof)
        for hname in HORIZONS:
            self._fit_for(pit, asof, hname)

    # ------------------------------------------------------------------------------------------------------
    def top_drivers(self, fit: _HorizonFit, n: int = 3) -> list[tuple[str, float]]:
        return sorted(fit.gain.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def predict(
        self, features: pd.DataFrame, asof: datetime, price: float, curve: pd.Series | None = None
    ) -> dict[str, ForecastQuantiles]:
        pit = slice_asof(features, asof)
        if pit.empty:
            raise NotFitted("nessuna feature disponibile alla data richiesta")
        design_now = self.design(pit)
        out: dict[str, ForecastQuantiles] = {}
        for hname, h in HORIZONS.items():
            fit = self._fit_for(pit, asof, hname)
            row = design_now.reindex(columns=fit.columns).iloc[[-1]].to_numpy(dtype=float)
            raw = {a: float(b.predict(row)[0]) for a, b in fit.boosters.items()}
            qr = sort_quantiles(raw, scale=0.01)
            qs = {a: float(price * math.exp(v)) for a, v in qr.items()}
            p_up = p_up_from_quantiles(qs, price)
            expected_vol = implied_vol_from_quantiles(qs, h)
            top = self.top_drivers(fit)
            drivers = [f"{feature_label_it(c)} ({share * 100:.0f}% del guadagno)" for c, share in top]
            drivers.append(f"Rendimento mediano atteso a {h}g: {qr[0.5] * 100:+.1f}%")
            meta: dict[str, Any] = {
                "anchor": fit.anchor.date().isoformat(),
                "n_train": fit.n_train,
                "n_features": len(fit.columns),
                "raw_quantiles_logret": {str(a): v for a, v in raw.items()},
                "monotone_fixed": any(raw[a] != qr[a] for a in raw),
                "top_features": [c for c, _ in top],
                "num_boost_round": self.num_boost_round,
            }
            out[hname] = make_forecast(hname, asof, price, qs, p_up, expected_vol, drivers, self.name, False, meta)
        return out
