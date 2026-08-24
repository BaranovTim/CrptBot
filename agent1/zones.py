"""Order blocks and fair value gaps, reduced to fixed-width numbers.

The real work in this file is not detection — an order block is "the last
opposing candle before a break of structure" and an FVG is a three-candle
imbalance, both of which are two lines of arithmetic.  The work is the
REDUCTION: at any bar there may be zero or seven live zones, and LightGBM
takes a fixed-length vector.  Collapsing a variable-length list of price
ranges into (distance to nearest, its size, its age, are we inside one) is
where the information is either kept or thrown away.

Note a zone discovered at bar t from a candle at bar j < t is not lookahead.
We learn of the zone at t, and only mark it live from t onward.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import Agent1Config
from .indicators import safe_div
from .structure import BOS, CHOCH, StructureEvent

ZONE_COLUMNS = (
    "dist_to_bull_ob_atr",
    "dist_to_bear_ob_atr",
    "inside_ob",
    "nearest_ob_age_bars",
    "dist_to_fvg_atr",
    "fvg_size_atr",
    "fvg_direction",
    "nearest_fvg_age_bars",
)


@dataclass
class Zone:
    bottom: float
    top: float
    direction: int      # +1 bullish, -1 bearish
    created_at: int     # bar we learned about it
    origin: int         # bar the price range came from

    def signed_distance(self, price: float) -> float:
        """(nearest edge - price); 0 when price is inside the zone."""
        if price > self.top:
            return self.top - price
        if price < self.bottom:
            return self.bottom - price
        return 0.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    @property
    def size(self) -> float:
        return self.top - self.bottom


def _find_order_block(
    o: np.ndarray, c: np.ndarray, h: np.ndarray, l: np.ndarray,
    event: StructureEvent, max_lookback: int,
) -> Optional[Zone]:
    """The last candle opposing the impulse, searching back from the break."""
    # search backwards from the breaking bar toward the swing that started
    # the move, but never further than max_lookback bars
    start = max(0, event.leg_start, event.bar - max_lookback)
    for j in range(event.bar, start - 1, -1):
        # for an up-break we want the last RED candle before the push up
        opposing = c[j] < o[j] if event.direction == 1 else c[j] > o[j]
        if opposing:
            return Zone(
                bottom=float(l[j]), top=float(h[j]),
                direction=event.direction, created_at=event.bar, origin=j,
            )
    return None


def compute_zones(
    bars: pd.DataFrame,
    events: List[StructureEvent],
    atr: pd.Series,
    cfg: Agent1Config,
) -> pd.DataFrame:
    n = len(bars)
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    a = atr.to_numpy(float)

    breaks_by_bar = {}
    for e in events:
        if e.kind in (BOS, CHOCH):
            breaks_by_bar.setdefault(e.bar, []).append(e)

    cols = {name: np.full(n, np.nan) for name in ZONE_COLUMNS}
    # "Not inside any zone" is an observed fact, not missing data, so this
    # one column is 0-filled rather than NaN-filled.
    cols["inside_ob"] = np.zeros(n)

    obs: List[Zone] = [] # order blocks that are still alive
    fvgs: List[Zone] = [] # gaps that price has not filled yet

    for t in range(n):
        # ---- 1. retire zones this bar invalidated -------------------------
        # a bullish zone that price CLOSED below has failed - it is not
        # support any more, so drop it. note: merely touching it is fine,
        # price coming back to the zone is the whole point of the zone
        obs = [z for z in obs if not (
            (z.direction == 1 and c[t] < z.bottom) or
            (z.direction == -1 and c[t] > z.top)
        )]
        # a gap is "closed" once price has traded all the way back through it
        fvgs = [z for z in fvgs if not (
            (z.direction == 1 and l[t] <= z.bottom) or
            (z.direction == -1 and h[t] >= z.top)
        )]

        # ---- 2. zones this bar creates ------------------------------------
        for e in breaks_by_bar.get(t, []):
            z = _find_order_block(o, c, h, l, e, cfg.max_ob_lookback)
            if z is not None and z.size > 0:
                obs.append(z)

        if t >= 2 and not np.isnan(a[t]) and a[t] > 0:
            min_size = cfg.fvg_min_size_atr * a[t]
            # a fair value gap is a 3-candle hole: this bar's low is still
            # ABOVE the high from two bars ago, so no trading happened in
            # between. price often comes back to fill it
            if l[t] > h[t - 2] and (l[t] - h[t - 2]) >= min_size:
                fvgs.append(Zone(float(h[t - 2]), float(l[t]), 1, t, t - 1))
            # mirror case: this bar's high is below the low from two bars ago
            elif h[t] < l[t - 2] and (l[t - 2] - h[t]) >= min_size:
                fvgs.append(Zone(float(h[t]), float(l[t - 2]), -1, t, t - 1))

        if len(obs) > cfg.max_active_zones:
            obs = obs[-cfg.max_active_zones:]
        if len(fvgs) > cfg.max_active_zones:
            fvgs = fvgs[-cfg.max_active_zones:]

        # ---- 3. reduce the live lists to scalars --------------------------
        price, atr_t = c[t], a[t] # this bar's close and its ATR
        if np.isnan(atr_t) or atr_t <= 0:
            continue

        # THE REDUCTION: there may be 0 or 20 live zones, but the model needs
        # a fixed number of columns. so we keep only "how far to the nearest
        # one", "how big is it", "how old", "are we inside it"
        bull = [z for z in obs if z.direction == 1]
        bear = [z for z in obs if z.direction == -1]
        if bull:
            # nearest = smallest absolute distance, above or below
            z = min(bull, key=lambda z: abs(z.signed_distance(price)))
            cols["dist_to_bull_ob_atr"][t] = z.signed_distance(price) / atr_t
        if bear:
            z = min(bear, key=lambda z: abs(z.signed_distance(price)))
            cols["dist_to_bear_ob_atr"][t] = z.signed_distance(price) / atr_t
        if obs:
            nearest = min(obs, key=lambda z: abs(z.signed_distance(price)))
            cols["nearest_ob_age_bars"][t] = t - nearest.created_at # how long ago we learned about it
            # Overlapping zones of both kinds: the newer one wins, since it
            # was drawn by the more recent break.
            inside = [z for z in obs if z.contains(price)]
            if inside:
                cols["inside_ob"][t] = max(inside, key=lambda z: z.created_at).direction

        if fvgs:
            z = min(fvgs, key=lambda z: abs(z.signed_distance(price)))
            cols["dist_to_fvg_atr"][t] = z.signed_distance(price) / atr_t
            cols["fvg_size_atr"][t] = z.size / atr_t
            cols["fvg_direction"][t] = z.direction
            cols["nearest_fvg_age_bars"][t] = t - z.created_at

    return pd.DataFrame(cols, index=bars.index)[list(ZONE_COLUMNS)]
