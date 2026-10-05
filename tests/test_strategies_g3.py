"""Offline deterministic tests for S7-S9 (physical confirmation, Brent-WTI, crack) and the strategy registry.

ALL MARKET DATA IN THIS MODULE IS SYNTHETIC: hand-built feature frames designed to trigger a documented branch.
The frame builders are shared with tests/test_strategies_g1.py.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import pytest

from engine.core.events import Direction
from engine.features.catalog import (
    BRENT_WTI_Z,
    CRACK_321,
    CRACK_321_Z,
    CURVE_APPROX,
    CUSHING_VS_5Y,
    PRODUCT_LEAD,
    RET_5,
    SPOT_FRONT_PREMIUM,
    SPREAD_CHG_5,
)
from engine.regime.base import LABEL_BACKWARDATION_HIGHVOL_GEO, LABEL_LOWVOL_RANGE
from engine.strategies.base import Family, Strategy
from engine.strategies.registry import (
    DEFAULT_CONFIG_DIR,
    lifecycles,
    load_strategies,
    strategies_by_family,
)
from engine.strategies.s07_physical_confirmation import S7PhysicalConfirmation
from engine.strategies.s08_brent_wti import S8BrentWti
from engine.strategies.s09_crack import S9CrackSpread
from tests.test_strategies_g1 import assert_valid, base_frame, make_ctx, set_last, set_tail

# rv_yz_21 = 0.30 in the synthetic frame -> 5-day sigma = 0.30 * sqrt(5/252) = 4.23%
BIG_MOVE = 0.08  # well beyond 1.5 sigma


# =========================================================================== S7
def _s7_frame(ret5: float, spread_chg: float, premium_delta: float, cushing: float) -> pd.DataFrame:
    df = set_tail(base_frame(), 10, **{SPOT_FRONT_PREMIUM: 0.01})
    df = set_last(df, **{SPOT_FRONT_PREMIUM: 0.01 + premium_delta})
    return set_last(df, **{RET_5: ret5, SPREAD_CHG_5: spread_chg, CUSHING_VS_5Y: cushing})


def test_s7_fades_a_rally_without_spread_confirmation():
    sig = S7PhysicalConfirmation().generate(make_ctx(_s7_frame(BIG_MOVE, -0.001, -0.002, 0.01)))
    assert sig is not None and sig.direction is Direction.SHORT
    assert_valid(sig)
    assert sig.meta["branch"] == "fade"
    assert sig.stop_pct == 1.5 * 0.02  # tight stop on the fade
    assert "sfumare" in sig.rationale


def test_s7_follows_a_rally_confirmed_by_the_physical():
    sig = S7PhysicalConfirmation().generate(make_ctx(_s7_frame(BIG_MOVE, 0.005, 0.002, -0.05)))
    assert sig is not None and sig.direction is Direction.LONG
    assert_valid(sig)
    assert sig.meta["branch"] == "follow"
    assert sig.stop_pct == 2.5 * 0.02
    assert "stretta fisica" in sig.rationale


def test_s7_mirrors_the_logic_on_sell_offs():
    fade = S7PhysicalConfirmation().generate(make_ctx(_s7_frame(-BIG_MOVE, 0.001, 0.002, -0.01)))
    assert fade is not None and fade.direction is Direction.LONG and fade.meta["branch"] == "fade"
    follow = S7PhysicalConfirmation().generate(make_ctx(_s7_frame(-BIG_MOVE, -0.005, -0.002, 0.05)))
    assert follow is not None and follow.direction is Direction.SHORT and follow.meta["branch"] == "follow"
    assert_valid(fade)
    assert_valid(follow)


def test_s7_none_small_move_ambiguous_branch_and_proxy_curve():
    # move inside 1.5 sigma
    assert S7PhysicalConfirmation().generate(make_ctx(_s7_frame(0.02, -0.001, -0.002, 0.01))) is None
    # rally with widening spreads but Cushing above the 5y mean: neither fade nor follow
    assert S7PhysicalConfirmation().generate(make_ctx(_s7_frame(BIG_MOVE, 0.005, 0.002, 0.05))) is None
    # proxy curve: no fade without real spreads
    proxy = set_last(_s7_frame(BIG_MOVE, -0.001, -0.002, 0.01), **{CURVE_APPROX: 1.0})
    assert S7PhysicalConfirmation().generate(make_ctx(proxy)) is None
    # the sigma multiple is a parameter
    loose = S7PhysicalConfirmation(params={"ret_sigma_mult": 0.2})
    assert loose.generate(make_ctx(_s7_frame(0.02, -0.001, -0.002, 0.01))) is not None


def test_s7_physical_confirms_true_and_false():
    confirming = make_ctx(set_last(base_frame(), **{SPREAD_CHG_5: 0.004, SPOT_FRONT_PREMIUM: 0.01}))
    assert S7PhysicalConfirmation.physical_confirms(confirming, Direction.LONG) is True
    assert S7PhysicalConfirmation.physical_confirms(confirming, 1) is True
    assert S7PhysicalConfirmation.physical_confirms(confirming, Direction.SHORT) is False
    assert S7PhysicalConfirmation.physical_confirms(confirming, Direction.FLAT) is False

    mirrored = make_ctx(set_last(base_frame(), **{SPREAD_CHG_5: -0.004, SPOT_FRONT_PREMIUM: -0.01}))
    assert S7PhysicalConfirmation.physical_confirms(mirrored, Direction.SHORT) is True
    assert S7PhysicalConfirmation.physical_confirms(mirrored, -1) is True
    assert S7PhysicalConfirmation.physical_confirms(mirrored, Direction.LONG) is False

    # a proxy curve or a missing feature can never confirm
    proxy = make_ctx(set_last(base_frame(), **{SPREAD_CHG_5: 0.004, SPOT_FRONT_PREMIUM: 0.01, CURVE_APPROX: 1.0}))
    assert S7PhysicalConfirmation.physical_confirms(proxy, Direction.LONG) is False
    missing = make_ctx(set_last(base_frame(), **{SPREAD_CHG_5: float("nan"), SPOT_FRONT_PREMIUM: 0.01}))
    assert S7PhysicalConfirmation.physical_confirms(missing, Direction.LONG) is False
    # it is stateless: no position, no account, only the context
    assert S7PhysicalConfirmation.physical_confirms(confirming, Direction.LONG) is True


def test_s7_determinism_and_params_exposed():
    ctx = make_ctx(_s7_frame(BIG_MOVE, -0.001, -0.002, 0.01))
    strat = S7PhysicalConfirmation()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None and first.to_dict() == second.to_dict()
    for key in ("ret_sigma_mult", "premium_lookback", "cushing_threshold", "stop_atr_mult_fade"):
        assert key in S7PhysicalConfirmation.default_params()


# =========================================================================== S8
def test_s8_entry_above_the_regime_threshold_and_symbol():
    rich = S8BrentWti().generate(make_ctx(set_last(base_frame(), **{BRENT_WTI_Z: 2.2})))
    assert rich is not None and rich.direction is Direction.SHORT  # Brent rich -> sell the spread
    assert_valid(rich)
    assert rich.instrument == "BZZ26/CLK26"
    assert rich.meta["multi_leg"] is True
    assert rich.meta["legs"] == [("BZZ26", 1.0), ("CLK26", -1.0)]
    assert rich.meta["entry_z"] == 2.0

    cheap = S8BrentWti().generate(make_ctx(set_last(base_frame(), **{BRENT_WTI_Z: -2.2})))
    assert cheap is not None and cheap.direction is Direction.LONG
    assert_valid(cheap)


def test_s8_regime_dependent_threshold():
    df = set_last(base_frame(), **{BRENT_WTI_Z: 2.2})
    # 2.2 sigma is enough in a normal regime...
    assert S8BrentWti().generate(make_ctx(df, label=LABEL_LOWVOL_RANGE)) is not None
    # ...but not in the geopolitical regime, where the doc requires 2.5
    assert S8BrentWti().generate(make_ctx(df, label=LABEL_BACKWARDATION_HIGHVOL_GEO)) is None
    strong = set_last(base_frame(), **{BRENT_WTI_Z: 2.6})
    geo = S8BrentWti().generate(make_ctx(strong, label=LABEL_BACKWARDATION_HIGHVOL_GEO))
    assert geo is not None and geo.direction is Direction.SHORT
    assert geo.meta["entry_z"] == 2.5
    assert "geopolitico" in geo.rationale
    strat = S8BrentWti()
    assert strat.entry_threshold(make_ctx(df, label=LABEL_BACKWARDATION_HIGHVOL_GEO)) == 2.5
    assert strat.entry_threshold(make_ctx(df, label=LABEL_LOWVOL_RANGE)) == 2.0


def test_s8_exit_band_and_no_mans_land():
    inside = S8BrentWti().generate(make_ctx(set_last(base_frame(), **{BRENT_WTI_Z: 0.3})))
    assert inside is not None and inside.direction is Direction.FLAT
    assert inside.meta["exit_band"] is True and inside.strength == 0.0
    assert_valid(inside)
    quiet = S8BrentWti(params={"emit_exit_signal": False})
    assert quiet.generate(make_ctx(set_last(base_frame(), **{BRENT_WTI_Z: 0.3}))) is None
    # between the exit band and the entry threshold: no opinion
    assert S8BrentWti().generate(make_ctx(set_last(base_frame(), **{BRENT_WTI_Z: 1.2}))) is None
    # missing z
    assert S8BrentWti().generate(make_ctx(set_last(base_frame(), **{BRENT_WTI_Z: float("nan")}))) is None


def test_s8_determinism_and_params_exposed():
    ctx = make_ctx(set_last(base_frame(), **{BRENT_WTI_Z: 2.2}))
    strat = S8BrentWti()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None and first.to_dict() == second.to_dict()
    for key in ("entry_z", "entry_z_geo", "exit_z", "geo_label_token", "stop_sigma_mult"):
        assert key in S8BrentWti.default_params()


# =========================================================================== S9
def _lead_frame(sigmas: float) -> pd.DataFrame:
    df = base_frame()
    sigma = float(df[PRODUCT_LEAD].iloc[-252:].std())
    return set_last(df, **{PRODUCT_LEAD: sigmas * sigma})


def test_s9_product_lead_goes_long_crude_for_a_week():
    sig = S9CrackSpread().generate(make_ctx(_lead_frame(2.0)))
    assert sig is not None and sig.direction is Direction.LONG
    assert_valid(sig)
    assert sig.instrument == "BZZ26"  # the flat-price leg trades ctx.instrument
    assert sig.horizon_days == 5
    assert sig.meta["branch"] == "product_lead"
    assert sig.stop_pct == 2.0 * 0.02
    assert "prodotti guidano" in sig.rationale


def test_s9_extreme_crack_trades_the_crack_instrument():
    short_crack = S9CrackSpread().generate(make_ctx(set_last(_lead_frame(0.0), **{CRACK_321_Z: 3.0})))
    assert short_crack is not None and short_crack.direction is Direction.SHORT
    assert_valid(short_crack)
    assert short_crack.instrument == "CRACK321-CLK26"
    assert short_crack.meta["multi_leg"] is True
    assert [code for code, _ in short_crack.meta["legs"]] == ["RBJ26", "HOJ26", "CLK26"]
    assert short_crack.horizon_days == 10

    long_crack = S9CrackSpread().generate(make_ctx(set_last(_lead_frame(0.0), **{CRACK_321_Z: -3.0})))
    assert long_crack is not None and long_crack.direction is Direction.LONG
    assert long_crack.instrument == "CRACK321-CLK26"
    assert_valid(long_crack)


def test_s9_none_without_a_branch_or_with_missing_product_features():
    assert S9CrackSpread().generate(make_ctx(set_last(_lead_frame(0.0), **{CRACK_321_Z: 1.0}))) is None
    # any NaN among the RBOB/HO derived features suppresses both branches
    for col in (PRODUCT_LEAD, CRACK_321, CRACK_321_Z):
        missing = set_last(_lead_frame(2.0), **{CRACK_321_Z: 3.0})
        missing = set_last(missing, **{col: float("nan")})
        assert S9CrackSpread().generate(make_ctx(missing)) is None, col
    # thresholds are parameters
    strict = S9CrackSpread(params={"product_lead_sigma_mult": 5.0, "crack_entry_z": 9.0})
    assert strict.generate(make_ctx(_lead_frame(2.0))) is None


def test_s9_determinism_and_params_exposed():
    ctx = make_ctx(_lead_frame(2.0))
    strat = S9CrackSpread()
    first, second = strat.generate(ctx), strat.generate(ctx)
    assert first is not None and second is not None and first.to_dict() == second.to_dict()
    for key in ("product_lead_sigma_mult", "crack_entry_z", "lead_horizon_days", "crack_horizon_days"):
        assert key in S9CrackSpread.default_params()


# =========================================================================== registry
def test_registry_loads_s1_to_s9_from_the_real_config():
    strategies = load_strategies()  # reads config/strategies.yaml, tolerating absent S10-S18 classes
    by_id = {s.id: s for s in strategies}
    for sid in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"):
        assert sid in by_id, f"{sid} missing from the registry"
        strategy = by_id[sid]
        assert isinstance(strategy, Strategy)
        assert strategy.name and strategy.family
        assert strategy.horizon_days >= 1 and strategy.warmup_days >= 1
        assert strategy.params  # default_params() exposes the documented thresholds
        assert strategy.lifecycle in ("research", "incubation", "active", "retired")  # type: ignore[attr-defined]
    assert by_id["S1"].family == Family.TREND
    assert by_id["S4"].family == Family.CARRY
    assert by_id["S8"].family == Family.RELATIVE_VALUE
    assert (DEFAULT_CONFIG_DIR / "strategies.yaml").exists()


def test_registry_lifecycles_and_family_grouping():
    life = lifecycles()
    assert life["S1"] == "incubation" and life["S19"] == "research"
    groups = strategies_by_family([s for s in load_strategies() if s.id in {"S1", "S2", "S4", "S8", "S9"}])
    assert [s.id for s in groups[Family.TREND]] == ["S1", "S2"]
    assert [s.id for s in groups[Family.CARRY]] == ["S4"]
    assert [s.id for s in groups[Family.RELATIVE_VALUE]] == ["S8", "S9"]


def _cfg(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"strategies": list(entries)}


def test_registry_skips_disabled_and_unimportable_entries(caplog: pytest.LogCaptureFixture):
    config = _cfg(
        {"id": "S1", "class": "engine.strategies.s01_momentum.S1MomentumCarry", "enabled": True},
        {"id": "S2", "class": "engine.strategies.s02_breakout.S2CompressionBreakout", "enabled": False},
        {"id": "S42", "class": "engine.strategies.s42_nope.S42Nope", "enabled": True},
        {"id": "S43", "class": "engine.strategies.s01_momentum.NoSuchClass", "enabled": True},
    )
    with caplog.at_level(logging.WARNING, logger="engine.strategies.registry"):
        strategies = load_strategies(config)
    assert [s.id for s in strategies] == ["S1"]
    messages = [rec.getMessage() for rec in caplog.records]
    assert sum("skipped" in msg for msg in messages) == 2
    assert any("S42" in msg for msg in messages) and any("S43" in msg for msg in messages)


def test_registry_raises_on_id_mismatch_and_bad_entries():
    bad_id = _cfg({"id": "S99", "class": "engine.strategies.s01_momentum.S1MomentumCarry", "enabled": True})
    with pytest.raises(ValueError, match="id mismatch"):
        load_strategies(bad_id)
    with pytest.raises(ValueError, match="dotted class path"):
        load_strategies(_cfg({"id": "S1", "class": "S1MomentumCarry"}))
    with pytest.raises(ValueError, match="lifecycle"):
        load_strategies(
            _cfg(
                {
                    "id": "S1",
                    "class": "engine.strategies.s01_momentum.S1MomentumCarry",
                    "lifecycle": "promoted-by-hand",
                }
            )
        )
    with pytest.raises(ValueError):
        load_strategies(_cfg({"class": "engine.strategies.s01_momentum.S1MomentumCarry"}))


def test_registry_applies_config_params_and_lifecycle():
    config = _cfg(
        {
            "id": "S1",
            "class": "engine.strategies.s01_momentum.S1MomentumCarry",
            "enabled": True,
            "lifecycle": "active",
            "params": {"stop_atr_mult": 4.0},
        }
    )
    strategy = load_strategies(config)[0]
    assert strategy.params["stop_atr_mult"] == 4.0
    assert strategy.params["cot_crowding_max"] == 0.9  # defaults are kept
    assert strategy.lifecycle == "active"  # type: ignore[attr-defined]
