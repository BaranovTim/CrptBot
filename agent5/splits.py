"""Purged K-fold with embargo: cross-validation that cannot cheat.

Ordinary K-fold assumes samples are independent. Ours are nothing of the
kind, in two distinct ways, each needing its own defence:

PURGE. A sample taken 10 bars before the test period has a label that
resolves up to max_hold bars later - INSIDE the test period. Train on it
and the model has memorised part of the test outcome. So any training
sample whose label window [t, t1] overlaps the test window is removed.

EMBARGO. Features are autocorrelated: volatility this hour looks like
volatility last hour. A training sample taken right AFTER the test block
is nearly a duplicate of the last test samples even though no label
overlaps. A strip of samples after each test block is dropped too.

Folds are contiguous in time - never shuffled. Shuffled folds interleave
train and test bars so thoroughly that purging would delete almost
everything, and what remains still leaks through autocorrelation.
"""
from __future__ import annotations

from typing import Iterator, Tuple

import numpy as np


class PurgedKFold:
    def __init__(self, n_splits: int, t1: np.ndarray, embargo_pct: float = 0.01):
        """
        t1[i] = position of the bar where sample i's label resolved.
        Samples must already be in time order (they are: one per bar).
        """
        if n_splits < 2:
            raise ValueError("n_splits must be at least 2")
        self.n_splits = n_splits
        self.t1 = np.asarray(t1, dtype=float)
        self.embargo_pct = float(embargo_pct)

    def split(self, positions: np.ndarray) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
        """Yield (train_idx, test_idx) as indices INTO ``positions``.

        ``positions[i]`` is the bar position of sample i, used together with
        t1 to decide overlap.
        """
        positions = np.asarray(positions, dtype=float)
        n = len(positions)
        if n < self.n_splits:
            raise ValueError(f"{n} samples cannot make {self.n_splits} folds")
        embargo = int(np.ceil(n * self.embargo_pct))

        # contiguous, near-equal test blocks in time order
        bounds = np.linspace(0, n, self.n_splits + 1, dtype=int)
        for k in range(self.n_splits):
            test_idx = np.arange(bounds[k], bounds[k + 1])
            test_start = positions[test_idx[0]]           # first bar the test can see
            test_end = np.nanmax(self.t1[test_idx])       # last bar any test label touches

            train_mask = np.ones(n, dtype=bool)
            train_mask[test_idx] = False

            # PURGE: a training sample leaks if its label window overlaps the
            # test window in either direction:
            #   its label resolves after the test starts  (t1 >= test_start)
            #   AND it begins before the test's labels end (t <= test_end)
            overlap = (self.t1 >= test_start) & (positions <= test_end)
            train_mask &= ~overlap

            # EMBARGO: drop the strip of samples just after the test block -
            # they share autocorrelated features with the test's tail even
            # when no label overlaps
            after = (positions > test_end) & (positions <= test_end + embargo)
            train_mask &= ~after

            yield np.flatnonzero(train_mask), test_idx
