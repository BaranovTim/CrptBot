"""W / M / head-and-shoulders, as graded completion rather than yes-no.

Your original question was how to represent "the beginning of a W".  A binary
detector cannot: until the second bottom prints, the shape is a hypothesis,
and by the time it is certain the move is over.  So each figure emits a
completion fraction in [0, 1]:

    ~0.4   first bottom and neckline exist, price has come back to the level
    ~0.7   second bottom confirmed, symmetric and correctly spaced
     1.0   neckline broken

Do not expect much accuracy from these.  They are cheap, and they are here so
the ablation can measure whether classical figures carry anything once
structure and order flow are present.  Being able to answer that with a number
is worth more than the features themselves.

Sign convention: W is inherently bullish and M inherently bearish, so their
direction is carried by the column name.  H&S can point either way, so it gets
an explicit ``hs_direction``.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from .config import Agent1Config
from .pivots import HIGH, LOW, Pivot

FIGURE_COLUMNS = ("w_completion", "m_completion", "hs_completion", "hs_direction")


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _kinds(pivs: Sequence[Pivot]) -> tuple:
    return tuple(p.kind for p in pivs)


def _double_bottom(
    pivs: List[Pivot], close: float, atr: float, t: int, cfg: Agent1Config
) -> float:
    """Bullish W. Mirrored for M by flipping signs at the call site."""
    tol = cfg.figure_tol_atr * atr
    score = 0.0

    # --- complete or near-complete: L1, H1, L2 -----------------------------
    if len(pivs) >= 3 and _kinds(pivs[-3:]) == (LOW, HIGH, LOW):
        l1, h1, l2 = pivs[-3:]
        sep = l2.index - l1.index
        depth = h1.price - max(l1.price, l2.price)
        if cfg.figure_min_sep <= sep <= cfg.figure_max_sep and depth > 0:
            sym = abs(l2.price - l1.price)
            base = 0.7 * _clip01(1.0 - sym / tol) if tol > 0 else 0.0
            if close > h1.price:
                base += 0.3 * _clip01((close - h1.price) / (0.5 * atr))
            score = max(score, _clip01(base))

    # --- forming: L1, H1 exist and price has returned to the L1 level ------
    if len(pivs) >= 2 and _kinds(pivs[-2:]) == (LOW, HIGH):
        l1, h1 = pivs[-2:]
        sep = t - l1.index
        depth = h1.price - l1.price
        if cfg.figure_min_sep <= sep <= cfg.figure_max_sep and depth > 0 and tol > 0:
            prox = abs(close - l1.price)
            score = max(score, 0.4 * _clip01(1.0 - prox / tol))

    return score


def _double_top(
    pivs: List[Pivot], close: float, atr: float, t: int, cfg: Agent1Config
) -> float:
    tol = cfg.figure_tol_atr * atr
    score = 0.0

    if len(pivs) >= 3 and _kinds(pivs[-3:]) == (HIGH, LOW, HIGH):
        h1, l1, h2 = pivs[-3:]
        sep = h2.index - h1.index
        depth = min(h1.price, h2.price) - l1.price
        if cfg.figure_min_sep <= sep <= cfg.figure_max_sep and depth > 0:
            sym = abs(h2.price - h1.price)
            base = 0.7 * _clip01(1.0 - sym / tol) if tol > 0 else 0.0
            if close < l1.price:
                base += 0.3 * _clip01((l1.price - close) / (0.5 * atr))
            score = max(score, _clip01(base))

    if len(pivs) >= 2 and _kinds(pivs[-2:]) == (HIGH, LOW):
        h1, l1 = pivs[-2:]
        sep = t - h1.index
        depth = h1.price - l1.price
        if cfg.figure_min_sep <= sep <= cfg.figure_max_sep and depth > 0 and tol > 0:
            prox = abs(close - h1.price)
            score = max(score, 0.4 * _clip01(1.0 - prox / tol))

    return score


def _head_shoulders(
    pivs: List[Pivot], close: float, atr: float, cfg: Agent1Config
) -> tuple:
    """Returns (completion, direction). Bearish H&S is -1, inverse is +1."""
    tol = cfg.figure_tol_atr * atr
    if len(pivs) < 5 or tol <= 0:
        return 0.0, 0.0

    last5 = pivs[-5:]
    kinds = _kinds(last5)

    if kinds == (HIGH, LOW, HIGH, LOW, HIGH):
        ls, t1, head, t2, rs = last5
        if head.price > ls.price and head.price > rs.price:
            sep = rs.index - ls.index
            if cfg.figure_min_sep <= sep <= cfg.figure_max_sep:
                sym = abs(rs.price - ls.price)
                score = 0.7 * _clip01(1.0 - sym / tol)
                neckline = min(t1.price, t2.price)
                if close < neckline:
                    score += 0.3 * _clip01((neckline - close) / (0.5 * atr))
                return _clip01(score), -1.0

    if kinds == (LOW, HIGH, LOW, HIGH, LOW):
        ls, t1, head, t2, rs = last5
        if head.price < ls.price and head.price < rs.price:
            sep = rs.index - ls.index
            if cfg.figure_min_sep <= sep <= cfg.figure_max_sep:
                sym = abs(rs.price - ls.price)
                score = 0.7 * _clip01(1.0 - sym / tol)
                neckline = max(t1.price, t2.price)
                if close > neckline:
                    score += 0.3 * _clip01((close - neckline) / (0.5 * atr))
                return _clip01(score), 1.0

    return 0.0, 0.0


def compute_figures(
    bars: pd.DataFrame, pivots: List[Pivot], atr: pd.Series, cfg: Agent1Config
) -> pd.DataFrame:
    n = len(bars)
    c = bars["close"].to_numpy(float)
    a = atr.to_numpy(float)

    piv = sorted(pivots, key=lambda p: (p.confirmed_at, p.index))
    k = 0
    seen: List[Pivot] = []

    w = np.full(n, np.nan)
    m = np.full(n, np.nan)
    hs = np.full(n, np.nan)
    hs_dir = np.full(n, np.nan)

    for t in range(n):
        while k < len(piv) and piv[k].confirmed_at <= t:
            seen.append(piv[k])
            k += 1
        atr_t = a[t]
        if np.isnan(atr_t) or atr_t <= 0:
            continue
        # From here on "no figure" is an observed 0.0, not missing data.
        tail = seen[-6:]
        w[t] = _double_bottom(tail, c[t], atr_t, t, cfg)
        m[t] = _double_top(tail, c[t], atr_t, t, cfg)
        hs[t], hs_dir[t] = _head_shoulders(tail, c[t], atr_t, cfg)

    return pd.DataFrame(
        {"w_completion": w, "m_completion": m,
         "hs_completion": hs, "hs_direction": hs_dir},
        index=bars.index,
    )
