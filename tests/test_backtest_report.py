"""Backtest report (markdown, Italian) and validation.json contract.

All market data here is SYNTHETIC (tests/synthetic.py) and the strategy is the toy one from tests/test_session.py:
the point is the report's plumbing — sections, contract keys, strict JSON, lifecycle decisions and zero weights —
not any market claim.
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.core.config import RiskConfig
from engine.core.store import StateStore
from engine.portfolio.base import CappedEqualWeightAllocator
from engine.report.backtest_report import run_backtest_report, run_validation
from tests.synthetic import make_market_data
from tests.test_session import MiniBuilder, StubRegime, ToyLong

# docs/SITE_DATA.md: validation.json = "Report backtest OOS: per strategia Sharpe/DSR/PBO/costi x2/per
# regime/crisi; lifecycle; strategie a peso zero e perché"
CONTRACT_KEYS = {
    "generated_at",
    "source",
    "asof",
    "window",
    "master",
    "strategies",
    "zero_weight",
    "pbo",
    "trials",
    "leverage",
    "stress",
    "thresholds",
    "skipped",
    "disclaimer",
}
STRATEGY_KEYS = {
    "id",
    "name",
    "family",
    "lifecycle",
    "explanation",
    "weight",
    "metrics",
    "costs",
    "dsr",
    "pbo",
    "by_regime",
    "by_crisis",
    "years",
    "n_obs",
}
SECTIONS = [
    "# Report di backtest",
    "## Metriche per strategia",
    "## Risultati per regime",
    "## Risultati per crisi",
    "## Sensibilità ai costi",
    "## DSR e PBO",
    "## Ciclo di vita",
    "## Istogramma della leva",
    "## Stress test",
]


def _strict(text: str) -> dict:
    """json.loads that REJECTS NaN/Infinity: the dashboard contract must be strict JSON."""

    def boom(token: str) -> float:
        raise AssertionError(f"token JSON non valido nel contratto: {token}")

    return json.loads(text, parse_constant=boom)


@pytest.fixture(scope="module")
def report(tmp_path_factory):  # noqa: ANN001, ANN201
    """One backtest report shared by the read-only tests (the event loop is the slow part)."""
    return _run(tmp_path_factory.mktemp("report"))


def _run(tmp_path, n_days: int = 300, strategies=None, md=None):  # noqa: ANN001, ANN201
    risk = RiskConfig.load()
    md = md if md is not None else make_market_data(n_days=n_days, vol=0.012, drift=0.0006)
    store = StateStore(tmp_path / "state")
    payload = run_backtest_report(
        md,
        [ToyLong()] if strategies is None else strategies,
        risk,
        out_dir=tmp_path / "out",
        store=store,
        allocator=CappedEqualWeightAllocator(),
        feature_builder=MiniBuilder(),
        regime_model=StubRegime(),
    )
    return payload, store, tmp_path / "out"


def test_markdown_report_has_the_italian_sections(report):  # noqa: ANN001
    payload, _store, out = report
    md_path = out / "backtest.md"
    assert md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    for heading in SECTIONS:
        assert heading in text, f"sezione mancante: {heading}"
    assert "Simulazione a scopo di studio" in text  # the disclaimer the brief requires
    assert "Toy momentum" in text
    assert payload["paths"]["markdown"] == str(md_path)
    # Italian number formatting: decimal comma, "n/d" for what is missing (never a raw NaN token)
    assert "n/d" in text
    assert "NaN" not in text and "Infinity" not in text
    assert "0," in text or "1," in text


def test_validation_json_matches_the_contract_and_is_strict_json(report):  # noqa: ANN001
    payload, store, out = report
    raw = (out / "validation.json").read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    data = _strict(raw)
    assert set(data) >= CONTRACT_KEYS
    assert data["source"] == "backtest"
    assert data["window"]["start"] and data["window"]["end"] and data["window"]["n_obs"] > 0
    assert data["master"]["metrics"]["sharpe"] is not None
    for s in data["strategies"]:
        assert set(s) >= STRATEGY_KEYS
    # the same payload is written to the state directory for the dashboard
    assert store.exists("validation.json")
    from_state = store.read_json("validation.json")
    assert from_state["generated_at"] == data["generated_at"]
    # strict JSON all the way down: no non-finite float anywhere in the tree
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, float):
            assert np.isfinite(node), node


def test_every_strategy_has_a_lifecycle_and_an_italian_explanation(report):  # noqa: ANN001
    payload, _store, _out = report
    assert payload["strategies"], "nessuna strategia nel report"
    for s in payload["strategies"]:
        assert s["lifecycle"] in {"research", "incubation", "active", "retired"}
        assert isinstance(s["explanation"], str) and s["explanation"]
        assert s["lifecycle_record"]["pbo"] == payload["master"]["pbo"]
        # the per-regime and per-crisis slices exist (crises are declared unavailable, never invented)
        assert isinstance(s["by_regime"], dict) and s["by_regime"]
        assert isinstance(s["by_crisis"], dict) and s["by_crisis"]
        for crisis in s["by_crisis"].values():
            assert crisis["available"] or crisis["reason"]


def test_a_strategy_failing_the_thresholds_gets_weight_zero_with_a_reason(report):  # noqa: ANN001
    payload, _store, _out = report
    toy = payload["strategies"][0]
    # the toy strategy cannot pass: one year of history, no DSR above 0.95, no PBO over a single variant
    assert toy["lifecycle"] != "active"
    assert toy["weight"] == 0.0
    zero = {z["id"]: z["reason"] for z in payload["zero_weight"]}
    assert toy["id"] in zero
    assert zero[toy["id"]].startswith("Peso zero:")
    assert zero[toy["id"]] == toy["explanation"]
    assert sum(s["weight"] for s in payload["strategies"]) == pytest.approx(0.0)


def test_costs_dsr_and_leverage_blocks_are_filled(report):  # noqa: ANN001
    payload, _store, _out = report
    master = payload["master"]
    costs = master["costs"]
    assert costs["available"] is True
    assert costs["x1"]["sharpe"] is not None and costs["x2"]["sharpe"] is not None
    # doubling the costs cannot improve the Sharpe
    assert costs["x2"]["sharpe"] <= costs["x1"]["sharpe"] + 1e-12
    assert costs["labels"]["x2"] == "costi raddoppiati (2x)"
    assert payload["trials"]["available"] is True and payload["trials"]["n_trials"] >= 2
    assert master["dsr"]["dsr_prob"] is not None
    lev = payload["leverage"]
    assert lev["available"] is True and lev["n_obs"] > 0
    assert lev["max"] <= 10.0 + 1e-9  # the hard cap of the brief, visible in the report
    assert sum(b["count"] for b in lev["bins"]) == lev["n_obs"]
    # the stress block is present and declares what it could not do
    assert payload["stress"] is not None
    assert payload["stress"]["n_unavailable"] >= 1
    assert all(s["synthetic"] for s in payload["stress"]["scenarios"])


def test_pbo_is_computed_when_variants_are_supplied(tmp_path):
    risk = RiskConfig.load()
    md = make_market_data(n_days=300, vol=0.012, drift=0.0006)
    rng = np.random.default_rng(4)
    # SYNTHETIC variants: four alternative return streams with the same length as the backtest
    variants = pd.DataFrame({f"v{i}": rng.normal(0.0002, 0.01, 260) for i in range(4)})
    payload = run_backtest_report(
        md,
        [ToyLong()],
        risk,
        out_dir=tmp_path / "out",
        store=StateStore(tmp_path / "state"),
        allocator=CappedEqualWeightAllocator(),
        feature_builder=MiniBuilder(),
        regime_model=StubRegime(),
        variants=variants,
    )
    pbo = payload["pbo"]
    assert pbo is not None
    assert 0.0 <= pbo["pbo"] <= 1.0
    assert pbo["n_variants"] == 4 and pbo["source"] == "variants forniti"
    # every variant was registered as a trial before anything was deflated
    assert payload["trials"]["n_trials"] >= 6
    assert payload["strategies"][0]["lifecycle_record"]["pbo"] == pbo["pbo"]
    text = (tmp_path / "out" / "backtest.md").read_text(encoding="utf-8")
    assert "PBO (CSCV" in text


def test_tolerates_an_empty_strategy_list_and_nan_columns(tmp_path):
    md = make_market_data(n_days=120)
    md.prices["ovx"] = np.nan  # a partly empty MarketData must not break the report
    md.prices["brent_spot"] = np.nan
    payload, _store, out = _run(tmp_path, strategies=[], md=md)
    assert payload["strategies"] == [] and payload["zero_weight"] == []
    assert (out / "backtest.md").exists()
    data = _strict((out / "validation.json").read_text(encoding="utf-8"))
    assert set(data) >= CONTRACT_KEYS
    assert any("PBO" in s for s in data["skipped"])


def test_tolerates_market_data_without_usable_prices(tmp_path):
    md = make_market_data(n_days=60)
    md.prices["brent_front_close"] = np.nan  # nothing tradeable at all
    payload, _store, out = _run(tmp_path, md=md)
    assert payload["window"]["n_obs"] == 0
    assert any("Backtest senza giorni validi" in s for s in payload["skipped"])
    assert (out / "backtest.md").exists()
    _strict((out / "validation.json").read_text(encoding="utf-8"))


def test_run_validation_restricts_the_window(tmp_path):
    risk = RiskConfig.load()
    md = make_market_data(n_days=600, start=date(2023, 1, 2), vol=0.012)
    payload = run_validation(
        md,
        [ToyLong()],
        risk,
        out_dir=tmp_path / "out",
        years=1.0,
        store=StateStore(tmp_path / "state"),
        allocator=CappedEqualWeightAllocator(),
        feature_builder=MiniBuilder(),
        regime_model=StubRegime(),
    )
    start = pd.Timestamp(payload["window"]["start"])
    end = pd.Timestamp(payload["window"]["end"])
    assert (end - start).days <= 370
    assert payload["window"]["n_obs"] < 300
    assert (tmp_path / "out" / "backtest.md").exists()
