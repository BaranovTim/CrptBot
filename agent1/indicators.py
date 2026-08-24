"""Causal building blocks.

Only backward-looking operations live here: rolling, shift, ewm, expanding.
No centred windows, no interpolation over gaps, nothing that peeks forward.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    # true range = the biggest of three distances:
    #   this bar's own height,
    #   the jump up from yesterday's close,
    #   the jump down from yesterday's close.
    # the last two are what catch overnight gaps that the bar height misses
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Wilder's ATR (RMA of true range).

    ``ewm(adjust=False)`` is a forward recursion from bar 0, so the value at
    bar i depends only on bars <= i.  That is what makes it safe to compute
    over the whole series at once instead of in a loop.
    """
    tr = true_range(high, low, close)
    # a running average that leans on the previous value: new = old*(1-a) + now*a.
    # adjust=False keeps it a plain forward recursion, so bar i only ever sees
    # bars 0..i. min_periods hides the first `period` bars while it settles
    out = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return out.rename("atr")


def safe_div(numer, denom):
    """Divide, returning NaN instead of inf where the denominator is ~0.

    Every distance in Agent 1 is normalised by ATR.  A zero ATR (a dead,
    perfectly flat window) must produce NaN, not a huge number that the model
    will happily treat as a giant signal.
    """
    denom = np.asarray(denom, dtype=float)
    numer = np.asarray(numer, dtype=float)
    # errstate just silences numpy's warning - the np.where below is what
    # actually handles it: divide only where the bottom is safely non-zero,
    # otherwise write NaN. never let inf through, because a tree reads inf as
    # "enormous signal" rather than "undefined"
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(np.abs(denom) > 1e-12, numer / denom, np.nan)
    return out


def rolling_max_forward(s: pd.Series, window: int) -> pd.Series:
    """max(s[i+1 : i+1+window]) — a FORWARD window.

    This is the one deliberately non-causal helper in the codebase, and it
    exists for exactly one caller: pivot detection.  A pivot is *located* by
    looking forward, but is then stamped with a ``confirmed_at`` bar so that
    no consumer is ever allowed to use it early.  Do not call this anywhere
    else.
    """
    # trick: flip the series, take a normal backward-looking window, flip back.
    # a backward window on reversed data is a forward window on real data
    rev = s.iloc[::-1]
    out = rev.rolling(window, min_periods=window).max().shift(1)
    return out.iloc[::-1]


def rolling_min_forward(s: pd.Series, window: int) -> pd.Series:
    """min(s[i+1 : i+1+window]) — see ``rolling_max_forward``."""
    # same flip trick as rolling_max_forward, for lows
    rev = s.iloc[::-1]
    out = rev.rolling(window, min_periods=window).min().shift(1)
    return out.iloc[::-1]
