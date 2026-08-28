"""Higher-timeframe indicators, aligned without leaking.

This duplicates a dozen lines of resampling logic that also exist in
``agent1/htf.py``, and that is deliberate.  The ablation plan deletes one
agent block at a time to measure what each contributes; an agent that stops
importing cleanly when its neighbour is removed cannot be ablated.  Agent
independence is a design requirement here, not tidiness, and a shared
twelve-line helper is not worth coupling them.

The trap being avoided: ``resample().ffill()`` will happily hand you the 4h
bar covering 12:00-16:00 while standing at 13:00.  Guards are (a) drop any
higher-timeframe bar that has not closed, and (b) join with a backward
``merge_asof`` on close_time, so a base bar can only see bars that had
already finished.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import pandas_rule

from .config import Agent2Config
from .indicators import atr as compute_atr
from .indicators import macd, rsi, safe_div

HTF_COLUMNS = ("rsi_14_4h", "macd_hist_atr_4h")


def infer_interval(index: pd.DatetimeIndex) -> pd.Timedelta:
    if len(index) < 3:
        return pd.Timedelta(0)
    return pd.Timedelta(np.median(np.diff(index.values)))


def resample_bars(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
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
    grouped = (
        bars.assign(_ot=bars.index - interval)
        .set_index("_ot")
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna(subset=["open"])
    )
    grouped.index = grouped.index + pd.tseries.frequencies.to_offset(rule)
    grouped.index.name = bars.index.name
    return grouped[grouped.index <= bars.index[-1]]


def compute_htf(bars: pd.DataFrame, cfg: Agent2Config) -> pd.DataFrame:
    htf = resample_bars(bars, cfg.htf_rule)
    need = max(cfg.ema_slow + cfg.macd_signal, cfg.rsi_period, cfg.atr_period) + 5
    if len(htf) < need:
        return pd.DataFrame(
            {c: np.full(len(bars), np.nan) for c in HTF_COLUMNS}, index=bars.index
        )

    htf_atr = compute_atr(htf["high"], htf["low"], htf["close"], cfg.atr_period)
    _, _, hist = macd(htf["close"], cfg.ema_fast, cfg.ema_slow, cfg.macd_signal)

    src = pd.DataFrame(
        {
            "htf_close": htf.index,
            "rsi_14_4h": rsi(htf["close"], cfg.rsi_period).to_numpy(),
            "macd_hist_atr_4h": safe_div(hist, htf_atr).to_numpy(),
        }
    ).reset_index(drop=True)

    merged = pd.merge_asof(
        pd.DataFrame({"base_close": bars.index}),
        src,
        left_on="base_close", right_on="htf_close", direction="backward",
    )
    out = merged[list(HTF_COLUMNS)]
    out.index = bars.index
    return out
