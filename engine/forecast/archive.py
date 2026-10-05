"""Live forecast archive: append-only record of every forecast and of its realised outcome (brief §11).

Two JSONL logs in the StateStore (monthly rotation by the forecast's asof):
  * `forecasts`          one record per (model, horizon, asof); idempotent via `idempotency_key`
                         = sha1(model, horizon, asof). Re-running the EOD job never duplicates a forecast and
                         never rewrites one: a revised forecast at the same asof is simply ignored.
  * `forecast_outcomes`  one record per resolved forecast (same key), appended once the settlement at the
                         target date (asof trading date + h ICE business days) is known. The original forecast
                         is never touched: the outcome is a new observation.

`track_record()` evaluates ONLY resolved live forecasts, so it is separate from any backtest by construction.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from engine.core.ids import idempotency_key
from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso, now_utc
from engine.forecast.base import HORIZONS, ForecastQuantiles
from engine.forecast.evaluation import (
    FRAME_COLUMNS,
    QUANTILE_FIELDS,
    as_trading_date,
    crps_from_quantiles,
    evaluate_frame,
    normalize_price_index,
    realised_price,
    score_frame,
    target_trading_date,
)

log = logging.getLogger(__name__)

FORECASTS_LOG = "forecasts"
OUTCOMES_LOG = "forecast_outcomes"
KEY_FIELD = "idempotency_key"


def forecast_key(model: str, horizon: str, asof: datetime) -> str:
    """Stable key of a forecast: sha1(model, horizon, asof as UTC ISO)."""
    return idempotency_key(model, horizon, iso(ensure_utc(asof)))


class ForecastArchive:
    def __init__(self, store: StateStore):
        self.store = store

    # ------------------------------------------------------------------ write
    def record(self, forecasts: list[ForecastQuantiles], run_id: str, recorded_at: datetime | None = None) -> int:
        """Append forecasts not yet archived. Returns the number of records written."""
        ts = ensure_utc(recorded_at) if recorded_at else now_utc()
        written = 0
        for f in forecasts:
            if f.horizon not in HORIZONS:
                raise ValueError(f"unknown horizon {f.horizon!r} for model {f.model!r}")
            asof = ensure_utc(f.asof)
            d = f.to_dict()
            d["asof"] = iso(asof)
            d[KEY_FIELD] = forecast_key(f.model, f.horizon, asof)
            d["run_id"] = run_id
            d["recorded_at"] = iso(ts)
            d["asof_date"] = as_trading_date(asof).isoformat()
            d["target_date"] = target_trading_date(asof, f.horizon).isoformat()
            if self.store.append_jsonl_unique(FORECASTS_LOG, d, KEY_FIELD, ts=asof):
                written += 1
        return written

    def resolve(self, prices: pd.Series, tolerance_days: int = 3, resolved_at: datetime | None = None) -> int:
        """Append an outcome for every pending forecast whose target settlement is known in `prices`.

        `prices` = front settlements indexed by trading date (the realised value of every horizon). A forecast
        whose exact target date is missing resolves on the first settlement within `tolerance_days` calendar
        days after it, labelled approx=True; otherwise it stays pending. Returns the number of outcomes written.
        """
        px = normalize_price_index(prices) if len(prices) else pd.Series(dtype=float)
        if px.empty:
            return 0
        ts = ensure_utc(resolved_at) if resolved_at else now_utc()
        done = self.store.jsonl_keys(OUTCOMES_LOG, KEY_FIELD)
        written = 0
        for rec in self.store.iter_jsonl(FORECASTS_LOG):
            key = rec.get(KEY_FIELD)
            if not key or key in done:
                continue
            asof = ensure_utc(datetime.fromisoformat(str(rec["asof"]).replace("Z", "+00:00")))
            target = target_trading_date(asof, str(rec["horizon"]))
            hit = realised_price(px, target, tolerance_days)
            if hit is None:
                continue
            realised, realised_date = hit
            outcome = self._outcome(rec, key, target.isoformat(), realised, realised_date.isoformat(), ts)
            if self.store.append_jsonl_unique(OUTCOMES_LOG, outcome, KEY_FIELD, ts=asof):
                done.add(key)
                written += 1
        return written

    @staticmethod
    def _outcome(
        rec: dict[str, Any], key: str, target_date: str, realised: float, realised_date: str, ts: datetime
    ) -> dict[str, Any]:
        p0, med = float(rec["price_now"]), float(rec["median"])
        q = {a: float(rec[c]) for a, c in QUANTILE_FIELDS.items()}
        p_up = rec.get("p_up")
        pred_up: bool | None
        if p_up is not None and float(p_up) != 0.5:
            pred_up = float(p_up) > 0.5
        elif med != p0:
            pred_up = med > p0
        else:
            pred_up = None
        actual_up: bool | None = None if realised == p0 else realised > p0
        realised_approx = realised_date != target_date
        return {
            KEY_FIELD: key,
            "model": rec["model"],
            "horizon": rec["horizon"],
            "asof": rec["asof"],
            "target_date": target_date,
            "realised_date": realised_date,
            "realised": realised,
            "price_now": p0,
            "median": med,
            "error": med - realised,
            "abs_error": abs(med - realised),
            "rel_error": (med - realised) / p0 if p0 else None,
            "rw_error": p0 - realised,
            "pred_up": pred_up,
            "actual_up": actual_up,
            "hit": None if pred_up is None or actual_up is None else pred_up == actual_up,
            "in_50": q[0.25] <= realised <= q[0.75],
            "in_90": q[0.05] <= realised <= q[0.95],
            "crps": crps_from_quantiles(q, realised),
            "crps_approx": True,
            "approx": bool(rec.get("approx", False)) or realised_approx,
            "realised_approx": realised_approx,
            "resolved_at": iso(ts),
        }

    # ------------------------------------------------------------------ read
    def forecasts(self) -> list[dict[str, Any]]:
        return self.store.read_jsonl(FORECASTS_LOG)

    def outcomes(self) -> list[dict[str, Any]]:
        return self.store.read_jsonl(OUTCOMES_LOG)

    def pending(self) -> list[dict[str, Any]]:
        """Archived forecasts without an outcome yet."""
        done = self.store.jsonl_keys(OUTCOMES_LOG, KEY_FIELD)
        return [r for r in self.forecasts() if r.get(KEY_FIELD) not in done]

    def latest(self, asof: datetime | None = None) -> list[ForecastQuantiles]:
        """Forecasts of the most recent asof (or exactly `asof`), one per model/horizon."""
        recs = self.forecasts()
        if not recs:
            return []
        if asof is None:
            target = max(str(r["asof"]) for r in recs)
        else:
            target = iso(ensure_utc(asof)) or ""
        return [ForecastQuantiles.from_dict(r) for r in recs if str(r["asof"]) == target]

    def frame(self) -> pd.DataFrame:
        """Tidy frame of every archived forecast joined with its outcome (realised NaN while pending), scored."""
        recs = self.forecasts()
        if not recs:
            return pd.DataFrame(columns=FRAME_COLUMNS)
        by_key = {o[KEY_FIELD]: o for o in self.outcomes()}
        rows: list[dict[str, Any]] = []
        for r in recs:
            o = by_key.get(r.get(KEY_FIELD, ""))
            asof = ensure_utc(datetime.fromisoformat(str(r["asof"]).replace("Z", "+00:00")))
            rows.append(
                {
                    "model": r["model"],
                    "horizon": r["horizon"],
                    "asof": asof,
                    "asof_date": pd.Timestamp(r.get("asof_date") or as_trading_date(asof)),
                    "target_date": pd.Timestamp(r.get("target_date") or target_trading_date(asof, r["horizon"])),
                    "realised_date": pd.Timestamp(o["realised_date"]) if o else pd.NaT,
                    "price_now": float(r["price_now"]),
                    "median": float(r["median"]),
                    "q05": float(r["q05"]),
                    "q25": float(r["q25"]),
                    "q75": float(r["q75"]),
                    "q95": float(r["q95"]),
                    "p_up": float(r["p_up"]) if r.get("p_up") is not None else float("nan"),
                    "expected_vol": float(r["expected_vol"]) if r.get("expected_vol") is not None else float("nan"),
                    "approx": bool(r.get("approx", False)),
                    "realised": float(o["realised"]) if o else float("nan"),
                    "realised_approx": bool(o.get("realised_approx", False)) if o else False,
                }
            )
        return score_frame(pd.DataFrame(rows, columns=FRAME_COLUMNS))

    def track_record(self, rw_model: str = "rw") -> dict[str, Any]:
        """Live track record: evaluation report per model/horizon using only resolved live forecasts.

        Returns {"generated_at", "source": "live", "n_forecasts", "n_resolved", "n_pending",
                 "first_asof", "last_asof", "models": {model: {horizon: metrics}}}.
        """
        df = self.frame()
        resolved = df[np.isfinite(df["realised"].to_numpy(dtype=float))] if not df.empty else df
        report = evaluate_frame(df, rw_model=rw_model) if not df.empty else {}
        return {
            "generated_at": iso(datetime.now(tz=UTC)),
            "source": "live",
            "n_forecasts": len(df),
            "n_resolved": len(resolved),
            "n_pending": int(len(df) - len(resolved)),
            "first_asof": iso(df["asof"].min()) if len(df) else None,
            "last_asof": iso(df["asof"].max()) if len(df) else None,
            "models": report,
        }
