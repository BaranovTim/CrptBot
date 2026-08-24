"""Closed-form indicator math. Nothing here is learned.

Every function is causal: the value at bar i depends only on bars <= i.  In
practice that means rolling, shift, ewm and expanding, and nothing else.  No
centred windows, no interpolation across gaps, no ``.mean()`` over a full
column.

Two implementation notes worth knowing:

  * Wilder's smoothing is done with ``ewm(alpha=1/n, adjust=False)``, which
    seeds from the first observation rather than from an n-bar SMA the way
    TA-Lib does.  The two converge exponentially and are indistinguishable
    after a few multiples of n; with a 200-bar warmup this never matters.
    It is mentioned because a value that differs from TradingView in the
    first few bars is expected, not a bug.

  * Every division is guarded.  An unguarded one produces ``inf`` on a flat
    window (zero ATR, zero band width, zero standard deviation), and ``inf``
    is not a big number to a decision tree — it is a wrecked split.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def safe_div(numer, denom, eps: float = 1e-12):
    """Elementwise divide; anything undefined becomes NaN rather than inf."""
    n = pd.Series(numer) if not isinstance(numer, pd.Series) else numer
    d = pd.Series(denom) if not isinstance(denom, pd.Series) else denom
    out = n / d.where(d.abs() > eps)
    return out.replace([np.inf, -np.inf], np.nan)


# --- smoothing ------------------------------------------------------------
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing — the one RSI, ATR and ADX are defined with."""
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rolling_z(s: pd.Series, n: int) -> pd.Series:
    """Standardise against a ROLLING window.

    Never against the whole column.  Full-sample mean and standard deviation
    would put information from the end of your history into every row at the
    beginning of it.
    """
    mu = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return safe_div(s - mu, sd)


# --- volatility -----------------------------------------------------------
def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(h, l, c), n)


def bollinger(c: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(c, n)
    sd = c.rolling(n, min_periods=n).std(ddof=0)
    return mid + k * sd, mid, mid - k * sd


def keltner(h, l, c, n: int = 20, k: float = 2.0):
    mid = ema(c, n)
    a = atr(h, l, c, n)
    return mid + k * a, mid, mid - k * a


# --- trend ----------------------------------------------------------------
def macd(c: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(c, fast) - ema(c, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def adx(h, l, c, n: int = 14):
    """Returns (ADX, +DI, -DI).

    ADX measures how trending the market is, and says nothing about which
    way — hence the separate DI spread for direction.
    """
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)

    atr_n = rma(true_range(h, l, c), n)
    plus_di = 100.0 * safe_div(rma(plus_dm, n), atr_n)
    minus_di = 100.0 * safe_div(rma(minus_dm, n), atr_n)
    dx = 100.0 * safe_div((plus_di - minus_di).abs(), plus_di + minus_di)
    return rma(dx, n), plus_di, minus_di


# --- momentum -------------------------------------------------------------
def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma(gain, n)
    avg_loss = rma(loss, n)
    out = 100.0 - 100.0 / (1.0 + safe_div(avg_gain, avg_loss))
    # An unbroken run of up bars gives zero average loss: RSI is 100, not NaN.
    out = out.where(~((avg_loss.abs() <= 1e-12) & avg_gain.notna()), 100.0)
    out = out.where(~((avg_gain.abs() <= 1e-12) & (avg_loss > 1e-12)), 0.0)
    return out


def stochastic(h, l, c, k: int = 14, d: int = 3):
    ll = l.rolling(k, min_periods=k).min()
    hh = h.rolling(k, min_periods=k).max()
    k_line = 100.0 * safe_div(c - ll, hh - ll)
    return k_line, k_line.rolling(d, min_periods=d).mean()


def cci(h, l, c, n: int = 20) -> pd.Series:
    tp = (h + l + c) / 3.0
    ma = sma(tp, n)
    md = (tp - ma).abs().rolling(n, min_periods=n).mean()
    return safe_div(tp - ma, 0.015 * md)


# --- volume ---------------------------------------------------------------
def mfi(h, l, c, v, n: int = 14) -> pd.Series:
    tp = (h + l + c) / 3.0
    flow = tp * v
    delta = tp.diff()
    pos = flow.where(delta > 0, 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(delta < 0, 0.0).rolling(n, min_periods=n).sum()
    out = 100.0 - 100.0 / (1.0 + safe_div(pos, neg))
    return out.where(~((neg.abs() <= 1e-12) & pos.notna()), 100.0)


def cmf(h, l, c, v, n: int = 20) -> pd.Series:
    mult = safe_div((c - l) - (h - c), h - l)
    mfv = mult * v
    return safe_div(
        mfv.rolling(n, min_periods=n).sum(), v.rolling(n, min_periods=n).sum()
    )


def obv(c: pd.Series, v: pd.Series) -> pd.Series:
    """On-balance volume.

    A cumulative sum, so it is wildly non-stationary and must never be fed to
    a model raw — only its rolling-standardised rate of change.
    """
    return (np.sign(c.diff()).fillna(0.0) * v).cumsum()


# --- event recency --------------------------------------------------------
def bars_since_sign_change(s: pd.Series):
    """(bars since the series last changed sign, direction of that change).

    The recency rule from Agent 1, applied to indicator crosses: how long ago
    MACD crossed its signal line is often more informative than which side of
    it we are on now.  ``ffill`` only ever propagates forward, so this stays
    causal.
    """
    sign = np.sign(s)
    sign = sign.where(sign != 0.0)              # sitting exactly on zero is not a cross
    prev = sign.ffill().shift(1)
    crossed = sign.notna() & prev.notna() & (sign != prev)

    pos = pd.Series(np.arange(len(s), dtype=float), index=s.index)
    cross_pos = pos.where(crossed).ffill()
    direction = sign.where(crossed).ffill()
    return pos - cross_pos, direction
