"""Does knowing what BITCOIN is doing improve an altcoin's model?

Every model reads only its own coin. In crypto that is a strange blindness:
altcoin returns are dominated by Bitcoin's, and Bitcoin's own structure
tends to lead. This adds a block of BTC context to each altcoin's features
and asks the standard question -- fit before the cutoff, score the year
after, with and without -- on the 4h structure models.

THE BLOCK (all from BTCUSDT's own closed 4h bars, aligned on close time)
    btc_ret_4h / 24h / 7d       Bitcoin's own momentum
    btc_ema20 / 50 / 200_atr    where Bitcoin sits against its averages
    btc_rsi14, btc_rv_pctile    its state
    btc_above_200               its long-term trend (the daily gate's cousin)
    btc_range7d_pos             where in its 7-day range
    btc_broke_high / broke_low  did Bitcoin just take out its last confirmed
                                swing (structure state, causal pivots)
    rel_ret_24h / 7d            the coin's return MINUS Bitcoin's -- relative
                                strength, the thing alt traders actually watch
    beta_30, corr_30            rolling 30-bar beta and correlation to BTC

    python research/btc_context.py [--coins ETH,SOL,...] [--cutoff 2025-09-20]
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

from agent1.pivots import HIGH, find_pivots                  # noqa: E402
from agent5 import Agent5Config                              # noqa: E402
from agent5.dataset import Dataset, build_dataset           # noqa: E402
from agent5.labels import _atr                               # noqa: E402
from agent5.model import fit_cv, fit_final                   # noqa: E402
from research.structure_rules import rolling_rank            # noqa: E402

FRAMES = Path("data_cache/research_frames")
COST = 0.10


def btc_block(btc: pd.DataFrame) -> pd.DataFrame:
    c = btc["close"]; h = btc["high"]; l = btc["low"]
    atr = _atr(btc, 14)
    lr = np.log(c).diff()
    out = pd.DataFrame(index=btc.index)
    out["btc_ret_4h"] = 100 * c.pct_change(1)
    out["btc_ret_24h"] = 100 * c.pct_change(6)
    out["btc_ret_7d"] = 100 * c.pct_change(42)
    for n in (20, 50, 200):
        out[f"btc_ema{n}_atr"] = (c - c.ewm(span=n, adjust=False).mean()) / atr
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["btc_rsi14"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    rv = lr.rolling(6).std()
    out["btc_rv_pctile"] = rv.rolling(6 * 30, min_periods=30).rank(pct=True)
    out["btc_above_200"] = (c > c.ewm(span=200, adjust=False).mean()).astype(float)
    hi, lo = h.rolling(42).max(), l.rolling(42).min()
    out["btc_range7d_pos"] = (c - lo) / (hi - lo).replace(0, np.nan)
    # structure state: is BTC above its last CONFIRMED swing high / below its last swing low
    piv = sorted(find_pivots(h, l, 3, 3, 3), key=lambda p: (p.confirmed_at, p.index))
    last_hi = np.full(len(btc), np.nan); last_lo = np.full(len(btc), np.nan)
    k = 0; cur_hi = cur_lo = np.nan
    for t in range(len(btc)):
        while k < len(piv) and piv[k].confirmed_at <= t:
            if piv[k].kind == HIGH:
                cur_hi = piv[k].price
            else:
                cur_lo = piv[k].price
            k += 1
        last_hi[t] = cur_hi; last_lo[t] = cur_lo
    cv = c.to_numpy(float)
    out["btc_broke_high"] = (cv > last_hi).astype(float)
    out["btc_broke_low"] = (cv < last_lo).astype(float)
    out["btc_dist_last_high_atr"] = (last_hi - cv) / atr.to_numpy(float)
    out["btc_dist_last_low_atr"] = (cv - last_lo) / atr.to_numpy(float)
    return out


def relative_block(alt: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    a = alt["close"]; b = btc["close"].reindex(alt.index)
    la, lb = np.log(a).diff(), np.log(b).diff()
    out = pd.DataFrame(index=alt.index)
    out["rel_ret_24h"] = 100 * (a.pct_change(6) - b.pct_change(6))
    out["rel_ret_7d"] = 100 * (a.pct_change(42) - b.pct_change(42))
    cov = la.rolling(30).cov(lb); var = lb.rolling(30).var()
    out["beta_30"] = cov / var.replace(0, np.nan)
    out["corr_30"] = la.rolling(30).corr(lb)
    return out


def subset(ds: Dataset, m):
    i = np.flatnonzero(m)
    return Dataset(X=ds.X.iloc[i].reset_index(drop=True), y=ds.y.iloc[i].reset_index(drop=True),
                   weight=ds.weight.iloc[i].reset_index(drop=True), t1=ds.t1[i],
                   positions=ds.positions[i], blocks=ds.blocks, index=ds.index[i])


def book(take, w, pnl, touch, win):
    if not take.any():
        return {"trades": 0}
    tw = w[take]; net = pnl[take] - COST
    mean = float(np.average(net, weights=tw))
    se = float(np.sqrt(np.sum((tw * (net - mean)) ** 2)) / tw.sum())
    return {"trades": float(tw.sum()), "mean": mean, "se": se,
            "hit": float(np.average((touch[take] == win).astype(float), weights=tw)),
            "calls": int(np.sum(take[1:] & ~take[:-1]) + take[0])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default="ETH,SOL,DOGE,XRP,ADA,BNB,ZEC,HYPE,ENA,UNI,NEAR,SUI,1000PEPE,ARB")
    ap.add_argument("--cutoff", default="2025-09-20")
    ap.add_argument("--out", default="research/results/btc_context.json")
    a = ap.parse_args()
    cutoff = pd.Timestamp(a.cutoff, tz="UTC")
    btc_bars = pickle.load(open(FRAMES / "BTCUSDT_4h_frames.pkl", "rb"))[0]
    B = btc_block(btc_bars)
    results = {}
    from sklearn.metrics import roc_auc_score
    from agent5.structure import structure_barrier
    for coin in [c.strip().upper() for c in a.coins.split(",")]:
        sym = coin + ("" if coin.endswith("USDT") else "USDT")
        fp = FRAMES / f"{sym}_4h_frames.pkl"
        if not fp.exists():
            continue
        bars, frames, warm = pickle.load(open(fp, "rb"))
        ctx = pd.concat([B.reindex(bars.index), relative_block(bars, btc_bars)], axis=1)
        for side in ("long", "short"):
            cfg = Agent5Config(max_hold_bars=16, geometry="structure", side=side)
            lab = structure_barrier(bars, cfg)
            row = {}
            for variant in ("base", "with_btc"):
                fr = dict(frames)
                if variant == "with_btc":
                    # ride along in agent2's frame: the dataset builder takes named
                    # blocks only, and this is a test, not the production wiring
                    fr["agent2"] = pd.concat([fr["agent2"], ctx], axis=1)
                ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **fr)
                pos = ds.positions.astype(int)
                touch = lab.touch.to_numpy(object)[pos]
                close = bars["close"].to_numpy(float)
                # realised % per row from the label
                tp = lab.tp_pct.to_numpy(float)[pos]; sl = lab.sl_pct.to_numpy(float)[pos]
                t1 = lab.t1.to_numpy(float)[pos]
                s = 1.0 if side == "long" else -1.0
                win = "upper" if side == "long" else "lower"
                lose = "lower" if side == "long" else "upper"
                exit_ret = s * 100 * (close[t1.astype(int)] / close[pos] - 1)
                pnl = np.where(touch == win, tp, np.where((touch == lose) | (touch == "ambiguous"), -sl, exit_ret))
                t1_time = bars.index[np.minimum(t1.astype(int), len(bars) - 1)]
                train = subset(ds, np.asarray(t1_time < cutoff)); test = subset(ds, np.asarray(ds.index >= cutoff))
                test_m = np.asarray(ds.index >= cutoff)
                if len(train) < 500 or len(test) < 200:
                    continue
                cols = list(ds.X.columns)
                cv = fit_cv(train, cfg, cols)
                model, cols2 = fit_final(train, cfg, cols)
                p = model.predict_proba(test.X[cols2].astype(float))[:, 1]
                w = test.weight.to_numpy(float); yt = test.y.to_numpy(float)
                take = rolling_rank(p, 0.85, 540)
                row[variant] = {"cv_auc": cv.mean_auc, "test_auc": float(roc_auc_score(yt, p, sample_weight=w)),
                                "book": book(take, w, pnl[test_m], touch[test_m], win)}
            if "base" in row and "with_btc" in row:
                results[f"{sym}|{side}"] = row
                b, x = row["base"], row["with_btc"]
                print(f"{sym:<13} {side:<5} test AUC {b['test_auc']:.3f} -> {x['test_auc']:.3f}  ({x['test_auc']-b['test_auc']:+.3f})  "
                      f"| calls net {b['book'].get('mean', float('nan')):+.3f}% -> {x['book'].get('mean', float('nan')):+.3f}%  "
                      f"hit {b['book'].get('hit', float('nan')):.0%} -> {x['book'].get('hit', float('nan')):.0%}", flush=True)
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(json.dumps(results, indent=1))
    if results:
        d = np.array([r["with_btc"]["test_auc"] - r["base"]["test_auc"] for r in results.values()])
        nb = [r["base"]["book"].get("mean") for r in results.values() if r["base"]["book"].get("trades")]
        nx = [r["with_btc"]["book"].get("mean") for r in results.values() if r["with_btc"]["book"].get("trades")]
        print(f"\n{len(results)} models: test AUC change mean {d.mean():+.4f} (se {d.std(ddof=1)/np.sqrt(len(d)):.4f}), "
              f"better on {(d > 0).sum()}/{len(d)} | calls net: base {np.mean(nb):+.3f}% -> with BTC {np.mean(nx):+.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
