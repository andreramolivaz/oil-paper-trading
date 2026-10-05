"""Deterministic identifiers (idempotency keys)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def idempotency_key(*parts: object) -> str:
    """Stable sha1 over the string form of the parts. Same inputs -> same key, always."""
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def short_id(*parts: object, n: int = 12) -> str:
    return idempotency_key(*parts)[:n]


def stable_join(items: Iterable[str]) -> str:
    return ",".join(sorted(items))
