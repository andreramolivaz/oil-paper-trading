"""The lists a vehicle must appear in at once. A vehicle added to one and not another is silently never
downloaded, never blocked, or judged by the wrong quality checks (review of phase 12, 2026-10-13)."""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.data.fetch import FRESHNESS, INTRADAY_ENTRIES
from engine.desk.data import LEG_TABLES, SATELLITE_TABLES
from engine.desk.vehicles import VEHICLES
from engine.live.jobs import DESK_REQUIRES, DESK_VEHICLE_ENTRIES, EPHEMERAL_RAW

CONFIG = yaml.safe_load(Path("config/data_sources.yaml").read_text(encoding="utf-8"))


def test_every_vehicle_is_downloaded_blocked_and_required_through_the_same_lists():
    wanted = {"BNO", "MCL"} | set(SATELLITE_TABLES)
    assert set(VEHICLES) == wanted
    assert set(DESK_VEHICLE_ENTRIES) == wanted and set(DESK_REQUIRES) == wanted
    for vehicle, (daily, _) in SATELLITE_TABLES.items():
        assert DESK_VEHICLE_ENTRIES[vehicle][0] == daily and DESK_REQUIRES[vehicle] == ((daily,),)


def test_every_half_hour_table_is_judged_as_intraday_and_trimmed_by_the_tick():
    half_hour = set(CONFIG["desk_intraday"])
    assert half_hour <= INTRADAY_ENTRIES, half_hour - INTRADAY_ENTRIES
    assert half_hour <= set(EPHEMERAL_RAW), half_hour - set(EPHEMERAL_RAW)
    for _, intraday in list(SATELLITE_TABLES.values()) + list(LEG_TABLES.values()):
        assert intraday in half_hour


def test_every_fund_table_the_desk_reads_has_the_funds_freshness():
    daily = [d for d, _ in SATELLITE_TABLES.values()] + [d for d, _ in LEG_TABLES.values()] + ["bno_daily"]
    for entry in daily:
        assert entry in CONFIG["desk"], entry
        assert FRESHNESS[entry] == FRESHNESS["bno_daily"], entry
