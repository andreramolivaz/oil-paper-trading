"""One vehicle's bad table must cost only its own books (review of phase 12, 2026-10-13).

Before the guard, an exception raised while a satellite's half-hour table was read skipped every vehicle after
it in the loop (the futures book, alphabetically last) and the desk's save: for as long as Yahoo served the same
table, no book after it was fed, marked or stopped, and the tick failed, so the runner pushed nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from engine.core.config import RiskConfig
from engine.data.raw_store import RawStore
from engine.desk.book import load_books
from engine.desk.data import build_desk_data
from engine.desk.live import build_desk, live_tick
from tests.synthetic import make_desk_raw_frames, save_raw_frame

RISK = RiskConfig.load()
END = date(2026, 10, 8)  # a Thursday


def _session(day: date, open_price: float, step: float = 0.0004) -> pd.DataFrame:
    start = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
    rows, last = [], open_price
    for _ in range(13):
        o = last
        c = o * (1.0 + step)
        rows.append({"open": o, "high": max(o, c) * 1.0002, "low": min(o, c) * 0.9998, "close": c, "volume": 500.0})
        last = c
    index = pd.DatetimeIndex([start + timedelta(minutes=30 * i) for i in range(13)], name="ts")
    return pd.DataFrame(rows, index=index)


def _archive(root) -> tuple[RawStore, dict[str, pd.DataFrame]]:
    store = RawStore(root)
    frames = make_desk_raw_frames(n=1100, end=END)
    for entry, frame in frames.items():
        save_raw_frame(store, entry, frame)
    idx = frames["bno_daily"].index
    rng = np.random.default_rng(11)
    px = 300.0 * np.exp(np.cumsum(rng.normal(0.001, 0.006, size=len(idx))))
    gld = pd.DataFrame(
        {"open": px * 0.999, "high": px * 1.004, "low": px * 0.995, "close": px, "adjclose": px}, index=idx
    )
    save_raw_frame(store, "gld_daily", gld)
    intraday = {
        "bno_intraday": _session(END, float(frames["bno_daily"]["close"].iloc[-1])),
        "gld_intraday": _session(END, float(gld["close"].iloc[-1])),
    }
    return store, intraday


def test_a_poisoned_satellite_table_costs_only_that_vehicle_and_the_tick_goes_on(tmp_path):
    books = load_books()
    store, intraday = _archive(tmp_path / "raw")

    def tick(now: datetime, poison: bool):
        for entry, table in intraday.items():
            seen = table[table.index < pd.Timestamp(now)]
            if poison and entry == "gld_intraday":
                seen = seen.assign(close=seen["close"].astype(str) + "x")  # float("...x") raises in the reader
            save_raw_frame(store, entry, seen, fetched_at=now)
        data = build_desk_data(store)
        desk = build_desk(tmp_path / "state", RISK, books)
        return live_tick(desk, data, now), desk

    t1518 = datetime(2026, 10, 8, 19, 18, tzinfo=UTC)
    first, desk = tick(t1518, poison=False)
    assert not first.failed and "gold" in {d["book"] for d in first.decisions}
    gold_mark_before = desk.books["gold"].broker.state.last_mark_ts

    # 15:48: GLD's half-hour table comes back malformed; the tick does not raise, reports it, serves the rest
    second, desk = tick(t1518 + timedelta(minutes=30), poison=True)
    assert second.failed == ["GLD"]
    assert any("GLD: tick failed" in n for n in second.notes) and "FAILED for GLD" in second.message()
    for book_id in ("prudente", "dinamico", "spinto"):  # copper and agri have no table here: idle, as always
        assert desk.books[book_id].broker.state.last_mark_ts == second.now, book_id  # fed and marked this tick
    assert desk.books["gold"].broker.state.last_mark_ts == gold_mark_before  # left exactly as it was
    # the desk state was saved: the decisions of 15:18 stand and are not retaken
    saved = build_desk(tmp_path / "state", RISK, books)
    assert (
        saved.state.last_decision_day["prudente"] == "2026-10-08"
        and saved.state.last_decision_day["gold"] == "2026-10-08"
    )

    # 16:18: the table is healthy again and the gold book catches up on its own
    third, desk = tick(t1518 + timedelta(minutes=60), poison=False)
    assert not third.failed and desk.books["gold"].broker.state.last_mark_ts == third.now
