"""SYNTHETIC market data for unit tests (never real data, never loaded by the engine).

See tests/fixtures/synthetic/README.md. Everything is seeded so tests are deterministic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from engine.core.calendar import is_business_day, listed_months
from engine.core.timeutil import settlement_ts
from engine.data.market_data import CURVE_COLUMNS, PRICE_COLUMNS, MarketData


def business_dates(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if is_business_day(d, "ICE"):
            out.append(d)
        d += timedelta(days=1)
    return out


def make_market_data(
    n_days: int = 500,
    start: date = date(2024, 1, 2),
    seed: int = 7,
    price0: float = 80.0,
    drift: float = 0.0002,
    vol: float = 0.02,
    with_curve: bool = True,
    backwardation: float = 0.01,
) -> MarketData:
    """A random-walk Brent front with coherent OHLC, plus an optional hand-built backwardated curve."""
    rng = np.random.default_rng(seed)
    days = business_dates(start, n_days)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="date")
    rets = rng.normal(drift, vol, size=n_days)
    close = price0 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[price0], close[:-1]]) * (1.0 + rng.normal(0, vol / 4, size=n_days))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0, vol / 3, size=n_days)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0, vol / 3, size=n_days)))

    prices = pd.DataFrame(index=idx, columns=PRICE_COLUMNS, dtype=float)
    prices["brent_front_open"] = open_
    prices["brent_front_high"] = high
    prices["brent_front_low"] = low
    prices["brent_front_close"] = close
    prices["brent_front_volume"] = rng.integers(20_000, 60_000, size=n_days).astype(float)
    prices["brent_cont"] = close
    prices["brent_spot"] = close * 1.01
    prices["wti_front_close"] = close - 4.0
    prices["wti_cont"] = close - 4.0
    prices["rbob_close"] = (close + 20.0) / 42.0
    prices["ho_close"] = (close + 30.0) / 42.0
    prices["ovx"] = 35.0 + rng.normal(0, 3, size=n_days)
    prices["vix"] = 18.0 + rng.normal(0, 2, size=n_days)
    prices["dxy"] = 103.0 + np.cumsum(rng.normal(0, 0.1, size=n_days))
    prices["spx"] = 5000.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.008, size=n_days)))
    prices["copper"] = 4.0 + np.cumsum(rng.normal(0, 0.01, size=n_days))
    prices["us10y"] = 4.2 + rng.normal(0, 0.05, size=n_days)
    prices["breakeven10y"] = 2.3 + rng.normal(0, 0.03, size=n_days)

    published = pd.Series([settlement_ts(d) for d in days], index=idx)

    curve = pd.DataFrame(index=idx, columns=[*CURVE_COLUMNS, "M1_code"], dtype=object)
    approx = pd.Series(False, index=idx)
    if with_curve:
        for i, d in enumerate(days):
            months = listed_months("BZ", d, 12, 3)
            front = close[i]
            for k in range(1, 13):
                curve.iloc[i, curve.columns.get_loc(f"M{k}")] = front * (1.0 - backwardation * (k - 1))
            y, m = months[0]
            from engine.core.calendar import contract_code

            curve.iloc[i, curve.columns.get_loc("M1_code")] = contract_code("BZ", y, m)

    md = MarketData(
        prices=prices,
        curve=curve,
        curve_approx=approx,
        published_at={"prices": published, "curve": published},
        meta={"synthetic": True, "prices_source": "synthetic"},
    )
    return md


def add_wpsr(md: MarketData, n_weeks: int = 60, seed: int = 11) -> MarketData:
    """Weekly WPSR rows indexed by publication timestamp (Wednesday 10:30 ET), period = previous Friday."""
    rng = np.random.default_rng(seed)
    days = [d.date() for d in pd.DatetimeIndex(md.prices.index)]
    wednesdays = [d for d in days if d.weekday() == 2][-n_weeks:]
    rows = []
    stocks = 430_000.0
    for d in wednesdays:
        stocks += float(rng.normal(0, 3000))
        rows.append(
            {
                "published_at": datetime.combine(d, datetime.min.time(), tzinfo=UTC).replace(hour=14, minute=30),
                "period": pd.Timestamp(d - timedelta(days=5)),
                "crude_stocks": stocks,
                "cushing_stocks": 25_000.0 + float(rng.normal(0, 500)),
                "gasoline_stocks": 220_000.0,
                "distillate_stocks": 115_000.0,
                "refinery_inputs": 16_000.0,
                "crude_production": 13_300.0,
                "crude_imports": 6_500.0,
                "crude_exports": 4_000.0,
                "gasoline_supplied": 8_800.0,
                "distillate_supplied": 3_900.0,
            }
        )
    md.wpsr = pd.DataFrame(rows).set_index("published_at") if rows else md.wpsr
    return md
