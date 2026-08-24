"""Detector runner — Agents 1 (patterns), 2 (indicators), 3 (news).

    python3 main.py --offline                 # synthetic bars, no network
    python3 main.py                           # BTCUSDT 1h from Binance
    python3 main.py --agent 2                 # indicators only
    python3 main.py --agent 3 --offline       # news, with synthetic headlines
    python3 main.py --symbol ETHUSDT --start 2024-06-01 --out features.parquet

Prints a human-readable read of the most recent bar per agent, then the
feature frame Agent 5 will eventually consume. Those are two different
channels: the trace explains, the frame predicts, and they must never cross.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import config
from agent1 import Agent1Config, PatternAgent
from agent2 import Agent2Config, IndicatorAgent
from agent3 import Agent3Config, NewsAgent


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Run the detector agents over candles.")
    p.add_argument("--agent", default="all", choices=("1", "2", "3", "all"),
                   help="which detector(s) to run (default: all)")
    p.add_argument("--asset", default="BTC", help="asset symbol for news scoping")
    p.add_argument("--news-jsonl", default=None,
                   help="replay a stored news corpus instead of the local store")
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


def report(name: str, agent, bars: pd.DataFrame, groups, rows: int) -> pd.DataFrame:
    """Run one detector, print its trace and per-block coverage."""
    need = getattr(agent, "required_bars", lambda _b: agent.warmup_bars)(bars)
    if len(bars) <= need:
        print(f"\nWARNING: {name} wants at least {need:,} bars, got {len(bars):,}. "
              f"Some columns will stay NaN.")

    features = agent.compute(bars)
    print()
    print(agent.latest(bars))          # the trace channel — humans only

    warm = min(agent.warmup_bars, max(0, len(features) - 1))
    print(f"\n{name}: {features.shape[0]:,} rows x {features.shape[1]} columns")
    for group, cols in groups.items():
        filled = features[list(cols)].iloc[warm:].notna().mean().mean()
        print(f"  {group:<11} {len(cols):>2} cols   {filled:5.1%} populated")
    return features


def load_news(args, bars):
    """News items for the bar range.

    Offline mode generates deterministic synthetic headlines. Otherwise the
    local store is read; an empty store is a normal, well-defined state
    (Agent 3 emits zero counts and NaN sentiment), not an error.
    """
    if args.news_jsonl:
        from newsfeed import JSONLReplay
        return JSONLReplay(Path(args.news_jsonl)).fetch()
    if args.offline:
        from tests.synthetic_news import make_news
        return make_news(bars, n=max(20, len(bars) // 20))

    import config as _cfg
    from newsfeed import JSONLNewsStore
    items = JSONLNewsStore(_cfg.DATA_CACHE / "news").load_items()
    if not items:
        print("  no stored news items — Agent 3 will report zero counts.\n"
              "  Collect some first, e.g.:\n"
              "    python3 -c \"from newsfeed import BinanceAnnouncements, JSONLNewsStore; \\\n"
              "      import config; s=JSONLNewsStore(config.DATA_CACHE/'news'); \\\n"
              "      print(s.append_items(BinanceAnnouncements(pages=2).fetch()), 'added')\"")
    return items


def main(argv=None) -> int:
    args = parse_args(argv)
    bars = load_bars(args)

    import agent1.schema as a1s
    import agent2.schema as a2s

    frames = {}
    warmups = []
    if args.agent in ("1", "all"):
        a1 = PatternAgent(Agent1Config(htf_rule=args.htf, break_mode=args.break_mode))
        frames["agent1"] = report("Agent 1 (patterns)", a1, bars,
                                  a1s.FEATURE_GROUPS, args.rows)
        warmups.append(a1.warmup_bars)
    if args.agent in ("2", "all"):
        a2 = IndicatorAgent(Agent2Config(htf_rule=args.htf))
        frames["agent2"] = report("Agent 2 (indicators)", a2, bars,
                                  a2s.FEATURE_GROUPS, args.rows)
        warmups.append(a2.required_bars(bars))
    if args.agent in ("3", "all"):
        import agent3.schema as a3s
        items = load_news(args, bars)
        a3 = NewsAgent(Agent3Config(asset=args.asset))
        scores = a3.score_items(items)
        print(f"\n{len(items)} news items, {len(scores)} scored "
              f"({a3.scorer.name})")
        f3 = a3.compute(bars, items, scores)
        print()
        print(a3.latest(bars, items, scores))
        print(f"\nAgent 3 (news): {f3.shape[0]:,} rows x {f3.shape[1]} columns")
        for group, cols in a3s.FEATURE_GROUPS.items():
            filled = f3[list(cols)].notna().mean().mean()
            print(f"  {group:<11} {len(cols):>2} cols   {filled:5.1%} populated")
        frames["agent3"] = f3
        warmups.append(a3.warmup_bars)

    combined = pd.concat(frames.values(), axis=1)
    warm = min(max(warmups, default=0), max(0, len(combined) - 1))
    usable = combined.iloc[warm:]

    print(f"\nCombined: {combined.shape[1]} columns, "
          f"{len(usable):,} usable rows after a {warm:,}-bar warmup")
    if combined.shape[1] > 20:
        print("  Note: feed Agent 5 10-15 of these to start, not all of them. After\n"
              "  uniqueness weighting the effective sample size is a few thousand\n"
              "  independent observations, and that many features on that many\n"
              "  samples will confidently find patterns that are not there.")

    with pd.option_context("display.width", 200, "display.max_columns", 10):
        print(f"\nLast {args.rows} rows (first 8 columns):")
        print(usable.iloc[-args.rows:, :8].round(3))

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix == ".parquet":
            combined.to_parquet(out)
        else:
            combined.to_csv(out)
        print(f"\nWrote {out}  ({len(combined):,} rows x {combined.shape[1]} cols)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
