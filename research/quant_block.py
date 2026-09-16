"""Experiment: does the quant block (agent5/quant.py) improve the judge?

Same coins, same purged CV, same shipped geometry (+/-1 ATR, the shipped
windows) -- the ONLY change is the extra feature block, so a change in the
answer is attributable to it. Scored two ways, both out-of-fold:

  1. AUC against the shuffled control, with the fold spread (the gate's rule).
  2. The book the SHIPPED decision rule would have produced: side from the
     calibrated p, taken when the EV clears fees, realised P&L per trade with
     an error bar. Calibration is cross-fitted (see research/four_r.py).

    usage: python research/quant_block.py CACHE_DIR [--coins BTC,ETH]
                                          [--intervals 1h,4h] [--slots 1,2]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config                              # noqa: E402
from agent5.dataset import build_dataset                     # noqa: E402
from agent5.labels import triple_barrier                     # noqa: E402
from agent5.metrics import shuffle_test                      # noqa: E402
from agent5.model import fit_cv, grouped_importance          # noqa: E402
from agent5.quant import QUANT_COLUMNS, compute_quant        # noqa: E402
from core import barriers_for, beats_shuffle, pandas_rule    # noqa: E402
from livefeed.store import BarStore                          # noqa: E402
from marketdata.funding import load_funding                  # noqa: E402
from research.four_r import (COST, cross_calibrated, frames_for,  # noqa: E402
                             pool, realised_pct, scale_bound_columns)


def hours_per_bar(interval: str) -> float:
    return pd.Timedelta(pandas_rule(interval)).total_seconds() / 3600.0


def market_closes(interval: str) -> pd.DataFrame:
    """Every coin's close on this timeframe, for breadth."""
    cols = {}
    for d in sorted(Path("data_cache/live").iterdir()):
        if not d.is_dir() or " " in d.name or not d.name.endswith("USDT"):
            continue
        b = BarStore(d.name, interval).load()
        if len(b):
            cols[d.name] = b["close"].astype(float)
    return pd.DataFrame(cols)


def quant_frame(cache: Path, sym: str, interval: str, bars: pd.DataFrame,
                btc: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    p = cache / f"{sym}_{interval}_quant.pkl"
    if p.exists():
        return pickle.load(open(p, "rb"))
    funding = load_funding(sym, refresh=False)
    q = compute_quant(bars, hours_per_bar(interval), btc=btc,
                      funding=funding, market=market)
    pickle.dump(q, open(p, "wb"))
    return q


def symmetric_book(p: np.ndarray, oof: np.ndarray, y, w, pnl_long, tp, sl,
                   cfg: Agent5Config) -> dict:
    """The shipped rule on +/-k barriers: side from p, EV must clear fees."""
    ok = ~np.isnan(p)
    side = np.where(p >= 0.5, 1.0, -1.0)
    conf = np.abs(p - 0.5)
    span = tp + sl
    need = (COST) / span                          # EV > 0
    need_strong = (COST + cfg.ev_threshold_pct) / span
    pnl = side * pnl_long                          # symmetric barriers mirror
    rules = {
        "all": ok,
        "ev>0": ok & (conf > need),
        "strong": ok & (conf > need_strong),
        "top10%": ok & (conf >= np.nanquantile(conf[ok], 0.90)),
        "top5%": ok & (conf >= np.nanquantile(conf[ok], 0.95)),
    }
    out = {}
    for name, take in rules.items():
        if not take.any():
            out[name] = {"trades": 0}
            continue
        tw = w[take]
        net = pnl[take] - COST
        mean = float(np.average(net, weights=tw))
        se = float(np.sqrt(np.sum((tw * (net - mean)) ** 2)) / tw.sum())
        won = (np.sign(pnl[take]) > 0).astype(float)
        out[name] = {
            "trades": float(tw.sum()), "rows": int(take.sum()),
            "share_of_bars": float(take.mean()),
            "hit_rate": float(np.average(won, weights=tw)),
            "pnl_per_trade": mean, "pnl_se": se,
            "calls": int(np.sum(take[1:] & ~take[:-1]) + take[0]),
        }
    return out


def vol_split(ds, p, oof, y, w, pnl_long, tp, sl, cfg) -> dict:
    """E3: the shipped rule's book inside each volatility tercile. The
    tercile is the causal `vol_pctile` regime column, so this is a filter
    the live engine could apply with no new data."""
    if "vol_pctile" not in ds.X.columns:
        return {}
    v = ds.X["vol_pctile"].to_numpy(float)
    out = {}
    for name, lo, hi in (("calm", 0.0, 1 / 3), ("mid", 1 / 3, 2 / 3), ("wild", 2 / 3, 1.01)):
        m = (v >= lo) & (v < hi)
        if m.sum() < 100:
            continue
        book = symmetric_book(np.where(m, p, np.nan), oof, y, w, pnl_long, tp, sl, cfg)
        out[name] = {k: book[k] for k in ("ev>0", "strong")}
    return out


def run(ds, cfg, pnl_long, tp, sl, with_importance=False) -> dict:
    cols = list(ds.X.columns)
    fit = fit_cv(ds, cfg, cols)
    sh = shuffle_test(ds, cfg, cols)
    y = ds.y.to_numpy(float); w = ds.weight.to_numpy(float)
    ok = ~np.isnan(fit.oof)
    from sklearn.metrics import roc_auc_score
    auc = float(roc_auc_score(y[ok], fit.oof[ok], sample_weight=w[ok]))
    p = cross_calibrated(fit.oof, y, w)
    r = {
        "samples": int(len(ds)), "effective_n": float(w.sum()),
        "auc": round(auc, 4), "folds": [round(a, 4) for a in fit.fold_auc],
        "spread": round(float(fit.auc_spread), 4), "shuffle": round(float(sh), 4),
        "beats_shuffle": bool(beats_shuffle(auc, sh, fit.auc_spread)),
        "book": symmetric_book(p, fit.oof, y, w, pnl_long, tp, sl, cfg),
        "by_vol": vol_split(ds, p, fit.oof, y, w, pnl_long, tp, sl, cfg),
    }
    if with_importance:
        imp = grouped_importance(fit, ds, cfg)
        r["block_importance"] = {k: round(float(v), 4) for k, v in imp.items()}
    return r


def fmt(key, base, quant) -> str:
    def b(r, k):
        x = r["book"][k]
        if not x.get("trades"):
            return "   none   "
        return f"{x['hit_rate']:4.1%} {x['pnl_per_trade']:+.3f}%±{x['pnl_se']:.3f}"
    d = quant["auc"] - base["auc"]
    verdict = ("+" if d > max(base["spread"], quant["spread"]) else
               "-" if d < -max(base["spread"], quant["spread"]) else "=")
    lines = [f"  {key}: AUC {base['auc']:.3f} -> {quant['auc']:.3f} ({d:+.3f}, "
             f"spread {base['spread']:.3f}/{quant['spread']:.3f}) [{verdict}]  "
             f"shuffle {base['shuffle']:.3f}/{quant['shuffle']:.3f}"]
    for k in ("ev>0", "strong", "top10%", "top5%"):
        lines.append(f"      {k:<7} base {b(base, k):<22}  quant {b(quant, k)}")
    if "block_importance" in quant:
        lines.append("      importance " + "  ".join(
            f"{k} {v:.0%}" for k, v in sorted(quant["block_importance"].items(),
                                              key=lambda kv: -kv[1])))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache")
    ap.add_argument("--coins", default="BTC,ETH,SOL,DOGE,XRP")
    ap.add_argument("--intervals", default="1h,4h")
    ap.add_argument("--slots", default="2")
    ap.add_argument("--holds", default="",
                    help="explicit windows in bars instead of the shipped h1/h2")
    ap.add_argument("--out", default="research/results/quant_block.json")
    ap.add_argument("--pooled", action="store_true",
                    help="one fit across all coins on a shared day axis (1d)")
    a = ap.parse_args()
    cache = Path(a.cache)
    coins = [c.strip().upper() + ("" if c.strip().upper().endswith("USDT") else "USDT")
             for c in a.coins.split(",") if c.strip()]
    slots = [int(s) for s in a.slots.split(",")]
    holds = [int(h) for h in a.holds.split(",") if h.strip()]
    out_path = Path(a.out)
    results = json.loads(out_path.read_text()) if out_path.exists() else {}

    for interval in [i.strip() for i in a.intervals.split(",")]:
        k, h1, h2 = barriers_for(interval)
        market = market_closes(interval)
        btc_got = frames_for(cache, "BTCUSDT", interval)
        btc = btc_got[0] if btc_got else None
        print(f"\n=== {interval}: +/-{k:g} ATR, holds h1={h1} h2={h2}; "
              f"breadth over {market.shape[1]} coins"
              f"{'; POOLED' if a.pooled else ''}", flush=True)
        if a.pooled:
            for slot in slots:
                hold = h1 if slot == 1 else h2
                key = f"{interval}|pooled|h{slot}"
                if key in results:
                    print(fmt(key, results[key]["base"], results[key]["quant"]), flush=True)
                    continue
                t0 = time.time()
                cfg = Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k)
                parts_b, parts_q = {}, {}
                for sym in coins:
                    got = frames_for(cache, sym, interval)
                    if got is None:
                        continue
                    bars, frames, warm = got
                    q = quant_frame(cache, sym, interval, bars, btc, market)
                    lab = triple_barrier(bars, cfg)
                    b_ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **frames)
                    q_ds = build_dataset(bars, cfg, warmup=warm, labels=lab, quant=q, **frames)
                    if len(b_ds) < 200:
                        continue
                    pos = b_ds.positions.astype(int)
                    pnl = realised_pct(bars, lab, "long")[pos]
                    tp = lab.tp_pct.to_numpy(float)[pos]
                    sl = lab.sl_pct.to_numpy(float)[pos]
                    touch = lab.touch.to_numpy(object)[pos]
                    parts_b[sym] = (b_ds, pnl, tp, sl, touch)
                    parts_q[sym] = (q_ds, pnl, tp, sl, touch)
                arms = {}
                for arm, parts in (("base", parts_b), ("quant", parts_q)):
                    dropped = scale_bound_columns(parts)
                    ds, pnl, tp, sl, touch, coin = pool(parts)
                    ds.X = ds.X.drop(columns=dropped)
                    arms[arm] = run(ds, cfg, pnl, tp, sl, with_importance=(arm == "quant"))
                    arms[arm]["dropped_scale_bound"] = dropped
                results[key] = {"base": arms["base"], "quant": arms["quant"],
                                "coins": sorted(parts_b), "seconds": round(time.time() - t0)}
                print(fmt(f"{key} ({time.time() - t0:.0f}s, {len(parts_b)} coins)",
                          arms["base"], arms["quant"]), flush=True)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(results, indent=1))
            continue
        for sym in coins:
            got = frames_for(cache, sym, interval)
            if got is None:
                continue
            bars, frames, warm = got
            q = quant_frame(cache, sym, interval, bars, btc, market)
            cover = q.notna().mean()
            plan = ([(f"hold{h}", h) for h in holds] if holds
                    else [(f"h{s_}", h1 if s_ == 1 else h2) for s_ in slots])
            for label, hold in plan:
                key = f"{interval}|{sym}|{label}"
                if key in results:
                    print(fmt(key, results[key]["base"], results[key]["quant"]), flush=True)
                    continue
                t0 = time.time()
                cfg = Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k)
                lab = triple_barrier(bars, cfg)
                base_ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **frames)
                quant_ds = build_dataset(bars, cfg, warmup=warm, labels=lab,
                                         quant=q, **frames)
                pos = base_ds.positions.astype(int)
                pnl = realised_pct(bars, lab, "long")[pos]
                tp = lab.tp_pct.to_numpy(float)[pos]
                sl = lab.sl_pct.to_numpy(float)[pos]
                base = run(base_ds, cfg, pnl, tp, sl)
                quant = run(quant_ds, cfg, pnl, tp, sl, with_importance=True)
                results[key] = {"base": base, "quant": quant,
                                "quant_coverage": {c: round(float(cover[c]), 3)
                                                   for c in QUANT_COLUMNS},
                                "seconds": round(time.time() - t0)}
                print(fmt(f"{key} ({time.time() - t0:.0f}s)", base, quant), flush=True)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(results, indent=1))
    print(f"\nwritten {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
