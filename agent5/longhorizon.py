"""What happened over long horizons, historically. NOT a prediction.

WHY THIS IS NOT A MODEL, AND WHY IT CANNOT BE
    A fitted model needs independent observations of the thing it predicts.
    Over long horizons those barely exist, and no amount of engineering
    creates them:

        horizon    labelled windows    INDEPENDENT outcomes
        1 month              2,409                      80
        6 months             2,257                      12
        1 year               2,074                       5

    Consecutive one-year windows share 364 of their 365 days, so 2,074 of them
    are not 2,074 pieces of evidence — they are about five, seen from 2,074
    angles. Binance's BTCUSDT futures begin in September 2019, which means
    seven independent years exist in the world. Seeding weekly or monthly bars
    changes the row labels and not the information.

    Fitting a classifier on five outcomes would produce a confident number
    determined entirely by which five years happened to occur. It would look
    exactly like the 1h models — same panel, same probability, same decimal
    places — and mean nothing at all. That is worse than showing nothing.

WHAT THIS DOES INSTEAD
    Counts. Over NON-OVERLAPPING windows: how often the asset was higher after
    the horizon, the median move, the worst drawdown suffered along the way,
    and — printed as prominently as the rest — how many observations that is.

    A Wilson interval is reported with every rate. At n=5 it spans most of the
    unit interval, which is the honest picture and makes the point better than
    any wording could.

WHY THE DRAWDOWN MATTERS MORE THAN THE RATE HERE
    "Up after a year" is the wrong question for anyone actually holding. The
    number that decides whether a position survives is how far underwater it
    went first, so it is computed and shown beside the outcome.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import pandas as pd

log = logging.getLogger(__name__)

# (label, bars on a DAILY series). Daily is the base because it is the longest
# continuous series this project holds; weekly and monthly are resamplings of
# it and carry no extra information.
HORIZONS = (
    ("1 month", 30),
    ("6 months", 182),
    ("1 year", 365),
)

# Below this many independent observations, a rate is reported but explicitly
# not offered as an estimate of anything.
MIN_MEANINGFUL = 30


def wilson(successes: int, n: int, z: float = 1.96) -> Optional[tuple]:
    """95% interval for a proportion, correct at small n.

    The normal approximation is not: at n=5 it produces bounds outside [0,1]
    and a width that understates the uncertainty, which is precisely the
    regime this module exists to be honest about.
    """
    if n <= 0:
        return None
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def outcomes(bars: pd.DataFrame, horizon_bars: int) -> List[Dict[str, float]]:
    """Non-overlapping forward windows, oldest first.

    NON-OVERLAPPING is the whole point. Stepping one bar at a time would give
    thousands of rows that are almost the same row, and every statistic
    computed from them would carry a confidence that does not exist.
    """
    if bars is None or bars.empty or horizon_bars < 1:
        return []
    close = bars["close"].astype(float)
    low = bars["low"].astype(float) if "low" in bars else close

    out = []
    i = 0
    while i + horizon_bars < len(close):
        start = float(close.iloc[i])
        end = float(close.iloc[i + horizon_bars])
        if start <= 0:
            i += horizon_bars
            continue
        window_low = float(low.iloc[i:i + horizon_bars + 1].min())
        out.append({
            "at": str(close.index[i].date()),
            "return_pct": (end / start - 1.0) * 100.0,
            # How far underwater it went before getting there. The number that
            # decides whether a position survives to see the outcome.
            "drawdown_pct": (window_low / start - 1.0) * 100.0,
        })
        i += horizon_bars
    return out


def summarise(bars: pd.DataFrame,
              horizons=HORIZONS) -> List[Dict[str, Any]]:
    """One row per horizon: the counts, and how little they support."""
    rows = []
    for label, h in horizons:
        obs = outcomes(bars, h)
        n = len(obs)
        if n == 0:
            rows.append({"horizon": label, "bars": h, "n": 0,
                         "note": "no history at this horizon"})
            continue

        rets = sorted(o["return_pct"] for o in obs)
        dds = sorted(o["drawdown_pct"] for o in obs)
        ups = sum(1 for r in rets if r > 0)
        ci = wilson(ups, n)

        rows.append({
            "horizon": label,
            "bars": h,
            # PRINTED AS PROMINENTLY AS THE RATE. Five observations and eighty
            # are different kinds of statement and the reader has to see which
            # one they are looking at.
            "n": n,
            "up_rate": ups / n * 100.0,
            "ci_low": None if ci is None else ci[0] * 100.0,
            "ci_high": None if ci is None else ci[1] * 100.0,
            "median_pct": rets[n // 2],
            "worst_pct": rets[0],
            "best_pct": rets[-1],
            "median_drawdown_pct": dds[n // 2],
            "worst_drawdown_pct": dds[0],
            "meaningful": n >= MIN_MEANINGFUL,
            "note": _note(n, label),
        })
    return rows


def _note(n: int, label: str) -> str:
    if n >= MIN_MEANINGFUL:
        return (f"{n} non-overlapping {label} windows. Thin, but enough to "
                f"read as a tendency.")
    return (f"Only {n} non-overlapping {label} windows exist in the whole "
            f"history. This is a count of what happened, not an estimate of "
            f"what will — with {n} observations the range below is as likely "
            f"as any point inside it.")


def report(symbol: str, bars: pd.DataFrame) -> Dict[str, Any]:
    """The payload the long-horizon panel draws."""
    price = None
    if bars is not None and not bars.empty:
        price = float(bars["close"].astype(float).iloc[-1])
    span_years = 0.0
    if bars is not None and len(bars) > 1:
        span_years = (bars.index[-1] - bars.index[0]).days / 365.25

    return {
        "symbol": symbol,
        "price": price,
        "history_years": round(span_years, 1),
        "horizons": summarise(bars),
        # Said once, at the top, rather than implied by absence.
        "is_forecast": False,
        "disclaimer": (
            f"These are counts of what has already happened across "
            f"{span_years:.1f} years of history, not forecasts. No model is "
            f"fitted at these horizons: a one-year window has about "
            f"{max(int(span_years), 1)} independent observations in existence, "
            f"and a classifier trained on that many would be reporting which "
            f"years happened rather than what comes next."),
    }
