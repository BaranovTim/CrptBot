#!/usr/bin/env python3
"""Live data collector.

    python3 collect.py                              # BTCUSDT 1h, poll, + news
    python3 collect.py --interval 1m --transport stream
    python3 collect.py --symbol BTCUSDT --symbol ETHUSDT --interval 1h --interval 4h
    python3 collect.py --once                       # single cycle, then exit
    python3 collect.py --status                     # what is stored, and any gaps
    python3 collect.py --repair                     # re-fetch missing bars

Records closed bars and news into data_cache/live/ and data_cache/news/.
Does not trade.
"""
from __future__ import annotations

import argparse
import logging
import sys

from livefeed import BarStore, build_collector, interval_delta


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Collect live market data.")
    p.add_argument("--symbol", action="append", default=None,
                   help="repeatable; default BTCUSDT")
    p.add_argument("--interval", action="append", default=None,
                   help="repeatable; default 1h")
    p.add_argument("--market", default="futures/um", help="futures/um or spot")
    p.add_argument("--transport", default="poll", choices=("poll", "stream"),
                   help="poll = REST timer (no extra deps); "
                        "stream = websocket (needs `websockets`)")
    p.add_argument("--no-news", action="store_true", help="skip news collection")
    p.add_argument("--news-interval", type=int, default=300,
                   help="seconds between news polls (default 300)")
    p.add_argument("--once", action="store_true",
                   help="run one cycle and exit - good for cron")
    p.add_argument("--status", action="store_true",
                   help="print what is stored, including gaps, then exit")
    p.add_argument("--repair", action="store_true",
                   help="re-fetch any missing bars, then exit")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")

    symbols = args.symbol or ["BTCUSDT"]
    intervals = args.interval or ["1h"]

    if args.status:
        for s in symbols:
            for i in intervals:
                print(BarStore(s, i).status(interval_delta(i)))
                print()
        return 0

    collector = build_collector(symbols, intervals,
                                with_news=not args.no_news,
                                transport=args.transport,
                                market=args.market,
                                news_interval=args.news_interval)

    if args.repair:
        for kc in collector.klines:
            kc.backfill()
            filled = kc.repair_gaps()
            print(f"{kc.symbol} {kc.interval}: {filled} bars repaired")
            print(kc.store.status(interval_delta(kc.interval)))
        return 0

    if args.once:
        for kc in collector.klines:
            kc.backfill()
            kc.poll_once()
            print(f"{kc.symbol} {kc.interval}: {kc.stats}")
        if collector.news is not None:
            collector.news.poll_once()
            print(f"news: {collector.news.stats}")
        return 0

    collector.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
