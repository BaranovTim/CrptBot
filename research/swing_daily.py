"""A swing strategy on daily bars, built the way the measured professionals
trade, and tested the way this project tests everything.

WHAT THE PROFESSIONALS DID (research/trader_patterns.py, 54 accounts, a year)
    held for days, not hours (median 28h, a quarter past 5 days); entered on
    pullbacks toward a level rather than chasing; won as often as the losers
    did (59% vs 57%) but let winners run twice as far (+6.2% vs +2.9%);
    kept about half of the best price a trade reached; scaled out in two
    clips; were paid by a few large trades and survived the rest.

TRANSLATED INTO RULES ON DAILY BARS
    entry     the pooled daily model's score in the top decile of its
              trailing window (the rule 4h now serves), with barriers on
              structure: target the next confirmed swing, stop the last one
    exits     three, on the SAME entries:
              fixed     target at the level, stop at the level, time-out at
                        the horizon (the label itself)
              trail     "until not closed": no time-out; the stop trails to
                        each newly confirmed swing low (high, for a short)
                        as the trade goes on, so the position lives as long
                        as the structure holds and gives back one swing
              half      take half at the first target, trail the rest
    horizons  10 / 20 / 40 daily bars for the label
    baseline  the current pooled 10-day +/-1 ATR model, same protocol

THE PROTOCOL
    Fifteen coins pooled on one shared day axis (purged CV across coins, as
    the daily model already is). Fit on everything before CUTOFF, score the
    year after, scrambled control on the identical recipe. Every number is
    realised P&L from the follower's price (the close after the signal),
    less 0.10% per round trip, with error bars.

    python research/swing_daily.py [--cutoff 2025-09-20] [--holds 10,20,40]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import warnings
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent1.pivots import HIGH, find_pivots                  # noqa: E402
from agent5 import Agent5Config                              # noqa: E402
from agent5.dataset import Dataset, build_dataset           # noqa: E402
from agent5.labels import _atr, triple_barrier               # noqa: E402
from agent5.model import fit_cv, fit_final                   # noqa: E402
from agent5.structure import structural_levels              # noqa: E402
from research.pooled_daily import EPOCH, pool, scale_bound_columns  # noqa: E402

COST = 0.10
FRAMES = Path("data_cache/research_frames")
TRAIL, MIN_N = 540, 50


def rolling_top(p: np.ndarray, q: float) -> np.ndarray:
    out = np.zeros(len(p), bool)
    for i in range(len(p)):
        past = p[max(0, i - TRAIL):i]
        if len(past) >= MIN_N:
            out[i] = (past < p[i]).mean() >= q
    return out


# ------------------------------------------------------------- exits
def replay(bars: pd.DataFrame, levels: dict, entry_i: int, side: str,
           hold: int, mode: str) -> dict:
    """One trade from the close of bar `entry_i`, under an exit mode.

      fixed   exit at the target, at the stop, or at the horizon's close
      trail   no target, no horizon: the stop trails to each newly confirmed
              swing low (high, for a short) and the trade ends on the stop
      half    half out at the target, stop to breakeven, the rest trails

    Causal: the stop moves only when a pivot CONFIRMS, at its confirmed_at
    bar, and only to a level between the old stop and the last close.
    """
    close = bars["close"].to_numpy(float); high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    long = side == "LONG"; s = 1.0 if long else -1.0
    e = close[entry_i]
    target = levels["res"][entry_i] if long else levels["sup"][entry_i]
    stop = levels["sup"][entry_i] if long else levels["res"][entry_i]
    if not (np.isfinite(target) and np.isfinite(stop)):
        return {}
    if mode.startswith("trail"):
        target = np.inf if long else -np.inf
    n = len(bars)
    span = 5 if mode.endswith("5") else 3
    piv = levels[f"_pivots{span}"]
    k = levels[f"_pivot_ptr{span}"][entry_i]
    remaining, realised = 1.0, 0.0
    limit = min(n - 1, entry_i + hold) if mode == "fixed" else n - 1

    def pct(px):
        return 100 * s * (px / e - 1)

    for t in range(entry_i + 1, limit + 1):
        if mode != "fixed":
            while k < len(piv) and piv[k].confirmed_at <= t:
                p = piv[k]; k += 1
                behind = (p.kind != HIGH) if long else (p.kind == HIGH)
                better = (stop < p.price < close[t - 1]) if long else (close[t - 1] < p.price < stop)
                if behind and better:
                    stop = p.price
        hit_t = high[t] >= target if long else low[t] <= target
        hit_s = low[t] <= stop if long else high[t] >= stop
        if hit_s:                                   # the ambiguous bar is a loss, as in the labeller
            return {"ret": realised + remaining * pct(stop), "bars": t - entry_i,
                    "how": "ambiguous" if hit_t else "stop"}
        if hit_t:
            if mode == "fixed":
                return {"ret": realised + remaining * pct(target), "bars": t - entry_i, "how": "target"}
            if mode.startswith("half") and remaining == 1.0:
                realised += 0.5 * pct(target); remaining = 0.5
                stop = max(stop, e) if long else min(stop, e)
                target = np.inf if long else -np.inf
    return {"ret": realised + remaining * pct(close[limit]), "bars": limit - entry_i, "how": "timeout"}


def with_pivots(bars: pd.DataFrame, levels: dict) -> dict:
    """Two swing sizes for the trail: 3-bar (agent1's default) and 5-bar,
    which confirms fewer, larger swings and gives a trade more room."""
    levels = dict(levels)
    for span in (3, 5):
        piv = sorted(find_pivots(bars["high"], bars["low"], span, span, span),
                     key=lambda p: (p.confirmed_at, p.index))
        conf = np.array([p.confirmed_at for p in piv])
        levels[f"_pivots{span}"] = piv
        levels[f"_pivot_ptr{span}"] = np.searchsorted(conf, np.arange(len(bars)) + 1)
    return levels


def book(rets, w=None):
    x = np.asarray(rets, float)
    if len(x) == 0:
        return {"n": 0}
    net = x - COST
    return {"n": int(len(x)), "mean": float(net.mean()), "se": float(net.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else float("nan"),
            "median": float(np.median(net)), "win": float((net > 0).mean()),
            "avg_win": float(net[net > 0].mean()) if (net > 0).any() else 0.0,
            "avg_loss": float(net[net <= 0].mean()) if (net <= 0).any() else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2025-09-20")
    ap.add_argument("--holds", default="10,20,40")
    ap.add_argument("--q", type=float, default=0.90)
    ap.add_argument("--out", default="research/results/swing_daily.json")
    a = ap.parse_args()
    cutoff = pd.Timestamp(a.cutoff, tz="UTC")
    cut_day = int((cutoff - EPOCH) / pd.Timedelta(days=1))
    syms = sorted(p.name.split("_1d_")[0] for p in FRAMES.glob("*_1d_frames.pkl"))
    print(f"{len(syms)} coins with daily frames: {', '.join(syms)}")
    frames = {s: pickle.load(open(FRAMES / f"{s}_1d_frames.pkl", "rb")) for s in syms}
    levels = {}
    for s, (bars, fr, warm) in frames.items():
        atr = _atr(bars, 14).to_numpy(float)
        levels[s] = with_pivots(bars, structural_levels(bars, atr))
    results = {}

    configs = [("structure", h) for h in [int(x) for x in a.holds.split(",")]] + [("atr", 10)]
    for geom, hold in configs:
        for side in ("long", "short"):
            name = f"{geom}|h{hold}|{side}"
            parts = {}
            for s, (bars, fr, warm) in frames.items():
                cfg = (Agent5Config(max_hold_bars=hold, geometry="structure", side=side)
                       if geom == "structure" else Agent5Config(max_hold_bars=hold, k_up=1.0, k_dn=1.0))
                ds = build_dataset(bars, cfg, warmup=warm, **fr)
                if len(ds) > 200:
                    parts[s] = ds
            dropped = scale_bound_columns(parts)
            pooled, coin = pool(parts)
            cols = [c for c in pooled.X.columns if c not in dropped]
            # the trade must RESOLVE before the cutoff to be training data
            train_m = pooled.t1 < cut_day
            test_m = pooled.positions >= cut_day
            def sub(m):
                i = np.flatnonzero(m)
                return Dataset(X=pooled.X.iloc[i].reset_index(drop=True), y=pooled.y.iloc[i].reset_index(drop=True),
                               weight=pooled.weight.iloc[i].reset_index(drop=True), t1=pooled.t1[i],
                               positions=pooled.positions[i], blocks=pooled.blocks, index=pooled.index[i]), coin.iloc[i].reset_index(drop=True)
            train, _ = sub(train_m); test, tcoin = sub(test_m)
            cfg = Agent5Config(max_hold_bars=hold, geometry=geom if geom == "structure" else "atr", side=side, k_up=1.0, k_dn=1.0)
            cv = fit_cv(train, cfg, cols)
            from agent5.metrics import shuffle_test
            sh = shuffle_test(train, cfg, cols)
            model, cols2 = fit_final(train, cfg, cols)
            p = model.predict_proba(test.X[cols2].astype(float))[:, 1]
            from sklearn.metrics import roc_auc_score
            yt = test.y.to_numpy(float); w = test.weight.to_numpy(float)
            auc_test = float(roc_auc_score(yt, p, sample_weight=w))
            # control
            rng = np.random.default_rng(0); ytr = train.y.copy(); ytr.iloc[:] = rng.permutation(ytr.to_numpy())
            scr = Dataset(X=train.X, y=ytr, weight=train.weight, t1=train.t1, positions=train.positions, blocks=train.blocks, index=train.index)
            ms, _ = fit_final(scr, cfg, cols)
            auc_ctrl = float(roc_auc_score(yt, ms.predict_proba(test.X[cols2].astype(float))[:, 1], sample_weight=w))
            # the entries: rolling top-decile PER COIN (each coin ranks against its own history)
            take = np.zeros(len(p), bool)
            for s in tcoin.unique():
                m = (tcoin == s).to_numpy()
                order = np.argsort(test.positions[m], kind="stable")
                idx = np.flatnonzero(m)[order]
                take[idx] = rolling_top(p[idx], a.q)
            # replay exits on the SAME entries, from the follower's price (that bar's close)
            side_u = side.upper()
            MODES = ("fixed", "trail", "trail5", "half", "half5")
            outs: Dict[str, List[float]] = {m: [] for m in MODES}
            bars_held: Dict[str, List[int]] = {m: [] for m in MODES}
            hows: Dict[str, List[str]] = {m: [] for m in MODES}
            n_entries = 0
            # the professionals' filter: only with the long-term trend. the
            # 200-day EMA of the close, known at the entry bar
            with_trend: Dict[str, List[bool]] = {m: [] for m in MODES}
            for j in np.flatnonzero(take):
                s = tcoin.iloc[j]; bars, _, _ = frames[s]
                # this test row's bar index in its own coin's frame
                i = bars.index.get_loc(test.index[j])
                ema = bars["close"].ewm(span=200, adjust=False).mean().iloc[i]
                trend_up = float(bars["close"].iloc[i]) > float(ema)
                aligned = trend_up if side_u == "LONG" else not trend_up
                n_entries += 1
                for mode in outs:
                    r = replay(bars, levels[s], i, side_u, hold, mode)
                    if r:
                        outs[mode].append(r["ret"]); bars_held[mode].append(r["bars"]); hows[mode].append(r["how"])
                        with_trend[mode].append(aligned)
            every = []
            for s in tcoin.unique():
                bars, _, _ = frames[s]
                for j in np.flatnonzero((tcoin == s).to_numpy()):
                    i = bars.index.get_loc(test.index[j])
                    r = replay(bars, levels[s], i, side_u, hold, "fixed")
                    if r:
                        every.append(r["ret"])
            res = {"auc_cv": cv.mean_auc, "shuffle_cv": sh, "spread_cv": cv.auc_spread,
                   "auc_test": auc_test, "auc_control": auc_ctrl, "train": len(train), "test": len(test),
                   "entries": n_entries, "every_bar_fixed": book(every)}
            for mode in outs:
                b = book(outs[mode]); b["bars_median"] = float(np.median(bars_held[mode])) if bars_held[mode] else np.nan
                b["how"] = {h: hows[mode].count(h) / max(1, len(hows[mode])) for h in ("target", "stop", "timeout", "ambiguous")}
                wt = np.asarray(with_trend[mode], bool); rets = np.asarray(outs[mode], float)
                b["with_trend"] = book(rets[wt]); b["against_trend"] = book(rets[~wt])
                res[mode] = b
            results[name] = res
            print(f"\n== {name}: CV AUC {cv.mean_auc:.3f} (shuffle {sh:.3f}, spread {cv.auc_spread:.3f}) | "
                  f"TEST AUC {auc_test:.3f} (control {auc_ctrl:.3f}) | {n_entries} entries in the test year "
                  f"(every bar, fixed exit: {res['every_bar_fixed'].get('mean', float('nan')):+.2f}%)", flush=True)
            for mode in outs:
                b = res[mode]
                if b["n"]:
                    wt, at = b["with_trend"], b["against_trend"]
                    print(f"   {mode:<6} n {b['n']:>4}  net/trade {b['mean']:+.2f}% ±{b['se']:.2f}  median {b['median']:+.2f}%  "
                          f"win {b['win']:.0%}  avg win {b['avg_win']:+.2f}%  avg loss {b['avg_loss']:+.2f}%  "
                          f"held {b['bars_median']:.0f}d  exits: target {b['how']['target']:.0%} stop {b['how']['stop']:.0%} "
                          f"timeout {b['how']['timeout']:.0%}  | with trend n {wt.get('n', 0)} {wt.get('mean', float('nan')):+.2f}% ±{wt.get('se', float('nan')):.2f} "
                          f"| against n {at.get('n', 0)} {at.get('mean', float('nan')):+.2f}%", flush=True)
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(results, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
