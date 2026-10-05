"""risk.json: real numbers where they exist, a stated reason where they do not.

Synthetic equity and price series (labelled as such) exercise the assembly logic; the real-data check lives in
tests/test_fixture_market_data.py and in the end-to-end run.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.core.config import RiskConfig
from engine.core.store import dumps
from engine.monitoring.risk_report import build_risk_payload, write_risk_file

RISK = RiskConfig.load()


def _equity_rows(n: int, seed: int = 5) -> list[dict]:
    """SYNTHETIC equity snapshots in the shape the broker writes."""
    rng = np.random.default_rng(seed)
    start = datetime(2026, 1, 2, 18, 30, tzinfo=UTC)
    equity = 10_000.0
    rows = []
    for i in range(n):
        equity *= float(1 + rng.normal(0.0004, 0.012))
        rows.append(
            {
                "ts": (start + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                "account_id": "master",
                "epoch": 1,
                "equity": equity,
                "leverage": float(abs(rng.normal(0.6, 0.2))),
                "margin_used": 900.0,
                "margin_level": 11.0,
                "gross_notional": 9000.0,
                "drawdown": 0.0,
                "daily_pnl": 0.0,
                "status": "active",
            }
        )
    return rows


def _prices(n: int = 1200, seed: int = 3) -> pd.Series:
    """SYNTHETIC daily Brent-like prices."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=n)
    return pd.Series(80.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.02, size=n))), index=idx)


def test_var_from_the_account_history(tmp_store):
    for row in _equity_rows(200):
        tmp_store.append_jsonl("equity", row, ts=datetime.fromisoformat(row["ts"].replace("Z", "+00:00")))
    payload = build_risk_payload(tmp_store, RISK, prices=None, run_monte_carlo=False, run_stress=False)
    assert payload["var"] and payload["es"]
    # ES is a worse loss than VaR at the same level, and 99% is worse than 95%
    assert payload["es"]["99% 1g"] >= payload["var"]["99% 1g"] > 0
    assert payload["var"]["99% 1g"] >= payload["var"]["95% 1g"]
    assert "storico del conto" in payload["var_source"]
    assert payload["leverage_stats"]["days_above_1x"] >= 0
    assert payload["leverage_stats"]["cap"] == RISK.max_leverage
    assert payload["margin"]["rate"] == RISK.margin_rate


def test_no_history_says_so_instead_of_guessing(tmp_store):
    payload = build_risk_payload(tmp_store, RISK, prices=None, run_monte_carlo=False, run_stress=False)
    assert payload["var"] is None and payload["es"] is None
    assert any("VaR storico non calcolabile" in m for m in payload["unavailable"])
    assert any("Storico della leva" in m for m in payload["unavailable"])


def test_parametric_fallback_uses_the_volatility_of_the_last_decision(tmp_store):
    ts = datetime(2026, 10, 5, 18, 30, tzinfo=UTC)
    tmp_store.append_jsonl("equity", _equity_rows(3)[0], ts=ts)
    tmp_store.append_jsonl("decisions", {"ts": ts.isoformat(), "vol_used": 0.45}, ts=ts)
    payload = build_risk_payload(tmp_store, RISK, prices=None, run_monte_carlo=False, run_stress=False)
    assert payload["var"] and "parametrica" in payload["var_source"]
    assert payload["var"]["99% 1g"] > payload["var"]["95% 1g"] > 0


def test_stress_and_ruin_are_assembled_and_labelled(tmp_store):
    for row in _equity_rows(60):
        tmp_store.append_jsonl("equity", row, ts=datetime.fromisoformat(row["ts"].replace("Z", "+00:00")))
    payload = build_risk_payload(tmp_store, RISK, prices=_prices(), run_monte_carlo=True, run_stress=True)
    ruin = payload["ruin"]
    assert 0.0 <= ruin["prob_ruin"] <= 1.0
    assert ruin["max_ruin_probability"] == pytest.approx(0.05)
    assert isinstance(ruin["within_budget"], bool)
    stress = payload["stress"]
    assert stress["episodes"] and stress["scenarios"]
    # every synthetic scenario must say it is synthetic
    assert all(s.get("synthetic") for s in stress["scenarios"])
    assert any("sintetico" in str(s.get("name", "")).lower() for s in stress["scenarios"])


def test_written_file_is_strict_json(tmp_store):
    for row in _equity_rows(40):
        tmp_store.append_jsonl("equity", row, ts=datetime.fromisoformat(row["ts"].replace("Z", "+00:00")))
    write_risk_file(tmp_store, RISK, prices=_prices(400), run_monte_carlo=False, run_stress=True)
    text = tmp_store.path("risk.json").read_text()
    assert "NaN" not in text and "Infinity" not in text
    doc = json.loads(text)
    assert doc["available"] is True and doc["generated_at"]
    # and it survives the store's own strict serialiser
    dumps(doc)


def test_every_number_is_finite_or_none(tmp_store):
    for row in _equity_rows(80):
        tmp_store.append_jsonl("equity", row, ts=datetime.fromisoformat(row["ts"].replace("Z", "+00:00")))
    payload = build_risk_payload(tmp_store, RISK, prices=_prices(600), run_monte_carlo=True, run_stress=True)

    def walk(node):
        if isinstance(node, float):
            assert math.isfinite(node), "a non-finite number reached risk.json"
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
