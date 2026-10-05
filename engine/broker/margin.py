"""Margin arithmetic of the paper broker: pure functions, no state (brief §10 "Margine e liquidazione").

Model (futures-style account):
  * cash is never debited for a purchase; a position only carries unrealized P&L,
    so ``equity = cash + sum(qty * (price - avg_price))``;
  * the margin requirement is ``rate * gross_notional`` (default 10 %, consistent with the 10x cap);
  * ``margin_level = equity / margin_used``; below ``stop_out_level`` (default 0.5) the broker force-liquidates;
  * the account is dead when ``equity <= dead_fraction * initial_capital`` (default 5 % of 10 000 $) or ``equity <= 0``.
"""

from __future__ import annotations

import math


def margin_required(gross_notional: float, rate: float) -> float:
    """Margin requirement in USD for a gross notional (sum over positions of ``|qty| * reference price``)."""
    if gross_notional < 0:
        raise ValueError("gross_notional must be >= 0")
    return gross_notional * rate


def margin_level(equity: float, margin_used: float) -> float | None:
    """``equity / margin_used``; None when there is no margin in use (flat account)."""
    if margin_used <= 0:
        return None
    return equity / margin_used


def leverage(gross_notional: float, equity: float) -> float:
    """Gross leverage ``gross_notional / equity``. 0 when flat; +inf when positions exist but equity <= 0."""
    if gross_notional <= 0:
        return 0.0
    if equity <= 0:
        return math.inf
    return gross_notional / equity


def liquidation_price(
    qty_bbl: float,
    avg_price: float,
    cash: float,
    margin_rate: float,
    stop_out_level: float,
    other_unrealized: float = 0.0,
    other_margin: float = 0.0,
) -> float | None:
    """Price at which the margin level of a single net position reaches ``stop_out_level``.

    Derivation. Let q be the signed quantity, a the average price, C the cash plus the (frozen) unrealized P&L of
    any other positions, r the margin rate, L the stop-out level and M_o the margin of the other positions. Then

        equity(p) = C + q (p - a)            margin(p) = r |q| p + M_o

    and the stop-out condition equity(p) = L * margin(p) solves to

        p* = (C - q a - L M_o) / (L r |q| - q).

    For a long (q > 0) the denominator |q| (L r - 1) is negative with any sane L r < 1, so p* < a when the account
    has positive cash: the price has to FALL to the liquidation level. For a short (q < 0) the denominator
    |q| (L r + 1) is positive and p* > a: the price has to RISE. Example (defaults r = 0.10, L = 0.5):
    long 1 000 bbl at 100 $ with 10 000 $ cash -> p* = -90 000 / -950 = 94.74 $ (equity 4 737 $, margin 9 474 $,
    level 0.50); the same short -> p* = 110 000 / 1 050 = 104.76 $.

    Returns None when flat, when the denominator vanishes, or when the solution is not a positive price
    (e.g. a long so lightly leveraged that the margin level never hits L before the price reaches zero).
    """
    if qty_bbl == 0:
        return None
    denom = stop_out_level * margin_rate * abs(qty_bbl) - qty_bbl
    if abs(denom) < 1e-12:
        return None
    p = (cash + other_unrealized - qty_bbl * avg_price - stop_out_level * other_margin) / denom
    if not math.isfinite(p) or p <= 0:
        return None
    return p


def is_dead(equity: float, initial_capital: float, dead_fraction: float) -> bool:
    """Account floor (brief §10): equity at or below ``dead_fraction`` of the initial capital, or non-positive."""
    return equity <= 0 or equity <= dead_fraction * initial_capital
