#!/usr/bin/env python3
"""Are the Alpaca keys present and working?

    python3 check_alpaca.py

WHY THIS EXISTS RATHER THAN "just try the screener"
    A wrong key and a missing key and a rate limit all end up as an empty
    table, which reads as "nothing matched your filters". This separates them
    and says which one it is.

WHAT IT DELIBERATELY NEVER PRINTS
    The key or any part of it. Not a prefix, not a suffix, not a length that
    would narrow a guess. It reports presence, and whether Alpaca accepted it,
    and nothing else — because the obvious next thing anyone does with output
    like this is paste it into a chat window.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from marketdata.alpaca import Alpaca, AlpacaError, credentials


def main() -> int:
    if credentials() is None:
        print("  keys       MISSING")
        print("\n  Set ALPACA_KEY_ID and ALPACA_SECRET_KEY in the "
              "environment (or .env on the server).")
        return 1
    print("  keys       present")

    client = Alpaca()
    try:
        universe = client.universe()
    except AlpacaError as e:
        print(f"  auth       REJECTED — {e}")
        return 1
    print(f"  universe   {len(universe):,} tradable US equities")

    # A real bar request, through the same code path the screener uses, so
    # this proves the SIP feed and the 15-minute clamp as well as the key.
    try:
        got = client.bars(["AAPL", "MSFT", "SPY"],
                          start=datetime.now(timezone.utc) - timedelta(days=30))
    except AlpacaError as e:
        print(f"  bars       FAILED — {e}")
        return 1

    if not got:
        print("  bars       EMPTY — key works, but no bars came back")
        return 1

    for sym, df in sorted(got.items()):
        last = df.index[-1].date()
        print(f"  {sym:<10} {len(df):>4} daily bars, latest {last}, "
              f"last close {df['close'].iloc[-1]:,.2f}, "
              f"volume {df['volume'].iloc[-1]:,.0f}")

    # The volume is the tell. AAPL trades tens of millions of shares a day on
    # the consolidated tape and roughly 2.5% of that on IEX alone, so a
    # plausible-looking couple of million here means the feed silently fell
    # back to IEX and every volume filter in the screener would be wrong.
    aapl = got.get("AAPL")
    if aapl is not None and len(aapl):
        v = float(aapl["volume"].iloc[-1])
        if v < 10_000_000:
            print(f"\n  WARNING: AAPL volume {v:,.0f} looks like the IEX feed, "
                  "not the full consolidated tape.")
            print("  Expect tens of millions. Check the `feed` parameter.")
            return 1
        print(f"\n  feed check AAPL volume {v:,.0f} — consolidated, as expected")

    print("\n  Alpaca is working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
