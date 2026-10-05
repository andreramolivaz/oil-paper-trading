"""FeatureBuilder protocol: the contract the trading session depends on.

`engine.features.builder.FullFeatureBuilder` is the production implementation. Tests may substitute a lighter
builder as long as it returns a daily frame whose columns come from `engine.features.catalog` and whose rows
only use data published at or before `asof` (point-in-time).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from engine.data.market_data import MarketData


@runtime_checkable
class FeatureBuilder(Protocol):
    """Builds the daily feature frame used by the regime model, the strategies and the forecasters."""

    def build(self, md: MarketData, asof: datetime) -> pd.DataFrame:
        """Daily frame indexed by tz-naive trading dates <= the trading date of `asof`, catalog columns."""
        ...
