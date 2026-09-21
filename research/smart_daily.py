"""Does smart-money confluence hold on the DAILY, trailed entries?

The 4h overlay was measured on 4h calls held to their levels. A daily entry
is a different animal: no time limit, the stop trails the swings, and a
24h window is a single bar. This asks the same three questions on the
daily swing entries (research/swing_daily.py, the served rule):

  1. On the model's daily calls, split by the followed traders' net entries
     on that coin in the last 24h / 72h: agree, silent, disagree -- under
     the trailed exit the app uses, and the fixed one for reference.
  2. Is the split the same on the year before (a second, independent year)?
     The fills only cover Aug 2025 onward, so no: the second year cannot be
     tested. Said plainly rather than skipped silently.
  3. Would a followed trader's entry ALONE, with no model call, be worth a
     trailed daily entry? (The overlay never creates calls; this says
     whether that rule is leaving money on the table.)

    python research/smart_daily.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent5 import Agent5Config                                  # noqa: E402
from agent5.dataset import Dataset, build_dataset               # noqa: E402
from agent5.labels import _atr                                  # noqa: E402
from agent5.model import fit_final                              # noqa: E402
from agent5.structure import structural_levels                  # noqa: E402
from research.pooled_daily import EPOCH, pool, scale_bound_columns  # noqa: E402
from research.smart_feature import FILLS, cohort_by_day, features_for  # noqa: E402
from research.swing_daily import COST, FRAMES, replay, rolling_top, with_pivots  # noqa: E402
from research.trader_patterns import load_trades, position_trades  # noqa: E402


def book(x):
    x = np.asarray(x, float) - COST
    if len(x) == 0:
        return "   none"
    se = x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else float("nan")
    return f"n {len(x):4d}  net {x.mean():+.2f}% ±{se:.2f}  median {np.median(x):+.2f}%  win {(x > 0).mean():.0%}"


def main() -> int:
    cutoff = pd.Timestamp("2025-09-20", tz="UTC")
    cut_day = int((cutoff - EPOCH) / pd.Timedelta(days=1))
    pos = position_trades(load_trades(FILLS)); pos = pos[pos["symbol"] != ""]
    cohort = cohort_by_day(pos)
    first = min(d.min() for d in cohort.values())
    print(f"cohort definable from {first.date()}; {len(cohort)} traders ever qualify")

    syms = sorted(p.name.split("_1d_")[0] for p in FRAMES.glob("*_1d_frames.pkl"))
    frames = {s: pickle.load(open(FRAMES / f"{s}_1d_frames.pkl", "rb")) for s in syms}
    levels, feats = {}, {}
    for s, (bars, fr, warm) in frames.items():
        atr = _atr(bars, 14).to_numpy(float)
        levels[s] = with_pivots(bars, structural_levels(bars, atr))
        feats[s] = features_for(s, bars.index, pos, cohort)

    hold = 10
    for side in ("long", "short"):
        parts = {}
        for s, (bars, fr, warm) in frames.items():
            ds = build_dataset(bars, Agent5Config(max_hold_bars=hold, geometry="structure", side=side), warmup=warm, **fr)
            if len(ds) > 200:
                parts[s] = ds
        dropped = scale_bound_columns(parts)
        pooled, coin = pool(parts)
        cols = [c for c in pooled.X.columns if c not in dropped]
        train_m = pooled.t1 < cut_day; test_m = pooled.positions >= cut_day

        def sub(m):
            i = np.flatnonzero(m)
            return Dataset(X=pooled.X.iloc[i].reset_index(drop=True), y=pooled.y.iloc[i].reset_index(drop=True),
                           weight=pooled.weight.iloc[i].reset_index(drop=True), t1=pooled.t1[i],
                           positions=pooled.positions[i], blocks=pooled.blocks, index=pooled.index[i]), coin.iloc[i].reset_index(drop=True)
        train, _ = sub(train_m); test, tcoin = sub(test_m)
        cfg = Agent5Config(max_hold_bars=hold, geometry="structure", side=side)
        model, cols2 = fit_final(train, cfg, cols)
        p = model.predict_proba(test.X[cols2].astype(float))[:, 1]
        take = np.zeros(len(p), bool)
        for s in tcoin.unique():
            m = (tcoin == s).to_numpy(); order = np.argsort(test.positions[m], kind="stable")
            idx = np.flatnonzero(m)[order]; take[idx] = rolling_top(p[idx], 0.90)
        sgn = 1.0 if side == "long" else -1.0
        side_u = side.upper()

        # 1. the model's calls, split by confluence
        rows = []
        for j in np.flatnonzero(take):
            s = tcoin.iloc[j]; bars = frames[s][0]; i = bars.index.get_loc(test.index[j])
            if test.index[j] < first:
                continue
            f = feats[s].iloc[i]
            r = {"f24": sgn * f["smart_net_24h"], "f72": sgn * f["smart_net_72h"], "expo": sgn * f["smart_exposure"]}
            for mode in ("trail", "half", "fixed"):
                rr = replay(bars, levels[s], i, side_u, hold, mode)
                r[mode] = rr["ret"] if rr else np.nan
            rows.append(r)
        D = pd.DataFrame(rows).dropna()
        print(f"\n== daily {side} calls in the test year with a definable cohort: {len(D)}")
        for c in ("f24", "f72", "expo"):
            print(f"   {c}: non-zero on {100*(D[c] != 0).mean():.0f}% of calls")
            for mode in ("trail", "half", "fixed"):
                print(f"      {mode:<6} agrees    {book(D[D[c] > 0][mode])}")
                print(f"      {mode:<6} silent    {book(D[D[c] == 0][mode])}")
                print(f"      {mode:<6} disagrees {book(D[D[c] < 0][mode])}")

        # 3. a followed trader's entry alone, no model call
        alone = []
        for j in range(len(test)):
            if take[j]:
                continue
            s = tcoin.iloc[j]; bars = frames[s][0]; i = bars.index.get_loc(test.index[j])
            if test.index[j] < first:
                continue
            f = sgn * feats[s]["smart_net_24h"].iloc[i]
            if f > 0:
                rr = replay(bars, levels[s], i, side_u, hold, "trail")
                if rr:
                    alone.append(rr["ret"])
        print(f"   a followed trader's {side} entry ALONE (no model call), trailed: {book(alone)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
