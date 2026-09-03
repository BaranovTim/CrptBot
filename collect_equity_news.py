#!/usr/bin/env python3
"""Poll equity news into its own store.

    python3 collect_equity_news.py --symbol AAPL --symbol NVDA

WHY A SEPARATE SCRIPT AND STORE
    The crypto collector polls five publisher feeds every five minutes. This
    polls SEC submissions once per followed symbol, and the SEC's fair-access
    limit is ten requests a second — a different shape of job with a different
    budget, sharing nothing but the store format.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, ".")

STORE = Path("data_cache") / "news_equities"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", action="append", default=[],
                    help="repeatable; the symbols to fetch filings for")
    ap.add_argument("--market-only", action="store_true",
                    help="skip per-symbol filings")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    from newsfeed.equities import MarketNews, TickerNews
    from newsfeed.store import JSONLNewsStore

    store = JSONLNewsStore(STORE)
    items = []
    try:
        items.extend(MarketNews().fetch())
    except Exception as e:
        print(f"  market feeds failed: {e}")
    if not args.market_only and args.symbol:
        try:
            items.extend(TickerNews(args.symbol).fetch())
        except Exception as e:
            print(f"  filings failed: {e}")

    added = store.append_items(items)
    print(f"  {len(items)} fetched, {added} new")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
