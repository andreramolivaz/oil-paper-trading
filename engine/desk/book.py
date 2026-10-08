"""One paper book: a vehicle, a risk budget and the rule that turns a forecast into an order.

Sizing is one formula (``signals.exposure_fraction``):

    exposure (multiple of equity) = forecast / 10 x volatility target / current volatility

then three ceilings, in this order: the book's own ``max_leverage``, the lower ``weekend_max_leverage`` on the
last decision before a market closure of more than one day, and the vehicle's margin. The paper broker
enforces the hard 10x on top of all of them. Nothing else raises or lowers a position: there is no gate to
pass and no discretionary override, which is the point - the three books differ only in how much of the same
forecast they buy.

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
from engine.desk.vehicles import Vehicle, get_vehicle

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
    sleeves: dict[str, float] = field(default_factory=lambda: dict.fromkeys(sg.SLEEVES, 1.0))
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


def load_books(config_dir: Path | None = None) -> list[BookConfig]:
    """Read ``config/books.yaml``. A book whose vehicle is unknown is a configuration error, not a skip."""
    raw = load_config(BOOKS_CONFIG, config_dir)
    books: list[BookConfig] = []
    for entry in raw.get("books", []):
        if not entry.get("enabled", True):
            continue
        get_vehicle(str(entry["vehicle"]))
        sleeves = {str(k): float(v) for k, v in (entry.get("sleeves") or dict.fromkeys(sg.SLEEVES, 1.0)).items()}
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

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["ts"] = self.ts.isoformat().replace("+00:00", "Z")
        return d


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

    # ------------------------------------------------------------------ views
    @property
    def id(self) -> str:
        return self.cfg.id

    def units(self, symbol: str) -> float:
        pos = self.broker.positions.get(symbol)
        return 0.0 if pos is None else float(pos.qty_bbl)

    def other_contract_positions(self, symbol: str) -> list[str]:
        """Symbols of open positions that are not ``symbol`` (an old contract waiting to be rolled)."""
        return [s for s, p in self.broker.positions.items() if s != symbol and p.qty_bbl != 0]

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
        if not self.vehicle.allow_short:
            raw = max(0.0, raw)
        full_cap = min(HARD_CAP, cfg.max_leverage, self.vehicle.max_leverage)
        cap = self._cap(day)
        limited = "obiettivo di volatilità"
        if abs(raw) > cap:
            limited = "tetto prima della chiusura dei mercati" if cap < full_cap else "tetto di leva del libro"
        capped = max(-cap, min(cap, raw))
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
        that cannot size (no forecast, no volatility, no price) holds what it has and says why."""
        cfg = self.cfg
        snap = self.broker.snapshot(ts)
        equity = float(snap.equity)
        current = self.units(symbol)
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
            return dec, None
        assert combined is not None and vol is not None
        raw, exposure, limited = self.exposure(float(combined), float(vol), day)
        lot = self.vehicle.lot
        per_lot = equity / price / lot  # lots that one times the equity buys
        target_lots = buffered_target(
            exact=exposure * per_lot,
            buffer=cfg.buffer * cfg.vol_target / float(vol) * per_lot,
            current=current / lot,
            cap=self._cap(day) * per_lot,
        )
        if cfg.long_only or not self.vehicle.allow_short:
            target_lots = max(0.0, target_lots)
        target = target_lots * lot
        delta = target - current
        delta = math.copysign(math.floor(abs(delta) / lot + 1e-9) * lot, delta) if delta else 0.0
        actual = target * price / equity
        if target == 0 and exposure != 0:
            limited = "lotto minimo"
        rationale = self._rationale(
            forecast, float(vol), raw, exposure, limited, target, current, delta, symbol, actual
        )
        dec = Decision(ts, cfg.id, cfg.vehicle, symbol, price, equity, forecast, float(vol), raw, exposure, limited,
                       target, current, delta, rationale, str(snap.status), actual)  # fmt: skip
        if delta == 0.0:
            return dec, None
        key = idempotency_key("desk", cfg.id, snap.epoch, day.isoformat(), symbol)
        order = Order(
            order_id=key[:16],
            idempotency_key=key,
            ts=ts,
            account_id=self.broker.account_id,
            instrument=symbol,
            qty_bbl=delta,
            order_type=OrderType.MARKET,
            reason=OrderReason.SIGNAL if current == 0 else OrderReason.REBALANCE,
            rationale=rationale,
            strategies_for=[k for k in sg.SLEEVES if (forecast.get(k) or 0.0) * delta > 0],
            strategies_against=[k for k in sg.SLEEVES if (forecast.get(k) or 0.0) * delta < 0],
            leverage={
                "value": round(abs(actual), 4),
                "wanted": round(abs(exposure), 4),
                "limited_by": limited,
                "components": {"vol_target": cfg.vol_target, "vol": float(vol), "cap": cfg.max_leverage},
            },
        )
        return dec, order

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
    ) -> str:
        v = self.vehicle
        text = (
            f"Trend {_signed(forecast.get('trend'))}, carry {_signed(forecast.get('carry'), 0)}, "
            f"carry-momentum {_signed(forecast.get('carry_momentum'), 0)}: "
            f"previsione {_signed(forecast.get('combined'))} su 20. "
            f"Volatilità {_pct(vol)} contro un obiettivo del {_pct(self.cfg.vol_target)}: "
            f"esposizione {_plain(abs(exposure))}x"
        )
        if abs(raw) > abs(exposure) + 1e-9:
            text += f" (sarebbe {_plain(abs(raw))}x, limitata da: {limited})"
        side = "long" if target > 0 else ("short" if target < 0 else "nessuna posizione")
        held = f"{side}, {_plain(abs(actual))}x" if target != 0 else side
        # one lot can be most of the account: when whole lots move the position away from the size wanted,
        # the reader is told by how much instead of being left to wonder why 0.47x became 0.91x
        if target != 0 and v.lot > 1 and abs(abs(actual) - abs(exposure)) > 0.1:
            one_lot = abs(actual) / (abs(target) / v.lot)
            held += f"; un lotto da {_units(v.lot)} {v.unit_it} vale {_plain(one_lot)}x del conto"
        elif target != 0 and abs(abs(actual) - abs(exposure)) > 0.01:
            # the buffer at work: the position stops at the edge of the band, or stays where it is inside it
            held += "; dentro la fascia di inerzia" if delta == 0 else "; al bordo della fascia di inerzia"
        if delta == 0:
            if target == 0 and current == 0:
                if exposure != 0 and v.lot > 1:
                    # wanted, but smaller than the size at which a whole lot is the nearer choice
                    return (
                        f"{text}, meno di quanto serve per un lotto intero ({_units(v.lot)} {v.unit_it}): "
                        f"nessuna posizione su {symbol}."
                    )
                return f"{text}. Nessuna posizione su {symbol}."
            return f"{text}. Posizione invariata: {_units(current)} {v.unit_it} su {symbol} ({held})."
        verb = "Compro" if delta > 0 else "Vendo"
        return f"{text}. {verb} {_units(delta)} {v.unit_it} di {symbol}: obiettivo {_units(target)} ({held})."
