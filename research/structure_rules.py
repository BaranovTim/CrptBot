"""Replay decision rules over held-out structure-model scores, no refits.

Input: the pickle written by `structure_holdout.py --dump`: per (coin, side)
the test-year scores, realised P&L, weights, touches, tp/sl, and the
training out-of-fold scores. Every rule below is causal (a bar's decision
uses only scores at or before it) and is scored on the same realised
outcomes, fees included.

    python research/structure_rules.py data_cache/research_frames/holdout_scores.pkl
"""
from __future__ import annotations

import pickle
import sys

import numpy as np

COST = 0.10


def rolling_rank(p, q, trail, min_n=50):
    out = np.zeros(len(p), bool)
    for i in range(len(p)):
        past = p[max(0, i - trail):i]
        if len(past) >= min_n:
            out[i] = (past < p[i]).mean() >= q
    return out


def book(take, w, pnl, touch, win):
    if not take.any():
        return None
    tw = w[take]; net = pnl[take] - COST
    mean = float(np.average(net, weights=tw))
    se = float(np.sqrt(np.sum((tw * (net - mean)) ** 2)) / tw.sum())
    return {"trades": float(tw.sum()), "mean": mean, "se": se,
            "hit": float(np.average((touch[take] == win).astype(float), weights=tw)),
            "timeout": float(np.average((touch[take] == "timeout").astype(float), weights=tw)),
            "calls": int(np.sum(take[1:] & ~take[:-1]) + take[0])}


def summarise(name, books):
    bs = [b for b in books if b]
    if not bs:
        print(f"{name:<34} no trades"); return
    tw = sum(b["trades"] for b in bs)
    pooled = sum(b["mean"] * b["trades"] for b in bs) / tw
    pos = sum(1 for b in bs if b["mean"] > 0); sig = sum(1 for b in bs if b["mean"] > 2 * b["se"])
    neg = sum(1 for b in bs if b["mean"] < -2 * b["se"])
    print(f"{name:<34} books {len(bs):>2}  pooled {pooled:+.3f}%  mean {np.mean([b['mean'] for b in bs]):+.3f}%  "
          f"hit {np.mean([b['hit'] for b in bs]):.1%}  timeout {np.mean([b['timeout'] for b in bs]):.1%}  "
          f"{pos} pos / {sig} sig+ / {neg} sig-  calls {sum(b['calls'] for b in bs)}")


def main():
    rows = pickle.load(open(sys.argv[1], "rb"))
    print(f"{len(rows)} (coin, side) rows; test bars {sum(len(r['p']) for r in rows)}")
    rules = {}
    for trail in (540, 1500, 4000):
        for q in (0.90, 0.95, 0.97):
            rules[f"rolling rank q{q} trail{trail}"] = lambda r, q=q, trail=trail: rolling_rank(r["p"], q, trail)
    for q in (0.90, 0.95):
        rules[f"train-OOF quantile q{q}"] = lambda r, q=q: r["p"] >= np.nanquantile(r["p_train_oof"], q)
    for cut in (0.55, 0.58, 0.60, 0.62):
        rules[f"absolute p >= {cut}"] = lambda r, cut=cut: r["p"] >= cut
    # expected value on the calibrated p with this bar's own tp/sl
    for th in (0.0, 0.05, 0.10):
        rules[f"EV > {th:.2f}% (shipped ATR rule)"] = lambda r, th=th: (r["p"] * r["tp"] - (1 - r["p"]) * r["sl"] - COST) > th
    # rank AND a floor on p: the model must be confident in absolute terms too
    for cut in (0.55, 0.58):
        rules[f"rolling q0.90 trail540 & p>={cut}"] = lambda r, cut=cut: rolling_rank(r["p"], 0.90, 540) & (r["p"] >= cut)
    rules["every bar"] = lambda r: np.ones(len(r["p"]), bool)
    for name, fn in rules.items():
        summarise(name, [book(fn(r), r["w"], r["pnl"], r["touch"], r["win"]) for r in rows])
    # by side, for the two rules that matter
    for side in ("long", "short"):
        sub = [r for r in rows if r["side"] == side]
        print(f"\n-- {side} only")
        summarise("every bar", [book(np.ones(len(r["p"]), bool), r["w"], r["pnl"], r["touch"], r["win"]) for r in sub])
        summarise("rolling rank q0.90 trail540", [book(rolling_rank(r["p"], 0.90, 540), r["w"], r["pnl"], r["touch"], r["win"]) for r in sub])
        summarise("absolute p >= 0.60", [book(r["p"] >= 0.60, r["w"], r["pnl"], r["touch"], r["win"]) for r in sub])
    # what the score distribution did: train OOF vs test
    print("\nscore drift (median p): train OOF -> test")
    for r in rows:
        print(f"  {r['symbol']:<9} {r['side']:<6} {np.nanmedian(r['p_train_oof']):.3f} -> {np.nanmedian(r['p']):.3f}   "
              f"test p90 {np.nanquantile(r['p'], .9):.3f}  base rate test {np.average((r['touch']==r['win']).astype(float), weights=r['w']):.3f}")


if __name__ == "__main__":
    main()
