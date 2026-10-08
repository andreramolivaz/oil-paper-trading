"""What the desk can trade, described the way the broker a retail account uses describes it.

The numbers are the ones a Robinhood account sees (verified 2026-10-08, sources in ``docs/RESEARCH.md``):

* ``BNO`` trades commission-free; Regulation T lets a margin account hold 2x overnight, with a 35 % maintenance
  requirement on this fund and 5.25 % a year on the borrowed cash.
* ``/MCL`` is 100 barrels of WTI, cash-settled to the ``/CL`` settlement. Robinhood charges 0.75 $ plus about
  0.52 $ of exchange and NFA fees per contract per side, asks roughly 10 % of the notional as margin day and
  night (no intraday discount), and does not let a position be carried to delivery.
* ICE Brent futures are NOT available there. The Brent exposure a US retail account can hold is the fund and
  its options; the leveraged linear exposure is WTI. That basis risk is real and is shown, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Vehicle:
    id: str
    kind: str  # "etf" | "future"
    name: str
    underlying: str  # "brent" | "wti"
    unit_it: str  # how one unit is called in the Italian UI
    lot: float  # smallest tradeable quantity, in broker units (shares, or barrels for futures)
    margin_rate: float  # initial margin as a fraction of notional
    stop_out_level: float  # equity / margin below which the broker force-reduces
    commission_per_lot: float  # USD per lot per side
    half_spread_bps: float  # half-spread paid by a marketable order at normal volatility
    financing_rate: float  # per year, on borrowed cash (funds held on margin); futures pay none
    allow_short: bool
    session: str  # "us_regular" | "globex"
    decision_time_ny: tuple[int, int]  # when the daily decision is taken, New York clock
    robinhood: str
    note_it: str

    @property
    def max_leverage(self) -> float:
        return 1.0 / self.margin_rate if self.margin_rate > 0 else 1.0


BNO = Vehicle(
    id="BNO",
    kind="etf",
    name="United States Brent Oil Fund",
    underlying="brent",
    unit_it="azioni",
    lot=1.0,
    margin_rate=0.50,
    stop_out_level=0.70,  # 0.70 * 50 % = 35 % of the position: the fund's maintenance requirement
    commission_per_lot=0.0,
    half_spread_bps=2.0,
    financing_rate=0.0525,
    allow_short=False,
    session="us_regular",
    decision_time_ny=(15, 0),
    robinhood="BNO (azioni; opzioni settimanali quotate)",
    note_it="ETF sul Brent: detiene il future ICE più vicino e lo rolla due settimane prima della scadenza.",
)

MCL = Vehicle(
    id="MCL",
    kind="future",
    name="Micro WTI Crude Oil",
    underlying="wti",
    unit_it="barili",
    lot=100.0,
    margin_rate=0.10,
    stop_out_level=0.50,
    commission_per_lot=1.27,
    half_spread_bps=1.5,
    financing_rate=0.0,
    allow_short=True,
    session="globex",
    decision_time_ny=(14, 35),
    robinhood="/MCL (future micro WTI, 100 barili)",
    note_it="Su Robinhood non c'è un future sul Brent: la leva lineare passa dal WTI, con rischio di base.",
)

VEHICLES: dict[str, Vehicle] = {v.id: v for v in (BNO, MCL)}


def get_vehicle(vehicle_id: str) -> Vehicle:
    try:
        return VEHICLES[vehicle_id]
    except KeyError as exc:
        raise ValueError(f"unknown vehicle {vehicle_id!r}; known: {sorted(VEHICLES)}") from exc
