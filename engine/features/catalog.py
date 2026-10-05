"""Feature catalogue: the single source of truth for feature column names used by regime, strategies, forecasts.

Every feature at date t uses only data published on or before the ICE settlement of t (point-in-time).
Strategies MUST reference features through these constants, never through ad-hoc strings.
"""

from __future__ import annotations

# --- prices & returns -------------------------------------------------------
PX = "px"  # Brent continuous roll-adjusted close (signals); never used as P&L
PX_FRONT = "px_front"  # Brent front settlement (P&L reference)
SPOT = "spot"  # EIA Brent spot (Dated proxy)
RET_1 = "ret_1"  # log return 1d of PX
RET_5 = "ret_5"
RET_21 = "ret_21"
RET_63 = "ret_63"
RET_126 = "ret_126"
RET_252 = "ret_252"
GAP_1 = "gap_1"  # open-to-previous-close log gap
# --- volatility ----------------------------------------------------------
RV_YZ_10 = "rv_yz_10"  # Yang-Zhang realized vol, annualised, 10d window
RV_YZ_21 = "rv_yz_21"
RV_YZ_63 = "rv_yz_63"
RV_CC_21 = "rv_cc_21"  # close-to-close realized vol 21d
OVX = "ovx"  # implied vol 30d (percent, annualised) - NaN before 2007
VRP = "vrp"  # OVX/100 - RV_YZ_21 (variance risk premium proxy)
VOL_OF_VOL = "vol_of_vol"  # 21d std of daily OVX changes
VOL_PCTL_1Y = "vol_pctl_1y"  # percentile rank of RV_YZ_21 over trailing 252d
GARCH_VOL = "garch_vol"  # one-step-ahead GARCH(1,1)-t forecast, annualised (walk-forward)
# --- trend & persistence ----------------------------------------------------
HURST_100 = "hurst_100"  # Hurst exponent (R/S or DFA) 100d
VR_5 = "vr_5"  # Lo-MacKinlay variance ratio q=5
VR_20 = "vr_20"
TSMOM_10 = "tsmom_10"  # sign-vol-scaled trend over 10d: ret/vol
TSMOM_21 = "tsmom_21"
TSMOM_63 = "tsmom_63"
TSMOM_126 = "tsmom_126"
TSMOM_252 = "tsmom_252"
EMA_FAST_SLOW = "ema_fast_slow"  # (EMA20 - EMA100)/PX
ATR_14 = "atr_14"  # fraction of price
DONCHIAN_POS_20 = "donchian_pos_20"  # position in 20d channel [0,1]
DONCHIAN_POS_55 = "donchian_pos_55"
# --- term structure ----------------------------------------------------------
M1 = "m1"
M2 = "m2"
M3 = "m3"
M6 = "m6"
M12 = "m12"
SLOPE_M1_M2 = "slope_m1_m2"  # (M1-M2)/M1, >0 backwardation
SLOPE_M1_M3 = "slope_m1_m3"
SLOPE_M1_M6 = "slope_m1_m6"
SLOPE_M1_M12 = "slope_m1_m12"
ROLL_YIELD_ANN = "roll_yield_ann"  # annualised (M1/M2 - 1) * 12/months_between
ROLL_YIELD_PCTL = "roll_yield_pctl"  # 5y percentile
BUTTERFLY_1_3_6 = "butterfly_1_3_6"  # M1 - 2*M3 + M6, as fraction of M1
DEC_DEC_SPREAD = "dec_dec_spread"  # nearest Dec minus following Dec, fraction
CURVE_APPROX = "curve_approx"  # 1 where the curve is a proxy (WTI C1-C4), 0 where real Brent contracts
SPOT_FRONT_PREMIUM = "spot_front_premium"  # (spot - M1)/M1: Dated vs futures proxy
SPREAD_CHG_5 = "spread_chg_5"  # 5d change of SLOPE_M1_M6 (physical confirmation for S7)
# --- inter-market ----------------------------------------------------------------
BRENT_WTI = "brent_wti"  # USD/bbl
BRENT_WTI_Z = "brent_wti_z"  # z-score of residual from rolling cointegration
CRACK_321 = "crack_321"  # USD/bbl, 3-2-1 with RBOB/HO vs WTI
CRACK_321_Z = "crack_321_z"
DIESEL_CRACK = "diesel_crack"  # HO*42 - Brent, USD/bbl
GASOLINE_CRACK = "gasoline_crack"
PRODUCT_LEAD = "product_lead"  # 5d product return minus 5d crude return
# --- macro ---------------------------------------------------------------------------
DXY = "dxy"
DXY_RET_21 = "dxy_ret_21"
VIX = "vix"
SPX_RET_21 = "spx_ret_21"
COPPER_RET_21 = "copper_ret_21"
US10Y = "us10y"
BREAKEVEN = "breakeven10y"
MACRO_FV_RESID = "macro_fv_resid"  # Kalman time-varying-beta fair value residual (fraction)
MACRO_FV_Z = "macro_fv_z"
# --- fundamentals (point-in-time: as of release) -----------------------------------------
CRUDE_STOCKS = "crude_stocks"
CRUDE_STOCKS_VS_5Y = "crude_stocks_vs_5y"  # deviation from 5y seasonal mean, fraction
CRUDE_STOCKS_5Y_RANGE_POS = "crude_stocks_5y_range_pos"  # position within 5y seasonal range [0,1]
CUSHING_STOCKS = "cushing_stocks"
CUSHING_VS_5Y = "cushing_vs_5y"
STOCK_SURPRISE = "stock_surprise"  # actual weekly change minus seasonal-model expectation (kbbl)
STOCK_SURPRISE_Z = "stock_surprise_z"
CUSHING_SURPRISE_Z = "cushing_surprise_z"
IMPLIED_DEMAND_Z = "implied_demand_z"
REFINERY_INPUTS_Z = "refinery_inputs_z"
DAYS_SINCE_WPSR = "days_since_wpsr"
RIGS = "rigs"
RIGS_CHG_13W = "rigs_chg_13w"
# --- positioning ---------------------------------------------------------------------------
COT_MM_NET_BRENT = "cot_mm_net_brent"  # contracts
COT_MM_NET_BRENT_PCTL = "cot_mm_net_brent_pctl"  # 3y percentile
COT_MM_NET_WTI_PCTL = "cot_mm_net_wti_pctl"
COT_MM_NET_CHG_4W = "cot_mm_net_chg_4w"
COT_CROWDING = "cot_crowding"  # |pctl - 0.5|*2 in [0,1]
# --- news / geopolitics ----------------------------------------------------------------------
GPR = "gpr"
GPR_Z = "gpr_z"  # z vs trailing 1y
GPR_THREAT_Z = "gpr_threat_z"
GEO_INDEX = "geo_index"  # blended geopolitical intensity in [0,1] (GPR + GDELT when available)
GEO_SPIKE = "geo_spike"  # 1 if GEO_INDEX jumped > threshold vs 10d mean
GDELT_TONE = "gdelt_tone"  # NaN before go-live
GDELT_VOLUME_Z = "gdelt_volume_z"
DEESCALATION_FLAG = "deescalation_flag"  # 1 when tone improves sharply and volume stays high
# --- insider (SEC Form 4, oil complex) ---------------------------------------------------------
INSIDER_SCORE = "insider_score"  # net open-market conviction, [-1, +1], approx (filing deadline inferred)
INSIDER_BUY_RATIO = "insider_buy_ratio"  # purchases / (purchases + sales) by count, [0, 1]
INSIDER_N_TX = "insider_n_tx"  # qualifying transactions in the trailing window
INSIDER_BREADTH = "insider_breadth"  # distinct tickers with at least one purchase in the window
# --- calendar / events -------------------------------------------------------------------------
HOURS_TO_EVENT = "hours_to_event"  # hours to next binary event (EIA, OPEC+, FOMC)
NEXT_EVENT_ID = "next_event_id"
DAYS_TO_EXPIRY = "days_to_expiry"  # front contract
IS_PRE_WEEKEND = "is_pre_weekend"
MONTH = "month"
DOY_SIN = "doy_sin"
DOY_COS = "doy_cos"
HURRICANE_SEASON = "hurricane_season"
# --- regime (filled by regime module) ----------------------------------------------------------------
REGIME_ID = "regime_id"
REGIME_LABEL = "regime_label"
REGIME_CONF = "regime_conf"
REGIME_P_PREFIX = "regime_p_"  # regime_p_0 .. regime_p_k
BOCPD_CP_PROB = "bocpd_cp_prob"  # change-point probability

ALL_NUMERIC_FEATURES: list[str] = [
    v
    for k, v in dict(globals()).items()
    if k.isupper() and isinstance(v, str) and v not in {REGIME_LABEL, NEXT_EVENT_ID, REGIME_P_PREFIX}
]
