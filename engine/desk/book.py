"""One paper book: a vehicle, a risk budget and the rule that turns a forecast into an order.

Sizing is one formula (``signals.exposure_fraction``):

    exposure (multiple of equity) = forecast / 10 x volatility target / current volatility

then three ceilings, in this order: the book's own ``max_leverage``, the lower ``weekend_max_leverage`` on the
last decision before a market closure of more than one day, and the vehicle's margin. The paper broker
enforces the hard 10x on top of all of them. Nothing else raises or lowers a position: there is no gate to
pass and no discretionary override, which is the point - the three books differ only in how much of the same
forecast they buy.

A fund cannot be sold short by the account the desk imitates. A fund book that is allowed to be short
(``short_via``) holds its short side as a LONG position in an inverse fund: ``-2x`` a day, bought with cash,
half the dollars for the same exposure. The two legs are sized by the same formula and the same buffer, the
ceilings apply to the oil exposure and not to the dollars, and a decision that changes side sells one leg and
buys the other.

The exact size is then turned into whole lots with Carver's buffer (*Systematic Trading*, "position inertia"):
the position is left alone while it sits inside a band of 10 % of a normal-size position around the exact
size, and when it falls outside it is traded to the EDGE of the band, not to the middle. That is what keeps a
book from buying and selling the same lot every time the forecast crosses a rounding threshold - and with one
micro contract worth 0.9x of a ten-thousand-dollar account, every threshold is a big one.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from engine.broker.paper import PaperBroker
from engine.core.calendar import is_business_day
from engine.core.config import RiskConfig, load_config
from engine.core.events import Order, OrderReason, OrderType
from engine.core.ids import idempotency_key
from engine.core.store import StateStore
from engine.desk import signals as sg
from engine.desk.vehicles import INVERSE_FUNDS, InverseFund, Vehicle, get_inverse_fund, get_vehicle

HARD_CAP = 10.0
BOOKS_CONFIG = "books"


@dataclass(frozen=True)
class BookConfig:
    id: str
    name: str
    vehicle: str
    vol_target: float
    max_leverage: float
    weekend_max_leverage: float | None = None
    long_only: bool = False
    short_via: str | None = None  # the inverse fund a fund book buys to be short (vehicles.INVERSE_FUNDS)
    sleeves: dict[str, float] = field(default_factory=lambda: dict(sg.DEFAULT_WEIGHTS))
    buffer: float = 0.10  # no trade while the target is within this fraction of a normal-size position
    daily_loss_breaker: float = 0.08
    description: str = ""

    def __post_init__(self) -> None:
        if not (0 < self.max_leverage <= HARD_CAP):
            raise ValueError(f"book {self.id}: max_leverage {self.max_leverage} must be in (0, {HARD_CAP:g}]")
        if self.weekend_max_leverage is not None and not (0 <= self.weekend_max_leverage <= self.max_leverage):
            raise ValueError(f"book {self.id}: weekend_max_leverage must be within [0, max_leverage]")
        if not (0 < self.vol_target <= 2.0):
            raise ValueError(f"book {self.id}: vol_target {self.vol_target} out of range")
        unknown = set(self.sleeves) - set(sg.SLEEVES)
        if unknown:
            raise ValueError(f"book {self.id}: unknown sleeves {sorted(unknown)}")
        if self.short_via is not None:
            get_inverse_fund(self.short_via)
            if self.long_only:
                raise ValueError(f"book {self.id}: a long-only book has no short leg")
            if get_vehicle(self.vehicle).kind != "etf":
                raise ValueError(f"book {self.id}: short_via is for fund books, a futures book sells the future")


def load_books(config_dir: Path | None = None) -> list[BookConfig]:
    """Read ``config/books.yaml``. A book whose vehicle is unknown is a configuration error, not a skip."""
    raw = load_config(BOOKS_CONFIG, config_dir)
    books: list[BookConfig] = []
    for entry in raw.get("books", []):
        if not entry.get("enabled", True):
            continue
        get_vehicle(str(entry["vehicle"]))
        sleeves = {str(k): float(v) for k, v in (entry.get("sleeves") or sg.DEFAULT_WEIGHTS).items()}
        weekend = entry.get("weekend_max_leverage")
        books.append(
            BookConfig(
                id=str(entry["id"]),
                name=str(entry.get("name", entry["id"])),
                vehicle=str(entry["vehicle"]),
                vol_target=float(entry["vol_target"]),
                max_leverage=float(entry["max_leverage"]),
                weekend_max_leverage=None if weekend is None else float(weekend),
                long_only=bool(entry.get("long_only", False)),
                short_via=None if entry.get("short_via") is None else str(entry["short_via"]),
                sleeves=sleeves,
                buffer=float(entry.get("buffer", 0.10)),
                daily_loss_breaker=float(entry.get("daily_loss_breaker", 0.08)),
                description=str(entry.get("description", "")),
            )
        )
    ids = [b.id for b in books]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate book ids in config: {ids}")
    return books


def book_risk(base: RiskConfig, cfg: BookConfig, vehicle: Vehicle) -> RiskConfig:
    """The broker configuration of a book: the vehicle's real lot, margin and fees on top of the account rules.

    ``default_max_leverage`` is set to the book's ceiling: the desk has no alpha gate, the ceiling IS the policy.
    """
    max_lev = min(HARD_CAP, cfg.max_leverage, vehicle.max_leverage)
    costs = dict(base.costs or {})
    costs.update(
        {
            "commission_per_lot_usd": vehicle.commission_per_lot,
            "base_spread_bps": vehicle.half_spread_bps,
            # A ten-thousand-dollar account moving one to ten micro contracts or a few hundred fund shares has
            # no market impact; a small per-turn charge stands in for a tick of slippage on delayed quotes.
            "slippage_bps_per_turn": 0.25,
            "max_slippage_bps": 5.0,
        }
    )
    raw = dict(base.raw or {})
    raw["margin"] = {
        **dict(raw.get("margin", {})),
        "rate": vehicle.margin_rate,
        "stop_out_level": vehicle.stop_out_level,
    }
    risk = dataclasses.replace(
        base,
        lot_bbl=vehicle.lot,
        max_leverage=max_lev,
        default_max_leverage=max_lev,
        margin_rate=vehicle.margin_rate,
        stop_out_margin_level=vehicle.stop_out_level,
        daily_loss_breaker=cfg.daily_loss_breaker,
        costs=costs,
        raw=raw,
    )
    return risk


def _round_half_away(x: float) -> int:
    """Round to the nearest integer, halves away from zero (Python's ``round`` sends 0.5 to 0 and 1.5 to 2)."""
    return math.floor(abs(x) + 0.5) * (1 if x >= 0 else -1)


def buffered_target(exact: float, buffer: float, current: float, cap: float) -> float:
    """The position to hold, in lots, given the exact size wanted, the buffer, what is held and the ceiling.

    ``exact`` is signed and fractional. Inside ``[round(exact - buffer), round(exact + buffer)]`` the current
    position stays; outside, it moves to the nearer edge. An exact size of zero means "no view" or "not
    allowed" and is always flat: inertia must not keep a book in a position its forecast no longer wants at
    all. The result never exceeds ``cap`` (rounded down to whole lots) in either direction.
    """
    if exact == 0.0 or not math.isfinite(exact):
        target = 0.0
    else:
        lower = _round_half_away(exact - buffer)
        upper = _round_half_away(exact + buffer)
        target = float(lower) if current < lower else (float(upper) if current > upper else float(current))
    whole_cap = float(math.floor(max(0.0, cap) + 1e-9))
    return max(-whole_cap, min(whole_cap, target))


def is_pre_closure(day: date) -> bool:
    """True on the last US business day before a closure of more than one calendar day (weekends, holidays)."""
    nxt = day + timedelta(days=1)
    while not is_business_day(nxt, "US"):
        nxt += timedelta(days=1)
    return (nxt - day).days > 1


@dataclass
class Decision:
    """What a book decided and why. Written to the log whether or not an order follows: flat has a reason too."""

    ts: datetime
    book: str
    vehicle: str
    symbol: str
    price: float
    equity: float
    forecast: dict[str, float | None]
    vol: float | None
    exposure_raw: float
    exposure: float
    limited_by: str
    target_units: float
    current_units: float
    order_units: float
    rationale: str
    status: str = ""
    exposure_actual: float = 0.0  # what the target position is worth, as a multiple of equity (whole lots)
    # the inverse fund of a book that has one: symbol, price, target_units, current_units, order_units, multiplier
    legs: list[dict[str, Any]] = field(default_factory=list)
    n_orders: int = 0  # orders this decision queued (0, 1, or 2 when a book with a short leg changes side)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["ts"] = self.ts.isoformat().replace("+00:00", "Z")
        return d


SOURCE_LABEL_IT = {"prezzo": "prezzo", "curva": "curva", "macro": "altri mercati"}


def _signed(x: float | None, digits: int = 1) -> str:
    """Signed number with an Italian decimal comma ('+6,2'); 'n/d' when missing."""
    if x is None or not math.isfinite(x):
        return "n/d"
    return f"{x:+.{digits}f}".replace(".", ",")


def _plain(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def _units(x: float) -> str:
    return f"{abs(x):,.0f}".replace(",", ".")


def _pct(x: float | None) -> str:
    if x is None or not math.isfinite(x):
        return "n/d"
    return f"{x * 100:.0f}%"


class Book:
    def __init__(
        self,
        cfg: BookConfig,
        base_risk: RiskConfig,
        store: StateStore | None = None,
        cost_multiplier: float = 1.0,
    ) -> None:
        self.cfg = cfg
        self.vehicle = get_vehicle(cfg.vehicle)
        self.short: InverseFund | None = None if cfg.short_via is None else get_inverse_fund(cfg.short_via)
        self.risk = book_risk(base_risk, cfg, self.vehicle)
        self.store = store
        account_id = f"desk-{cfg.id}"
        from engine.broker.costs import CostModel

        costs = CostModel(self.risk, multiplier=cost_multiplier)
        self.broker = (
            PaperBroker.load(account_id, self.risk, store, costs=costs)
            if store is not None
            else PaperBroker(account_id, self.risk, costs=costs)
        )
        # The broker's leverage cap is in dollars; the book's ceiling is in oil exposure. An inverse fund is
        # counted for its multiple, so the cap holds at the FILL price and after every mark, not only at the
        # price the decision saw: a short side decided at its ceiling and filled on a gap would otherwise end
        # above it, bought partly on margin. Set for every book: a fund that is not held weighs nothing.
        self.broker.exposure_weights = {fund.id: abs(fund.multiplier) for fund in INVERSE_FUNDS.values()}
        # a book that changes side sells one leg and buys the other in the same decision: the purchase is
        # accepted against what the queued sale is about to free (and checked again at its own fill price)
        self.broker.net_queued_sales = True

    # ------------------------------------------------------------------ views
    @property
    def id(self) -> str:
        return self.cfg.id

    def units(self, symbol: str) -> float:
        pos = self.broker.positions.get(symbol)
        return 0.0 if pos is None else float(pos.qty_bbl)

    @property
    def short_symbol(self) -> str | None:
        return None if self.short is None else self.short.id

    def leg(self) -> InverseFund | None:
        """The inverse fund this book's short side lives in: the configured one, or - when the configuration
        no longer has one - a fund the book still HOLDS. Such an orphan is only ever sold: a book must not be
        left with a position nothing manages because a line was removed from a file."""
        if self.short is not None:
            return self.short
        for symbol, pos in self.broker.positions.items():
            if symbol in INVERSE_FUNDS and pos.qty_bbl != 0:
                return INVERSE_FUNDS[symbol]
        return None

    def uses(self, symbol: str) -> bool:
        """True when ``symbol`` is an inverse fund this book is configured for, holds or has an order on."""
        if symbol not in INVERSE_FUNDS:
            return False
        held = self.broker.positions.get(symbol)
        queued = any(o.instrument == symbol for o in self.broker.pending_orders())
        return symbol == self.short_symbol or queued or (held is not None and held.qty_bbl != 0)

    def other_contract_positions(self, symbol: str) -> list[str]:
        """Symbols of open positions that are not ``symbol`` (an old contract waiting to be rolled). An inverse
        fund is a book's other LEG, never a contract to roll."""
        return [
            s for s, p in self.broker.positions.items() if s != symbol and s not in INVERSE_FUNDS and p.qty_bbl != 0
        ]

    @staticmethod
    def multiplier(symbol: str) -> float:
        """Oil exposure of one dollar held in ``symbol``: 1 for a vehicle, -2 for the inverse fund."""
        return INVERSE_FUNDS[symbol].multiplier if symbol in INVERSE_FUNDS else 1.0

    def net_exposure(self, equity: float) -> float:
        """Signed oil exposure of what is held, as a multiple of ``equity``, at the last marks."""
        return self._exposure_sum(equity, signed=True)

    def effective_leverage(self, equity: float) -> float:
        """Absolute oil exposure of what is held, as a multiple of ``equity``. For a book with one instrument
        this is the broker's leverage; an inverse fund counts for twice its dollars, which is what it risks."""
        return self._exposure_sum(equity, signed=False)

    def _exposure_sum(self, equity: float, signed: bool) -> float:
        if not math.isfinite(equity) or equity <= 0:
            return 0.0
        st = self.broker.state
        total = 0.0
        for symbol, pos in st.positions.items():
            price = st.last_prices.get(symbol, pos.last_price if pos.last_price is not None else pos.avg_price)
            worth = float(pos.qty_bbl) * float(price) * self.multiplier(symbol)
            total += worth if signed else abs(worth)
        return total / equity

    # ------------------------------------------------------------------ sizing
    def _cap(self, day: date) -> float:
        """The leverage ceiling in force for a decision taken on ``day``."""
        cfg = self.cfg
        cap = min(HARD_CAP, cfg.max_leverage, self.vehicle.max_leverage)
        if cfg.weekend_max_leverage is not None and is_pre_closure(day):
            cap = min(cap, cfg.weekend_max_leverage)
        return cap

    def exposure(self, forecast: float, vol: float, day: date) -> tuple[float, float, str]:
        """(uncapped exposure, exposure after the ceilings, what limited it)."""
        cfg = self.cfg
        raw = sg.exposure_fraction(forecast, vol, cfg.vol_target, max_leverage=math.inf, long_only=cfg.long_only)
        if not self.vehicle.allow_short and self.short is None:
            raw = max(0.0, raw)
        full_cap = min(HARD_CAP, cfg.max_leverage, self.vehicle.max_leverage)
        cap = self._cap(day)
        limited = "obiettivo di volatilità"
        if abs(raw) > cap:
            limited = "tetto prima della chiusura dei mercati" if cap < full_cap else "tetto di leva del libro"
        capped = max(-cap, min(cap, raw))
        if self.short is not None and capped < -self.short.max_exposure:
            # the short side is an inverse fund bought with cash: it cannot carry more than its own multiple
            capped, limited = -self.short.max_exposure, "il fondo inverso si compra solo in contanti"
        if raw == 0.0:
            limited = "nessuna previsione" if forecast == 0 or not math.isfinite(forecast) else "solo long"
        return raw, capped, limited

    def decide(
        self,
        ts: datetime,
        day: date,
        symbol: str,
        price: float,
        forecast: dict[str, float | None],
        vol: float | None,
    ) -> tuple[Decision, Order | None]:
        """Turn today's forecast into at most one order on ``symbol``. Never raises on missing inputs: a book
        that cannot size (no forecast, no volatility, no price) holds what it has and says why.

        A book with a short leg can queue two orders in one decision and must be asked through :meth:`orders`.
        """
        if self.short is not None:
            raise ValueError(f"book {self.id} has a short leg: use orders(), which returns both of its orders")
        decision, orders = self.orders(ts, day, symbol, price, forecast, vol)
        return decision, (orders[0] if orders else None)

    def orders(
        self,
        ts: datetime,
        day: date,
        symbol: str,
        price: float,
        forecast: dict[str, float | None],
        vol: float | None,
        leg_price: float | None = None,
    ) -> tuple[Decision, list[Order]]:
        """The decision of the day and the orders it queues: none, one, or - for a book with a short leg that
        changes side - the sale of one leg and the purchase of the other.

        ``leg_price`` is a RECENT price of the inverse fund. Without one the short side is never opened or
        added to: a side already held is sized on its last mark and can only shrink (a book is not put into an
        instrument at a price nobody has seen for days, and is not kept from leaving one either).
        """
        cfg, leg = self.cfg, self.leg()
        orphan = leg is not None and self.short is None  # held, but no longer in the configuration: sold
        snap = self.broker.snapshot(ts)
        equity = float(snap.equity)
        current = self.units(symbol)
        leg_current = 0.0 if leg is None else self.units(leg.id)
        fresh = leg_price is not None and math.isfinite(leg_price) and leg_price > 0
        leg_ref = float(leg_price) if fresh and leg_price is not None else self._last_mark(leg, leg_current)
        # the book's own mix of the sleeves (config/books.yaml), not the equal-weight column of the data layer
        combined = sg.combine_values(forecast, cfg.sleeves)
        forecast = {**forecast, "combined": combined}
        usable = (
            combined is not None
            and math.isfinite(combined)
            and vol is not None
            and math.isfinite(vol)
            and vol > 0
            and price > 0
            and equity > 0
        )
        if not usable:
            reason = (
                "Dati insufficienti per dimensionare (previsione, volatilità o prezzo mancanti): posizione invariata."
            )
            dec = Decision(ts, cfg.id, cfg.vehicle, symbol, price, equity, forecast, vol, 0.0, 0.0, "dati mancanti",
                           current, current, 0.0, reason, str(snap.status))  # fmt: skip
            dec.legs = self._leg_rows(leg, leg_ref, leg_current, leg_current, 0.0)
            return dec, []
        assert combined is not None and vol is not None
        raw, exposure, limited = self.exposure(float(combined), float(vol), day)
        ceiling = limited  # what cut the exposure wanted, if anything did: the words for "sarebbe ... x"
        lot = self.vehicle.lot
        per_lot = equity / price / lot  # lots that one times the equity buys
        band = cfg.buffer * cfg.vol_target / float(vol)  # the buffer, as a multiple of equity
        target_lots = buffered_target(
            exact=exposure * per_lot, buffer=band * per_lot, current=current / lot, cap=self._cap(day) * per_lot
        )
        if cfg.long_only or not self.vehicle.allow_short:
            target_lots = max(0.0, target_lots)
        target = target_lots * lot
        delta = target - current
        delta = math.copysign(math.floor(abs(delta) / lot + 1e-9) * lot, delta) if delta else 0.0
        # The short leg: the same formula and the same buffer on the other side of zero. One dollar of the
        # inverse fund is worth |multiplier| dollars of short exposure, so the same band is half as many dollars.
        leg_target, leg_delta = leg_current, 0.0
        if leg is not None and leg_ref is not None:
            per_share = equity / leg_ref / abs(leg.multiplier)  # shares that carry 1x of short exposure
            leg_target = max(
                0.0,
                buffered_target(
                    exact=-exposure * per_share,
                    buffer=band * per_share,
                    current=leg_current,
                    cap=min(self._cap(day), leg.max_exposure) * per_share,
                ),
            )
            if orphan:
                leg_target = 0.0
            elif not fresh:
                leg_target = min(leg_target, leg_current)  # an old mark may close a side, never open one
            leg_delta = float(_round_half_away(leg_target - leg_current))
        actual = target * price / equity
        if leg is not None and leg_ref is not None:
            actual += leg.multiplier * leg_target * leg_ref / equity
        if exposure != 0 and target == 0 and leg_target == 0:
            # wanted, but smaller than what the position sizes allow: a whole lot for a future, the edge of
            # the inertia band for a fund of one-share lots
            limited = "lotto minimo" if lot > 1 else "fascia di inerzia"
        unpriced = exposure < 0 and leg is not None and not fresh  # a short is wanted and cannot be opened
        if unpriced and leg is not None and leg_delta >= 0:
            limited = f"nessun prezzo per {leg.id}"
        rationale = self._rationale(
            forecast, float(vol), raw, exposure, ceiling, target, current, delta, symbol, actual,
            leg, leg_ref, leg_target, leg_current, leg_delta, equity, price, fresh,
        )  # fmt: skip
        dec = Decision(ts, cfg.id, cfg.vehicle, symbol, price, equity, forecast, float(vol), raw, exposure, limited,
                       target, current, delta, rationale, str(snap.status), actual)  # fmt: skip
        dec.legs = self._leg_rows(leg, leg_ref, leg_target, leg_current, leg_delta)
        orders: list[Order] = []
        levered = {
            "value": round(abs(actual), 4),
            "wanted": round(abs(exposure), 4),
            "limited_by": limited,
            "components": {"vol_target": cfg.vol_target, "vol": float(vol), "cap": cfg.max_leverage},
        }
        # (symbol, units to trade, units held, oil exposure of one unit bought, can it be sold short)
        wanted = [(symbol, delta, current, 1.0, self.vehicle.allow_short)]
        if leg is not None:
            wanted.append((leg.id, leg_delta, leg_current, leg.multiplier, False))
        sale_id: str | None = None
        for instrument, units, held, direction, shortable in sorted(wanted, key=lambda w: w[1] > 0):  # sales first
            if units == 0.0:
                continue
            key = idempotency_key("desk", cfg.id, snap.epoch, day.isoformat(), instrument)
            push = units * direction  # positive: this order adds long oil exposure
            order = Order(
                order_id=key[:16],
                idempotency_key=key,
                ts=ts,
                account_id=self.broker.account_id,
                instrument=instrument,
                qty_bbl=units,
                order_type=OrderType.MARKET,
                reason=OrderReason.SIGNAL if held == 0 else OrderReason.REBALANCE,
                rationale=rationale,
                strategies_for=[k for k in sg.SLEEVES if (forecast.get(k) or 0.0) * push > 0],
                strategies_against=[k for k in sg.SLEEVES if (forecast.get(k) or 0.0) * push < 0],
                leverage=levered,
                # a fund cannot be short: its sale closes what is there when it fills, never more
                reduce_only=units < 0 and not shortable,
                # the purchase of one leg is paid for by the sale of the other and waits for it: if the two
                # tables deliver their bars a tick apart, the book must not hold both sides in between
                after=sale_id if units > 0 else None,
            )
            if units < 0:
                sale_id = order.order_id
            orders.append(order)
        dec.n_orders = len(orders)
        return dec, orders

    def _last_mark(self, leg: InverseFund | None, held: float) -> float | None:
        """The last mark of a short side that is HELD: the only price an old side can be reduced on. A fund
        the book does not hold has no price worth trading on unless the caller brings a recent one."""
        if leg is None or held <= 0:
            return None
        known = self.broker.state.last_prices.get(leg.id)
        return float(known) if known is not None and math.isfinite(known) and known > 0 else None

    @staticmethod
    def _leg_rows(
        leg: InverseFund | None, price: float | None, target: float, current: float, delta: float
    ) -> list[dict[str, Any]]:
        if leg is None:
            return []
        return [
            {
                "symbol": leg.id,
                "price": price,
                "target_units": target,
                "current_units": current,
                "order_units": delta,
                "multiplier": leg.multiplier,
            }
        ]

    def _rationale(
        self,
        forecast: dict[str, float | None],
        vol: float,
        raw: float,
        exposure: float,
        limited: str,
        target: float,
        current: float,
        delta: float,
        symbol: str,
        actual: float,
        leg: InverseFund | None = None,
        leg_price: float | None = None,
        leg_target: float = 0.0,
        leg_current: float = 0.0,
        leg_delta: float = 0.0,
        equity: float = 0.0,
        price: float = 0.0,
        fresh: bool = True,
    ) -> str:
        v = self.vehicle
        # the three sources of the forecast, each the average of its sleeves (the sleeves are in the table)
        sources = sg.source_values(forecast, self.cfg.sleeves)
        parts = ", ".join(f"{SOURCE_LABEL_IT.get(name, name)} {_signed(value)}" for name, value in sources.items())
        text = f"{parts[:1].upper()}{parts[1:]}: previsione {_signed(forecast.get('combined'))} su 20. "
        if exposure == 0 and limited == "solo long":
            # a negative forecast in a book that cannot be short: there is no size to explain, only the reason
            text += "Negativa, e questo libro non va short: resta in contanti"
        else:
            text += (
                f"Volatilità {_pct(vol)} contro un obiettivo del {_pct(self.cfg.vol_target)}: "
                f"esposizione {_plain(abs(exposure))}x"
            )
        if abs(raw) > abs(exposure) + 1e-9:
            text += f" (sarebbe {_plain(abs(raw))}x, limitata da: {limited})"
        if leg is None or (leg_target == 0 and leg_current == 0 and exposure >= 0):
            # one instrument, or a book with a short leg that neither holds nor wants it today
            return self._position_text(text, symbol, v.unit_it, v.lot, target, current, delta, exposure, actual)
        # ---- a book with a short leg in play: the leg that carries today's side leads, the other follows
        if self.short is None:
            # the configuration no longer has a short side, the account still holds one: it is sold
            worth = target * price / equity if equity > 0 else 0.0
            lead = self._position_text(text, symbol, v.unit_it, v.lot, target, current, delta, exposure, worth)
            return f"{lead} Vendo {_units(leg_delta)} {leg.unit_it} di {leg.id}: il libro non ha più un lato short."
        if exposure < 0:
            if leg_price is None:
                lead = f"{text}, short. Nessun prezzo recente per {leg.id}: il lato short non si apre."
            elif not fresh:
                # held, and priced only on its last mark: it may shrink on that price, it does not grow -
                # and when it stays where it is, that is why, not the inertia band
                text += f", short. Nessun prezzo recente per {leg.id}: il lato short non cresce"
                worth = leg.multiplier * leg_target * leg_price / equity if equity > 0 else 0.0
                lead = self._position_text(
                    text, leg.id, leg.unit_it, 1.0, leg_target, leg_current, leg_delta, exposure, worth, True,
                    band=leg_delta != 0,
                )  # fmt: skip
            else:
                text += (
                    f", short (tramite {leg.id}, che ogni giorno rende {_signed(leg.multiplier, 0)} volte il WTI: "
                    "in dollari ne basta la metà)"
                )
                worth = leg.multiplier * leg_target * leg_price / equity if equity > 0 else 0.0
                lead = self._position_text(
                    text, leg.id, leg.unit_it, 1.0, leg_target, leg_current, leg_delta, exposure, worth, True
                )
            other = self._closing_text(symbol, v.unit_it, target, current, delta, "long")
        else:
            worth = target * price / equity if equity > 0 else 0.0
            lead = self._position_text(text, symbol, v.unit_it, v.lot, target, current, delta, exposure, worth)
            other = self._closing_text(leg.id, leg.unit_it, leg_target, leg_current, leg_delta, "short")
        return f"{lead} {other}".strip()

    def _position_text(
        self,
        text: str,
        symbol: str,
        unit: str,
        lot: float,
        target: float,
        current: float,
        delta: float,
        exposure: float,
        actual: float,
        inverse: bool = False,
        band: bool = True,
    ) -> str:
        """What the leading instrument does: nothing, hold, buy or sell, and what the position is then worth.
        ``inverse`` marks the inverse fund, whose long position is the book's short side; ``band=False`` when a
        position differs from the size wanted for a reason that is not the inertia band (and has been said)."""
        if inverse:
            side = "short" if target > 0 else "nessuna posizione"
        else:
            side = "long" if target > 0 else ("short" if target < 0 else "nessuna posizione")
        held = f"{side}, {_plain(abs(actual))}x" if target != 0 else side
        # one lot can be most of the account: when whole lots move the position away from the size wanted,
        # the reader is told by how much instead of being left to wonder why 0.47x became 0.91x
        if target != 0 and lot > 1 and abs(abs(actual) - abs(exposure)) > 0.1:
            one_lot = abs(actual) / (abs(target) / lot)
            held += f"; un lotto da {_units(lot)} {unit} vale {_plain(one_lot)}x del conto"
        elif band and target != 0 and abs(abs(actual) - abs(exposure)) > 0.01:
            # the buffer at work: the position stops at the edge of the band, or stays where it is inside it
            held += "; dentro la fascia di inerzia" if delta == 0 else "; al bordo della fascia di inerzia"
        if delta == 0:
            if target == 0 and current == 0:
                if exposure != 0 and lot > 1:
                    # wanted, but smaller than the size at which a whole lot is the nearer choice
                    return (
                        f"{text}, meno di quanto serve per un lotto intero ({_units(lot)} {unit}): "
                        f"nessuna posizione su {symbol}."
                    )
                return f"{text}. Nessuna posizione su {symbol}."
            return f"{text}. Posizione invariata: {_units(current)} {unit} su {symbol} ({held})."
        verb = "Compro" if delta > 0 else "Vendo"
        return f"{text}. {verb} {_units(delta)} {unit} di {symbol}: obiettivo {_units(target)} ({held})."

    @staticmethod
    def _closing_text(symbol: str, unit: str, target: float, current: float, delta: float, side: str) -> str:
        """What happens to the leg that does NOT carry today's side: sold, trimmed, or a remainder left alone."""
        if delta < 0 and target == 0:
            return f"Vendo {_units(delta)} {unit} di {symbol}: il lato {side} si chiude."
        if delta < 0:
            return f"Vendo {_units(delta)} {unit} di {symbol}: del lato {side} ne restano {_units(target)}."
        if target > 0:
            return f"Restano {_units(target)} {unit} di {symbol} (lato {side}, dentro la fascia di inerzia)."
        return ""
