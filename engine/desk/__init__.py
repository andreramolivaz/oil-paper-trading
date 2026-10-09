"""The desk: a handful of paper books that trade instruments a retail account can really buy.

Why a second engine next to ``engine.backtest.session``: the first one found no promotable strategy in 18.5
years and was flat by construction, and it measured everything on the continuous ``BZ=F`` series, which is not
one contract. Near expiry Yahoo interleaves the expiring and the next contract inside the same bar, and in a
steep backwardation that prints fake 5-7 $ moves and fake -6 % roll days. The desk only ever touches series
with no roll jump in them:

* ``BNO`` (United States Brent Oil Fund) - an exchange-traded fund, real opens and closes, since 2010;
* ``MCL`` (Micro WTI, 100 bbl) - priced contract by contract, and backtested on the exact NYMEX contract 1/2
  settlements from 1986 with an early roll, so a position is never carried into an expiry.

Everything a book decides comes from one forecast: seven sleeves with published rules, re-measured here on
real data (``docs/RESEARCH.md``), grouped by where their information comes from. The price of crude: trend
(EWMAC), acceleration, skew. The futures curve: carry (the sign of the slope) and carry-momentum (the slope
against its own 20-day average). Other markets: the trend of copper and of the dollar, read a day late.
Leverage is an output of volatility targeting, capped per book and never above the hard 10x; it is not an
input.

A fund cannot be sold short by the account the desk imitates: a fund book that may be short holds that side as
a long position in the inverse fund ``SCO``, bought with cash.

A fourth book sells put credit spreads on the real, delayed option quotes of the oil funds. Its evidence is a
model replay, so it is labelled experimental everywhere and exists to be measured on real quotes.

Modules:
    vehicles   what can be traded, with its real lot, margin and fees
    signals    causal forecasts, pure functions of return and slope series
    data       the clean series, assembled from the raw snapshot store
    book       one paper account: sizing, caps, whole lots, order generation through ``PaperBroker``
    engine     ``Desk``: the single loop that drives the backtest and the live tick
    backtest   history replay, metrics, block-bootstrap ruin
    live       the scheduler's tick: completed bars, financing, roll, one decision a session, mark
    options    the options book and its model replay
    report     the backtest payload the weekly job writes
    export     ``site-data/desk.json``, the one file the terminal reads
"""
