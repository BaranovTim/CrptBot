"""Swing high / swing low detection — the file everything else inherits from.

The single most destructive bug available in this whole project is a pivot
that is visible before the bars proving it exists have closed.  Every SMC
feature is built on pivots, so one bar of leaked future here contaminates the
entire feature block, and the backtest will look wonderful.

The defence is structural rather than careful: a ``Pivot`` carries both the
bar where the extremum happened (``index``) and the bar from which it may be
used (``confirmed_at``).  Consumers iterate bar by bar and may only ingest
pivots whose ``confirmed_at <= t``.  Detection scanning the whole array is
then harmless — the stamp is what enforces honesty.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

from .indicators import rolling_max_forward, rolling_min_forward

HIGH = 1
LOW = -1


@dataclass(frozen=True)
class Pivot:
    index: int          # bar where the extremum occurred
    confirmed_at: int   # first bar at which this pivot may be used
    price: float
    kind: int           # +1 swing high, -1 swing low

    @property
    def is_high(self) -> bool:
        return self.kind == HIGH


def find_pivots(
    high: pd.Series,
    low: pd.Series,
    left: int = 3,
    right: int = 3,
    confirm_bars: int = None,
) -> List[Pivot]:
    """Fractal pivots: a bar that dominates ``left`` bars back and ``right`` forward.

    Ties are broken toward the earlier bar (strict ``>`` on the left, ``>=`` on
    the right), so a plateau of equal highs yields exactly one pivot, at its
    first bar.  Deterministic tie-breaking matters: equal highs are themselves
    a signal, and we do not want the count wobbling with float noise.
    """
    if confirm_bars is None:
        confirm_bars = right
    if confirm_bars < right:
        raise ValueError("confirm_bars must be >= right")

    # work with plain 0..n-1 positions, not timestamps, so "3 bars back" is
    # just index-3 and never depends on the calendar
    h = high.reset_index(drop=True).astype(float)
    l = low.reset_index(drop=True).astype(float)
    n = len(h)

    # highest high in the `left` bars BEFORE this one.
    # shift(1) is what makes it "before" instead of "including"
    left_max = h.rolling(left, min_periods=left).max().shift(1)
    # highest high in the `right` bars AFTER this one
    right_max = rolling_max_forward(h, right)
    # same two windows for lows
    left_min = l.rolling(left, min_periods=left).min().shift(1)
    right_min = rolling_min_forward(l, right)

    # a swing high = taller than everything on its left, and at least as tall
    # as everything on its right.
    # strict `>` on the left and loose `>=` on the right is the tie-break rule:
    # if five bars share the same high, only the FIRST one becomes the pivot
    is_high = (h > left_max) & (h >= right_max)
    is_low = (l < left_min) & (l <= right_min)

    pivots: List[Pivot] = []
    # np.flatnonzero gives the bar numbers where the test came out True.
    # na_value=False means "not enough bars yet" counts as not-a-pivot
    for i in np.flatnonzero(is_high.to_numpy(na_value=False)):
        i = int(i)
        c = i + confirm_bars      # the bar where we are ALLOWED to know this
        # if confirmation would land past the end of the data we never learned
        # about it in time, so we must not use it at all
        if c < n:
            pivots.append(Pivot(index=i, confirmed_at=c, price=float(h.iat[i]), kind=HIGH))
    for i in np.flatnonzero(is_low.to_numpy(na_value=False)):
        i = int(i)
        c = i + confirm_bars
        if c < n:
            pivots.append(Pivot(index=i, confirmed_at=c, price=float(l.iat[i]), kind=LOW))

    # sort by the bar we LEARN about them, not the bar they happened on -
    # that is the order a live system would receive them in
    pivots.sort(key=lambda p: (p.confirmed_at, p.index, -p.kind))
    return pivots


def by_confirmation(pivots: List[Pivot], n_bars: int) -> Dict[int, List[Pivot]]:
    """Bucket pivots by the bar at which they become usable."""
    out: Dict[int, List[Pivot]] = {}
    for p in pivots:
        if 0 <= p.confirmed_at < n_bars:
            out.setdefault(p.confirmed_at, []).append(p)
    return out
