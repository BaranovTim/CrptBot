"""Experiment: a 4R geometry -- take profit four times as far as the stop.

THE ASK
    Fewer calls, each worth a lot when it lands: TP at 4x the stop distance,
    so a trade that wins pays +4R and one that loses costs -1R. At that
    payoff the book breaks even at a 20% hit rate.

WHAT THAT 20% ACTUALLY IS
    It is the random-walk base rate. With barriers at +4k and -k a driftless
    price touches the far one first exactly k/(4k+k) = 20% of the time, so
    20% is not "a bar we can clear even with a bad model" -- it is what a
    coin flip delivers, and the coin flip pays the fees. See agent5/barriers.py:
    the edge a geometry demands is (cost + threshold) / span, and the split
    of the span does not appear in it. What 4R changes is the SHAPE of the
    book: rare and big instead of frequent and small. Whether the model can
    pick the rare ones is the empirical question this script asks.

WHY IT CANNOT BE ANSWERED WITH THE SHIPPED MODELS
    Every shipped model was fitted on +/-1 ATR labels. Its probability is
    the answer to "which barrier of an equal pair is touched first", and a
    trade taken at +4/-1 is a different question. The label and the trade
    must share the geometry, so this refits from scratch. And a SHORT is no
    longer the long's mirror: "falls 4 before rising 1" needs its own label,
    so each (coin, timeframe) gets two models here, long and short.

WHAT CHANGES WITH THE WINDOW
    A far target takes longer to reach. Under a random walk the mean time to
    resolve scales with the PRODUCT of the two distances, four times the
    symmetric case, and the winners take about twice as long again as the
    average. So the hold is 4x and 8x the shipped one; with the shipped
    window nearly everything would time out and the "4R" would be a story
    the labels never tested.

THE LABEL FOR A TIMEOUT
    Counted as a LOSS (y = 0). The positive class is then exactly "the 4R
    target was reached inside the window" -- the event the caller is paying
    for. The book is scored separately with what each trade REALLY returned:
    +TP, -SL, or the mark at the time exit, less fees, so a strategy that
    lives on timeouts is not flattered by a label convention.

WHAT "IT WORKED" MEANS
    Two bars, both out-of-fold on purged CV:
      1. the model beats its shuffled control by more than the fold spread
         (the same rule the gate applies to everything else), and
      2. the trades the decision rule would actually take -- calibrated p
         above breakeven, with the calibration fitted on OTHER rows than the
         ones scored -- come out positive per trade AFTER fees, on their
         realised outcomes, by more than their own error bar, with a hit
         rate visibly above the 20% base rate.
    Either alone is a story. Both together are a strategy.

    usage: python research/four_r.py CACHE_DIR [--coins BTC,ETH] [--intervals 1h,4h]
                                     [--ratio 4] [--holds 4,8] [--pooled 1d]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config                              # noqa: E402
from agent5.calibration import fit_calibrator                # noqa: E402
from agent5.dataset import Dataset, build_dataset           # noqa: E402
from agent5.labels import LabelResult, triple_barrier        # noqa: E402
from agent5.metrics import shuffle_test                      # noqa: E402
from agent5.model import fit_cv                              # noqa: E402
from core import TIMEFRAMES, barriers_for, beats_shuffle, htf_for  # noqa: E402
from livefeed.store import BarStore                          # noqa: E402
from train import compute_frames                             # noqa: E402

COST = 0.10                       # round trip, % -- the crypto default
EPOCH = pd.Timestamp("2015-01-01", tz="UTC")
SIDES = ("long", "short")


# ----------------------------------------------------------------- data
def frames_for(cache: Path, sym: str, interval: str):
    """Detector features, once per (coin, timeframe). Label-independent."""
    p = cache / f"{sym}_{interval}_frames.pkl"
    if p.exists():
        return pickle.load(open(p, "rb"))
    bars = BarStore(sym, interval).load()
    if bars.empty:
        return None
    frames, warm, why = compute_frames(bars, htf_for(interval), sym, interval,
                                       no_tape=True)
    if frames is None:
        print(f"  {sym} {interval}: skipped ({why})", flush=True)
        return None
    cache.mkdir(parents=True, exist_ok=True)
    pickle.dump((bars, frames, warm), open(p, "wb"))
    return bars, frames, warm


def geometry(k: float, ratio: float, hold: int, side: str) -> Agent5Config:
    """The barriers as the LABELLER sees them (up/down), for one side.

    A long's target is above; a short's target is below, at the same
    distances swapped. Both are expressed as k_up/k_dn so the same
    labeller resolves them, and the short's outcome is mirrored afterwards.
    """
    far, near = k * ratio, k
    if side == "long":
        return Agent5Config(max_hold_bars=hold, k_up=far, k_dn=near)
    return Agent5Config(max_hold_bars=hold, k_up=near, k_dn=far)


def labels_for(bars: pd.DataFrame, cfg: Agent5Config, side: str) -> LabelResult:
    """Triple-barrier labels for one side, timeouts counted as losses.

    For the short, the labeller's 'lower' touch IS the win. tp_pct/sl_pct
    are swapped to name the distances from the trade's point of view.
    """
    lab = triple_barrier(bars, cfg)
    touch = lab.touch.to_numpy(object)
    win = "upper" if side == "long" else "lower"
    y = np.where(lab.y.isna(), np.nan, (touch == win).astype(float))
    y = pd.Series(y, index=lab.y.index, name="y")
    if side == "long":
        return replace(lab, y=y)
    return replace(lab, y=y, tp_pct=lab.sl_pct.rename("tp_pct"),
                   sl_pct=lab.tp_pct.rename("sl_pct"))


def realised_pct(bars: pd.DataFrame, lab: LabelResult, side: str) -> np.ndarray:
    """What each labelled trade ACTUALLY returned, before fees, in %.

    +TP on the target, -SL on the stop (and on the ambiguous bar, as the
    labeller counts it), and the mark-to-market at the time exit otherwise.
    """
    close = bars["close"].to_numpy(float)
    touch = lab.touch.to_numpy(object)
    t1 = lab.t1.to_numpy(float)
    tp = lab.tp_pct.to_numpy(float)
    sl = lab.sl_pct.to_numpy(float)
    n = len(close)
    out = np.full(n, np.nan)
    win = "upper" if side == "long" else "lower"
    lose = "lower" if side == "long" else "upper"
    sign = 1.0 if side == "long" else -1.0
    ok = ~np.isnan(t1)
    idx = np.arange(n)
    exit_ret = np.full(n, np.nan)
    exit_ret[ok] = sign * 100.0 * (close[t1[ok].astype(int)] / close[idx[ok]] - 1.0)
    out = np.where(touch == win, tp, out)
    out = np.where((touch == lose) | (touch == "ambiguous"), -sl, out)
    out = np.where(touch == "timeout", exit_ret, out)
    return out


def build(cache: Path, sym: str, interval: str, cfg: Agent5Config, side: str):
    """(Dataset, realised %, tp %, sl %, touch) for one coin/side/geometry."""
    got = frames_for(cache, sym, interval)
    if got is None:
        return None
    bars, frames, warm = got
    lab = labels_for(bars, cfg, side)
    ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **frames)
    pos = ds.positions.astype(int)
    pnl = realised_pct(bars, lab, side)[pos]
    return (ds, pnl, lab.tp_pct.to_numpy(float)[pos],
            lab.sl_pct.to_numpy(float)[pos], lab.touch.to_numpy(object)[pos])


# ----------------------------------------------------------------- pooling
def pool(parts: dict):
    """One Dataset on a shared day axis (see research/pooled_daily.py), with
    the per-sample bookkeeping carried along in the same order."""
    Xs, ys, ws, t1s, poss, idxs, coins = [], [], [], [], [], [], []
    pnls, tps, sls, touches = [], [], [], []
    for sym, (ds, pnl, tp, sl, touch) in parts.items():
        day = ((ds.index.tz_convert("UTC").normalize() - EPOCH)
               / pd.Timedelta(days=1)).astype(int).to_numpy()
        offset = ds.t1 - ds.positions
        Xs.append(ds.X); ys.append(ds.y); ws.append(ds.weight)
        poss.append(day); t1s.append(day + offset)
        idxs.append(ds.index); coins.append(pd.Series(sym, index=ds.X.index))
        pnls.append(pnl); tps.append(tp); sls.append(sl); touches.append(touch)
    X = pd.concat(Xs); y = pd.concat(ys); w = pd.concat(ws)
    pos = np.concatenate(poss); t1 = np.concatenate(t1s)
    index = idxs[0].append(idxs[1:]); coin = pd.concat(coins)
    order = np.argsort(pos, kind="stable")
    blocks = next(iter(parts.values()))[0].blocks
    ds = Dataset(X=X.iloc[order].reset_index(drop=True),
                 y=y.iloc[order].reset_index(drop=True),
                 weight=w.iloc[order].reset_index(drop=True),
                 t1=t1[order], positions=pos[order], blocks=blocks,
                 index=index[order])
    cat = lambda xs: np.concatenate(xs)[order]          # noqa: E731
    return (ds, cat(pnls), cat(tps), cat(sls), cat(touches),
            coin.iloc[order].reset_index(drop=True))


def scale_bound_columns(parts: dict, ratio: float = 20.0):
    meds = pd.DataFrame({s: p[0].X.abs().median() for s, p in parts.items()})
    hi, lo = meds.max(axis=1), meds.min(axis=1).replace(0, np.nan)
    bad = (hi / lo) > ratio
    return sorted(meds.index[bad.fillna(False)].tolist())


# ----------------------------------------------------------------- scoring
def book(take: np.ndarray, w: np.ndarray, pnl: np.ndarray, touch: np.ndarray,
         win: str, coin: pd.Series | None) -> dict:
    """The realised book of the trades a rule takes. Weighted by label
    uniqueness so overlapping bars do not count as separate trades."""
    if not take.any():
        return {"trades": 0}
    tw = w[take]
    hit = (touch[take] == win).astype(float)
    timeout = (touch[take] == "timeout").astype(float)
    net = pnl[take] - COST
    mean = float(np.average(net, weights=tw))
    # standard error of the weighted mean. the weights sum to the effective
    # number of independent trades, so this is the error bar a book of that
    # many trades would carry -- a "+0.3% per trade" on a 0.5% error bar is
    # not a result
    se = float(np.sqrt(np.sum((tw * (net - mean)) ** 2)) / tw.sum())
    out = {
        "trades": float(tw.sum()),                       # effective, not rows
        "rows": int(take.sum()),
        "share_of_bars": float(take.mean()),
        "hit_rate": float(np.average(hit, weights=tw)),
        "timeout_rate": float(np.average(timeout, weights=tw)),
        "pnl_per_trade": mean,
        "pnl_se": se,
    }
    # distinct calls: a run of consecutive firing bars is one call. per coin,
    # because the pooled set interleaves coins on the day axis
    if coin is None:
        edges = int(np.sum(take[1:] & ~take[:-1]) + take[0])
    else:
        edges = 0
        for sym in coin.unique():
            m = take & (coin.to_numpy() == sym)
            edges += int(np.sum(m[1:] & ~m[:-1]) + m[0])
    out["calls"] = edges
    return out


def cross_calibrated(oof: np.ndarray, y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Calibrated probabilities that never saw their own labels.

    JudgeAgent fits its isotonic map on the out-of-fold scores and applies it
    to the same rows -- fine for a probability the live model will use on
    NEW bars, circular for a threshold rule scored on THESE bars: isotonic
    hands every bin its own empirical hit rate, so "p above breakeven" would
    select exactly the bins whose in-sample hit rate is above breakeven and
    the book would look profitable by construction. Two contiguous halves in
    time, each calibrated by the other, keeps the scored labels out of the map.
    """
    p = np.full(len(oof), np.nan)
    ok = ~np.isnan(oof)
    idx = np.flatnonzero(ok)
    cut = len(idx) // 2
    halves = (idx[:cut], idx[cut:])
    for fit_on, apply_to in (halves, halves[::-1]):
        cal = fit_calibrator(oof[fit_on], y[fit_on], w[fit_on])
        p[apply_to] = cal.transform(oof[apply_to])
    return p


def score(ds: Dataset, cfg: Agent5Config, cols, pnl, tp, sl, touch, side,
          coin=None) -> dict:
    fit = fit_cv(ds, cfg, cols)
    sh = shuffle_test(ds, cfg, cols)
    y = ds.y.to_numpy(float); w = ds.weight.to_numpy(float)
    ok = ~np.isnan(fit.oof)
    p = cross_calibrated(fit.oof, y, w)
    from sklearn.metrics import roc_auc_score
    auc = float(roc_auc_score(y[ok], fit.oof[ok], sample_weight=w[ok]))
    win = "upper" if side == "long" else "lower"

    # the decision rule as shipped would apply it, on this geometry:
    # EV = p*tp - (1-p)*sl - cost > 0  <=>  p > (sl + cost) / (tp + sl)
    breakeven = (sl + COST) / (tp + sl)
    strong = (sl + COST + cfg.ev_threshold_pct) / (tp + sl)
    rules = {
        "all": ok,
        "ev>0": ok & (p > breakeven),
        "ev>0.05": ok & (p > strong),
        "top2%_raw": ok & (fit.oof >= np.nanquantile(fit.oof, 0.98)),
        "top10%_raw": ok & (fit.oof >= np.nanquantile(fit.oof, 0.90)),
        "top5%_raw": ok & (fit.oof >= np.nanquantile(fit.oof, 0.95)),
    }
    return {
        "samples": int(len(ds)), "effective_n": float(w.sum()),
        "base_rate": float(np.average(y[ok], weights=w[ok])),
        "no_edge_rate": float(np.average(sl[ok] / (tp[ok] + sl[ok]), weights=w[ok])),
        "breakeven_p": float(np.average(breakeven[ok], weights=w[ok])),
        "tp_pct": float(np.nanmedian(tp)), "sl_pct": float(np.nanmedian(sl)),
        "auc": round(auc, 4), "folds": [round(a, 4) for a in fit.fold_auc],
        "spread": round(float(fit.auc_spread), 4), "shuffle": round(float(sh), 4),
        "beats_shuffle": bool(beats_shuffle(auc, sh, fit.auc_spread)),
        "p_max": float(np.nanmax(p)), "p_q99": float(np.nanquantile(p, 0.99)),
        "book": {name: book(m, w, pnl, touch, win, coin) for name, m in rules.items()},
    }


def fmt(name: str, r: dict) -> str:
    b = r["book"]
    def row(k):
        x = b[k]
        if not x.get("trades"):
            return f"      {k:<11} no trades"
        return (f"      {k:<11} {x['calls']:>5} calls ({x['trades']:>7.0f} eff)  "
                f"hit {x['hit_rate']:5.1%}  timeout {x['timeout_rate']:5.1%}  "
                f"P&L/trade {x['pnl_per_trade']:+.3f}% (se {x['pnl_se']:.3f})  "
                f"fires {x['share_of_bars']:.1%} of bars")
    head = (f"    {name}: AUC {r['auc']:.3f} spread {r['spread']:.3f} shuffle {r['shuffle']:.3f} "
            f"-> {'CLEARS' if r['beats_shuffle'] else 'fails'} | base {r['base_rate']:.1%} "
            f"(random walk {r['no_edge_rate']:.1%}, breakeven {r['breakeven_p']:.1%}) "
            f"| TP {r['tp_pct']:.2f}% SL {r['sl_pct']:.2f}% | p99 {r['p_q99']:.3f}")
    return "\n".join([head] + [row(k) for k in b])


# ----------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache")
    ap.add_argument("--coins", default="")
    ap.add_argument("--intervals", default=",".join(TIMEFRAMES))
    ap.add_argument("--ratio", type=float, default=4.0)
    ap.add_argument("--holds", default="4,8", help="multiples of the shipped h2 window")
    ap.add_argument("--pooled", default="1d", help="intervals fitted across coins")
    ap.add_argument("--out", default="research/results/four_r.json")
    a = ap.parse_args()

    cache = Path(a.cache)
    coins = [c.strip().upper() for c in a.coins.split(",") if c.strip()]
    coins = [c if c.endswith("USDT") else c + "USDT" for c in coins]
    if not coins:
        coins = sorted({f.name.split("judge_")[1].rsplit("_", 2)[0]
                        for f in Path("output").glob("judge_*_1h_h1.joblib")})
    intervals = [i.strip() for i in a.intervals.split(",") if i.strip()]
    mults = [float(m) for m in a.holds.split(",")]
    pooled = {i.strip() for i in a.pooled.split(",") if i.strip()}

    out_path = Path(a.out)
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    results.setdefault("ratio", a.ratio)
    results.setdefault("runs", {})

    for interval in intervals:
        k, _h1, h2 = barriers_for(interval)
        holds = sorted({max(2, int(round(h2 * m))) for m in mults})
        print(f"\n=== {interval}: SL {k:g} ATR, TP {k * a.ratio:g} ATR, "
              f"holds {holds} bars ({'pooled' if interval in pooled else 'per coin'})",
              flush=True)
        for hold in holds:
            for side in SIDES:
                cfg = geometry(k, a.ratio, hold, side)
                t0 = time.time()
                if interval in pooled:
                    parts = {}
                    for sym in coins:
                        got = build(cache, sym, interval, cfg, side)
                        if got is not None and len(got[0]) > 200:
                            parts[sym] = got
                    if not parts:
                        continue
                    dropped = scale_bound_columns(parts)
                    ds, pnl, tp, sl, touch, coin = pool(parts)
                    cols = [c for c in ds.X.columns if c not in dropped]
                    r = score(ds, cfg, cols, pnl, tp, sl, touch, side, coin)
                    r["coins"] = sorted(parts)
                    key = f"{interval}|pooled|h{hold}|{side}"
                    results["runs"][key] = r
                    print(fmt(f"{key} ({time.time() - t0:.0f}s)", r), flush=True)
                else:
                    for sym in coins:
                        key = f"{interval}|{sym}|h{hold}|{side}"
                        if key in results["runs"]:
                            continue
                        got = build(cache, sym, interval, cfg, side)
                        if got is None or len(got[0]) < 500:
                            continue
                        ds, pnl, tp, sl, touch = got
                        r = score(ds, cfg, list(ds.X.columns), pnl, tp, sl, touch, side)
                        results["runs"][key] = r
                        print(fmt(f"{key} ({time.time() - t0:.0f}s)", r), flush=True)
                        t0 = time.time()
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_text(json.dumps(results, indent=1))
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(results, indent=1))
    print(f"\nwritten {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
