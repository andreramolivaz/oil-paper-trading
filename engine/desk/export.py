"""``site-data/desk.json``: everything the terminal page shows, in one file.

The page is one screen of text, so it gets one small JSON (well under 100 kB) rebuilt at every tick instead of
the ten files the old dashboard needed. The rules are the exporter's usual ones: nothing is invented (a value
the engine does not have is ``null``), every price carries where it came from and when, anything approximate
says ``approx``, and the output is strict JSON.

``site-data/desk_backtest.json`` is the full backtest payload (``state/desk/backtest.json``) copied through for
the backtest page; the terminal itself only needs the summary table that is embedded here.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.config import RiskConfig, Settings
from engine.core.store import StateStore, dumps
from engine.core.timeutil import ensure_utc, iso, parse_iso
from engine.data.raw_store import RawStore
from engine.desk import signals as sg
from engine.desk.allocator import (
    AllocatorConfig,
    describe,
    hormuz_weekend_scale,
    ratio_known_on,
    runup_state,
    transit_ratios,
)
from engine.desk.book import Book, is_pre_closure
from engine.desk.data import MACRO_MAX_AGE_DAYS, DeskData, _latest, build_desk_data, held_contract, hormuz_summary
from engine.desk.engine import DECISIONS_LOG, Desk, _clean
from engine.desk.live import (
    NEW_YORK,
    ROOT_OF_VEHICLE,
    build_desk,
    decision_day,
    desk_store,
    last_quote,
    latest_price,
)
from engine.desk.options import MONITOR_FILE, STATE_FILE, load_options_config, options_store
from engine.desk.prediction import configured_series, latest_reading, prediction_block
from engine.desk.report import BACKTEST_FILE
from engine.desk.vehicles import get_vehicle
from engine.desk.watch import describe as describe_watch
from engine.desk.watch import recent_alerts

log = logging.getLogger(__name__)

DESK_FILE = "desk.json"
DESK_BACKTEST_FILE = "desk_backtest.json"
PRICE_SOURCE = "Yahoo Finance, 10-15 minute delay"
DISCLAIMER = "Study simulation on real data. No financial advice, no real orders, no broker connected."
MAX_CURVE_POINTS = 360
MAX_DECISIONS = 40
MAX_FILLS = 60
STRESS_MOVES = (-0.30, -0.20, -0.10, 0.10, 0.20)  # gaps of the vehicle's price the books are revalued on


# ----------------------------------------------------------------------------------------------- helpers
def _r(x: Any, digits: int = 4) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, digits) if math.isfinite(v) else None


def _prev_close(series: pd.Series, today: date) -> tuple[float | None, str | None]:
    """Last daily close strictly before ``today`` (the base of "change today")."""
    if series is None or series.empty:
        return None, None
    before = series.loc[: pd.Timestamp(today) - pd.Timedelta(days=1)].dropna()
    if before.empty:
        return None, None
    return float(before.iloc[-1]), pd.Timestamp(before.index[-1]).date().isoformat()


def _quote(
    name: str, symbol: str, price: float | None, ts: datetime | None, prev: float | None, prev_day: str | None
) -> dict[str, Any]:
    change = None if price is None or prev is None or prev <= 0 else price / prev - 1.0
    return {
        "name": name,
        "symbol": symbol,
        "price": _r(price, 3),
        "asof": iso(ts) if ts is not None else None,
        "change": _r(change, 5),
        "prev_close": _r(prev, 3),
        "prev_close_day": prev_day,
        "source": PRICE_SOURCE,
    }


def _curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Equity snapshots -> at most ``MAX_CURVE_POINTS`` points: every snapshot of the last three days, then
    the last snapshot of each earlier day."""
    if not rows:
        return []
    frame = pd.DataFrame(
        {
            "t": pd.to_datetime(pd.Series([r.get("ts") for r in rows], dtype="object"), utc=True, errors="coerce"),
            "v": [r.get("equity") for r in rows],
        }
    )
    frame = frame.dropna().sort_values("t")
    if frame.empty:
        return []
    cutoff = frame["t"].iloc[-1] - pd.Timedelta(days=3)
    recent = frame[frame["t"] >= cutoff]
    old = frame[frame["t"] < cutoff]
    if not old.empty:
        old = old.groupby(old["t"].dt.date, as_index=False).last()
    out = pd.concat([old, recent], ignore_index=True)
    if len(out) > MAX_CURVE_POINTS:
        step = math.ceil(len(out) / MAX_CURVE_POINTS)
        out = pd.concat([out.iloc[:-1:step], out.iloc[[-1]]], ignore_index=True)
    return [{"t": iso(t.to_pydatetime()), "v": round(float(v), 2)} for t, v in zip(out["t"], out["v"], strict=True)]


def _current_epoch(rows: list[dict[str, Any]], epoch: int) -> list[dict[str, Any]]:
    return [r for r in rows if int(r.get("epoch", epoch) or epoch) == epoch]


# ----------------------------------------------------------------------------------------------- blocks
def market_block(data: DeskData, now: datetime) -> dict[str, Any]:
    today = now.astimezone(NEW_YORK).date()
    out: dict[str, Any] = {}
    # Brent: the contract a futures book would hold, never the continuous symbol
    bz_code = held_contract("BZ", today)
    bz_daily = data.contracts.get("BZ", pd.DataFrame())
    bz_series = bz_daily[bz_code].dropna() if bz_code in bz_daily.columns else pd.Series(dtype="float64")
    price, ts = last_quote(data.intraday.get("BZ"), bz_code, now)
    if price is None and not bz_series.empty:
        price = float(bz_series.iloc[-1])
        ts = datetime.combine(pd.Timestamp(bz_series.index[-1]).date(), datetime.min.time(), tzinfo=UTC) + timedelta(
            hours=18, minutes=30
        )
    out["brent"] = _quote("Brent", bz_code, price, ts, *_prev_close(bz_series, today))
    cl_code = held_contract("CL", today)
    cl_daily = data.contracts.get("CL", pd.DataFrame())
    cl_series = cl_daily[cl_code].dropna() if cl_code in cl_daily.columns else pd.Series(dtype="float64")
    price, ts = latest_price(data, "MCL", cl_code, now)
    out["wti"] = _quote("WTI", cl_code, price, ts, *_prev_close(cl_series, today))
    bno = data.series.get("BNO")
    if bno is not None:
        price, ts = latest_price(data, "BNO", "BNO", now)
        out["bno"] = _quote("BNO (Brent fund)", "BNO", price, ts, *_prev_close(bno.bars["close"], today))
    if not data.ovx.empty:
        out["ovx"] = {
            "value": _r(data.ovx.iloc[-1], 2),
            "asof": pd.Timestamp(data.ovx.index[-1]).date().isoformat(),
            "source": "Cboe OVX (FRED / Yahoo)",
            "note": "30-day implied volatility of USO options",
        }
    spread = None
    if out["brent"]["price"] is not None and out["wti"]["price"] is not None:
        spread = out["brent"]["price"] - out["wti"]["price"]
    out["brent_wti"] = {
        "value": _r(spread, 2),
        "note": f"{bz_code} minus {cl_code}: different expiries, not the same-expiry spread",
    }
    out["hormuz"] = hormuz_summary(data.hormuz) or None
    # the two markets the macro sleeves read: the last close a later day's download has confirmed, with its date
    for name, (label, source) in MACRO_QUOTES.items():
        closes = data.macro.get(name)
        if closes is None or closes.empty:
            continue
        out[name] = {
            "name": label,
            "value": _r(closes.iloc[-1], 3),
            "asof": pd.Timestamp(closes.index[-1]).date().isoformat(),
            "source": source,
        }
    return out


MACRO_QUOTES = {
    "copper": ("Copper (COMEX future, $/lb)", "Yahoo Finance HG=F, daily close"),
    "dollar": ("Dollar index (DXY)", "Yahoo Finance DX-Y.NYB, daily close"),
}


def _macro_days(data: DeskData, day: pd.Timestamp) -> dict[str, str | None]:
    """The date of the close each macro sleeve read for the forecast of ``day``: the latest one strictly
    before it (those markets close after the oil settlement), or None when that is more than a week old."""
    out: dict[str, str | None] = {}
    for name in sg.SOURCES["macro"]:
        closes = data.macro.get(name)
        prior = None if closes is None else closes[closes.index < day]
        if prior is None or prior.empty or (day - pd.Timestamp(prior.index[-1])).days > MACRO_MAX_AGE_DAYS:
            out[name] = None
        else:
            out[name] = pd.Timestamp(prior.index[-1]).date().isoformat()
    return out


def forecast_block(data: DeskData) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for vehicle_id, series in data.series.items():
        if series.forecasts.empty:
            continue
        row = series.forecasts.iloc[-1]
        day = pd.Timestamp(series.forecasts.index[-1])
        vehicle = get_vehicle(vehicle_id)
        prev = series.forecasts.iloc[-2] if len(series.forecasts) > 1 else None
        sleeves = {name: _r(row.get(name), 2) for name in sg.SLEEVES}
        out[vehicle_id] = {
            "vehicle": vehicle_id,
            "underlying": vehicle.underlying,
            "day": day.date().isoformat(),
            **sleeves,
            # the average of each source's sleeves: the three numbers the combination is made of
            "sources": {name: _r(value, 2) for name, value in sg.source_values(sleeves).items()},
            "macro_day": _macro_days(data, day),
            "combined": _r(row.get("combined"), 2),
            "combined_prev": None if prev is None else _r(prev.get("combined"), 2),
            "vol": _r(series.vol.iloc[-1], 4),
            "slope": _r(series.slope.iloc[-1], 4),
            "slope_pair": str(series.slope_pair.iloc[-1] or ""),
            "slope_approx": bool(series.slope_approx.iloc[-1]),
            "return_source": str(series.return_source.iloc[-1]),
            "approx": bool(series.slope_approx.iloc[-1]) or "approx" in str(series.return_source.iloc[-1]),
        }
    return out


def book_block(book: Book, desk: Desk, state_dir: Path, now: datetime) -> dict[str, Any]:
    cfg, vehicle, st = book.cfg, book.vehicle, book.broker.state
    snap = book.broker.snapshot(now)
    initial = float(book.risk.initial_capital)
    equity = float(snap.equity)
    day_base = float(st.day_start_equity) if st.day_start_equity else None
    positions = []
    for symbol, pos in st.positions.items():
        if pos.qty_bbl == 0:
            continue
        last = float(st.last_prices.get(symbol, pos.avg_price))
        notional = abs(pos.qty_bbl) * last
        unrealized = (last - pos.avg_price) * pos.qty_bbl
        times = book.multiplier(symbol)  # -2 for the inverse fund a short side is held in, 1 otherwise
        positions.append(
            {
                "symbol": symbol,
                "units": float(pos.qty_bbl),
                "unit": vehicle.unit,
                # the side of the OIL exposure: shares of an inverse fund are a short position
                "side": "long" if pos.qty_bbl * times > 0 else "short",
                "multiplier": times,
                "avg_price": _r(pos.avg_price, 4),
                "last_price": _r(last, 4),
                "notional": _r(notional, 2),
                "unrealized": _r(unrealized, 2),
                "opened_at": iso(pos.opened_ts) if getattr(pos, "opened_ts", None) else None,
            }
        )
    today = now.astimezone(NEW_YORK).date()
    day = decision_day(vehicle, now)
    store = StateStore(Path(state_dir) / "desk" / cfg.id)
    equity_rows = _current_epoch(store.read_jsonl("equity"), st.epoch)
    # what a gap in the vehicle's price does to the book, at the last marks: the positions revalued at once,
    # with an inverse fund moving by its multiple. Before any stop-out or breaker, which can only cut the loss
    # later; the weekend gaps of 2026 reached 16 %, so the first number to read is the -20 % one.
    stress = {}
    for move in STRESS_MOVES:
        pnl = sum(
            float(pos.qty_bbl) * float(st.last_prices.get(symbol, pos.avg_price)) * book.multiplier(symbol) * move
            for symbol, pos in st.positions.items()
            if pos.qty_bbl != 0
        )
        stress[f"{move:+.0%}"] = _r(pnl / equity, 5) if equity > 0 else None
    decisions = [d for d in desk_store(state_dir).tail_jsonl(DECISIONS_LOG, 400) if d.get("book") == cfg.id]
    full_cap = min(10.0, cfg.max_leverage, vehicle.max_leverage)
    cap_today = book._cap(today)
    return {
        "id": cfg.id,
        "kind": "linear",
        # "core": one of the books the desk is judged on; "candidate": a paper book beside them; "retired": the
        # lifecycle rule stopped it. The configured role unless the allocator changed it.
        "role": desk.role_of(book),
        "configured_role": cfg.role,
        "role_change": desk.state.roles.get(cfg.id),
        # the allocator's scales in force at the last decision (engine/desk/allocator.py), 1 = the book's own rules
        "allocator": desk.state.allocator.get(cfg.id),
        "lifecycle": (desk.state.allocator.get("_lifecycle") or {}).get(cfg.id),
        "name": cfg.name,
        "description": cfg.description,
        "vehicle": vehicle.id,
        "vehicle_name": vehicle.name,
        "underlying": vehicle.underlying,
        "robinhood": vehicle.robinhood,
        "note": vehicle.note,
        "rules": {
            "vol_target": cfg.vol_target,
            "max_leverage": full_cap,
            "weekend_max_leverage": cfg.weekend_max_leverage,
            "long_only": bool(cfg.long_only or (not vehicle.allow_short and book.short is None)),
            # the inverse fund the book buys to be short (null: it sells the vehicle itself, or is long only)
            "short_via": book.short_symbol,
            "short_note": None if book.short is None else book.short.note,
            "daily_loss_breaker": cfg.daily_loss_breaker,
            "margin_rate": vehicle.margin_rate,
            "decision_time_ny": f"{vehicle.decision_time_ny[0]:02d}:{vehicle.decision_time_ny[1]:02d}",
        },
        "cap_today": cap_today,
        "cap_reduced_today": bool(cap_today < full_cap - 1e-9),
        "pre_closure_today": is_pre_closure(today),
        "equity": _r(equity, 2),
        "initial_capital": initial,
        "pnl_total": _r(equity - initial, 2),
        "pnl_total_pct": _r(equity / initial - 1.0, 5) if initial > 0 else None,
        "pnl_day": _r(snap.daily_pnl, 2),
        "pnl_day_pct": _r(float(snap.daily_pnl) / day_base, 5) if day_base and day_base > 0 else None,
        "drawdown": _r(snap.drawdown, 5),
        "peak_equity": _r(st.peak_equity, 2),
        # both in oil exposure: an inverse fund counts for its multiple, not for its dollars
        "leverage": _r(book.effective_leverage(equity), 4),
        "exposure": _r(book.net_exposure(equity), 4) if equity > 0 else None,
        "margin_used": _r(snap.margin_used, 2),
        "margin_level": _r(snap.margin_level, 3) if snap.margin_level is not None else None,
        "liquidation_price": _r(snap.liquidation_price, 3) if snap.liquidation_price is not None else None,
        "status": str(snap.status),
        "epoch": st.epoch,
        "started_at": iso(st.epoch_started_ts) if st.epoch_started_ts else None,
        "marked_at": iso(st.last_mark_ts) if st.last_mark_ts else None,
        "price_asof": iso(st.last_price_asof) if st.last_price_asof else None,
        "positions": positions,
        "pending": [
            {"symbol": o.instrument, "units": float(o.qty_bbl), "decided_at": iso(o.ts), "reason": str(o.reason)}
            for o in st.pending
        ],
        "n_fills": int(st.epoch_n_fills),
        "costs": {
            "commission": _r(st.total_commission, 2),
            "spread_and_slippage": _r(st.total_slippage_usd, 2),
            "financing": _r(st.total_financing, 2),
        },
        # cash the fund paid its holders, credited on the ex-date (a fund's raw price falls by it that day)
        "distributions": _r(st.total_distributions, 2),
        # the book's return on a gap of the vehicle's price, as a fraction of equity, before any stop
        "stress": stress,
        "last_decision": decisions[-1] if decisions else None,
        "decision_owed": desk.state.last_decision_day.get(cfg.id) != day.isoformat(),
        "last_roll": desk.state.last_roll.get(cfg.id),
        "curve": _curve(equity_rows),
    }


def options_block(state_dir: Path, risk: RiskConfig, config_dir: Path | None) -> dict[str, Any] | None:
    cfg = load_options_config(config_dir)
    if cfg is None:
        return None
    store = options_store(state_dir, cfg)
    state = store.read_json(STATE_FILE) or {}
    monitor = store.read_json(MONITOR_FILE) or {}
    initial = float(state.get("initial_capital", risk.initial_capital))
    epoch = int(state.get("epoch", 1))
    rows = _current_epoch(store.read_jsonl("equity"), epoch)
    equity = float(rows[-1]["equity"]) if rows else float(state.get("equity", initial))
    structures = list(state.get("structures", []))
    max_loss_open = sum((float(s["width"]) - float(s["mark"])) * 100.0 * int(s["contracts"]) for s in structures)
    delta_notional = sum(float(s.get("delta_notional") or 0.0) for s in structures)
    day_rows = [r for r in rows if str(r.get("ts", ""))[:10] < (rows[-1]["ts"][:10] if rows else "")]
    day_base = float(day_rows[-1]["equity"]) if day_rows else initial
    peak = max([initial, *[float(r["equity"]) for r in rows]]) if rows else initial
    return {
        "id": cfg.id,
        "kind": "options",
        "role": "core",
        "name": cfg.name,
        "description": cfg.description,
        "vehicle": "/".join(u.symbol for u in cfg.underlyings),
        "robinhood": "option spreads on USO and BNO (level 3)",
        "experimental": True,
        "rules": monitor.get("rules") or {},
        "equity": _r(equity, 2),
        "initial_capital": initial,
        "pnl_total": _r(equity - initial, 2),
        "pnl_total_pct": _r(equity / initial - 1.0, 5) if initial > 0 else None,
        "pnl_day": _r(equity - day_base, 2),
        "pnl_day_pct": _r(equity / day_base - 1.0, 5) if day_base > 0 else None,
        "drawdown": _r(max(0.0, 1.0 - equity / peak), 5) if peak > 0 else None,
        "leverage": _r(abs(delta_notional) / equity, 4) if equity > 0 else None,
        "exposure": _r(delta_notional / equity, 4) if equity > 0 else None,
        "max_loss_open": _r(max_loss_open, 2),
        "max_loss_open_pct": _r(max_loss_open / equity, 5) if equity > 0 else None,
        "status": state.get("status", "active"),
        "epoch": epoch,
        "started_at": state.get("started_at"),
        "structures": structures,
        "n_closed": int(state.get("n_closed", 0)),
        "n_wins": int(state.get("n_wins", 0)),
        "realized_pnl": _r(state.get("realized_pnl", 0.0), 2),
        "fees": _r(state.get("fees", 0.0), 2),
        "monitor": monitor or None,
        "last_decision_day": state.get("last_decision_day"),
        "curve": _curve(rows),
    }


def decisions_block(state_dir: Path, options_id: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for d in desk_store(state_dir).tail_jsonl(DECISIONS_LOG, 200):
        forecast = d.get("forecast") or {}
        rows.append(
            {
                "ts": d.get("ts"),
                "book": d.get("book"),
                "symbol": d.get("symbol"),
                "price": _r(d.get("price"), 3),
                "forecast": _r(forecast.get("combined"), 2),
                "exposure": _r(d.get("exposure"), 3),
                "limited_by": d.get("limited_by"),
                # the order on the vehicle, or - when only the short leg trades - the one on the inverse fund
                "order_units": d.get("order_units")
                or next((leg.get("order_units") for leg in d.get("legs") or [] if leg.get("order_units")), 0.0),
                "legs": d.get("legs") or [],
                "text": d.get("rationale"),
                "status": d.get("status"),
            }
        )
    if options_id:
        for d in StateStore(Path(state_dir) / "desk" / options_id).tail_jsonl("decisions", 60):
            rows.append(
                {
                    "ts": d.get("ts"),
                    "book": d.get("book"),
                    "symbol": d.get("underlying"),
                    "price": _r((d.get("candidate") or {}).get("underlying_price"), 3),
                    "forecast": _r(d.get("forecast"), 2),
                    "exposure": None,
                    "limited_by": None,
                    # the log is append-only: rows written before 2026-10-13 carry the Italian "aperta"
                    "order_units": 1 if d.get("action") in ("opened", "aperta") else 0,
                    "text": d.get("reason"),
                    "status": d.get("action"),
                }
            )
    rows.sort(key=lambda r: str(r.get("ts")), reverse=True)
    return rows[:MAX_DECISIONS]


def fills_block(state_dir: Path, desk: Desk, options_id: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for book in desk.books.values():
        store = StateStore(Path(state_dir) / "desk" / book.id)
        for f in store.tail_jsonl("trades", 80):
            meta = f.get("meta") or {}
            rows.append(
                {
                    # an order fills at the OPEN of its bar: that is when it was executed. The broker stamps
                    # the fill with the bar's end, which is when it learned of it (forced exits keep that).
                    "ts": meta.get("bar_start") or f.get("ts"),
                    "book": book.id,
                    "symbol": f.get("instrument"),
                    "units": f.get("qty_bbl"),
                    "unit": book.vehicle.unit,
                    "price": _r(f.get("price"), 4),
                    "reference_price": _r(f.get("reference_price"), 4),
                    "cost_bps": _r(meta.get("total_adverse_bps"), 2),
                    "commission": _r(f.get("commission"), 2),
                    "reason": f.get("reason"),
                    "realized_pnl": _r(f.get("realized_pnl"), 2),
                    "decided_at": meta.get("order_ts"),
                    "leverage_after": _r(meta.get("leverage_after"), 3),
                }
            )
    if options_id:
        for t in StateStore(Path(state_dir) / "desk" / options_id).tail_jsonl("trades", 40):
            short, long = t.get("short") or {}, t.get("long") or {}
            rows.append(
                {
                    "ts": t.get("ts"),
                    "book": options_id,
                    "symbol": (
                        f"{t.get('underlying')} put {short.get('strike'):g}/{long.get('strike'):g} {t.get('expiry')}"
                    ),
                    "units": -int(t.get("contracts") or 0)
                    if t.get("action") == "open"
                    else int(t.get("contracts") or 0),
                    "unit": "spread",
                    "price": _r(t.get("credit") if t.get("action") == "open" else t.get("settle_value"), 4),
                    "reference_price": None,
                    "cost_bps": None,
                    "commission": None,
                    "reason": "open" if t.get("action") == "open" else "expiry",
                    "realized_pnl": _r(t.get("pnl"), 2),
                    "decided_at": None,
                    "leverage_after": None,
                }
            )
    rows.sort(key=lambda r: str(r.get("ts")), reverse=True)
    return rows[:MAX_FILLS]


def health_block(store: StateStore, now: datetime) -> dict[str, Any]:
    health = store.read_json("health.json") or {}
    sources = list(health.get("sources", []))
    not_green = [
        {
            "source": s.get("source"),
            "status": s.get("status"),
            "message": str(s.get("message") or "")[:160],
            "asof": s.get("data_asof"),
        }
        for s in sources
        if str(s.get("status")) != "green"
    ]
    ticks = [r for r in store.tail_jsonl("runs", 400) if r.get("job") == "tick"]
    last = ticks[-1] if ticks else None
    last_ok = next((r for r in reversed(ticks) if r.get("status") == "ok"), None)
    day_ago = now - timedelta(hours=24)
    recent = [r for r in ticks if (parse_iso(r.get("finished")) or day_ago) > day_ago]
    return {
        "overall": health.get("overall"),
        "checked_at": health.get("checked_at"),
        "critical": health.get("critical"),
        "n_sources": len(sources),
        "n_green": sum(1 for s in sources if str(s.get("status")) == "green"),
        "not_green": not_green,
        "last_tick": None
        if last is None
        else {"finished": last.get("finished"), "status": last.get("status"), "message": last.get("message")},
        "last_ok_tick": None if last_ok is None else last_ok.get("finished"),
        "ticks_24h": len(recent),
        "failed_24h": sum(1 for r in recent if r.get("status") == "failed"),
    }


def backtest_summary(state_dir: Path) -> dict[str, Any] | None:
    payload = desk_store(state_dir).read_json(BACKTEST_FILE)
    if not payload:
        return None
    books = {}
    for book_id, b in (payload.get("books") or {}).items():
        books[book_id] = {
            "vehicle": b.get("vehicle"),
            "start": b.get("start"),
            "end": b.get("end"),
            "stats": b.get("stats"),
            "ruin": b.get("ruin"),
            "died": b.get("died"),
            "costs": b.get("costs"),
            "double_cost_sharpe": ((payload.get("double_cost") or {}).get(book_id) or {})
            .get("stats", {})
            .get("sharpe"),
            "same_close_sharpe": ((payload.get("same_close") or {}).get(book_id) or {}).get("stats", {}).get("sharpe"),
            "last_years": dict(list((b.get("yearly") or {}).items())[-6:]),
        }
    options = payload.get("options") or {}
    return {
        "generated_at": (payload.get("meta") or {}).get("generated_at"),
        "fill_rule": (payload.get("meta") or {}).get("fill_rule"),
        "books": books,
        "portfolio": payload.get("portfolio") or {},
        "benchmarks": {
            k: {"name": v.get("name"), "stats": v.get("stats")} for k, v in (payload.get("benchmarks") or {}).items()
        },
        "options": None
        if not options.get("available")
        else {
            "approx": True,
            "stats": (options.get("headline") or {}).get("stats"),
            "sharpe_range": options.get("sharpe_range"),
            "method": options.get("method"),
        },
    }


# ----------------------------------------------------------------------------------------------- entry point
def build_desk_payload(
    settings: Settings, raw: RawStore, risk: RiskConfig, store: StateStore, now: datetime | None = None
) -> dict[str, Any]:
    now = ensure_utc(now or datetime.now(tz=UTC))
    data = build_desk_data(raw, settings)
    allocator_cfg = AllocatorConfig.load(settings.config_dir)
    desk = build_desk(settings.state_dir, risk, allocator=allocator_cfg)
    options_cfg = load_options_config(settings.config_dir)
    linear = [book_block(b, desk, settings.state_dir, now) for b in desk.books.values()]
    # the core books first (the options book among them), then the candidates: the page reads them in order
    books: list[dict[str, Any]] = [b for b in linear if b["role"] == "core"]
    options = options_block(settings.state_dir, risk, settings.config_dir)
    if options is not None:
        books.append(options)
    books += [b for b in linear if b["role"] != "core"]

    def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
        total = sum(float(b["equity"] or 0.0) for b in rows)
        initial = sum(float(b["initial_capital"] or 0.0) for b in rows)
        return {
            "equity": _r(total, 2),
            "initial_capital": _r(initial, 2),
            "pnl": _r(total - initial, 2),
            "pnl_pct": _r(total / initial - 1.0, 5) if initial > 0 else None,
            "n_books": len(rows),
        }

    today = now.astimezone(NEW_YORK).date()
    market = market_block(data, now)
    payload = {
        "generated_at": iso(now),
        "disclaimer": DISCLAIMER,
        "market": market,
        "forecast": forecast_block(data),
        "books": books,
        # the sum of the core books (the desk as it is judged) and, apart, the sum of the candidate books
        "total": totals([b for b in books if b["role"] == "core"]),
        "total_candidates": totals([b for b in books if b["role"] != "core"]),
        "decisions": decisions_block(settings.state_dir, options_cfg.id if options_cfg else None),
        "fills": fills_block(settings.state_dir, desk, options_cfg.id if options_cfg else None),
        "health": health_block(store, now),
        "backtest": backtest_summary(settings.state_dir),
        "calendar": {
            "today_ny": today.isoformat(),
            "pre_closure": is_pre_closure(today),
            "next_roll": {root: held_contract(root, today) for root in sorted(set(ROOT_OF_VEHICLE.values()))},
        },
        "data_notes": list(data.meta.get("notes", [])),
        "data_missing": list(data.meta.get("missing", [])),
        "allocator": allocator_block(desk, data, allocator_cfg, today),
        "prediction_markets": prediction_block(
            latest_reading(raw),
            now,
            references={k: market[k] for k in ("brent", "wti") if k in market},
            options=_options_monitor_entries(settings.state_dir, settings.config_dir),
            hormuz=market.get("hormuz"),
            inventories=_inventories(raw),
            asked=configured_series(settings.config_dir),
            alerts=recent_alerts(desk_store(settings.state_dir), now),
            watch_rules=describe_watch(),
        ),
    }
    cleaned: dict[str, Any] = _clean(payload)
    return cleaned


def _options_monitor_entries(state_dir: Path, config_dir: Path | None) -> dict[str, dict[str, Any]]:
    """The options monitor's entries by underlying symbol (ATM implied volatility, quote time): what the
    prediction-market page compares the crowd with."""
    cfg = load_options_config(config_dir)
    if cfg is None:
        return {}
    monitor = options_store(state_dir, cfg).read_json(MONITOR_FILE) or {}
    out: dict[str, dict[str, Any]] = {}
    for entry in monitor.get("underlyings", []) or []:
        if isinstance(entry, dict) and entry.get("symbol"):
            out[str(entry["symbol"])] = {
                k: entry.get(k) for k in ("symbol", "atm_iv", "iv30", "quote_ts", "price", "expiry")
            }
    return out


def _inventories(raw: RawStore) -> dict[str, Any] | None:
    """The last EIA weekly figure of commercial crude stocks (million barrels), beside the market that asks
    where the next one will land."""
    table = _latest(raw, "wpsr")
    if table is None or table.empty or "crude_stocks" not in table.columns:
        return None
    stocks = pd.to_numeric(table["crude_stocks"], errors="coerce").dropna()
    if stocks.empty:
        return None
    return {
        "crude_stocks_mmbbl": _r(float(stocks.iloc[-1]) / 1000.0, 1),
        "week_ending": pd.Timestamp(stocks.index[-1]).date().isoformat(),
        "source": "EIA Weekly Petroleum Status Report (commercial stocks, SPR excluded)",
    }


def allocator_block(desk: Desk, data: DeskData, cfg: AllocatorConfig, today: date) -> dict[str, Any]:
    """The allocator's rules and what they say today: the bubble switch per vehicle, the strait's reading, the
    scales of the last decision per book, the lifecycle's record."""
    runup: dict[str, Any] = {}
    for vehicle_id in desk.vehicles():
        series = data.series.get(vehicle_id)
        if series is None or series.returns.empty:
            continue
        active, since = runup_state(sg.total_return_index(series.returns), cfg, today)
        runup[vehicle_id] = {"active": active, "last_run_up": since.isoformat() if since else None}
    ratio = ratio_known_on(transit_ratios(data.hormuz, cfg), today)
    return {
        "rules": describe(cfg),
        "runup": runup,
        "hormuz": {
            "transit_ratio": None if ratio is None else _r(ratio, 4),
            "closed": bool(ratio is not None and ratio < cfg.hormuz_closed_share),
            "weekend_scale": hormuz_weekend_scale(ratio, cfg),
        },
        "books": {k: v for k, v in desk.state.allocator.items() if not k.startswith("_")},
        "lifecycle": desk.state.allocator.get("_lifecycle"),
        "role_changes": dict(desk.state.roles),
    }


def export_desk(
    settings: Settings, raw: RawStore, risk: RiskConfig, store: StateStore, out_dir: Path, now: datetime | None = None
) -> list[str]:
    """Write ``desk.json`` and ``desk_backtest.json`` into ``out_dir``. Returns the file names written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    payload = build_desk_payload(settings, raw, risk, store, now)
    (out_dir / DESK_FILE).write_text(dumps(payload, indent=None) + "\n", encoding="utf-8")
    written.append(DESK_FILE)
    backtest = desk_store(settings.state_dir).read_json(BACKTEST_FILE)
    if backtest:
        (out_dir / DESK_BACKTEST_FILE).write_text(dumps(_clean(backtest), indent=None) + "\n", encoding="utf-8")
        written.append(DESK_BACKTEST_FILE)
    return written
