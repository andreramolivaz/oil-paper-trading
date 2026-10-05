"""OVX-implied price range (brief §11: "range a 1 mese implicito nell'OVX").

OVX is the CBOE Crude Oil Volatility Index: the 30-calendar-day implied volatility of options on the USO ETF,
which tracks the front NYMEX WTI future. It is NOT a Brent implied vol and NOT a term-structure of vol, so the
range derived here is an approximation (approx=True everywhere it is shown): WTI vol is used as a proxy for
Brent vol and the 30-day level is applied to the requested horizon with the square-root-of-time rule.

range = price * exp(-/+ z * (OVX/100) * sqrt(days/252))      (days = trading days, 21 ≈ one month)

With z=1 the band covers ~68% of outcomes under a lognormal with that vol; z=1.645 gives ~90%.
"""

from __future__ import annotations

import math
from typing import Any

APPROX_NOTE_IT = (
    "Range ≈: l'OVX è la volatilità implicita a 30 giorni delle opzioni sull'ETF USO (WTI), usata come proxy "
    "della volatilità del Brent."
)


def ovx_implied_range(price: float, ovx: float, days: int = 21, z: float = 1.0) -> tuple[float, float]:
    """(low, high) = price * exp(-/+ z * ovx/100 * sqrt(days/252)). `ovx` is in percent (e.g. 45.2)."""
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"price must be a positive finite number, got {price}")
    if not math.isfinite(ovx) or ovx < 0:
        raise ValueError(f"ovx must be a non-negative finite percentage, got {ovx}")
    if days <= 0:
        raise ValueError("days must be > 0")
    if z <= 0:
        raise ValueError("z must be > 0")
    width = z * (ovx / 100.0) * math.sqrt(days / 252.0)
    return float(price * math.exp(-width)), float(price * math.exp(width))


def ovx_implied_range_record(
    price: float, ovx: float | None, days: int = 21, z: float = 1.0, asof: str | None = None, source: str = "cboe_ovx"
) -> dict[str, Any]:
    """JSON-ready dict for the dashboard; `low`/`high` are None when OVX is missing (never invented)."""
    if ovx is None or not math.isfinite(ovx):
        return {
            "low": None,
            "high": None,
            "ovx": None,
            "days": days,
            "z": z,
            "approx": True,
            "source": source,
            "asof": asof,
            "note": "OVX non disponibile: range non calcolato.",
        }
    low, high = ovx_implied_range(price, ovx, days=days, z=z)
    return {
        "low": low,
        "high": high,
        "ovx": float(ovx),
        "days": days,
        "z": z,
        "approx": True,
        "source": source,
        "asof": asof,
        "note": APPROX_NOTE_IT,
    }
