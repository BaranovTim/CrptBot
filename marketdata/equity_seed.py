"""Alpaca stock bars into the BarStore the training pipeline already reads.

WHY A BRIDGE AND NOT A SECOND PIPELINE
    `train.py` fits models from a `BarStore` of closed OHLCV bars indexed by
    CLOSE time. Nothing in it is crypto-specific — the labelling, the purged
    folds, the calibration and the shuffle test all take a price series. What
    is crypto-specific is where the bars come from.

    So this writes equity bars into that same store, in the same shape, and
    the whole apparatus works unchanged. A parallel equities pipeline would
    have been a second copy of every subtle thing this one gets right.

THE ONE CONVERSION THAT MATTERS: OPEN TIME TO CLOSE TIME
    Alpaca stamps a bar with the START of its period. This repo indexes by the
    END, and the reason is written at the top of `binance.py`: a bar labelled
    12:00 covers 12:00-12:59:59 and its close is unknowable until 13:00. Index
    it by open time and you have handed the model an hour of free foresight.

    So every timestamp is shifted forward by one bar width here, once, rather
    than in each of the places that would otherwise have to remember.

WHAT THIS DOES NOT FIX, AND CANNOT
    Equities gap. A stop between Friday's close and Monday's open does not
    fill at the stop — it fills at the gap. The triple-barrier labelling
    assumes a barrier is touched at its level, which is true intrabar for a
    24/7 market and false across an overnight halt. Models fitted this way
    will read slightly better than they would trade, and no amount of care
    here changes that. It is recorded on the model card rather than hidden.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pandas as pd

log = logging.getLogger(__name__)

# Repo interval -> Alpaca timeframe, and the bar's width.
INTERVALS: Dict[str, tuple] = {
    "1m": ("1Min", timedelta(minutes=1)),
    "5m": ("5Min", timedelta(minutes=5)),
    "15m": ("15Min", timedelta(minutes=15)),
    "1h": ("1Hour", timedelta(hours=1)),
    "4h": ("1Hour", timedelta(hours=1)),      # resampled below; Alpaca has no 4h
    "1d": ("1Day", timedelta(days=1)),
    "1w": ("1Week", timedelta(weeks=1)),
}

# How far back to seed, per interval. Intraday history is enormous and the
# model's window is bars, not years: 20,000 one-minute bars is about two
# months of market hours and is already more than the fit needs.
LOOKBACK_DAYS: Dict[str, int] = {
    "1m": 60, "5m": 180, "15m": 365, "1h": 730, "4h": 1095,
    "1d": 3650, "1w": 3650,
}


def seed_with_context(symbol: str, interval: str) -> int:
    """Seed an interval AND the higher timeframe its model needs.

    THE BUG THIS EXISTS TO PREVENT
        Agent 2 reads a higher timeframe alongside the one being fitted — 1d
        is trained with 1w context. Seeding only the requested interval left
        `BarStore(AAPL, 1w)` empty, and training then exited after printing
        the bar count, with no model, no error and exit status 0. Fifty
        minutes of CPU and nothing to show, twice, before anyone looked at
        what `htf_for` wanted.
    """
    from core.timeframes import htf_for

    total = seed_equity(symbol, interval)
    htf = htf_for(interval)
    if htf and htf in INTERVALS:
        total += seed_equity(symbol, htf)
    elif htf:
        log.warning("no Alpaca timeframe for %s, the %s model will have no "
                    "higher-timeframe context", htf, interval)
    return total


def seed_equity(symbol: str, interval: str,
                store=None, client=None) -> int:
    """Download and store closed bars. Returns how many were new."""
    from livefeed import BarStore
    from marketdata.alpaca import Alpaca, AlpacaError

    if interval not in INTERVALS:
        raise ValueError(f"unsupported interval {interval!r}")

    tf, width = INTERVALS[interval]
    client = client or Alpaca()
    if not client.configured:
        raise AlpacaError("no Alpaca credentials; cannot seed equity bars")

    start = datetime.now(timezone.utc) - timedelta(
        days=LOOKBACK_DAYS.get(interval, 365))
    got = client.bars([symbol], timeframe=tf, start=start)
    df = got.get(symbol.upper())
    if df is None or df.empty:
        log.warning("equity seed %s %s: no bars", symbol, interval)
        return 0

    if interval == "4h":
        # Alpaca has no four-hour bar, so it is built from hours. `label` and
        # `closed` both left, then shifted like everything else — resampling
        # with a right label would double-shift and put every bar four hours
        # into the future.
        df = df.resample("4h", label="left", closed="left").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"}).dropna(subset=["close"])

    # open time -> close time. See the module docstring.
    df = df.copy()
    df.index = df.index + width
    df.index.name = "close_time"
    df = _as_kline(df)

    store = store or BarStore(symbol.upper(), interval)
    written = store.append(df)
    log.info("equity seed %s %s: %d bars, %d new",
             symbol, interval, len(df), written)
    return written


def _as_kline(df: pd.DataFrame) -> pd.DataFrame:
    """Alpaca's columns in the shape the BarStore expects.

    TWO FIELDS ARE FILLED IN, TWO ARE LEFT EMPTY, AND THE DIFFERENCE IS THE
    POINT.

      quote_volume      real: volume x VWAP is the dollar value traded, which
                        is exactly what Binance's quote_volume means.
      number_of_trades  real: Alpaca reports the trade count per bar.

      taker_buy_base    LEFT AS NaN. These are the buy side of Binance's
      taker_buy_quote   aggressor split, and US equity bars carry no such
                        split — the SIP does not publish who crossed the
                        spread. Filling them with zero would tell the model
                        that every trade in every stock was seller-initiated,
                        which is a strong and completely false signal. NaN
                        says "unknown", which is what it is, and Agent 4's
                        coverage columns already know how to report that.
    """
    out = df.copy()
    vwap = out["vwap"] if "vwap" in out else out["close"]
    out["quote_volume"] = out["volume"] * vwap
    out["number_of_trades"] = out["trades"] if "trades" in out else float("nan")
    out["taker_buy_base_volume"] = float("nan")
    out["taker_buy_quote_volume"] = float("nan")
    out["ignore"] = 0
    return out
