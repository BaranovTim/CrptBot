#!/usr/bin/env python3
"""The 4h models: ONE fit per side across every coin, installed under each
coin's name.

    python3 train_pooled_4h.py                  # fit, evaluate, install, fill the score book
    python3 train_pooled_4h.py --dry-run        # fit and report only

WHY POOLED
    research/wf4h.py refit the 4h structure models every six months on their
    own past and scored the next six, Sep 2023 -> Sep 2026, as an account
    (three positions, fees and funding paid). A model per coin -- what this
    replaces -- made money in 2 of the 6 half-years and lost 132% in total at
    full exposure. One model over all fifteen coins won in every window on
    AUC and in 5 of 6 on money; with the two changes below, 6 of 6. The
    learning curves say why: the model is data-hungry (twelve months of
    history 0.568 AUC, everything 0.599) and one coin is not enough of it.

WHAT IS DIFFERENT FROM THE PER-COIN FIT
    * The STOP sits `STOP_BUFFER_ATR` past its structural level instead of on
      it (Agent5Config.stop_buffer_atr). The target does not move.
    * The model carries `rank_pool`, so the monitor ranks each reading
      against every coin's recent readings (monitor.SCOREBOOK) and calls
      from POOLED_RANKS -- the top 3% is "strong".
    * Calls are sized equally (Agent5Config.equal_size_pct), not by Kelly.

    Same features, same purged CV, same isotonic calibration, same shuffled
    control and the same gate as every other fit: a side that does not
    clear its control is not installed.

INSTALL
    As the daily pooled model does: the same file under every coin's 4h
    slots (`judge_<SYM>_4h_h1` = long, `_h2` = short) so serving loads
    models per coin unchanged, and a verdict per coin with
    `source: "pooled"`. Then the score book is filled from these bars, so
    the server's first ranking after the install is against a full pool.

RETRAIN
    Every six months is what the walk-forward measured; the models did not
    decay within a year, so it is housekeeping, not a fix. Each fit gets a
    new `rank_pool` name and starts a fresh pool.
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

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config, JudgeAgent            # noqa: E402
from agent5.dataset import Dataset                      # noqa: E402
from core import (barriers_for, beats_shuffle, eval_path,   # noqa: E402
                  model_paths, slot_side)
import research.pooled_daily as PD                      # noqa: E402

INTERVAL = "4h"
STOP_BUFFER_ATR = 0.5
# THE ENTRY (research/improve_4h.py, monitor.resting_orders): a resting order
# 0.5 ATR better than the call's close, stop moved with it, good for 6 bars
# (a day). 4 bars was chosen on a replay that let the account pick among
# orders knowing which would fill; re-tuned on the served policy (round
# four), 6 bars beat 4 on the development half-years for all four models
# tried and on the holdout for three of them. 0.25/0.75/1.0 ATR and a
# limit AT the level were all worse than 0.5 ATR.
ENTRY_OFFSET_ATR = 0.5
ENTRY_VALID_BARS = 6
# SEED BAGGING (agent5.model.SeedBag): the final fit under five seeds,
# averaged. One reseed of the same model moved the served account from
# Sharpe 2.22/2.22 to 1.82/1.40; the bag beat the average seed on both
# periods and every seed on the six-half-year total.
BAG_SEEDS = (7, 11, 13, 17, 19)
EPOCH = pd.Timestamp("2015-01-01", tz="UTC")
BAR = pd.Timedelta(hours=4)
# the fifteen the walk-forward validated, which are also the ones served
COINS = ["1000PEPEUSDT", "ADAUSDT", "ARBUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT",
         "ENAUSDT", "ETHUSDT", "HYPEUSDT", "NEARUSDT", "SOLUSDT", "SUIUSDT",
         "UNIUSDT", "XRPUSDT", "ZECUSDT"]


def pool_4h(parts: dict) -> tuple:
    """One Dataset on a shared 4h-BAR axis, sorted by time.

    `research.pooled_daily.pool` lays coins on a DAY axis, which is right for
    1d and wrong here twice over: the cutoff arithmetic, and the purge -- a
    label resolving 16 bars ahead would be treated as resolving 16 days
    ahead. Same shared-time property (a fold is a span of real time across
    every coin, so BTC's March cannot train a model tested on ETH's March),
    in the right units."""
    Xs, ys, ws, t1s, poss, idxs, coins = [], [], [], [], [], [], []
    for sym, ds in parts.items():
        ix = pd.DatetimeIndex(ds.index)
        ix = ix.tz_localize("UTC") if ix.tz is None else ix.tz_convert("UTC")
        pos = np.asarray((ix - EPOCH) / BAR, dtype=np.int64)
        off = np.asarray(ds.t1, float) - np.asarray(ds.positions, float)
        Xs.append(ds.X.reset_index(drop=True)); ys.append(ds.y.reset_index(drop=True))
        ws.append(ds.weight.reset_index(drop=True))
        poss.append(pos); t1s.append(pos + off); idxs.append(ix)
        coins.append(pd.Series(sym, index=range(len(ds))))
    X = pd.concat(Xs, ignore_index=True); y = pd.concat(ys, ignore_index=True)
    w = pd.concat(ws, ignore_index=True); coin = pd.concat(coins, ignore_index=True)
    pos = np.concatenate(poss); t1 = np.concatenate(t1s)
    index = idxs[0].append(idxs[1:])
    o = np.argsort(pos, kind="stable")
    blocks = next(iter(parts.values())).blocks
    return (Dataset(X=X.iloc[o].reset_index(drop=True), y=y.iloc[o].reset_index(drop=True),
                    weight=w.iloc[o].reset_index(drop=True), t1=t1[o], positions=pos[o],
                    blocks=blocks, index=index[o]),
            coin.iloc[o].reset_index(drop=True))


def fill_score_book(coins) -> int:
    """Score every coin's recent closed bars with the INSTALLED models, the
    way the server will, so its first ranking is against a full pool."""
    from livefeed import BarStore
    from monitor import SCOREBOOK, Monitor, evaluate

    n = 0
    for sym in coins:
        p1, p2 = model_paths(sym, INTERVAL)
        if not (p1.exists() and p2.exists()):
            continue
        mon = Monitor(sym, INTERVAL, p1, p2)
        bars = BarStore(sym, INTERVAL).load()
        if bars.empty:
            continue
        X = mon.features(bars)
        last = bars.index[-1]
        for judge, name in ((mon.h1, "A"), (mon.h2, "B")):
            a = evaluate(judge, bars, X, name, opened_at=last,
                         ends_at=last + judge.cfg.max_hold_bars * mon.delta,
                         bars_left=judge.cfg.max_hold_bars, symbol=sym)
        n += 1
        print(f"  score book: {sym} recorded (last reading ranks {a.rank:.3f})", flush=True)
    return n


def update_rules(coins) -> int:
    """Write the DECISION rules (the entry) into the installed pooled models
    without refitting them.

    The entry is how a call is traded, not what the model learned: nothing
    in the fit depends on it. Refitting to change it would also mint a new
    `rank_pool` and throw away the score book the server has built, so the
    rule is written into each installed model's config instead. Only files
    that already carry a pooled fit are touched.
    """
    import dataclasses

    n = 0
    for sym in coins:
        for path in model_paths(sym, INTERVAL):
            if not path.exists():
                continue
            judge = JudgeAgent.load(path)
            if not getattr(judge.cfg, "rank_pool", ""):
                print(f"  {path.name}: not a pooled model, left alone")
                continue
            judge.cfg = dataclasses.replace(judge.cfg, entry_offset_atr=ENTRY_OFFSET_ATR,
                                            entry_valid_bars=ENTRY_VALID_BARS)
            judge.save(path)
            n += 1
    print(f"entry rule written into {n} installed models "
          f"({ENTRY_OFFSET_ATR:g} ATR, {ENTRY_VALID_BARS} bars)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--cache", default="data_cache/train_frames_4h",
                   help="where the per-coin feature frames are cached")
    p.add_argument("--coins", default=",".join(COINS))
    p.add_argument("--stop-buffer", type=float, default=STOP_BUFFER_ATR)
    p.add_argument("--update-rules", action="store_true",
                   help="write the entry rule into the installed models; no refit")
    a = p.parse_args(argv)
    if a.update_rules:
        return update_rules([c.strip().upper() for c in a.coins.split(",") if c.strip()])

    PD.INTERVAL = INTERVAL
    PD.CACHE = Path(a.cache)
    coins = [c.strip().upper() for c in a.coins.split(",") if c.strip()]
    k, h1, h2 = barriers_for(INTERVAL)
    pool_id = f"crypto-4h-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    print(f"pooled 4h fit over {len(coins)} coins, structure barriers, {h2}-bar window, "
          f"stop {a.stop_buffer:g} ATR past its level; h1 = long, h2 = short; "
          f"pool {pool_id}", flush=True)

    frames = {}
    for sym in coins:
        t0 = time.time()
        got = PD.frames_for(sym)
        if got is None:
            print(f"  {sym}: no frames, skipped", flush=True)
            continue
        frames[sym] = got
        print(f"  {sym}: {len(got[0]):,} bars to {got[0].index[-1]:%Y-%m-%d %H:%M} "
              f"[{time.time() - t0:.0f}s]", flush=True)

    verdict = {"symbol": "POOLED", "interval": INTERVAL, "source": "pooled",
               "coins": sorted(frames), "rank_pool": pool_id,
               "stop_buffer_atr": a.stop_buffer, "bag_seeds": list(BAG_SEEDS),
               "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "horizons": {}}
    fitted = {}
    for slot, hold in ((1, h1), (2, h2)):
        side = slot_side(INTERVAL, slot)
        cfg = Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k, geometry="structure",
                           side=side, stop_buffer_atr=a.stop_buffer, rank_pool=pool_id,
                           entry_offset_atr=ENTRY_OFFSET_ATR, entry_valid_bars=ENTRY_VALID_BARS,
                           bag_seeds=BAG_SEEDS)
        parts = {}
        for sym, (bars, fr, warm) in frames.items():
            ds = JudgeAgent(cfg).build(bars, warmup=warm, **fr)
            if len(ds) > 500:
                parts[sym] = ds
        dropped = PD.scale_bound_columns(parts)
        pooled, _ = pool_4h(parts)
        cols = [c for c in pooled.X.columns if c not in dropped]
        judge = JudgeAgent(cfg)
        t0 = time.time()
        report = judge.fit(pooled, columns=cols, with_shuffle=True,
                           with_ablation=False, with_importance=False)
        ev = report.evaluation
        ok = beats_shuffle(ev.auc, ev.shuffle_auc, ev.auc_spread)
        print(f"  h{slot} ({side}): AUC {ev.auc:.3f}  spread {ev.auc_spread:.3f}  "
              f"shuffle {ev.shuffle_auc:.3f}  {'CLEARS' if ok else 'fails'}  "
              f"({len(pooled):,} samples, {len(parts)} coins, {len(cols)} columns) "
              f"[{time.time() - t0:.0f}s]", flush=True)
        verdict["horizons"][f"h{slot}"] = {
            "auc": round(float(ev.auc), 4), "shuffle": round(float(ev.shuffle_auc), 4),
            "spread": round(float(ev.auc_spread), 4), "effective_n": int(len(pooled)),
            "beats_shuffle": bool(ok), "hold": hold, "geometry": "structure", "side": side,
        }
        fitted[slot] = judge

    if not all(h["beats_shuffle"] for h in verdict["horizons"].values()):
        print("\nA SIDE FAILED ITS CONTROL -- nothing installed.", flush=True)
        return 1
    if a.dry_run:
        print("\ndry run: nothing written", flush=True)
        return 0

    tmp = Path(tempfile.mkdtemp())
    for slot, judge in fitted.items():
        judge.save(tmp / f"pooled_h{slot}.joblib")
    for sym in sorted(frames):
        p1, p2 = model_paths(sym, INTERVAL)
        for slot, dst in ((1, p1), (2, p2)):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp / f"pooled_h{slot}.joblib", dst)
        eval_path(sym, INTERVAL).write_text(json.dumps(dict(verdict, symbol=sym), indent=1))
    print(f"\ninstalled the pooled 4h models under {len(frames)} coins, with verdicts", flush=True)

    n = fill_score_book(sorted(frames))
    print(f"score book filled from {n} coins", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
