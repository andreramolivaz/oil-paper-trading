"""File-backed state store: atomic JSON writes, append-only JSONL with monthly rotation, unique appends.

Layout under root (the `data` branch checkout in production, a temp dir in tests):
    state/account.json, state/positions.json, state/epochs.json, state/health.json, ...
    state/trades-YYYY-MM.jsonl, state/equity-YYYY-MM.jsonl, state/forecasts-YYYY-MM.jsonl
    state/raw/<source>/...
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "item"):  # numpy scalars
        return o.item()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o)!r}")


def dumps(obj: Any, indent: int | None = None) -> str:
    return json.dumps(obj, default=_default, ensure_ascii=False, indent=indent, sort_keys=False, allow_nan=False)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class StateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- JSON documents -------------------------------------------------
    def path(self, name: str) -> Path:
        return self.root / name

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def read_json(self, name: str, default: Any = None) -> Any:
        p = self.path(name)
        if not p.exists():
            return default
        with p.open(encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, name: str, obj: Any, indent: int | None = 2) -> None:
        atomic_write_text(self.path(name), dumps(obj, indent=indent) + "\n")

    # ---- JSONL logs -----------------------------------------------------
    @staticmethod
    def _month_suffix(ts: datetime | None) -> str:
        t = (ts or datetime.now(tz=UTC)).astimezone(UTC)
        return f"{t.year:04d}-{t.month:02d}"

    def jsonl_path(self, name: str, ts: datetime | None = None, rotate: bool = True) -> Path:
        if rotate:
            return self.path(f"{name}-{self._month_suffix(ts)}.jsonl")
        return self.path(f"{name}.jsonl")

    def jsonl_files(self, name: str) -> list[Path]:
        files = sorted(self.root.glob(f"{name}-????-??.jsonl"))
        single = self.path(f"{name}.jsonl")
        if single.exists():
            files.insert(0, single)
        return files

    def append_jsonl(self, name: str, record: Any, ts: datetime | None = None, rotate: bool = True) -> None:
        p = self.jsonl_path(name, ts, rotate)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def iter_jsonl(self, name: str) -> Iterator[dict[str, Any]]:
        for p in self.jsonl_files(name):
            with p.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        yield json.loads(line)

    def read_jsonl(self, name: str) -> list[dict[str, Any]]:
        return list(self.iter_jsonl(name))

    def jsonl_keys(self, name: str, key_field: str) -> set[str]:
        return {str(r[key_field]) for r in self.iter_jsonl(name) if key_field in r}

    def append_jsonl_unique(
        self, name: str, record: Any, key_field: str, ts: datetime | None = None, rotate: bool = True
    ) -> bool:
        """Append unless a record with the same key already exists. Returns True if written."""
        d = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        key = str(d[key_field])
        if key in self.jsonl_keys(name, key_field):
            return False
        self.append_jsonl(name, d, ts, rotate)
        return True

    def tail_jsonl(self, name: str, n: int) -> list[dict[str, Any]]:
        rows = self.read_jsonl(name)
        return rows[-n:]

    # ---- maintenance ----------------------------------------------------
    def compact_jsonl(self, name: str, keep_months: int) -> list[Path]:
        """Delete monthly files older than keep_months (caller must have archived them). Returns deleted paths."""
        files = [p for p in self.jsonl_files(name) if p.name != f"{name}.jsonl"]
        deleted: list[Path] = []
        for p in files[:-keep_months] if keep_months > 0 else []:
            p.unlink()
            deleted.append(p)
        return deleted

    def list(self, pattern: str = "*") -> Iterable[Path]:
        return sorted(self.root.glob(pattern))
