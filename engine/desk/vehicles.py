"""What the desk can trade, described the way the broker a retail account uses describes it.

The numbers are the ones a Robinhood account sees (verified 2026-10-08, sources in ``docs/RESEARCH.md``):

* ``BNO`` trades commission-free; Regulation T lets a margin account hold 2x overnight, with a 35 % maintenance
  requirement on this fund and 5.25 % a year on the borrowed cash.
* ``/MCL`` is 100 barrels of WTI, cash-settled to the ``/CL`` settlement. Robinhood charges 0.75 $ plus about
  0.52 $ of exchange and NFA fees per contract per side, asks roughly 10 % of the notional as margin day and
  night (no intraday discount), and does not let a position be carried to delivery.
* ICE Brent futures are NOT available there. The Brent exposure a US retail account can hold is the fund and
  its options; the leveraged linear exposure is WTI. That basis risk is real and is shown, not hidden.
* ``SCO`` (ProShares UltraShort Bloomberg Crude Oil) trades commission-free like any other fund. It aims at
  -2x the DAILY return of a WTI futures index, so over more than a day its return is not -2x the oil's: a book
  that holds it resizes the position at every decision, and its backtest uses the fund's real prices, which
  contain the fee (0.95 % a year), the roll and the daily reset. It is how a fund book goes short without
  borrowing shares: it BUYS the inverse fund, with cash, and can lose no more than what it put in it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Vehicle:
    id: str
    kind: str  # "etf" | "future"
    name: str
    underlying: str  # "brent" | "wti"
    unit: str  # how one unit is called in the UI, plural ("shares", "barrels")
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
    note: str

    @property
    def max_leverage(self) -> float:
        return 1.0 / self.margin_rate if self.margin_rate > 0 else 1.0


BNO = Vehicle(
    id="BNO",
    kind="etf",
    name="United States Brent Oil Fund",
    underlying="brent",
    unit="shares",
    lot=1.0,
    margin_rate=0.50,
    stop_out_level=0.70,  # 0.70 * 50 % = 35 % of the position: the fund's maintenance requirement
    commission_per_lot=0.0,
    half_spread_bps=2.0,
    financing_rate=0.0525,
    allow_short=False,
    session="us_regular",
    decision_time_ny=(15, 0),
    robinhood="BNO (shares; weekly options listed)",
    note="Brent ETF: holds the nearest ICE future and rolls it two weeks before the expiry.",
)

MCL = Vehicle(
    id="MCL",
    kind="future",
    name="Micro WTI Crude Oil",
    underlying="wti",
    unit="barrels",
    lot=100.0,
    margin_rate=0.10,
    stop_out_level=0.50,
    commission_per_lot=1.27,
    half_spread_bps=1.5,
    financing_rate=0.0,
    allow_short=True,
    session="globex",
    decision_time_ny=(14, 35),
    robinhood="/MCL (micro WTI future, 100 barrels)",
    note="Robinhood has no Brent future: the linear leverage goes through WTI, with basis risk.",
)

# ---- the satellite markets (phase 12): the same price forecast, on the funds of other markets -----------------
# Each is a fund a Robinhood account holds like BNO (commission-free, Regulation T margin 50 %, maintenance 35 %),
# decided at the same time of day and filled on the same half-hour bars. The half-spreads are the ones quoted on
# an ordinary day (GLD trades like a large stock; CPER and DBA are thinner and pay more). Why these three is in
# ``docs/RESEARCH.md``, chapter 8: the weather moves agriculture, geopolitics moves gold, the electricity demand
# of AI moves copper - independent bets beside the crude the four core books hold. Natural gas through its funds
# (UNG, short via KOLD) was tried there and failed the declared filter; it is not a vehicle.
GLD = Vehicle(
    id="GLD",
    kind="etf",
    name="SPDR Gold Shares",
    underlying="gold",
    unit="shares",
    lot=1.0,
    margin_rate=0.50,
    stop_out_level=0.70,
    commission_per_lot=0.0,
    half_spread_bps=1.0,
    financing_rate=0.0525,
    allow_short=False,
    session="us_regular",
    decision_time_ny=(15, 0),
    robinhood="GLD (shares)",
    note="Gold fund: holds bullion in a vault, no roll and no curve; 0.40 % a year.",
)

CPER = Vehicle(
    id="CPER",
    kind="etf",
    name="United States Copper Index Fund",
    underlying="copper",
    unit="shares",
    lot=1.0,
    margin_rate=0.50,
    stop_out_level=0.70,
    commission_per_lot=0.0,
    half_spread_bps=8.0,
    financing_rate=0.0525,
    allow_short=False,
    session="us_regular",
    decision_time_ny=(15, 0),
    robinhood="CPER (shares)",
    note="Copper fund: holds COMEX copper futures along a rules-based curve position; thin, pays its spread.",
)

DBA = Vehicle(
    id="DBA",
    kind="etf",
    name="Invesco DB Agriculture Fund",
    underlying="agriculture",
    unit="shares",
    lot=1.0,
    margin_rate=0.50,
    stop_out_level=0.70,
    commission_per_lot=0.0,
    half_spread_bps=5.0,
    financing_rate=0.0525,
    allow_short=False,
    session="us_regular",
    decision_time_ny=(15, 0),
    robinhood="DBA (shares)",
    note="Agriculture fund: corn, soybeans, wheat, sugar, coffee, cocoa, cattle and hogs futures, rolled on rules.",
)

VEHICLES: dict[str, Vehicle] = {v.id: v for v in (BNO, MCL, GLD, CPER, DBA)}


@dataclass(frozen=True)
class InverseFund:
    """A fund a book BUYS to be short: the second leg of a fund book (``BookConfig.short_via``).

    It is not a vehicle of its own: it has no forecast and no book. It is priced on its own bars, sized so
    that ``shares x price x |multiplier|`` is the short exposure wanted, and never bought on margin.
    """

    id: str
    name: str
    underlying: str  # "wti": the short side of a Brent book carries the Brent-WTI basis
    multiplier: float  # oil exposure of one dollar held, per day (-2.0)
    max_weight: float  # ceiling on its notional as a fraction of equity: 1.0 = cash only, never borrowed
    unit: str  # how one unit is called in the UI, plural
    robinhood: str
    note: str

    @property
    def max_exposure(self) -> float:
        """The largest short exposure (multiple of equity) the fund can carry without borrowing."""
        return self.max_weight * abs(self.multiplier)


SCO = InverseFund(
    id="SCO",
    name="ProShares UltraShort Bloomberg Crude Oil",
    underlying="wti",
    multiplier=-2.0,
    max_weight=1.0,
    unit="shares",
    robinhood="SCO (shares)",
    note=(
        "ETF that returns -2 times a WTI futures index each day: the book's short side is obtained by buying it "
        "with cash, and it cannot lose more than what is invested in it. Over several days its return is not "
        "-2 times the crude's (daily reset, 0.95% a year in fees)."
    ),
)

INVERSE_FUNDS: dict[str, InverseFund] = {f.id: f for f in (SCO,)}


def get_inverse_fund(fund_id: str) -> InverseFund:
    try:
        return INVERSE_FUNDS[fund_id]
    except KeyError as exc:
        raise ValueError(f"unknown inverse fund {fund_id!r}; known: {sorted(INVERSE_FUNDS)}") from exc


def get_vehicle(vehicle_id: str) -> Vehicle:
    try:
        return VEHICLES[vehicle_id]
    except KeyError as exc:
        raise ValueError(f"unknown vehicle {vehicle_id!r}; known: {sorted(VEHICLES)}") from exc
