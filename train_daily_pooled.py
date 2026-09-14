#!/usr/bin/env python3
"""The daily models: ONE fit across every coin, installed under each coin's
name.

    python3 train_daily_pooled.py                 # fit, evaluate, install
    python3 train_daily_pooled.py --dry-run       # fit and report only

WHY DAILY IS DIFFERENT FROM EVERY OTHER TIMEFRAME
    Per-coin daily fits sit at their shuffled control on all 15 coins: a
    coin has ~1,300 daily bars, and the daily signal is slow and weak.
    Pooling the coins (~23,000 samples) with a ten-day window gives a model
    that scores 0.519 cross-validated and 0.530 on a held-out year, against
    controls at 0.50. Per coin, with two days, there is nothing to find.

    So `train.py` does not fit 1d. This does, once for all coins, and writes
    the SAME model into every coin's daily slot -- `judge_<SYM>_1d_h1` and
    `_h2` -- because the server loads models per coin and that keeps serving
    unchanged. The features are scale-free, so one model reads every coin.

    h1 is the 5-day model (the half-spent window, as everywhere else) and
    h2 the 10-day one. h1 is weaker (0.508 cross-validated) and will mostly
    say WAIT; that is correct, and the gate reads the verdict per horizon.

THE VERDICT
    Written per coin, from the pooled evaluation, with `source: "pooled"`
    and the held-out numbers alongside. The gate reads it like any other.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config, JudgeAgent            # noqa: E402
from core import (barriers_for, beats_shuffle, eval_path,   # noqa: E402
                  model_paths)
import research.pooled_daily as PD                      # noqa: E402

INTERVAL = "1d"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--cache", default=None,
                   help="where cached feature frames live (default: a temp dir)")
    a = p.parse_args(argv)

    PD.CACHE = Path(a.cache) if a.cache else Path(tempfile.mkdtemp())
    k, h1, h2 = barriers_for(INTERVAL)
    syms = PD.symbols()
    print(f"pooled daily fit over {len(syms)} coins, +/-{k:g} ATR, "
          f"holds {h1}/{h2} bars", flush=True)

    verdict = {"symbol": "POOLED", "interval": INTERVAL, "source": "pooled",
               "coins": syms,
               "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "horizons": {}}
    fitted = {}
    for slot, hold in ((1, h1), (2, h2)):
        parts = {}
        for sym in syms:
            ds = PD.build_one(sym, hold, k)
            if ds is not None and len(ds) > 200:
                parts[sym] = ds
        dropped = PD.scale_bound_columns(parts)
        pooled, coin = PD.pool(parts)
        cols = [c for c in pooled.X.columns if c not in dropped]

        cfg = Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k)
        judge = JudgeAgent(cfg)
        # the full pipeline: purged CV, isotonic calibration on OOF, the
        # shuffle control -- then the final refit on everything
        report = judge.fit(pooled, columns=cols, with_shuffle=True,
                           with_ablation=False, with_importance=False)
        ev = report.evaluation
        ok = beats_shuffle(ev.auc, ev.shuffle_auc, ev.auc_spread)
        print(f"  h{slot} ({hold} bars): AUC {ev.auc:.3f}  spread "
              f"{ev.auc_spread:.3f}  shuffle {ev.shuffle_auc:.3f}  "
              f"{'CLEARS' if ok else 'fails'}  ({len(pooled):,} samples, "
              f"{len(cols)} columns)", flush=True)
        verdict["horizons"][f"h{slot}"] = {
            "auc": round(float(ev.auc), 4),
            "shuffle": round(float(ev.shuffle_auc), 4),
            "spread": round(float(ev.auc_spread), 4),
            "effective_n": int(len(pooled)),
            "beats_shuffle": bool(ok), "hold": hold,
        }
        fitted[slot] = judge

    if not any(h["beats_shuffle"] for h in verdict["horizons"].values()):
        print("\nNO HORIZON CLEARS THE BAR -- nothing installed.", flush=True)
        return 1
    if a.dry_run:
        print("\ndry run: nothing written", flush=True)
        return 0

    # install: the same file under every coin's daily slots, plus verdicts
    tmp = Path(tempfile.mkdtemp())
    for slot, judge in fitted.items():
        judge.save(tmp / f"pooled_h{slot}.joblib")
    n = 0
    for sym in syms:
        p1, p2 = model_paths(sym, INTERVAL)
        for slot, dst in ((1, p1), (2, p2)):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp / f"pooled_h{slot}.joblib", dst)
        v = dict(verdict, symbol=sym)
        eval_path(sym, INTERVAL).write_text(json.dumps(v, indent=1))
        n += 1
    print(f"\ninstalled the pooled daily model under {n} coins, with verdicts",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
