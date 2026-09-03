#!/usr/bin/env python3
"""Seed and train equity models, the same pipeline the crypto side uses.

    python3 train_stocks.py --symbol AAPL --symbol NVDA
    python3 train_stocks.py --symbol AAPL --intervals 1h,4h,1d

WHY THIS IS A THIN WRAPPER AND NOT A PIPELINE
    It downloads bars into the store and calls `train.py`. Everything that
    matters — triple-barrier labels, purged folds with an embargo, isotonic
    calibration, the shuffle test — is the same code that fits BTC. A separate
    equities pipeline would be a second copy of every subtle thing this one
    already gets right, and the two would drift.

READ THIS BEFORE TRUSTING AN EQUITY MODEL
    OVERNIGHT GAPS. Triple-barrier labelling assumes a barrier is touched at
    its level. That is true intrabar in a market that never closes and false
    across an overnight halt: a stop between Friday's close and Monday's open
    fills at the gap, not at the stop. So the labels are slightly kinder than
    reality, and a fitted model reads better than it would trade.

    ONE COST FOR EVERY NAME. 0.05% round trip assumes a liquid large cap. A
    thin small cap costs several times that, and its model will promise more
    than it can deliver.

    NO EARNINGS AWARENESS. A quarterly report is the largest single-day move
    most stocks make, it is scheduled, and nothing here knows the date. The
    model sees the gap as ordinary volatility.

    None of these make the models useless. All of them mean the numbers are
    optimistic, in the same direction, by an amount nobody here has measured.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time

sys.path.insert(0, ".")

DEFAULT_INTERVALS = "1h,4h,1d"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", action="append", default=[], required=False,
                    help="repeatable. Defaults to the followed list if empty.")
    ap.add_argument("--intervals", default=DEFAULT_INTERVALS,
                    help=f"default {DEFAULT_INTERVALS}. 1m and 5m are "
                         "accepted but rarely worth it: the cost floor eats "
                         "the edge long before the model finds one.")
    ap.add_argument("--seed-only", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    symbols = [s.upper() for s in args.symbol]
    if not symbols:
        print("  no symbols given (use --symbol AAPL)")
        return 1

    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]

    from marketdata.equity_seed import seed_with_context

    for sym in symbols:
        for iv in intervals:
            try:
                # ...and the higher timeframe the model reads alongside it.
                # Without it training exits silently with no model.
                seed_with_context(sym, iv)
            except Exception as e:
                print(f"  seed {sym} {iv} failed: {e}")

    if args.seed_only:
        return 0

    for sym in symbols:
        t0 = time.time()
        print(f"\n  training {sym} on {','.join(intervals)}")
        # `--no-seed`, because the bars are already in the store and the
        # crypto seeder would go looking for them on Binance.
        r = subprocess.run(
            [sys.executable, "train.py", "--symbol", sym,
             "--intervals", ",".join(intervals), "--no-seed"],
            check=False)
        print(f"  {sym}: exit {r.returncode} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
