"""Agent 1 runner.

    python3 main.py --offline                 # synthetic bars, no network
    python3 main.py                           # BTCUSDT 1h from Binance
    python3 main.py --symbol ETHUSDT --start 2024-06-01 --out features.parquet

Prints a human-readable read of the most recent bar, then the feature frame
that Agent 5 will eventually consume. Those are two different channels: the
trace explains, the frame predicts, and they must never cross.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import config
from agent1 import Agent1Config, PatternAgent
from agent1.schema import FEATURE_GROUPS


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Run Agent 1 (pattern detector) over candles.")
    p.add_argument("--symbol", default=config.SYMBOL)
    p.add_argument("--interval", default=config.INTERVAL)
    p.add_argument("--start", default=config.HISTORY_START)
    p.add_argument("--end", default=None)
    p.add_argument("--market", default=config.MARKET,
                   help="futures/um (default) or spot")
    p.add_argument("--offline", action="store_true",
                   help="use deterministic synthetic bars instead of downloading")
    p.add_argument("--bars", type=int, default=1500,
                   help="number of synthetic bars when --offline")
    p.add_argument("--htf", default="4h", help="higher timeframe for context features")
    p.add_argument("--break-mode", default="close", choices=("close", "wick"))
    p.add_argument("--out", default=None,
                   help="write the feature frame to this .parquet or .csv")
    p.add_argument("--rows", type=int, default=8, help="rows of features to print")
    return p.parse_args(argv)


def load_bars(args) -> pd.DataFrame:
    if args.offline:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from tests.synthetic import make_bars
        print(f"Using {args.bars} synthetic bars (offline mode).")
        return make_bars(args.bars, interval=args.interval)

    from marketdata import load_klines
    print(f"Loading {args.symbol} {args.interval} from data.binance.vision "
          f"({args.market}) since {args.start} ...")
    bars = load_klines(
        symbol=args.symbol, interval=args.interval, start=args.start,
        end=args.end, market=args.market, cache_dir=config.DATA_CACHE,
    )
    print(f"  {len(bars):,} bars  {bars.index[0]} -> {bars.index[-1]}")
    return bars


def main(argv=None) -> int:
    args = parse_args(argv)
    bars = load_bars(args)

    agent = PatternAgent(Agent1Config(htf_rule=args.htf, break_mode=args.break_mode))
    if len(bars) <= agent.warmup_bars:
        print(f"\nWARNING: only {len(bars)} bars but warmup is {agent.warmup_bars}. "
              f"Early features will be sparse.")

    features = agent.compute(bars)
    latest = agent.latest(bars)

    print()
    print(latest)          # the trace channel — for humans only

    print(f"\nFeature frame: {features.shape[0]:,} rows x {features.shape[1]} columns")
    for group, cols in FEATURE_GROUPS.items():
        filled = features[list(cols)].iloc[agent.warmup_bars:].notna().mean().mean()
        print(f"  {group:<10} {len(cols):>2} cols   {filled:5.1%} populated")

    usable = features.iloc[agent.warmup_bars:]
    print(f"\nUsable rows after {agent.warmup_bars}-bar warmup: {len(usable):,}")

    with pd.option_context("display.width", 200, "display.max_columns", 12):
        print(f"\nLast {args.rows} rows (first 10 columns):")
        print(usable.iloc[-args.rows:, :10].round(3))

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix == ".parquet":
            features.to_parquet(out)
        else:
            features.to_csv(out)
        print(f"\nWrote {out}  ({len(features):,} rows)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
