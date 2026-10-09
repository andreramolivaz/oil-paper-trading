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


# ----------------------------------------------------------------------------------------------- desk helpers
def us_business_dates(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if is_business_day(d, "US"):
            out.append(d)
        d += timedelta(days=1)
    return out


SYNTHETIC_PAST_DAYS = 900  # enough for the slowest skew lookback (365) plus 500 days of its own history


def make_vehicle_series(
    vehicle: str = "BNO",
    n_days: int = 420,
    start: date = date(2024, 1, 2),
    seed: int = 3,
    price0: float = 30.0,
    drift: float = 0.0006,
    vol: float = 0.02,
    slope_level: float = 0.10,
):
    """SYNTHETIC daily series of one desk vehicle (a seeded random walk), with the forecasts computed by the
    engine's own signal functions. For a futures vehicle the bar symbol is the contract a book would hold."""
    from engine.desk import signals as sg
    from engine.desk.data import VehicleSeries, held_codes, known_before

    rng = np.random.default_rng(seed)
    days = us_business_dates(start, n_days)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="date")
    rets = pd.Series(rng.normal(drift, vol, size=n_days), index=idx)
    close = price0 * (1.0 + rets).cumprod()
    open_ = close.shift(1).fillna(price0) * (1.0 + rng.normal(0, vol / 4, size=n_days))
    bars = pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0, vol / 3, size=n_days))),
            "low": np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0, vol / 3, size=n_days))),
            "close": close,
        },
        index=idx,
    )
    bars["symbol"] = held_codes("CL", idx).to_numpy() if vehicle == "MCL" else vehicle
    slope = pd.Series(slope_level + np.cumsum(rng.normal(0, 0.004, size=n_days)), index=idx)
    forecasts = pd.DataFrame(index=idx)
    forecasts["trend"] = sg.trend_forecast(rets)
    forecasts["accel"] = sg.accel_forecast(rets)
    # the skew needs years of its own history and the macro sleeves a year of another market's closes: both
    # get a synthetic past that ends where the vehicle's series begins (separate generators, so the vehicle's
    # own prices are the same whatever is added here)
    past = pd.bdate_range(end=idx[0] - pd.Timedelta(days=1), periods=SYNTHETIC_PAST_DAYS)
    extra = np.random.default_rng(seed + 1000)
    long_returns = pd.concat([pd.Series(extra.normal(0.0, vol, size=len(past)), index=past), rets])
    forecasts["skew"] = sg.skew_forecast(long_returns).reindex(idx)
    forecasts["carry"] = sg.carry_forecast(slope)
    forecasts["carry_momentum"] = sg.carry_momentum_forecast(slope)
    for name, inverse in (("copper", False), ("dollar", True)):
        walk = extra.normal(0.0, 0.012, size=len(past) + n_days)
        closes = pd.Series(4.0 * np.exp(np.cumsum(walk)), index=past.append(idx))
        forecasts[name] = known_before(sg.macro_trend_forecast(closes, inverse=inverse), idx)
    forecasts = forecasts[list(sg.SLEEVES)]
    forecasts["combined"] = sg.combine({name: forecasts[name] for name in sg.SLEEVES})
    return VehicleSeries(
        vehicle=vehicle,
        bars=bars,
        returns=rets,
        return_source=pd.Series("sintetico", index=idx, dtype="object"),
        forecasts=forecasts,
        vol=sg.ew_vol(rets),
        slope=slope,
        slope_approx=pd.Series(False, index=idx),
        slope_pair=pd.Series("sintetico", index=idx, dtype="object"),
    )


def make_desk_data(n_days: int = 420, start: date = date(2024, 1, 2), seed: int = 3):
    """SYNTHETIC DeskData with both vehicles and no intraday bars (tests add the bars they need)."""
    from engine.desk.data import DeskData

    data = DeskData(meta={"synthetic": True, "missing": [], "notes": []})
    data.series["BNO"] = make_vehicle_series("BNO", n_days, start, seed, price0=30.0)
    data.series["MCL"] = make_vehicle_series("MCL", n_days, start, seed + 1, price0=70.0)
    data.contracts = {"CL": pd.DataFrame(), "BZ": pd.DataFrame()}
    data.legs["SCO"] = inverse_fund_bars(data.series["BNO"].bars)
    return data


def inverse_fund_bars(bars: pd.DataFrame, multiplier: float = -2.0, price0: float = 20.0) -> pd.DataFrame:
    """SYNTHETIC daily bars of an inverse fund: every day it returns exactly ``multiplier`` times the fund in
    ``bars``, overnight and intraday alike, so what a position in it should be worth is known to the cent."""
    prev = bars["close"].shift(1).fillna(bars["open"].iloc[0])
    overnight = multiplier * (bars["open"] / prev - 1.0)
    session = multiplier * (bars["close"] / bars["open"] - 1.0)
    close = price0 * ((1.0 + overnight) * (1.0 + session)).cumprod()
    open_ = close / (1.0 + session)
    out = pd.DataFrame({"open": open_, "close": close}, index=bars.index)
    out["high"] = out[["open", "close"]].max(axis=1)
    out["low"] = out[["open", "close"]].min(axis=1)
    return out[["open", "high", "low", "close"]]


def half_hour_bars(day: date, price: float, n: int = 13, first: tuple[int, int] = (13, 30), step: float = 0.001):
    """SYNTHETIC 30-minute bars of one session, indexed by bar START in UTC. Each bar opens where the previous
    one closed and moves by ``step`` (relative), so every open and close is known exactly."""
    start = datetime(day.year, day.month, day.day, first[0], first[1], tzinfo=UTC)
    rows = []
    last = price
    for _ in range(n):
        o = last
        c = o * (1.0 + step)
        rows.append({"open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1000.0})
        last = c
    index = pd.DatetimeIndex([start + timedelta(minutes=30 * i) for i in range(n)], name="ts")
    return pd.DataFrame(rows, index=index)


def make_desk_raw_frames(n: int = 560, seed: int = 21, end: date | None = None) -> dict[str, pd.DataFrame]:
    """SYNTHETIC raw tables for the desk's data builder, keyed by raw-store entry. No per-contract tables: the
    WTI return comes from the EIA-style contract 1..4 columns and the Brent fund's slope from the WTI curve,
    which is the configuration the long history actually has. Copper and the dollar index are two more random
    walks on the same dates; with the default 560 days the skew sleeve has too little history to speak (it
    needs about 870), which is the state a young archive is really in - pass a larger ``n`` to have it."""
    rng = np.random.default_rng(seed)
    idx = (
        pd.bdate_range("2021-01-04", periods=n, name="date")
        if end is None
        else pd.DatetimeIndex(
            [pd.Timestamp(d) for d in us_business_dates(end - timedelta(days=int(n * 1.6)), 4 * n) if d <= end][-n:],
            name="date",
        )
    )
    wti = 60.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, size=n)))
    spread = 1.0 + 0.03 * np.sin(np.arange(n) / 40.0)  # contract 1 over contract 4: backwardation and contango
    eia = pd.DataFrame(
        {"RCLC1": wti, "RCLC2": wti / spread ** (1 / 3), "RCLC3": wti / spread ** (2 / 3), "RCLC4": wti / spread},
        index=idx,
    )
    ohlc = pd.DataFrame({"open": wti * 0.999, "high": wti * 1.01, "low": wti * 0.99, "close": wti}, index=idx)
    bno = 20.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, size=n)))
    fund = pd.DataFrame(
        {"open": bno * 0.998, "high": bno * 1.01, "low": bno * 0.99, "close": bno, "adjclose": bno}, index=idx
    )
    ovx = pd.DataFrame({"value": 35.0 + rng.normal(0, 2, size=n)}, index=idx)
    # drawn last, so that the tables above are the same numbers they were before these two existed
    copper = 4.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.013, size=n)))
    dollar = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, size=n)))
    return {
        "wti_curve_hist": eia,
        "wti_front": ohlc,
        "brent_front": pd.DataFrame({"close": wti + 4.0}, index=idx),
        "bno_daily": fund,
        "uso_daily": fund * 2.0,
        "ovx": ovx,
        "copper_daily": pd.DataFrame({"close": copper}, index=idx),
        "dxy_daily": pd.DataFrame({"close": dollar}, index=idx),
    }


def save_raw_frame(
    store, entry: str, frame: pd.DataFrame, adapter: str = "sintetico", fetched_at: datetime | None = None
) -> None:
    """Archive ``frame`` as a snapshot of ``entry``. By default it is stamped as downloaded at noon (UTC) of the
    day AFTER its last row: a table read then holds only rows a later day has confirmed, which is what the
    desk's data layer takes as final for the markets it reads as of the previous close
    (``engine.desk.data.final_rows``). Pass ``fetched_at`` to archive a table as it was read at a given moment."""
    from engine.data.base import FetchResult

    frame = frame.copy()
    frame["published_at"] = pd.Timestamp("2026-01-01", tz="UTC")
    if fetched_at is None:
        dates = frame.index if isinstance(frame.index, pd.DatetimeIndex) else None
        for column in ("date", "ts"):
            if dates is None and column in frame.columns:
                dates = pd.DatetimeIndex(pd.to_datetime(frame[column], utc=True))
        last = pd.Timestamp("2026-01-01") if dates is None or len(dates) == 0 else pd.Timestamp(dates.max())
        last = last.tz_localize(None) if last.tzinfo is not None else last
        fetched_at = (last.normalize() + pd.Timedelta(days=1, hours=12)).to_pydatetime().replace(tzinfo=UTC)
    store.save(entry, adapter, FetchResult(source=adapter, frame=frame, fetched_at=fetched_at))
