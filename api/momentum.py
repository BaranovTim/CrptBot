"""The weekly momentum rotation: long the three coins with the best 30-day
return, short the three with the worst, rebalanced every Monday 00:00 UTC.

WHAT IT IS, MEASURED (research/new_strategies.py, research/QUANT.md)
    Cross-sectional momentum -- the one crypto factor the literature rates
    strong -- on the fifteen coins this server serves, 2021 -> 2026, net of
    0.10% per position per rebalance. Rebalanced at every one of the 42
    four-hour slots of the week, it made money at all of them: Sharpe 0.62 to
    1.31, median 1.00. On the ten coins already established in 2021 (a check
    on the list being today's winners), median 0.70. Long-only, the best
    three over holding all ten: median 0.42. The 30-day reversal loses as
    much as the momentum makes, which is the shape a real effect has.

    It is a different kind of recommendation from a 4h call: a basket held
    for a week, both legs, sized equally. The short leg needs a futures
    account; the long leg alone is the spot version, and weaker.

WHY MONDAY 00:00 UTC
    It is the natural time for a person, and nothing about a weekday should
    matter to a 30-day signal. It was, in fact, historically the weakest of
    the 42 phases (0.62); the median is the expectation, not the phase.

PURE: takes daily closes, returns the picks. The service feeds it the bar
stores and caches the result.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

LOOKBACK_DAYS = 30
PICKS = 3
MIN_COINS = 8          # below this there is no cross-section to rank


def week_start(now: pd.Timestamp) -> pd.Timestamp:
    """The Monday 00:00 UTC at or before `now`."""
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    day = now.normalize()
    return day - pd.Timedelta(days=day.dayofweek)


def _asof(s: pd.Series, t: pd.Timestamp) -> Optional[float]:
    """The last close at or before `t` (a daily bar closes 23:59:59.999)."""
    s = s[s.index <= t]
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if np.isfinite(v) and v > 0 else None


def ranking(closes: Dict[str, pd.Series], at: pd.Timestamp) -> List[dict]:
    """Every coin's 30-day return as of `at`, best first. A coin with no
    close in the week before `at`, or none 30 days earlier, is left out
    rather than ranked on a stale price."""
    out = []
    for sym, s in closes.items():
        if s is None or s.empty:
            continue
        s = s.sort_index()
        recent = s[(s.index <= at) & (s.index > at - pd.Timedelta(days=7))]
        now = _asof(s, at)
        then = _asof(s, at - pd.Timedelta(days=LOOKBACK_DAYS))
        if recent.empty or now is None or then is None:
            continue
        out.append({"symbol": sym, "ret_30d": (now / then - 1.0) * 100.0, "close": now})
    out.sort(key=lambda r: r["ret_30d"], reverse=True)
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return out


def rotation(closes: Dict[str, pd.Series], now: pd.Timestamp,
             prices: Optional[Dict[str, float]] = None) -> dict:
    """This week's picks, how they have done since Monday, and the live
    ranking ("if the week ended now").

    `prices` are live prices for the week-so-far figures; without them the
    latest close is used.
    """
    start = week_start(now)
    ranked = ranking(closes, start)
    live = ranking(closes, pd.Timestamp(now) if pd.Timestamp(now).tzinfo else
                   pd.Timestamp(now).tz_localize("UTC"))
    ok = len(ranked) >= MIN_COINS

    def since_monday(r: dict) -> Optional[float]:
        px = (prices or {}).get(r["symbol"])
        if px is None:
            px = _asof(closes[r["symbol"]], pd.Timestamp(now))
        return (px / r["close"] - 1.0) * 100.0 if px else None

    longs = [dict(r, week_pct=since_monday(r)) for r in ranked[:PICKS]] if ok else []
    shorts = [dict(r, week_pct=since_monday(r)) for r in ranked[-PICKS:][::-1]] if ok else []
    week = None
    lw = [r["week_pct"] for r in longs if r["week_pct"] is not None]
    sw = [r["week_pct"] for r in shorts if r["week_pct"] is not None]
    if lw and sw:
        # half the capital a side, as it was measured
        week = 0.5 * float(np.mean(lw)) - 0.5 * float(np.mean(sw))
    return {
        "week_start": start.isoformat(),
        "next_rebalance": (start + pd.Timedelta(days=7)).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "picks": PICKS,
        "universe": len(ranked),
        "available": ok,
        "longs": longs,
        "shorts": shorts,
        "week_pct": week,
        "ranking": ranked,
        "live_ranking": live,
        "measured": ("Rebalanced at each of the week's 42 four-hour slots, 2021-2026, "
                     "it made money at every one: Sharpe 0.62-1.31, median 1.00, net of "
                     "fees. On the ten coins already established in 2021, median 0.70. "
                     "Long-only (spot), the three longs beat holding all ten, median 0.42."),
    }
