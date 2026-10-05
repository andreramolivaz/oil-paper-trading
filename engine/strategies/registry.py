"""Strategy registry: build the strategy universe from config/strategies.yaml.

Rules:
  * classes are imported lazily from their dotted path, so a missing module (e.g. a strategy still being
    written) degrades to a warning and a skip instead of crashing the engine;
  * `enabled: false` entries are not instantiated at all;
  * every instance carries a `lifecycle` attribute (research | incubation | active | retired), which is set only
    by the validation report - never by the registry;
  * the instance's own `id` must match the config `id`: a mismatch is a configuration bug and raises.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, cast

import yaml

from engine.strategies.base import Strategy

log = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CONFIG_FILENAME = "strategies.yaml"
VALID_LIFECYCLES = ("research", "incubation", "active", "retired")


def config_path(config_dir: Path | None = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / CONFIG_FILENAME


def load_config(config: dict[str, Any] | None = None, config_dir: Path | None = None) -> dict[str, Any]:
    """Return the strategies config, reading config/strategies.yaml when none is passed in."""
    if config is not None:
        return config
    path = config_path(config_dir)
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} does not contain a mapping")
    return loaded


def _entries(config: dict[str, Any] | None, config_dir: Path | None) -> list[dict[str, Any]]:
    cfg = load_config(config, config_dir)
    raw = cfg.get("strategies") or []
    if not isinstance(raw, list):
        raise ValueError("'strategies' must be a list of entries")
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"invalid strategy entry: {entry!r}")
        out.append(entry)
    return out


def load_strategies(config: dict[str, Any] | None = None, config_dir: Path | None = None) -> list[Strategy]:
    """Instantiate every enabled strategy of the config, skipping (with a warning) the ones that cannot load."""
    strategies: list[Strategy] = []
    for entry in _entries(config, config_dir):
        sid = str(entry.get("id", "")).strip()
        dotted = str(entry.get("class", "")).strip()
        if not sid or not dotted:
            raise ValueError(f"strategy entry needs both 'id' and 'class': {entry!r}")
        if not entry.get("enabled", True):
            log.info("strategy %s disabled in config, skipped", sid)
            continue
        module_name, _, class_name = dotted.rpartition(".")
        if not module_name:
            raise ValueError(f"strategy {sid}: '{dotted}' is not a dotted class path")
        try:
            module = importlib.import_module(module_name)
            klass = getattr(module, class_name)
        except Exception as exc:
            # S10-S18 may not exist yet while another agent writes them: warn and skip (ImportError,
            # AttributeError, but also a SyntaxError in a module that is still being written).
            log.warning("strategy %s: cannot import %s (%s: %s), skipped", sid, dotted, type(exc).__name__, exc)
            continue
        if not (isinstance(klass, type) and issubclass(klass, Strategy)):
            log.warning("strategy %s: %s is not a Strategy subclass, skipped", sid, dotted)
            continue

        params = entry.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"strategy {sid}: 'params' must be a mapping")
        instance = klass(params=params)
        if instance.id != sid:
            raise ValueError(f"strategy id mismatch: config says {sid!r}, {dotted} says {instance.id!r}")
        lifecycle = str(entry.get("lifecycle", "research"))
        if lifecycle not in VALID_LIFECYCLES:
            raise ValueError(f"strategy {sid}: unknown lifecycle {lifecycle!r}")
        cast(Any, instance).lifecycle = lifecycle
        strategies.append(instance)
    return strategies


def lifecycles(config: dict[str, Any] | None = None, config_dir: Path | None = None) -> dict[str, str]:
    """id -> lifecycle for every entry of the config, enabled or not (the dashboard shows them all)."""
    out: dict[str, str] = {}
    for entry in _entries(config, config_dir):
        sid = str(entry.get("id", "")).strip()
        if sid:
            out[sid] = str(entry.get("lifecycle", "research"))
    return out


def strategies_by_family(strategies: list[Strategy]) -> dict[str, list[Strategy]]:
    """Group strategies by family, preserving the config order (used by the 3-family agreement gate)."""
    out: dict[str, list[Strategy]] = {}
    for strategy in strategies:
        out.setdefault(strategy.family, []).append(strategy)
    return out
