"""Instruments: Brent/WTI/product futures, calendar spreads, inter-market spreads, synthetic options."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from engine.core.calendar import contract_code, expiry_for, parse_contract_code

BARRELS_PER_CONTRACT = 1000  # ICE Brent, NYMEX WTI
GALLONS_PER_BARREL = 42.0  # RBOB and HO quote in USD/gal


class Kind(StrEnum):
    FUTURE = "future"
    CALENDAR_SPREAD = "calendar_spread"
    INTERMARKET_SPREAD = "intermarket_spread"
    CRACK = "crack"
    SYNTHETIC_OPTION = "synthetic_option"
    CASH_INDEX = "cash_index"  # non-tradeable references (OVX, spot)


ROOT_META: dict[str, dict[str, object]] = {
    "BZ": {"name": "ICE Brent Crude", "exchange": "ICE Futures Europe", "tick": 0.01, "unit": "USD/bbl"},
    "CL": {"name": "NYMEX WTI Crude", "exchange": "CME NYMEX", "tick": 0.01, "unit": "USD/bbl"},
    "RB": {"name": "NYMEX RBOB Gasoline", "exchange": "CME NYMEX", "tick": 0.0001, "unit": "USD/gal"},
    "HO": {"name": "NYMEX NY Harbor ULSD", "exchange": "CME NYMEX", "tick": 0.0001, "unit": "USD/gal"},
}


@dataclass(frozen=True)
class Future:
    root: str
    year: int
    month: int

    @property
    def code(self) -> str:
        return contract_code(self.root, self.year, self.month)

    @property
    def expiry(self) -> date:
        return expiry_for(self.root, self.year, self.month)

    @property
    def yahoo_symbol(self) -> str:
        return f"{self.code}.NYM"

    @classmethod
    def from_code(cls, code: str) -> Future:
        root, y, m = parse_contract_code(code)
        return cls(root, y, m)

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class Leg:
    future: Future
    ratio: float  # +1 long, -1 short; crack 3-2-1 uses -3, +2, +1 (per barrel-equivalent)


@dataclass(frozen=True)
class Instrument:
    """What the paper broker can hold. Prices always in USD per barrel-equivalent of the first leg."""

    symbol: str
    kind: Kind
    legs: tuple[Leg, ...] = field(default_factory=tuple)
    description: str = ""
    approx: bool = False  # True for synthetic instruments (options) - labelled as approximation everywhere

    @property
    def root(self) -> str:
        return self.legs[0].future.root if self.legs else self.symbol

    @property
    def expiry(self) -> date | None:
        return min((l.future.expiry for l in self.legs), default=None)

    @staticmethod
    def future(f: Future) -> Instrument:
        return Instrument(symbol=f.code, kind=Kind.FUTURE, legs=(Leg(f, 1.0),), description=f"{f.root} {f.code}")

    @staticmethod
    def calendar_spread(near: Future, far: Future) -> Instrument:
        return Instrument(
            symbol=f"{near.code}-{far.code}",
            kind=Kind.CALENDAR_SPREAD,
            legs=(Leg(near, 1.0), Leg(far, -1.0)),
            description=f"Calendar spread {near.code} vs {far.code}",
        )

    @staticmethod
    def brent_wti(brent: Future, wti: Future) -> Instrument:
        return Instrument(
            symbol=f"{brent.code}/{wti.code}",
            kind=Kind.INTERMARKET_SPREAD,
            legs=(Leg(brent, 1.0), Leg(wti, -1.0)),
            description=f"Brent-WTI {brent.code} vs {wti.code}",
        )

    @staticmethod
    def crack_321(crude: Future, rbob: Future, ho: Future) -> Instrument:
        """3-2-1 crack per barrel of crude: (2*RBOB*42 + 1*HO*42 - 3*CL) / 3."""
        return Instrument(
            symbol=f"CRACK321-{crude.code}",
            kind=Kind.CRACK,
            legs=(Leg(rbob, 2.0 / 3.0), Leg(ho, 1.0 / 3.0), Leg(crude, -1.0)),
            description=f"3-2-1 crack vs {crude.code}",
        )


def product_price_per_bbl(price_per_gal: float) -> float:
    return price_per_gal * GALLONS_PER_BARREL
