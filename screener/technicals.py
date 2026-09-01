"""The screener fields that come from price and volume alone.

WHY THESE REUSE agent2.indicators RATHER THAN REIMPLEMENTING
    RSI has more than one definition in the wild — Wilder's smoothing, a
    simple moving average, an EMA — and they disagree by several points near
    the 30 line, which is exactly where the "oversold bounce" preset makes its
    decision. If the screener computed RSI one way and the model's features
    computed it another, a stock could be oversold on the screener and not
    oversold to the model, in the same app, on the same bar. So there is one
    definition and both callers import it.

WHAT "TODAY" MEANS HERE
    The last CLOSED daily bar, never a forming one. Alpaca's free tier already
    forces a 15-minute lag, but that is a coincidence of the plan rather than
    a guarantee, and a partial session's volume compared against a full
    session's average would make every stock look quiet in the morning and
    normal by the close.

RELATIVE VOLUME IS A RATIO
    Today's volume divided by the average of the previous N sessions. Finviz
    writes its thresholds as `Over 2`, meaning twice normal. A share count in
    that field cannot mean anything, which is why the preset that arrived with
    `Over 100K` is implemented as a ratio and flagged rather than guessed at.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from agent2.indicators import rsi, sma

log = logging.getLogger(__name__)

# Enough history for SMA200 plus a year of beta, with room for holidays.
MIN_BARS = 260


def technicals(bars: pd.DataFrame,
               benchmark: Optional[pd.Series] = None,
               avg_volume_days: int = 50) -> Dict[str, Optional[float]]:
    """Every price-derived screener field for one symbol.

    `bars` is daily OHLCV, oldest first, split and dividend adjusted.
    `benchmark` is the index close series beta is measured against.

    A field that cannot be computed comes back None rather than 0.0. The
    distinction is the whole point: a stock with 40 bars of history has no
    SMA200, and reporting that as zero would put it above every price in the
    market and into the results of every breakout screen.
    """
    out: Dict[str, Optional[float]] = {
        k: None for k in
        ("price", "change_pct", "sma20", "sma50", "sma200",
         "above_sma20", "above_sma50", "above_sma200", "rsi14",
         "high_50d", "low_50d", "at_50d_high", "at_50d_low",
         "avg_volume", "current_volume", "rel_volume", "beta", "bars")
    }
    if bars is None or bars.empty or "close" not in bars:
        return out

    close = pd.to_numeric(bars["close"], errors="coerce").dropna()
    if close.empty:
        return out
    volume = pd.to_numeric(bars.get("volume"), errors="coerce")

    out["bars"] = float(len(close))
    out["price"] = float(close.iloc[-1])

    if len(close) >= 2:
        prev = float(close.iloc[-2])
        if prev:
            out["change_pct"] = (float(close.iloc[-1]) / prev - 1.0) * 100.0

    for n in (20, 50, 200):
        if len(close) >= n:
            v = float(sma(close, n).iloc[-1])
            out[f"sma{n}"] = v
            # The comparison, not just the level: every preset asks "is price
            # above it", and doing that subtraction at each call site is how
            # one of them ends up with the inequality backwards.
            out[f"above_sma{n}"] = 1.0 if out["price"] > v else 0.0

    if len(close) >= 15:
        r = rsi(close, 14).iloc[-1]
        out["rsi14"] = None if pd.isna(r) else float(r)

    if len(close) >= 50:
        window = close.iloc[-50:]
        hi, lo = float(window.max()), float(window.min())
        out["high_50d"], out["low_50d"] = hi, lo
        # "New High" in Finviz means the latest bar set it, not merely that
        # price is near it. Exact equality on floats read from JSON is safe
        # here because both sides are the same stored value.
        out["at_50d_high"] = 1.0 if out["price"] >= hi else 0.0
        out["at_50d_low"] = 1.0 if out["price"] <= lo else 0.0

    if volume is not None and volume.notna().any():
        vol = volume.dropna()
        out["current_volume"] = float(vol.iloc[-1])
        if len(vol) > avg_volume_days:
            # EXCLUDES today. Including it makes a stock its own baseline, so
            # a genuine 5x day reports about 4.2x and the bigger the spike the
            # more it is understated — the error is worst exactly when the
            # field matters.
            base = float(vol.iloc[-(avg_volume_days + 1):-1].mean())
            out["avg_volume"] = base
            if base > 0:
                out["rel_volume"] = float(vol.iloc[-1]) / base

    if benchmark is not None and len(close) >= 60:
        out["beta"] = _beta(close, benchmark)

    return out


def _beta(close: pd.Series, benchmark: pd.Series,
          lookback: int = 252) -> Optional[float]:
    """Covariance of daily returns with the benchmark, over its variance.

    Aligned on the index and not merely truncated to the same length: a stock
    that was halted for a day would otherwise have every subsequent return
    compared against the wrong session, which produces a beta near zero for a
    stock that simply missed a day.
    """
    a = close.pct_change().dropna()
    b = pd.to_numeric(benchmark, errors="coerce").pct_change().dropna()
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(joined) < 60:
        return None
    joined = joined.iloc[-lookback:]
    var = float(joined.iloc[:, 1].var())
    if var <= 0:
        return None
    cov = float(joined.iloc[:, 0].cov(joined.iloc[:, 1]))
    return cov / var
