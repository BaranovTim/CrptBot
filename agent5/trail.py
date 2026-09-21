"""Managing a daily trade the way the measured professionals did.

WHERE THIS COMES FROM
    research/swing_daily.py, two separate years, fifteen coins pooled, fit
    before each year and scored on it. The same entries, three exits:

        fixed target and stop, ten-day time-out       lost, both years
        half out at the first level, trail the rest   +2.2% / +2.8% per trade
        no target, no clock, stop trails the swings   +5.1% / +4.8% per trade

    on longs taken with the 200-day trend, and positive in every cell for
    shorts. The professionals' books looked the same: winners let run for
    days, a few large trades paying for many small losses. So a daily entry
    is not held to a clock any more. It is held until the structure breaks.

THE TRAIL
    A long starts with its stop at the nearest confirmed swing low. Each
    time a NEW swing low confirms above the stop -- and below the last
    close, so a swing the market has already fallen through does not pull
    the stop above the price -- the stop moves up to it. It never moves
    down. A short is the mirror. Causal: a swing is used only from the bar
    at which it confirmed, never from the bar it printed on.

THE TREND
    Longs only above the 200-day average. Below it, the same entries lost
    in both years under every exit; above it they made the money. Shorts
    are not gated: they were positive on both sides of the average, and
    best when fading a rally above it.

WHAT IS DELIBERATELY NOT HERE
    A target. The trail is the exit. The level ahead is still shown, as
    information about where the next fight is, and the app's "half out"
    is a choice the person makes, not a rule the server applies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from .structure import structural_levels
from .labels import _atr

PIVOT_SPAN = 3
TREND_SPAN = 200


@dataclass
class TrailState:
    side: str                      # LONG | SHORT
    stop: float                    # where the stop sits now
    initial_stop: float            # where it started (the level at entry)
    moved_at: Optional[pd.Timestamp]   # the bar close the last move happened on
    moves: int                     # how many times it has moved
    swings: List[float] = field(default_factory=list)   # every stop it has sat at

    def to_json(self) -> dict:
        return {"side": self.side, "stop": self.stop, "initial_stop": self.initial_stop,
                "moved_at": self.moved_at.isoformat() if self.moved_at is not None else None,
                "moves": self.moves, "swings": self.swings}


def trend_ok(bars: pd.DataFrame, side: str, span: int = TREND_SPAN) -> Optional[bool]:
    """Is this side WITH the long-term trend? None when there is not
    enough history to say. Longs need the close above the average;
    shorts are never gated (see the module docstring)."""
    if side.upper() != "LONG":
        return True
    c = bars["close"]
    if len(c) < span // 2:
        return None
    ema = c.ewm(span=span, adjust=False).mean()
    return bool(float(c.iloc[-1]) > float(ema.iloc[-1]))


def trend_state(bars: pd.DataFrame, span: int = TREND_SPAN) -> dict:
    c = bars["close"]
    if len(c) < span // 2:
        return {"ema": None, "above": None, "span": span}
    ema = float(c.ewm(span=span, adjust=False).mean().iloc[-1])
    return {"ema": ema, "above": bool(float(c.iloc[-1]) > ema), "span": span}


def trailing_stop(bars: pd.DataFrame, opened_at, side: str,
                  initial_stop: Optional[float] = None,
                  span: int = PIVOT_SPAN) -> Optional[TrailState]:
    """The stop for a trade opened at the close of the bar at `opened_at`,
    as of the newest CLOSED bar in `bars`.

    `initial_stop` is the stop the trade was logged with; without it, the
    nearest confirmed swing behind the entry (the structural stop). The
    trail only ever tightens from there.
    """
    from agent1.pivots import HIGH, find_pivots

    side = side.upper()
    long = side == "LONG"
    idx = bars.index
    t0 = pd.Timestamp(opened_at)
    if t0.tzinfo is None:
        t0 = t0.tz_localize("UTC")
    # the entry bar: the last closed bar at or before the open time
    i0 = int(idx.searchsorted(t0, side="right")) - 1
    if i0 < 0 or i0 >= len(bars):
        return None
    close = bars["close"].to_numpy(float)

    if initial_stop is None or not np.isfinite(initial_stop):
        atr = _atr(bars, 14).to_numpy(float)
        lv = structural_levels(bars, atr)
        initial_stop = lv["sup"][i0] if long else lv["res"][i0]
        if not np.isfinite(initial_stop):
            initial_stop = close[i0] - atr[i0] if long else close[i0] + atr[i0]
    stop = float(initial_stop)
    swings = [stop]
    moved_at = None
    piv = sorted(find_pivots(bars["high"], bars["low"], span, span, span),
                 key=lambda p: (p.confirmed_at, p.index))
    n = len(bars)
    for p in piv:
        t = p.confirmed_at
        if t <= i0 or t >= n:
            continue
        behind = (p.kind != HIGH) if long else (p.kind == HIGH)
        if not behind:
            continue
        ref = close[t - 1]
        better = (stop < p.price < ref) if long else (ref < p.price < stop)
        if better:
            stop = float(p.price)
            swings.append(stop)
            moved_at = idx[t]
    return TrailState(side=side, stop=stop, initial_stop=float(initial_stop),
                      moved_at=moved_at, moves=len(swings) - 1, swings=swings)
