"""Every way of placing a stop and a target that this project can test.

WHAT A RULE IS
    A function from (bars up to and including t) to (tp_distance, sl_distance)
    in PRICE UNITS at bar t. Nothing else. Keeping the rules to that shape is
    what makes them comparable: the backtest walks each one forward through
    the same bars with the same costs and the same tie-breaks, so a
    difference in the result is a difference in the rule.

WHY EVERY RULE IS EVALUATED THE SAME WAY
    A win rate on its own cannot rank these. A rule with a stop three times
    further than its target wins ~75% of the time and is not thereby good;
    one with a target three times further wins ~25% and is not thereby bad.
    The comparison that means anything is expected value after costs, and the
    diagnostic that means anything is whether the realised hit rate beats
    `sl / (tp + sl)` — the rate a driftless random walk would produce for
    that exact geometry. See `agent5/barriers.py`.
"""
from __future__ import annotations

from typing import Callable, Dict, Tuple

import numpy as np
import pandas as pd

# (tp_distance, sl_distance) in price units, per bar, as arrays.
Rule = Callable[[pd.DataFrame], Tuple[np.ndarray, np.ndarray]]


def _atr(bars: pd.DataFrame, n: int = 14) -> np.ndarray:
    high, low, close = bars["high"].values, bars["low"].values, bars["close"].values
    prev = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def atr_rule(k_tp: float, k_sl: float, n: int = 14) -> Rule:
    """The volatility-scaled family. `k_tp == k_sl` is what this project ships."""
    def rule(bars: pd.DataFrame):
        a = _atr(bars, n)
        return a * k_tp, a * k_sl
    return rule


def percent_rule(tp_pct: float, sl_pct: float) -> Rule:
    """Fixed percentages. The naive baseline — ignores volatility entirely,
    so the same 1% is a huge move on a quiet day and noise on a wild one."""
    def rule(bars: pd.DataFrame):
        c = bars["close"].values
        return c * tp_pct / 100.0, c * sl_pct / 100.0
    return rule


def _swing(bars: pd.DataFrame, look: int):
    high = bars["high"].rolling(look, min_periods=2).max().values
    low = bars["low"].rolling(look, min_periods=2).min().values
    return high, low


def fib_retracement_rule(look: int = 50, tp_level: float = 0.618,
                         sl_level: float = 1.0) -> Rule:
    """The Fibonacci retracement tool, made testable.

    HOW THE TOOL IS ACTUALLY USED
        Anchor on the most recent swing: the highest high and lowest low of
        the last `look` bars. The retracement levels are that range times
        0.236 / 0.382 / 0.5 / 0.618 / 0.786, measured back from the extreme.
        For a long: the target is a retracement level above, and the stop
        sits beyond the swing low that anchored the move — which is the
        1.0 level.

    WHAT MAKES THIS A FAIR TEST
        The distances it produces are compared against `arbitrary_ratio_rule`
        below, which does exactly the same thing with a NON-Fibonacci ratio.
        If 0.618 carries information, it must beat 0.5 or 0.577 at the same
        anchor. If it does not, the tool is a way of choosing a distance and
        nothing more — which is a perfectly respectable thing to be, but a
        very different claim.
    """
    def rule(bars: pd.DataFrame):
        high, low = _swing(bars, look)
        rng = np.nan_to_num(high - low, nan=0.0)
        return rng * tp_level, rng * sl_level
    return rule


def arbitrary_ratio_rule(look: int = 50, tp_level: float = 0.577,
                         sl_level: float = 1.0) -> Rule:
    """The control for the Fibonacci test: same anchor, unremarkable ratio.

    0.577 is 1/sqrt(3) — a number with no following whatsoever, chosen to sit
    close enough to 0.618 that any difference cannot be explained by the
    distance alone.
    """
    return fib_retracement_rule(look, tp_level, sl_level)


def fib_extension_rule(look: int = 50, ext: float = 1.618) -> Rule:
    """Target at a Fibonacci EXTENSION of the swing, stop at the swing low.

    This is the placement most guides recommend for targets, and it produces
    a genuinely wide payoff — which is the interesting part, because a wide
    payoff is exactly what the arithmetic says cannot help on its own.
    """
    def rule(bars: pd.DataFrame):
        high, low = _swing(bars, look)
        rng = np.nan_to_num(high - low, nan=0.0)
        return rng * ext, rng * 1.0
    return rule


def swing_structure_rule(look: int = 20, reward: float = 1.0) -> Rule:
    """Stop beyond the recent swing low, target a multiple of that risk.

    The "put it where the idea is wrong" school: the distance is decided by
    structure and the target follows from it.
    """
    def rule(bars: pd.DataFrame):
        _, low = _swing(bars, look)
        c = bars["close"].values
        sl = np.nan_to_num(c - low, nan=0.0)
        return sl * reward, sl
    return rule


def bollinger_rule(n: int = 20, k: float = 2.0) -> Rule:
    """Target and stop at the volatility bands. A standard-deviation cousin
    of the ATR rule, included to see whether the volatility ESTIMATOR
    matters as much as the multiple does."""
    def rule(bars: pd.DataFrame):
        c = pd.Series(bars["close"].values)
        sd = c.rolling(n, min_periods=2).std().fillna(method="bfill").values
        d = np.nan_to_num(sd * k, nan=0.0)
        return d, d
    return rule


def chandelier_rule(look: int = 22, k: float = 3.0, reward: float = 1.0) -> Rule:
    """Chandelier exit: stop hangs `k` ATR below the highest high of `look`.

    Widely recommended for trend following, and a genuinely different shape —
    the stop distance depends on how far price has already run, not only on
    current volatility.
    """
    def rule(bars: pd.DataFrame):
        a = _atr(bars)
        high, _ = _swing(bars, look)
        c = bars["close"].values
        sl = np.nan_to_num(c - (high - k * a), nan=0.0)
        sl = np.maximum(sl, a * 0.5)          # never a zero-width stop
        return sl * reward, sl
    return rule


def catalogue() -> Dict[str, Rule]:
    """Every rule the horse race runs, named for the report."""
    return {
        # the project's current geometry, and its neighbours
        "ATR 1:1 (shipped)":      atr_rule(1.0, 1.0),
        "ATR 2:2":                atr_rule(2.0, 2.0),
        "ATR 3:3":                atr_rule(3.0, 3.0),
        # the classic advice, and its mirror
        "ATR 2:1 (r:r 2)":        atr_rule(2.0, 1.0),
        "ATR 3:1 (r:r 3)":        atr_rule(3.0, 1.0),
        "ATR 1:2 (win often)":    atr_rule(1.0, 2.0),
        "ATR 1:3 (win more)":     atr_rule(1.0, 3.0),
        # fibonacci, and its control
        "Fib 0.618 / swing":      fib_retracement_rule(50, 0.618, 1.0),
        "Fib 0.382 / swing":      fib_retracement_rule(50, 0.382, 1.0),
        "Fib ext 1.618 / swing":  fib_extension_rule(50, 1.618),
        "Arbitrary 0.577 (ctrl)": arbitrary_ratio_rule(50, 0.577, 1.0),
        "Arbitrary 0.450 (ctrl)": arbitrary_ratio_rule(50, 0.450, 1.0),
        # other schools
        "Swing structure 1:1":    swing_structure_rule(20, 1.0),
        "Swing structure 2:1":    swing_structure_rule(20, 2.0),
        "Bollinger 2sd":          bollinger_rule(20, 2.0),
        "Chandelier 3ATR":        chandelier_rule(22, 3.0, 1.0),
        "Fixed 1% / 1%":          percent_rule(1.0, 1.0),
        "Fixed 2% / 1%":          percent_rule(2.0, 1.0),
    }
