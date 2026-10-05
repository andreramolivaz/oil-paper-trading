"""Point-in-time raw snapshot store.

Layout under ``<root>/raw/<source>/<key>/``:

* ``<observed_at>.parquet`` - one file per fetch, named after the UTC instant the fetch was observed
  (``20261005T155233Z.parquet``: ISO 8601 basic format, safe on every filesystem and sortable as text);
* ``latest.parquet`` - a copy of the newest snapshot, refreshed on every :meth:`RawStore.save`;
* ``<observed_at>.json`` instead of ``.parquet`` when the frame cannot be written as parquet (mixed-type object
  columns, exotic index): the fallback keeps the data rather than losing the fetch.

Every snapshot carries two extra columns: ``observed_at`` (UTC, when WE saw the data) and ``source`` (the adapter
that produced it). Together with the adapter's own ``published_at`` column they make revisions explicit: a later
snapshot never overwrites an earlier one, so :meth:`RawStore.load_asof` can answer "what did we know at T?".
Nothing is ever rewritten in place; :meth:`RawStore.compact` only deletes whole intermediate snapshots.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from engine.data.base import FetchResult

log = logging.getLogger(__name__)

OBSERVED_AT_COLUMN = "observed_at"
SOURCE_COLUMN = "source"
LATEST_NAME = "latest"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
STAMP_RE = re.compile(r"^(\d{8}T\d{6}Z)$")
INDEX_FIELD = "__index__"
INDEX_NAME_FIELD = "__index_name__"


def format_stamp(ts: datetime) -> str:
    """UTC instant -> the snapshot file stem."""
    return ts.astimezone(UTC).strftime(STAMP_FORMAT)


def parse_stamp(stem: str) -> datetime | None:
    """Snapshot file stem -> UTC instant (None when the name is not a stamp, e.g. ``latest``)."""
    m = STAMP_RE.match(stem)
    if not m:
        return None
    return datetime.strptime(m.group(1), STAMP_FORMAT).replace(tzinfo=UTC)


def _as_utc(ts: datetime | pd.Timestamp) -> datetime:
    t = pd.Timestamp(ts)
    t = t.tz_localize(UTC) if t.tzinfo is None else t.tz_convert(UTC)
    return t.to_pydatetime()


class RawStore:
    """Append-only snapshot store for adapter output (``<root>/raw/<source>/<key>/``)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    # ---- paths ------------------------------------------------------------------------------------------------
    def dir_for(self, source: str, key: str) -> Path:
        return self.root / "raw" / source / key

    def sources(self) -> list[str]:
        base = self.root / "raw"
        return sorted(p.name for p in base.glob("*") if p.is_dir()) if base.exists() else []

    def keys(self, source: str) -> list[str]:
        base = self.root / "raw" / source
        return sorted(p.name for p in base.glob("*") if p.is_dir()) if base.exists() else []

    # ---- write ------------------------------------------------------------------------------------------------
    def save(self, source: str, key: str, result: FetchResult) -> Path:
        """Write ``result.frame`` (plus ``observed_at`` and ``source``) as a new snapshot; refresh ``latest``.

        ``observed_at`` is ``result.fetched_at``. Returns the path of the snapshot written.
        """
        observed_at = _as_utc(result.fetched_at)
        frame = result.frame.copy()
        frame[OBSERVED_AT_COLUMN] = pd.Timestamp(observed_at)
        frame[SOURCE_COLUMN] = result.source
        folder = self.dir_for(source, key)
        folder.mkdir(parents=True, exist_ok=True)
        stem = format_stamp(observed_at)
        path = folder / f"{stem}.parquet"
        try:
            frame.to_parquet(path, engine="pyarrow", index=True)
            fmt = "parquet"
        except Exception as e:  # pyarrow refuses mixed-type object columns / exotic indexes
            if path.exists():
                path.unlink()
            path = folder / f"{stem}.json"
            path.write_text(_frame_to_json(frame), encoding="utf-8")
            fmt = "json"
            log.warning("raw store %s/%s: parquet failed (%s), wrote JSON instead", source, key, e)
        latest = folder / f"{LATEST_NAME}.{fmt}"
        latest.write_bytes(path.read_bytes())
        for other in folder.glob(f"{LATEST_NAME}.*"):  # a format change must not leave a stale latest behind
            if other != latest:
                other.unlink()
        return path

    # ---- read -------------------------------------------------------------------------------------------------
    def history(self, source: str, key: str) -> list[tuple[datetime, Path]]:
        """Every snapshot as ``(observed_at, path)``, oldest first (``latest`` excluded)."""
        folder = self.dir_for(source, key)
        if not folder.exists():
            return []
        out: list[tuple[datetime, Path]] = []
        for path in folder.iterdir():
            if path.suffix not in {".parquet", ".json"}:
                continue
            stamp = parse_stamp(path.stem)
            if stamp is not None:
                out.append((stamp, path))
        out.sort(key=lambda item: (item[0], item[1].name))
        return out

    def load_latest(self, source: str, key: str) -> pd.DataFrame | None:
        """The newest snapshot (``latest`` file when present, else the newest stamped one)."""
        folder = self.dir_for(source, key)
        for suffix in (".parquet", ".json"):
            path = folder / f"{LATEST_NAME}{suffix}"
            if path.exists():
                return _read(path)
        hist = self.history(source, key)
        return _read(hist[-1][1]) if hist else None

    def load_asof(self, source: str, key: str, asof: datetime) -> pd.DataFrame | None:
        """The newest snapshot observed at or before ``asof`` (revision-aware point-in-time read)."""
        cutoff = _as_utc(asof)
        candidates = [(ts, path) for ts, path in self.history(source, key) if ts <= cutoff]
        return _read(candidates[-1][1]) if candidates else None

    def load_all(self, source: str, key: str) -> list[tuple[datetime, pd.DataFrame]]:
        """Every snapshot, oldest first, as ``(observed_at, frame)``."""
        return [(ts, _read(path)) for ts, path in self.history(source, key)]

    # ---- maintenance ------------------------------------------------------------------------------------------
    def compact(self, source: str, key: str, keep_last_n: int, keep_weekly: bool = True) -> list[Path]:
        """Delete intermediate snapshots, keeping the last ``keep_last_n`` and (by default) the first of each
        ISO week. Returns the deleted paths. ``latest`` is untouched."""
        hist = self.history(source, key)
        if not hist:
            return []
        keep: set[Path] = {path for _, path in hist[-max(0, keep_last_n) :]} if keep_last_n > 0 else set()
        if keep_weekly:
            seen: set[tuple[int, int]] = set()
            for ts, path in hist:  # oldest first: the first snapshot of each ISO week is kept
                iso = ts.isocalendar()
                week = (iso[0], iso[1])
                if week not in seen:
                    seen.add(week)
                    keep.add(path)
        deleted: list[Path] = []
        for _, path in hist:
            if path not in keep:
                path.unlink()
                deleted.append(path)
        return deleted


def _read(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path, engine="pyarrow")
    return _frame_from_json(path.read_text(encoding="utf-8"))


def _frame_to_json(frame: pd.DataFrame) -> str:
    payload = {
        INDEX_NAME_FIELD: frame.index.name,
        INDEX_FIELD: [_json_scalar(v) for v in frame.index],
        "columns": {str(c): [_json_scalar(v) for v in frame[c]] for c in frame.columns},
    }
    return json.dumps(payload, ensure_ascii=False)


def _json_scalar(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        return value.item()  # numpy scalar
    return value if isinstance(value, str | int | float | bool) else str(value)


def _frame_from_json(text: str) -> pd.DataFrame:
    payload = json.loads(text)
    columns = {str(k): v for k, v in payload["columns"].items()}
    index = pd.Index(payload[INDEX_FIELD], name=payload.get(INDEX_NAME_FIELD))
    with contextlib.suppress(TypeError, ValueError):
        index = pd.DatetimeIndex(index, name=index.name)
    frame = pd.DataFrame(columns, index=index)
    for col in (OBSERVED_AT_COLUMN, "published_at"):
        if col in frame.columns:
            frame[col] = pd.to_datetime(frame[col], errors="coerce", utc=True)
    return frame
