#!/usr/bin/env python3
"""Build the screener table. Run daily, after the US close.

    python3 build_screener.py                  # everything
    python3 build_screener.py --no-filings     # prices only, fast
    python3 build_screener.py --symbols AAPL,MSFT,NVDA

WHY A SCRIPT AND NOT A REQUEST HANDLER
    Thirteen thousand symbols is minutes of work. Behind an HTTP request that
    is a phone on a timeout; as a cron job it is a file that already exists by
    the time anyone opens the page.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

sys.path.insert(0, ".")

from screener import universe


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", help="comma separated, instead of the "
                                      "whole tradable universe")
    ap.add_argument("--no-filings", action="store_true",
                    help="skip SEC EDGAR; company fields stay blank")
    ap.add_argument("--no-short-interest", action="store_true")
    ap.add_argument("--refresh-short-interest", action="store_true",
                    help="pull a fresh FINRA file first")
    ap.add_argument("--download-filings", action="store_true",
                    help="fetch SEC's bulk companyfacts.zip first (large, "
                         "and the company fields are all blank without it)")
    ap.add_argument("--crypto", action="store_true",
                    help="build the crypto table instead of the equity one")
    ap.add_argument("--filings-only", action="store_true",
                    help="re-join filings and short interest onto the table "
                         "already on disk, without re-fetching prices")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.refresh_short_interest:
        from marketdata import finra
        try:
            finra.save(finra.fetch_short_interest())
        except Exception as e:
            # A stale short interest file is far better than no table.
            print(f"  short interest refresh failed, using what is on disk: {e}")

    if args.crypto:
        from screener import crypto as _c

        t0 = time.time()
        rows = _c.build()
        _c.save(rows)
        trained = sum(1 for r in rows.values() if r.get("trained"))
        print(f"\n  {len(rows):,} crypto pairs in {time.time() - t0:.0f}s "
              f"({trained} with a fitted model)")
        return 0

    if args.download_filings:
        from marketdata.edgar import download_bulk
        download_bulk()

    symbols = ([s.strip().upper() for s in args.symbols.split(",")]
               if args.symbols else None)

    t0 = time.time()
    if args.filings_only:
        # The eighteen minutes is the price fetch. Re-running it to correct a
        # filings join that failed would be eighteen minutes to fix a
        # thirty-second mistake.
        rows = universe.load().get("rows") or {}
        if not rows:
            print("  no table on disk yet — run without --filings-only first")
            return 1
        universe.rejoin(rows,
                        with_filings=not args.no_filings,
                        with_short_interest=not args.no_short_interest)
    else:
        rows = universe.build(symbols=symbols,
                              with_filings=not args.no_filings,
                              with_short_interest=not args.no_short_interest)
    universe.save(rows)

    # What actually got populated, printed rather than assumed. "The job ran"
    # and "the job produced anything usable" looked identical for the whole
    # life of Agent 4 until somebody counted the columns.
    counts = {}
    for row in rows.values():
        for k, v in row.items():
            if v is not None:
                counts[k] = counts.get(k, 0) + 1
    print(f"\n  {len(rows):,} symbols in {time.time() - t0:.0f}s\n")
    for k in sorted(counts, key=lambda x: -counts[x]):
        pct = 100.0 * counts[k] / max(len(rows), 1)
        print(f"    {k:<24} {counts[k]:>6,}  {pct:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
