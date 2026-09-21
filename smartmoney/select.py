"""Who to follow: a record, not a rank.

WHAT THE LEADERBOARD IS
    Thirty days of profit. Measured against a year of fills for 54 of its
    best-looking accounts (research/trader_patterns.py): 21 lost money over
    the year, the median one had 4 liquidations, and for the median one
    five trades were 73% of the gross. The board ranks variance. Half of
    it is market makers whose fills never close at a loss and say nothing
    about entries.

WHAT PERSISTED
    Traders profitable on their POSITION TRADES over a half-year (at least
    five of them) went on to make +1.5% per trade over the next 24h, from
    the price a follower would pay, in the half-year after -- both sides,
    seven of nine traders, against +0.3% of drift. Thin, but it is the one
    thing in that analysis that held out of sample, and it is what this
    selects on.

THE RECORD
    A POSITION TRADE is an episode (smartmoney/episodes.py) held at least
    thirty minutes and reaching at least $5k. A record over the last
    RECORD_DAYS qualifies when it has enough of them, made money, won more
    than it lost, was consistent across the weeks it spans, and was not
    liquidated more than it can explain. Ranked by a multiplicative score
    so no single axis carries a bad one. The leaderboard is still where
    the candidates come from -- it is the only public list of accounts
    worth reading -- but it no longer decides.
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from marketdata.hyperliquid import (fetch_leaderboard, iter_leaderboard,
                                    performance, user_fills_since)

from .episodes import Episode, reconstruct

log = logging.getLogger(__name__)

# the leaderboard cut: real accounts with real activity
MIN_ACCOUNT_USD = 100_000.0
MIN_MONTH_VOLUME_USD = 1_000_000.0
CANDIDATES = 400            # deep, because most of the top is machines

# the record
RECORD_DAYS = 180
MIN_HOLD_HOURS = 0.5
MIN_NOTIONAL_USD = 5_000.0
MIN_POSITION_TRADES = 8
MIN_WIN_RATE = 0.50
MAX_WIN_RATE = 0.92         # a book that never loses is not a trading book
MIN_WEEKS = 4
CONSISTENCY = 0.6           # share of covered weeks positive
MAX_LIQUIDATIONS = 3
MAX_MAKER_SHARE = 0.95      # 99% resting quotes is a market maker
TRACK = 25


@dataclass
class TraderStats:
    address: str
    account_value: float
    pnl_30d: float
    roi_30d: float
    volume_30d: float
    pnl_all: float
    # the record, on position trades over RECORD_DAYS
    position_trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    pnl_record: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    payoff: float = 0.0
    weeks_covered: int = 0
    weeks_positive: int = 0
    median_hold_h: float = 0.0
    maker_share: float = 0.0
    liquidations: int = 0
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
        return (self.position_trades >= MIN_POSITION_TRADES
                and self.pnl_record > 0
                and MIN_WIN_RATE <= self.win_rate <= MAX_WIN_RATE
                and self.weeks_covered >= MIN_WEEKS
                and self.weeks_positive >= math.ceil(CONSISTENCY * self.weeks_covered)
                and self.liquidations <= MAX_LIQUIDATIONS
                and self.maker_share <= MAX_MAKER_SHARE
                and bool(self.coins))


def candidates(path: Path, limit: int = CANDIDATES) -> List[TraderStats]:
    """Leaderboard rows worth reading a record for."""
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


def record_stats(fills: Iterable[dict], now: Optional[datetime] = None,
                 days: int = RECORD_DAYS) -> dict:
    """The record inside one address's fills. Pure; testable."""
    now = now or datetime.now(timezone.utc)
    cutoff_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    eps = [e for e in reconstruct(list(fills)) if e.t_close_ms >= cutoff_ms]
    pos = [e for e in eps if e.hold_hours >= MIN_HOLD_HOURS and e.max_notional >= MIN_NOTIONAL_USD]
    wins = [e for e in pos if e.pnl > 0]; losses = [e for e in pos if e.pnl <= 0]
    by_week: Dict[str, float] = {}
    for e in pos:
        t = datetime.fromtimestamp(e.t_close_ms / 1000, tz=timezone.utc)
        iso = t.isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        by_week[wk] = by_week.get(wk, 0.0) + e.pnl
    total_notional = sum(e.max_notional for e in eps) or 1.0
    holds = sorted(e.hold_hours for e in pos)
    avg_win = (sum(e.ret_pct for e in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(e.ret_pct for e in losses) / len(losses)) if losses else 0.0
    return {
        "position_trades": len(pos),
        "wins": len(wins),
        "win_rate": (len(wins) / len(pos)) if pos else 0.0,
        "pnl_record": sum(e.pnl for e in pos),
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "payoff": (avg_win / -avg_loss) if avg_loss < 0 else (99.0 if avg_win > 0 else 0.0),
        "weeks_covered": len(by_week),
        "weeks_positive": sum(1 for v in by_week.values() if v > 0),
        "median_hold_h": holds[len(holds) // 2] if holds else 0.0,
        "maker_share": sum(e.maker_entry * e.max_notional for e in eps) / total_notional,
        "liquidations": sum(1 for e in pos if e.liquidated),
        "coins": sorted({e.symbol for e in pos if e.symbol}),
    }


def score_of(t: TraderStats) -> float:
    """Multiplicative, so no single axis can carry a bad one.

    log10 of the record's profit in units of $10k, times the win rate,
    times the payoff ratio capped at 3, times the share of covered weeks
    that were positive.
    """
    if not t.qualifies:
        return 0.0
    size = math.log10(1.0 + max(t.pnl_record, 0.0) / 10_000.0)
    consistency = t.weeks_positive / t.weeks_covered if t.weeks_covered else 0.0
    return round(size * t.win_rate * min(t.payoff, 3.0) * consistency, 4)


def select_traders(leaderboard: Path,
                   fills_fn: Callable[[str], List[dict]] = user_fills_since,
                   n: int = TRACK, now: Optional[datetime] = None,
                   pause: Callable[[], None] = lambda: None) -> List[TraderStats]:
    """The followed set: candidates with their record, filtered on the bar,
    ranked by `score_of`. `pause` runs between reads."""
    picked: List[TraderStats] = []
    for c in candidates(leaderboard):
        try:
            fills = fills_fn(c.address)
        except Exception as e:                      # one dead read, not the run
            log.warning("smartmoney: fills for %s failed: %s", c.address, e)
            fills = []
        pause()
        for k, v in record_stats(fills, now=now).items():
            setattr(c, k, v)
        c.score = score_of(c)
        if c.score > 0:
            picked.append(c)
    picked.sort(key=lambda t: -t.score)
    return picked[:n]


def refresh_leaderboard(cache_dir: Path, max_age_s: float = 6 * 3600) -> Optional[Path]:
    return fetch_leaderboard(cache_dir, max_age_s=max_age_s)
