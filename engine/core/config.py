"""Configuration loading. YAML files in config/, environment overrides for paths and secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    config_dir: Path
    eia_api_key: str | None
    fred_api_key: str | None
    github_repo: str
    offline: bool  # True in tests / CI unit stage: adapters must not touch the network

    @staticmethod
    def from_env() -> Settings:
        return Settings(
            state_dir=Path(os.environ.get("OPT_STATE_DIR", REPO_ROOT / "state")),
            config_dir=Path(os.environ.get("OPT_CONFIG_DIR", CONFIG_DIR)),
            eia_api_key=os.environ.get("EIA_API_KEY") or None,
            fred_api_key=os.environ.get("FRED_API_KEY") or None,
            github_repo=os.environ.get("GITHUB_REPOSITORY", "andreramolivaz/oil-paper-trading"),
            offline=os.environ.get("OPT_OFFLINE", "0") == "1",
        )


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


@cache
def _cached_yaml(path_str: str, mtime: float) -> dict[str, Any]:
    return load_yaml(Path(path_str))


def load_config(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Load config/<name>.yaml (cached by path+mtime so tests can rewrite files)."""
    p = (config_dir or CONFIG_DIR) / f"{name}.yaml"
    return dict(_cached_yaml(str(p), p.stat().st_mtime))


def deep_get(d: dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


@dataclass
class RiskConfig:
    """Typed view of config/risk.yaml (defaults mirror the brief)."""

    initial_capital: float = 10_000.0
    lot_bbl: float = 100.0
    max_leverage: float = 10.0
    default_max_leverage: float = 1.0
    margin_rate: float = 0.10
    stop_out_margin_level: float = 0.50
    dead_equity_fraction: float = 0.05
    daily_loss_breaker: float = 0.05
    es99_budget: float = 0.03
    kelly_fraction: float = 0.25
    vol_target_annual: float = 0.15
    hysteresis_bbl_fraction: float = 0.15
    stale_minutes_intraday: int = 90
    stale_days_eod: int = 3
    drawdown_delever: dict[str, float] = field(default_factory=lambda: {"0.10": 0.5, "0.20": 0.25, "0.30": 0.0})
    gate: dict[str, Any] = field(default_factory=dict)
    costs: dict[str, Any] = field(default_factory=dict)
    event_delever: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def load(config_dir: Path | None = None) -> RiskConfig:
        d = load_config("risk", config_dir)
        rc = RiskConfig(raw=d)
        acct = d.get("account", {})
        lev = d.get("leverage", {})
        marg = d.get("margin", {})
        brk = d.get("breakers", {})
        siz = d.get("sizing", {})
        stale = d.get("staleness", {})
        rc.initial_capital = float(acct.get("initial_capital", rc.initial_capital))
        rc.lot_bbl = float(acct.get("lot_bbl", rc.lot_bbl))
        rc.max_leverage = float(lev.get("hard_cap", rc.max_leverage))
        rc.default_max_leverage = float(lev.get("default_cap", rc.default_max_leverage))
        rc.drawdown_delever = {str(k): float(v) for k, v in lev.get("drawdown_delever", rc.drawdown_delever).items()}
        rc.margin_rate = float(marg.get("rate", rc.margin_rate))
        rc.stop_out_margin_level = float(marg.get("stop_out_level", rc.stop_out_margin_level))
        rc.dead_equity_fraction = float(acct.get("dead_equity_fraction", rc.dead_equity_fraction))
        rc.daily_loss_breaker = float(brk.get("daily_loss", rc.daily_loss_breaker))
        rc.es99_budget = float(siz.get("es99_budget", rc.es99_budget))
        rc.kelly_fraction = float(siz.get("kelly_fraction", rc.kelly_fraction))
        rc.vol_target_annual = float(siz.get("vol_target_annual", rc.vol_target_annual))
        rc.hysteresis_bbl_fraction = float(siz.get("hysteresis_fraction", rc.hysteresis_bbl_fraction))
        rc.stale_minutes_intraday = int(stale.get("intraday_minutes", rc.stale_minutes_intraday))
        rc.stale_days_eod = int(stale.get("eod_days", rc.stale_days_eod))
        rc.gate = dict(d.get("gate", {}))
        rc.costs = dict(d.get("costs", {}))
        rc.event_delever = dict(d.get("event_delever", {}))
        if rc.max_leverage > 10.0:
            raise ValueError("leverage.hard_cap cannot exceed 10x (brief §3)")
        return rc
