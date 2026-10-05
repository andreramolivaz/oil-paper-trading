"""Corporate-insider conviction in the oil complex, point-in-time by filing deadline.

Input: ``md.insider`` — SEC Form 4 transactions (see ``engine/data/adapters/insider.py``) indexed by
transaction date, with ``published_at`` set to the statutory two-business-day filing deadline. Every statistic
below is computed on rows whose ``published_at <= settlement_ts(date)``, so a transaction enters the score on
the day the market could first have read the filing, never on the day it happened.

The thesis: the people who run oil production know their own marginal cost, decline rates and hedge book. When
several of them buy their own stock with their own money, after tax, at a real price, they are expressing a
view on forward oil economics that no public series carries yet. It is weak and slow, and it is one of the
oldest documented anomalies in the literature (Lakonishok & Lee 2001; Cohen, Malloy & Pomorski 2012 on the
difference between routine and opportunistic insiders).

Four measured properties of the real data shape the formula:

1. **Compensation is not conviction.** Three quarters of the rows are grants, vesting, option exercises and
   tax withholding, nearly all printing ``share_price = 0``. Only ``open_market`` rows count.
2. **A 10% owner is not an insider for this purpose.** On Occidental, 146 of 258 open-market purchases are a
   fund accumulating a stake, with a median size of 17 M$ — a view on an equity, not on crude. Excluded.
3. **One trade must not be the score.** Sizes span four orders of magnitude, so each transaction is divided by
   that ticker's own trailing median open-market size and then clipped to ``CLIP``: a 500 M$ purchase and a
   5 M$ purchase both say "bought", and the score says how many insiders said it, not how rich one is.
4. **Purchases carry more than sales.** An insider sells for a house, a divorce or a diversification rule;
   there is only one reason to buy. Sales enter at ``SALE_WEIGHT``.

Columns
-------
``INSIDER_SCORE``    net conviction in [-1, +1]: the trailing-window net flow, ranked against its own 3-year
    history. Positive = insiders are net buyers by the standards of the last three years.
``INSIDER_BUY_RATIO``  purchases / (purchases + sales) by COUNT in the window, in [0, 1]. Scale-free, and the
    honest companion to a value-weighted score.
``INSIDER_N_TX``     qualifying transactions in the window. The strategy refuses to act below a floor: with
    nine open-market purchases per name per year, a window can legitimately be empty.
``INSIDER_BREADTH``  distinct tickers with at least one qualifying purchase in the window. One company's board
    buying is a company story; six companies' boards buying at once is an oil story.

``result.attrs["insider_universe"]`` records the tickers actually seen, and ``["insider_approx"]`` is always
True: ``published_at`` is inferred from the filing deadline, never observed.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from engine.data.market_data import MarketData
from engine.features import catalog as cat

#: Trailing window, in calendar days, over which transactions are accumulated. Form 4 flow is sparse: a month
#: is mostly empty, a quarter carries a readable signal.
WINDOW_DAYS = 90
#: Transactions needed before the score means anything at all.
MIN_TX = 6
#: Per-ticker normalised size is clipped here, so no single trade can dominate the basket.
CLIP = 3.0
#: Sales are informative, but much less than purchases.
SALE_WEIGHT = 0.4
#: Window used to rank the raw flow against its own history (3 years of trading days).
RANK_WINDOW = 756
MIN_RANK_OBS = 252
#: Trailing window used to learn each ticker's typical open-market transaction size.
SCALE_WINDOW = 400

COLUMNS: list[str] = [
    cat.INSIDER_SCORE,
    cat.INSIDER_BUY_RATIO,
    cat.INSIDER_N_TX,
    cat.INSIDER_BREADTH,
]


def _qualifying(insider: pd.DataFrame) -> pd.DataFrame:
    """Open-market transactions by officers and directors: the only rows that carry a view."""
    if insider.empty:
        return insider
    mask = insider["open_market"].astype(bool) & ~insider["ten_percent_owner"].astype(bool)
    return insider.loc[mask]


def _normalised_size(frame: pd.DataFrame) -> pd.Series:
    """Each transaction in units of its ticker's own typical size, clipped.

    Expanding (not full-sample) median: the scale a date uses is learned only from transactions published
    before it, so the normalisation cannot leak the future into the past.
    """
    # The transaction index carries duplicate dates, so everything here is POSITIONAL: label alignment would
    # silently scatter one ticker's values across another's rows.
    work = frame.reset_index(drop=True)
    out = pd.Series(np.nan, index=work.index, dtype=float)
    for _, rows in work.groupby("ticker", sort=False):
        value = rows["value_usd"].astype(float)
        # NO bfill: backfilling the scale would pull a LATER median onto an EARLIER transaction, which is
        # look-ahead and nothing else. Until a ticker has three prior transactions its scale is unknown, the
        # normalised size stays NaN and the row simply does not contribute. Honest beats complete.
        scale = value.abs().shift(1).rolling(SCALE_WINDOW, min_periods=3).median()
        safe = scale.where(scale > 0.0)
        out.iloc[list(rows.index)] = pd.Series((value / safe).to_numpy())
    return pd.Series(out.clip(-CLIP, CLIP).to_numpy(), index=frame.index)


def build(md: MarketData, index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Insider features on the Brent trading calendar."""
    dates = pd.DatetimeIndex(md.prices.index) if index is None else index
    empty = pd.DataFrame(np.nan, index=dates, columns=COLUMNS)
    empty[cat.INSIDER_N_TX] = 0.0
    empty[cat.INSIDER_BREADTH] = 0.0
    empty.attrs["insider_universe"] = []
    empty.attrs["insider_approx"] = True

    insider = getattr(md, "insider", None)
    if insider is None or len(insider) == 0:
        return empty
    rows = _qualifying(insider)
    if rows.empty:
        return empty

    # A TOTAL, DETERMINISTIC order. published_at is full of ties (every transaction on the same day shares a
    # filing deadline) and pandas' default sort is not stable, so the within-ticker sequence — and therefore
    # the trailing median that normalises each size — depended on the order rows happened to arrive in. The
    # same date then scored differently on a truncated sample, which is a look-ahead failure even though no
    # future value was read. Sorting on the data itself removes the ambiguity.
    rows = rows.sort_values(["published_at", "ticker", "executive", "side", "value_usd", "shares"], kind="mergesort")
    published = pd.to_datetime(rows["published_at"], utc=True)
    signed = _normalised_size(rows)
    is_buy = rows["side"].astype(str).str.upper().eq("A")
    weight = np.where(is_buy, 1.0, SALE_WEIGHT)
    # A disposal is a negative flow whatever the sign of its normalised size.
    flow = pd.Series(np.abs(signed.to_numpy()) * np.where(is_buy, 1.0, -1.0) * weight, index=rows.index)

    work = pd.DataFrame(
        {
            "published": published.to_numpy(),
            "flow": flow.to_numpy(),
            "buy": is_buy.to_numpy(),
            "ticker": rows["ticker"].to_numpy(),
        }
    ).dropna(subset=["flow"])
    if work.empty:
        return empty
    work = work.sort_values("published", kind="mergesort").reset_index(drop=True)

    # One pass over the trading calendar: for each date take the transactions ALREADY PUBLISHED inside the
    # trailing window. `searchsorted` keeps it O(n log n) instead of rebuilding a mask per day.
    pub = pd.DatetimeIndex(work["published"])
    asof = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    asof_utc = asof.tz_localize("UTC") if asof.tz is None else asof.tz_convert("UTC")
    # settlement_index gives calendar dates; a transaction counts once the filing deadline has passed.
    end = pub.searchsorted(asof_utc + pd.Timedelta(hours=23, minutes=59), side="right")
    start = pub.searchsorted(asof_utc - pd.Timedelta(days=WINDOW_DAYS), side="left")

    flows = work["flow"].to_numpy()
    buys = work["buy"].to_numpy()
    tickers = work["ticker"].to_numpy()
    cum = np.concatenate([[0.0], np.cumsum(flows)])
    cum_buy = np.concatenate([[0], np.cumsum(buys.astype(int))])

    raw = np.full(len(dates), np.nan)
    n_tx = np.zeros(len(dates))
    breadth = np.zeros(len(dates))
    buy_ratio = np.full(len(dates), np.nan)
    for i, (lo, hi) in enumerate(zip(start, end, strict=True)):
        count = int(hi - lo)
        n_tx[i] = count
        if count <= 0:
            continue
        raw[i] = float(cum[hi] - cum[lo])
        n_buy = int(cum_buy[hi] - cum_buy[lo])
        buy_ratio[i] = n_buy / count
        if n_buy:
            window_buy = buys[lo:hi]
            breadth[i] = float(len(set(tickers[lo:hi][window_buy])))

    raw_series = pd.Series(raw, index=dates)
    # Rank against the series' own history: the absolute flow has no natural unit, its percentile does.
    rank = raw_series.rolling(RANK_WINDOW, min_periods=MIN_RANK_OBS).rank(pct=True)
    score = (rank - 0.5) * 2.0
    score = score.where(pd.Series(n_tx, index=dates) >= MIN_TX)

    out = pd.DataFrame(index=dates)
    out[cat.INSIDER_SCORE] = score
    out[cat.INSIDER_BUY_RATIO] = pd.Series(buy_ratio, index=dates)
    out[cat.INSIDER_N_TX] = pd.Series(n_tx, index=dates)
    out[cat.INSIDER_BREADTH] = pd.Series(breadth, index=dates)
    out.attrs["insider_universe"] = sorted(set(tickers.tolist()))
    out.attrs["insider_approx"] = True
    return out


def latest_detail(md: MarketData, asof: datetime, limit: int = 8) -> list[dict[str, object]]:
    """The transactions behind the current score, for the dashboard: every number must be traceable."""
    insider = getattr(md, "insider", None)
    if insider is None or len(insider) == 0:
        return []
    rows = _qualifying(insider)
    if rows.empty:
        return []
    asof_ts = pd.Timestamp(asof)
    if asof_ts.tzinfo is None:
        asof_ts = asof_ts.tz_localize("UTC")
    published = pd.to_datetime(rows["published_at"], utc=True)
    window = rows.loc[(published <= asof_ts) & (published >= asof_ts - pd.Timedelta(days=WINDOW_DAYS))]
    if window.empty:
        return []
    window = window.assign(published=published.loc[window.index]).sort_values("published", ascending=False)
    return [
        {
            "ticker": str(r.ticker),
            "role": str(r.role),
            "side": "acquisto" if str(r.side).upper() == "A" else "vendita",
            "shares": float(r.shares),
            "price": float(r.price),
            "value_usd": float(r.value_usd),
            "transaction_date": str(pd.Timestamp(str(idx)).date()),
            "published_at": pd.Timestamp(r.published).isoformat(),
        }
        for idx, r in window.head(limit).iterrows()
    ]


__all__ = ["COLUMNS", "MIN_TX", "WINDOW_DAYS", "build", "latest_detail"]
