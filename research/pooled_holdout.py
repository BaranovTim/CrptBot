"""The confirmation: a pooled daily model, fitted before a date, scored after.

Cross-validation is purged and embargoed, but every window in the curve was
chosen while the whole history was in view. That is the multiple-testing
trap the pipeline's own summary warns about, and no amount of care inside
the CV removes it. The only clean answer is a period the choice never saw:
fit on everything before CUTOFF, score on everything after, once.

The scrambled control goes through the IDENTICAL procedure -- shuffled
labels before the cutoff, scored on the real labels after -- so that any
optimism in this exact recipe shows up in the control rather than being
mistaken for skill.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config                                    # noqa: E402
from agent5.dataset import Dataset                                 # noqa: E402
from agent5.model import fit_final                                 # noqa: E402
from research.pooled_daily import CACHE as _C, build_one, pool, symbols  # noqa: E402
import research.pooled_daily as PD                                 # noqa: E402

PD.CACHE = Path(sys.argv[1])
CUTOFF = pd.Timestamp(sys.argv[2] if len(sys.argv) > 2 else "2025-09-13", tz="UTC")
GEOMS = [(int(a.split(":")[0]), float(a.split(":")[1])) for a in sys.argv[3:]] or [(10, 1.0)]


def subset(ds: Dataset, mask: np.ndarray) -> Dataset:
    idx = np.flatnonzero(mask)
    return Dataset(X=ds.X.iloc[idx].reset_index(drop=True),
                   y=ds.y.iloc[idx].reset_index(drop=True),
                   weight=ds.weight.iloc[idx].reset_index(drop=True),
                   t1=ds.t1[idx], positions=ds.positions[idx],
                   blocks=ds.blocks, index=ds.index[idx])


def holdout_auc(train: Dataset, test: Dataset, cfg, cols, seed=None) -> float:
    from sklearn.metrics import roc_auc_score
    if seed is not None:
        # the control: same recipe, labels scrambled BEFORE the cutoff only
        rng = np.random.default_rng(seed)
        y = train.y.copy(); y.iloc[:] = rng.permutation(y.to_numpy())
        train = Dataset(X=train.X, y=y, weight=train.weight, t1=train.t1,
                        positions=train.positions, blocks=train.blocks,
                        index=train.index)
    model, cols = fit_final(train, cfg, cols)
    p = model.predict_proba(test.X[cols].astype(float))[:, 1]
    return float(roc_auc_score(test.y.to_numpy(float), p,
                               sample_weight=test.weight.to_numpy(float)))


def main() -> int:
    out = {}
    cut_day = int((CUTOFF - PD.EPOCH) / pd.Timedelta(days=1))
    for hold, k in GEOMS:
        parts = {s: ds for s in symbols()
                 if (ds := build_one(s, hold, k)) is not None and len(ds) > 200}
        pooled, coin = pool(parts)
        dropped = PD.scale_bound_columns(parts)
        cols = [c for c in pooled.X.columns if c not in dropped]
        # PURGE at the cutoff too: a training label that resolves after the
        # cutoff has seen the holdout's first days
        train = subset(pooled, pooled.t1 < cut_day)
        test = subset(pooled, pooled.positions >= cut_day)
        cfg = Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k)
        real = holdout_auc(train, test, cfg, cols)
        ctrl = [holdout_auc(train, test, cfg, cols, seed=s) for s in range(3)]
        # per-coin on the holdout
        from sklearn.metrics import roc_auc_score
        model, cols = fit_final(train, cfg, cols)
        p = model.predict_proba(test.X[cols].astype(float))[:, 1]
        tc = coin.to_numpy()[pooled.positions >= cut_day]
        by = {}
        for s in sorted(set(tc)):
            m = tc == s
            yy = test.y.to_numpy(float)[m]
            if m.sum() > 60 and len(np.unique(yy)) > 1:
                by[s] = round(float(roc_auc_score(yy, p[m], sample_weight=test.weight.to_numpy(float)[m])), 3)
        label = f"{hold} days x {k:g} ATR"
        print(f"{label}: train {len(train):,} samples to {CUTOFF.date()}, "
              f"holdout {len(test):,} samples after")
        print(f"   holdout AUC {real:.3f}   scrambled controls "
              f"[{' '.join(f'{c:.3f}' for c in ctrl)}] mean {np.mean(ctrl):.3f}")
        print("   per-coin on the holdout:",
              "  ".join(f"{s[:-4]} {a:.3f}" for s, a in sorted(by.items(), key=lambda kv: -kv[1])))
        out[label] = {"train": len(train), "holdout": len(test), "auc": round(real, 4),
                      "controls": [round(c, 4) for c in ctrl], "per_coin": by}
    Path("research/results/pooled_daily_holdout.json").write_text(json.dumps(
        {"cutoff": str(CUTOFF.date()), "results": out}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
