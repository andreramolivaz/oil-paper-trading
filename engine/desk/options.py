"""The options book: put credit spreads in the direction of the forecast, on real delayed quotes.

What the evidence says, and what this book therefore is NOT (``docs/RESEARCH.md`` has the numbers):

* Oil options are expensive on average. From 2007 the OVX index has been about six volatility points above the
  volatility that followed, four days out of five, and buying an at-the-money straddle every month lost money
  with a t-statistic beyond -2.5. Buying both ways because "it can go up more or fall hard" is the losing side.
* Selling that premium with a hedge attached does not collect it either, once the hedge is paid for at real
  bid-ask spreads and with the smile: an iron condor or an iron butterfly sold every week comes out at or
  below zero in the replay. The premium is real; at ten thousand dollars it does not survive four legs.
* What is left is the direction. A put spread sold below the market when trend and curve say long keeps a
  small positive result across the smile and cost assumptions tried (Sharpe 0.4 to 0.7, two to four per cent
  a year on the account, worst replay drawdown -14 %); the mirror image on the short side shows nothing and
  is not traded. That is the same bet as the linear books with a ceiling on the gain and a floor on the loss.

Every number in that last sentence comes from a MODEL replay (Black-Scholes on OVX with an assumed smile and
an assumed quoted spread), because no free history of option quotes exists. It is labelled ``approx`` wherever
it is shown and decides nothing. This book exists to replace it with the real thing: every entry, mark and
exit below is priced on the bid and ask the exchange actually published.

The rules, all of them:

1. Once per US session, at the first tick after 15:00 New York with a chain less than 45 minutes old, and at
   most one new structure every ten business days, two open at a time.
2. Only when the combined forecast of the underlying is at least +5 (half the normal conviction).
3. Expiry: the listed one closest to 30 days, between 21 and 45.
4. Short put: the strike whose delta is closest to 0.20. Long put: the lowest strike that keeps the maximum
   loss of one contract within 5 % of equity - the widest wing the account can afford, because the replay's
   edge shrinks to nothing as the wing narrows. No wing at least 3 % of the price wide fits -> no trade.
5. Both legs must be quoted no wider than 25 % of their mid-price. Sold at bid + a quarter of the spread,
   bought at ask - a quarter: three quarters of the way to the bad side of a delayed quote.
6. Held to expiry and settled at intrinsic value on the fund's official close. Early assignment and pin risk
   are not modelled: a real account would close a spread that is near its short strike on the last day.

The Brent fund's own options (BNO) are read and shown next to the WTI fund's (USO). On 2026-10-08 they were
quoted 35 to 100 % of the mid-price wide, so rule 5 keeps the book out of them until that changes; the reason
is on the terminal every day rather than hidden behind a model price.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from statistics import NormalDist
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from engine.core.calendar import add_business_days, is_business_day
from engine.core.config import RiskConfig, load_config
from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso
from engine.data.raw_store import RawStore
from engine.desk.data import DeskData, _daily, _latest

log = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
OPTIONS_BOOK_ID = "opzioni"
STATE_FILE = "book.json"
MONITOR_FILE = "monitor.json"
MULTIPLIER = 100.0  # shares per contract
MARKET_OPEN_NY = time(9, 30)
MARKET_CLOSE_NY = time(16, 0)
SETTLE_AFTER_NY = time(16, 15)
SETTLE_FALLBACK_DAYS = 4  # no official close this long after the expiry -> settle on the last known price, approx


@dataclass(frozen=True)
class Underlying:
    symbol: str  # the fund the options are written on
    chain: str  # raw-store entry of its option chain
    daily: str  # raw-store entry of its daily bars (the unadjusted close settles an expiry)
    forecast: str  # the desk series whose forecast decides the direction
    label_it: str = ""


@dataclass(frozen=True)
class OptionsConfig:
    id: str = OPTIONS_BOOK_ID
    name: str = "Opzioni"
    underlyings: tuple[Underlying, ...] = (
        Underlying("USO", "uso_options", "uso_daily", "MCL", "fondo WTI"),
        Underlying("BNO", "bno_options", "bno_daily", "BNO", "fondo Brent"),
    )
    forecast_threshold: float = 5.0
    short_delta: float = 0.20
    delta_tolerance: float = 0.07
    dte_min: int = 21
    dte_max: int = 45
    dte_target: int = 30
    risk_per_structure: float = 0.05
    max_open: int = 2
    min_business_days_between_entries: int = 10
    min_width_pct: float = 0.03
    min_credit_to_width: float = 0.10
    max_leg_spread: float = 0.25
    max_quote_age_minutes: int = 45
    fill_fraction: float = 0.25
    fee_per_contract: float = 0.05
    decision_time_ny: tuple[int, int] = (15, 0)
    description: str = ""

    def __post_init__(self) -> None:
        if not (0 < self.risk_per_structure <= 0.10):
            raise ValueError("options book: risk_per_structure must be in (0, 0.10]")
        if not (1 <= self.max_open <= 4):
            raise ValueError("options book: max_open must be between 1 and 4")
        if not (0.05 <= self.short_delta <= 0.45):
            raise ValueError("options book: short_delta out of range")
        if not (0 <= self.fill_fraction <= 0.5):
            raise ValueError("options book: fill_fraction is between 0 (the bad side) and 0.5 (the mid)")


def load_options_config(config_dir: Path | None = None) -> OptionsConfig | None:
    """``options_book`` of ``config/books.yaml``; None when the section is missing or disabled."""
    raw = load_config("books", config_dir).get("options_book")
    if not isinstance(raw, dict) or not raw.get("enabled", True):
        return None
    base = OptionsConfig()
    listed = raw.get("underlyings")
    underlyings = base.underlyings
    if isinstance(listed, list) and listed:
        underlyings = tuple(
            Underlying(
                symbol=str(u["symbol"]),
                chain=str(u["chain"]),
                daily=str(u["daily"]),
                forecast=str(u["forecast"]),
                label_it=str(u.get("label", "")),
            )
            for u in listed
        )
    names = {f.name for f in dataclasses.fields(OptionsConfig)} - {"underlyings", "decision_time_ny"}
    values: dict[str, Any] = {k: v for k, v in raw.items() if k in names}
    when = raw.get("decision_time_ny")
    if isinstance(when, list | tuple) and len(when) == 2:
        values["decision_time_ny"] = (int(when[0]), int(when[1]))
    return dataclasses.replace(base, underlyings=underlyings, **values)


# ----------------------------------------------------------------------------------------------- the chain
@dataclass
class Chain:
    symbol: str
    frame: pd.DataFrame
    quote_ts: datetime
    price: float
    iv30: float | None

    def age_minutes(self, now: datetime) -> float:
        return (ensure_utc(now) - self.quote_ts).total_seconds() / 60.0

    def today(self) -> date:
        return self.quote_ts.astimezone(NEW_YORK).date()


def load_chain(raw: RawStore, entry: str, symbol: str, asof: datetime | None = None) -> Chain | None:
    frame = _latest(raw, entry, asof)
    if frame is None or frame.empty or "option" not in frame.columns:
        return None
    frame = frame.reset_index(drop=True)
    stamp = pd.Timestamp(frame["quote_ts"].iloc[0])
    quote_ts = (stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)).to_pydatetime()
    price = float(frame["underlying_price"].iloc[0])
    iv30 = None
    if "iv30" in frame.columns and pd.notna(frame["iv30"].iloc[0]):
        iv30 = float(frame["iv30"].iloc[0]) / 100.0
    if not math.isfinite(price) or price <= 0:
        return None
    frame = frame.copy()
    frame["expiry"] = pd.to_datetime(frame["expiry"]).dt.normalize()
    frame["mid"] = (frame["bid"] + frame["ask"]) / 2.0
    frame["rel_spread"] = ((frame["ask"] - frame["bid"]) / frame["mid"]).where(frame["mid"] > 0)
    return Chain(symbol=symbol, frame=frame, quote_ts=quote_ts, price=price, iv30=iv30)


def sell_price(bid: float, ask: float, fraction: float) -> float:
    """Where a paper SELL fills: the bid plus ``fraction`` of the spread (0 = the bid, 0.5 = the mid)."""
    return bid + fraction * (ask - bid)


def buy_price(bid: float, ask: float, fraction: float) -> float:
    return ask - fraction * (ask - bid)


def _quoted(frame: pd.DataFrame) -> pd.DataFrame:
    ok = (frame["bid"] > 0) & (frame["ask"] > frame["bid"]) & frame["delta"].notna()
    return frame[ok]


def expiries_in_window(chain: Chain, cfg: OptionsConfig) -> list[tuple[pd.Timestamp, int]]:
    """Listed expiries between ``dte_min`` and ``dte_max`` days out, closest to the target first."""
    today = pd.Timestamp(chain.today())
    out = []
    for expiry in sorted(pd.unique(chain.frame["expiry"])):
        dte = int((pd.Timestamp(expiry) - today).days)
        if cfg.dte_min <= dte <= cfg.dte_max:
            out.append((pd.Timestamp(expiry), dte))
    return sorted(out, key=lambda item: (abs(item[1] - cfg.dte_target), item[1]))


def candidate(chain: Chain, cfg: OptionsConfig, equity: float) -> tuple[dict[str, Any] | None, str]:
    """The put credit spread the book would sell on this chain right now, or ``(None, why not)`` in Italian.

    It does not look at the forecast or at what is already open: it answers "is there something tradeable
    here", which the terminal shows every day whether or not the book acts on it.
    """
    window = expiries_in_window(chain, cfg)
    if not window:
        return None, f"nessuna scadenza quotata fra {cfg.dte_min} e {cfg.dte_max} giorni"
    budget = cfg.risk_per_structure * equity
    f = cfg.fill_fraction
    reasons: list[str] = []
    for expiry, dte in window:
        puts = _quoted(chain.frame[(chain.frame["expiry"] == expiry) & (chain.frame["right"] == "P")])
        puts = puts[puts["strike"] < chain.price]
        if puts.empty:
            reasons.append(f"{expiry.date().isoformat()}: nessuna put quotata sotto il prezzo")
            continue
        gap = (puts["delta"].abs() - cfg.short_delta).abs()
        near = puts[gap <= cfg.delta_tolerance]
        if near.empty:
            reasons.append(f"{expiry.date().isoformat()}: nessuna put vicina a delta {cfg.short_delta:.2f}")
            continue
        # among the strikes near the target delta, the closest one that is quoted tightly enough to sell
        tight = near[near["rel_spread"] <= cfg.max_leg_spread]
        if tight.empty:
            worst = near.loc[near.index[int(gap[near.index].to_numpy().argmin())]]
            reasons.append(
                f"{expiry.date().isoformat()}: la put da vendere ({worst['strike']:g}) è quotata "
                f"{float(worst['bid']):.2f}-{float(worst['ask']):.2f}, "
                f"larga il {float(worst['rel_spread']):.0%} del prezzo"
            )
            continue
        short = tight.loc[tight.index[int(gap[tight.index].to_numpy().argmin())]]
        short_fill = sell_price(float(short["bid"]), float(short["ask"]), f)
        best: dict[str, Any] | None = None
        too_wide = 0
        for _, long in puts[puts["strike"] < float(short["strike"])].sort_values("strike").iterrows():
            if not (float(long["rel_spread"]) <= cfg.max_leg_spread):
                too_wide += 1
                continue
            width = float(short["strike"]) - float(long["strike"])
            credit = short_fill - buy_price(float(long["bid"]), float(long["ask"]), f)
            fees = 2 * cfg.fee_per_contract
            max_loss = (width - credit) * MULTIPLIER + fees
            if credit <= 0 or max_loss <= 0 or max_loss > budget:
                continue
            if width < cfg.min_width_pct * chain.price or credit < cfg.min_credit_to_width * width:
                continue
            best = {  # strikes ascend: the first one that fits is the widest wing the budget allows
                "underlying": chain.symbol,
                "kind": "put_credit_spread",
                "expiry": expiry.date().isoformat(),
                "dte": dte,
                "contracts": max(1, int(budget // max_loss)),
                "short": _leg(short, -1, short_fill),
                "long": _leg(long, +1, buy_price(float(long["bid"]), float(long["ask"]), f)),
                "width": round(width, 4),
                "credit": round(credit, 4),
                "credit_mid": round(float(short["mid"]) - float(long["mid"]), 4),
                "max_loss_per_contract": round(max_loss, 2),
                "breakeven": round(float(short["strike"]) - credit, 2),
                "credit_to_width": round(credit / width, 4),
                "underlying_price": chain.price,
                "quote_ts": iso(chain.quote_ts),
            }
            break
        if best is not None:
            return best, ""
        reasons.append(
            f"{expiry.date().isoformat()}: nessuna ala entro il budget di {budget:,.0f} $".replace(",", ".")
            + (f" ({too_wide} strike scartati perché quotati troppo larghi)" if too_wide else "")
        )
    return None, "; ".join(reasons[:3])


def _leg(row: pd.Series, qty: int, fill: float) -> dict[str, Any]:
    return {
        "option": str(row["option"]),
        "right": str(row["right"]),
        "strike": float(row["strike"]),
        "qty": qty,
        "fill": round(float(fill), 4),
        "bid": float(row["bid"]),
        "ask": float(row["ask"]),
        "delta": None if pd.isna(row["delta"]) else round(float(row["delta"]), 4),
        "iv": None if pd.isna(row["iv"]) else round(float(row["iv"]), 4),
    }


def chain_quality(chain: Chain, cfg: OptionsConfig) -> dict[str, Any]:
    """What the chain looks like at the expiry the book would use: implied volatility by delta, the cost of a
    straddle (the market's own estimate of the move to that date) and how wide the quotes are."""
    out: dict[str, Any] = {
        "symbol": chain.symbol,
        "price": chain.price,
        "iv30": chain.iv30,
        "quote_ts": iso(chain.quote_ts),
        "rows": len(chain.frame),
    }
    window = expiries_in_window(chain, cfg)
    if not window:
        return out
    expiry, dte = window[0]
    side = chain.frame[chain.frame["expiry"] == expiry]
    points: dict[str, Any] = {}
    for right, name in (("P", "put"), ("C", "call")):
        legs = _quoted(side[side["right"] == right])
        for target in (0.50, 0.25, 0.10):
            if legs.empty:
                continue
            row = legs.iloc[int((legs["delta"].abs() - target).abs().to_numpy().argmin())]
            if abs(abs(float(row["delta"])) - target) > 0.08:
                continue
            points[f"{name}_{int(target * 100)}"] = {
                "strike": float(row["strike"]),
                "iv": None if pd.isna(row["iv"]) else round(float(row["iv"]), 4),
                "bid": float(row["bid"]),
                "ask": float(row["ask"]),
                "rel_spread": round(float(row["rel_spread"]), 4),
            }
    out.update({"expiry": expiry.date().isoformat(), "dte": dte, "points": points})
    atm = [points[k] for k in ("put_50", "call_50") if k in points]
    if len(atm) == 2:
        straddle = sum((p["bid"] + p["ask"]) / 2.0 for p in atm)
        out["straddle_pct"] = round(straddle / chain.price, 4)  # break-even move either way by the expiry
        ivs = [p["iv"] for p in atm if p["iv"]]
        out["atm_iv"] = round(float(np.mean(ivs)), 4) if ivs else None
    spreads = [p["rel_spread"] for k, p in points.items() if k.endswith(("_25", "_10"))]
    if spreads:
        out["median_rel_spread"] = round(float(np.median(spreads)), 4)
        out["tradeable"] = bool(np.median(spreads) <= cfg.max_leg_spread)
    return out


# ----------------------------------------------------------------------------------------------- the book
@dataclass
class OptionsBook:
    """Cash, open structures and the bookkeeping to rebuild them from JSON at every tick."""

    cfg: OptionsConfig
    store: StateStore | None
    initial_capital: float
    cash: float
    epoch: int = 1
    realized_pnl: float = 0.0
    fees: float = 0.0
    structures: list[dict[str, Any]] = field(default_factory=list)
    last_decision_day: str | None = None
    last_entry_day: str | None = None
    n_closed: int = 0
    n_wins: int = 0
    status: str = "active"
    started_at: str | None = None
    epochs: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ persistence
    @staticmethod
    def load(cfg: OptionsConfig, risk: RiskConfig, store: StateStore | None) -> OptionsBook:
        d = (store.read_json(STATE_FILE) if store is not None else None) or {}
        initial = float(d.get("initial_capital", risk.initial_capital))
        return OptionsBook(
            cfg=cfg,
            store=store,
            initial_capital=initial,
            cash=float(d.get("cash", initial)),
            epoch=int(d.get("epoch", 1)),
            realized_pnl=float(d.get("realized_pnl", 0.0)),
            fees=float(d.get("fees", 0.0)),
            structures=list(d.get("structures", [])),
            last_decision_day=d.get("last_decision_day"),
            last_entry_day=d.get("last_entry_day"),
            n_closed=int(d.get("n_closed", 0)),
            n_wins=int(d.get("n_wins", 0)),
            status=str(d.get("status", "active")),
            started_at=d.get("started_at"),
            epochs=list(d.get("epochs", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": f"desk-{self.cfg.id}",
            "epoch": self.epoch,
            "initial_capital": self.initial_capital,
            "cash": round(self.cash, 4),
            "equity": round(self.equity(), 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "fees": round(self.fees, 2),
            "structures": self.structures,
            "last_decision_day": self.last_decision_day,
            "last_entry_day": self.last_entry_day,
            "n_closed": self.n_closed,
            "n_wins": self.n_wins,
            "status": self.status,
            "started_at": self.started_at,
            "epochs": self.epochs,
        }

    def save(self) -> None:
        if self.store is not None:
            self.store.write_json(STATE_FILE, self.to_dict())

    # ------------------------------------------------------------------ views
    def liability(self) -> float:
        """Dollars it would cost to buy every open structure back at the marks."""
        return float(sum(float(s["mark"]) * MULTIPLIER * int(s["contracts"]) for s in self.structures))

    def equity(self) -> float:
        return self.cash - self.liability()

    def max_loss_open(self) -> float:
        """Dollars the book loses if every open structure ends at its worst (from the marks, not the entry)."""
        return float(
            sum((float(s["width"]) - float(s["mark"])) * MULTIPLIER * int(s["contracts"]) for s in self.structures)
        )

    def delta_notional(self) -> float:
        """Share-equivalent exposure in dollars (delta x 100 x contracts x price), signed: long is positive."""
        return float(sum(float(s.get("delta_notional") or 0.0) for s in self.structures))

    # ------------------------------------------------------------------ actions
    def open(self, cand: dict[str, Any], ts: datetime, day: date, forecast: float, rationale: str) -> dict[str, Any]:
        n = int(cand["contracts"])
        fees = 2 * self.cfg.fee_per_contract * n
        self.cash += float(cand["credit"]) * MULTIPLIER * n - fees
        self.fees += fees
        mark = max(0.0, min(float(cand["width"]), float(cand["credit_mid"])))
        structure = {
            "id": (
                f"{cand['underlying']}-{cand['expiry']}-{cand['short']['strike']:g}-{cand['long']['strike']:g}"
                f"-{day.isoformat()}"
            ),
            "kind": cand["kind"],
            "underlying": cand["underlying"],
            "expiry": cand["expiry"],
            "opened_at": iso(ts),
            "opened_day": day.isoformat(),
            "contracts": n,
            "short": cand["short"],
            "long": cand["long"],
            "width": cand["width"],
            "credit": cand["credit"],
            "max_loss": round((float(cand["width"]) - float(cand["credit"])) * MULTIPLIER * n + fees, 2),
            "max_gain": round(float(cand["credit"]) * MULTIPLIER * n - fees, 2),
            "breakeven": cand["breakeven"],
            "fees": round(fees, 2),
            "forecast": round(float(forecast), 2),
            "underlying_at_entry": cand["underlying_price"],
            "mark": round(mark, 4),
            "marked_at": cand["quote_ts"],
            "mark_stale": False,
            "delta_notional": None,
            "rationale": rationale,
        }
        self.structures.append(structure)
        self.last_entry_day = day.isoformat()
        self._log("trades", {"ts": iso(ts), "action": "open", "epoch": self.epoch, **_public(structure)}, ts)
        return structure

    def mark(self, chains: dict[str, Chain], now: datetime) -> None:
        """Mark each open structure at the mid of its two legs; a structure whose legs are not in a fresh
        chain keeps its last mark and says so."""
        for s in self.structures:
            chain = chains.get(str(s["underlying"]))
            s["mark_stale"] = True
            if chain is None:
                continue
            rows = chain.frame.set_index("option")
            short_id, long_id = str(s["short"]["option"]), str(s["long"]["option"])
            if short_id not in rows.index or long_id not in rows.index:
                continue
            short: pd.Series = rows.loc[short_id]  # type: ignore[assignment]
            long: pd.Series = rows.loc[long_id]  # type: ignore[assignment]
            if not (float(short["ask"]) > 0 and float(long["ask"]) > 0):
                continue
            value = float(short["mid"]) - float(long["mid"])
            s["mark"] = round(max(0.0, min(float(s["width"]), value)), 4)
            s["marked_at"] = iso(chain.quote_ts)
            s["mark_stale"] = chain.age_minutes(now) > 24 * 60
            s["underlying_last"] = chain.price
            delta = None
            if pd.notna(short["delta"]) and pd.notna(long["delta"]):
                # short one put (delta negative) and long a lower one: the spread is long the underlying
                delta = -float(short["delta"]) + float(long["delta"])
            s["delta_notional"] = (
                None if delta is None else round(delta * MULTIPLIER * int(s["contracts"]) * chain.price, 2)
            )

    def settle(self, closes: dict[str, pd.Series], chains: dict[str, Chain], now: datetime) -> list[dict[str, Any]]:
        """Expire what is due: intrinsic value on the official close of the expiry date."""
        local = ensure_utc(now).astimezone(NEW_YORK)
        done: list[dict[str, Any]] = []
        keep: list[dict[str, Any]] = []
        for s in self.structures:
            expiry = date.fromisoformat(str(s["expiry"]))
            due = local.date() > expiry or (local.date() == expiry and local.time() >= SETTLE_AFTER_NY)
            if not due:
                keep.append(s)
                continue
            series = closes.get(str(s["underlying"]))
            close: float | None = None
            approx = False
            if series is not None and pd.Timestamp(expiry) in series.index:
                close = float(series.loc[pd.Timestamp(expiry)])
            elif (local.date() - expiry).days >= SETTLE_FALLBACK_DAYS:
                chain = chains.get(str(s["underlying"]))
                last = None if series is None or series.empty else float(series.loc[: pd.Timestamp(expiry)].iloc[-1])
                close = last if last is not None else (None if chain is None else chain.price)
                approx = True
            if close is None or not math.isfinite(close) or close <= 0:
                s["awaiting_close"] = True
                keep.append(s)
                continue
            k_short, k_long = float(s["short"]["strike"]), float(s["long"]["strike"])
            value = max(0.0, k_short - close) - max(0.0, k_long - close)
            n = int(s["contracts"])
            self.cash -= value * MULTIPLIER * n
            pnl = (float(s["credit"]) - value) * MULTIPLIER * n - float(s["fees"])
            self.realized_pnl += pnl
            self.n_closed += 1
            self.n_wins += 1 if pnl > 0 else 0
            record = {
                "ts": iso(now),
                "action": "expiry",
                "epoch": self.epoch,
                **_public(s),
                "settle_price": close,
                "settle_value": round(value, 4),
                "pnl": round(pnl, 2),
                "r_multiple": round(pnl / float(s["max_loss"]), 3) if float(s["max_loss"]) > 0 else None,
                "approx": approx,
            }
            self._log("trades", record, now)
            done.append(record)
        self.structures = keep
        if self.equity() <= 0.05 * self.initial_capital:
            self.status = "dead"
        return done

    def reset(self, ts: datetime) -> None:
        self.epochs.append(
            {
                "epoch": self.epoch,
                "ended_at": iso(ts),
                "final_equity": round(self.equity(), 2),
                "realized_pnl": round(self.realized_pnl, 2),
                "n_closed": self.n_closed,
                "open_abandoned": len(self.structures),
            }
        )
        self.epoch += 1
        self.cash = self.initial_capital
        self.realized_pnl = 0.0
        self.fees = 0.0
        self.structures = []
        self.last_decision_day = None
        self.last_entry_day = None
        self.n_closed = 0
        self.n_wins = 0
        self.status = "active"
        self.started_at = iso(ts)

    def _log(self, name: str, record: dict[str, Any], ts: datetime) -> None:
        if self.store is not None:
            self.store.append_jsonl(name, _clean(record), ts=ts)


def _public(structure: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "kind",
        "underlying",
        "expiry",
        "contracts",
        "short",
        "long",
        "width",
        "credit",
        "max_loss",
        "max_gain",
        "breakeven",
        "forecast",
    )
    return {k: structure.get(k) for k in keys}


def _clean(obj: Any) -> Any:
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    if isinstance(obj, datetime):
        return iso(obj)
    if isinstance(obj, np.floating | np.integer):
        return _clean(obj.item())
    return obj


# ----------------------------------------------------------------------------------------------- the tick
def options_store(state_dir: Path, cfg: OptionsConfig | None = None) -> StateStore:
    return StateStore(Path(state_dir) / "desk" / (cfg.id if cfg is not None else OPTIONS_BOOK_ID))


def session_day(now: datetime) -> date | None:
    """The New York date when it is a US business day, else None."""
    day = ensure_utc(now).astimezone(NEW_YORK).date()
    return day if is_business_day(day, "US") else None


def session_open(now: datetime) -> bool:
    """True during the regular US option session. Outside it the chain on file holds the last quotes of the
    session, however recently its file was written (the fund keeps trading after hours and refreshes it)."""
    local = ensure_utc(now).astimezone(NEW_YORK)
    return session_day(now) is not None and MARKET_OPEN_NY <= local.time() < MARKET_CLOSE_NY


def decision_window(cfg: OptionsConfig, now: datetime) -> bool:
    """True between the decision time and the close of a US session."""
    local = ensure_utc(now).astimezone(NEW_YORK)
    if session_day(now) is None:
        return False
    return time(*cfg.decision_time_ny) <= local.time() < MARKET_CLOSE_NY


def decision_due(state_dir: Path, now: datetime, config_dir: Path | None = None) -> bool:
    """Does the book still owe today's decision? The scheduler asks before fetching, to refresh the chains
    for the one tick that trades on them."""
    cfg = load_options_config(config_dir)
    if cfg is None or not decision_window(cfg, now):
        return False
    state = options_store(state_dir, cfg).read_json(STATE_FILE) or {}
    day = session_day(now)
    return day is not None and state.get("last_decision_day") != day.isoformat()


def _closes(raw: RawStore, entry: str) -> pd.Series:
    frame = _daily(_latest(raw, entry))
    if frame.empty or "close" not in frame.columns:
        return pd.Series(dtype="float64")
    return frame["close"].astype("float64").dropna()


def _forecast(data: DeskData, key: str, day: date) -> tuple[float | None, float | None]:
    series = data.series.get(key)
    if series is None or series.forecasts.empty:
        return None, None
    rows = series.forecasts.loc[: pd.Timestamp(day)]
    if rows.empty or (pd.Timestamp(day) - pd.Timestamp(rows.index[-1])).days > 4:
        return None, None
    value = rows["combined"].iloc[-1]
    vol = series.vol.loc[: pd.Timestamp(day)]
    return (None if pd.isna(value) else float(value)), (
        None if vol.empty or pd.isna(vol.iloc[-1]) else float(vol.iloc[-1])
    )


def _business_days_between(a: date, b: date) -> int:
    n, day = 0, a
    while day < b and n < 400:
        day = add_business_days(day, 1, "US")
        n += 1
    return n


def _fmt(x: float | None, digits: int = 2) -> str:
    return "n/d" if x is None or not math.isfinite(x) else f"{x:.{digits}f}".replace(".", ",")


def options_tick(
    state_dir: Path,
    risk: RiskConfig,
    data: DeskData,
    raw: RawStore,
    now: datetime | None = None,
    cfg: OptionsConfig | None = None,
) -> dict[str, Any]:
    """Settle, mark, take the daily decision if it is due, snapshot. Idempotent within a session day."""
    now = ensure_utc(now or datetime.now(tz=UTC))
    cfg = cfg if cfg is not None else load_options_config()
    if cfg is None:
        return {"enabled": False}
    store = options_store(state_dir, cfg)
    book = OptionsBook.load(cfg, risk, store)
    if book.started_at is None:
        book.started_at = iso(now)
    chains: dict[str, Chain] = {}
    for u in cfg.underlyings:
        chain = load_chain(raw, u.chain, u.symbol)
        if chain is not None:
            chains[u.symbol] = chain
    closes = {u.symbol: _closes(raw, u.daily) for u in cfg.underlyings}

    settled = book.settle(closes, chains, now)
    book.mark(chains, now)

    report: dict[str, Any] = {"settled": len(settled), "opened": 0, "decisions": []}
    day = session_day(now)
    monitor: dict[str, Any] = {
        "generated_at": iso(now),
        "session_open": session_open(now),
        "underlyings": [],
        "rules": _rules(cfg),
    }
    # With no fresh chain at all there is nothing to decide ON: the decision stays owed, the scheduler keeps
    # refreshing the chains (``decision_due``) and the next tick inside the session takes it. The monitor below
    # still says, every tick, why nothing can be traded.
    fresh_any = any(c.age_minutes(now) <= cfg.max_quote_age_minutes for c in chains.values())
    deciding = day is not None and decision_window(cfg, now) and book.last_decision_day != day.isoformat() and fresh_any
    for u in cfg.underlyings:
        chain = chains.get(u.symbol)
        forecast, vol = _forecast(data, u.forecast, day or now.astimezone(NEW_YORK).date())
        entry: dict[str, Any] = {
            "symbol": u.symbol,
            "label": u.label_it,
            "forecast_series": u.forecast,
            "forecast": forecast,
            "realized_vol": vol,
            "threshold": cfg.forecast_threshold,
        }
        cand: dict[str, Any] | None = None
        why = "nessuna catena di opzioni archiviata"
        if chain is not None:
            entry.update(chain_quality(chain, cfg))
            entry["quote_age_minutes"] = round(max(0.0, chain.age_minutes(now)), 1)
            if entry.get("atm_iv") is not None and vol is not None:
                entry["iv_minus_rv"] = round(float(entry["atm_iv"]) - vol, 4)
            cand, why = candidate(chain, cfg, book.equity())
        entry["candidate"] = cand
        entry["no_candidate_reason"] = why if cand is None else ""
        gate = _gate(book, cfg, u, chain, cand, why, forecast, day, now)
        entry["gate"] = gate
        monitor["underlyings"].append(entry)
        if not deciding or day is None:
            continue
        decision = {
            "ts": iso(now),
            "day": day.isoformat(),
            "book": cfg.id,
            "underlying": u.symbol,
            "forecast": forecast,
            "action": "nessuna",
            "reason": gate["reason"],
            "candidate": cand,
        }
        if gate["open"] and cand is not None and forecast is not None:
            rationale = _rationale(cfg, u, cand, forecast)
            structure = book.open(cand, now, day, forecast, rationale)
            decision.update({"action": "aperta", "reason": rationale, "structure": structure["id"]})
            report["opened"] += 1
            cand, why = None, "struttura appena aperta"
        report["decisions"].append(decision)
        book._log("decisions", decision, now)
        if chain is not None and "points" in entry:
            book._log("smile", {"day": day.isoformat(), **{k: entry.get(k) for k in _SMILE_KEYS}}, now)
    if deciding and day is not None:
        book.last_decision_day = day.isoformat()
    book.mark(chains, now)
    book.save()
    equity = book.equity()
    snapshot = {
        "ts": iso(now),
        "epoch": book.epoch,
        "equity": round(equity, 2),
        "cash": round(book.cash, 2),
        "open_structures": len(book.structures),
        "max_loss_open": round(book.max_loss_open(), 2),
        "delta_notional": round(book.delta_notional(), 2),
        "leverage": round(abs(book.delta_notional()) / equity, 4) if equity > 0 else 0.0,
        "status": book.status,
    }
    store.append_jsonl("equity", snapshot, ts=now)
    store.write_json(MONITOR_FILE, _clean(monitor))
    report.update({"equity": snapshot["equity"], "open": len(book.structures), "status": book.status})
    return _clean(report)


_SMILE_KEYS = (
    "symbol",
    "price",
    "iv30",
    "expiry",
    "dte",
    "atm_iv",
    "straddle_pct",
    "median_rel_spread",
    "points",
    "quote_ts",
)


def _gate(
    book: OptionsBook,
    cfg: OptionsConfig,
    u: Underlying,
    chain: Chain | None,
    cand: dict[str, Any] | None,
    why: str,
    forecast: float | None,
    day: date | None,
    now: datetime,
) -> dict[str, Any]:
    """Every condition an entry needs, in the order they are checked, with the first one that fails."""
    checks: list[tuple[str, bool, str]] = []
    checks.append(("conto attivo", book.status == "active", "conto fermo: equity sotto il 5% del capitale"))
    fresh = chain is not None and chain.age_minutes(now) <= cfg.max_quote_age_minutes
    age = (
        "nessuna catena scaricata"
        if chain is None
        else f"quotazioni di {chain.age_minutes(now):.0f} minuti fa: servono meno di {cfg.max_quote_age_minutes} minuti"
    )
    checks.append(("quotazioni fresche", fresh, age))
    ok_forecast = forecast is not None and forecast >= cfg.forecast_threshold
    checks.append(
        (
            "previsione",
            ok_forecast,
            f"previsione {u.forecast} {_fmt(forecast, 1)} sotto la soglia di +{cfg.forecast_threshold:g}: "
            "nessuna vendita di put (il lato ribassista non ha mostrato margine e non si tratta)",
        )
    )
    checks.append(
        (
            "posti liberi",
            len(book.structures) < cfg.max_open,
            f"già {len(book.structures)} strutture aperte (massimo {cfg.max_open})",
        )
    )
    spaced = True
    if book.last_entry_day and day is not None:
        gap = _business_days_between(date.fromisoformat(book.last_entry_day), day)
        spaced = gap >= cfg.min_business_days_between_entries
    checks.append(
        (
            "distanza dall'ultima",
            spaced,
            f"ultima apertura il {book.last_entry_day}: una nuova ogni {cfg.min_business_days_between_entries} sedute",
        )
    )
    duplicate = cand is not None and any(
        s["underlying"] == cand["underlying"] and s["expiry"] == cand["expiry"] for s in book.structures
    )
    checks.append(("struttura negoziabile", cand is not None and not duplicate, why or "scadenza già in portafoglio"))
    failed = next((c for c in checks if not c[1]), None)
    return {
        "open": failed is None,
        "reason": "tutte le condizioni soddisfatte" if failed is None else failed[2],
        "checks": [{"name": name, "ok": ok} for name, ok, _ in checks],
    }


def _rationale(cfg: OptionsConfig, u: Underlying, cand: dict[str, Any], forecast: float) -> str:
    short, long = cand["short"], cand["long"]
    n = int(cand["contracts"])
    return (
        f"Previsione {u.forecast} {_fmt(forecast, 1)} (soglia +{cfg.forecast_threshold:g}). "
        f"Vendo {n} spread di put {u.symbol} {short['strike']:g}/{long['strike']:g} "
        f"scadenza {cand['expiry']} ({cand['dte']} giorni): incasso {_fmt(float(cand['credit']))} $ per azione "
        f"(a metà prezzo sarebbero {_fmt(float(cand['credit_mid']))}), "
        f"perdita massima {_fmt(float(cand['max_loss_per_contract']) * n, 0)} $, "
        f"pareggio a {_fmt(float(cand['breakeven']))} con il fondo a {_fmt(float(cand['underlying_price']))}."
    )


def _rules(cfg: OptionsConfig) -> dict[str, Any]:
    return {
        "forecast_threshold": cfg.forecast_threshold,
        "short_delta": cfg.short_delta,
        "dte": [cfg.dte_min, cfg.dte_target, cfg.dte_max],
        "risk_per_structure": cfg.risk_per_structure,
        "max_open": cfg.max_open,
        "max_leg_spread": cfg.max_leg_spread,
        "fill_fraction": cfg.fill_fraction,
        "max_quote_age_minutes": cfg.max_quote_age_minutes,
    }


def reset_options_book(state_dir: Path, risk: RiskConfig, ts: datetime, cfg: OptionsConfig | None = None) -> bool:
    cfg = cfg if cfg is not None else load_options_config()
    if cfg is None:
        return False
    book = OptionsBook.load(cfg, risk, options_store(state_dir, cfg))
    book.reset(ensure_utc(ts))
    book.save()
    return True


# ----------------------------------------------------------------------------------------------- the replay
# Everything below is a MODEL, used only to say whether the idea is worth a paper book. No free history of
# option quotes exists, so the replay prices with Black-Scholes on the OVX index (the 30-day implied volatility
# of USO options), a fixed smile and a fixed quoted spread, both read off the real chain of 2026-10-08.
SMILES: dict[str, dict[str, float]] = {
    "piatto": {"P25": 1.0, "P10": 1.0},
    # USO, 2026-10-08, 22 to 43 days: at the money 0.46, 25-delta put 0.465, 10-delta put 0.49
    "ottobre_2026": {"P25": 1.01, "P10": 1.065},
    # a steeper put wing, the usual shape outside a supply shock
    "put_ripido": {"P25": 1.06, "P10": 1.185},
}
REPLAY_LIFE = 21  # trading days to expiry at entry
REPLAY_WIDTH = 0.04  # wing width as a fraction of the price (what the 5 % budget buys at today's price)
REPLAY_QUOTED_SPREAD = (0.06, 0.0002)  # quoted spread = 6 % of the option mid + 2 bp of the underlying


_NORMAL = NormalDist()


def _norm_cdf(x: float) -> float:
    return _NORMAL.cdf(x)


def _norm_ppf(p: float) -> float:
    return _NORMAL.inv_cdf(p)


def bs_put(spot: float, strike: float, years: float, vol: float) -> float:
    """Black-Scholes put, zero rate. At or past expiry: intrinsic value."""
    if years <= 0 or vol <= 0:
        return max(0.0, strike - spot)
    sd = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * sd * sd) / sd
    return strike * _norm_cdf(-(d1 - sd)) - spot * _norm_cdf(-d1)


def put_strike_for_delta(spot: float, years: float, vol: float, delta: float) -> float:
    sd = vol * math.sqrt(years)
    return spot * math.exp(_norm_ppf(delta) * sd + 0.5 * sd * sd)


def put_delta(spot: float, strike: float, years: float, vol: float) -> float:
    sd = vol * math.sqrt(years)
    return _norm_cdf(-(math.log(spot / strike) + 0.5 * sd * sd) / sd)


def _smile(smile: dict[str, float], delta: float) -> float:
    p25, p10 = smile["P25"], smile["P10"]
    if delta >= 0.5:
        return 1.0
    if delta >= 0.25:
        return 1.0 + (p25 - 1.0) * (0.5 - delta) / 0.25
    return p25 + (p10 - p25) * (0.25 - delta) / 0.15


def replay(
    price: pd.Series,
    implied: pd.Series,
    forecast: pd.Series,
    cfg: OptionsConfig,
    smile: str = "ottobre_2026",
    cost_multiplier: float = 1.0,
    step: int | None = None,
) -> tuple[pd.Series, list[dict[str, Any]]]:
    """Daily equity (starting at 1) and the list of trades of the book's rules on model prices.

    ``price``: adjusted close of the fund. ``implied``: its 30-day implied volatility as a fraction. ``forecast``:
    the combined forecast known at each close. All three are read at the close of the entry day only.
    """
    index = price.index
    px = price.to_numpy(dtype="float64")
    iv = implied.reindex(index).ffill(limit=3).to_numpy(dtype="float64")
    fc = forecast.reindex(index).ffill(limit=3).to_numpy(dtype="float64")
    sm = SMILES[smile]
    step = cfg.min_business_days_between_entries if step is None else step
    a, b = REPLAY_QUOTED_SPREAD
    life_years = REPLAY_LIFE / 252.0
    equity = 1.0
    open_: list[dict[str, Any]] = []
    path = np.empty(len(index))
    trades: list[dict[str, Any]] = []
    last_entry = -(10**9)

    def quoted(mid: float, spot: float) -> float:
        return a * mid + b * spot

    for i in range(len(index)):
        spot, vol = px[i], iv[i]
        still: list[dict[str, Any]] = []
        for s in open_:
            remaining = (s["expiry_i"] - i) / 252.0
            sigma = vol if math.isfinite(vol) and vol > 0 else s["vol0"]
            value = bs_put(spot, s["k_short"], remaining, sigma * s["m_short"]) - bs_put(
                spot, s["k_long"], remaining, sigma * s["m_long"]
            )
            mark = (s["credit"] - value) * s["n"]
            equity += mark - s["mark"]
            s["mark"] = mark
            if i >= s["expiry_i"]:
                trades.append({"opened": index[s["i0"]], "closed": index[i], "r": mark / s["risk"], "pnl": mark})
            else:
                still.append(s)
        open_ = still
        path[i] = equity
        ready = (
            math.isfinite(vol)
            and vol > 0.05
            and math.isfinite(fc[i])
            and i + REPLAY_LIFE < len(index)
            and equity > 0.05
        )
        if not ready or fc[i] < cfg.forecast_threshold or len(open_) >= cfg.max_open or i - last_entry < step:
            continue
        m_short = _smile(sm, cfg.short_delta)
        k_short = put_strike_for_delta(spot, life_years, vol * m_short, cfg.short_delta)
        k_long = k_short - REPLAY_WIDTH * spot
        m_long = _smile(sm, put_delta(spot, k_long, life_years, vol))
        mid_short = bs_put(spot, k_short, life_years, vol * m_short)
        mid_long = bs_put(spot, k_long, life_years, vol * m_long)
        slip = (0.5 - cfg.fill_fraction) * cost_multiplier * (quoted(mid_short, spot) + quoted(mid_long, spot))
        credit = mid_short - mid_long - slip - 2 * cfg.fee_per_contract / MULTIPLIER
        width = k_short - k_long
        if credit < cfg.min_credit_to_width * width:
            continue
        risk_usd = cfg.risk_per_structure * equity
        n = risk_usd / (width - credit)
        mark0 = (credit - (mid_short - mid_long)) * n  # the entry cost is on the books from day one
        equity += mark0
        path[i] = equity
        last_entry = i
        open_.append(
            {
                "i0": i, "expiry_i": i + REPLAY_LIFE, "k_short": k_short, "k_long": k_long, "m_short": m_short,
                "m_long": m_long, "credit": credit, "n": n, "mark": mark0, "vol0": vol, "risk": risk_usd,
            }
        )  # fmt: skip
    return pd.Series(path, index=index, dtype="float64"), trades


def replay_payload(data: DeskData, raw: RawStore, cfg: OptionsConfig, initial_capital: float) -> dict[str, Any]:
    """The replay under every smile and at single and double cost, for ``desk/backtest.json``. Always ``approx``."""
    from engine.desk.backtest import downsample, performance, yearly_returns

    price = _daily(_latest(raw, "uso_daily"))
    series = data.series.get("MCL")
    if price.empty or data.ovx.empty or series is None:
        return {"approx": True, "available": False, "reason": "servono USO giornaliero, OVX e la previsione WTI"}
    column = "adjclose" if "adjclose" in price.columns and price["adjclose"].notna().any() else "close"
    px = price[column].astype("float64").dropna()
    px = px.loc[data.ovx.index[0] :]
    implied = data.ovx / 100.0
    forecast = series.forecasts["combined"]
    cases: list[dict[str, Any]] = []
    headline: dict[str, Any] = {}
    for smile in SMILES:
        for mult in (1.0, 2.0):
            equity, trades = replay(px, implied, forecast, cfg, smile=smile, cost_multiplier=mult)
            stats = performance(equity)
            rs = [t["r"] for t in trades]
            stats.update(
                {
                    "trades": len(trades),
                    "trades_per_year": round(len(trades) / max(1e-9, len(equity) / 252.0), 1),
                    "win_rate": round(float(np.mean([r > 0 for r in rs])), 3) if rs else None,
                    "avg_r": round(float(np.mean(rs)), 3) if rs else None,
                    "worst_r": round(float(np.min(rs)), 2) if rs else None,
                }
            )
            cases.append({"smile": smile, "cost_multiplier": mult, "stats": stats})
            if smile == "ottobre_2026" and mult == 1.0:
                headline = {
                    "stats": stats,
                    "yearly": {str(k): v for k, v in yearly_returns(equity).items()},
                    "curve": downsample(equity * initial_capital, 300),
                    "start": equity.index[0].date().isoformat(),
                    "end": equity.index[-1].date().isoformat(),
                }
    sharpes = [c["stats"].get("sharpe") for c in cases if c["stats"].get("sharpe") is not None]
    return {
        "approx": True,
        "available": True,
        "underlying": "USO",
        "method": (
            "Modello, non storico di quotazioni: Black-Scholes sull'indice OVX, smile e spread denaro-lettera "
            "fissi letti sulla catena USO dell'8 ottobre 2026, strike continui, 21 sedute alla scadenza, "
            "ala larga il 4% del prezzo."
        ),
        "sharpe_range": [min(sharpes), max(sharpes)] if sharpes else None,
        "headline": headline,
        "cases": cases,
    }
