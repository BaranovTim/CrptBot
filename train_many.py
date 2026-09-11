#!/usr/bin/env python3
"""Fit many symbols at once, on a machine that has cores to spare.

    python3 train_many.py AVAXUSDT LINKUSDT LTCUSDT
    python3 train_many.py --file coins.txt --workers 6
    python3 train_many.py --file coins.txt --dry-run     # what it would do

WHY THIS EXISTS
    `api/trainer.py` runs one fit at a time on the droplet, which has one
    vCPU and is already serving the API. That is correct for a fit started
    from the phone -- two at once would take the dashboard down -- and
    hopeless for filling a hundred coins.

ONE PROCESS PER SYMBOL, NOT PER FIT
    Deliberate. `train.py` seeds the bar store for the interval it is
    fitting AND for the higher timeframe above it, so two processes on the
    same symbol's 1h and 4h would write the same store at the same time.
    Across symbols there is nothing shared, so that is where the
    parallelism goes. Each process still walks its own intervals in order.

THE LIMIT IS BINANCE, NOT YOUR CPU
    A cold symbol downloads years of klines before it fits anything, and
    Binance meters that per IP. Enough parallel seeds and it answers 429,
    then 418, and a ban lasts minutes to days. So the default worker count
    is well under the core count, and raising it is a decision about the
    rate limit rather than about your CPU.

RESUMABLE
    A symbol whose models already exist is skipped, so an interrupted run
    continues where it stopped. `--force` refits anyway.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
OUT = Path("output")
LOGS = Path("data_cache") / "train_logs"


def already_done(symbol: str, intervals) -> bool:
    """True when every interval has both heads on disk."""
    return all((OUT / f"judge_{symbol}_{iv}_h{h}.joblib").exists()
               for iv in intervals for h in (1, 2))


def fit(symbol: str, intervals, force: bool) -> tuple:
    if not force and already_done(symbol, intervals):
        return symbol, "skipped", 0.0, ""
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"{symbol}.log"
    cmd = [sys.executable, "train.py", "--symbol", symbol,
           "--intervals", ",".join(intervals), "--no-tape"]
    started = time.time()
    with open(log, "w") as fh:
        # Three hours, matching api/trainer.py: a fit that has not finished
        # by then is stuck, and one stuck job must not hold a worker for the
        # rest of the run.
        try:
            r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                               timeout=3 * 3600)
            rc = r.returncode
        except subprocess.TimeoutExpired:
            return symbol, "timeout", time.time() - started, str(log)
    took = time.time() - started
    # EXIT 0 IS NOT SUCCESS -- same trap api/trainer.py documents. train.py
    # exits cleanly when a timeframe has too little history to fit, so the
    # files on disk are the only honest check.
    if rc != 0:
        return symbol, "failed", took, str(log)
    if not already_done(symbol, intervals):
        return symbol, "incomplete", took, str(log)
    return symbol, "done", took, str(log)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="*", help="e.g. AVAXUSDT LINKUSDT")
    p.add_argument("--file", help="one symbol per line; # comments allowed")
    p.add_argument("--intervals", default=",".join(TIMEFRAMES))
    p.add_argument("--workers", type=int, default=4,
                   help="parallel symbols. Default 4 -- bounded by Binance's "
                        "per-IP rate limit, not by your cores. Raise slowly "
                        "and watch for 429 in the logs.")
    p.add_argument("--force", action="store_true",
                   help="refit symbols that already have models")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would run, fit nothing")
    a = p.parse_args(argv)

    symbols = [s.strip().upper() for s in a.symbols if s.strip()]
    if a.file:
        for line in Path(a.file).read_text().splitlines():
            line = line.split("#", 1)[0].strip().upper()
            if line:
                symbols.append(line)
    # dedupe, keep order given
    seen, ordered = set(), []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    if not ordered:
        p.error("no symbols given")

    intervals = [i.strip() for i in a.intervals.split(",") if i.strip()]
    todo = [s for s in ordered if a.force or not already_done(s, intervals)]
    skip = len(ordered) - len(todo)

    print(f"{len(ordered)} symbols, {len(intervals)} timeframes each")
    if skip:
        print(f"  {skip} already trained, skipping (--force to refit)")
    print(f"  {len(todo)} to fit, {a.workers} at a time")
    if a.dry_run:
        for s in todo:
            print("   would fit", s)
        return 0
    if not todo:
        return 0

    started = time.time()
    counts = {}
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(fit, s, intervals, a.force): s for s in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            sym, state, took, log = fut.result()
            counts[state] = counts.get(state, 0) + 1
            mins = took / 60.0
            elapsed = (time.time() - started) / 60.0
            # Rate from finished work, so the estimate is measured rather
            # than assumed -- the first symbol is always slowest (cold
            # download) and a fixed per-symbol guess would be wrong all run.
            left = (elapsed / n) * (len(todo) - n)
            note = f"  see {log}" if state not in ("done", "skipped") else ""
            print(f"[{n}/{len(todo)}] {sym:<14} {state:<10} "
                  f"{mins:5.1f} min   ~{left:.0f} min left{note}", flush=True)

    print(f"\nfinished in {(time.time()-started)/60:.1f} min: "
          + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    return 0 if counts.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
