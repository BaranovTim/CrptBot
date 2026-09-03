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
         "avg_volume", "current_volume", "rel_volume", "beta", "bars",
         "gap_pct", "change_from_open", "atr_pct",
         "volatility_w", "volatility_m",
         "at_20d_high", "at_20d_low", "at_52w_high", "at_52w_low",
         "off_52w_high", "off_52w_low", "at_all_time_high",
         "perf_week", "perf_month", "perf_quarter", "perf_half",
         "perf_year", "perf_ytd")
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

    _extended(bars, out)
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


def _extended(bars: pd.DataFrame, out: Dict[str, Optional[float]]) -> None:
    """The rest of the Finviz-shaped price fields, computed in place.

    EVERY ONE OF THESE IS None WHEN THE HISTORY IS TOO SHORT.
    A stock listed three months ago has no one-year performance, and the
    tempting default — 0.0 — would put it in the middle of every performance
    screen instead of out of one.
    """
    close = bars["close"].astype(float).dropna()
    if close.empty:
        return
    price = float(close.iloc[-1])

    # Gap: today's open against yesterday's close. Meaningless for crypto,
    # which is why it never existed here; for equities it is one of the most
    # watched numbers of the session, because the market was shut in between.
    if "open" in bars and len(close) >= 2:
        try:
            op = float(bars["open"].astype(float).iloc[-1])
            prev = float(close.iloc[-2])
            if prev:
                out["gap_pct"] = (op / prev - 1.0) * 100.0
            if op:
                out["change_from_open"] = (price / op - 1.0) * 100.0
        except (TypeError, ValueError):
            pass

    if {"high", "low"} <= set(bars.columns) and len(bars) >= 15:
        try:
            from agent1.indicators import atr as _atr

            a = _atr(bars["high"].astype(float), bars["low"].astype(float),
                     close, 14).dropna()
            # As a PERCENT of price, never in dollars: a $3 range means
            # something completely different on a $10 stock and a $900 one.
            if not a.empty and price:
                out["atr_pct"] = float(a.iloc[-1]) / price * 100.0
        except Exception:
            pass

    # Standard deviation of daily returns, in percent. NOT annualised —
    # Finviz's is not either, and annualising silently would make every
    # threshold anyone types wrong by about sixteen times.
    rets = close.pct_change().dropna()
    for key, n in (("volatility_w", 5), ("volatility_m", 21)):
        if len(rets) >= n:
            out[key] = float(rets.tail(n).std() * 100.0)

    def extremes(window, hi_key, lo_key):
        if len(close) < window:
            return None, None
        hi, lo = float(close.tail(window).max()), float(close.tail(window).min())
        out[hi_key] = 1.0 if price >= hi else 0.0
        out[lo_key] = 1.0 if price <= lo else 0.0
        return hi, lo

    extremes(20, "at_20d_high", "at_20d_low")
    hi52, lo52 = extremes(252, "at_52w_high", "at_52w_low")
    if hi52 and hi52 > 0:
        # Positive means "below the high", which is how it reads out loud.
        out["off_52w_high"] = (hi52 - price) / hi52 * 100.0
    if lo52 and lo52 > 0:
        out["off_52w_low"] = (price - lo52) / lo52 * 100.0

    # "All time" within the history held — about ten years, not since listing.
    # Said in the field help rather than implied by the name.
    out["at_all_time_high"] = 1.0 if price >= float(close.max()) else 0.0

    for key, n in (("perf_week", 5), ("perf_month", 21),
                   ("perf_quarter", 63), ("perf_half", 126),
                   ("perf_year", 252)):
        if len(close) > n:
            base = float(close.iloc[-(n + 1)])
            if base:
                out[key] = (price / base - 1.0) * 100.0

    # Year to date needs the CALENDAR, not a bar count: 63 trading days is a
    # quarter in March and nothing like year-to-date in November.
    try:
        year = close.index[-1].year
        ytd = close[close.index >= f"{year}-01-01"]
        if len(ytd) >= 2 and float(ytd.iloc[0]):
            out["perf_ytd"] = (price / float(ytd.iloc[0]) - 1.0) * 100.0
    except Exception:
        pass
