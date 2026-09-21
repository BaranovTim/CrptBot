"""The confirmation for research/structure_levels.py: fit before a date,
score the year after, once.

The sweep chose its placement with the whole history in view -- the
multiple-testing trap the pipeline's own notes warn about. So this fits the
chosen geometry on everything before CUTOFF, takes the confidence threshold
from the TRAINING half's out-of-fold scores (the 90th and 95th percentile),
and scores the trades that threshold would have taken in the year after,
which nothing in the choice ever saw. A scrambled-label control runs the
identical recipe.

    python research/structure_holdout.py CACHE [--cutoff 2025-09-20]
        [--intervals 4h] [--holds 16] [--placements at,second_at]
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config                             # noqa: E402
from agent5.dataset import Dataset, build_dataset           # noqa: E402
from agent5.labels import _atr                              # noqa: E402
from agent5.model import fit_cv, fit_final                  # noqa: E402
from core import barriers_for                               # noqa: E402
from research.four_r import COST, frames_for                # noqa: E402
from research.structure_levels import (Placement, barrier_labels,  # noqa: E402
                                       geometry_levels, realised,
                                       structural_levels)

PLACEMENTS = {
    "front_beyond": (0.10, 0.25, 1),
    "at": (0.0, 0.0, 1),
    "second_at": (0.0, 0.25, 2),
}


def subset(ds: Dataset, mask: np.ndarray) -> Dataset:
    idx = np.flatnonzero(mask)
    return Dataset(X=ds.X.iloc[idx].reset_index(drop=True),
                   y=ds.y.iloc[idx].reset_index(drop=True),
                   weight=ds.weight.iloc[idx].reset_index(drop=True),
                   t1=ds.t1[idx], positions=ds.positions[idx],
                   blocks=ds.blocks, index=ds.index[idx])


def _rolling_top(p: np.ndarray, q: float, trail: int = 540, min_n: int = 50) -> np.ndarray:
    """True where the score is at or above the q-quantile of the previous
    `trail` scores -- the rule monitor.py serves, replayed causally."""
    out = np.zeros(len(p), bool)
    for i in range(len(p)):
        past = p[max(0, i - trail):i]
        if len(past) >= min_n:
            out[i] = (past < p[i]).mean() >= q
    return out


def book(take, w, pnl, touch, win):
    if not take.any():
        return {"trades": 0}
    tw = w[take]; net = pnl[take] - COST
    mean = float(np.average(net, weights=tw))
    se = float(np.sqrt(np.sum((tw * (net - mean)) ** 2)) / tw.sum())
    return {"trades": float(tw.sum()), "rows": int(take.sum()),
            "hit_rate": float(np.average((touch[take] == win).astype(float), weights=tw)),
            "pnl_per_trade": mean, "pnl_se": se,
            "calls": int(np.sum(take[1:] & ~take[:-1]) + take[0])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache")
    ap.add_argument("--coins", default="BTC,ETH,SOL,DOGE,XRP")
    ap.add_argument("--intervals", default="4h")
    ap.add_argument("--holds", default="16")
    ap.add_argument("--placements", default="at,second_at")
    ap.add_argument("--cutoff", default="2025-09-20")
    ap.add_argument("--out", default="research/results/structure_holdout.json")
    ap.add_argument("--funding", action="store_true",
                    help="fill the regime block's funding_z from the cached settled rates")
    ap.add_argument("--dump", default="", help="pickle per-bar test scores here, for rule replays")
    ap.add_argument("--geometry", default="structure", help="structure | atr (the shipped ±1 ATR, as a baseline)")
    a = ap.parse_args()
    dump_rows: list = []
    cache = Path(a.cache)
    cutoff = pd.Timestamp(a.cutoff, tz="UTC")
    coins = [c.strip().upper() + ("" if c.strip().upper().endswith("USDT") else "USDT")
             for c in a.coins.split(",") if c.strip()]
    results = {}
    for interval in [i.strip() for i in a.intervals.split(",")]:
        for hold in [int(h) for h in a.holds.split(",")]:
            for name in [p.strip() for p in a.placements.split(",")]:
                Placement.tp_shave, Placement.sl_buffer, Placement.target_rank = PLACEMENTS[name]
                print(f"\n=== {interval} hold {hold} placement {name}: "
                      f"fit < {cutoff.date()}, score after", flush=True)
                rows = []
                for sym in coins:
                    got = frames_for(cache, sym, interval)
                    if got is None:
                        continue
                    bars, frames, warm = got
                    frames = dict(frames)
                    if a.funding:
                        from marketdata.funding import align_to_bars, load_funding
                        frames["funding"] = align_to_bars(load_funding(sym, refresh=False), bars.index)
                    atr = _atr(bars, 14).to_numpy(float)
                    lv = structural_levels(bars, atr)
                    for side in ("long", "short"):
                        upper, lower, _ = geometry_levels(bars, atr, lv, a.geometry, side)
                        lab = barrier_labels(bars, upper, lower, hold, side)
                        cfg = Agent5Config(max_hold_bars=hold, k_up=1.0, k_dn=1.0)
                        ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **frames)
                        pos = ds.positions.astype(int)
                        pnl = realised(bars, lab, side)[pos]
                        touch = lab.touch.to_numpy(object)[pos]
                        win = "upper" if side == "long" else "lower"
                        # a training label may not resolve inside the test
                        # period: its t1 must be before the cutoff too
                        t1_time = bars.index[np.minimum(ds.t1.astype(int), len(bars) - 1)]
                        train_m = np.asarray(t1_time < cutoff)
                        test_m = np.asarray(ds.index >= cutoff)
                        train, test = subset(ds, train_m), subset(ds, test_m)
                        if len(train) < 500 or len(test) < 200:
                            continue
                        cols = list(ds.X.columns)
                        cv = fit_cv(train, cfg, cols)
                        q85, q90, q95 = np.nanquantile(cv.oof, [0.85, 0.90, 0.95])
                        model, cols = fit_final(train, cfg, cols)
                        p = model.predict_proba(test.X[cols].astype(float))[:, 1]
                        w = test.weight.to_numpy(float)
                        # control: scrambled labels, identical recipe
                        rng = np.random.default_rng(0)
                        ytr = train.y.copy(); ytr.iloc[:] = rng.permutation(ytr.to_numpy())
                        scr = Dataset(X=train.X, y=ytr, weight=train.weight, t1=train.t1,
                                      positions=train.positions, blocks=train.blocks,
                                      index=train.index)
                        cvs = fit_cv(scr, cfg, cols)
                        s90 = np.nanquantile(cvs.oof, 0.90)
                        ms, _ = fit_final(scr, cfg, cols)
                        ps = ms.predict_proba(test.X[cols].astype(float))[:, 1]
                        from sklearn.metrics import roc_auc_score
                        yt = test.y.to_numpy(float)
                        row = {
                            "symbol": sym, "side": side,
                            "train": int(len(train)), "test": int(len(test)),
                            "auc_test": float(roc_auc_score(yt, p, sample_weight=w)),
                            "auc_control": float(roc_auc_score(yt, ps, sample_weight=w)),
                            "top10": book(p >= q90, w, pnl[test_m], touch[test_m], win),
                            "top5": book(p >= q95, w, pnl[test_m], touch[test_m], win),
                            "top15": book(p >= q85, w, pnl[test_m], touch[test_m], win),
                            # the SERVED rule: rank against the trailing 540 test-period
                            # scores rather than a fixed training cut
                            "roll10": book(_rolling_top(p, 0.90), w, pnl[test_m], touch[test_m], win),
                            "roll5": book(_rolling_top(p, 0.95), w, pnl[test_m], touch[test_m], win),
                            "roll15": book(_rolling_top(p, 0.85), w, pnl[test_m], touch[test_m], win),
                            "control_top10": book(ps >= s90, w, pnl[test_m], touch[test_m], win),
                            "all": book(np.ones(len(p), bool), w, pnl[test_m], touch[test_m], win),
                        }
                        rows.append(row)
                        if a.dump:
                            import pickle
                            dump_rows.append({"symbol": sym, "side": side, "t": test.index,
                                              "p": p, "p_train_oof": cv.oof, "t_train": train.index,
                                              "w": w, "pnl": pnl[test_m], "touch": touch[test_m],
                                              "tp": lab.tp_pct.to_numpy(float)[pos][test_m],
                                              "sl": lab.sl_pct.to_numpy(float)[pos][test_m], "win": win})
                        t10, c10 = row["top10"], row["control_top10"]
                        print(f"  {sym:<9} {side:<6} test AUC {row['auc_test']:.3f} (control {row['auc_control']:.3f}) | "
                              f"top10: {t10.get('calls', 0):>3} calls hit {t10.get('hit_rate', float('nan')):.1%} "
                              f"net {t10.get('pnl_per_trade', float('nan')):+.3f}%±{t10.get('pnl_se', float('nan')):.3f} | "
                              f"control top10 net {c10.get('pnl_per_trade', float('nan')):+.3f}% | "
                              f"all rows net {row['all']['pnl_per_trade']:+.3f}%", flush=True)
                key = f"{interval}|h{hold}|{name}"
                results[key] = rows
                tops = [r["top10"] for r in rows if r["top10"].get("trades")]
                ctrl = [r["control_top10"] for r in rows if r["control_top10"].get("trades")]
                print(f"  => {name}: test AUC mean {np.mean([r['auc_test'] for r in rows]):.3f} "
                      f"(control {np.mean([r['auc_control'] for r in rows]):.3f}) | top10 net mean "
                      f"{np.mean([t['pnl_per_trade'] for t in tops]):+.3f}% "
                      f"({sum(1 for t in tops if t['pnl_per_trade'] > 0)}/{len(tops)} positive, "
                      f"{sum(1 for t in tops if t['pnl_per_trade'] > 2 * t['pnl_se'])} above 2se) | "
                      f"control top10 mean {np.mean([c['pnl_per_trade'] for c in ctrl]):+.3f}%", flush=True)
                for key in ("roll5", "roll10", "roll15", "top5", "top15"):
                    bs = [r[key] for r in rows if r[key].get("trades")]
                    if bs:
                        tw = sum(b["trades"] for b in bs)
                        pooled = sum(b["pnl_per_trade"] * b["trades"] for b in bs) / tw
                        print(f"     {key:<7} books {len(bs):>2}: mean of books {np.mean([b['pnl_per_trade'] for b in bs]):+.3f}%  "
                              f"pooled {pooled:+.3f}%  hit {np.mean([b['hit_rate'] for b in bs]):.1%}  "
                              f"{sum(1 for b in bs if b['pnl_per_trade'] > 0)} positive, "
                              f"{sum(1 for b in bs if b['pnl_per_trade'] > 2 * b['pnl_se'])} above 2se, "
                              f"{sum(1 for b in bs if b['pnl_per_trade'] < -2 * b['pnl_se'])} below; "
                              f"calls {sum(b['calls'] for b in bs)}", flush=True)
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(json.dumps(results, indent=1))
    if a.dump:
        import pickle
        pickle.dump(dump_rows, open(a.dump, "wb"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
