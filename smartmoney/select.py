"""Who to follow: a record, not a rank.

"THE MOST SUCCESSFUL TRADES" IS THE WRONG SORT KEY ON ITS OWN
    A scalper with nine hundred tiny wins and one liquidation has the most
    successful trades on the board and a negative month. A lottery wallet
    that went 40x on one HYPE long has the best ROI and no record at all.
    So a trader qualifies on FOUR things at once, each of which the others
    can fake:

      the month made money and the account is real     leaderboard
      enough closed trades to be a record               fills
      most of them won, and the wins outweigh the losses   fills
      it was not one good week                          fills, by ISO week

    and the ranking score multiplies them, so being extraordinary on one axis
    cannot buy a place with a bad number on another.

WHAT A "TRADE" IS HERE
    A reducing fill -- one with realised PnL -- net of its fee. A position
    closed in three clips counts three times; that overstates the count for
    everybody equally and is why the minimum is 100, not 30.

WHERE THE NUMBERS COME FROM
    The leaderboard gives PnL / ROI / volume; the venue's `userFills` gives
    the newest 2,000 fills. For a busy account that is days, not months, so
    consistency is judged over the weeks the fills actually cover, and a
    record that covers fewer than two weeks does not qualify.
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from marketdata.hyperliquid import (fetch_leaderboard, iter_leaderboard,
                                    performance, symbol_for, user_fills)

log = logging.getLogger(__name__)

# the bar: see the module docstring for why each exists
MIN_ACCOUNT_USD = 100_000.0
MIN_MONTH_VOLUME_USD = 1_000_000.0
MIN_CLOSED_TRADES = 100
MIN_WIN_RATE = 0.55
MIN_PROFIT_FACTOR = 1.2
MIN_WEEKS_COVERED = 2
# THE BOT FILTER. The first live selection was twenty-five accounts at "100%
# of 2,000, profit factor 99": market makers and basis arbitrage, whose
# fills only ever close in profit because the risk sits elsewhere, at
# 100+ fills a day, some of them in nothing but spot indices. None of them
# has a view to follow. A record with no losing fill is not a trading
# record, and a hundred closes a day is not a position.
MAX_WIN_RATE = 0.90
MAX_PROFIT_FACTOR = 20.0    # no realised losses at all = losers held, not traded
MAX_FILLS_PER_DAY = 30.0
MIN_ACTIVE_DAYS = 6
CONSISTENCY = 0.6           # share of covered weeks that must be positive
LOOKBACK_DAYS = 90
# Measured on the live board: of the top 150 by monthly profit, 127 fail the
# bot filter -- the biggest numbers on the board are made by machines. Real
# discretionary records sit further down, so the pool is deep and the fills
# read (one call each, once a day) is what finds them.
CANDIDATES = 400
TRACK = 25                  # how many end up followed


@dataclass
class TraderStats:
    address: str
    account_value: float
    pnl_30d: float
    roi_30d: float
    volume_30d: float
    pnl_all: float
    closed_trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    weeks_covered: int = 0
    weeks_positive: int = 0
    days_covered: float = 0.0
    active_days: int = 0            # distinct days with a reducing fill
    fills_per_day: float = 0.0
    coins: List[str] = field(default_factory=list)   # Binance symbols traded
    score: float = 0.0
    display_name: str = ""

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "TraderStats":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: d[k] for k in keys if k in d})

    @property
    def qualifies(self) -> bool:
        return (self.closed_trades >= MIN_CLOSED_TRADES
                and MIN_WIN_RATE <= self.win_rate <= MAX_WIN_RATE
                and MIN_PROFIT_FACTOR <= self.profit_factor <= MAX_PROFIT_FACTOR
                and self.fills_per_day <= MAX_FILLS_PER_DAY
                and self.active_days >= MIN_ACTIVE_DAYS
                and bool(self.coins)                  # perpetuals, not spot indices
                and self.weeks_covered >= MIN_WEEKS_COVERED
                # most of the covered weeks positive: one good week is not a record
                and self.weeks_positive >= math.ceil(CONSISTENCY * self.weeks_covered))


def candidates(path: Path, limit: int = CANDIDATES) -> List[TraderStats]:
    """Leaderboard rows worth reading fills for: real accounts that made
    money this month and over their life, ranked by the month."""
    out: List[TraderStats] = []
    for row in iter_leaderboard(path):
        try:
            acct = float(row.get("accountValue") or 0)
        except (TypeError, ValueError):
            continue
        m = performance(row, "month")
        a = performance(row, "allTime")
        if (acct < MIN_ACCOUNT_USD or m["pnl"] <= 0 or a["pnl"] <= 0
                or m["vlm"] < MIN_MONTH_VOLUME_USD):
            continue
        out.append(TraderStats(
            address=str(row.get("ethAddress", "")).lower(),
            account_value=acct, pnl_30d=m["pnl"], roi_30d=m["roi"],
            volume_30d=m["vlm"], pnl_all=a["pnl"],
            display_name=str(row.get("displayName") or ""),
        ))
    out.sort(key=lambda t: -t.pnl_30d)
    return out[:limit]


def fill_stats(fills: Iterable[dict], now: Optional[datetime] = None,
               lookback_days: int = LOOKBACK_DAYS) -> dict:
    """The record inside one address's fills. Pure; testable."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    gains = losses = 0.0
    wins = trades = 0
    by_week: Dict[str, float] = {}
    coins: set = set()
    days: set = set()
    oldest: Optional[datetime] = None
    for f in fills:
        try:
            t = datetime.fromtimestamp(int(f.get("time", 0)) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
        if t < cutoff:
            continue
        oldest = t if oldest is None or t < oldest else oldest
        sym = symbol_for(str(f.get("coin", "")))
        if sym:
            coins.add(sym)
        try:
            pnl = float(f.get("closedPnl", 0) or 0)
            fee = float(f.get("fee", 0) or 0)
        except (TypeError, ValueError):
            continue
        if pnl == 0.0 and not str(f.get("dir", "")).startswith("Close"):
            continue                                 # an opening fill
        net = pnl - fee
        trades += 1
        if net > 0:
            wins += 1
            gains += net
        else:
            losses += -net
        iso = t.isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        by_week[wk] = by_week.get(wk, 0.0) + net
        days.add(t.date())
    return {
        "closed_trades": trades,
        "wins": wins,
        "win_rate": (wins / trades) if trades else 0.0,
        # capped: a record with no losing fill yet is not infinitely good, and
        # `inf` is not JSON -- the phone's decoder would reject the payload
        "profit_factor": min(gains / losses, 99.0) if losses > 0 else (99.0 if gains > 0 else 0.0),
        "weeks_covered": len(by_week),
        "weeks_positive": sum(1 for v in by_week.values() if v > 0),
        "days_covered": ((now - oldest).total_seconds() / 86400.0) if oldest else 0.0,
        "active_days": len(days),
        "fills_per_day": (trades / max(1.0, (now - oldest).total_seconds() / 86400.0)
                          if oldest else 0.0),
        "coins": sorted(coins),
    }


def score_of(t: TraderStats) -> float:
    """Multiplicative, so no single axis can carry a bad one.

    log10 of the month's profit in units of $10k (so $22M is 3.3, $100k is
    1.0), times the win rate, times the profit factor capped at 3 (beyond
    that it is one lucky trade), times the share of covered weeks that were
    positive. A perfect record on a $50k month scores below a decent record
    on a $5M month, which is the intended reading of "successful".
    """
    if not t.qualifies:
        return 0.0
    size = math.log10(1.0 + max(t.pnl_30d, 0.0) / 10_000.0)
    pf = min(t.profit_factor, 3.0)
    consistency = t.weeks_positive / t.weeks_covered if t.weeks_covered else 0.0
    return round(size * t.win_rate * pf * consistency, 4)


def select_traders(leaderboard: Path, fills_fn: Callable[[str], List[dict]] = user_fills,
                   n: int = TRACK, now: Optional[datetime] = None,
                   pause: Callable[[], None] = lambda: None) -> List[TraderStats]:
    """The followed set: candidates enriched with their fill record, filtered
    on the bar, ranked by `score_of`. `pause` is called between fill reads
    so the caller can be polite to the venue."""
    picked: List[TraderStats] = []
    for c in candidates(leaderboard):
        try:
            fills = fills_fn(c.address)
        except Exception as e:                      # one dead read, not the run
            log.warning("smartmoney: fills for %s failed: %s", c.address, e)
            fills = []
        pause()
        s = fill_stats(fills, now=now)
        for k, v in s.items():
            setattr(c, k, v)
        c.score = score_of(c)
        if c.score > 0:
            picked.append(c)
    picked.sort(key=lambda t: -t.score)
    return picked[:n]


def refresh_leaderboard(cache_dir: Path, max_age_s: float = 6 * 3600) -> Optional[Path]:
    return fetch_leaderboard(cache_dir, max_age_s=max_age_s)
