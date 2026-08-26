#!/usr/bin/env python3
"""The percentage. One reading of the whole pipeline, right now.

    python3 predict.py                  # one reading from the newest bar
    python3 predict.py --watch          # re-read each time a bar closes
    python3 predict.py --model m.joblib # a specific frozen model

NOT named signal.py: that shadows the stdlib `signal` module, and joblib
imports it deep inside sklearn. The failure is a baffling
`module 'signal' has no attribute 'SIGINT'` from a file you never touched.

This is the piece that turns everything else into an answer. It does NOT
train - it loads a model that was already fitted and frozen, computes the
detector features for the newest bars, and runs the frozen arithmetic.

    collect.py   records bars           (runs forever, prints no signal)
    main.py      trains and freezes     (runs once, slow)
    predict.py   reads the probability  (runs in milliseconds)

Training is batch and offline. Prediction is arithmetic on a fixed function.
Keeping them in separate commands is not tidiness - a model that refits on
every incoming bar is a model chasing noise, and the plan is explicit that
"training and predicting in real time" is the wrong shape.

WHAT THE NUMBER IS
------------------
A calibrated probability that a LONG entered at this bar's close reaches
+k_up ATR before -k_dn ATR, within max_hold bars. It is not "chance BTC goes
up". It is tied to specific barriers and a specific holding period, and it
means nothing without them - which is why they are printed alongside it.

The decision is not the probability. A 61% chance with a good payoff is a
trade; the same 61% with a bad one is not. EV after costs decides.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd

import config as project_config
from core import describe_bars_problem, utc_now


def load_bars(args) -> pd.DataFrame:
    """Bars from the live store, or downloaded history if asked."""
    if args.history:
        from marketdata import load_klines
        print(f"loading {args.symbol} {args.interval} history since {args.start} ...")
        return load_klines(args.symbol, args.interval, start=args.start,
                           end=None, cache_dir=project_config.DATA_CACHE)

    from livefeed import BarStore
    bars = BarStore(args.symbol, args.interval).load()
    if bars.empty:
        raise SystemExit(
            f"no collected bars for {args.symbol} {args.interval}.\n"
            f"  start recording:  python3 collect.py --symbol {args.symbol} "
            f"--interval {args.interval}\n"
            f"  or read history:  python3 predict.py --history")
    return bars


def build_features(bars: pd.DataFrame, args):
    """Run whichever detectors have enough history, and report what was used."""
    from agent1 import PatternAgent
    from agent2 import IndicatorAgent
    from agent4 import FlowAgent

    frames, warmups, used, skipped = {}, [], [], []
    for name, agent, kw in (("agent1", PatternAgent(), {}),
                            ("agent2", IndicatorAgent(), {}),
                            ("agent4", FlowAgent(), {})):
        need = getattr(agent, "required_bars", lambda _b: agent.warmup_bars)(bars)
        if len(bars) <= need:
            skipped.append(f"{name} (needs {need:,} bars, have {len(bars):,})")
            continue
        frames[name] = agent.compute(bars, **kw)
        warmups.append(agent.warmup_bars)
        used.append(name)
    return frames, max(warmups, default=0), used, skipped


def read_once(judge, bars: pd.DataFrame, args) -> Optional[str]:
    from agent5.dataset import build_dataset

    frames, warmup, used, skipped = build_features(bars, args)
    if not frames:
        raise SystemExit(
            "not enough history for any detector.\n  " + "\n  ".join(skipped))

    # build the SAME feature table the model was trained on. build_dataset
    # drops unlabelled rows by default, and the newest bars are exactly the
    # unlabelled ones - so labels are switched off here: we are predicting,
    # not training, and the future has not happened yet
    from agent5.labels import LabelResult
    import numpy as np

    n = len(bars)
    blank = LabelResult(
        y=pd.Series(0.0, index=bars.index), t1=pd.Series(np.arange(n, dtype=float),
                                                         index=bars.index),
        weight=pd.Series(1.0, index=bars.index),
        touch=pd.Series("n/a", index=bars.index),
        tp_pct=pd.Series(np.nan, index=bars.index),
        sl_pct=pd.Series(np.nan, index=bars.index))

    ds = build_dataset(bars, judge.cfg, warmup=warmup, labels=blank,
                       **{k: v for k, v in frames.items()})

    missing = [c for c in judge.columns if c not in ds.X.columns]
    if missing:
        raise SystemExit(
            f"the frozen model needs {len(missing)} columns this run did not "
            f"produce: {missing[:5]}{'...' if len(missing) > 5 else ''}\n"
            f"  detectors that ran: {', '.join(used) or 'none'}\n"
            f"  skipped: {'; '.join(skipped) or 'none'}\n"
            f"  the model must be used with the same blocks it was trained on.")

    out = [judge.latest(bars, ds.X)]
    if skipped:
        out.append("  NOTE: " + "; ".join(skipped) + " - not enough history")
    return "\n".join(out)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Read the current trading signal.")
    p.add_argument("--symbol", default=project_config.SYMBOL)
    p.add_argument("--interval", default=project_config.INTERVAL)
    p.add_argument("--model", default="output/judge.joblib",
                   help="frozen model from `main.py --judge --save-model`")
    p.add_argument("--history", action="store_true",
                   help="use downloaded history instead of the live store")
    p.add_argument("--start", default=project_config.HISTORY_START)
    p.add_argument("--watch", action="store_true",
                   help="keep running, re-reading when each bar closes")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    model_path = Path(args.model)
    if not model_path.exists():
        raise SystemExit(
            f"no frozen model at {model_path}.\n"
            f"  train one first:\n"
            f"    python3 main.py --judge --save-model {model_path} "
            f"--start 2024-01-01\n"
            f"  training needs a few thousand bars; the live store will not "
            f"have that for weeks.")

    from agent5 import JudgeAgent
    judge = JudgeAgent.load(model_path)

    bars = load_bars(args)
    problem = describe_bars_problem(bars)
    if problem:
        print(f"DATA QUALITY: {problem}\n")

    print(read_once(judge, bars, args))

    if not args.watch:
        return 0

    # re-read when a new bar closes. the store is the source of truth, so
    # this picks up whatever collect.py has written
    from livefeed import interval_delta
    step = interval_delta(args.interval)
    last_seen = bars.index[-1]
    print(f"\nwatching for new {args.interval} bars - Ctrl-C to stop")
    try:
        while True:
            time.sleep(20)
            fresh = load_bars(args)
            if fresh.index[-1] > last_seen:
                last_seen = fresh.index[-1]
                print()
                print(read_once(judge, fresh, args))
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
