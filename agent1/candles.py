"""Candlestick patterns, aggregated to two counts.

Low expected value, near-zero cost — which is the only reason they are here.

Two deliberate choices.  First, native vectorised patterns rather than a
TA-Lib dependency: ``pandas_ta`` is effectively unmaintained and most of its
CDL patterns need a C library that is painful to install.  Set
``use_pandas_ta_classic=True`` to switch to that fork's 62 patterns if you
install it.  Second, the output is two counts, not 62 columns.  Sixty-two
sparse booleans on a few thousand effective samples is a gift to overfitting.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Agent1Config

CANDLE_COLUMNS = ("cdl_bull_count_3", "cdl_bear_count_3")


def _native_signals(bars: pd.DataFrame):
    o = bars["open"].astype(float)
    h = bars["high"].astype(float)
    l = bars["low"].astype(float)
    c = bars["close"].astype(float)

    body = (c - o).abs()
    rng = (h - l).replace(0.0, np.nan)
    upper = h - c.combine(o, max)
    lower = c.combine(o, min) - l
    bull_bar = c > o
    bear_bar = c < o

    o1, c1, h1, l1 = o.shift(1), c.shift(1), h.shift(1), l.shift(1)
    body1 = (c1 - o1).abs()
    o2, c2 = o.shift(2), c.shift(2)
    body2 = (c2 - o2).abs()
    mid2 = (o2 + c2) / 2.0
    avg_body = body.rolling(14, min_periods=14).mean()

    bull = {
        "engulfing": (c1 < o1) & bull_bar & (c >= o1) & (o <= c1),
        "hammer": (lower >= 2 * body) & (upper <= body) & (body > 0),
        "piercing": (c1 < o1) & bull_bar & (o < c1) & (c > (o1 + c1) / 2) & (c < o1),
        "morning_star": (c2 < o2) & (body2 > avg_body) & (body1 < body2 * 0.5)
                        & bull_bar & (c > mid2),
        "marubozu": bull_bar & (body >= 0.9 * rng),
    }
    bear = {
        "engulfing": (c1 > o1) & bear_bar & (c <= o1) & (o >= c1),
        "shooting_star": (upper >= 2 * body) & (lower <= body) & (body > 0),
        "dark_cloud": (c1 > o1) & bear_bar & (o > c1) & (c < (o1 + c1) / 2) & (c > o1),
        "evening_star": (c2 > o2) & (body2 > avg_body) & (body1 < body2 * 0.5)
                        & bear_bar & (c < mid2),
        "marubozu": bear_bar & (body >= 0.9 * rng),
    }
    bull_hits = sum(s.fillna(False).astype(int) for s in bull.values())
    bear_hits = sum(s.fillna(False).astype(int) for s in bear.values())
    return bull_hits, bear_hits


def _pandas_ta_classic_signals(bars: pd.DataFrame):
    import pandas_ta_classic as ta  # noqa: F401  (optional dependency)

    df = bars[["open", "high", "low", "close"]].copy()
    res = df.ta.cdl_pattern(name="all")
    # The library emits +100 / -100 / 0 per pattern.
    bull_hits = (res > 0).sum(axis=1)
    bear_hits = (res < 0).sum(axis=1)
    return bull_hits, bear_hits


def compute_candles(bars: pd.DataFrame, cfg: Agent1Config) -> pd.DataFrame:
    if cfg.use_pandas_ta_classic:
        try:
            bull_hits, bear_hits = _pandas_ta_classic_signals(bars)
        except ImportError:
            bull_hits, bear_hits = _native_signals(bars)
    else:
        bull_hits, bear_hits = _native_signals(bars)

    w = cfg.candle_window
    out = pd.DataFrame(
        {
            "cdl_bull_count_3": bull_hits.rolling(w, min_periods=w).sum(),
            "cdl_bear_count_3": bear_hits.rolling(w, min_periods=w).sum(),
        },
        index=bars.index,
    )
    return out.astype(float)
