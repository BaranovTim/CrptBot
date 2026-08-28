"""One vocabulary for timeframes, and one place the `m` trap is handled.

THE TRAP
--------
Binance writes fifteen minutes as ``15m``. pandas reads ``15m`` as FIFTEEN
MONTH-ENDS:

    200 one-minute bars resampled by '15m'   ->  1 group
    200 one-minute bars resampled by '15min' -> 14 groups

Lowercase ``m`` is months, ``min`` is minutes. Nothing raises. The existing
``htf_rule="4h"`` worked only because ``h`` happens to mean the same thing in
both notations — the moment anyone wrote a minute-based higher timeframe, the
whole HTF block would have silently become a single all-history bucket and
every ``*_htf`` column would have gone flat. The features would still compute,
the tests would still pass, and the model would quietly learn nothing.

So: every interval in this project is written in BINANCE notation, and any
code handing one to pandas goes through `pandas_rule` first.

THE LADDER
----------
`HTF_FOR` is the higher timeframe used for context features. It has to scale
with the base — a fixed 4h is four bars up from 1h and two hundred and forty
bars up from 1m, and at that distance it barely changes between samples. One
rung up, roughly 4-16x, is what a human means by "check the higher timeframe".
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# The intervals the app offers, fastest first.
TIMEFRAMES: List[str] = ["1m", "5m", "15m", "1h", "4h", "1d"]

# Binance notation -> pandas offset alias.
_PANDAS_RULE: Dict[str, str] = {
    "1s": "1s",
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
    "1d": "1D", "3d": "3D", "1w": "1W",
}

_SECONDS: Dict[str, int] = {
    "1s": 1, "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
}

# One rung up. Kept between roughly 4x and 16x: close enough that it moves
# while you watch it, far enough that it says something the base does not.
HTF_FOR: Dict[str, str] = {
    "1m": "15m",
    "5m": "1h",
    "15m": "4h",
    "1h": "4h",      # unchanged: the frozen 1h models were fitted with this
    "4h": "1d",
    "1d": "1w",
}

# How far back to train each timeframe. Fast intervals produce bars far
# quicker, so a shorter window still yields more samples than a long window
# does on a slow one — and 1m over three years is 1.5M bars, which Agent 1
# would spend ten minutes on for no extra signal.
HISTORY_START: Dict[str, str] = {
    "1m": "2026-05-01",
    "5m": "2025-08-01",
    "15m": "2024-08-01",
    "1h": "2023-01-01",
    "4h": "2021-01-01",
    "1d": "2019-01-01",
}


# Barrier geometry per timeframe: (k ATR each side, bars to hold).
#
# THE DEFAULT +1/-1 ATR OVER 1-2 BARS IS A SCALPING QUESTION.
# At fast timeframes fees kill it before accuracy is even discussed: one ATR
# of a 1m BTCUSDT bar is ~0.046% of price, so a +1/-1 barrier pair spans
# ~0.092% — while a taker round trip costs 0.100%. The target is smaller than
# the fee. No model can win that.
#
# Day trading does not mean predicting the next minute. It means using fast
# bars for RESOLUTION while holding for tens of minutes to hours. So the
# barriers widen as the bars shrink:
#
#   k     chosen so the TP-to-SL span (2*k*atr%) clears about 1%, which puts
#         a 0.1% round trip near 10% of the span instead of over 100%
#   hold  about 2*k^2 bars, because a random walk covers k ATR in roughly
#         k^2 bars — enough time for the target to be reachable rather than
#         a barrier that only the vertical (time-out) ever hits
#
# The result is a ~2-4 hour horizon on every fast timeframe, which is what
# day trading actually is. 1h/4h/1d keep +1/-1: their spans already clear
# costs comfortably, and 1h's frozen models were fitted that way.
BARRIERS: Dict[str, Tuple[float, int]] = {
    "1m": (11.0, 240),      # span ~1.01%, hold 4h
    "5m": (4.0, 32),        # span ~1.11%, hold 2h40
    "15m": (2.0, 8),        # span ~1.11%, hold 2h
    "1h": (1.0, 2),         # span ~1.28%, hold 2h
    "4h": (1.0, 2),         # span ~3.06%, hold 8h
    "1d": (1.0, 2),         # span ~8.27%, hold 2d
}


def barriers_for(interval: str) -> Tuple[float, int, int]:
    """(k ATR each side, h1 hold in bars, h2 hold in bars).

    h1 is the half-way horizon the monitor reads once a window is partly
    spent — the same "different question with less time left" split that has
    always separated h1 from h2, generalised past 1-and-2 bars.
    """
    k, h2 = BARRIERS.get(interval, (1.0, 2))
    return k, max(1, h2 // 2), h2


def pandas_rule(interval: str) -> str:
    """Binance interval -> pandas offset alias. Raises on anything unknown.

    Never pass a Binance interval straight to `resample`. See the module
    docstring for what that costs.
    """
    try:
        return _PANDAS_RULE[interval]
    except KeyError:
        raise ValueError(
            f"unknown interval {interval!r}; expected one of "
            f"{sorted(_PANDAS_RULE)}") from None


def interval_seconds(interval: str) -> int:
    try:
        return _SECONDS[interval]
    except KeyError:
        raise ValueError(f"unknown interval {interval!r}") from None


def interval_delta(interval: str) -> pd.Timedelta:
    return pd.Timedelta(seconds=interval_seconds(interval))


def htf_for(interval: str) -> str:
    """The higher timeframe used for context features on this base."""
    return HTF_FOR.get(interval, "4h")


def history_start(interval: str) -> str:
    return HISTORY_START.get(interval, "2023-01-01")


def bars_per_day(interval: str) -> float:
    return 86400.0 / interval_seconds(interval)


def model_paths(symbol: str, interval: str,
                output_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """Where the frozen h1/h2 models for this pair and timeframe live.

    BTCUSDT 1h keeps the original unqualified names, because those files
    already exist and are already referenced by `monitor.py` and every command
    in the README. Renaming them to be tidy would break a working setup for
    nothing.
    """
    from config import OUTPUT_DIR

    out = Path(output_dir or OUTPUT_DIR)
    if (symbol.upper(), interval) == ("BTCUSDT", "1h"):
        return out / "judge_h1.joblib", out / "judge_h2.joblib"
    stem = f"judge_{symbol.upper()}_{interval}"
    return out / f"{stem}_h1.joblib", out / f"{stem}_h2.joblib"


def is_trained(symbol: str, interval: str,
               output_dir: Optional[Path] = None) -> bool:
    h1, h2 = model_paths(symbol, interval, output_dir)
    return h1.exists() and h2.exists()
