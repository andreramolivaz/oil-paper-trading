"""The linear lottery books (phase 22, ``docs/RESEARCH.md`` chapter 17 D): what the page says beside them.

A lottery book (``role: lottery`` in ``config/books.yaml``) is a book in which ONE hard rule is suspended by the
owner's decision, for that book alone: the 0.50 ceiling on the volatility target. Everything else is kept - the
sizing formula with the allocator's multiplier, the 10x hard cap, the vehicle's margin, the daily-loss breaker, the
5 % floor - and it is out of every total, never promoted, without a backtest. The Rocket (``rocket.py``) is the
other member of the group and has its own module.

``MODEL_ODDS`` is what the one measurement found before the book started (``scripts/research_oracle.py --vehicle
MCL --vol-target 1.5 --weekend-cap 3.0 --weekend-scale 0.5 --event-budget 0.5 --odds``, 15 November 2026, chapter
17 M): a result shown beside the book, not a parameter of anything. Two rows, on purpose: the archive's own days
in another order, which inherit eight months that went well, and the same days with their mean removed, which is
what the book looks like if the rule has no edge - the prior it was registered with.
"""

from __future__ import annotations

from typing import Any

SUSPENDED_RULE = "no volatility target above 0.50"
DECIDED = "the owner's decision of 14 November 2026"
MODEL_ODDS: dict[str, dict[str, Any]] = {
    "oracle_3x": {
        "approx": True,
        "span": "18 March - 13 November 2026",
        "sessions": 167,
        "horizon_sessions": 63,
        "paths": 20000,
        "measured": {
            "sharpe_after_costs": 1.40,
            "sharpe_se": 1.72,
            "halves": [1.48, 1.40],
            "net_return": 0.80,  # on a fixed 10 000 $ account at whole contracts, eight months
            "volatility": 0.86,
            "worst_day": -0.131,
            "best_day": 0.199,
            "mean_exposure": 1.35,
            "max_exposure": 3.00,
            "days_cut_by_a_ceiling": 57,
            "days_with_a_forecast": 149,
        },
        # the archive's days in another order (a block bootstrap of the daily net returns, blocks of 5 sessions)
        "as_measured": {
            "p_double": 0.1146,
            "p_up_50": 0.3149,
            "p_gain": 0.7034,
            "p_lose_half": 0.0132,
            "p_touch_half": 0.0268,
            "p_dead": 0.0,
            "median_multiple": 1.24,
            "p10_multiple": 0.737,
            "p90_multiple": 2.07,
        },
        # the same days with their mean removed: no edge, the prior
        "no_edge": {
            "p_double": 0.0255,
            "p_up_50": 0.1096,
            "p_gain": 0.4148,
            "p_lose_half": 0.0688,
            "p_touch_half": 0.1060,
            "p_dead": 0.0,
            "median_multiple": 0.919,
            "p10_multiple": 0.544,
            "p90_multiple": 1.536,
        },
        "source": (
            "docs/RESEARCH.md 17 M: the Oracle's rule on the micro WTI future at a 150 % volatility target, whole "
            "contracts, the book's ceilings; three months resampled from eight months of one war"
        ),
    }
}


def block(book_id: str, vol_target: float) -> dict[str, Any]:
    """What the page shows beside a linear lottery book: the rule suspended, by whom, and the model's odds."""
    return {
        "suspended": f"{SUSPENDED_RULE} (this book: {vol_target:.2f})",
        "decided": DECIDED,
        "odds": MODEL_ODDS.get(book_id),
        "note": (
            "The same bet as the Oracle with a stake three times larger, not a better one. Out of every total, "
            "never promoted, no backtest."
        ),
    }
