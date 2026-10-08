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

from engine.core.calendar import contract_code, listed_months  # noqa: E402
from engine.core.instruments import Future  # noqa: E402
from engine.data.adapters.yahoo import YahooAdapter  # noqa: E402

DAILY = [
    "BZ=F", "CL=F", "MCL=F", "QM=F", "HO=F", "RB=F", "NG=F",
    "BNO", "USO", "UCO", "SCO", "DBO", "OILK", "XLE", "XOP", "^OVX", "^VIX", "DX-Y.NYB",
]  # fmt: skip
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="out/research")
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
    for symbol in DAILY:
        run(f"daily/{symbol}", lambda s=symbol: yahoo.fetch_daily(s, date(2000, 1, 1), today), out / "daily" / f"{safe(symbol)}.parquet")
    for root, months in CURVE_MONTHS.items():
        for year, month in listed_months(root, today, months):
            code = contract_code(root, year, month)
            run(f"contract/{code}", lambda c=code: yahoo.fetch_contract_history(c), out / "contract" / f"{code}.parquet")
    for symbol in INTRADAY_1H:
        run(f"1h/{symbol}", lambda s=symbol: yahoo.fetch_intraday(s, "1h", 730), out / "1h" / f"{safe(symbol)}.parquet")
    for root, months in CONTRACT_INTRADAY_MONTHS.items():
        for year, month in listed_months(root, today, months):
            fut = Future(root, year, month)
            run(f"1h/{fut.code}", lambda f=fut: yahoo.fetch_intraday(f.yahoo_symbol, "1h", 730), out / "1h" / f"{fut.code}.parquet")
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
            run(f"1h/{code}", lambda f=fut: yahoo.fetch_intraday(f.yahoo_symbol, "1h", 730), out / "1h" / f"{code}.parquet")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"\n{len(manifest['ok'])} ok, {len(manifest['failed'])} failed, {yahoo.requests_made} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
