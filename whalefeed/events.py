"""Whale and insider transactions, with the same timestamp discipline as news.

WHAT THIS IS FOR
----------------
"Did someone important just buy or sell, and how much." Three kinds of
actor, in descending order of how much the signal is worth:

  INSIDER      a named officer or director filing SEC Form 4. Exact share
               count, exact price, exact date, legally required. The best
               data quality available anywhere for free.
  INSTITUTION  a fund's 13F/13D position. Real, but 45 days stale by law.
  ON-CHAIN     a large wallet transfer. Fast, but pseudonymous and gamed.

READ THIS BEFORE TRUSTING ANY OF IT
-----------------------------------
Your own plan flagged whale-following as the weakest of the whale signals:
"named-whale tracking is lagging and actively gamed by people who know they
are being watched." Both halves are true here and neither is fixable:

  LAGGING     Form 4 is due within two business days of the trade. 13F is
              due 45 days after quarter end - by the time you read it the
              position may be long gone. On-chain you see the transfer
              after it has landed.
  GAMED       anyone who knows their wallet is watched can move funds to
              manufacture a signal. Named wallets are the most watched
              objects in crypto.

So this is built as EVIDENCE, not as a trigger. It reports what happened
with amounts and timestamps; it does not tell Agent 5 to trade.

NOT ALL TRANSACTIONS MEAN THE SAME THING
----------------------------------------
The Form 4 transaction code matters more than the direction:

    P   open-market PURCHASE       the real signal - they chose to buy
    S   open-market SALE           often routine (a scheduled 10b5-1 plan)
    M   option/derivative exercise mechanical, expresses no view
    A   grant or award             compensation, expresses no view
    F   shares withheld for tax    automatic, expresses no view
    G   gift                       expresses no view

An M followed by an S on the same day is an employee exercising options and
selling to cover - it is a paycheck, not a bearish call. Treating that as
"insider selling" is the single most common way this data is misread, so
``conviction`` below encodes it explicitly.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# how much a transaction code tells you about the actor's actual opinion
CONVICTION = {
    "P": 1.0,    # open-market purchase - they wanted this
    "S": 0.5,    # sale - real, but often scheduled in advance
    "M": 0.0,    # option exercise - mechanical
    "A": 0.0,    # award/grant - compensation
    "F": 0.0,    # tax withholding - automatic
    "G": 0.0,    # gift
    "C": 0.0,    # conversion
    "X": 0.0,    # option expiration
}

ACTOR_KINDS = ("insider", "institution", "onchain", "treasury")


def _utc(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        v = float(ts)
        unit = 1e6 if v > 1e14 else (1e3 if v > 1e11 else 1.0)
        return datetime.fromtimestamp(v / unit, tz=timezone.utc)
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        d = datetime.fromisoformat(s[:10])        # bare date like 2026-08-24
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class WhaleEvent:
    """One important actor moving one asset, with an amount and three clocks."""

    actor: str                       # "Michael J Saylor", "Strategy Inc"
    actor_kind: str                  # insider | institution | onchain | treasury
    action: str                      # BUY | SELL
    asset: str                       # MSTR, BTC, ...
    amount_usd: float                # signed magnitude is in `action`, this is size
    source: str                      # sec-form4, whale-alert, ...

    # the three clocks, exactly as in newsfeed/events.py
    event_time: Optional[datetime] = None      # when the trade happened
    published_at: Optional[datetime] = None    # when the filing became public
    ingested_at: Optional[datetime] = None     # when WE saw it

    quantity: float = 0.0            # shares or coins
    price: float = 0.0
    transaction_code: str = ""       # Form 4 code; "" for non-SEC sources
    title: str = ""                  # "Chief Executive Officer"
    url: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_time", _utc(self.event_time))
        object.__setattr__(self, "published_at", _utc(self.published_at))
        object.__setattr__(self, "ingested_at", _utc(self.ingested_at))
        if self.published_at is None:
            raise ValueError("published_at is required - an event with no "
                             "public timestamp cannot be point-in-time gated")
        if self.action not in ("BUY", "SELL"):
            raise ValueError(f"action must be BUY or SELL, got {self.action!r}")

    @property
    def id(self) -> str:
        """Content hash. The same filing seen twice dedupes to one event."""
        payload = (f"{self.source}|{self.actor}|{self.asset}|{self.action}|"
                   f"{self.quantity:.6f}|{self.price:.6f}|"
                   f"{self.event_time.isoformat() if self.event_time else ''}")
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def conviction(self) -> float:
        """0..1: how much this transaction reflects an actual opinion.

        An option exercise scores 0 no matter how large. Size is not
        conviction - a $40m automatic vesting event says nothing at all.
        """
        if self.transaction_code:
            return CONVICTION.get(self.transaction_code.upper(), 0.25)
        return 0.75 if self.actor_kind in ("onchain", "treasury") else 0.5

    def observable_at(self, safety_lag: timedelta = timedelta(0)) -> datetime:
        """First moment this may legitimately be used.

        The LATEST clock we trust, never `event_time`. A Form 4 trade on the
        24th that was filed on the 26th was not knowable on the 24th - using
        the trade date would hand a backtest two free days on every filing.
        """
        stamps = [self.published_at]
        if self.ingested_at is not None:
            stamps.append(self.ingested_at)
        return max(stamps) + safety_lag

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "actor": self.actor, "actor_kind": self.actor_kind,
            "action": self.action, "asset": self.asset,
            "amount_usd": self.amount_usd, "quantity": self.quantity,
            "price": self.price, "transaction_code": self.transaction_code,
            "title": self.title, "source": self.source, "url": self.url,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "published_at": self.published_at.isoformat(),
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WhaleEvent":
        return cls(
            actor=d["actor"], actor_kind=d.get("actor_kind", "insider"),
            action=d["action"], asset=d.get("asset", ""),
            amount_usd=float(d.get("amount_usd", 0.0)),
            quantity=float(d.get("quantity", 0.0)),
            price=float(d.get("price", 0.0)),
            transaction_code=d.get("transaction_code", ""),
            title=d.get("title", ""), source=d.get("source", ""),
            url=d.get("url", ""), event_time=d.get("event_time"),
            published_at=d["published_at"], ingested_at=d.get("ingested_at"),
            meta=d.get("meta", {}) or {})

    def describe(self) -> str:
        """One line, in the same shape the news alert uses."""
        amt = (f"${self.amount_usd:,.0f}" if self.amount_usd
               else f"{self.quantity:,.0f} units")
        who = f"{self.actor}" + (f" ({self.title})" if self.title else "")
        return f"{who} {self.action} {amt} of {self.asset}"
