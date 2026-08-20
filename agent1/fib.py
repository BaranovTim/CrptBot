"""Fibonacci levels off the last confirmed swing leg.

Fib is only meaningful with an objective anchor.  Ours is the most recent
pair of confirmed opposing pivots — no discretion, no hand-drawn leg.

We emit a continuous ``dist_to_fib_618_atr`` rather than a boolean "is price
at the 0.618?".  A boolean needs an arbitrary tolerance, and picking that
tolerance is exactly the kind of unvalidated assumption Agent 5 exists to
avoid.  Hand it the distance; let the tree find the threshold, if there is one.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from .pivots import HIGH, Pivot

FIB_COLUMNS = ("fib_position", "fib_leg_direction", "dist_to_fib_618_atr")

GOLDEN = 0.618


def compute_fib(bars: pd.DataFrame, pivots: List[Pivot], atr: pd.Series) -> pd.DataFrame:
    n = len(bars)
    c = bars["close"].to_numpy(float)
    a = atr.to_numpy(float)

    piv = sorted(pivots, key=lambda p: (p.confirmed_at, p.index))
    k = 0
    seen: List[Pivot] = []

    pos = np.full(n, np.nan)
    leg_dir = np.full(n, np.nan)
    d618 = np.full(n, np.nan)

    for t in range(n):
        while k < len(piv) and piv[k].confirmed_at <= t:
            seen.append(piv[k])
            k += 1
        if len(seen) < 2:
            continue
        atr_t = a[t]
        if np.isnan(atr_t) or atr_t <= 0:
            continue

        end = seen[-1]
        start = None
        for p in reversed(seen[:-1]):
            if p.kind != end.kind and p.index < end.index:
                start = p
                break
        if start is None:
            continue

        lo = min(start.price, end.price)
        hi = max(start.price, end.price)
        span = hi - lo
        if span <= 0:
            continue

        up_leg = end.kind == HIGH
        pos[t] = (c[t] - lo) / span
        leg_dir[t] = 1.0 if up_leg else -1.0
        # Retracement measured back from where the leg ended.
        level = hi - GOLDEN * span if up_leg else lo + GOLDEN * span
        d618[t] = (level - c[t]) / atr_t

    return pd.DataFrame(
        {"fib_position": pos, "fib_leg_direction": leg_dir, "dist_to_fib_618_atr": d618},
        index=bars.index,
    )
