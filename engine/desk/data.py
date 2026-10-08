"""The clean series the desk trades and reads, assembled from the raw snapshot store.

Three things are built here, and each one exists because the obvious series was wrong:

**The investable WTI return.** A futures position is never one continuous price: it is a sequence of contracts.
``held_contract`` says which one a book holds on a day (the front, left ``roll_days`` business days before its
last trading day, so nothing is ever carried into an expiry - on 2020-04-20 that is the difference between
-306 % and an ordinary bad day). The return of that contract from one close to the next comes, in order, from

1. the per-contract table (``cl_contracts``): exact, for contracts still listed when the engine first saw them;
2. the EIA NYMEX contract 1 and 2 settlements, 1983 -> 2024-04-05: exact, including across the roll;
3. Yahoo's front series ``CL=F`` on the days it is unambiguously the held contract, and on the handful of days
   a month it is not (the roll window and the day after an expiry) the return of the ``USO`` fund, which holds
   the same contract. Those days are flagged ``approx``.

**The curve slope.** Front minus a far December contract, over the far price, per year between the two
expiries: positive is backwardation. Carry and carry-momentum forecasts are computed on the slope against ONE
far contract at a time and only then stitched together, because a 20-day average that straddles two different
contract pairs compares numbers that are not the same thing. Before the per-contract history starts the WTI
contract 2 minus contract 4 spread from EIA stands in; for the Brent fund that is a cross-market proxy and is
flagged ``approx``.

**The bars.** ``BNO`` has real opens, highs and lows. The WTI backtest bars are an index of the investable
return, anchored to the last real close, with each day's open/high/low taken from ``CL=F`` in proportion to its
close; before ``CL=F`` exists (August 2000) open = high = low = close, which makes a market order fill at the
NEXT day's close - a full day late, on purpose.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from engine.core.calendar import add_business_days, contract_code, expiry_for, parse_contract_code
from engine.core.config import Settings
from engine.data.raw_store import OBSERVED_AT_COLUMN, SOURCE_COLUMN, RawStore
from engine.desk import signals as sg

log = logging.getLogger(__name__)

ROLL_DAYS = 5  # business days before the last trading day on which a futures book leaves the front
FAR_MIN_DAYS = 300  # the far contract of the slope is the nearest December at least this far past the front
MARKET_OF_ROOT = {"CL": "US", "BZ": "ICE"}
EIA_COLUMNS = ("RCLC1", "RCLC2", "RCLC3", "RCLC4")
META = (OBSERVED_AT_COLUMN, SOURCE_COLUMN)
RATIO_BOUNDS = (0.5, 2.0)  # an intraday open/close ratio outside this is a data artefact (negative WTI, 2020)


@dataclass
class VehicleSeries:
    """Everything a book needs about one vehicle, daily, index = tz-naive trading date."""

    vehicle: str
    bars: pd.DataFrame  # open, high, low, close, symbol (the contract code for futures)
    returns: pd.Series  # investable close-to-close return
    return_source: pd.Series  # where each day's return came from
    forecasts: pd.DataFrame  # trend, carry, carry_momentum, combined
    vol: pd.Series  # annualised, floored
    slope: pd.Series  # annualised curve slope shown in the terminal
    slope_approx: pd.Series  # True where the slope is a cross-market proxy
    slope_pair: pd.Series  # which contracts the slope was measured on, as text

    def last_day(self) -> pd.Timestamp | None:
        return None if self.bars.empty else pd.Timestamp(self.bars.index[-1])


@dataclass
class DeskData:
    series: dict[str, VehicleSeries] = field(default_factory=dict)
    ovx: pd.Series = field(default_factory=lambda: pd.Series(dtype="float64"))
    hormuz: pd.DataFrame = field(default_factory=pd.DataFrame)
    intraday: dict[str, pd.DataFrame] = field(default_factory=dict)  # vehicle (or "BZ") -> 30-minute bars
    contracts: dict[str, pd.DataFrame] = field(default_factory=dict)  # root -> wide close table (date x code)
    meta: dict[str, Any] = field(default_factory=dict)

    def truncate(self, day: pd.Timestamp) -> DeskData:
        """Rows up to and including ``day`` (used by the no-look-ahead test and by nothing else)."""
        out = DeskData(ovx=self.ovx.loc[:day], hormuz=self.hormuz, meta=dict(self.meta))
        for key, s in self.series.items():
            out.series[key] = VehicleSeries(
                vehicle=s.vehicle,
                bars=s.bars.loc[:day],
                returns=s.returns.loc[:day],
                return_source=s.return_source.loc[:day],
                forecasts=s.forecasts.loc[:day],
                vol=s.vol.loc[:day],
                slope=s.slope.loc[:day],
                slope_approx=s.slope_approx.loc[:day],
                slope_pair=s.slope_pair.loc[:day],
            )
        return out


# ----------------------------------------------------------------------------------------------- calendar
def roll_day(root: str, year: int, month: int, roll_days: int = ROLL_DAYS) -> date:
    """Last day a book holds the contract: ``roll_days`` business days before its last trading day."""
    return add_business_days(expiry_for(root, year, month), -roll_days, MARKET_OF_ROOT.get(root, "US"))


def contract_schedule(root: str, start: date, end: date, roll_days: int = ROLL_DAYS) -> pd.DataFrame:
    """One row per contract month covering [start, end]: code, expiry, roll_day (sorted by expiry)."""
    rows = []
    year, month = start.year, start.month
    # start two months back: the contract held on `start` may be dated two months ahead (ICE Brent)
    idx = year * 12 + (month - 1) - 1
    while True:
        y, m = idx // 12, idx % 12 + 1
        expiry = expiry_for(root, y, m)
        rows.append({"code": contract_code(root, y, m), "expiry": expiry, "roll_day": roll_day(root, y, m, roll_days)})
        if expiry > end + timedelta(days=70):
            break
        idx += 1
    return pd.DataFrame(rows).sort_values("expiry").reset_index(drop=True)


def held_contract(root: str, day: date, roll_days: int = ROLL_DAYS) -> str:
    """The contract whose return a book earns on ``day``: the first one not yet past its roll day."""
    schedule = contract_schedule(root, day - timedelta(days=5), day, roll_days)
    for code, last_day in zip(schedule["code"], schedule["roll_day"], strict=True):
        if last_day >= day:
            return str(code)
    raise ValueError(f"no contract found for {root} on {day}")  # pragma: no cover - the schedule looks ahead


def held_codes(root: str, index: pd.DatetimeIndex, roll_days: int = ROLL_DAYS) -> pd.Series:
    """``held_contract`` for every date of ``index`` (vectorised over the schedule)."""
    if len(index) == 0:
        return pd.Series(dtype="object", index=index)
    schedule = contract_schedule(root, index[0].date(), index[-1].date(), roll_days)
    rolls = pd.DatetimeIndex(pd.to_datetime(schedule["roll_day"]))
    pos = rolls.searchsorted(index, side="left")  # first contract with roll_day >= t
    pos = np.minimum(pos, len(schedule) - 1)
    return pd.Series(schedule["code"].to_numpy()[pos], index=index, dtype="object")


def front_codes(root: str, index: pd.DatetimeIndex) -> pd.Series:
    """The exchange front on each date (held until its last trading day): what ``CL=F`` and EIA C1 quote."""
    return held_codes(root, index, roll_days=0)


def expiry_of(code: str) -> date:
    root, year, month = parse_contract_code(code)
    return expiry_for(root, year, month)


# ----------------------------------------------------------------------------------------------- raw loading
def _latest(store: RawStore, entry: str, asof: datetime | None = None) -> pd.DataFrame | None:
    """Newest snapshot of ``entry`` from whichever adapter has one (the config order is not needed here: these
    tables have a single source each, and a snapshot written by any adapter is better than none)."""
    best: pd.DataFrame | None = None
    best_seen: pd.Timestamp | None = None
    for adapter in store.keys(entry):
        frame = store.load_latest(entry, adapter) if asof is None else store.load_asof(entry, adapter, asof)
        if frame is None or frame.empty:
            continue
        seen = pd.Timestamp(frame[OBSERVED_AT_COLUMN].max()) if OBSERVED_AT_COLUMN in frame.columns else None
        if best is None or (seen is not None and (best_seen is None or seen > best_seen)):
            best, best_seen = frame, seen
    if best is None:
        return None
    return best.drop(columns=[c for c in META if c in best.columns])


def _daily(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(out.index, errors="coerce"))
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    out.index = pd.DatetimeIndex(idx.normalize(), name="date")
    out = out[out.index.notna()]
    return out[~out.index.duplicated(keep="last")].sort_index()


def contracts_wide(frame: pd.DataFrame | None, column: str = "close") -> pd.DataFrame:
    """Long per-contract table (date, code, close, ...) -> wide table of ``column``, index date, one column per
    contract code. Rows with a null value are dropped before pivoting: a missing bar is missing, never zero."""
    if frame is None or frame.empty or "code" not in frame.columns or column not in frame.columns:
        return pd.DataFrame()
    work = frame[["date", "code", column]].dropna(subset=[column]).copy()
    work["date"] = pd.to_datetime(work["date"]).dt.normalize()
    work = work.drop_duplicates(subset=["date", "code"], keep="last")
    wide = work.pivot(index="date", columns="code", values=column).sort_index()
    wide.index = pd.DatetimeIndex(wide.index, name="date")
    return wide.astype("float64")


# ----------------------------------------------------------------------------------------------- WTI return
def investable_futures_returns(
    root: str,
    contracts: pd.DataFrame,
    eia: pd.DataFrame,
    front: pd.Series,
    fund: pd.Series,
    roll_days: int = ROLL_DAYS,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Close-to-close return of the held contract, its source per day, and the held contract code.

    ``contracts``: wide close table by contract code. ``eia``: contract 1..4 settlements. ``front``: the
    exchange-front close (``CL=F``). ``fund``: the adjusted close of the fund that holds the same contract.
    """
    index = pd.DatetimeIndex([])
    for obj in (contracts, eia, front, fund):
        if obj is not None and len(obj):
            index = index.union(pd.DatetimeIndex(obj.index))
    index = pd.DatetimeIndex(index.sort_values(), name="date")
    if len(index) == 0:
        empty = pd.Series(dtype="float64")
        return empty, pd.Series(dtype="object"), pd.Series(dtype="object")
    held = held_codes(root, index, roll_days)
    exchange_front = front_codes(root, index)
    out = pd.Series(np.nan, index=index, dtype="float64")
    source = pd.Series("", index=index, dtype="object")

    # 1. per-contract table: the held contract's own close on t and on the previous row of the calendar
    if not contracts.empty:
        wide = contracts.reindex(index)
        prev_wide = wide.shift(1)
        for code in pd.unique(held.dropna()):
            if code not in wide.columns:
                continue
            mask = (held == code).to_numpy()
            ret = (wide[code] / prev_wide[code] - 1.0).where(prev_wide[code] > 0)
            take = mask & ret.notna().to_numpy()
            out[take] = ret[take]
            source[take] = "contratto"

    # 2. EIA contract 1 / contract 2 settlements (exact, across the roll)
    if eia is not None and not eia.empty and {"RCLC1", "RCLC2"} <= set(eia.columns):
        e = eia.reindex(index)
        c1, c2 = e["RCLC1"], e["RCLC2"]
        both = c1.notna() & c2.notna()
        # Returns are taken between consecutive EIA rows, never across a gap in the EIA calendar.
        prev_c1 = c1.where(both).shift(1)
        prev_c2 = c2.where(both).shift(1)
        prev_front = exchange_front.shift(1)
        in_window = held != exchange_front  # the book already holds the next contract (= EIA contract 2)
        after_expiry = (exchange_front != prev_front) & prev_front.notna()  # contract 1 is a new contract today
        # each return is valid only if the two prices IT uses are positive (contract 1 settled at -37 $ on
        # 2020-04-20: that must not void the contract 2 return of the book, which had already rolled)
        r1 = (c1 / prev_c1 - 1.0).where((c1 > 0) & (prev_c1 > 0))
        r2 = (c2 / prev_c2 - 1.0).where((c2 > 0) & (prev_c2 > 0))
        r_cont = (c1 / prev_c2 - 1.0).where((c1 > 0) & (prev_c2 > 0))  # today's contract 1 was yesterday's 2
        eia_ret = r1.where(~in_window, r2).where(~after_expiry, r_cont)
        take = out.isna() & eia_ret.notna()
        out[take] = eia_ret[take]
        source[take] = "eia"

    # 3. Yahoo front where it IS the held contract on both days; the fund on the days it is not
    if front is not None and len(front):
        f = front.reindex(index)
        prev_f = f.shift(1)
        same_contract = (held == exchange_front) & (exchange_front == exchange_front.shift(1))
        front_ret = (f / prev_f - 1.0).where(same_contract & (prev_f > 0) & (f > 0))
        take = out.isna() & front_ret.notna()
        out[take] = front_ret[take]
        source[take] = "front"
    if fund is not None and len(fund):
        u = fund.reindex(index)
        fund_ret = (u / u.shift(1) - 1.0).where(u.shift(1) > 0)
        take = out.isna() & fund_ret.notna()
        out[take] = fund_ret[take]
        source[take] = "fondo (approx)"
    keep = out.notna()
    return out[keep], source[keep], held[keep]


# ----------------------------------------------------------------------------------------------- slope
def far_december(front_code: str, available: list[str], min_days: int = FAR_MIN_DAYS) -> str | None:
    """Nearest December contract of the same root at least ``min_days`` past the front's last trading day."""
    root = parse_contract_code(front_code)[0]
    front_expiry = expiry_of(front_code)
    best: tuple[date, str] | None = None
    for code in available:
        try:
            r, _y, m = parse_contract_code(code)
        except (KeyError, ValueError):
            continue
        if r != root or m != 12:
            continue
        exp = expiry_of(code)
        if (exp - front_expiry).days < min_days:
            continue
        if best is None or exp < best[0]:
            best = (exp, code)
    return None if best is None else best[1]


def slope_against(front_price: pd.Series, front_code: pd.Series, far_close: pd.Series, far_code: str) -> pd.Series:
    """Annualised slope between the front and one far contract: (front - far) / far / years between expiries.

    NaN where either price is missing or the far contract is closer than ``FAR_MIN_DAYS`` to the front.
    """
    index = front_price.index
    far = far_close.reindex(index)
    far_expiry = pd.Timestamp(expiry_of(far_code))
    front_expiry = pd.DatetimeIndex([pd.Timestamp(expiry_of(c)) if isinstance(c, str) else pd.NaT for c in front_code])
    days = pd.Series((far_expiry - front_expiry).days, index=index, dtype="float64")
    slope = (front_price - far) / far / (days / 365.25)
    return slope.where((days >= FAR_MIN_DAYS) & (far > 0) & (front_price > 0))


def curve_forecasts(
    front_price: pd.Series,
    contracts: pd.DataFrame,
    root: str,
    fallback_slope: pd.Series | None = None,
    fallback_approx: bool = False,
    fallback_label: str = "",
) -> pd.DataFrame:
    """Carry and carry-momentum forecasts plus the slope they came from.

    Columns: carry, carry_momentum, slope, slope_approx (bool), slope_pair (text). The forecasts are computed on
    one contract pair at a time and stitched at the forecast level (module docstring).
    """
    index = pd.DatetimeIndex(front_price.index)
    out = pd.DataFrame(
        {
            "carry": np.nan,
            "carry_momentum": np.nan,
            "slope": np.nan,
            "slope_approx": False,
            "slope_pair": "",
        },
        index=index,
    )
    out["slope_pair"] = out["slope_pair"].astype("object")
    fronts = front_codes(root, index)
    decembers = sorted(
        (c for c in contracts.columns if c.startswith(root) and len(c) == len(root) + 3 and c[-3] == "Z"),
        key=expiry_of,
    )
    if decembers:
        # the designated far contract of each day: nearest December far enough that has a close that day
        designated = pd.Series("", index=index, dtype="object")
        per_far: dict[str, pd.DataFrame] = {}
        for far in decembers:
            slope = slope_against(front_price, fronts, contracts[far], far)
            if slope.notna().sum() == 0:
                continue
            per_far[far] = pd.DataFrame(
                {
                    "slope": slope,
                    "carry": sg.carry_forecast(slope),
                    "carry_momentum": sg.carry_momentum_forecast(slope),
                }
            )
        for far in decembers:  # nearest first: a nearer usable contract wins
            if far not in per_far:
                continue
            usable = per_far[far]["slope"].notna() & (designated == "")
            designated[usable] = far
        for far, frame in per_far.items():
            mask = (designated == far).to_numpy()
            if not mask.any():
                continue
            for col in ("slope", "carry", "carry_momentum"):
                out.loc[mask, col] = frame.loc[mask, col]
            out.loc[mask, "slope_pair"] = [f"{f}-{far}" for f in fronts[mask]]
    if fallback_slope is not None and len(fallback_slope):
        fb = fallback_slope.reindex(index)
        fb_frame = pd.DataFrame(
            {"slope": fb, "carry": sg.carry_forecast(fb), "carry_momentum": sg.carry_momentum_forecast(fb)}
        )
        # A day is taken from the fallback only when the own-curve slope itself is missing; the carry-momentum
        # of the first 20 own-curve days (still warming up) is taken from the fallback forecast.
        need_slope = out["slope"].isna() & fb.notna()
        out.loc[need_slope, "slope"] = fb[need_slope]
        out.loc[need_slope, "carry"] = fb_frame.loc[need_slope, "carry"]
        out.loc[need_slope, "slope_approx"] = fallback_approx
        out.loc[need_slope, "slope_pair"] = fallback_label
        need_cm = out["carry_momentum"].isna() & fb_frame["carry_momentum"].notna()
        out.loc[need_cm, "carry_momentum"] = fb_frame.loc[need_cm, "carry_momentum"]
    return out


# ----------------------------------------------------------------------------------------------- bars
def index_bars(returns: pd.Series, anchor_close: float, ohlc: pd.DataFrame | None, symbols: pd.Series) -> pd.DataFrame:
    """Bars of an investable index anchored so that its last close equals ``anchor_close``.

    Open/high/low come from ``ohlc`` (the exchange front) in proportion to its own close where that ratio is
    sane; elsewhere open = high = low = close.
    """
    index = total_index(returns)
    level = index / index.iloc[-1] * anchor_close
    bars = pd.DataFrame({"open": level, "high": level, "low": level, "close": level}, index=returns.index)
    if ohlc is not None and not ohlc.empty and {"open", "high", "low", "close"} <= set(ohlc.columns):
        o = ohlc.reindex(returns.index)
        lo, hi = RATIO_BOUNDS
        for col in ("open", "high", "low"):
            ratio = (o[col] / o["close"]).where((o["close"] > 0) & (o[col] > 0))
            ratio = ratio.where((ratio > lo) & (ratio < hi))
            bars[col] = (level * ratio).where(ratio.notna(), level)
        bars["high"] = bars[["open", "high", "low", "close"]].max(axis=1)
        bars["low"] = bars[["open", "high", "low", "close"]].min(axis=1)
    bars["symbol"] = symbols.reindex(returns.index).to_numpy()
    return bars


def total_index(returns: pd.Series) -> pd.Series:
    return sg.total_return_index(returns)


def _forecast_frame(returns: pd.Series, curve: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(index=returns.index)
    frame["trend"] = sg.trend_forecast(returns)
    frame["carry"] = curve["carry"].reindex(returns.index)
    frame["carry_momentum"] = curve["carry_momentum"].reindex(returns.index)
    frame["combined"] = sg.combine({name: frame[name] for name in sg.SLEEVES})
    return frame


# ----------------------------------------------------------------------------------------------- public API
def build_desk_data(store: RawStore, settings: Settings | None = None, asof: datetime | None = None) -> DeskData:
    """Assemble every series the desk needs from the raw snapshots. Missing tables degrade the result (a
    vehicle without prices is simply absent); nothing is filled in."""
    del settings  # reserved: every table here has one source, the config order is not needed
    data = DeskData(meta={"built_at": datetime.now(tz=UTC), "asof": asof, "missing": [], "notes": []})
    missing: list[str] = data.meta["missing"]

    def load(entry: str) -> pd.DataFrame | None:
        frame = _latest(store, entry, asof)
        if frame is None or frame.empty:
            missing.append(entry)
            return None
        return frame

    eia_raw = load("wti_curve_hist")
    eia = _daily(eia_raw)
    eia = eia[[c for c in EIA_COLUMNS if c in eia.columns]].astype("float64") if not eia.empty else pd.DataFrame()
    eia_slope = (
        ((eia["RCLC2"] - eia["RCLC4"]) / eia["RCLC4"] * 6.0).dropna()
        if {"RCLC2", "RCLC4"} <= set(eia.columns)
        else pd.Series(dtype="float64")
    )
    cl_contracts = contracts_wide(load("cl_contracts"))
    bz_contracts = contracts_wide(load("bz_contracts"))
    data.contracts = {"CL": cl_contracts, "BZ": bz_contracts}
    cl_front = _daily(load("wti_front"))
    bz_front = _daily(load("brent_front"))
    uso = _daily(load("uso_daily"))
    bno = _daily(load("bno_daily"))

    # ---- BNO: the fund is the series ---------------------------------------------------------------------
    if not bno.empty and "close" in bno.columns:
        price = bno["adjclose"] if "adjclose" in bno.columns and bno["adjclose"].notna().any() else bno["close"]
        price = price.astype("float64").dropna()
        returns = price.pct_change().dropna()
        bars = bno.reindex(returns.index)[["open", "high", "low", "close"]].astype("float64")
        bars = bars.where(bars > 0)
        for col in ("open", "high", "low"):
            bars[col] = bars[col].where(bars[col].notna(), bars["close"])
        bars["symbol"] = "BNO"
        front_price = (
            bz_front["close"].astype("float64").dropna()
            if not bz_front.empty and "close" in bz_front.columns
            else pd.Series(dtype="float64")
        )
        curve = curve_forecasts(
            front_price,
            bz_contracts,
            "BZ",
            fallback_slope=eia_slope,
            fallback_approx=True,
            fallback_label="WTI C2-C4 (proxy)",
        )
        curve = curve.reindex(curve.index.union(returns.index)).sort_index()
        # The curve is known at the futures settlement, before the fund's close: carry it to the fund's dates
        # with a short forward fill (holidays differ between London and New York), never beyond three rows.
        for col in ("carry", "carry_momentum", "slope"):
            curve[col] = curve[col].ffill(limit=3)
        curve["slope_pair"] = curve["slope_pair"].replace("", np.nan).ffill(limit=3).fillna("")
        curve["slope_approx"] = curve["slope_approx"].astype("boolean").ffill(limit=3).fillna(False).astype(bool)
        curve = curve.reindex(returns.index)
        data.series["BNO"] = VehicleSeries(
            vehicle="BNO",
            bars=bars,
            returns=returns,
            return_source=pd.Series("fondo", index=returns.index, dtype="object"),
            forecasts=_forecast_frame(returns, curve),
            vol=sg.ew_vol(returns),
            slope=curve["slope"],
            slope_approx=curve["slope_approx"],
            slope_pair=curve["slope_pair"],
        )
    else:
        data.meta["notes"].append("BNO: nessun prezzo giornaliero archiviato")

    # ---- MCL: the held WTI contract -----------------------------------------------------------------------
    front_close = (
        cl_front["close"].astype("float64").dropna()
        if not cl_front.empty and "close" in cl_front.columns
        else pd.Series(dtype="float64")
    )
    fund_close = pd.Series(dtype="float64")
    if not uso.empty:
        col = "adjclose" if "adjclose" in uso.columns and uso["adjclose"].notna().any() else "close"
        fund_close = uso[col].astype("float64").dropna()
    returns, source, held = investable_futures_returns("CL", cl_contracts, eia, front_close, fund_close)
    if len(returns) > 300:
        anchor = _anchor_close(held, cl_contracts, front_close)
        bars = index_bars(returns, anchor, cl_front if not cl_front.empty else None, held)
        slope_front = front_close if len(front_close) else pd.Series(dtype="float64")
        own = curve_forecasts(
            slope_front.reindex(returns.index),
            cl_contracts,
            "CL",
            fallback_slope=None,
        )
        # WTI has its own prompt curve from EIA until 2024-04-05: prefer it while it exists (it is the slope the
        # published results are about), then the front-to-far-December slope.
        eia_part = pd.DataFrame(
            {
                "slope": eia_slope.reindex(returns.index),
                "carry": sg.carry_forecast(eia_slope).reindex(returns.index),
                "carry_momentum": sg.carry_momentum_forecast(eia_slope).reindex(returns.index),
            }
        )
        curve = own.copy()
        use_eia = eia_part["slope"].notna()
        for col in ("slope", "carry", "carry_momentum"):
            curve[col] = eia_part[col].where(use_eia & eia_part[col].notna(), own[col])
        curve["slope_pair"] = own["slope_pair"].where(~use_eia, "WTI C2-C4 (EIA)")
        curve["slope_approx"] = False
        for col in ("carry", "carry_momentum", "slope"):
            curve[col] = curve[col].ffill(limit=3)
        data.series["MCL"] = VehicleSeries(
            vehicle="MCL",
            bars=bars,
            returns=returns,
            return_source=source,
            forecasts=_forecast_frame(returns, curve),
            vol=sg.ew_vol(returns),
            slope=curve["slope"],
            slope_approx=curve["slope_approx"],
            slope_pair=curve["slope_pair"].astype("object"),
        )
    else:
        data.meta["notes"].append("MCL: storico WTI insufficiente")

    # ---- context ------------------------------------------------------------------------------------------
    ovx = _daily(load("ovx"))
    if not ovx.empty:
        ovx_col = next((c for c in ("value", "close") if c in ovx.columns), None)
        if ovx_col is not None:
            data.ovx = ovx[ovx_col].astype("float64").dropna()
    hormuz = _latest(store, "hormuz", asof)
    if hormuz is not None and not hormuz.empty:
        data.hormuz = _daily(hormuz)
    # "BZ" is not a vehicle: no book trades ICE Brent. Its bars are only the Brent price the terminal shows.
    for vehicle, entry in (("BNO", "bno_intraday"), ("MCL", "cl_intraday"), ("BZ", "bz_intraday")):
        frame = _latest(store, entry, asof)
        if frame is not None and not frame.empty:
            data.intraday[vehicle] = frame
    return data


def _anchor_close(held: pd.Series, contracts: pd.DataFrame, front_close: pd.Series) -> float:
    """Last real close of the contract held on the last day (so the index ends on a real price)."""
    last_day = held.index[-1]
    code = str(held.iloc[-1])
    if code in contracts.columns:
        col = contracts[code].dropna()
        col = col.loc[:last_day]
        if len(col) and math.isfinite(float(col.iloc[-1])) and float(col.iloc[-1]) > 0:
            return float(col.iloc[-1])
    fc = front_close.loc[:last_day].dropna()
    if len(fc) and float(fc.iloc[-1]) > 0:
        return float(fc.iloc[-1])
    return 100.0


def hormuz_summary(hormuz: pd.DataFrame, asof: pd.Timestamp | None = None) -> dict[str, Any]:
    """Tanker transits through the Strait of Hormuz: last week against the year before the collapse.

    The baseline is the median of the 7-day average over the whole history that ends 60 days before the last
    observation, so a closure does not become its own baseline. Returns {} when there is no data.
    """
    if hormuz is None or hormuz.empty or "n_tanker" not in hormuz.columns:
        return {}
    rows = hormuz
    if "portname" in rows.columns:
        rows = rows[rows["portname"] == "Strait of Hormuz"]
    series = rows["n_tanker"].astype("float64").sort_index()
    if asof is not None:
        series = series.loc[:asof]
    if len(series) < 30:
        return {}
    weekly = series.rolling(7, min_periods=5).mean()
    last = weekly.dropna()
    if last.empty:
        return {}
    last_day = pd.Timestamp(last.index[-1])
    # the reference: the median of the 7-day average over everything seen up to 60 days ago. A median barely
    # moves when a closure adds months of zeros to years of normal traffic, and the 60 days keep the collapse
    # itself out of its own baseline.
    history = weekly.loc[: last_day - pd.Timedelta(days=60)].dropna()
    baseline = float(history.median()) if len(history) >= 200 else float("nan")
    value = float(last.iloc[-1])
    ratio = value / baseline if math.isfinite(baseline) and baseline > 0 else float("nan")
    month_ago = weekly.loc[: last_day - pd.Timedelta(days=28)].dropna()
    return {
        "asof": last_day.date().isoformat(),
        "tankers_7d": round(value, 2),
        "baseline": None if not math.isfinite(baseline) else round(baseline, 1),
        "ratio": None if not math.isfinite(ratio) else round(ratio, 4),
        "tankers_7d_month_ago": None if month_ago.empty else round(float(month_ago.iloc[-1]), 2),
        "closed": bool(math.isfinite(ratio) and ratio < 0.25),
        "source": "IMF PortWatch (AIS)",
    }
