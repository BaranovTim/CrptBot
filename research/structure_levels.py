"""Experiment: targets and stops on the chart's levels instead of +/-k ATR.

THE OBJECTION THIS ANSWERS
    Every TP and SL the app has ever shown was `entry +/- k x ATR`: the
    same percentage for every trade on a coin that day, blind to where the
    previous swing sits, where the equal highs hold the stops, where
    yesterday's range ended. The 2R / 4R experiments stretched those same
    blind barriers wider. This puts them on the structure instead.

THE LEVELS, ALL CAUSAL (agent1's own pivots, usable only from `confirmed_at`)
    resistance   confirmed swing highs above price, yesterday's high, the
                 equal-highs cluster, and the 1.0 / 1.618 extension of the
                 last completed swing leg
    support      the mirror: swing lows below, yesterday's low, equal lows,
                 the extensions downward

    A LONG takes profit a shade IN FRONT of the nearest resistance (the
    order fills before the pool of sellers waiting there) and stops a
    buffer BEYOND the nearest support (the sweep that runs the stops
    resting at the level does not take this one). A SHORT is the mirror.
    Candidate levels closer than `MIN_ATR` are skipped as noise; with none
    inside `MAX_ATR` the trade has no structural target and falls back to
    the ATR barrier, and the row is counted as such.

THREE GEOMETRIES SCORED SIDE BY SIDE, PER COIN, PER SIDE
    atr        entry +/- 1 ATR, the shipped one, for reference
    structure  target at structure, stop at structure
    risk2r     stop at structure, target at twice the stop's distance --
               "risk defined by the chart, reward by the ratio"

Each is a different label, so each is a different fit: same purged CV,
same shuffle control, calibration cross-fitted in time, P&L realised at
the target, the stop, or the mark at the time exit, less fees, with error
bars. See research/four_r.py for the scoring and research/QUANT.md for why
none of this is believed until it survives that.

    usage: python research/structure_levels.py CACHE_DIR [--coins ...]
               [--intervals 1h,4h] [--holds 4,8] [--out ...]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent1.config import Agent1Config                      # noqa: E402
from agent1.pivots import HIGH, find_pivots                 # noqa: E402
from agent5 import Agent5Config                             # noqa: E402
from agent5.dataset import build_dataset                    # noqa: E402
from agent5.labels import LabelResult, _atr, _uniqueness_weights  # noqa: E402
from core import TIMEFRAMES, barriers_for                   # noqa: E402
from research.four_r import frames_for, score, fmt          # noqa: E402

MIN_ATR = 0.5          # a level closer than this is noise, not a target
MAX_ATR = 6.0          # further than this is not this trade's level
FALLBACK_TP = 1.0      # ATR, when no structural target exists
FALLBACK_SL = 1.0


class Placement:
    """Where, relative to the level, the orders go. Module state so the
    grid can sweep it; see `--shave`, `--buffer`, `--target-rank`."""
    tp_shave = 0.10        # take profit this many ATR in front of the level
    sl_buffer = 0.25       # stop this many ATR beyond the level
    target_rank = 1        # 1 = nearest level, 2 = the one behind it


# ------------------------------------------------------------ the levels
def structural_levels(bars: pd.DataFrame, atr: np.ndarray,
                      cfg: Agent1Config = Agent1Config()) -> Dict[str, np.ndarray]:
    """Per bar: nearest usable resistance above and support below.

    Returns {res, sup, res_kind, sup_kind, has_res, has_sup}. Prices, not
    distances, so the labeller can place barriers at them directly.
    """
    n = len(bars)
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    piv = find_pivots(bars["high"], bars["low"], cfg.pivot_left, cfg.pivot_right,
                      cfg.confirm_bars)
    piv = sorted(piv, key=lambda p: (p.confirmed_at, p.index))

    # yesterday's completed range, per bar (shift so today never sees itself)
    day = bars.index.tz_convert("UTC").normalize()
    daily = pd.DataFrame({"h": high, "l": low}, index=day).groupby(level=0).agg(
        {"h": "max", "l": "min"}).shift(1)
    pdh = daily["h"].reindex(day).to_numpy(float)
    pdl = daily["l"].reindex(day).to_numpy(float)

    res = np.full(n, np.nan); sup = np.full(n, np.nan)
    res2 = np.full(n, np.nan); sup2 = np.full(n, np.nan)
    res_kind = np.full(n, "", dtype=object); sup_kind = np.full(n, "", dtype=object)
    k = 0
    highs: List[float] = []
    lows: List[float] = []
    seen = []                      # pivots in confirmation order, for the leg
    tol_lb = cfg.equal_level_lookback
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
        cands_up: List[Tuple[float, str]] = [(h, "swing") for h in highs[-40:] if h > c]
        cands_dn: List[Tuple[float, str]] = [(l, "swing") for l in lows[-40:] if l < c]
        if np.isfinite(pdh[t]) and pdh[t] > c:
            cands_up.append((pdh[t], "pdh"))
        if np.isfinite(pdl[t]) and pdl[t] < c:
            cands_dn.append((pdl[t], "pdl"))
        # equal highs / lows: a cluster of recent swings within tolerance
        tol = cfg.equal_level_tol_atr * a
        for pool, cands, above in ((highs[-tol_lb:], cands_up, True),
                                   (lows[-tol_lb:], cands_dn, False)):
            for i in range(len(pool)):
                for j in range(i + 1, len(pool)):
                    if abs(pool[i] - pool[j]) <= tol:
                        lvl = max(pool[i], pool[j]) if above else min(pool[i], pool[j])
                        if (lvl > c) == above and lvl != c:
                            cands.append((lvl, "equal"))
        # the last completed leg's extensions
        if len(seen) >= 2:
            end = seen[-1]
            start = next((p for p in reversed(seen[:-1])
                          if p.kind != end.kind and p.index < end.index), None)
            if start is not None:
                span = abs(end.price - start.price)
                for mult in (1.0, 1.618):
                    up = end.price + mult * span if end.kind == HIGH else start.price + mult * span
                    dn = end.price - mult * span if end.kind != HIGH else start.price - mult * span
                    if up > c:
                        cands_up.append((up, f"fib{mult:g}"))
                    if dn < c:
                        cands_dn.append((dn, f"fib{mult:g}"))
        # nearest usable on each side: not noise-close, not out of reach
        up_ok = [(lvl, kd) for lvl, kd in cands_up if MIN_ATR * a <= lvl - c <= MAX_ATR * a]
        dn_ok = [(lvl, kd) for lvl, kd in cands_dn if MIN_ATR * a <= c - lvl <= MAX_ATR * a]
        if up_ok:
            ordered = sorted(set(up_ok), key=lambda x: x[0])
            res[t], res_kind[t] = ordered[0]
            behind = [x for x in ordered if x[0] > ordered[0][0] + 0.25 * a]
            if behind:
                res2[t] = behind[0][0]
        if dn_ok:
            ordered = sorted(set(dn_ok), key=lambda x: -x[0])
            sup[t], sup_kind[t] = ordered[0]
            behind = [x for x in ordered if x[0] < ordered[0][0] - 0.25 * a]
            if behind:
                sup2[t] = behind[0][0]
    return {"res": res, "sup": sup, "res2": res2, "sup2": sup2,
            "res_kind": res_kind, "sup_kind": sup_kind,
            "has_res": np.isfinite(res), "has_sup": np.isfinite(sup),
            "has_res2": np.isfinite(res2), "has_sup2": np.isfinite(sup2)}


# ----------------------------------------------------------- the labeller
def barrier_labels(bars: pd.DataFrame, upper: np.ndarray, lower: np.ndarray,
                   max_hold: int, side: str) -> LabelResult:
    """agent5's triple barrier with per-bar levels. The trade's own TP and
    SL distances are returned in % of entry, from the trade's point of view.
    Timeouts count as a loss (the target was not reached). Ambiguous bars
    count as a loss, as the shipped labeller does."""
    n = len(bars)
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    y = np.full(n, np.nan); t1 = np.full(n, np.nan)
    touch = np.full(n, None, dtype=object)
    ok = np.isfinite(upper) & np.isfinite(lower) & (upper > close) & (lower < close)
    can = ok & (np.arange(n) + max_hold < n)
    pending = can.copy()
    idx_all = np.arange(n)
    win_touch = "upper" if side == "long" else "lower"
    for h in range(1, max_hold + 1):
        if not pending.any():
            break
        t = idx_all[pending]
        ahead = t + h
        hit_up = high[ahead] >= upper[t]
        hit_dn = low[ahead] <= lower[t]
        both = hit_up & hit_dn
        for mask, name in ((both, "ambiguous"), (hit_up & ~hit_dn, "upper"),
                           (hit_dn & ~hit_up, "lower")):
            if mask.any():
                sel = t[mask]
                y[sel] = 1.0 if name == win_touch else 0.0
                t1[sel] = ahead[mask]
                touch[sel] = name
        pending[t[hit_up | hit_dn]] = False
    if pending.any():
        t = idx_all[pending]
        y[t] = 0.0
        t1[t] = t + max_hold
        touch[t] = "timeout"
    weight = _uniqueness_weights(y, t1, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        up_pct = np.where(ok, 100.0 * (upper - close) / close, np.nan)
        dn_pct = np.where(ok, 100.0 * (close - lower) / close, np.nan)
    tp_pct, sl_pct = (up_pct, dn_pct) if side == "long" else (dn_pct, up_pct)
    ix = bars.index
    return LabelResult(y=pd.Series(y, index=ix, name="y"),
                       t1=pd.Series(t1, index=ix, name="t1"),
                       weight=pd.Series(weight, index=ix, name="weight"),
                       touch=pd.Series(touch, index=ix, name="touch"),
                       tp_pct=pd.Series(tp_pct, index=ix, name="tp_pct"),
                       sl_pct=pd.Series(sl_pct, index=ix, name="sl_pct"))


def geometry_levels(bars: pd.DataFrame, atr: np.ndarray, lv: dict,
                    kind: str, side: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(upper, lower, structural) price arrays for one geometry and side."""
    c = bars["close"].to_numpy(float)
    if kind == "atr":
        return c + atr, c - atr, np.zeros(len(c), bool)
    P = Placement
    res, sup = lv["res"], lv["sup"]
    # the target may sit on the level behind the nearest one; the stop is
    # always beyond the NEAREST opposing level (the one the sweep runs)
    if P.target_rank == 2:
        t_res = np.where(lv["has_res2"], lv["res2"], res)
        t_sup = np.where(lv["has_sup2"], lv["sup2"], sup)
    else:
        t_res, t_sup = res, sup
    tp_res = np.where(lv["has_res"], t_res - P.tp_shave * atr, c + FALLBACK_TP * atr)
    sl_sup = np.where(lv["has_sup"], sup - P.sl_buffer * atr, c - FALLBACK_SL * atr)
    tp_sup = np.where(lv["has_sup"], t_sup + P.tp_shave * atr, c - FALLBACK_TP * atr)
    sl_res = np.where(lv["has_res"], res + P.sl_buffer * atr, c + FALLBACK_SL * atr)
    if kind == "structure":
        if side == "long":
            return tp_res, sl_sup, lv["has_res"] & lv["has_sup"]
        return sl_res, tp_sup, lv["has_res"] & lv["has_sup"]
    if kind == "risk2r":
        if side == "long":
            risk = c - sl_sup
            return c + 2.0 * risk, sl_sup, lv["has_sup"]
        risk = sl_res - c
        return sl_res, c - 2.0 * risk, lv["has_res"]
    raise ValueError(kind)


def realised(bars: pd.DataFrame, lab: LabelResult, side: str) -> np.ndarray:
    close = bars["close"].to_numpy(float)
    touch = lab.touch.to_numpy(object)
    t1 = lab.t1.to_numpy(float)
    tp = lab.tp_pct.to_numpy(float); sl = lab.sl_pct.to_numpy(float)
    n = len(close); idx = np.arange(n)
    win = "upper" if side == "long" else "lower"
    lose = "lower" if side == "long" else "upper"
    sign = 1.0 if side == "long" else -1.0
    ok = ~np.isnan(t1)
    exit_ret = np.full(n, np.nan)
    exit_ret[ok] = sign * 100.0 * (close[t1[ok].astype(int)] / close[idx[ok]] - 1.0)
    out = np.full(n, np.nan)
    out = np.where(touch == win, tp, out)
    out = np.where((touch == lose) | (touch == "ambiguous"), -sl, out)
    out = np.where(touch == "timeout", exit_ret, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache")
    ap.add_argument("--coins", default="BTC,ETH,SOL,DOGE,XRP")
    ap.add_argument("--intervals", default="1h,4h")
    ap.add_argument("--holds", default="1,4,8", help="multiples of the shipped h2")
    ap.add_argument("--geometries", default="atr,structure,risk2r")
    ap.add_argument("--out", default="research/results/structure_levels.json")
    ap.add_argument("--shave", type=float, default=0.10)
    ap.add_argument("--buffer", type=float, default=0.25)
    ap.add_argument("--target-rank", type=int, default=1)
    ap.add_argument("--tag", default="", help="suffix on result keys for a sweep")
    a = ap.parse_args()
    Placement.tp_shave, Placement.sl_buffer = a.shave, a.buffer
    Placement.target_rank = a.target_rank
    cache = Path(a.cache)
    coins = [c.strip().upper() + ("" if c.strip().upper().endswith("USDT") else "USDT")
             for c in a.coins.split(",") if c.strip()]
    out_path = Path(a.out)
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    for interval in [i.strip() for i in a.intervals.split(",")]:
        k, _h1, h2 = barriers_for(interval)
        holds = sorted({max(2, int(round(h2 * float(m)))) for m in a.holds.split(",")})
        print(f"\n=== {interval}: holds {holds}", flush=True)
        for sym in coins:
            got = frames_for(cache, sym, interval)
            if got is None:
                continue
            bars, frames, warm = got
            atr = _atr(bars, 14).to_numpy(float)
            lv = structural_levels(bars, atr)
            cover = float(np.mean(lv["has_res"] & lv["has_sup"]))
            kinds = pd.Series(lv["res_kind"][lv["has_res"]]).value_counts(normalize=True)
            print(f"  {sym}: structural target+stop on {cover:.0%} of bars; "
                  f"targets by kind {kinds.round(2).to_dict()}", flush=True)
            for hold in holds:
                for geom in [g.strip() for g in a.geometries.split(",")]:
                    for side in ("long", "short"):
                        key = f"{interval}|{sym}|h{hold}|{geom}{a.tag}|{side}"
                        if key in results:
                            continue
                        t0 = time.time()
                        upper, lower, structural = geometry_levels(bars, atr, lv, geom, side)
                        lab = barrier_labels(bars, upper, lower, hold, side)
                        cfg = Agent5Config(max_hold_bars=hold, k_up=1.0, k_dn=1.0)
                        ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **frames)
                        pos = ds.positions.astype(int)
                        pnl = realised(bars, lab, side)[pos]
                        tp = lab.tp_pct.to_numpy(float)[pos]
                        sl = lab.sl_pct.to_numpy(float)[pos]
                        touch = lab.touch.to_numpy(object)[pos]
                        r = score(ds, cfg, list(ds.X.columns), pnl, tp, sl, touch, side)
                        r["structural_share"] = float(np.mean(structural[pos]))
                        r["median_tp_atr"] = float(np.nanmedian((tp / 100.0 * bars["close"].to_numpy(float)[pos]) / atr[pos]))
                        r["median_sl_atr"] = float(np.nanmedian((sl / 100.0 * bars["close"].to_numpy(float)[pos]) / atr[pos]))
                        results[key] = r
                        print(fmt(f"{key} ({time.time() - t0:.0f}s; tp {r['median_tp_atr']:.1f} ATR, "
                                  f"sl {r['median_sl_atr']:.1f} ATR)", r), flush=True)
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_text(json.dumps(results, indent=1))
    print(f"\nwritten {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
