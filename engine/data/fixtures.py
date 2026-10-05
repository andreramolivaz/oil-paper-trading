"""Load the captured real :class:`MarketData` snapshot used by the offline tests.

``tests/fixtures/market_data/`` holds one parquet per table, captured from the live sources with
``python -m engine.cli fetch --snapshot`` + :func:`engine.data.assemble.build_market_data` (see the folder's
README.md for the capture time, the per-table sources and the real values). The ``published_at`` series travel
INSIDE their table as columns (``published_at``, plus ``published_at_brent_spot`` / ``published_at_wti_spot`` in
``prices.parquet``) and are lifted back into :attr:`MarketData.published_at` here, so the fixture is as
point-in-time safe as the live object.

``intraday`` is deliberately absent (it would double the fixture size and no offline test needs it).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from engine.data.market_data import (
    COT_COLUMNS,
    CURVE_COLUMNS,
    NEWS_COLUMNS,
    PRICE_COLUMNS,
    WPSR_COLUMNS,
    MarketData,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "market_data"
PUBLISHED_COLUMN = "published_at"
PUBLISHED_PREFIX = "published_at_"
META_FILE = "meta.json"
TABLE_FILES = {
    "prices": "prices.parquet",
    "curve": "curve.parquet",
    "wpsr": "wpsr.parquet",
    "cot": "cot.parquet",
    "news": "news.parquet",
    "rigs": "rigs.parquet",
}


def fixture_dir(folder: Path | str | None = None) -> Path:
    return Path(folder) if folder is not None else FIXTURE_DIR


def _read(folder: Path, name: str) -> pd.DataFrame | None:
    path = folder / TABLE_FILES[name]
    if not path.exists():
        return None
    return pd.read_parquet(path, engine="pyarrow")


def _pop_published(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Split the ``published_at*`` columns out of a table into ``{key: series}``."""
    out: dict[str, pd.Series] = {}
    columns = [c for c in frame.columns if str(c) == PUBLISHED_COLUMN or str(c).startswith(PUBLISHED_PREFIX)]
    for col in columns:
        key = "self" if str(col) == PUBLISHED_COLUMN else str(col)[len(PUBLISHED_PREFIX) :]
        out[key] = pd.to_datetime(frame[col], errors="coerce", utc=True)
    return frame.drop(columns=columns), out


def load_fixture_market_data(folder: Path | str | None = None) -> MarketData:
    """The captured real :class:`MarketData` (raises ``FileNotFoundError`` when the fixture is absent)."""
    base = fixture_dir(folder)
    if not base.exists():
        raise FileNotFoundError(f"fixture MarketData non trovata: {base}")
    published: dict[str, pd.Series] = {}

    prices = _read(base, "prices")
    if prices is None:
        raise FileNotFoundError(f"{base / TABLE_FILES['prices']} mancante")
    prices, pub = _pop_published(prices)
    prices.index = pd.DatetimeIndex(prices.index, name="date")
    prices = prices.reindex(columns=PRICE_COLUMNS)
    for key, series in pub.items():
        published["prices" if key == "self" else key] = series

    curve_raw = _read(base, "curve")
    if curve_raw is not None and not curve_raw.empty:
        curve, pub = _pop_published(curve_raw)
        curve.index = pd.DatetimeIndex(curve.index, name="date")
        extra = [c for c in curve.columns if c not in {*CURVE_COLUMNS, "M1_code"}]
        curve = curve.reindex(columns=[*CURVE_COLUMNS, "M1_code", *extra])
        if "self" in pub:
            published["curve"] = pub["self"]
    else:
        curve = pd.DataFrame(columns=[*CURVE_COLUMNS, "M1_code"], index=pd.DatetimeIndex([], name="date"))
    proxy = [c for c in curve.columns if str(c).startswith("WTI_C")]
    has_real = curve["M1"].notna() if "M1" in curve.columns else pd.Series(False, index=curve.index)
    curve_approx = (
        ((~has_real.fillna(False)) & curve[proxy].notna().any(axis=1)).astype(bool)
        if proxy
        else pd.Series(False, index=curve.index, dtype=bool)
    )

    wpsr = _read(base, "wpsr")
    if wpsr is None or wpsr.empty:
        wpsr = pd.DataFrame(columns=WPSR_COLUMNS, index=pd.DatetimeIndex([], name="published_at"))
    else:
        wpsr.index = pd.DatetimeIndex(pd.to_datetime(wpsr.index, utc=True), name="published_at")
        wpsr = wpsr.reindex(columns=WPSR_COLUMNS)

    cot = _read(base, "cot")
    if cot is None or cot.empty:
        cot = pd.DataFrame(columns=COT_COLUMNS, index=pd.DatetimeIndex([], name="published_at"))
    else:
        cot.index = pd.DatetimeIndex(pd.to_datetime(cot.index, utc=True), name="published_at")
        cot = cot.reindex(columns=COT_COLUMNS)

    news_raw = _read(base, "news")
    if news_raw is None or news_raw.empty:
        news = pd.DataFrame(columns=NEWS_COLUMNS, index=pd.DatetimeIndex([], name="date"))
    else:
        news, pub = _pop_published(news_raw)
        news.index = pd.DatetimeIndex(news.index, name="date")
        news = news.reindex(columns=NEWS_COLUMNS)
        if "self" in pub:
            published["news"] = pub["self"]

    rigs_raw = _read(base, "rigs")
    if rigs_raw is None or rigs_raw.empty:
        rigs = pd.Series(dtype="float64", name="rigs")
    else:
        rigs_frame, pub = _pop_published(rigs_raw)
        rigs_frame.index = pd.DatetimeIndex(rigs_frame.index, name="date")
        column = "rigs" if "rigs" in rigs_frame.columns else str(rigs_frame.columns[0])
        rigs = pd.to_numeric(rigs_frame[column], errors="coerce").astype("float64").rename("rigs")
        if "self" in pub:
            published["rigs"] = pub["self"]

    meta_path = base / META_FILE
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["fixture"] = str(base)
    return MarketData(
        prices=prices,
        curve=curve,
        curve_approx=curve_approx,
        wpsr=wpsr,
        cot=cot,
        news=news,
        rigs=rigs,
        intraday=None,
        published_at=published,
        health=[],
        meta=meta,
    )


def save_fixture_market_data(
    md: MarketData,
    folder: Path | str | None = None,
    start: str | None = None,
    meta: dict[str, object] | None = None,
) -> list[Path]:
    """Write ``md`` as the fixture (used by the capture script; keeps no intraday). Returns the files written."""
    base = fixture_dir(folder)
    base.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    prices = md.prices.copy()
    if start:
        prices = prices[prices.index >= pd.Timestamp(start)]
    for key, column in (("prices", PUBLISHED_COLUMN), ("brent_spot", f"{PUBLISHED_PREFIX}brent_spot")):
        series = md.published_at.get(key)
        if series is not None:
            prices[column] = pd.to_datetime(series.reindex(prices.index), utc=True)
    wti_pub = md.published_at.get("wti_spot")
    if wti_pub is not None:
        prices[f"{PUBLISHED_PREFIX}wti_spot"] = pd.to_datetime(wti_pub.reindex(prices.index), utc=True)
    written.append(_write(prices, base / TABLE_FILES["prices"]))

    curve = md.curve.copy()
    curve_pub = md.published_at.get("curve")
    if curve_pub is not None and len(curve):
        curve[PUBLISHED_COLUMN] = pd.to_datetime(curve_pub.reindex(curve.index), utc=True)
    written.append(_write(curve, base / TABLE_FILES["curve"]))
    written.append(_write(md.wpsr, base / TABLE_FILES["wpsr"]))
    written.append(_write(md.cot, base / TABLE_FILES["cot"]))

    news = md.news.copy()
    news_pub = md.published_at.get("news")
    if news_pub is not None and len(news):
        news[PUBLISHED_COLUMN] = pd.to_datetime(news_pub.reindex(news.index), utc=True)
    written.append(_write(news, base / TABLE_FILES["news"]))

    rigs = md.rigs.to_frame("rigs")
    rigs_pub = md.published_at.get("rigs")
    if rigs_pub is not None and len(rigs):
        rigs[PUBLISHED_COLUMN] = pd.to_datetime(rigs_pub.reindex(rigs.index), utc=True)
    written.append(_write(rigs, base / TABLE_FILES["rigs"]))

    if meta is not None:
        (base / META_FILE).write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        written.append(base / META_FILE)
    return written


def _write(frame: pd.DataFrame, path: Path) -> Path:
    out = frame.copy()
    out.columns = [str(c) for c in out.columns]
    out.to_parquet(path, engine="pyarrow", index=True)
    return path
