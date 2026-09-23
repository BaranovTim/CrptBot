"""Barriers on the chart's levels instead of a fixed ATR distance.

WHY THIS EXISTS
    Every model this project shipped before September 2026 was asked the
    same question: "does price move +k ATR before -k ATR?" -- a barrier the
    same distance from entry for every bar of the day, blind to where the
    last swing sits or where the equal highs hold the stops. Asked instead
    "does price reach the NEXT confirmed swing before it breaks the LAST
    one?", the same features on the same coins scored 0.58-0.61 AUC against
    0.51 (research/structure_levels.py), and kept it on a year they never
    saw (0.584 against a scrambled control at 0.511,
    research/structure_holdout.py). The features are levels -- Agent 1 is
    pivots, structure, liquidity, fib -- and this is the label that asks
    them a question they can answer.

THE LEVELS, ALL CAUSAL
    Agent 1's own fractal pivots, usable only from `confirmed_at`, so a
    swing high is a level three bars after it printed and never earlier.
    Plus yesterday's completed high and low, the equal-highs / equal-lows
    clusters, and the 1.0 / 1.618 extension of the last completed leg.
    Nearest usable level on each side of the close, ignoring anything
    closer than MIN_ATR (noise) or further than MAX_ATR (not this trade's).

PLACEMENT
    Target AT the nearest level ahead, stop AT the nearest level behind.
    Measured against "in front of / beyond" (the whale-style placement)
    and against the second level: at-the-level was the best out of time
    and the only one whose top-5% books were positive on every coin.

ONE SIDE PER MODEL
    A long's target is the resistance; a short's is the support. Unlike
    the symmetric ATR barrier, a short is NOT the long's mirror, so a
    structure timeframe carries two models: `side="long"` and
    `side="short"`. Each answers "is MY target reached before MY stop?"
    Timeouts count as a loss: the positive class is exactly "the target
    was reached inside the window", the event the trade pays for.

FALLBACK
    A bar with no usable level on one side gets a ±1 ATR barrier on that
    side, and the label is still formed. Measured at ~3% of bars on 4h.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .config import Agent5Config
from .labels import LabelResult, _atr, _uniqueness_weights

MIN_ATR = 0.5
MAX_ATR = 6.0
FALLBACK_ATR = 1.0
# how far apart two levels must be to count as two, in ATR
DISTINCT_ATR = 0.25


def structural_levels(bars: pd.DataFrame, atr: np.ndarray,
                      pivot_left: int = 3, pivot_right: int = 3,
                      confirm_bars: int = 3, equal_tol_atr: float = 0.15,
                      equal_lookback: int = 8) -> Dict[str, np.ndarray]:
    """Per bar: the nearest usable resistance above and support below.

    Returns prices (`res`, `sup`), the level behind each (`res2`, `sup2`),
    the kind of the nearest (`swing`, `pdh`, `pdl`, `equal`, `fib1`,
    `fib1.618`) and boolean `has_*` masks. Defaults are Agent 1's.
    """
    from agent1.pivots import HIGH, find_pivots

    n = len(bars)
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    piv = sorted(find_pivots(bars["high"], bars["low"], pivot_left, pivot_right, confirm_bars),
                 key=lambda p: (p.confirmed_at, p.index))

    idx = bars.index
    if getattr(idx, "tz", None) is None:
        day = idx.normalize()
    else:
        day = idx.tz_convert("UTC").normalize()
    daily = (pd.DataFrame({"h": high, "l": low}, index=day)
             .groupby(level=0).agg({"h": "max", "l": "min"}).shift(1))
    pdh = daily["h"].reindex(day).to_numpy(float)
    pdl = daily["l"].reindex(day).to_numpy(float)

    res = np.full(n, np.nan); sup = np.full(n, np.nan)
    res2 = np.full(n, np.nan); sup2 = np.full(n, np.nan)
    res_kind = np.full(n, "", dtype=object); sup_kind = np.full(n, "", dtype=object)
    k = 0
    highs: List[float] = []
    lows: List[float] = []
    seen: list = []
    for t in range(n):
        while k < len(piv) and piv[k].confirmed_at <= t:
            p = piv[k]
            (highs if p.kind == HIGH else lows).append(p.price)
            seen.append(p)
            k += 1
        a = atr[t]
        if not np.isfinite(a) or a <= 0 or not highs or not lows:
            continue
        c = close[t]
        up: List[Tuple[float, str]] = [(h, "swing") for h in highs[-40:] if h > c]
        dn: List[Tuple[float, str]] = [(l, "swing") for l in lows[-40:] if l < c]
        if np.isfinite(pdh[t]) and pdh[t] > c:
            up.append((pdh[t], "pdh"))
        if np.isfinite(pdl[t]) and pdl[t] < c:
            dn.append((pdl[t], "pdl"))
        tol = equal_tol_atr * a
        for pool, cands, above in ((highs[-equal_lookback:], up, True),
                                   (lows[-equal_lookback:], dn, False)):
            for i in range(len(pool)):
                for j in range(i + 1, len(pool)):
                    if abs(pool[i] - pool[j]) <= tol:
                        lvl = max(pool[i], pool[j]) if above else min(pool[i], pool[j])
                        if (lvl > c) == above and lvl != c:
                            cands.append((lvl, "equal"))
        if len(seen) >= 2:
            end = seen[-1]
            start = next((p for p in reversed(seen[:-1])
                          if p.kind != end.kind and p.index < end.index), None)
            if start is not None:
                span = abs(end.price - start.price)
                for mult in (1.0, 1.618):
                    ext_up = (end.price if end.kind == HIGH else start.price) + mult * span
                    ext_dn = (end.price if end.kind != HIGH else start.price) - mult * span
                    if ext_up > c:
                        up.append((ext_up, f"fib{mult:g}"))
                    if ext_dn < c:
                        dn.append((ext_dn, f"fib{mult:g}"))
        up_ok = sorted({x for x in up if MIN_ATR * a <= x[0] - c <= MAX_ATR * a}, key=lambda x: x[0])
        dn_ok = sorted({x for x in dn if MIN_ATR * a <= c - x[0] <= MAX_ATR * a}, key=lambda x: -x[0])
        if up_ok:
            res[t], res_kind[t] = up_ok[0]
            behind = [x for x in up_ok if x[0] > up_ok[0][0] + DISTINCT_ATR * a]
            if behind:
                res2[t] = behind[0][0]
        if dn_ok:
            sup[t], sup_kind[t] = dn_ok[0]
            behind = [x for x in dn_ok if x[0] < dn_ok[0][0] - DISTINCT_ATR * a]
            if behind:
                sup2[t] = behind[0][0]
    return {"res": res, "sup": sup, "res2": res2, "sup2": sup2,
            "res_kind": res_kind, "sup_kind": sup_kind,
            "has_res": np.isfinite(res), "has_sup": np.isfinite(sup),
            "has_res2": np.isfinite(res2), "has_sup2": np.isfinite(sup2)}


def barrier_prices(bars: pd.DataFrame, cfg: Agent5Config,
                   levels: Dict[str, np.ndarray] = None,
                   atr: np.ndarray = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(upper, lower, structural) per bar for this config's side.

    `structural` says whether both barriers came from a level (True) or
    one fell back to the ATR distance.
    """
    if atr is None:
        atr = _atr(bars, cfg.atr_period).to_numpy(float)
    if levels is None:
        levels = structural_levels(bars, atr)
    c = bars["close"].to_numpy(float)
    res = np.where(levels["has_res"], levels["res"], c + FALLBACK_ATR * atr)
    sup = np.where(levels["has_sup"], levels["sup"], c - FALLBACK_ATR * atr)
    both = levels["has_res"] & levels["has_sup"]
    # upper is always above, lower always below; which is target and which
    # is stop depends on the side, and the labeller reads that from cfg
    return res, sup, both


def stop_beyond(bars: pd.DataFrame, cfg: Agent5Config,
                levels: Dict[str, np.ndarray] = None,
                atr: np.ndarray = None) -> Dict[str, np.ndarray]:
    """The levels with this side's STOP moved `cfg.stop_buffer_atr` past its
    level; the target never moves. A long's stop is the support below, so
    it moves down; a short's is the resistance above, so it moves up.

    Only a stop that came from a level moves. Where there is no level the
    barrier is the one-ATR fallback, which is a distance, not a place
    anyone else's stop is sitting -- and the research that chose the buffer
    (research/wf4h.py) left the fallback alone, so this does too.
    """
    b = float(getattr(cfg, "stop_buffer_atr", 0.0) or 0.0)
    if atr is None:
        atr = _atr(bars, cfg.atr_period).to_numpy(float)
    if levels is None:
        levels = structural_levels(bars, atr)
    if b <= 0:
        return levels
    out = dict(levels)
    if cfg.side == "long":
        out["sup"] = levels["sup"] - b * atr
    else:
        out["res"] = levels["res"] + b * atr
    return out


def structure_barrier(bars: pd.DataFrame, cfg: Agent5Config,
                      levels: Dict[str, np.ndarray] = None) -> LabelResult:
    """The triple barrier with per-bar levels, for `cfg.side`.

    Same mechanics as `labels.triple_barrier` (ambiguous bar = loss, no
    partial labels at the end of the data), with two differences that
    follow from the question: a timeout is a LOSS (the target was not
    reached), and `tp_pct` / `sl_pct` are from the trade's point of view,
    so a short's take-profit is the distance DOWN to the support.
    """
    n = len(bars)
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    atr = _atr(bars, cfg.atr_period).to_numpy(float)
    long = cfg.side == "long"
    levels = stop_beyond(bars, cfg, levels, atr)
    upper, lower, _ = barrier_prices(bars, cfg, levels, atr)

    y = np.full(n, np.nan); t1 = np.full(n, np.nan)
    touch = np.full(n, None, dtype=object)
    has_atr = np.isfinite(atr) & (atr > 0)
    ok = has_atr & np.isfinite(upper) & np.isfinite(lower) & (upper > close) & (lower < close)
    can = ok & (np.arange(n) + cfg.max_hold_bars < n)
    pending = can.copy()
    idx_all = np.arange(n)
    win = "upper" if long else "lower"
    for h in range(1, cfg.max_hold_bars + 1):
        if not pending.any():
            break
        t = idx_all[pending]
        ahead = t + h
        hit_up = high[ahead] >= upper[t]
        hit_dn = low[ahead] <= lower[t]
        for mask, name in ((hit_up & hit_dn, "ambiguous"), (hit_up & ~hit_dn, "upper"),
                           (hit_dn & ~hit_up, "lower")):
            if mask.any():
                sel = t[mask]
                y[sel] = 1.0 if name == win else 0.0
                t1[sel] = ahead[mask]
                touch[sel] = name
        pending[t[hit_up | hit_dn]] = False
    if pending.any():
        t = idx_all[pending]
        y[t] = 0.0
        t1[t] = t + cfg.max_hold_bars
        touch[t] = "timeout"
    weight = _uniqueness_weights(y, t1, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        up_pct = np.where(ok, 100.0 * (upper - close) / close, np.nan)
        dn_pct = np.where(ok, 100.0 * (close - lower) / close, np.nan)
    tp_pct, sl_pct = (up_pct, dn_pct) if long else (dn_pct, up_pct)
    ix = bars.index
    return LabelResult(
        y=pd.Series(y, index=ix, name="y"),
        t1=pd.Series(t1, index=ix, name="t1"),
        weight=pd.Series(weight, index=ix, name="weight"),
        touch=pd.Series(touch, index=ix, name="touch"),
        tp_pct=pd.Series(tp_pct, index=ix, name="tp_pct"),
        sl_pct=pd.Series(sl_pct, index=ix, name="sl_pct"),
    )
