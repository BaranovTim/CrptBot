"""One input check, used by every agent.

Before this existed each agent validated its own way, and a probe found four
different behaviours on the same broken input: an empty frame crashed Agent 2
with an IndexError, sailed through Agents 3 and 4, and was cleanly rejected
by Agent 1. Duplicate timestamps were caught by two agents and silently
double-counted by the other two.

That inconsistency is worse than any single missing check, because it means
you cannot reason about what "the agents accept" — you have to remember four
separate answers.

The rule here is simple: reject loudly, early, with a message that says what
to do about it. A detector that quietly returns numbers from corrupt input is
far more expensive than one that refuses to start.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

# the five columns every agent assumes exist
REQUIRED_OHLCV = ("open", "high", "low", "close", "volume")


def check_bars(
    bars: pd.DataFrame,
    required: Sequence[str] = REQUIRED_OHLCV,
    who: str = "agent",
    require_tz: bool = True,
) -> pd.DataFrame:
    """Raise unless `bars` is safe to compute features from.

    Returns the frame unchanged so it can be used inline:
        bars = check_bars(bars, who="Agent 1")
    """
    # not a DataFrame at all - catch the obvious mistake first
    if not isinstance(bars, pd.DataFrame):
        raise TypeError(f"{who}: bars must be a pandas DataFrame, got {type(bars).__name__}")

    # an empty frame is almost always an upstream bug (bad date range, failed
    # download). returning empty features would hide it
    if len(bars) == 0:
        raise ValueError(
            f"{who}: bars is empty. This usually means the date range had no "
            f"data or a download failed silently - check the loader, not the agent."
        )

    missing = [c for c in required if c not in bars.columns]
    if missing:
        raise ValueError(f"{who}: bars is missing required columns: {missing}")

    # the index must be real timestamps, not integers or strings
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise TypeError(
            f"{who}: bars must be indexed by close_time as a DatetimeIndex. "
            f"Indexing by open_time makes the bar's own close visible at its "
            f"open, which is a one-bar look into the future."
        )

    # timezone matters. a naive index holding LOCAL time silently shifts every
    # session and daily boundary - measured at 276/400 bars corrupted on a
    # Europe/Kyiv index - and nothing raises
    if require_tz and bars.index.tz is None:
        raise ValueError(
            f"{who}: bars index is timezone-naive. Use UTC "
            f"(bars.index = bars.index.tz_localize('UTC')). A naive index "
            f"holding local time silently shifts session and daily levels, "
            f"and news timestamps compare against the wrong instant."
        )

    # tz-aware but not UTC is the sneakier version of the same bug. it passes
    # every obvious check, then cuts days and 4h buckets on LOCAL midnight:
    # measured at 451/500 bars wrong for the Asian session and 812 wrong 4h
    # values on an America/New_York index. converting is safer than rejecting,
    # and pandas still aligns joins across zones because they are the same
    # instants
    if require_tz and str(bars.index.tz) != "UTC":
        bars = bars.copy()
        bars.index = bars.index.tz_convert("UTC")

    if not bars.index.is_monotonic_increasing:
        raise ValueError(f"{who}: bars index must be sorted ascending - use bars.sort_index()")

    # duplicates double-count volume and break every rolling window
    if bars.index.has_duplicates:
        n = int(bars.index.duplicated().sum())
        raise ValueError(
            f"{who}: bars index has {n} duplicate timestamps. These double-count "
            f"volume and corrupt every rolling window - "
            f"use bars[~bars.index.duplicated(keep='last')]."
        )

    # strings that look like numbers are the classic CSV-loading mistake
    bad = [c for c in required if not pd.api.types.is_numeric_dtype(bars[c])]
    if bad:
        raise TypeError(
            f"{who}: these columns are not numeric: {bad}. If you loaded from "
            f"CSV, pass them through pd.to_numeric first."
        )

    return bars


def describe_bars_problem(bars: pd.DataFrame) -> Optional[str]:
    """Warn about data that is legal but suspicious. Returns None if clean.

    These do NOT raise. They are conditions that produce meaningless features
    rather than crashes, so you want to see them but not be blocked by them.
    """
    notes = []

    if {"high", "low"} <= set(bars.columns):
        # high below low means the feed is corrupt or the columns are swapped.
        # everything downstream still "works" and returns garbage
        broken = int((bars["high"] < bars["low"]).sum())
        if broken:
            notes.append(f"{broken} bars have high < low (corrupt feed or swapped columns)")

    if {"high", "low", "close", "open"} <= set(bars.columns):
        # close outside the bar's own range is impossible in real data
        outside = int(((bars["close"] > bars["high"]) | (bars["close"] < bars["low"])).sum())
        if outside:
            notes.append(f"{outside} bars have close outside [low, high]")

    for c in REQUIRED_OHLCV:
        if c in bars.columns:
            n = int(bars[c].isna().sum())
            if n:
                notes.append(f"{c} has {n} NaN values ({n / len(bars):.1%})")

    if "close" in bars.columns:
        # a completely flat series makes every volatility measure zero, which
        # turns every ATR-normalised feature into NaN
        if bars["close"].nunique() == 1:
            notes.append("close is constant - all volatility features will be NaN")

    if len(bars) > 3 and isinstance(bars.index, pd.DatetimeIndex):
        # gaps are normal (exchange downtime) but big ones distort any feature
        # that counts bars, because "10 bars ago" stops meaning "10 hours ago"
        step = pd.Series(bars.index).diff().dropna()
        if len(step) and step.nunique() > 1:
            typical = step.median()
            gaps = int((step > typical * 3).sum())
            if gaps:
                notes.append(
                    f"{gaps} gaps larger than 3x the {typical} bar interval - "
                    f"bar-count features (bars_since_*) will understate real elapsed time"
                )

    return "; ".join(notes) if notes else None


def utc_now() -> pd.Timestamp:
    """Current time as a tz-aware UTC Timestamp.

    Exists because `pd.Timestamp.utcnow()` changed: in pandas 2.x it already
    returns a tz-aware value, so the once-idiomatic
    `pd.Timestamp.utcnow().tz_localize("UTC")` now raises TypeError.

    That bug sat in five files and never fired, because every call site that
    used it was only reached when an explicit end date was NOT given - and
    every test happened to pass one. One helper, used everywhere, so the next
    pandas change is a single edit.
    """
    return pd.Timestamp.now(tz="UTC")
