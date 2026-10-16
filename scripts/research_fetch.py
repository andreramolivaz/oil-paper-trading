#!/usr/bin/env python3
"""Download the research data set that the scheduled jobs do not archive (Yahoo, key-less).

Why this exists: the live jobs only keep the continuous front series (``BZ=F``), and that series is not a
single contract near expiry - Yahoo interleaves the expiring and the next contract inside its intraday bars,
which in a steep backwardation prints fake 5-7 $ moves inside one hour. Research on intraday rules therefore
needs per-contract bars and the exchange-traded funds (which have no roll jump at all). The same adapter and
pacing as the engine are used, so this is exactly what the engine itself would see.

Usage (network required; run by .github/workflows/research-data.yml and uploaded as an artifact):

    python scripts/research_fetch.py --out out/research
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.core.calendar import contract_code, listed_months
from engine.core.instruments import Future
from engine.data.adapters.yahoo import YahooAdapter

DAILY = [
    "BZ=F", "CL=F", "MCL=F", "QM=F", "HO=F", "RB=F", "NG=F",
    "BNO", "USO", "UCO", "SCO", "DBO", "OILK", "XLE", "XOP", "^OVX", "^VIX", "DX-Y.NYB",
]  # fmt: skip
# Phase 12 (October 2026): the funds of the other markets a satellite book could trade from a Robinhood account,
# their inverse funds, and the continuous futures of the same markets for context. Daily only.
DAILY_SATELLITES = [
    "GLD", "IAU", "GLL", "CPER", "UNG", "KOLD", "BOIL", "DBA", "CORN", "WEAT", "SOYB", "URA",
    "GC=F", "HG=F", "NG=F", "ZC=F", "ZW=F", "ZS=F", "TLT", "SPY", "UUP", "SGOV",
    # phase 13 (13 October 2026): the themes as markets - utilities, lithium, semiconductors, clean energy;
    # SH and TBF, the inverse funds of equities and Treasuries, for a short side later
    "XLU", "LIT", "SMH", "ICLN", "SH", "TBF",
]  # fmt: skip
# Kalshi, the prediction market: the public API needs no key. The whole catalogue of series and the open
# markets are saved as JSON so that the oil, Hormuz and Iran markets can be found by reading, not guessing.
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
INTRADAY_1H = ["BZ=F", "CL=F", "MCL=F", "BNO", "USO", "UCO", "SCO", "XLE"]
INTRADAY_SHORT = ["BZ=F", "CL=F", "MCL=F", "BNO", "USO"]
SHORT_INTERVALS = ["30m", "15m", "5m"]
CURVE_MONTHS = {"BZ": 36, "CL": 40}
CONTRACT_INTRADAY_MONTHS = {"BZ": 4, "CL": 4}  # nearest contracts: clean single-contract intraday bars


def _save(frame: Any, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    for col in out.columns:
        if str(out[col].dtype).startswith("datetime64") and getattr(out[col].dt, "tz", None) is not None:
            out[col] = out[col].dt.tz_convert("UTC")
    out.to_parquet(path)
    return len(out)


def _kalshi_catalogue(out: Path) -> int:
    """Page through the prediction market's series and open markets and save them as JSON (no key needed)."""
    import time

    import requests

    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["Accept"] = "application/json"

    def page(path: str, params: dict[str, Any], key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(400):
            q = dict(params)
            if cursor:
                q["cursor"] = cursor
            r = session.get(f"{KALSHI_BASE}/{path}", params=q, timeout=30)
            r.raise_for_status()
            body = r.json()
            rows.extend(body.get(key) or [])
            cursor = body.get("cursor") or None
            if not cursor:
                break
            time.sleep(0.25)
        return rows

    series = page("series", {"limit": 1000}, "series")
    (out / "series.json").write_text(json.dumps(series, indent=0))
    print(f"ok   kalshi/series {len(series)} rows", flush=True)
    markets = page("markets", {"limit": 1000, "status": "open"}, "markets")
    (out / "markets_open.json").write_text(json.dumps(markets, indent=0))
    print(f"ok   kalshi/markets_open {len(markets)} rows", flush=True)
    # the settled markets of the series that look like ours, for the history that may exist
    words = ("oil", "brent", "wti", "crude", "hormuz", "iran", "opec", "ceasefire", "gasoline", "tanker")
    ours = [s for s in series if any(w in json.dumps(s).lower() for w in words)]
    (out / "series_ours.json").write_text(json.dumps(ours, indent=1))
    print(f"ok   kalshi/series_ours {len(ours)} rows: {[s.get('ticker') for s in ours][:40]}", flush=True)
    settled: list[dict[str, Any]] = []
    for s in ours[:40]:
        try:
            params = {"limit": 1000, "series_ticker": s.get("ticker"), "status": "settled"}
            settled += page("markets", params, "markets")
        except Exception as exc:  # one series must not stop the rest
            print(f"FAIL kalshi/settled {s.get('ticker')}: {exc}", flush=True)
    (out / "markets_settled_ours.json").write_text(json.dumps(settled, indent=0))
    print(f"ok   kalshi/markets_settled_ours {len(settled)} rows", flush=True)
    return 0


def _kalshi_trades(out: Path) -> int:
    """The trades of the last week on the markets the desk reads (config/data_sources.yaml, group desk, entry
    kalshi): the busiest twelve by 24-hour volume plus every market of the strait and the US-Iran series, so
    that a fixture of real trade objects exists for the unusual-activity alert (phase 13). One call a second."""
    import time

    import requests
    import yaml

    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["Accept"] = "application/json"
    doc = yaml.safe_load(Path("config/data_sources.yaml").read_text(encoding="utf-8")) or {}
    series = [str(x) for entry in doc.get("desk", {}).get("kalshi", []) for x in (entry.get("series") or [])]
    markets: list[dict[str, Any]] = []
    for ticker in series:
        r = session.get(
            f"{KALSHI_BASE}/markets", params={"series_ticker": ticker, "status": "open", "limit": 200}, timeout=30
        )
        r.raise_for_status()
        for m in r.json().get("markets") or []:
            m["series_ticker"] = ticker
            markets.append(m)
        time.sleep(1.0)
    (out / "markets_watch.json").write_text(json.dumps(markets, indent=0))
    print(f"ok   kalshi/markets_watch {len(markets)} rows", flush=True)
    busy = sorted(markets, key=lambda m: -float(m.get("volume_24h_fp") or 0.0))[:12]
    events = [m for m in markets if m["series_ticker"] in ("KXHORMUZNORM", "KXUSAIRANAGREEMENT", "KXUSIRANMEETING")]
    wanted: dict[str, dict[str, Any]] = {m["ticker"]: m for m in busy + events}
    since = int(time.time()) - 7 * 86400
    trades: dict[str, list[dict[str, Any]]] = {}
    for ticker in wanted:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(5):
            q: dict[str, Any] = {"ticker": ticker, "limit": 1000, "min_ts": since}
            if cursor:
                q["cursor"] = cursor
            r = session.get(f"{KALSHI_BASE}/markets/trades", params=q, timeout=30)
            r.raise_for_status()
            body = r.json()
            rows.extend(body.get("trades") or [])
            cursor = body.get("cursor") or None
            time.sleep(1.0)
            if not cursor:
                break
        trades[ticker] = rows
        print(f"ok   kalshi/trades {ticker} {len(rows)} rows", flush=True)
    (out / "trades_watch.json").write_text(json.dumps(trades, indent=0))
    return 0


def _kalshi_history(out: Path, series_arg: str | None = None, since_arg: str = "2026-01-01") -> int:
    """The daily candlesticks of every market, open or settled, of the event series the desk reads (the three
    the speculative book's index is made of first, then the other strait and supply series), from 2026-01-01:
    what the archive would hold had the desk read those markets every day since (phase 14, the backfill). One
    call a second; the price ladders are left out (hundreds of daily markets, nothing the index reads).
    ``--series`` (a comma-separated list of series tickers) downloads other series instead - phase 15 looked at
    the Fed, recession, trade and energy-policy series this way, before registering any rule on them."""
    from engine.data.adapters.kalshi import KalshiAdapter
    from engine.desk.oracle import ALL_SERIES

    out.mkdir(parents=True, exist_ok=True)
    if series_arg:
        series = [s.strip().upper() for s in series_arg.split(",") if s.strip()]
    else:
        others = ["KXCLOSEHORMUZ", "KXHORMUZRETURN", "KXHORMUZWEEKLY", "KXMAXSHIPSHORMUZ", "KXOPECCUTS", "KXSAUDICRUDE"]
        series = [*ALL_SERIES, *others]
    since = int(datetime.combine(date.fromisoformat(since_arg), datetime.min.time(), tzinfo=UTC).timestamp())
    history = KalshiAdapter().fetch_history(series, since)
    (out / "history.json").write_text(json.dumps(history, indent=0))
    for ticker, block in history["series"].items():
        n_candles = sum(len(v) for v in block["candles"].values())
        print(f"ok   kalshi/history {ticker}: {len(block['markets'])} markets, {n_candles} daily candles", flush=True)
    if history.get("failed"):
        print(f"FAIL kalshi/history {len(history['failed'])} failed: {list(history['failed'])[:10]}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="out/research")
    parser.add_argument(
        "--only",
        choices=["all", "satellites", "kalshi", "kalshi-trades", "kalshi-history"],
        default="all",
        help="'satellites': only the daily tables of DAILY_SATELLITES (phase 12 research), a few dozen requests; "
        "'kalshi': the prediction market's catalogue (series, open markets) as JSON; 'kalshi-trades': the open "
        "markets the desk reads and a week of their trades (the busiest and the event markets), for the alert; "
        "'kalshi-history': the daily candlesticks of every market of the event series since January 2026",
    )
    parser.add_argument("--series", default=None, help="kalshi-history: a comma-separated list of series tickers")
    parser.add_argument("--since", default="2026-01-01", help="kalshi-history: the first day (ISO date)")
    args = parser.parse_args()
    out = Path(args.out)
    yahoo = YahooAdapter(min_interval=1.3, retries=2, backoff=4.0)
    today = datetime.now(tz=UTC).date()
    manifest: dict[str, Any] = {"fetched_at": datetime.now(tz=UTC).isoformat(), "ok": {}, "failed": {}}

    def run(key: str, fn: Any, path: Path) -> None:
        try:
            res = fn()
            manifest["ok"][key] = {"rows": _save(res.frame, path), "meta": {k: str(v) for k, v in res.meta.items()}}
            print(f"ok   {key:<28} {manifest['ok'][key]['rows']:>7} rows", flush=True)
        except Exception as exc:  # one missing symbol must not stop the rest
            manifest["failed"][key] = f"{type(exc).__name__}: {exc}"[:300]
            print(f"FAIL {key:<28} {manifest['failed'][key]}", flush=True)

    safe = lambda s: s.replace("=", "_").replace("^", "_").replace(".", "_")  # noqa: E731
    if args.only == "kalshi":
        return _kalshi_catalogue(out / "kalshi")
    if args.only == "kalshi-trades":
        return _kalshi_trades(out / "kalshi")
    if args.only == "kalshi-history":
        return _kalshi_history(out / "kalshi", args.series, args.since)
    if args.only == "satellites":
        for symbol in DAILY_SATELLITES:
            run(
                f"daily/{symbol}",
                lambda s=symbol: yahoo.fetch_daily(s, date(2000, 1, 1), today),
                out / "daily" / f"{safe(symbol)}.parquet",
            )
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
        print(f"\n{len(manifest['ok'])} ok, {len(manifest['failed'])} failed, {yahoo.requests_made} requests")
        return 0
    for symbol in DAILY:
        run(
            f"daily/{symbol}",
            lambda s=symbol: yahoo.fetch_daily(s, date(2000, 1, 1), today),
            out / "daily" / f"{safe(symbol)}.parquet",
        )
    for root, months in CURVE_MONTHS.items():
        for year, month in listed_months(root, today, months):
            code = contract_code(root, year, month)
            run(
                f"contract/{code}", lambda c=code: yahoo.fetch_contract_history(c), out / "contract" / f"{code}.parquet"
            )
    for symbol in INTRADAY_1H:
        run(f"1h/{symbol}", lambda s=symbol: yahoo.fetch_intraday(s, "1h", 730), out / "1h" / f"{safe(symbol)}.parquet")
    for root, months in CONTRACT_INTRADAY_MONTHS.items():
        for year, month in listed_months(root, today, months):
            fut = Future(root, year, month)
            run(
                f"1h/{fut.code}",
                lambda f=fut: yahoo.fetch_intraday(f.yahoo_symbol, "1h", 730),
                out / "1h" / f"{fut.code}.parquet",
            )
            for interval in SHORT_INTERVALS:
                run(
                    f"{interval}/{fut.code}",
                    lambda f=fut, i=interval: yahoo.fetch_intraday(f.yahoo_symbol, i, 60),
                    out / interval / f"{fut.code}.parquet",
                )
    for symbol in INTRADAY_SHORT:
        for interval in SHORT_INTERVALS:
            run(
                f"{interval}/{symbol}",
                lambda s=symbol, i=interval: yahoo.fetch_intraday(s, i, 60),
                out / interval / f"{safe(symbol)}.parquet",
            )
    # Dec contracts further out are the liquid ones: their 1h history is a clean single-contract series
    for code in ("BZZ26", "BZZ27", "CLZ26", "CLZ27", "CLM27"):
        if f"1h/{code}" not in manifest["ok"]:
            fut = Future.from_code(code)
            run(
                f"1h/{code}",
                lambda f=fut: yahoo.fetch_intraday(f.yahoo_symbol, "1h", 730),
                out / "1h" / f"{code}.parquet",
            )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"\n{len(manifest['ok'])} ok, {len(manifest['failed'])} failed, {yahoo.requests_made} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
