"""TradingSession: next-bar fills, roll, shadow accounts, idempotency, 1x cap, persistence.

All market data here is SYNTHETIC (tests/synthetic.py) and labelled as such; the point of these tests is the
mechanics of the event loop, not any market claim.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from engine.backtest.runner import run_backtest, trading_days
from engine.backtest.session import SessionState, TradingSession
from engine.broker.paper import PaperBroker
from engine.core.config import RiskConfig
from engine.core.events import Direction, Signal
from engine.core.store import StateStore
from engine.core.timeutil import settlement_ts
from engine.features import catalog as cat
from engine.portfolio.base import CappedEqualWeightAllocator
from engine.regime.base import LABEL_LOWVOL_RANGE, RegimeState
from engine.strategies.base import Family, MarketContext, Strategy
from tests.synthetic import make_market_data


class StubRegime:
    """Always the same readable regime, high confidence: keeps the session deterministic."""

    name = "stub"

    def fit(self, features: pd.DataFrame) -> None:  # pragma: no cover - nothing to fit
        return None

    def infer(self, features: pd.DataFrame, ts: datetime) -> RegimeState:
        return RegimeState(
            ts=ts,
            regime_id=0,
            label=LABEL_LOWVOL_RANGE,
            confidence=0.9,
            probabilities={LABEL_LOWVOL_RANGE: 0.9},
            model="stub",
        )

    def history(self, features: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(index=features.index)


class MiniBuilder:
    """Minimal feature builder: only what the toy strategy needs, computed from md.prices."""

    def build(self, md, asof):  # noqa: ANN001, ANN201
        close = md.prices["brent_front_close"].dropna().astype(float)
        close = close.loc[: pd.Timestamp(asof.date())]
        frame = pd.DataFrame(index=close.index)
        frame[cat.PX] = close
        frame[cat.PX_FRONT] = close
        frame[cat.RET_1] = close.pct_change()
        frame[cat.RET_21] = close.pct_change(21)
        frame[cat.RV_YZ_21] = close.pct_change().rolling(21).std() * (252**0.5)
        frame[cat.ATR_14] = (
            ((md.prices["brent_front_high"] - md.prices["brent_front_low"]).reindex(close.index) / close)
            .rolling(14)
            .mean()
        )
        return frame


class ToyLong(Strategy):
    """Long when the 21-day return is positive, flat otherwise. Deterministic, no magic."""

    id = "T1"
    name = "Toy momentum"
    family = Family.TREND
    horizon_days = 5
    warmup_days = 30
    requires = (cat.RET_21, cat.RV_YZ_21)

    def generate(self, ctx: MarketContext) -> Signal | None:
        r = ctx.f(cat.RET_21)
        if pd.isna(r) or r <= 0:
            return None
        vol = ctx.f(cat.RV_YZ_21, 0.3)
        return self.make_signal(
            ctx,
            Direction.LONG,
            prob=0.6,
            expected_return=0.01,
            expected_vol=max(vol, 0.05),
            rationale=f"Test: rendimento 21g positivo ({r:.3f}).",
            stop_pct=0.05,
        )


def build_session(tmp_path, n_days: int = 300, strict_pit: bool = False, md=None):  # noqa: ANN001, ANN201
    risk = RiskConfig.load()
    md = md if md is not None else make_market_data(n_days=n_days, vol=0.012, drift=0.0006)
    store = StateStore(tmp_path / "state")
    master = PaperBroker("master", risk, store=None)
    session = TradingSession(
        md=md,
        strategies=[ToyLong()],
        master=master,
        risk=risk,
        allocator=CappedEqualWeightAllocator(),
        feature_builder=MiniBuilder(),
        regime_model=StubRegime(),
        store=store,
        strict_pit=strict_pit,
        state=SessionState(),
    )
    return session, md, risk, store


def test_fill_happens_on_the_next_bar_open(tmp_path):
    session, md, risk, _ = build_session(tmp_path)
    days = [d.date() for d in pd.DatetimeIndex(md.prices.index)][:120]
    # run until the toy strategy queues its first order
    queued_day = None
    for day in days:
        session.run_day(day)
        if session.master.pending_orders():
            queued_day = day
            break
    assert queued_day is not None, "the toy strategy never produced an order"
    order = session.master.pending_orders()[0]
    assert order.ts == settlement_ts(queued_day)
    assert not session.master.positions, "nothing may be filled on the decision day"

    next_day = days[days.index(queued_day) + 1]
    session.run_day(next_day)
    pos = session.master.positions[order.instrument]
    open_next = float(md.prices.loc[pd.Timestamp(next_day), "brent_front_open"])
    close_decision = float(md.prices.loc[pd.Timestamp(queued_day), "brent_front_close"])
    # filled at the NEXT day's open plus adverse costs, never at the price that generated the signal
    assert pos.avg_price >= open_next
    assert pos.avg_price == pytest.approx(open_next, rel=0.01)
    assert pos.avg_price != pytest.approx(close_decision, rel=1e-9)


def test_master_and_shadow_tracked_and_capped_at_1x(tmp_path):
    session, md, risk, store = build_session(tmp_path)
    for day in [d.date() for d in pd.DatetimeIndex(md.prices.index)][:150]:
        session.run_day(day)
    asof = settlement_ts(date(2024, 1, 2))
    assert "T1" in session.shadows
    snap = session.master.snapshot(session.master.state.last_mark_ts or asof)
    assert snap.leverage <= 1.0 + 1e-9
    shadow_snap = session.shadows["T1"].snapshot(session.shadows["T1"].state.last_mark_ts or asof)
    assert shadow_snap.leverage <= 1.0 + 1e-9
    assert snap.equity > 0 and shadow_snap.equity > 0
    # the store received the audit trail
    assert store.read_jsonl("equity")
    assert store.read_jsonl("signals")
    assert store.read_jsonl("regime")
    assert store.exists("session.json")


def test_run_day_is_idempotent(tmp_path):
    session, md, _, store = build_session(tmp_path)
    days = [d.date() for d in pd.DatetimeIndex(md.prices.index)][:80]
    for day in days:
        session.run_day(day)
    equity_rows = len(store.read_jsonl("equity"))
    fills = session.master.state.n_fills
    positions = {k: v.qty_bbl for k, v in session.master.positions.items()}
    for day in days:  # replay the whole range: must change nothing
        res = session.run_day(day)
        assert res.skipped == "already processed"
    assert len(store.read_jsonl("equity")) == equity_rows
    assert session.master.state.n_fills == fills
    assert {k: v.qty_bbl for k, v in session.master.positions.items()} == positions


def test_roll_on_calendar_date(tmp_path):
    session, md, _, store = build_session(tmp_path, n_days=400)
    days = [d.date() for d in pd.DatetimeIndex(md.prices.index)]
    rolls: list[tuple[date, str, str]] = []
    for day in days[:200]:
        res = session.run_day(day)
        if res.rolled:
            rolls.append((day, *res.rolled))
    assert rolls, "no roll happened in 200 trading days (one is expected every month)"
    first = rolls[0]
    # the roll moved to the contract the calendar says is the front on that day
    assert first[2] == session.front_code(first[0])
    assert first[1] != first[2]
    logged = store.read_jsonl("rolls")
    assert logged and logged[0]["to"] == first[2]
    # with a real curve the roll is not flagged approximate
    assert logged[0]["approx"] is False


def test_session_state_roundtrip(tmp_path):
    session, md, risk, store = build_session(tmp_path)
    days = [d.date() for d in pd.DatetimeIndex(md.prices.index)][:60]
    for day in days:
        session.run_day(day)
    session.save()
    reloaded, _, _, _ = build_session(tmp_path, md=md)
    reloaded.store = store
    reloaded.load()
    assert reloaded.state.last_day == days[-1].isoformat()
    assert reloaded.master.state.cash == pytest.approx(session.master.state.cash)
    assert set(reloaded.master.positions) == set(session.master.positions)
    # a replayed day is still a no-op after a reload
    assert reloaded.run_day(days[-1]).skipped == "already processed"


def test_runner_produces_equity_and_metrics(tmp_path):
    risk = RiskConfig.load()
    md = make_market_data(n_days=250, vol=0.012, drift=0.0006)
    store = StateStore(tmp_path / "state")
    res = run_backtest(
        md,
        [ToyLong()],
        risk,
        allocator=CappedEqualWeightAllocator(),
        feature_builder=MiniBuilder(),
        regime_model=StubRegime(),
        store=store,
    )
    assert not res.equity.empty
    assert "master" in res.equity.columns and "T1" in res.equity.columns and "buy_hold" in res.equity.columns
    assert res.equity["buy_hold"].iloc[0] == pytest.approx(risk.initial_capital, rel=1e-6)
    assert "master" in res.summary
    assert res.summary["master"].n_obs == len(res.equity)
    assert len(res.days) == len(trading_days(md, None, None))
    assert res.meta["initial_capital"] == risk.initial_capital


def test_spread_instruments_are_priced_from_their_legs(tmp_path):
    """Multi-leg symbols the strategies emit must be priceable, or they could never execute."""
    session, md, _, _ = build_session(tmp_path, n_days=120)
    day = [d.date() for d in pd.DatetimeIndex(md.prices.index)][-1]
    row = md.prices.loc[pd.Timestamp(day)]
    codes = session.curve_codes(day)

    # Brent-WTI: the difference of the two front closes
    bw = session.price_instrument(f"{codes['M1']}/CLZ26", day)
    assert bw == pytest.approx(float(row["brent_front_close"]) - float(row["wti_front_close"]))

    # 3-2-1 crack from RBOB and heating oil per barrel against WTI
    crack = session.price_instrument("CRACK321-CLZ26", day)
    expected = (2 * float(row["rbob_close"]) * 42 + float(row["ho_close"]) * 42) / 3 - float(row["wti_front_close"])
    assert crack == pytest.approx(expected)

    # calendar spread and butterfly come from the curve
    cal = session.price_instrument(f"{codes['M1']}-{codes['M3']}", day)
    assert cal == pytest.approx(
        float(md.curve.loc[pd.Timestamp(day), "M1"]) - float(md.curve.loc[pd.Timestamp(day), "M3"])
    )
    fly = session.price_instrument(f"FLY-{codes['M1']}-{codes['M3']}-{codes['M6']}", day)
    m1 = float(md.curve.loc[pd.Timestamp(day), "M1"])
    m3 = float(md.curve.loc[pd.Timestamp(day), "M3"])
    m6 = float(md.curve.loc[pd.Timestamp(day), "M6"])
    assert fly == pytest.approx(m1 - 2 * m3 + m6)

    # an unknown symbol is simply not tradeable, never guessed
    assert session.price_instrument("NOPE-XYZ", day) is None
    assert session.price_instrument("FLY-BZZ99-BZF99", day) is None


def test_auxiliary_bars_cover_open_spread_positions(tmp_path):
    """A pending order on a spread must receive a bar, otherwise it could never fill."""
    from engine.core.events import Order, OrderType

    session, md, risk, _ = build_session(tmp_path, n_days=120)
    days = [d.date() for d in pd.DatetimeIndex(md.prices.index)]
    # Run a day first: the broker needs the front outright marked before it can size a spread (the leverage
    # check measures a multi-leg notional against its first leg's outright price).
    for d in days[:-2]:
        session.run_day(d, decide=False)
    day = days[-2]
    codes = session.curve_codes(day)
    symbol = f"{codes['M1']}/CLZ26"
    session.register_instrument(symbol)
    key = "test-spread-order"
    session.master.submit(
        Order(
            order_id=key,
            idempotency_key=key,
            ts=settlement_ts(day),
            account_id="master",
            instrument=symbol,
            qty_bbl=100.0,
            order_type=OrderType.MARKET,
        )
    )
    bars = session.auxiliary_bars(days[-1])
    assert [b.symbol for b in bars] == [symbol]
    assert bars[0].open == bars[0].close  # synthetic spread: no observable intrabar extremes
    fills = session.on_bar(bars[0])
    assert fills == 1
    assert session.master.positions[symbol].qty_bbl == pytest.approx(100.0)
