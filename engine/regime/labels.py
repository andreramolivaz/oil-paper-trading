"""Mapping fitted HMM states to the readable Italian labels of `engine.regime.base`.

The mapping never looks at calendar dates: it only reads the STANDARDISED means of a fitted state
(z-scores inside the training window of that fit, see `engine.regime.hmm`). Two mechanisms are combined:

1. **Precedence rules** (`_RULES`) -- hand-written, documented tests on the standardised means that encode
   the economics of the Brent market. They are checked in order and the first match wins, so an unambiguous
   shock state can never be captured by the generic nearest-centroid step.
2. **Nearest centroid** over `PROTOTYPES` -- a prototype vector per label, expressed in the same z-score
   units. Used when no precedence rule fires. Only the features actually present in the fit are compared
   (euclidean distance on the intersection, normalised by the number of compared features so that fits with
   a different feature count stay comparable).

Two states may receive the same label; `FullRegimeModel` then adds their probabilities.

All thresholds are in standard deviations of the training window, not in physical units, so the mapping is
scale free and survives the regime of 2026 (very high absolute vol) as well as the 2015-2016 glut.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from engine.features import catalog as cat
from engine.regime.base import (
    LABEL_BACKWARDATION_HIGHVOL_GEO,
    LABEL_BULL_SQUEEZE,
    LABEL_CONTANGO_GLUT_DOWNTREND,
    LABEL_LOWVOL_RANGE,
    LABEL_SHOCK_CRASH,
    LABEL_TRANSITION,
)

# Stable ASCII slugs for the Italian labels: used for JSON keys and DataFrame columns, where the human
# readable label (spaces, "+", accents) would be unusable. The slug is derived from the constant NAME,
# not from the Italian text, so translating the UI never renames a column.
LABEL_SLUG: dict[str, str] = {
    LABEL_BACKWARDATION_HIGHVOL_GEO: "backwardation_highvol_geo",
    LABEL_CONTANGO_GLUT_DOWNTREND: "contango_glut_downtrend",
    LABEL_LOWVOL_RANGE: "lowvol_range",
    LABEL_SHOCK_CRASH: "shock_crash",
    LABEL_BULL_SQUEEZE: "bull_squeeze",
    LABEL_TRANSITION: "transition",
}

# Labels an HMM state can carry. LABEL_TRANSITION is NOT one of them: it is produced by the model when no
# label is confident enough or when the change-point detector fires.
STATE_LABELS: list[str] = [
    LABEL_SHOCK_CRASH,
    LABEL_BACKWARDATION_HIGHVOL_GEO,
    LABEL_BULL_SQUEEZE,
    LABEL_CONTANGO_GLUT_DOWNTREND,
    LABEL_LOWVOL_RANGE,
]


def prob_column(label: str) -> str:
    """Column/JSON key holding the probability of `label` (e.g. ``regime_p_shock_crash``)."""
    return f"{cat.REGIME_P_PREFIX}{LABEL_SLUG[label]}"


PROB_COLUMNS: list[str] = [prob_column(x) for x in STATE_LABELS]

# Prototype vectors in z-score units of the training window. Reading of each row:
#   SHOCK/CRASH            : vol explodes, one-month return deeply negative, curve whipsaws into
#                            backwardation, trend broken, geopolitics loud, stocks not yet built.
#   BACKWARDATION+VOL+GEO  : the 2022 and 2026 regime -- high vol, steep backwardation, high geo index,
#                            tight stocks, positive but noisy trend.
#   BULL SQUEEZE           : trend and curve clearly positive, vol only moderate, stocks draining.
#   CONTANGO+GLUT+DOWNTREND: 2015-2016 / 2020 -- negative slope, negative trend, stocks above the 5y mean.
#   LOW-VOL RANGE          : vol well below average, no trend, quiet geopolitics.
PROTOTYPES: dict[str, dict[str, float]] = {
    LABEL_SHOCK_CRASH: {
        cat.RET_21: -2.0,
        cat.RV_YZ_21: 2.2,
        cat.SLOPE_M1_M6: 0.6,
        cat.VRP: -0.8,
        cat.TSMOM_63: -1.6,
        cat.GEO_INDEX: 1.2,
        cat.CRUDE_STOCKS_VS_5Y: -0.2,
    },
    LABEL_BACKWARDATION_HIGHVOL_GEO: {
        cat.RET_21: 0.5,
        cat.RV_YZ_21: 1.2,
        cat.SLOPE_M1_M6: 1.3,
        cat.VRP: 0.1,
        cat.TSMOM_63: 0.8,
        cat.GEO_INDEX: 1.4,
        cat.CRUDE_STOCKS_VS_5Y: -0.9,
    },
    LABEL_BULL_SQUEEZE: {
        cat.RET_21: 1.2,
        cat.RV_YZ_21: 0.1,
        cat.SLOPE_M1_M6: 1.0,
        cat.VRP: 0.3,
        cat.TSMOM_63: 1.4,
        cat.GEO_INDEX: -0.1,
        cat.CRUDE_STOCKS_VS_5Y: -0.8,
    },
    LABEL_CONTANGO_GLUT_DOWNTREND: {
        cat.RET_21: -0.9,
        cat.RV_YZ_21: 0.3,
        cat.SLOPE_M1_M6: -1.3,
        cat.VRP: 0.0,
        cat.TSMOM_63: -1.2,
        cat.GEO_INDEX: -0.3,
        cat.CRUDE_STOCKS_VS_5Y: 1.2,
    },
    LABEL_LOWVOL_RANGE: {
        cat.RET_21: 0.0,
        cat.RV_YZ_21: -0.9,
        cat.SLOPE_M1_M6: 0.0,
        cat.VRP: 0.3,
        cat.TSMOM_63: 0.0,
        cat.GEO_INDEX: -0.6,
        cat.CRUDE_STOCKS_VS_5Y: 0.0,
    },
}

# Neutral value used for a feature the fit does not carry (e.g. the curve slope before real contracts are
# available, VRP before OVX starts in 2007). 0.0 = "average of the training window".
NEUTRAL = 0.0


def _get(z: dict[str, float], name: str) -> float:
    return z.get(name, NEUTRAL)


def _rule_shock(z: dict[str, float]) -> bool:
    """Very high realised vol together with a strongly negative one-month return."""
    return _get(z, cat.RV_YZ_21) >= 1.30 and _get(z, cat.RET_21) <= -0.80


def _rule_backwardation_geo(z: dict[str, float]) -> bool:
    """High vol, positive curve slope and a loud geopolitical index."""
    return _get(z, cat.RV_YZ_21) >= 0.55 and _get(z, cat.SLOPE_M1_M6) >= 0.30 and _get(z, cat.GEO_INDEX) >= 0.55


def _rule_contango_glut(z: dict[str, float]) -> bool:
    """Negative slope (contango), negative trend and stocks above the 5y seasonal mean."""
    return (
        _get(z, cat.SLOPE_M1_M6) <= -0.30 and _get(z, cat.TSMOM_63) <= -0.30 and _get(z, cat.CRUDE_STOCKS_VS_5Y) >= 0.30
    )


def _rule_bull_squeeze(z: dict[str, float]) -> bool:
    """Positive trend and positive slope with vol still moderate."""
    return _get(z, cat.TSMOM_63) >= 0.50 and _get(z, cat.SLOPE_M1_M6) >= 0.20 and _get(z, cat.RV_YZ_21) <= 0.90


def _rule_lowvol_range(z: dict[str, float]) -> bool:
    """Vol below average and no trend to speak of."""
    return _get(z, cat.RV_YZ_21) <= -0.35 and abs(_get(z, cat.TSMOM_63)) <= 0.50


# Order matters: first match wins.
_RULES: list[tuple[str, Callable[[dict[str, float]], bool]]] = [
    (LABEL_SHOCK_CRASH, _rule_shock),
    (LABEL_BACKWARDATION_HIGHVOL_GEO, _rule_backwardation_geo),
    (LABEL_CONTANGO_GLUT_DOWNTREND, _rule_contango_glut),
    (LABEL_BULL_SQUEEZE, _rule_bull_squeeze),
    (LABEL_LOWVOL_RANGE, _rule_lowvol_range),
]


def _nearest_centroid(z: dict[str, float]) -> str:
    """Label whose prototype is closest (mean squared z-distance over the shared features)."""
    best_label = LABEL_LOWVOL_RANGE
    best_dist = float("inf")
    for label in STATE_LABELS:
        proto = PROTOTYPES[label]
        shared = [k for k in proto if k in z]
        if not shared:
            continue
        dist = float(np.mean([(z[k] - proto[k]) ** 2 for k in shared]))
        if dist < best_dist:
            best_dist = dist
            best_label = label
    return best_label


def label_state(mean: npt.NDArray[np.float64], feature_names: list[str]) -> str:
    """Label one fitted state from its standardised mean vector."""
    z = {name: float(mean[i]) for i, name in enumerate(feature_names)}
    for label, rule in _RULES:
        if rule(z):
            return label
    return _nearest_centroid(z)


def label_states(means: npt.NDArray[np.float64], feature_names: list[str]) -> list[str]:
    """Label every fitted state. `means` is (n_states, n_features) in standardised units."""
    arr = np.atleast_2d(np.asarray(means, dtype="float64"))
    if arr.shape[1] != len(feature_names):
        raise ValueError(f"means has {arr.shape[1]} columns but {len(feature_names)} feature names were given")
    return [label_state(arr[i], feature_names) for i in range(arr.shape[0])]
