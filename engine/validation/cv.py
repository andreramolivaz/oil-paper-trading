"""Cross-validation for time series with overlapping labels (brief §12; López de Prado, *Advances in Financial
Machine Learning*, ch. 7).

Observation ``i`` carries a label computed over the forward window ``[i, i + label_horizon]`` (``label_horizon``
bars, e.g. a 5-day forward return has ``label_horizon=5``).  A plain K-fold leaks information in two ways:

* **purging**: a training observation whose label window overlaps the test block already "knows" part of the test
  outcome, and a test label whose window overlaps a training observation leaks the other way.  Both are removed:
  every training index ``i`` satisfies ``|i - j| > label_horizon`` for every test index ``j``;
* **embargo**: serial correlation of features survives the purge, so a further ``ceil(embargo_pct * n)``
  observations *after* each test block are dropped from the training set as well.

Both generators yield ``(train_idx, test_idx)`` as ``numpy.int64`` arrays; the test blocks are contiguous, so a
model is never trained on the two sides of a hole while being tested in the middle of its own training span.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sized
from typing import Any

import numpy as np

__all__ = ["PurgedKFold", "purged_train_indices", "walk_forward_splits"]


def _n_obs(x: int | Sized) -> int:
    n = x if isinstance(x, int) else len(x)
    if n < 2:
        raise ValueError("need at least two observations")
    return n


def purged_train_indices(n: int, test_idx: np.ndarray, label_horizon: int = 0, embargo: int = 0) -> np.ndarray:
    """Training indices for a test set: everything except the test indices, the ``label_horizon`` observations on
    either side of every contiguous test run (purge) and the ``embargo`` observations after each run.

    ``test_idx`` may contain several non-adjacent runs (combinatorial CV); adjacent runs merge naturally.
    """
    if label_horizon < 0 or embargo < 0:
        raise ValueError("label_horizon and embargo must be >= 0")
    mask = np.ones(n, dtype=bool)
    t = np.unique(np.asarray(test_idx, dtype=np.int64))
    if t.size == 0:
        return np.flatnonzero(mask)
    if t[0] < 0 or t[-1] >= n:
        raise ValueError("test indices out of range")
    mask[t] = False
    breaks = np.flatnonzero(np.diff(t) > 1)
    starts = np.concatenate(([t[0]], t[breaks + 1]))
    ends = np.concatenate((t[breaks], [t[-1]]))
    for a, b in zip(starts, ends, strict=True):
        lo = max(0, int(a) - label_horizon)
        hi = min(n, int(b) + 1 + label_horizon + embargo)
        mask[lo:hi] = False
    return np.flatnonzero(mask)


class PurgedKFold:
    """K-fold with contiguous test blocks, purging of overlapping labels and an embargo after each test block.

    Parameters
    ----------
    n_splits:
        Number of contiguous test folds (>= 2).
    embargo_pct:
        Fraction of the sample dropped from the training set right after each test block
        (``ceil(embargo_pct * n)`` observations).  0.01 is López de Prado's default.
    label_horizon:
        Number of forward bars each label spans; training observations within this distance of a test
        observation (on either side) are purged.
    """

    def __init__(self, n_splits: int = 5, embargo_pct: float = 0.01, label_horizon: int = 1):
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        if not 0.0 <= embargo_pct < 1.0:
            raise ValueError("embargo_pct must be in [0, 1)")
        if label_horizon < 0:
            raise ValueError("label_horizon must be >= 0")
        self.n_splits = int(n_splits)
        self.embargo_pct = float(embargo_pct)
        self.label_horizon = int(label_horizon)

    def get_n_splits(self, X: Any = None, y: Any = None, groups: Any = None) -> int:  # noqa: N803 (sklearn API)
        return self.n_splits

    def embargo_size(self, n: int) -> int:
        return math.ceil(self.embargo_pct * n)

    def split(self, X: int | Sized, y: Any = None, groups: Any = None) -> Iterator[tuple[np.ndarray, np.ndarray]]:  # noqa: N803
        """Yield ``(train_idx, test_idx)``; ``X`` may be the number of observations or any sized object."""
        n = _n_obs(X)
        if self.n_splits > n:
            raise ValueError("n_splits cannot exceed the number of observations")
        embargo = self.embargo_size(n)
        for test_idx in np.array_split(np.arange(n, dtype=np.int64), self.n_splits):
            train_idx = purged_train_indices(n, test_idx, self.label_horizon, embargo)
            yield train_idx, test_idx

    def __repr__(self) -> str:
        return (
            f"PurgedKFold(n_splits={self.n_splits}, embargo_pct={self.embargo_pct}, label_horizon={self.label_horizon})"
        )


def walk_forward_splits(
    n: int,
    train_min: int,
    test_len: int,
    step: int | None = None,
    label_horizon: int = 0,
    expanding: bool = True,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Walk-forward (anchored/expanding by default) splits over ``n`` observations.

    The first test block starts at ``train_min``; successive blocks advance by ``step`` (default ``test_len``).
    The last block may be shorter than ``test_len``.  The last ``label_horizon`` training observations before each
    test block are purged (their labels would overlap the test window).  With ``expanding=False`` the training
    window slides and keeps a fixed length of ``train_min`` observations.
    """
    if n < 2 or train_min < 1 or test_len < 1:
        raise ValueError("n >= 2, train_min >= 1 and test_len >= 1 are required")
    if label_horizon < 0:
        raise ValueError("label_horizon must be >= 0")
    step = test_len if step is None else int(step)
    if step < 1:
        raise ValueError("step must be >= 1")
    if train_min + 1 > n:
        return
    start = train_min
    while start < n:
        stop = min(n, start + test_len)
        train_end = max(0, start - label_horizon)
        train_start = 0 if expanding else max(0, train_end - train_min)
        test_idx = np.arange(start, stop, dtype=np.int64)
        train_idx = np.arange(train_start, train_end, dtype=np.int64)
        if train_idx.size > 0:
            yield train_idx, test_idx
        start += step
