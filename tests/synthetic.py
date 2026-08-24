"""Deterministic fake bars, so tests never depend on the network."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_bars(n: int = 1200, seed: int = 7, interval: str = "1h",
              start: str = "2024-01-01") -> pd.DataFrame:
    """A random walk with realistic-looking wicks and the full kline schema.

    Not meant to be market-like — meant to be reproducible and to exercise
    every branch (pivots, breaks, sweeps, gaps).
    """
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(interval)

    ret = rng.normal(0, 0.004, n) + 0.02 * np.sin(np.arange(n) / 40.0) * 0.01
    close = 40000 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.003, n)) * close
    high = np.maximum(open_, close) + spread * rng.random(n)
    low = np.minimum(open_, close) - spread * rng.random(n)

    volume = np.abs(rng.normal(120, 40, n)) + 1.0
    vwap = (high + low + close) / 3.0
    trades = np.maximum(1, rng.poisson(900, n))

    open_time = pd.date_range(start, periods=n, freq=interval, tz="UTC")
    close_time = open_time + step - pd.Timedelta(milliseconds=1)

    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume,
            "quote_volume": volume * vwap,
            "number_of_trades": trades,
            "taker_buy_base_volume": volume * rng.uniform(0.35, 0.65, n),
            "taker_buy_quote_volume": volume * vwap * rng.uniform(0.35, 0.65, n),
        },
        index=close_time,
    ).rename_axis("close_time")
