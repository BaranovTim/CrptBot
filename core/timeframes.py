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
# THE TRADING TIMEFRAMES. 1m and 5m were removed as timeframes the app
# offers: their fits sat at chance on every coin and the fee arithmetic
# needed p > 0.6 to break even on a 1% span. 1m BARS are still collected --
# `price_range` reads them to see whether a level was touched, and the
# collector's health check watches them -- so the lookup tables below keep
# their 1m and 5m rows. Only this list decides what is offered and trained.
TIMEFRAMES: List[str] = ["15m", "1h", "4h", "1d"]

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
    "15m": (2.0, 8),        # span ~1.11%, hold 2h
    "1h": (1.0, 2),         # span ~1.28%, hold 2h
    # SIXTEEN BARS, ON THE CHART'S LEVELS. 4h is the structure timeframe
    # (see GEOMETRY below): the barriers are the nearest confirmed swing
    # ahead and behind, ~0.8 ATR each on the median bar, and a target that
    # far takes longer to reach than a fixed ATR. Measured at 8/16/32 bars:
    # 16 was the best out of time. The k here is the FALLBACK distance for
    # a bar with no usable level on one side.
    "4h": (1.0, 16),        # levels ~0.8 ATR each way, hold 64h
    # TEN DAYS, NOT TWO. Measured, not chosen: with a 2-day window every
    # daily model on every coin sat at its shuffled control (0 of 15). The
    # window curve rose monotonically -- 0.498 at 2 days, 0.508 at 5,
    # 0.519 at 10 -- and a pooled 10-day model scored 0.530 on a held-out
    # year against controls at 0.50. Whatever the daily features know, it
    # is slow. See research/pooled_daily.py and research/pooled_holdout.py.
    "1d": (1.0, 10),        # span ~8.27%, hold 10d
}


# Which timeframes ask the STRUCTURE question rather than the ATR one. On a
# structure timeframe the two model slots are not two horizons but two
# SIDES: h1 is the long model, h2 the short model, both on the same hold.
# Everything that reads `model_paths` still finds two files; everything that
# reads `barriers_for` still gets two holds (equal). What changes is what
# each model was asked, and `evaluate` reads that from the model's own cfg.
GEOMETRY: Dict[str, str] = {
    "4h": "structure",
}


def geometry_for(interval: str) -> str:
    return GEOMETRY.get(interval, "atr")


def barriers_for(interval: str) -> Tuple[float, int, int]:
    """(k ATR each side, h1 hold in bars, h2 hold in bars).

    h1 is the half-way horizon the monitor reads once a window is partly
    spent — the same "different question with less time left" split that has
    always separated h1 from h2, generalised past 1-and-2 bars. On a
    structure timeframe both slots hold for the full window, because the
    slots are sides, not horizons.
    """
    k, h2 = BARRIERS.get(interval, (1.0, 2))
    if geometry_for(interval) == "structure":
        return k, h2, h2
    return k, max(1, h2 // 2), h2


def slot_side(interval: str, slot: int) -> str:
    """Which side a model slot answers for, on a structure timeframe."""
    return "long" if slot == 1 else "short"


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


def eval_path(symbol: str, interval: str,
              output_dir: Optional[Path] = None) -> Path:
    """Where the evaluation verdict for a pair and timeframe lives.

    A sidecar beside the models, not inside them: the models are joblib
    blobs read by one loader, and the verdict is a few numbers a human, a
    test and the API all want to read without unpickling anything.
    """
    out = Path(output_dir) if output_dir else Path("output")
    return out / f"eval_{symbol.upper()}_{interval}.json"


def beats_shuffle(auc: float, shuffle: float, spread: float) -> bool:
    """Does this fit know something a fit on scrambled labels does not?

    NOT `auc > 0.5`. The honest control is the same model trained on the
    same features with the labels shuffled -- it lands near 0.5 but not on
    it, and the fold spread says how far a fit can wander by luck. A model
    only earns a call if it clears BOTH: it beats the scrambled control by
    more than the noise between folds. BTCUSDT 1d scored 0.480 against a
    shuffle of 0.481 and a spread of 0.019, which is precisely "nothing".
    """
    try:
        a, sh, sp = float(auc), float(shuffle), float(spread)
    except (TypeError, ValueError):
        return False
    if a != a or sp != sp:
        return False
    # BOTH, not either. 1000PEPE 1d scored 0.470 against a shuffle of 0.465
    # -- "beat" its control while being worse than a coin flip, because the
    # control itself had wandered below 0.5 on a small daily sample. A model
    # earns a call only if it is above 0.5 by more than the fold noise AND
    # above its scrambled control by more than the fold noise.
    return (a - 0.5) > sp and (a - sh) > sp


_VERDICTS: dict = {}


def model_usable(symbol: str, interval: str,
                 output_dir: Optional[Path] = None,
                 slot: Optional[str] = None) -> Tuple[bool, str]:
    """(usable, reason). Usable when no verdict exists -- an unevaluated
    model is the status quo, and gating it on a file nobody has written
    would silence every pair on the day this ships. With `slot` ("h1" or
    "h2"), usable when THAT model beats its shuffle; without, when at
    least one does. Otherwise not, and the reason says so in words the
    dashboard can show."""
    import json

    path = eval_path(symbol, interval, output_dir)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return True, ""
    # Read once per file version. This runs on every dashboard serve, and
    # a JSON parse per request on a one-core box is a cost worth skipping.
    cached = _VERDICTS.get(path)
    if cached and cached[0] == mtime:
        v = cached[1]
    else:
        try:
            v = json.loads(path.read_text())
        except (ValueError, OSError):
            return True, ""
        _VERDICTS[path] = (mtime, v)
    hs = v.get("horizons") or {}
    if not hs:
        return True, ""
    # Recomputed from the numbers, never read from the stored flag: the rule
    # is `beats_shuffle`, in one place, and tightening it must not require
    # rewriting every verdict file already on disk.
    if slot and slot in hs:
        h = hs[slot]
        if beats_shuffle(h.get("auc"), h.get("shuffle"), h.get("spread")):
            return True, ""
        what = h.get("side") or slot
        return False, (
            f"No call on {interval}: the {what} model does not beat a control "
            f"trained on scrambled labels (AUC {h.get('auc', 0):.3f} vs shuffle "
            f"{h.get('shuffle', 0):.3f}). A call from it would be a coin flip.")
    if any(beats_shuffle(h.get("auc"), h.get("shuffle"), h.get("spread"))
           for h in hs.values()):
        return True, ""
    best = max(hs.values(), key=lambda h: h.get("auc") or 0)
    return False, (
        f"No call on {interval}: this model does not beat a control trained "
        f"on scrambled labels (AUC {best.get('auc', 0):.3f} vs shuffle "
        f"{best.get('shuffle', 0):.3f}). A call from it would be a coin flip.")
