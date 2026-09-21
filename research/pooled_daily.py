"""Experiment 1: ONE daily model trained on every coin at once.

THE HYPOTHESIS
    Per-coin daily models fail (0 of 15 beat their shuffled control) and the
    first suspect is sample size: 1,300 daily bars against 32,000 hourly.
    If the coins share their daily patterns, pooling them gives one model
    ~36,000 examples. This asks whether that alone lifts the daily model
    over the bar. It changes NOTHING else -- same features, same barriers,
    same horizons, same evaluation code -- so a change in the answer is
    attributable to pooling and only pooling.

THE LEAK POOLING INTRODUCES, AND HOW IT IS CLOSED
    With one coin, a fold is a span of bars. With fifteen, a fold that split
    by ROW would put BTC's March in training and ETH's March in test -- the
    same market week seen twice, which is exactly the kind of leak the
    shuffle test exists to catch. So every coin's samples are placed on ONE
    shared day axis, and `PurgedKFold` -- unchanged -- makes each fold a
    contiguous span of calendar days across all coins at once. Purge and
    embargo then apply across coins for free.

SCALE
    Any feature that carries a coin's price level (a raw close, a raw
    volume) would let the model tell coins apart by magnitude and learn
    per-coin base rates instead of shared structure. Columns whose median
    differs by more than 20x between coins are reported and dropped.

WHAT "IT WORKED" MEANS
    The same rule the gate applies: AUC above 0.5 by more than the fold
    spread AND above its own shuffled control by more than the fold spread.
    Nothing looser, because the point of this project's evaluation is that
    it says no when the answer is no.
"""
from __future__ import annotations

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

from agent5 import Agent5Config, JudgeAgent            # noqa: E402
from agent5.dataset import Dataset                      # noqa: E402
from agent5.calibration import fit_calibrator           # noqa: E402
from agent5.metrics import evaluate, shuffle_test       # noqa: E402
from agent5.model import fit_cv                         # noqa: E402
from core import barriers_for, beats_shuffle, htf_for   # noqa: E402
from livefeed.store import BarStore                     # noqa: E402
from train import compute_frames                        # noqa: E402

INTERVAL = "1d"
CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("research/results/cache")
OUT = Path(sys.argv[1]).parent / "pooled_daily_curve.json" if len(sys.argv) > 1 else Path("research/results/pooled_daily_curve.json")
EPOCH = pd.Timestamp("2015-01-01", tz="UTC")


def symbols():
    return sorted({f.name.split("judge_")[1].rsplit("_", 2)[0]
                   for f in Path("output").glob("judge_*_1d_h1.joblib")})


def frames_for(sym: str):
    """The detector features for one coin, computed once and cached. The
    features do not depend on the label, so every barrier geometry and
    every window is built from the same cached frames -- this is the slow,
    network-bound step, and it now runs once per coin, ever."""
    p = CACHE / f"{sym}_{INTERVAL}_frames.pkl"
    if p.exists():
        return pickle.load(open(p, "rb"))
    bars = BarStore(sym, INTERVAL).load()
    if bars.empty:
        return None
    frames, warm, why = compute_frames(bars, htf_for(INTERVAL), sym, INTERVAL,
                                       no_tape=True)
    if frames is None:
        print(f"  {sym}: skipped ({why})", flush=True)
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    pickle.dump((bars, frames, warm), open(p, "wb"))
    return bars, frames, warm


def build_one(sym: str, hold: int, k: float, side: str = None) -> Dataset | None:
    """The per-coin dataset, exactly as train.py builds it, for any
    (window, barrier) from the cached frames. With `side`, the structure
    label for that side (agent5/structure.py); without, the ATR one."""
    tag = f"_{side}" if side else ""
    p = CACHE / f"{sym}_{INTERVAL}_h{hold}_k{k:g}{tag}.pkl"
    if p.exists():
        return pickle.load(open(p, "rb"))
    got = frames_for(sym)
    if got is None:
        return None
    bars, frames, warm = got
    cfg = (Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k, geometry="structure", side=side)
           if side else Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k))
    judge = JudgeAgent(cfg)
    ds = judge.build(bars, warmup=warm, **frames)
    pickle.dump(ds, open(p, "wb"))
    return ds


def pool(parts: dict[str, Dataset]) -> tuple[Dataset, pd.Series]:
    """One Dataset on a shared day axis, sorted by day. Returns the pooled
    set and a Series naming each row's coin, for the per-coin breakdown."""
    Xs, ys, ws, t1s, poss, idxs, coins = [], [], [], [], [], [], []
    for sym, ds in parts.items():
        day = ((ds.index.tz_convert("UTC").normalize() - EPOCH)
               / pd.Timedelta(days=1)).astype(int).to_numpy()
        offset = ds.t1 - ds.positions          # bars until the label resolved
        Xs.append(ds.X); ys.append(ds.y); ws.append(ds.weight)
        poss.append(day); t1s.append(day + offset)
        idxs.append(ds.index); coins.append(pd.Series(sym, index=ds.X.index))
    X = pd.concat(Xs); y = pd.concat(ys); w = pd.concat(ws)
    pos = np.concatenate(poss); t1 = np.concatenate(t1s)
    index = idxs[0].append(idxs[1:]); coin = pd.concat(coins)
    order = np.argsort(pos, kind="stable")
    X = X.iloc[order].reset_index(drop=True)
    y = y.iloc[order].reset_index(drop=True)
    w = w.iloc[order].reset_index(drop=True)
    coin = coin.iloc[order].reset_index(drop=True)
    blocks = next(iter(parts.values())).blocks
    return Dataset(X=X, y=y, weight=w, t1=t1[order], positions=pos[order],
                   blocks=blocks, index=index[order]), coin


def scale_bound_columns(parts: dict[str, Dataset], ratio: float = 20.0):
    """Columns whose typical magnitude differs wildly between coins."""
    meds = pd.DataFrame({s: ds.X.abs().median() for s, ds in parts.items()})
    hi, lo = meds.max(axis=1), meds.min(axis=1).replace(0, np.nan)
    bad = (hi / lo) > ratio
    return sorted(meds.index[bad.fillna(False)].tolist())


def per_coin_auc(fit, ds: Dataset, coin: pd.Series) -> dict:
    from sklearn.metrics import roc_auc_score
    out = {}
    ok = ~np.isnan(fit.oof)
    y = ds.y.to_numpy(float); w = ds.weight.to_numpy(float)
    for sym in sorted(coin.unique()):
        m = ok & (coin.to_numpy() == sym)
        if m.sum() > 50 and len(np.unique(y[m])) > 1:
            out[sym] = round(float(roc_auc_score(y[m], fit.oof[m],
                                                 sample_weight=w[m])), 4)
    return out


def main() -> int:
    k0, h1, h2 = barriers_for(INTERVAL)
    # geometries: "hold:k" on the command line, else the shipped h1/h2
    geoms = [(int(a.split(":")[0]), float(a.split(":")[1]))
             for a in sys.argv[2:]] or [(h1, k0), (h2, k0)]
    syms = symbols()
    print(f"pooling {len(syms)} coins on {INTERVAL}; geometries "
          f"{', '.join(f'{h} bars x {k:g} ATR' for h, k in geoms)}", flush=True)
    results = {"interval": INTERVAL, "coins": syms, "horizons": {},
               "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    for hold, k in geoms:
        slot = f"{hold}bars_x{k:g}atr"
        t0 = time.time()
        parts = {}
        for sym in syms:
            ds = build_one(sym, hold, k)
            if ds is not None and len(ds) > 200:
                parts[sym] = ds
                print(f"  {slot} {sym:<14} {len(ds):>6,} samples", flush=True)
        dropped = scale_bound_columns(parts)
        pooled, coin = pool(parts)
        cols = [c for c in pooled.X.columns if c not in dropped]
        print(f"  {slot}: pooled {len(pooled):,} samples, {len(cols)} columns "
              f"({len(dropped)} scale-bound dropped: {dropped[:6]}"
              f"{'...' if len(dropped) > 6 else ''})", flush=True)

        cfg = Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k)
        fit = fit_cv(pooled, cfg, cols)
        sh = shuffle_test(pooled, cfg, cols)
        # the same three steps JudgeAgent.fit takes, in the same order
        cal = fit_calibrator(fit.oof, pooled.y.to_numpy(float),
                             pooled.weight.to_numpy(float))
        ev = evaluate(pooled, fit, cal.transform(fit.oof), cfg, shuffle_auc=sh)
        ok = beats_shuffle(ev.auc, sh, fit.auc_spread)
        by_coin = per_coin_auc(fit, pooled, coin)

        print(f"  {slot} ({hold} bar{'s' if hold > 1 else ''}, +/-{k:g} ATR): "
              f"AUC {ev.auc:.3f}  folds [{' '.join(f'{a:.3f}' for a in fit.fold_auc)}]  "
              f"spread {fit.auc_spread:.3f}  shuffle {sh:.3f}  "
              f"-> {'CLEARS THE BAR' if ok else 'does not clear the bar'}  "
              f"({time.time() - t0:.0f}s)", flush=True)
        print("     per-coin AUC of the pooled model's out-of-fold predictions:",
              flush=True)
        for s, a in sorted(by_coin.items(), key=lambda kv: -kv[1]):
            print(f"       {s:<14} {a:.3f}", flush=True)

        results["horizons"][slot] = {
            "hold": hold, "k": k, "samples": int(len(pooled)), "columns": len(cols),
            "dropped_scale_bound": dropped,
            "auc": round(float(ev.auc), 4), "folds": [round(a, 4) for a in fit.fold_auc],
            "spread": round(float(fit.auc_spread), 4), "shuffle": round(float(sh), 4),
            "beats_shuffle": bool(ok), "per_coin_auc": by_coin,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(results, indent=1))

    print(f"\nwritten {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
