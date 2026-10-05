"""`FullRegimeModel`: walk-forward HMM + readable labels + BOCPD, implementing `RegimeModel` (brief §7).

Pipeline for every trading date t, using rows <= t only:

    features -> WalkForwardHmm (causal forward filter)      -> posterior per canonical state
             -> labels.label_states(standardised state means) -> posterior per readable label
             -> bocpd.change_point_probability                -> P(structural break)

The label probabilities are the state posteriors summed over the states that share a label. The reported
regime is the most likely label; the reported `regime_id` is the canonical state id that carries most of
that label's probability, so the id is stable across refits (see `hmm.WalkForwardHmm._relabel`).

Transition rule (brief §7, "se nessun regime supera la soglia di confidenza... transizione"):
`confidence < min_confidence` OR `change_point_prob > cp_threshold` => `regime_id = -1` and
`LABEL_TRANSITION`, while `probabilities` still carries the underlying label distribution so the dashboard
can show what the model was hesitating between.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.core.timeutil import ensure_utc, london_date
from engine.features import catalog as cat
from engine.regime.base import LABEL_TRANSITION, RegimeState
from engine.regime.bocpd import (
    DEFAULT_HAZARD,
    DEFAULT_RUN_LENGTH_MAX,
    DEFAULT_SHORT_RUN,
    change_point_probability,
)
from engine.regime.hmm import (
    DEFAULT_MIN_OBS,
    DEFAULT_N_ITER,
    DEFAULT_N_STATES,
    DEFAULT_RANDOM_STATE,
    DEFAULT_REFIT_EVERY,
    HmmFit,
    WalkForwardHmm,
)
from engine.regime.labels import PROB_COLUMNS, STATE_LABELS, label_states, prob_column

log = logging.getLogger(__name__)

_PICKLE_VERSION = 1
DEFAULT_MIN_CONFIDENCE = 0.55
DEFAULT_CP_THRESHOLD = 0.5
HISTORY_COLUMNS: list[str] = [cat.REGIME_ID, cat.REGIME_LABEL, cat.REGIME_CONF, *PROB_COLUMNS, cat.BOCPD_CP_PROB]


@dataclass
class _Details:
    """Per-row internals kept beside `history()` (model string, fit date, warm-up flag)."""

    history: pd.DataFrame
    model: pd.Series
    fit_date: pd.Series
    warmup: pd.Series
    approx: pd.Series  # per row: the active fit had no curve slope, so the label is a best effort
    features_used: list[str]
    slope_missing: bool


class FullRegimeModel:
    """Walk-forward regime model. `fit` is the weekly refit; `infer` is point-in-time at `ts`."""

    name = "hmm-wf"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.min_confidence = float(cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE))
        self.cp_threshold = float(cfg.get("cp_threshold", DEFAULT_CP_THRESHOLD))
        self.hazard = float(cfg.get("hazard", DEFAULT_HAZARD))
        self.run_length_max = int(cfg.get("run_length_max", DEFAULT_RUN_LENGTH_MAX))
        self.short_run = int(cfg.get("short_run", DEFAULT_SHORT_RUN))
        self.use_vol_channel = bool(cfg.get("use_vol_channel", True))
        self.config: dict[str, Any] = cfg
        self.hmm = WalkForwardHmm(
            min_obs=int(cfg.get("min_obs", DEFAULT_MIN_OBS)),
            refit_every=int(cfg.get("refit_every", DEFAULT_REFIT_EVERY)),
            n_states_range=tuple(cfg.get("n_states_range", DEFAULT_N_STATES)),
            random_state=int(cfg.get("random_state", DEFAULT_RANDOM_STATE)),
            n_iter=int(cfg.get("n_iter", DEFAULT_N_ITER)),
            tol=float(cfg.get("tol", 1e-3)),
        )
        self._cache_key: str | None = None
        self._details: _Details | None = None

    # ---- RegimeModel protocol ----------------------------------------------
    def fit(self, features: pd.DataFrame) -> None:
        """Estimate the walk-forward model on `features` (idempotent, cached by content digest)."""
        self._details_for(features)

    def history(self, features: pd.DataFrame) -> pd.DataFrame:
        """Walk-forward per-row regime frame (see HISTORY_COLUMNS)."""
        return self._details_for(features).history.copy()

    def infer(self, features: pd.DataFrame, ts: datetime) -> RegimeState:
        """Point-in-time regime at `ts`: the last trading date of the frame at or before `ts`."""
        det = self._details_for(features)
        day = pd.Timestamp(london_date(ensure_utc(ts)))
        idx = det.history.index
        usable = idx[idx <= day]
        if len(usable) == 0:
            log.warning("regime: no feature row at or before %s; returning a transition state", day.date())
            return RegimeState(
                ts=ensure_utc(ts),
                regime_id=-1,
                label=LABEL_TRANSITION,
                confidence=0.0,
                probabilities={},
                change_point_prob=0.0,
                features_used=list(det.features_used),
                model=self.name,
                approx=det.slope_missing,
            )
        at = usable[-1]
        row = det.history.loc[at]
        probs = {lab: float(row[prob_column(lab)]) for lab in STATE_LABELS}
        total = sum(probs.values())
        label = str(row[cat.REGIME_LABEL])
        conf = float(row[cat.REGIME_CONF])
        cp = float(row[cat.BOCPD_CP_PROB])
        regime_id = int(row[cat.REGIME_ID])
        if total <= 0.0:
            probs = {}
        return RegimeState(
            ts=ensure_utc(ts),
            regime_id=regime_id,
            label=label,
            confidence=conf,
            probabilities=probs,
            change_point_prob=cp,
            features_used=list(det.features_used),
            model=str(det.model.loc[at]),
            approx=bool(det.approx.loc[at]),
        )

    # ---- persistence --------------------------------------------------------
    def save(self, path: str | Path) -> Path:
        """Pickle the FITTED PARAMETERS only (no market data, no feature frame).

        The weekly job writes this cache so the live runner can `load` it and infer without re-estimating
        20+ HMMs. The payload is a dict of config values plus `HmmFit` records (training moments, start
        probabilities, transition matrix, means, diagonal covariances). It is engine-internal and versioned
        by `_PICKLE_VERSION`; a stale or unreadable cache must be regenerated, never patched.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _PICKLE_VERSION,
            "config": self.config,
            "fits": list(self.hmm.fit_cache.items()),
        }
        with p.open("wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        return p

    def save_file(self, path: str | Path) -> Path:
        """Alias of `save` under the name the live runner duck-types (`engine/live/jobs.py`)."""
        return self.save(path)

    def load_file(self, path: str | Path) -> None:
        """Merge a cache written by `save` into THIS model, keeping this model's own configuration.

        The live runner builds the model from the current config and then warms it with the weekly cache,
        so only the fitted parameters travel; a cache entry is keyed by (fit position, training-data
        digest) and is therefore ignored automatically when the data behind it has changed.
        """
        other = type(self).load(path)
        self.hmm.fit_cache.update(other.hmm.fit_cache)

    @classmethod
    def load(cls, path: str | Path) -> FullRegimeModel:
        """Rebuild a model from `save`; falls back to an unfitted model when the cache is unusable."""
        p = Path(path)
        with p.open("rb") as f:
            payload = pickle.load(f)
        if not isinstance(payload, dict) or payload.get("version") != _PICKLE_VERSION:
            raise ValueError(f"{p} is not a regime cache of version {_PICKLE_VERSION}")
        model = cls(payload.get("config") or {})
        for key, fit in payload.get("fits") or []:
            if isinstance(fit, HmmFit):
                model.hmm.fit_cache[tuple(key)] = fit
        return model

    # ---- internals ----------------------------------------------------------
    @staticmethod
    def _digest(features: pd.DataFrame) -> str:
        h = hashlib.sha1(usedforsecurity=False)
        h.update(np.asarray(features.index, dtype="datetime64[ns]").view("int64").tobytes())
        for col in sorted(features.columns):
            s = features[col]
            if s.dtype.kind not in "fiub":
                continue
            h.update(col.encode())
            h.update(np.ascontiguousarray(s.to_numpy(dtype="float64", na_value=np.nan)).tobytes())
        return h.hexdigest()

    def _details_for(self, features: pd.DataFrame) -> _Details:
        key = self._digest(features)
        if self._details is not None and self._cache_key == key:
            return self._details
        det = self._build(features)
        self._details = det
        self._cache_key = key
        return det

    def _build(self, features: pd.DataFrame) -> _Details:
        res = self.hmm.fit(features)
        idx = features.index
        cp = change_point_probability(
            features,
            hazard=self.hazard,
            run_length_max=self.run_length_max,
            short_run=self.short_run,
            use_vol_channel=self.use_vol_channel,
        ).reindex(idx)

        n = len(idx)
        n_labels = len(STATE_LABELS)
        probs = np.zeros((n, n_labels), dtype="float64")
        best_id = np.full((n, n_labels), -1, dtype="int64")
        model_str = pd.Series("", index=idx, dtype="object")
        approx = pd.Series(bool(res.slope_missing), index=idx, dtype="bool")

        post = res.posteriors
        cols = list(post.columns)
        col_of: dict[Any, int] = {cid: j for j, cid in enumerate(cols)}
        vals = post.to_numpy(dtype="float64") if cols else np.zeros((n, 0), dtype="float64")

        for fit in res.fits:
            rows = (res.fit_date == fit.fit_date).to_numpy(dtype=bool)
            if not rows.any():
                continue
            fit_labels = label_states(fit.means, fit.feature_names)
            model_str[rows] = f"hmm{fit.n_states}-wf-{fit.fit_date.date().isoformat()}"
            approx[rows] = bool(fit.slope_missing)
            for li, lab in enumerate(STATE_LABELS):
                cids = [cid for cid, lb in zip(fit.canonical_ids, fit_labels) if lb == lab]
                if not cids:
                    continue
                jj = [col_of[c] for c in cids]
                sub = vals[np.ix_(np.flatnonzero(rows), jj)]
                probs[rows, li] = sub.sum(axis=1)
                best_id[rows, li] = np.asarray(cids, dtype="int64")[sub.argmax(axis=1)]

        has_fit = probs.sum(axis=1) > 0.0
        win = probs.argmax(axis=1)
        rows_ix = np.arange(n)
        conf = np.where(has_fit, probs[rows_ix, win], 0.0)
        regime_id = np.where(has_fit, best_id[rows_ix, win], -1)
        label = np.array([STATE_LABELS[w] for w in win], dtype=object)
        label = np.where(has_fit, label, LABEL_TRANSITION)

        cp_vals = cp.to_numpy(dtype="float64")
        cp_vals = np.where(np.isfinite(cp_vals), cp_vals, 0.0)
        transition = (~has_fit) | (conf < self.min_confidence) | (cp_vals > self.cp_threshold)
        regime_id = np.where(transition, -1, regime_id)
        label = np.where(transition, LABEL_TRANSITION, label)

        hist = pd.DataFrame(index=idx)
        hist[cat.REGIME_ID] = regime_id.astype("int64")
        hist[cat.REGIME_LABEL] = label
        hist[cat.REGIME_CONF] = conf
        for li, lab in enumerate(STATE_LABELS):
            hist[prob_column(lab)] = probs[:, li]
        hist[cat.BOCPD_CP_PROB] = cp_vals
        hist = hist[HISTORY_COLUMNS]
        model_str[model_str == ""] = self.name
        return _Details(
            history=hist,
            model=model_str,
            fit_date=res.fit_date,
            warmup=res.warmup,
            approx=approx,
            features_used=list(res.features_used),
            slope_missing=bool(res.slope_missing),
        )
