"""Higher-timeframe context, aligned without leaking.

Resampling is where higher-timeframe features usually go wrong.  A 4h bar
covering 12:00-16:00 must not be visible at 13:00, but a naive
``resample().ffill()`` will hand it to you there, and the resulting backtest
looks superb.

Two guards: an in-progress higher-timeframe bar is dropped entirely, and the
join is a backward ``merge_asof`` on ``close_time``, so a base bar can only
ever see higher-timeframe bars that had already closed.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from core import pandas_rule

from .config import Agent1Config
from .indicators import atr as compute_atr
from .pivots import find_pivots
from .structure import compute_structure

HTF_COLUMNS = ("trend_direction_4h", "position_in_range_4h")


def infer_interval(index: pd.DatetimeIndex) -> pd.Timedelta: # Определить таймфрейм
    if len(index) < 3:
        return pd.Timedelta(0)
    return pd.Timedelta(np.median(np.diff(index.values)))


def resample_bars(bars: pd.DataFrame, rule: str) -> pd.DataFrame: # Return a new DataFrame of bars resampled to the given rule, aligned to the close of each period.
    """Aggregate to ``rule``, keeping only periods that have actually closed.

    ``rule`` is a BINANCE interval ("15m", "4h", "1d"), not a pandas alias.
    The two are not the same: pandas reads "15m" as fifteen MONTH-ENDS, so a
    minute-based higher timeframe collapses all of history into one bucket and
    every HTF column goes flat with nothing raising. `pandas_rule` is the
    translation, and it rejects what it does not recognise rather than letting
    pandas guess.
    """
    rule = pandas_rule(rule)
    interval = infer_interval(bars.index)
    open_time = bars.index - interval
    grouped = (
        bars.assign(_ot=open_time)
        .set_index("_ot")
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna(subset=["open"])
    )
    offset = pd.tseries.frequencies.to_offset(rule)
    grouped.index = grouped.index + offset          # index by HTF close_time
    grouped.index.name = bars.index.name
    # An HTF bar whose close_time is past our last base close is still forming.
    return grouped[grouped.index <= bars.index[-1]]


def compute_htf(bars: pd.DataFrame, cfg: Agent1Config) -> pd.DataFrame:
    htf = resample_bars(bars, cfg.htf_rule)
    need = cfg.atr_period + cfg.pivot_left + cfg.pivot_right + 2
    if len(htf) < need:
        return pd.DataFrame(
            {c: np.full(len(bars), np.nan) for c in HTF_COLUMNS}, index=bars.index
        )

    htf_atr = compute_atr(htf["high"], htf["low"], htf["close"], cfg.atr_period)
    htf_pivots = find_pivots(
        htf["high"], htf["low"], cfg.pivot_left, cfg.pivot_right, cfg.confirm_bars
    )
    struct, _ = compute_structure(htf, htf_pivots, htf_atr, cfg.break_mode)

    src = struct[["trend_direction", "position_in_range"]].rename(
        columns={"trend_direction": "trend_direction_4h",
                 "position_in_range": "position_in_range_4h"}
    )
    src = src.reset_index().rename(columns={src.index.name or "index": "htf_close"})
    src.columns = ["htf_close"] + list(HTF_COLUMNS)

    base = pd.DataFrame({"base_close": bars.index})
    merged = pd.merge_asof(
        base, src, left_on="base_close", right_on="htf_close", direction="backward"
    )
    out = merged[list(HTF_COLUMNS)]
    out.index = bars.index
    return out
