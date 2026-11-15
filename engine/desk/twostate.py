"""The two-state reading of the option chain (phase 22, ``docs/RESEARCH.md`` chapter 17 B, C and G).

A futures curve gives the market's MEAN. In a market that hangs on one event (will the strait reopen) the mean
hides the shape: the options on the WTI fund price a distribution, and that distribution can have two humps. This
module reads it. For one expiry it fits the out-of-the-money quotes with a mixture of two lognormals,

    w * LN(F_low, s_low) + (1 - w) * LN(F_high, s_high),      w * F_low + (1 - w) * F_high = F,

beside ONE lognormal, and says that the chain "shows two states" only when the mixture is clearly the better
description (the five declared conditions of 17 B 6). Otherwise the answer is "one state today", which is a result
and is shown as one. The states are called low and high, never "peace" and "war": the weights say which one the
market finds likelier, not their names.

What the reading is used for, exactly as registered:

* SHOWN: the two state prices, the weight, the fit's error, the term structure of at-the-money implied volatility,
  and - for every oil book - what each state would cost or pay (``scenario_pnl``).
* The EVENT BUDGET of one book, the Oracle 3x (17 C 2, ``event_caps``): the exposure on a side is at most
  ``budget / |adverse state's move|``. No other book's size reads this module.
* ARCHIVED: every reading (``twostate-YYYY-MM.jsonl``) and, once per US session, the out-of-the-money quotes
  themselves (``chain-YYYY-MM.jsonl``) - the daily history of real option quotes the project lacked.

Everything here is a MODEL of delayed quotes on American options (out of the money, on a fund that pays nothing,
the early-exercise premium is small) and carries ``approx``. The weights of a mixture fitted on options are known
to be unstable from day to day; the state means are steadier. Nothing here is a forecast.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import brentq, least_squares
from scipy.special import ndtr

from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso, parse_iso
from engine.data.raw_store import RawStore
from engine.desk.options import NEW_YORK, Chain, load_chain

log = logging.getLogger(__name__)

SOURCE_ENTRY = "uso_options"
SYMBOL = "USO"
TWOSTATE_LOG = "twostate"  # state/desk/twostate-YYYY-MM.jsonl: every reading
CHAIN_LOG = "chain"  # state/desk/chain-YYYY-MM.jsonl: the out-of-the-money quotes, once per US session
# 17 B: every constant under the name the chapter gives it
MAX_REL_SPREAD = 0.25  # a quote wider than a quarter of its mid is not read (the options book's rule 5)
MIN_SIDE = 8  # puts and calls needed, each, for a reading
DTE_MIN = 14
DTE_MAX = 75
DTE_HEADLINE = 45
RATE = 0.04  # a declared constant: on 45 days it moves a price by half a per cent
IMPROVEMENT = 0.70  # the mixture's error must be at most this share of the single lognormal's
W_MIN = 0.05  # a state lighter than this is a tail, not a state
SEPARATION = 1.0  # the distance between the state means, in the larger state's own standard deviations
MAX_RMSE = 2.0  # the mixture's mean error, in half bid-ask spreads
MAX_AGE_HOURS = 26.0  # how old a reading may be for the event budget
VOL_MIN = 0.05
VOL_MAX = 3.0
D_MAX = 2.0
MIN_HALF_SPREAD = 0.01  # dollars: the unit of the error is never smaller than a cent
STARTS_W = (0.1, 0.25, 0.5, 0.75, 0.9)
STARTS_D = (0.1, 0.25, 0.5)
# 17 G: the term structure and the daily archive of the quotes
TERM_DTE_MIN = 4
CHAIN_ARCHIVE_AFTER_NY = time(15, 0)
CHAIN_COLUMNS = ("expiry", "right", "strike", "bid", "ask", "delta")
# 17 J 5: the near expiry (4 to 10 days) against the one nearest 45 days
TERM_NEAR_MAX_DTE = 10
TERM_FLAG_POINTS = 0.05
SESSION_CLOSE_NY = time(16, 0)
# 17 N (added after the first fits, and said so there): the mixture prices nearly every quote inside its bid-ask
# whether or not its two components are two STATES, so it is also read as what it always is - a smooth
# description of the smile - for the quantiles of the price at the expiry and the weight of the tails
QUANTILES = (0.05, 0.25, 0.50, 0.75, 0.95)
TAIL_MOVES = (0.10, 0.20)


# ----------------------------------------------------------------------------------------------- pricing
def black(forward: float, strikes: np.ndarray, sd: float, calls: np.ndarray) -> np.ndarray:
    """Undiscounted Black prices of options on a forward: ``sd`` is the standard deviation of the log price at
    the expiry (volatility times the square root of the years), ``calls`` a boolean mask (puts where False)."""
    sd = max(float(sd), 1e-9)
    d1 = (np.log(forward / strikes) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    call = forward * ndtr(d1) - strikes * ndtr(d2)
    put = strikes * ndtr(-d2) - forward * ndtr(-d1)
    return np.where(calls, call, put)


def state_forwards(forward: float, w: float, d: float) -> tuple[float, float]:
    """The two state means given the forward, the low state's weight and their log distance."""
    low = forward / (w + (1.0 - w) * math.exp(d))
    return low, low * math.exp(d)


def mixture_prices(
    forward: float,
    strikes: np.ndarray,
    calls: np.ndarray,
    years: float,
    w: float,
    d: float,
    s_low: float,
    s_high: float,
) -> np.ndarray:
    root = math.sqrt(years)
    f_low, f_high = state_forwards(forward, w, d)
    return w * black(f_low, strikes, s_low * root, calls) + (1.0 - w) * black(f_high, strikes, s_high * root, calls)


def implied_vol(price: float, forward: float, strike: float, years: float, call: bool) -> float | None:
    """The Black volatility of one undiscounted option price, or None when the price is outside what any
    volatility between the bounds can produce."""
    if not (price > 0 and forward > 0 and strike > 0 and years > 0):
        return None
    root = math.sqrt(years)
    k, c = np.array([strike]), np.array([call])

    def gap(vol: float) -> float:
        return float(black(forward, k, vol * root, c)[0]) - price

    try:
        if gap(VOL_MIN) > 0 or gap(VOL_MAX) < 0:
            return None
        return float(brentq(gap, VOL_MIN, VOL_MAX, xtol=1e-6))
    except ValueError:
        return None


# ----------------------------------------------------------------------------------------------- the quotes
@dataclass(frozen=True)
class Quotes:
    """The out-of-the-money quotes of one expiry that pass the filter, as arrays, with the forward they are out
    of the money against."""

    forward: float
    years: float
    discount: float
    strikes: np.ndarray
    calls: np.ndarray  # True for a call
    mids: np.ndarray  # undiscounted (the quoted mid divided by the discount factor)
    halves: np.ndarray  # half the bid-ask spread, undiscounted, at least a cent
    n_puts: int
    n_calls: int


def years_to_expiry(expiry: date, quote_ts: datetime) -> float:
    """Calendar time from the quote to the expiry's close (16:00 New York), in years."""
    close = datetime.combine(expiry, SESSION_CLOSE_NY, tzinfo=NEW_YORK)
    return max(0.0, (close - ensure_utc(quote_ts)).total_seconds() / (365.0 * 86400.0))


def _two_sided(frame: pd.DataFrame) -> pd.DataFrame:
    ok = (frame["bid"] > 0) & (frame["ask"] > frame["bid"])
    out = frame[ok].copy()
    out["mid"] = (out["bid"] + out["ask"]) / 2.0
    return out


def forward_of(frame: pd.DataFrame, spot: float, years: float) -> float | None:
    """The forward of one expiry by put-call parity: the median, over the three strikes nearest the fund's price
    that quote both a call and a put, of ``K + (C - P) * exp(r T)`` on the mids."""
    quoted = _two_sided(frame)
    calls = quoted[quoted["right"] == "C"].set_index("strike")["mid"]
    puts = quoted[quoted["right"] == "P"].set_index("strike")["mid"]
    both = sorted(set(calls.index) & set(puts.index), key=lambda k: abs(float(k) - spot))[:3]
    if not both:
        return None
    grow = math.exp(RATE * years)
    values = [float(k) + (float(calls[k]) - float(puts[k])) * grow for k in both]
    out = float(np.median(values))
    return out if math.isfinite(out) and out > 0 else None


def otm_quotes(frame: pd.DataFrame, spot: float, years: float) -> Quotes | None:
    """The quotes the reading is fitted on (17 B 1), or None when the expiry has no forward."""
    forward = forward_of(frame, spot, years)
    if forward is None or years <= 0:
        return None
    quoted = _two_sided(frame)
    quoted = quoted[(quoted["ask"] - quoted["bid"]) / quoted["mid"] <= MAX_REL_SPREAD]
    is_call = quoted["right"] == "C"
    otm = quoted[(is_call & (quoted["strike"] > forward)) | (~is_call & (quoted["strike"] < forward))]
    otm = otm.sort_values(["strike", "right"])
    discount = math.exp(-RATE * years)
    calls = (otm["right"] == "C").to_numpy()
    halves = np.maximum((otm["ask"] - otm["bid"]).to_numpy(dtype="float64") / 2.0, MIN_HALF_SPREAD)
    return Quotes(
        forward=forward,
        years=years,
        discount=discount,
        strikes=otm["strike"].to_numpy(dtype="float64"),
        calls=calls,
        mids=otm["mid"].to_numpy(dtype="float64") / discount,
        halves=halves / discount,
        n_puts=int((~calls).sum()),
        n_calls=int(calls.sum()),
    )


# ----------------------------------------------------------------------------------------------- the fits
def _rmse(residuals: np.ndarray) -> float:
    return float(math.sqrt(float(np.mean(residuals * residuals)))) if residuals.size else float("nan")


def fit_single(q: Quotes) -> dict[str, float]:
    """One lognormal: the volatility that fits the quotes best, the error in half spreads."""
    root = math.sqrt(q.years)

    def residuals(x: np.ndarray) -> np.ndarray:
        return (black(q.forward, q.strikes, float(x[0]) * root, q.calls) - q.mids) / q.halves

    best = None
    for start in (0.2, 0.5, 1.0):
        res = least_squares(residuals, [start], bounds=([VOL_MIN], [VOL_MAX]))
        if best is None or res.cost < best.cost:
            best = res
    assert best is not None
    r = residuals(best.x)
    return {"vol": float(best.x[0]), "rmse": _rmse(r), "inside": float(np.mean(np.abs(r) <= 1.0))}


def fit_mixture(q: Quotes) -> dict[str, float]:
    """Two lognormals (17 B 3-4): the best of the fixed grid of starts. Deterministic."""

    def residuals(x: np.ndarray) -> np.ndarray:
        w, d, s_low, s_high = (float(v) for v in x)
        return (mixture_prices(q.forward, q.strikes, q.calls, q.years, w, d, s_low, s_high) - q.mids) / q.halves

    base = fit_single(q)["vol"]
    lower, upper = [1e-3, 0.0, VOL_MIN, VOL_MIN], [1.0 - 1e-3, D_MAX, VOL_MAX, VOL_MAX]
    best = None
    for w0 in STARTS_W:
        for d0 in STARTS_D:
            start = [w0, d0, min(max(base, VOL_MIN), VOL_MAX), min(max(base, VOL_MIN), VOL_MAX)]
            try:
                res = least_squares(residuals, start, bounds=(lower, upper))
            except ValueError:  # a start the solver refuses is one start fewer
                continue
            if best is None or res.cost < best.cost - 1e-12:
                best = res
    if best is None:
        raise ValueError("the mixture could not be fitted from any start")
    w, d, s_low, s_high = (float(v) for v in best.x)
    r = residuals(best.x)
    f_low, f_high = state_forwards(q.forward, w, d)
    return {
        "w": w,
        "d": d,
        "s_low": s_low,
        "s_high": s_high,
        "f_low": f_low,
        "f_high": f_high,
        "rmse": _rmse(r),
        "inside": float(np.mean(np.abs(r) <= 1.0)),
    }


def verdict(q: Quotes, single: Mapping[str, float], mix: Mapping[str, float]) -> tuple[bool, list[str]]:
    """Does the chain show two states (17 B 6)? ``(yes, the conditions that failed, in the reader's words)``."""
    failed: list[str] = []
    if q.n_puts < MIN_SIDE or q.n_calls < MIN_SIDE:
        failed.append(f"too few quotes ({q.n_puts} puts, {q.n_calls} calls; {MIN_SIDE} each are needed)")
    if not (mix["rmse"] <= IMPROVEMENT * single["rmse"]):
        failed.append(
            f"two states fit the quotes only {1.0 - mix['rmse'] / single['rmse']:.0%} better than one "
            f"({1.0 - IMPROVEMENT:.0%} is needed)"
            if single["rmse"] > 0
            else "one lognormal already fits the quotes exactly"
        )
    if not (W_MIN <= mix["w"] <= 1.0 - W_MIN):
        light = min(mix["w"], 1.0 - mix["w"])
        failed.append(f"the lighter state weighs {light:.1%}: a tail, not a state")
    spread = max(mix["s_low"], mix["s_high"]) * math.sqrt(q.years)
    if not (mix["d"] >= SEPARATION * spread):
        failed.append("the two states overlap: their means are closer than one state's own width")
    if not (mix["rmse"] <= MAX_RMSE):
        failed.append(f"the fit misses the quotes by {mix['rmse']:.1f} half spreads on average")
    return (not failed), failed


def mixture_cdf(x: float, forward: float, years: float, w: float, d: float, s_low: float, s_high: float) -> float:
    """P(price at the expiry <= x) under the fitted mixture (risk-neutral: it prices options, it is not a
    forecast of frequencies)."""
    if x <= 0:
        return 0.0
    root = math.sqrt(years)
    f_low, f_high = state_forwards(forward, w, d)

    def one(mean: float, vol: float) -> float:
        sd = max(vol * root, 1e-9)
        return float(ndtr((math.log(x / mean) + 0.5 * sd * sd) / sd))

    return w * one(f_low, s_low) + (1.0 - w) * one(f_high, s_high)


def distribution(q: Quotes, mix: Mapping[str, float]) -> dict[str, Any]:
    """The quantiles of the price at the expiry, as moves against the forward, and the weight of the tails
    beyond ``TAIL_MOVES``, from the fitted mixture."""
    args = (q.forward, q.years, mix["w"], mix["d"], mix["s_low"], mix["s_high"])
    out: dict[str, Any] = {"quantiles": {}, "tails": {}}
    for level in QUANTILES:
        try:
            x = brentq(lambda v, p=level: mixture_cdf(v, *args) - p, q.forward * 1e-3, q.forward * 50.0, xtol=1e-6)
        except ValueError:
            continue
        out["quantiles"][f"q{round(level * 100):02d}"] = round(float(x) / q.forward - 1.0, 4)
    for move in TAIL_MOVES:
        key = f"{round(move * 100)}"
        out["tails"][f"down_{key}"] = round(mixture_cdf(q.forward * (1.0 - move), *args), 4)
        out["tails"][f"up_{key}"] = round(1.0 - mixture_cdf(q.forward * (1.0 + move), *args), 4)
    return out


def atm_vol(frame: pd.DataFrame, forward: float, years: float) -> float | None:
    """The implied volatility at the forward: the Black volatilities of the nearest out-of-the-money put and call
    (two-sided quotes, on the mids), interpolated in the strike."""
    quoted = _two_sided(frame)
    discount = math.exp(-RATE * years)
    puts = quoted[(quoted["right"] == "P") & (quoted["strike"] <= forward)].sort_values("strike")
    calls = quoted[(quoted["right"] == "C") & (quoted["strike"] >= forward)].sort_values("strike")
    points: list[tuple[float, float]] = []
    if not puts.empty:
        row = puts.iloc[-1]
        vol = implied_vol(float(row["mid"]) / discount, forward, float(row["strike"]), years, False)
        if vol is not None:
            points.append((float(row["strike"]), vol))
    if not calls.empty:
        row = calls.iloc[0]
        vol = implied_vol(float(row["mid"]) / discount, forward, float(row["strike"]), years, True)
        if vol is not None:
            points.append((float(row["strike"]), vol))
    if not points:
        return None
    if len(points) == 1 or points[0][0] == points[1][0]:
        return float(np.mean([v for _, v in points]))
    (k0, v0), (k1, v1) = points
    return float(v0 + (v1 - v0) * (forward - k0) / (k1 - k0))


# ----------------------------------------------------------------------------------------------- the reading
def read_expiry(frame: pd.DataFrame, expiry: date, spot: float, quote_ts: datetime) -> dict[str, Any] | None:
    """One expiry's reading: the forward, the single lognormal, the mixture, whether two states show and why not."""
    years = years_to_expiry(expiry, quote_ts)
    q = otm_quotes(frame, spot, years)
    dte = (expiry - ensure_utc(quote_ts).astimezone(NEW_YORK).date()).days
    if q is None:
        return None
    row: dict[str, Any] = {
        "expiry": expiry.isoformat(),
        "dte": int(dte),
        "years": round(years, 6),
        "forward": round(q.forward, 4),
        "n_puts": q.n_puts,
        "n_calls": q.n_calls,
        "atm_vol": atm_vol(frame, q.forward, years),
    }
    if q.n_puts + q.n_calls < 5:
        row.update({"two_states": False, "why_not": ["too few quotes to fit anything"]})
        return row
    single = fit_single(q)
    root = math.sqrt(years)
    row["single"] = {
        "vol": round(single["vol"], 4),
        "rmse": round(single["rmse"], 3),
        "inside": round(single["inside"], 3),
        # the band of one standard deviation at the expiry, as returns against the forward
        "low_move": round(math.exp(-single["vol"] * root) - 1.0, 4),
        "high_move": round(math.exp(single["vol"] * root) - 1.0, 4),
    }
    try:
        mix = fit_mixture(q)
    except ValueError as exc:
        row.update({"two_states": False, "why_not": [str(exc)]})
        return row
    ok, failed = verdict(q, single, mix)
    row["mixture"] = {
        "w_low": round(mix["w"], 4),
        "low_move": round(mix["f_low"] / q.forward - 1.0, 4),
        "high_move": round(mix["f_high"] / q.forward - 1.0, 4),
        "vol_low": round(mix["s_low"], 4),
        "vol_high": round(mix["s_high"], 4),
        "rmse": round(mix["rmse"], 3),
        "inside": round(mix["inside"], 3),
        "improvement": round(1.0 - mix["rmse"] / single["rmse"], 3) if single["rmse"] > 0 else None,
    }
    row["two_states"] = ok
    row["why_not"] = failed
    if mix["rmse"] <= MAX_RMSE:
        # a fit that misses the quotes describes nothing: no quantiles from it
        row["distribution"] = distribution(q, mix)
    return row


def read_chain(chain: Chain) -> dict[str, Any]:
    """The reading of one download of the chain: every expiry between ``DTE_MIN`` and ``DTE_MAX`` days, the one
    nearest ``DTE_HEADLINE`` as the headline, and the term structure of at-the-money volatility from
    ``TERM_DTE_MIN`` days out."""
    today = chain.today()
    frame = chain.frame
    expiries: list[dict[str, Any]] = []
    term: list[dict[str, Any]] = []
    for stamp in sorted(pd.unique(frame["expiry"])):
        expiry = pd.Timestamp(stamp).date()
        dte = (expiry - today).days
        if dte < TERM_DTE_MIN or dte > DTE_MAX:
            continue
        part = frame[frame["expiry"] == stamp]
        years = years_to_expiry(expiry, chain.quote_ts)
        if dte >= DTE_MIN:
            try:
                row = read_expiry(part, expiry, chain.price, chain.quote_ts)
            except Exception as exc:  # one expiry's quotes in a shape nobody expected cost that expiry only
                log.warning("two-state reading of %s %s failed: %s", chain.symbol, expiry, exc)
                row = None
            if row is not None:
                expiries.append(row)
                if row.get("atm_vol") is not None:
                    term.append({"expiry": row["expiry"], "dte": row["dte"], "atm_vol": round(row["atm_vol"], 4)})
                continue
        forward = forward_of(part, chain.price, years)
        vol = None if forward is None else atm_vol(part, forward, years)
        if vol is not None:
            term.append({"expiry": expiry.isoformat(), "dte": int(dte), "atm_vol": round(vol, 4)})
    for row in expiries:
        if row.get("atm_vol") is not None:
            row["atm_vol"] = round(float(row["atm_vol"]), 4)
    headline = min(expiries, key=lambda r: (abs(r["dte"] - DTE_HEADLINE), r["dte"])) if expiries else None
    return {
        "ts": iso(chain.quote_ts),
        "day": today.isoformat(),
        "symbol": chain.symbol,
        "price": round(chain.price, 4),
        "approx": True,
        "headline": None if headline is None else headline["expiry"],
        "expiries": expiries,
        "term": sorted(term, key=lambda r: r["dte"]),
    }


def headline_of(reading: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The headline expiry's row of a reading, or None."""
    if not reading or not reading.get("headline"):
        return None
    for row in reading.get("expiries") or ():
        if row.get("expiry") == reading["headline"]:
            return dict(row)
    return None


def term_flag(reading: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """17 J 5: the near expiry (``TERM_DTE_MIN`` to ``TERM_NEAR_MAX_DTE`` days) against the one nearest the
    headline horizon; flagged when the near one is ``TERM_FLAG_POINTS`` of volatility above."""
    term = [r for r in (reading or {}).get("term") or () if r.get("atm_vol") is not None]
    near = [r for r in term if TERM_DTE_MIN <= int(r["dte"]) <= TERM_NEAR_MAX_DTE]
    far = [r for r in term if int(r["dte"]) >= DTE_MIN]
    if not near or not far:
        return None
    a = min(near, key=lambda r: int(r["dte"]))
    b = min(far, key=lambda r: (abs(int(r["dte"]) - DTE_HEADLINE), int(r["dte"])))
    gap = float(a["atm_vol"]) - float(b["atm_vol"])
    return {"near": a, "far": b, "gap": round(gap, 4), "flag": gap >= TERM_FLAG_POINTS}


# ----------------------------------------------------------------------------------------------- the archive
def _session_key(reading: Mapping[str, Any]) -> str:
    return f"{reading.get('day')}|{reading.get('symbol')}"


def chain_record(chain: Chain) -> dict[str, Any]:
    """The out-of-the-money quotes of one download, compact: one row per option quoted within the filter, for
    the expiries between ``TERM_DTE_MIN`` and ``DTE_MAX`` days (17 G)."""
    today = chain.today()
    rows: list[list[Any]] = []
    forwards: dict[str, float] = {}
    for stamp in sorted(pd.unique(chain.frame["expiry"])):
        expiry = pd.Timestamp(stamp).date()
        dte = (expiry - today).days
        if dte < TERM_DTE_MIN or dte > DTE_MAX:
            continue
        part = chain.frame[chain.frame["expiry"] == stamp]
        years = years_to_expiry(expiry, chain.quote_ts)
        forward = forward_of(part, chain.price, years)
        if forward is None:
            continue
        forwards[expiry.isoformat()] = round(forward, 4)
        quoted = _two_sided(part)
        quoted = quoted[(quoted["ask"] - quoted["bid"]) / quoted["mid"] <= MAX_REL_SPREAD]
        is_call = quoted["right"] == "C"
        otm = quoted[(is_call & (quoted["strike"] > forward)) | (~is_call & (quoted["strike"] < forward))]
        for _, r in otm.sort_values(["strike", "right"]).iterrows():
            delta = r.get("delta")
            rows.append(
                [
                    expiry.isoformat(),
                    str(r["right"]),
                    float(r["strike"]),
                    float(r["bid"]),
                    float(r["ask"]),
                    None if delta is None or not math.isfinite(float(delta)) else round(float(delta), 4),
                ]
            )
    return {
        "key": f"{today.isoformat()}|{chain.symbol}",
        "day": today.isoformat(),
        "ts": iso(chain.quote_ts),
        "symbol": chain.symbol,
        "price": round(chain.price, 4),
        "forwards": forwards,
        "columns": list(CHAIN_COLUMNS),
        "rows": rows,
    }


def slim(reading: Mapping[str, Any]) -> dict[str, Any]:
    """A reading as the archive keeps it: the headline expiry whole, the other expiries without what only the
    headline's page shows (the single lognormal, the reasons, the tail weights). The chain is read every hour of
    the US session and the log is append-only: a reading is about 2 kB instead of 6."""
    out = dict(reading)
    rows: list[dict[str, Any]] = []
    for row in reading.get("expiries") or ():
        if row.get("expiry") == reading.get("headline"):
            rows.append(dict(row))
            continue
        mix = row.get("mixture") or {}
        quantiles = (row.get("distribution") or {}).get("quantiles") or {}
        rows.append(
            {
                "expiry": row.get("expiry"),
                "dte": row.get("dte"),
                "forward": row.get("forward"),
                "atm_vol": row.get("atm_vol"),
                "two_states": bool(row.get("two_states")),
                "mixture": {k: mix.get(k) for k in ("w_low", "low_move", "high_move", "rmse") if k in mix},
                "distribution": {"quantiles": {k: quantiles[k] for k in ("q05", "q50", "q95") if k in quantiles}},
            }
        )
    out["expiries"] = rows
    return out


def record_reading(raw: RawStore, store: StateStore, now: datetime) -> dict[str, Any]:
    """Read the newest chain on file and archive what is new: the two-state reading when the chain's quotes are
    newer than the last reading's, and the quotes themselves once per US session (the first chain quoted at or
    after 15:00 New York). Idempotent: the same chain twice writes nothing the second time."""
    now = ensure_utc(now)
    chain = load_chain(raw, SOURCE_ENTRY, SYMBOL, now)
    if chain is None:
        return {"status": "no chain on file"}
    out: dict[str, Any] = {"status": "ok", "quote_ts": iso(chain.quote_ts)}
    last = latest_reading(store)
    last_ts = None if last is None else parse_iso(str(last.get("ts") or ""))
    if last_ts is not None and chain.quote_ts <= last_ts:
        out["reading"] = "already read"
    else:
        reading = read_chain(chain)
        store.append_jsonl(TWOSTATE_LOG, slim(reading), chain.quote_ts)
        head = headline_of(reading)
        out["reading"] = "written"
        out["two_states"] = None if head is None else bool(head.get("two_states"))
    local = chain.quote_ts.astimezone(NEW_YORK)
    if local.weekday() < 5 and local.time() >= CHAIN_ARCHIVE_AFTER_NY:
        key = f"{chain.today().isoformat()}|{chain.symbol}"
        have = {str(r.get("key")) for r in store.tail_jsonl(CHAIN_LOG, 10)}
        if key not in have:
            store.append_jsonl(CHAIN_LOG, chain_record(chain), chain.quote_ts)
            out["chain"] = "archived"
    return out


def latest_reading(store: StateStore) -> dict[str, Any] | None:
    """The newest reading on file, by its quote time."""
    rows = [r for r in store.tail_jsonl(TWOSTATE_LOG, 40) if r.get("ts")]
    if not rows:
        return None
    return max(rows, key=lambda r: str(r["ts"]))


def reading_for(store: StateStore, now: datetime, max_age_hours: float = MAX_AGE_HOURS) -> dict[str, Any] | None:
    """The newest reading quoted at or before ``now`` and at most ``max_age_hours`` old, or None."""
    now = ensure_utc(now)
    best: tuple[datetime, dict[str, Any]] | None = None
    for row in store.tail_jsonl(TWOSTATE_LOG, 80):
        ts = parse_iso(str(row.get("ts") or ""))
        if ts is None or ts > now or now - ts > timedelta(hours=max_age_hours):
            continue
        if best is None or ts > best[0]:
            best = (ts, row)
    return None if best is None else best[1]


def history(store: StateStore, days: int = 30) -> list[dict[str, Any]]:
    """The last reading of each of the last ``days`` sessions: the headline's weight and state moves, for the
    page's small history."""
    by_day: dict[str, dict[str, Any]] = {}
    for row in store.tail_jsonl(TWOSTATE_LOG, 400):
        day = str(row.get("day") or "")
        if day and (day not in by_day or str(row.get("ts")) > str(by_day[day].get("ts"))):
            by_day[day] = row
    out: list[dict[str, Any]] = []
    for day in sorted(by_day)[-days:]:
        head = headline_of(by_day[day]) or {}
        mix = head.get("mixture") or {}
        out.append(
            {
                "day": day,
                "two_states": bool(head.get("two_states")),
                "w_low": mix.get("w_low"),
                "low_move": mix.get("low_move"),
                "high_move": mix.get("high_move"),
                "atm_vol": head.get("atm_vol"),
            }
        )
    return out


# ----------------------------------------------------------------------------------------------- what it is used for
def state_moves(reading: Mapping[str, Any] | None) -> tuple[float, float] | None:
    """(the low state's move, the high state's move) of the headline expiry as returns against the forward, when
    the reading shows two states; None otherwise."""
    head = headline_of(reading)
    if not head or not head.get("two_states"):
        return None
    mix = head.get("mixture") or {}
    low, high = mix.get("low_move"), mix.get("high_move")
    if low is None or high is None or not (math.isfinite(low) and math.isfinite(high)):
        return None
    return float(low), float(high)


def tail_moves(reading: Mapping[str, Any] | None) -> tuple[float, float] | None:
    """(the 5 % quantile's move, the 95 % quantile's move) of the headline expiry against the forward: the
    options' own one-in-twenty moves either way by that date. None without a fit that prices the quotes."""
    head = headline_of(reading)
    quantiles = ((head or {}).get("distribution") or {}).get("quantiles") or {}
    low, high = quantiles.get("q05"), quantiles.get("q95")
    if low is None or high is None or not (math.isfinite(low) and math.isfinite(high)):
        return None
    return float(low), float(high)


def largest_move(returns: Iterable[float], sessions: int = 252) -> float | None:
    """The largest absolute one-session return among the last ``sessions`` (the event budget's fallback, 17 C 2)."""
    values = [abs(float(r)) for r in returns if r is not None and math.isfinite(float(r))]
    tail = values[-sessions:]
    return max(tail) if tail else None


def event_caps(
    budget: float, moves: tuple[float, float] | None, fallback_move: float | None
) -> tuple[float | None, float | None, str]:
    """The event budget's ceilings on the long and on the short side, as multiples of equity, and the words for
    them. Long: the adverse state is the low one; short: the high one. Without a valid two-state reading both
    sides use the largest one-session move of the last year; without that either, no ceiling (None, None)."""
    if moves is not None:
        low, high = moves
        long_cap = budget / abs(low) if low < 0 else None
        short_cap = budget / abs(high) if high > 0 else None
        text = (
            f"event budget: at most {budget:.0%} of the account in the adverse state the options price "
            f"(low {low:+.0%}, high {high:+.0%})"
        )
        return long_cap, short_cap, text
    if fallback_move is not None and fallback_move > 0:
        cap = budget / fallback_move
        text = (
            f"event budget: no two-state reading within {MAX_AGE_HOURS:.0f} hours, so at most {budget:.0%} of the "
            f"account on the largest one-session move of the last year ({fallback_move:.0%})"
        )
        return cap, cap, text
    return None, None, "event budget: nothing to size it on today"


def scenario_pnl(exposure: float, equity: float, moves: tuple[float, float] | None) -> dict[str, float] | None:
    """What each state would cost or pay a book that holds ``exposure`` times its ``equity`` of oil (17 C 1): a
    straight line in the state's move, no option convexity, no change of size on the way."""
    if moves is None or not math.isfinite(exposure) or not math.isfinite(equity):
        return None
    low, high = moves
    return {"low": round(exposure * equity * low, 2), "high": round(exposure * equity * high, 2)}


def describe() -> dict[str, Any]:
    """The rule in the reader's words, with its numbers, for the page."""
    return {
        "model": "two lognormals fitted on the out-of-the-money quotes of one expiry, beside one lognormal",
        "quotes": (
            f"two-sided, no wider than {MAX_REL_SPREAD:.0%} of the mid, at least {MIN_SIDE} puts and {MIN_SIDE} calls"
        ),
        "expiries": f"{DTE_MIN} to {DTE_MAX} days; the headline is the one nearest {DTE_HEADLINE} days",
        "two_states_when": (
            f"the mixture's error is at most {IMPROVEMENT:.0%} of one lognormal's, each state weighs at least "
            f"{W_MIN:.0%}, the state means are at least one state's width apart, and the fit is within "
            f"{MAX_RMSE:g} half spreads on average"
        ),
        "source": "Cboe delayed quotes of the WTI fund's options (USO); a model, approx",
    }
