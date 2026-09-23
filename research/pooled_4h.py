"""ONE 4h structure model trained on every coin at once.

THE HYPOTHESIS, AND WHY IT WAS NEVER TESTED
    Pooling is what turned the daily model from "0 of 15 beat their
    shuffled control" into the shipped swing strategy. On 4h it was never
    tried: each coin carries its own pair of models fitted on its own
    ~12,000 bars, and the shipped evaluation only ever covered five coins.
    Nothing about 4h made pooling inapplicable -- it simply was not asked.

WHAT CHANGES, AND WHAT DOES NOT
    Same features, same structure barriers, same 16-bar hold, same purged
    evaluation, same shuffled control, same rank rule. The only change is
    whether the coins are fitted together. A change in the answer is
    attributable to pooling and to nothing else.

THE LEAK POOLING INTRODUCES
    Every coin's rows are placed on ONE shared 4h-bar axis, so a fold is a
    contiguous span of real time across all coins at once and BTC's March
    can never train a model tested on ETH's March. `research/pooled_daily`
    does the same on a day axis; that axis is reused here and then put back
    into 4h bars, because on 4h a day axis also over-purges (a label that
    resolves 16 BARS ahead would be treated as resolving 16 days ahead).

THE RULE POOLING MAKES POSSIBLE
    A per-coin model's score means nothing outside its own coin, which is
    why the served rule ranks each score against that coin's own trailing
    window. A pooled model's score means the same thing everywhere, so the
    coins can be ranked AGAINST EACH OTHER -- "which of the thirty (fifteen
    coins x two sides) is the best trade right now" -- and an absolute
    threshold becomes meaningful. Both are reported.

    python research/pooled_4h.py [--cutoff 2025-09-20] [--end 2026-09-20]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config                              # noqa: E402
from agent5.dataset import Dataset, build_dataset           # noqa: E402
from agent5.model import fit_cv, fit_final                   # noqa: E402
from agent5.structure import structure_barrier               # noqa: E402
from research.pooled_daily import pool, scale_bound_columns  # noqa: E402
from research.structure_rules import rolling_rank            # noqa: E402

FRAMES = Path("data_cache/research_frames")
COST = 0.10
HOLD = 16
EPOCH = pd.Timestamp("2015-01-01", tz="UTC")
# the five coins the shipped per-coin models were measured on, so the
# comparison can be like for like as well as universe-wide
SHIPPED = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"}


def stat(net, w) -> dict:
    net = np.asarray(net, float); w = np.asarray(w, float)
    if len(net) == 0:
        return {"n": 0}
    mean = float(np.average(net, weights=w))
    se = float(np.sqrt(np.sum((w * (net - mean)) ** 2)) / w.sum())
    return {"n": int(len(net)), "mean": mean, "se": se,
            "sigma": float(mean / se) if se else 0.0}


def show(name: str, s: dict) -> None:
    print(f"   {name:<40} " + ("none" if not s.get("n") else
          f"n {s['n']:5d}  net {s['mean']:+.3f}% +-{s['se']:.3f} ({s['sigma']:+.1f}s)"))


def realised(lab, bars, index, side) -> np.ndarray:
    """% per row from the label: the barrier that was touched, or the mark
    at the time exit. The same arithmetic the other structure studies use."""
    loc = bars.index.get_indexer(pd.DatetimeIndex(index))
    touch = lab.touch.to_numpy(object)[loc]
    tp = lab.tp_pct.to_numpy(float)[loc]; sl = lab.sl_pct.to_numpy(float)[loc]
    t1 = lab.t1.to_numpy(float)[loc]; close = bars["close"].to_numpy(float)
    sgn = 1.0 if side == "long" else -1.0
    win = "upper" if side == "long" else "lower"
    lose = "lower" if side == "long" else "upper"
    ex = sgn * 100 * (close[np.minimum(t1.astype(int), len(close) - 1)] / close[loc] - 1)
    return np.where(touch == win, tp,
                    np.where((touch == lose) | (touch == "ambiguous"), -sl, ex))


def one_side(side: str, cut: int, end: int, syms) -> pd.DataFrame:
    cfg = Agent5Config(max_hold_bars=HOLD, geometry="structure", side=side)
    parts, meta = {}, {}
    for s in syms:
        bars, fr, warm = pickle.load(open(FRAMES / f"{s}_4h_frames.pkl", "rb"))
        lab = structure_barrier(bars, cfg)
        ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **fr)
        if len(ds) >= 500:
            parts[s] = ds
            meta[s] = (lab, bars)
    dropped = scale_bound_columns(parts)
    pooled, coin = pool(parts)
    cols = [c for c in pooled.X.columns if c not in dropped]
    idx = pd.DatetimeIndex(pooled.index)
    pos = np.asarray((idx - EPOCH) / pd.Timedelta(hours=4), dtype=np.int64)
    o = np.argsort(pos, kind="stable")
    pooled = Dataset(X=pooled.X.iloc[o].reset_index(drop=True),
                     y=pooled.y.iloc[o].reset_index(drop=True),
                     weight=pooled.weight.iloc[o].reset_index(drop=True),
                     t1=pos[o] + HOLD, positions=pos[o],
                     blocks=pooled.blocks, index=idx[o])
    coin = coin.iloc[o].reset_index(drop=True)

    def sub(m):
        i = np.flatnonzero(m)
        return Dataset(X=pooled.X.iloc[i].reset_index(drop=True),
                       y=pooled.y.iloc[i].reset_index(drop=True),
                       weight=pooled.weight.iloc[i].reset_index(drop=True),
                       t1=pooled.t1[i], positions=pooled.positions[i],
                       blocks=pooled.blocks, index=pooled.index[i]), coin.iloc[i].reset_index(drop=True)

    train, _ = sub(pooled.t1 < cut)
    test, tcoin = sub((pooled.positions >= cut) & (pooled.positions < end))
    cv = fit_cv(train, cfg, cols)
    # the gate's control: the identical recipe with the labels scrambled
    shuffled = Dataset(X=train.X,
                       y=pd.Series(np.random.default_rng(7).permutation(train.y.to_numpy())),
                       weight=train.weight, t1=train.t1, positions=train.positions,
                       blocks=train.blocks, index=train.index)
    control = fit_cv(shuffled, cfg, cols)
    model, cols2 = fit_final(train, cfg, cols)
    p = model.predict_proba(test.X[cols2].astype(float))[:, 1]
    from sklearn.metrics import roc_auc_score
    y = test.y.to_numpy(float); w = test.weight.to_numpy(float)
    oot = float(roc_auc_score(y, p, sample_weight=w))
    print(f"  {side:<6} {len(parts)} coins, {len(train):,} train / {len(test):,} test"
          f"   CV {cv.mean_auc:.3f} +-{cv.auc_spread:.3f}   control {control.mean_auc:.3f}"
          f"   OUT OF TIME {oot:.3f}", flush=True)

    pnl = np.full(len(test), np.nan)
    for s in tcoin.unique():
        m = (tcoin == s).to_numpy()
        pnl[np.flatnonzero(m)] = realised(*meta[s], test.index[m], side)
    take = np.zeros(len(p), bool)
    for s in tcoin.unique():
        m = (tcoin == s).to_numpy()
        i = np.flatnonzero(m)[np.argsort(test.positions[m], kind="stable")]
        take[i] = rolling_rank(p[i], 0.90, 540)
    return pd.DataFrame({"pos": test.positions, "t": pd.DatetimeIndex(test.index),
                         "coin": tcoin.to_numpy(), "side": side, "p": p, "pnl": pnl,
                         "w": w, "rank_take": take, "oot_auc": oot}).dropna(subset=["pnl"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2025-09-20", help="fit before this")
    ap.add_argument("--end", default="2026-09-20", help="score until this")
    ap.add_argument("--out", default="research/results/pooled_4h.json")
    a = ap.parse_args()
    cut = int((pd.Timestamp(a.cutoff, tz="UTC") - EPOCH) / pd.Timedelta(hours=4))
    end = int((pd.Timestamp(a.end, tz="UTC") - EPOCH) / pd.Timedelta(hours=4))
    syms = sorted(p.name.split("_4h_")[0] for p in FRAMES.glob("*_4h_frames.pkl"))
    print(f"{len(syms)} coins, fit < {a.cutoff}, scored {a.cutoff} -> {a.end}")

    D = pd.concat([one_side(s, cut, end, syms) for s in ("long", "short")], ignore_index=True)
    res = {"cutoff": a.cutoff, "end": a.end, "coins": len(syms),
           "auc": {s: float(g["oot_auc"].iloc[0]) for s, g in D.groupby("side")}}

    print("\n  the served rule, on pooled scores")
    r = D[D["rank_take"]]
    res["rank_all"] = stat(r["pnl"] - COST, r["w"]); show("per-coin trailing rank >= 0.90", res["rank_all"])
    rs = r[r["coin"].isin(SHIPPED)]
    res["rank_shipped5"] = stat(rs["pnl"] - COST, rs["w"])
    show("...restricted to the shipped five coins", res["rank_shipped5"])

    print("\n  what one shared score buys: comparable across coins")
    for thr in (0.55, 0.60, 0.65):
        b = D[D["p"] >= thr]
        res[f"abs_{thr}"] = stat(b["pnl"] - COST, b["w"])
        show(f"pooled score >= {thr:.2f} (no rank at all)", res[f"abs_{thr}"])
    for k in (1, 2, 3):
        b = D.sort_values("p", ascending=False).groupby("pos").head(k)
        res[f"best_{k}"] = stat(b["pnl"] - COST, b["w"])
        show(f"best {k} of the {2*len(syms)} rows, per bar", res[f"best_{k}"])

    print("\n  concentration (score >= 0.60): one coin or one month can make any of this")
    sel = D[D["p"] >= 0.60]
    if len(sel):
        bycoin = sel.groupby("coin")["pnl"].agg(["size", "mean"])
        bym = sel.assign(m=sel["t"].dt.to_period("M")).groupby("m")["pnl"].mean() - COST
        res["coins_positive"] = int((bycoin["mean"] - COST > 0).sum())
        res["months_positive"] = [int((bym > 0).sum()), int(len(bym))]
        print(f"   {res['coins_positive']}/{len(bycoin)} coins positive, "
              f"{res['months_positive'][0]}/{res['months_positive'][1]} months positive, "
              f"biggest single coin {100 * bycoin['size'].max() / len(sel):.0f}% of the rows")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
