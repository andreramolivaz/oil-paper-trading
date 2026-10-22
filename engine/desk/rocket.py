"""The Rocket: a 1 000 $ lottery on bought USO options (``docs/RESEARCH.md`` chapter 14).

The owner asked on 2026-10-22 for "another account that starts from 1 000 dollars and aims at about 10 000 in three
months, super aggressive, super leveraged, that can be restarted with a click", on the Dynamic's direction plus
Kalshi, with the project's risk rules suspended for this book alone. What the evidence says (chapter 14 E, a MODEL,
so every number carries ``approx``): since 2011 a 10x came in 1.8 % of the three-month windows, all of them in three
crashes bought with puts (autumn 2014, May 2015, early 2020); the median after three months is half the account, and
half of the windows lose more than half; in 2026 none of the 17 windows reached 2x. It is a lottery and says so.

Why bought options: with 1 000 $ a broker lends nothing (margin needs 2 000 $), one /MCL is ten times the account and
its margin takes nearly all of it, and a 2x fund never makes a 10x in three months. A bought option loses at most
its premium and gains convexly. USO has weekly expiries and one-dollar strikes, the desk already downloads its real
Cboe quotes, and the book can be copied on Robinhood and on Alpaca (bought options are level 2).

The rules, all of them (chapter 14 A; the constants and the keys of ``rocket_book`` carry the same names):

1. 1 000 $ at every life. At 5 % of that (50 $) or less the book is dead and stops; ``reset_rocket_book`` (the
   ``rocket-reset`` workflow, one click on the book's page) starts a new life at 1 000 $ and keeps the old ones.
2. Once per US session, at the first tick after 15:00 New York with a USO chain less than 45 minutes old. The ticks
   run at :18 and :48, so that is the 15:18 tick, the one that downloads the Brent fund's table for the Dynamic's own
   decision: both read the same forecast.
3. The signal of day D: the Dynamic's direction (the Brent fund's combined forecast, long at +5 or more, short at -5
   or less) and Kalshi's agreement (the weekly change of the de-escalation index as the oracle reads it, below zero
   for a long bet, above zero for a short one, its size not counted). A new bet needs both.
4. Long: calls; short: puts. The listed expiry closest to 30 days between 21 and 45; among the options of that
   expiry and side quoted no wider than 25 % of their mid and out of the money, the one whose delta is nearest 0.30
   if its cost fits in half the equity, else the next one further from the price, down to delta 0.10; none fits, no
   bet. As many whole contracts as half the equity buys at ask less a quarter of the spread plus 0.05 $ a contract,
   and never a delta notional above 10x the equity. One position at a time.
5. Sold (bid plus a quarter of the spread) when the Dynamic no longer points the bet's way; with 7 days or fewer left
   sold and, if rule 3 still holds, replaced the same day. No profit target. Not sold by the expiry: settled at
   intrinsic value on the fund's official close, as the options book does.

Suspended for this book (the owner's decision of 22 October): the options book's 5 % per structure and two
structures, and in spirit the 50 % volatility target - this book has none, and its risk is far above any other's.
Kept: real quotes less than 45 minutes old, never a price the feed did not bracket, the 10x on the delta notional,
the 5 % floor, fills on the bad side of the spread. It is out of the desk's totals and the candidates', in its own
group, never promoted, and has no row in the backtest's tables.

Whole contracts, which the model of 14 E did not have: with 1 000 $ one contract at delta 0.30 cost about 420 $ on
the chain of 16 October (42 % of the account, not half), and the cheapest contract the rule admitted cost 119 $, so
below about 240 $ nothing fits in half the account. The book then cannot bet and says so (``monitor.stuck``); a
reset starts a new life.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.config import load_config
from engine.core.store import StateStore
from engine.core.timeutil import ensure_utc, iso
from engine.data.raw_store import RawStore
from engine.desk import oracle
from engine.desk.data import DeskData
from engine.desk.options import (
    MULTIPLIER,
    NEW_YORK,
    SETTLE_FALLBACK_DAYS,
    Chain,
    Underlying,
    _clean,
    buy_price,
    load_chain,
    sell_price,
    session_close_ny,
    session_day,
    session_open,
    settle_after_ny,
    settled_closes,
)

log = logging.getLogger(__name__)

ROCKET_BOOK_ID = "rocket"
STATE_FILE = "book.json"
MONITOR_FILE = "monitor.json"
RESET_WORKFLOW = "rocket-reset.yml"
USO_OPTIONS = Underlying("USO", "uso_options", "uso_daily", "BNO", "WTI fund")
# What the model said once, before the first decision (docs/RESEARCH.md 14 E): shown on the page beside the book, a
# result and not a parameter. Three-month windows from 1 000 $ every week since 2011, the Dynamic alone.
MODEL_ODDS: dict[str, Any] = {
    "approx": True,
    "span": "2011-2026",
    "windows": 812,
    "p_10x": 0.0271,  # 22 of 812: the lives that reached the target, which ends them (14 H)
    "p_2x": 0.0764,  # 62 of 812 ended at 2x or more (the 22 among them)
    "p_lose_half": 0.1601,  # 130 of 812
    "p_dead": 0.0,
    "p_stuck": 0.4015,  # 326 of 812 ended unable to bet: nothing the rule admits fit in half the account
    "median_multiple": 0.68,
    "best_multiple": 33.0,
    "ten_x_episodes": ["autumn 2014", "summer and November 2015", "early 2020", "January 2026"],
    # the 812 windows by how they ended, worst first (the counts add up to 812): what the page draws as one bar
    "outcomes": [
        {"id": "lost_half", "label": "lost half or more", "windows": 130, "share": 0.1601},
        {"id": "between", "label": "between half and double", "windows": 620, "share": 0.7635},
        {"id": "doubled", "label": "doubled, short of 10x", "windows": 40, "share": 0.0493},
        {"id": "ten_x", "label": "reached 10x", "windows": 22, "share": 0.0271},
    ],
    "halves": {"2011-2018": 0.0347, "2019-2026": 0.0184},
    "windows_2026": 17,
    "p_2x_2026": 0.0,
    "median_multiple_2026": 0.83,
    "source": "docs/RESEARCH.md 14 G-H: Black-Scholes on the OVX index, whole contracts at today's price of USO, "
    "the life ending at 10x (no history of option quotes exists)",
}
ACTIONS = ("open", "sell", "roll", "target", "expiry")
TARGET_MULTIPLE = 10.0  # the life ends at the target (14 H): ten times its capital


@dataclass(frozen=True)
class RocketConfig:
    id: str = ROCKET_BOOK_ID
    name: str = "Rocket"
    initial_capital: float = 1000.0
    underlying: Underlying = USO_OPTIONS
    forecast_threshold: float = 5.0
    target_delta: float = 0.30
    min_delta: float = 0.10
    dte_min: int = 21
    dte_target: int = 30
    dte_max: int = 45
    roll_days: int = 7
    stake: float = 0.5
    max_delta_leverage: float = 10.0
    max_leg_spread: float = 0.25
    max_quote_age_minutes: int = 45
    fill_fraction: float = 0.25
    fee_per_contract: float = 0.05
    decision_time_ny: tuple[int, int] = (15, 0)
    floor: float = 0.05
    description: str = ""

    def __post_init__(self) -> None:
        if not (0 < self.stake <= 1.0):
            raise ValueError("rocket book: stake is a fraction of the equity in (0, 1]")
        if not (0 < self.min_delta <= self.target_delta < 0.5):
            raise ValueError("rocket book: 0 < min_delta <= target_delta < 0.5")
        if not (0 < self.max_delta_leverage <= 10.0):
            raise ValueError("rocket book: the delta notional never exceeds 10x the equity (the hard rule)")
        if not (0 <= self.fill_fraction <= 0.5):
            raise ValueError("rocket book: fill_fraction is between 0 (the bad side) and 0.5 (the mid)")
        if not (self.dte_min <= self.dte_target <= self.dte_max):
            raise ValueError("rocket book: dte_min <= dte_target <= dte_max")
        if self.initial_capital <= 0 or not (0 < self.floor < 1):
            raise ValueError("rocket book: a positive capital and a floor in (0, 1)")


def load_rocket_config(config_dir: Path | None = None) -> RocketConfig | None:
    """``rocket_book`` of ``config/books.yaml``; None when the section is missing or disabled."""
    raw = load_config("books", config_dir).get("rocket_book")
    if not isinstance(raw, dict) or not raw.get("enabled", True):
        return None
    names = {f.name for f in dataclasses.fields(RocketConfig)} - {"underlying", "decision_time_ny"}
    values: dict[str, Any] = {k: v for k, v in raw.items() if k in names}
    u = raw.get("underlying")
    if isinstance(u, dict):
        values["underlying"] = Underlying(
            symbol=str(u["symbol"]),
            chain=str(u["chain"]),
            daily=str(u["daily"]),
            forecast=str(u["forecast"]),
            label=str(u.get("label", "")),
        )
    when = raw.get("decision_time_ny")
    if isinstance(when, list | tuple) and len(when) == 2:
        values["decision_time_ny"] = (int(when[0]), int(when[1]))
    return dataclasses.replace(RocketConfig(), **values)


# ----------------------------------------------------------------------------------------------- the signal
@dataclass(frozen=True)
class RocketSignal:
    day: date
    forecast: float | None  # the Dynamic's: the Brent fund's combined forecast
    direction: int  # +1 long, -1 short, 0 none
    kalshi_change: float | None  # the de-escalation index's weekly change, in probability (0.01 = one point)
    kalshi_index: float | None
    kalshi_note: str
    agree: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "forecast": None if self.forecast is None else round(self.forecast, 2),
            "direction": self.direction,
            "kalshi_change": None if self.kalshi_change is None else round(self.kalshi_change, 4),
            "kalshi_index": None if self.kalshi_index is None else round(self.kalshi_index, 4),
            "kalshi_note": self.kalshi_note,
            "agree": self.agree,
            "text": self.text(),
        }

    def text(self) -> str:
        if self.forecast is None:
            return "No forecast of the Dynamic for today: nothing is decided and the position stays."
        side = {1: "long", -1: "short", 0: "no direction"}[self.direction]
        head = f"The Dynamic's forecast is {self.forecast:+.1f} ({side})."
        if self.kalshi_change is None:
            return f"{head} Kalshi: no weekly change of the de-escalation index to read ({self.kalshi_note})."
        points = self.kalshi_change * 100.0
        move = f"the de-escalation index moved {points:+.1f} points in a week"
        if self.direction == 0:
            return f"{head} Kalshi: {move}."
        verdict = "it agrees" if self.agree else "it does not agree"
        return f"{head} Kalshi: {move}, {verdict}."


def forecast_of_day(data: DeskData, key: str, day: date) -> float | None:
    """The combined forecast of series ``key`` on ``day`` itself, as the Dynamic's live decision reads it
    (``live._forecast_row``): yesterday's row is not today's forecast (found in review: with the fund's table
    missing, the book decided on the day before's forecast while the Dynamic rightly waited)."""
    series = data.series.get(key)
    if series is None or series.forecasts.empty:
        return None
    ts = pd.Timestamp(day)
    if ts not in series.forecasts.index:
        return None
    value = series.forecasts["combined"].get(ts)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def read_signal(data: DeskData, state_dir: Path, day: date, cfg: RocketConfig) -> RocketSignal:
    """Rule 3 for the decision of ``day``: the Dynamic's direction and Kalshi's agreement with it."""
    forecast = forecast_of_day(data, cfg.underlying.forecast, day)
    direction = 0
    if forecast is not None:
        direction = 1 if forecast >= cfg.forecast_threshold else (-1 if forecast <= -cfg.forecast_threshold else 0)
    change: float | None = None
    index: float | None = None
    note = ""
    try:
        verdict = oracle.forecast_for(StateStore(Path(state_dir) / "desk"), day)
        change = verdict.change if verdict.change is not None and math.isfinite(verdict.change) else None
        index = verdict.reading.value if verdict.reading is not None else None
        note = verdict.note
    except Exception as exc:  # the archive is on the side of the book: a bad line must not cost the tick
        log.warning("rocket: Kalshi reading failed: %s", exc)
        note = f"the archive could not be read ({type(exc).__name__})"
    agree = direction != 0 and change is not None and ((change < 0) if direction > 0 else (change > 0))
    return RocketSignal(day, forecast, direction, change, index, note, agree)


# ----------------------------------------------------------------------------------------------- the choice
def expiry_of(chain: Chain, cfg: RocketConfig) -> tuple[pd.Timestamp, int] | None:
    """The listed expiry closest to ``dte_target`` days between ``dte_min`` and ``dte_max`` (the earlier on a tie)."""
    today = pd.Timestamp(chain.today())
    listed = []
    for expiry in sorted(pd.unique(chain.frame["expiry"])):
        dte = int((pd.Timestamp(expiry) - today).days)
        if cfg.dte_min <= dte <= cfg.dte_max:
            listed.append((pd.Timestamp(expiry), dte))
    if not listed:
        return None
    return min(listed, key=lambda item: (abs(item[1] - cfg.dte_target), item[1]))


def _eligible(chain: Chain, cfg: RocketConfig, expiry: pd.Timestamp, right: str) -> pd.DataFrame:
    """The options rule 4 may buy at that expiry on that side: quoted on both sides, with a delta, no wider than the
    limit, out of the money, delta at least ``min_delta``; the absolute delta in ``abs_delta``, the furthest last."""
    side = chain.frame[(chain.frame["expiry"] == expiry) & (chain.frame["right"] == right)]
    legs = side[(side["bid"] > 0) & (side["ask"] > side["bid"]) & side["delta"].notna()].copy()
    legs["abs_delta"] = legs["delta"].abs()
    legs = legs[legs["strike"] > chain.price] if right == "C" else legs[legs["strike"] < chain.price]
    legs = legs[(legs["abs_delta"] >= cfg.min_delta) & (legs["rel_spread"] <= cfg.max_leg_spread)]
    return legs.sort_values("abs_delta", ascending=False)


def per_contract(row: pd.Series, cfg: RocketConfig) -> tuple[float, float]:
    """(the fill a share, what one contract costs with its fees)."""
    fill = buy_price(float(row["bid"]), float(row["ask"]), cfg.fill_fraction)
    return fill, fill * MULTIPLIER + cfg.fee_per_contract


def select(chain: Chain, cfg: RocketConfig, direction: int, equity: float) -> tuple[dict[str, Any] | None, str]:
    """The option rule 4 buys on this chain for a bet in ``direction``, or ``(None, why not)``."""
    if direction == 0:
        return None, "no direction"
    right, name = ("C", "call") if direction > 0 else ("P", "put")
    found = expiry_of(chain, cfg)
    if found is None:
        return None, f"no listed expiry between {cfg.dte_min} and {cfg.dte_max} days"
    expiry, dte = found
    legs = _eligible(chain, cfg, expiry, right)
    if legs.empty:
        return None, (
            f"no out-of-the-money {name} expiring {expiry.date().isoformat()} is quoted within "
            f"{cfg.max_leg_spread:.0%} of its price above delta {cfg.min_delta:.2f}"
        )
    budget = cfg.stake * equity
    nearest = float(legs.iloc[int((legs["abs_delta"] - cfg.target_delta).abs().to_numpy().argmin())]["abs_delta"])
    for _, row in legs[legs["abs_delta"] <= nearest].iterrows():
        fill, cost = per_contract(row, cfg)
        delta = float(row["abs_delta"])
        n = int(budget // cost) if cost > 0 else 0
        n = min(n, int(cfg.max_delta_leverage * equity // (delta * MULTIPLIER * chain.price)))
        if n < 1:
            continue
        return {
            "underlying": chain.symbol,
            "kind": "long_call" if right == "C" else "long_put",
            "right": right,
            "option": str(row["option"]),
            "strike": float(row["strike"]),
            "expiry": expiry.date().isoformat(),
            "dte": dte,
            "contracts": n,
            "fill": round(fill, 4),
            "bid": float(row["bid"]),
            "ask": float(row["ask"]),
            "mid": round(float(row["mid"]), 4),
            "delta": round(float(row["delta"]), 4),
            "iv": None if pd.isna(row["iv"]) else round(float(row["iv"]), 4),
            "fees": round(cfg.fee_per_contract * n, 2),
            "cost": round(cost * n, 2),
            "share_of_equity": round(cost * n / equity, 4) if equity > 0 else None,
            "delta_notional": round(delta * MULTIPLIER * n * chain.price * direction, 2),
            "underlying_price": chain.price,
            "quote_ts": iso(chain.quote_ts),
        }, ""
    return None, (
        f"no {name} expiring {expiry.date().isoformat()} between delta {nearest:.2f} and {cfg.min_delta:.2f} costs "
        f"less than half the account ({budget:,.2f} $)"
    )


def cheapest(chain: Chain, cfg: RocketConfig) -> float | None:
    """What the cheapest contract rule 4 could ever buy costs (either side, with its fees): below twice that, the book
    cannot bet whatever the signal."""
    found = expiry_of(chain, cfg)
    if found is None:
        return None
    costs = [
        per_contract(row, cfg)[1]
        for right in ("C", "P")
        for _, row in _eligible(chain, cfg, found[0], right).iterrows()
    ]
    return round(min(costs), 2) if costs else None


def occ_parts(option: str) -> dict[str, Any] | None:
    """The OCC symbol's parts (``USO261113C00175000``: root, expiry, right, strike): what a broker's form asks for."""
    if len(option) < 16 or option[-9] not in "CP":
        return None
    try:
        expiry = datetime.strptime(option[-15:-9], "%y%m%d").date()
        strike = int(option[-8:]) / 1000.0
    except ValueError:
        return None
    return {"root": option[:-15], "expiry": expiry.isoformat(), "right": option[-9], "strike": strike}


def replicate(option: str, contracts: int, limit: float, side: str) -> dict[str, Any]:
    """The same order on a real account, in the shape each broker takes it: Alpaca's order API (the OCC symbol, a
    limit good for the day; options take ``day`` or ``gtc``) and Robinhood's option form. Paper figures for a study,
    never advice."""
    parts = occ_parts(option) or {}
    right = "call" if parts.get("right") == "C" else "put"
    limit = round(limit, 2)
    verb = "Buy" if side == "buy" else "Sell"
    return {
        "alpaca": {
            "symbol": option,
            "qty": contracts,
            "side": side,
            "type": "limit",
            "limit_price": limit,
            "time_in_force": "day",
        },
        "robinhood": (
            f"{parts.get('root', '')}, Trade, Trade options: {verb}, {right.capitalize()}, expiring "
            f"{parts.get('expiry', '')}, strike {parts.get('strike', 0):g}; {contracts} contract"
            f"{'' if contracts == 1 else 's'}, limit {limit:.2f}"
        ),
    }


def _row(chain: Chain | None, option: str) -> pd.Series | None:
    if chain is None:
        return None
    rows = chain.frame[chain.frame["option"] == option]
    if rows.empty:
        return None
    row: pd.Series = rows.iloc[0]
    return row


def intrinsic(p: dict[str, Any], price: float) -> float:
    strike = float(p["strike"])
    return max(0.0, price - strike) if p["right"] == "C" else max(0.0, strike - price)


def beyond_archive(chain: Chain | None, p: dict[str, Any]) -> bool:
    """True when the held option is not in the chain because its strike lies outside the strikes the archive
    kept for its expiry and side (the download keeps a window around the fund's price): deep in the money after
    a large move, which is where the book's only big wins are. Such an option is valued and sold at its intrinsic
    value on the fund's delayed price, marked approx (found in review: it was held at a stale mark instead)."""
    if chain is None or _row(chain, str(p["option"])) is not None:
        return False
    expiry = pd.Timestamp(str(p["expiry"]))
    side = chain.frame[(chain.frame["expiry"] == expiry) & (chain.frame["right"] == p["right"])]
    if side.empty:
        return False
    strike = float(p["strike"])
    deep = strike > float(side["strike"].max()) if p["right"] == "P" else strike < float(side["strike"].min())
    return deep and intrinsic(p, chain.price) > 0


# ----------------------------------------------------------------------------------------------- the book
@dataclass
class RocketBook:
    """Cash, the one open option, the lives; rebuilt from JSON at every tick."""

    cfg: RocketConfig
    store: StateStore | None
    initial_capital: float
    cash: float
    epoch: int = 1
    realized_pnl: float = 0.0
    fees: float = 0.0
    position: dict[str, Any] | None = None
    last_decision_day: str | None = None
    n_closed: int = 0
    n_wins: int = 0
    status: str = "active"
    started_at: str | None = None
    peak_equity: float | None = None
    min_equity: float | None = None
    epochs: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def load(cfg: RocketConfig, store: StateStore | None) -> RocketBook:
        d = (store.read_json(STATE_FILE) if store is not None else None) or {}
        initial = float(d.get("initial_capital", cfg.initial_capital))
        return RocketBook(
            cfg=cfg,
            store=store,
            initial_capital=initial,
            cash=float(d.get("cash", initial)),
            epoch=int(d.get("epoch", 1)),
            realized_pnl=float(d.get("realized_pnl", 0.0)),
            fees=float(d.get("fees", 0.0)),
            position=d.get("position"),
            last_decision_day=d.get("last_decision_day"),
            n_closed=int(d.get("n_closed", 0)),
            n_wins=int(d.get("n_wins", 0)),
            status=str(d.get("status", "active")),
            started_at=d.get("started_at"),
            peak_equity=d.get("peak_equity"),
            min_equity=d.get("min_equity"),
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
            "position": self.position,
            "last_decision_day": self.last_decision_day,
            "n_closed": self.n_closed,
            "n_wins": self.n_wins,
            "status": self.status,
            "started_at": self.started_at,
            "peak_equity": None if self.peak_equity is None else round(self.peak_equity, 2),
            "min_equity": None if self.min_equity is None else round(self.min_equity, 2),
            "epochs": self.epochs,
        }

    def save(self) -> None:
        if self.store is not None:
            self.store.write_json(STATE_FILE, _clean(self.to_dict()))

    # ------------------------------------------------------------------ views
    def value(self) -> float:
        """The open option at its mark, in dollars."""
        p = self.position
        return 0.0 if p is None else float(p["mark"]) * MULTIPLIER * int(p["contracts"])

    def equity(self) -> float:
        return self.cash + self.value()

    def delta_notional(self) -> float:
        p = self.position
        return 0.0 if p is None else float(p.get("delta_notional") or 0.0)

    # ------------------------------------------------------------------ actions
    def open(self, cand: dict[str, Any], ts: datetime, day: date, signal: RocketSignal, rationale: str) -> None:
        self.cash -= float(cand["cost"])
        self.fees += float(cand["fees"])
        n = int(cand["contracts"])
        self.position = {
            "id": f"{cand['underlying']}-{cand['expiry']}-{cand['right']}{cand['strike']:g}-{day.isoformat()}",
            "kind": cand["kind"],
            "direction": 1 if cand["right"] == "C" else -1,
            "underlying": cand["underlying"],
            "option": cand["option"],
            "right": cand["right"],
            "strike": cand["strike"],
            "expiry": cand["expiry"],
            "contracts": n,
            "paid": cand["fill"],
            "cost": cand["cost"],
            "fees": cand["fees"],
            "opened_at": iso(ts),
            "opened_day": day.isoformat(),
            "mark": cand["mid"],
            "marked_at": cand["quote_ts"],
            "mark_stale": False,
            "delta": cand["delta"],
            "delta_notional": cand["delta_notional"],
            "underlying_at_entry": cand["underlying_price"],
            "underlying_last": cand["underlying_price"],
            "forecast": signal.forecast,
            "kalshi_change": signal.kalshi_change,
            "rationale": rationale,
            "replicate": replicate(str(cand["option"]), n, float(cand["fill"]), "buy"),
        }
        record = {"ts": iso(ts), "action": "open", "epoch": self.epoch, **_public(self.position), "price": cand["fill"]}
        record["equity_after"] = round(self.equity(), 2)
        record["replicate"] = self.position["replicate"]
        self._log("trades", record, ts)

    def close(
        self,
        price: float,
        ts: datetime,
        action: str,
        reason: str,
        approx: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sell the open option at ``price`` a share (``sell``, ``roll``, ``target``), or settle it at ``price``
        (``expiry``). ``extra`` goes into the trade's record (the settlement price of an expiry)."""
        p = self.position
        if p is None:
            raise ValueError("rocket book: nothing open to close")
        if action not in ACTIONS[1:]:
            raise ValueError(f"rocket book: unknown closing action {action!r}")
        n = int(p["contracts"])
        fees = self.cfg.fee_per_contract * n if action != "expiry" else 0.0
        proceeds = max(0.0, price) * MULTIPLIER * n - fees
        pnl = proceeds - float(p["cost"])
        self.cash += proceeds
        self.fees += fees
        self.realized_pnl += pnl
        self.n_closed += 1
        self.n_wins += 1 if pnl > 0 else 0
        record = {
            "ts": iso(ts),
            "action": action,
            "epoch": self.epoch,
            **_public(p),
            "price": round(max(0.0, price), 4),
            "proceeds": round(proceeds, 2),
            "pnl": round(pnl, 2),
            "multiple": round(proceeds / float(p["cost"]), 3) if float(p["cost"]) > 0 else None,
            "reason": reason,
            "approx": approx,
            "equity_after": round(self.cash, 2),
            **(extra or {}),
        }
        if action != "expiry":
            record["replicate"] = replicate(str(p["option"]), n, max(0.0, price), "sell")
        self._log("trades", record, ts)
        self.position = None
        return record

    def liquidation(self, chain: Chain | None) -> float | None:
        """What the open option would fetch now, a share: the bid plus a quarter of the spread on a two-sided quote,
        its intrinsic value when it lies beyond the archived strikes (approx), None when it cannot be sold."""
        p = self.position
        if p is None:
            return None
        row = _row(chain, str(p["option"]))
        if row is not None and float(row["bid"]) > 0 and float(row["ask"]) > float(row["bid"]):
            return sell_price(float(row["bid"]), float(row["ask"]), self.cfg.fill_fraction)
        if chain is not None and beyond_archive(chain, p):
            return intrinsic(p, chain.price)
        return None

    def at_target(self, chain: Chain | None) -> bool:
        """The life's target (14 H): the account, with the open option at what it would fetch, is worth at least
        ``TARGET_MULTIPLE`` times the capital of the life."""
        p = self.position
        value = 0.0
        if p is not None:
            price = self.liquidation(chain)
            if price is None:
                return False
            n = int(p["contracts"])
            value = price * MULTIPLIER * n - self.cfg.fee_per_contract * n
        return self.cash + value >= TARGET_MULTIPLE * self.initial_capital

    def mark(self, chain: Chain | None, now: datetime) -> None:
        """The open option at the mid of its quote; without one in the chain it keeps its last mark and says so."""
        p = self.position
        if p is None:
            return
        p["mark_stale"] = True
        row = _row(chain, str(p["option"]))
        if chain is not None and row is None and beyond_archive(chain, p):
            p["mark"] = round(intrinsic(p, chain.price), 4)
            p["marked_at"] = iso(chain.quote_ts)
            p["mark_stale"] = chain.age_minutes(now) > 24 * 60
            p["mark_approx"] = True
            p["underlying_last"] = chain.price
            p["delta"] = 1.0 if p["right"] == "C" else -1.0
            p["delta_notional"] = round(float(p["delta"]) * MULTIPLIER * int(p["contracts"]) * chain.price, 2)
            return
        if chain is None or row is None or not (float(row["ask"]) > 0):
            return
        p["mark_approx"] = False
        p["mark"] = round(max(0.0, float(row["mid"])), 4)
        p["marked_at"] = iso(chain.quote_ts)
        p["mark_stale"] = chain.age_minutes(now) > 24 * 60
        p["underlying_last"] = chain.price
        if pd.notna(row["delta"]):
            delta = float(row["delta"])
            p["delta"] = round(delta, 4)
            p["delta_notional"] = round(delta * MULTIPLIER * int(p["contracts"]) * chain.price, 2)

    def settle(self, closes: pd.Series, chain: Chain | None, now: datetime) -> dict[str, Any] | None:
        """An option still held on its expiry: intrinsic value on the fund's official close of that date."""
        p = self.position
        if p is None:
            return None
        local = ensure_utc(now).astimezone(NEW_YORK)
        expiry = date.fromisoformat(str(p["expiry"]))
        if not (local.date() > expiry or (local.date() == expiry and local.time() >= settle_after_ny(expiry))):
            return None
        close: float | None = None
        approx = False
        if pd.Timestamp(expiry) in closes.index:
            close = float(closes.loc[pd.Timestamp(expiry)])
        elif (local.date() - expiry).days >= SETTLE_FALLBACK_DAYS:
            before = closes.loc[: pd.Timestamp(expiry)]
            close = float(before.iloc[-1]) if not before.empty else (None if chain is None else chain.price)
            approx = True
        if close is None or not math.isfinite(close) or close <= 0:
            p["awaiting_close"] = True
            return None
        return self.close(
            intrinsic(p, close),
            now,
            "expiry",
            f"expired with {p['underlying']} at {close:.2f}",
            approx=approx,
            extra={"settle_price": close},
        )

    def check_floor(self) -> bool:
        """Dead at the floor: no new bet until a reset (rule 1). True when it has just died."""
        equity = self.equity()
        self.peak_equity = equity if self.peak_equity is None else max(self.peak_equity, equity)
        self.min_equity = equity if self.min_equity is None else min(self.min_equity, equity)
        if self.status == "active" and equity <= self.cfg.floor * self.initial_capital:
            self.status = "dead"
            return True
        return False

    def reset(self, ts: datetime) -> None:
        """A new life at the configured capital. The life that ends is archived with its figures; an option still
        open is abandoned, as a reset of any book closes what it holds."""
        self.epochs.append(
            {
                "epoch": self.epoch,
                "started_at": self.started_at,
                "ended_at": iso(ts),
                "start_equity": self.initial_capital,
                "final_equity": round(self.equity(), 2),
                "min_equity": None if self.min_equity is None else round(self.min_equity, 2),
                "peak_equity": None if self.peak_equity is None else round(self.peak_equity, 2),
                "realized_pnl": round(self.realized_pnl, 2),
                "n_closed": self.n_closed,
                "end_reason": self.status if self.status in ("dead", "target", "unreadable") else "reset_manual",
                "open_abandoned": 0 if self.position is None else 1,
            }
        )
        self.epoch += 1
        self.initial_capital = self.cfg.initial_capital
        self.cash = self.initial_capital
        self.realized_pnl = 0.0
        self.fees = 0.0
        self.position = None
        self.last_decision_day = None
        self.n_closed = 0
        self.n_wins = 0
        self.status = "active"
        self.started_at = iso(ts)
        self.peak_equity = None
        self.min_equity = None

    def _log(self, name: str, record: dict[str, Any], ts: datetime) -> None:
        if self.store is not None:
            self.store.append_jsonl(name, _clean(record), ts=ts)


def _public(p: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "kind",
        "underlying",
        "option",
        "right",
        "strike",
        "expiry",
        "contracts",
        "paid",
        "cost",
        "fees",
        "delta",
        "delta_notional",
        "underlying_at_entry",
        "forecast",
        "kalshi_change",
    )
    return {k: p.get(k) for k in keys}


# ----------------------------------------------------------------------------------------------- the tick
def rocket_store(state_dir: Path, cfg: RocketConfig | None = None) -> StateStore:
    return StateStore(Path(state_dir) / "desk" / (cfg.id if cfg is not None else ROCKET_BOOK_ID))


def decision_window(cfg: RocketConfig, now: datetime) -> bool:
    """True between the decision time and the close of a US session (none on a day that closes early)."""
    local = ensure_utc(now).astimezone(NEW_YORK)
    day = session_day(now)
    if day is None:
        return False
    return time(*cfg.decision_time_ny) <= local.time() < session_close_ny(day)


def decision_due(state_dir: Path, now: datetime, config_dir: Path | None = None) -> bool:
    """Does the book still owe today's decision? The scheduler asks before fetching, to refresh the chain. An
    unreadable state file owes nothing: it must not cost the tick (found in review)."""
    cfg = load_rocket_config(config_dir)
    if cfg is None or not decision_window(cfg, now):
        return False
    try:
        state = rocket_store(state_dir, cfg).read_json(STATE_FILE) or {}
    except (ValueError, TypeError):
        return False
    day = session_day(now)
    return day is not None and state.get("last_decision_day") != day.isoformat()


def _words(cand: dict[str, Any]) -> str:
    kind = "call" if cand["right"] == "C" else "put"
    n = int(cand["contracts"])
    return (
        f"{n} {cand['underlying']} {kind}{'' if n == 1 else 's'} at strike {cand['strike']:g} "
        f"expiring {cand['expiry']} ({cand['dte']} days, delta {abs(float(cand['delta'])):.2f}) "
        f"at {float(cand['fill']):.2f} $ a share: {float(cand['cost']):,.2f} $ with the fees, "
        f"{float(cand['share_of_equity'] or 0):.0%} of the account, "
        f"with the fund at {float(cand['underlying_price']):.2f}"
    )


def _held_words(p: dict[str, Any]) -> str:
    kind = "call" if p["right"] == "C" else "put"
    n = int(p["contracts"])
    return (
        f"{n} {p['underlying']} {kind}{'' if n == 1 else 's'} at strike {float(p['strike']):g} expiring {p['expiry']}"
    )


def _decide(
    book: RocketBook, cfg: RocketConfig, chain: Chain, signal: RocketSignal, day: date, now: datetime
) -> dict[str, Any]:
    """Rules 3 to 5 and the target of 14 H for today's decision: the target, the exit, then a new bet. The decision's
    record, for the log. A day without the Dynamic's forecast is not decided here (``rocket_tick`` waits)."""
    reasons: list[str] = [signal.text()]
    orders: list[dict[str, Any]] = []
    actions: list[str] = []
    p = book.position
    if book.status == "active" and book.at_target(chain):
        if p is not None:
            price = book.liquidation(chain)
            assert price is not None  # at_target needs a price
            approx = _row(chain, str(p["option"])) is None
            held, n = _held_words(p), int(p["contracts"])
            rec = book.close(price, now, "target", "the life reached its target: ten times its capital", approx=approx)
            actions.append("target")
            orders.append({"symbol": str(p["option"]), "units": -n})
            where = " (its intrinsic value: beyond the archived strikes)" if approx else ""
            reasons.append(
                f"Sold {held} at {price:.2f} $ a share{where}: {rec['pnl']:+,.2f} $ on the bet, "
                f"{float(rec['multiple'] or 0):.2f} times what it cost."
            )
        book.status = "target"
        reasons.append(
            f"The life reached its target: {book.equity():,.2f} $, at least ten times its "
            f"{book.initial_capital:,.0f} $. It ends here; a new one starts with the button."
        )
        p = None
    if p is not None:
        dte = (date.fromisoformat(str(p["expiry"])) - day).days
        action, why = None, ""
        if signal.direction != int(p["direction"]):
            action, why = "sell", "the Dynamic no longer points this way"
        elif dte <= cfg.roll_days:
            action, why = "roll", f"{dte} days to the expiry"
        if action is not None:
            price = book.liquidation(chain)
            if price is not None:
                approx = _row(chain, str(p["option"])) is None
                held, n = _held_words(p), int(p["contracts"])
                rec = book.close(price, now, action, why, approx=approx)
                actions.append(action)
                orders.append({"symbol": str(p["option"]), "units": -n})
                where = " (its intrinsic value: beyond the archived strikes)" if approx else ""
                reasons.append(
                    f"Sold {held} at {price:.2f} $ a share{where} ({why}): {rec['pnl']:+,.2f} $ on the bet, "
                    f"{float(rec['multiple'] or 0):.2f} times what it cost."
                )
            else:
                reasons.append(f"{why[:1].upper()}{why[1:]}, but the option has no bid: held for now.")
        else:
            reasons.append(f"Holding {_held_words(p)}: the Dynamic still points this way, {dte} days to the expiry.")
    if book.position is None:
        if book.status == "target":
            pass
        elif book.status != "active":
            reasons.append("The account is at or below 5% of its capital: no new bet until a reset.")
        elif signal.direction == 0:
            reasons.append("No new bet without a direction of the Dynamic.")
        elif not signal.agree:
            reasons.append("No new bet: Kalshi does not agree with the Dynamic.")
        else:
            cand, why_not = select(chain, cfg, signal.direction, book.equity())
            if cand is None:
                reasons.append(f"No option fits the rule: {why_not}.")
            else:
                text = f"Bought {_words(cand)}."
                book.open(cand, now, day, signal, text)
                actions.append("open")
                orders.append({"symbol": str(cand["option"]), "units": int(cand["contracts"])})
                reasons.append(text)
    return {
        "ts": iso(now),
        "day": day.isoformat(),
        "book": cfg.id,
        "underlying": cfg.underlying.symbol,
        "underlying_price": chain.price,
        "signal": signal.to_dict(),
        "forecast": signal.forecast,
        "action": "+".join(actions) if actions else "none",
        "orders": orders,
        "reason": " ".join(reasons),
        "position": None if book.position is None else book.position.get("id"),
        "held": book.position is not None,
        "status": book.status,
        "equity": round(book.equity(), 2),
    }


def rocket_tick(
    state_dir: Path,
    data: DeskData,
    raw: RawStore,
    now: datetime | None = None,
    cfg: RocketConfig | None = None,
    wait: str | None = None,
) -> dict[str, Any]:
    """Settle, mark, take the daily decision if it is due, snapshot. Idempotent within a session day. ``wait`` is the
    scheduler's reason for taking no decision this tick (the Dynamic's fund table is red, or its decision time passed
    while the tick was downloading): the decision stays owed, as the Dynamic's own does."""
    now = ensure_utc(now or datetime.now(tz=UTC))
    cfg = cfg if cfg is not None else load_rocket_config()
    if cfg is None:
        return {"enabled": False}
    store = rocket_store(state_dir, cfg)
    book = RocketBook.load(cfg, store)
    if book.started_at is None:
        book.started_at = iso(now)
    u = cfg.underlying
    chain = load_chain(raw, u.chain, u.symbol)
    report: dict[str, Any] = {"settled": 0, "opened": 0, "sold": 0, "decision": None, "waiting": None}
    if book.settle(settled_closes(raw, u.daily), chain, now) is not None:
        report["settled"] = 1
    book.mark(chain, now)
    died = book.check_floor()

    day = session_day(now)
    signal = read_signal(data, state_dir, day or ensure_utc(now).astimezone(NEW_YORK).date(), cfg)
    fresh = chain is not None and chain.age_minutes(now) <= cfg.max_quote_age_minutes
    owed = day is not None and decision_window(cfg, now) and book.last_decision_day != day.isoformat()
    waiting: str | None = None
    if owed and wait:
        waiting = wait
    elif owed and signal.forecast is None:
        waiting = "no forecast of the Dynamic for today yet: the decision waits for the next tick"
    elif owed and not fresh:
        waiting = "no USO quotes less than 45 minutes old: the decision waits for the next tick"
    if owed and day is not None and chain is not None and waiting is None:
        decision = _decide(book, cfg, chain, signal, day, now)
        book._log("decisions", decision, now)
        book.last_decision_day = day.isoformat()
        report["decision"] = decision
        report["opened"] = int("open" in decision["action"])
        report["sold"] = int(any(a in decision["action"] for a in ("sell", "roll", "target")))
        book.mark(chain, now)
        died = book.check_floor() or died
    report["waiting"] = waiting
    book.save()

    equity = book.equity()
    snapshot = {
        "ts": iso(now),
        "epoch": book.epoch,
        "equity": round(equity, 2),
        "cash": round(book.cash, 2),
        "value": round(book.value(), 2),
        "open": 0 if book.position is None else 1,
        "delta_notional": round(book.delta_notional(), 2),
        "leverage": round(abs(book.delta_notional()) / equity, 4) if equity > 0 else 0.0,
        "status": book.status,
    }
    store.append_jsonl("equity", snapshot, ts=now)
    store.write_json(MONITOR_FILE, _clean(monitor(book, cfg, chain, signal, now, waiting)))
    report.update({"equity": snapshot["equity"], "status": book.status, "died": died})
    return _clean(report)


def monitor(
    book: RocketBook,
    cfg: RocketConfig,
    chain: Chain | None,
    signal: RocketSignal,
    now: datetime,
    waiting: str | None = None,
) -> dict[str, Any]:
    """What the page shows beside the book every tick: the signal of the day, the bet the rule would place now (when
    nothing is open), whether the account is too small to bet at all, the rules and the model's odds."""
    candidate: dict[str, Any] | None = None
    why = ""
    low: float | None = None
    if chain is None:
        why = "no option chain archived"
    else:
        low = cheapest(chain, cfg)
        if book.position is not None:
            why = "a bet is open: one at a time"
        elif book.status != "active":
            why = "the life is over"
        elif signal.direction == 0:
            why = "no direction of the Dynamic"
        else:
            candidate, why = select(chain, cfg, signal.direction, book.equity())
            if candidate is not None:
                candidate["replicate"] = replicate(
                    str(candidate["option"]), int(candidate["contracts"]), float(candidate["fill"]), "buy"
                )
    stuck = book.position is None and low is not None and low > cfg.stake * book.equity()
    return {
        "generated_at": iso(now),
        "session_open": session_open(now),
        "quote_ts": None if chain is None else iso(chain.quote_ts),
        "quote_age_minutes": None if chain is None else round(max(0.0, chain.age_minutes(now)), 1),
        "underlying_price": None if chain is None else chain.price,
        "signal": signal.to_dict(),
        "candidate": candidate,
        "candidate_needs_kalshi": candidate is not None and not signal.agree,
        "no_candidate_reason": "" if candidate is not None else why,
        "cheapest_cost": low,
        # the equity below which nothing the rule admits fits in the stake (whole contracts): the page marks it
        "bet_floor": None if low is None else round(low / cfg.stake, 2),
        "bet_floor_multiple": None if low is None else round(low / cfg.stake / book.initial_capital, 4),
        "stuck": bool(stuck and book.status == "active"),
        "waiting": waiting,
        "epoch": book.epoch,
        "rules": rules(cfg),
        "odds": MODEL_ODDS,
    }


def rules(cfg: RocketConfig) -> dict[str, Any]:
    return {
        "initial_capital": cfg.initial_capital,
        "forecast_threshold": cfg.forecast_threshold,
        "target_delta": cfg.target_delta,
        "min_delta": cfg.min_delta,
        "dte": [cfg.dte_min, cfg.dte_target, cfg.dte_max],
        "roll_days": cfg.roll_days,
        "stake": cfg.stake,
        "max_delta_leverage": cfg.max_delta_leverage,
        "max_leg_spread": cfg.max_leg_spread,
        "fill_fraction": cfg.fill_fraction,
        "fee_per_contract": cfg.fee_per_contract,
        "max_quote_age_minutes": cfg.max_quote_age_minutes,
        "decision_time_ny": f"{cfg.decision_time_ny[0]:02d}:{cfg.decision_time_ny[1]:02d}",
        "floor": cfg.floor,
        "reset_workflow": RESET_WORKFLOW,
    }


def reset_rocket_book(state_dir: Path, ts: datetime, cfg: RocketConfig | None = None) -> bool:
    """A new life. An unreadable state file (a crash mid-write) is archived as such and the life starts fresh: the
    button must be able to repair the book (found in review). The monitor is rewritten for the new life, so the page
    does not show the old life's "too small to bet" until the next tick (found in review)."""
    cfg = cfg if cfg is not None else load_rocket_config()
    if cfg is None:
        return False
    store = rocket_store(state_dir, cfg)
    when = ensure_utc(ts)
    try:
        book = RocketBook.load(cfg, store)
    except (ValueError, TypeError, KeyError) as exc:
        log.warning("rocket: the state file could not be read (%s): a new life starts from a clean state", exc)
        book = RocketBook(cfg, store, cfg.initial_capital, cfg.initial_capital, status="unreadable")
    book.reset(when)
    book.save()
    fresh = RocketSignal(when.astimezone(NEW_YORK).date(), None, 0, None, None, "", False)
    note = "a new life: the next tick reads the day"
    store.write_json(MONITOR_FILE, _clean(monitor(book, cfg, None, fresh, when, note)))
    return True
