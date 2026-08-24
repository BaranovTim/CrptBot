"""Market structure: BOS, CHoCH, the dealing range, and liquidity sweeps.

BOS and CHoCH are geometrically the same event — price took out a prior swing
level.  What separates them is the trend that was in force at the moment of
the break: with the trend it is a continuation (BOS), against it, the first
sign of a change of character (CHoCH).  So this cannot be a function of one
bar; it is a state machine walked forward over the window.

That state is not memory *between* calls.  It is rebuilt from bar 0 of the
window on every call, so the function stays pure: same window in, same
numbers out, which is exactly what makes the no-lookahead test meaningful.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from .indicators import safe_div
from .pivots import HIGH, LOW, Pivot, by_confirmation

BOS = "BOS"
CHOCH = "CHOCH"
SWEEP = "SWEEP"


@dataclass(frozen=True)
class StructureEvent:
    bar: int
    kind: str            # BOS | CHOCH | SWEEP
    direction: int       # +1 bullish, -1 bearish
    level: float         # the swing price that was taken out
    pivot_index: int     # bar of the swing that was taken out
    leg_start: int       # bar of the opposing swing that started the impulse


STRUCTURE_COLUMNS = (
    "trend_direction",
    "bars_since_bos",
    "bars_since_choch",
    "consecutive_bos_count",
    "position_in_range",
    "range_size_atr",
    "bars_since_sweep",
    "sweep_direction",
)


def compute_structure(
    bars: pd.DataFrame,
    pivots: List[Pivot],
    atr: pd.Series,
    break_mode: str = "close",
) -> Tuple[pd.DataFrame, List[StructureEvent]]:
    n = len(bars)
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    a = atr.to_numpy(float)

    pending = by_confirmation(pivots, n)

    trend_arr = np.full(n, np.nan)
    since_bos = np.full(n, np.nan)
    since_choch = np.full(n, np.nan)
    consec_arr = np.full(n, np.nan)
    pos_in_range = np.full(n, np.nan)
    range_size = np.full(n, np.nan)
    since_sweep = np.full(n, np.nan)
    sweep_dir_arr = np.full(n, np.nan)

    events: List[StructureEvent] = []

    active_sh: Optional[Pivot] = None   # nearest unbroken swing high
    active_sl: Optional[Pivot] = None
    range_sh: Optional[Pivot] = None    # newest swing high, broken or not
    range_sl: Optional[Pivot] = None
    trend = 0 # 0 = no trend yet, +1 = up, -1 = down
    last_bos: Optional[int] = None
    last_choch: Optional[int] = None
    consec_bos = 0 # how many continuations in a row without a reversal
    last_sweep: Optional[int] = None
    last_sweep_dir = 0

    # walk forward one bar at a time, exactly like a live system would
    for t in range(n):
        # ---- 1. ingest pivots that become usable on this bar --------------
        # only pivots whose confirmed_at == t. anything later is still unknown
        for p in pending.get(t, []):
            if p.kind == HIGH:
                range_sh = p
                # With confirm_bars > pivot_right there is a gap in which the
                # level may already have been taken out before we hear about
                # it.  Such a level is stale; record it for the range, but
                # never arm it as a breakable level.
                if h[p.index + 1 : t + 1].max(initial=-np.inf) <= p.price:
                    active_sh = p
            else:
                range_sl = p
                if l[p.index + 1 : t + 1].min(initial=np.inf) >= p.price:
                    active_sl = p

        # ---- 2. did this bar take out a level? ----------------------------
        # "close" mode = the level only counts as broken if the bar CLOSED
        # beyond it. "wick" mode = any trade beyond it counts. close is
        # quieter, wick catches stop-hunts
        up_probe = c[t] if break_mode == "close" else h[t] # what price we test the level against
        dn_probe = c[t] if break_mode == "close" else l[t]
        broke_up = active_sh is not None and up_probe > active_sh.price
        broke_dn = active_sl is not None and dn_probe < active_sl.price

        # a huge bar can poke through both sides. let the candle's own body
        # decide which break was the real one - green bar means the up-break
        # is the story, red bar means the down-break is
        if broke_up and broke_dn:
            if c[t] >= o[t]:
                broke_dn = False
            else:
                broke_up = False

        if broke_up:
            # THIS is the only difference between BOS and CHoCH: the same
            # break is a continuation if we were already going up, and a
            # change-of-character if we were going down
            kind = BOS if trend == 1 else CHOCH
            if kind == BOS:
                consec_bos += 1
                last_bos = t
            else:
                consec_bos = 0
                last_choch = t
            trend = 1      # we are now officially in an uptrend
            events.append(
                StructureEvent(
                    bar=t,
                    kind=kind,
                    direction=1,
                    level=active_sh.price,
                    pivot_index=active_sh.index,
                    leg_start=active_sl.index if active_sl is not None else max(0, t - 1),
                )
            )
            # the level has been used up. we wait for a NEW confirmed swing
            # high before there is anything to break again
            active_sh = None # level used up, wait for a new swing high
        elif broke_dn:
            kind = BOS if trend == -1 else CHOCH
            if kind == BOS:
                consec_bos += 1
                last_bos = t
            else:
                consec_bos = 0
                last_choch = t
            trend = -1
            events.append(
                StructureEvent(
                    bar=t,
                    kind=kind,
                    direction=-1,
                    level=active_sl.price,
                    pivot_index=active_sl.index,
                    leg_start=active_sh.index if active_sh is not None else max(0, t - 1),
                )
            )
            active_sl = None # level used up, wait for a new swing low
        else:
            # ---- 3. no break: was liquidity taken and rejected? -----------
            # Wick through the level, close back inside.  Stops above the
            # highs got filled and price refused to stay there — that reads
            # bearish, hence direction -1 (positive is always bullish).
            if active_sh is not None and h[t] > active_sh.price >= c[t]:
                last_sweep, last_sweep_dir = t, -1
                events.append(
                    StructureEvent(t, SWEEP, -1, active_sh.price, active_sh.index, active_sh.index)
                )
            elif active_sl is not None and l[t] < active_sl.price <= c[t]:
                last_sweep, last_sweep_dir = t, 1
                events.append(
                    StructureEvent(t, SWEEP, 1, active_sl.price, active_sl.index, active_sl.index)
                )

        # ---- 4. emit this bar's row ---------------------------------------
        # before the first break we have no trend at all. leave those bars
        # NaN rather than guessing a direction
        if trend != 0:
            trend_arr[t] = trend
            consec_arr[t] = consec_bos
        if last_bos is not None: # only fill this in once a BOS has actually happened
            since_bos[t] = t - last_bos # how many bars ago, in bars not hours
        if last_choch is not None:
            since_choch[t] = t - last_choch
        if last_sweep is not None:
            since_sweep[t] = t - last_sweep
            sweep_dir_arr[t] = last_sweep_dir
        # where price sits between the newest swing low and swing high.
        # 0 = at the low (discount), 1 = at the high (premium).
        # deliberately NOT clipped: >1 means price broke out above the range,
        # which is real information that clipping would erase
        if range_sh is not None and range_sl is not None:
            hi, lo = range_sh.price, range_sl.price
            if hi > lo:
                pos_in_range[t] = (c[t] - lo) / (hi - lo)
                range_size[t] = safe_div(hi - lo, a[t])

    out = pd.DataFrame(
        {
            "trend_direction": trend_arr,
            "bars_since_bos": since_bos,
            "bars_since_choch": since_choch,
            "consecutive_bos_count": consec_arr,
            "position_in_range": pos_in_range,
            "range_size_atr": range_size,
            "bars_since_sweep": since_sweep,
            "sweep_direction": sweep_dir_arr,
        },
        index=bars.index,
    )
    return out, events
