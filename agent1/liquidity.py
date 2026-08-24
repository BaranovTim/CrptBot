"""Resting liquidity: equal highs/lows, previous-day levels, session extremes.

Cheap to compute and genuinely used by people who move size, which is the
argument for including them at all.

Everything here is anchored to UTC.  Bars are indexed by ``close_time``, and
a bar that closes at exactly 00:00:00 belongs to the day that just ended, not
the one starting — hence the 1ns nudge before flooring.  Getting this wrong
shifts every daily level by one bar and is invisible in a plot.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from .config import Agent1Config
from .pivots import HIGH, Pivot

LIQUIDITY_COLUMNS = (
    "dist_to_equal_highs_atr",
    "dist_to_equal_lows_atr",
    "dist_to_pdh_atr",
    "dist_to_pdl_atr",
    "dist_to_asia_high_atr",
    "dist_to_asia_low_atr",
)


def _owning_day(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    # step back 1 nanosecond before flooring. a bar that closes at exactly
    # 00:00:00 belongs to the day that just ENDED, not the one starting
    return (index - pd.Timedelta(nanoseconds=1)).floor("D")


def _owning_hour(index: pd.DatetimeIndex) -> np.ndarray:
    return (index - pd.Timedelta(nanoseconds=1)).hour.to_numpy()


def _equal_level(prices: List[float], tol: float, ref: float) -> Optional[float]:
    """Nearest cluster of >=2 swings sitting within ``tol`` of each other."""
    # try each swing as the centre and see how many others sit near it.
    # 2+ swings at almost the same price = a shelf of stop orders resting there
    best = None
    for anchor in prices:
        cluster = [p for p in prices if abs(p - anchor) <= tol]
        if len(cluster) >= 2:
            level = float(np.mean(cluster))
            if best is None or abs(level - ref) < abs(best - ref):
                best = level
    return best


def _previous_day_levels(bars: pd.DataFrame) -> pd.DataFrame:
    day = _owning_day(bars.index)
    daily = pd.DataFrame({"high": bars["high"].to_numpy(), "low": bars["low"].to_numpy()},
                         index=day).groupby(level=0).agg({"high": "max", "low": "min"})
    # shift(1) so today only ever sees YESTERDAY's finished high/low,
    # never today's own still-forming range
    prev = daily.shift(1)            # yesterday's completed range
    return pd.DataFrame(
        {"pdh": prev["high"].reindex(day).to_numpy(),
         "pdl": prev["low"].reindex(day).to_numpy()},
        index=bars.index,
    )


def _asia_levels(bars: pd.DataFrame, start_h: int, end_h: int) -> pd.DataFrame:
    """The most recent Asian-session extreme known at each bar.

    Inside the session that is the running extreme so far (expanding within
    the group, so still causal).  Outside it, the last session that has
    actually finished.
    """
    idx = bars.index
    day = _owning_day(idx)
    hour = _owning_hour(idx)
    in_session = (hour >= start_h) & (hour < end_h)

    sess_key = pd.Series(np.where(in_session, day.astype("int64"), -1), index=idx)
    grp = sess_key.to_numpy()

    running_hi = np.full(len(idx), np.nan)
    running_lo = np.full(len(idx), np.nan)
    hi = bars["high"].to_numpy(float)
    lo = bars["low"].to_numpy(float)
    cur_key, cur_hi, cur_lo = None, np.nan, np.nan
    for i in range(len(idx)):
        if grp[i] == -1:
            continue
        if grp[i] != cur_key:
            cur_key, cur_hi, cur_lo = grp[i], hi[i], lo[i]
        else:
            cur_hi, cur_lo = max(cur_hi, hi[i]), min(cur_lo, lo[i])
        running_hi[i], running_lo[i] = cur_hi, cur_lo

    # Completed sessions become available from the bar after the session's
    # last bar — never before.
    completed = pd.DataFrame({"hi": running_hi, "lo": running_lo}, index=idx)
    last_bar_of_session = pd.Series(in_session, index=idx) & ~pd.Series(
        np.r_[in_session[1:], False], index=idx
    )
    finished = completed[last_bar_of_session.to_numpy()]
    # ``shift(1)`` moves each completed session's value off its own last bar,
    # so the finished session is first readable on the bar AFTER it ended.
    prev_hi = finished["hi"].reindex(idx).shift(1).ffill()
    prev_lo = finished["lo"].reindex(idx).shift(1).ffill()

    out_hi = np.where(in_session, running_hi, prev_hi.to_numpy())
    out_lo = np.where(in_session, running_lo, prev_lo.to_numpy())
    return pd.DataFrame({"asia_high": out_hi, "asia_low": out_lo}, index=idx)


def compute_liquidity(
    bars: pd.DataFrame,
    pivots: List[Pivot],
    atr: pd.Series,
    cfg: Agent1Config,
) -> pd.DataFrame:
    n = len(bars)
    c = bars["close"].to_numpy(float)
    a = atr.to_numpy(float)

    piv = sorted(pivots, key=lambda p: (p.confirmed_at, p.index))
    k = 0
    highs: List[float] = []
    lows: List[float] = []

    eqh = np.full(n, np.nan)
    eql = np.full(n, np.nan)

    for t in range(n):
        while k < len(piv) and piv[k].confirmed_at <= t:
            (highs if piv[k].kind == HIGH else lows).append(piv[k].price)
            k += 1
        atr_t = a[t]
        if np.isnan(atr_t) or atr_t <= 0:
            continue
        tol = cfg.equal_level_tol_atr * atr_t
        lb = cfg.equal_level_lookback
        hi_level = _equal_level(highs[-lb:], tol, c[t]) if len(highs) >= 2 else None
        lo_level = _equal_level(lows[-lb:], tol, c[t]) if len(lows) >= 2 else None
        if hi_level is not None:
            eqh[t] = (hi_level - c[t]) / atr_t
        if lo_level is not None:
            eql[t] = (lo_level - c[t]) / atr_t

    pd_lv = _previous_day_levels(bars)
    asia = _asia_levels(bars, cfg.asia_session_utc[0], cfg.asia_session_utc[1])

    with np.errstate(invalid="ignore", divide="ignore"):
        out = pd.DataFrame(
            {
                "dist_to_equal_highs_atr": eqh,
                "dist_to_equal_lows_atr": eql,
                "dist_to_pdh_atr": (pd_lv["pdh"].to_numpy() - c) / a,
                "dist_to_pdl_atr": (pd_lv["pdl"].to_numpy() - c) / a,
                "dist_to_asia_high_atr": (asia["asia_high"].to_numpy() - c) / a,
                "dist_to_asia_low_atr": (asia["asia_low"].to_numpy() - c) / a,
            },
            index=bars.index,
        )
    return out.replace([np.inf, -np.inf], np.nan)[list(LIQUIDITY_COLUMNS)]
