"""News items, and the timestamp discipline that makes them safe to use.

This file is Agent 3's equivalent of ``agent1/pivots.py`` — the one place
where a mistake contaminates everything downstream, and the one place worth
reading twice.

Price data is honest: an exchange timestamp is the matching engine's clock,
accurate to the microsecond. News data is not. A news API gives you
*publication* time, which can lag the moment the information actually hit the
market by seconds or hours, and some aggregators backfill or silently revise
timestamps after the fact. That is where lookahead bias enters a news dataset,
and it does not look like a bug — every row has a plausible timestamp.

So an item carries three clocks, and the distinction between them is the whole
game:

    event_time    when the thing actually happened (often unknown)
    published_at  when the source published it
    ingested_at   when WE first saw it

``observable_at`` is the **latest** of the ones we trust, never the earliest.
Using ``event_time`` to decide when a headline became usable is exactly the
lookahead this file exists to prevent: the event happened at 14:00, but if we
only learned of it at 14:40, a backtest that acts at 14:00 is trading on
information nobody had.

Prefer primary sources with their own clock — an exchange announcement API,
an on-chain block time, a GitHub commit — over an aggregator's guess.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Categories are a closed set. A news taxonomy that grows by one label every
# time something unusual happens produces a feature whose meaning drifts.
CATEGORIES = (
    "regulatory",    # bans, approvals, lawsuits, policy
    "listing",       # listings, delistings, pair additions
    "security",      # hacks, exploits, bridge failures, insolvency
    "macro",         # rates, CPI, equities, dollar
    "protocol",      # upgrades, forks, tokenomics
    "partnership",   # integrations, adoption, funding
    "market",        # price commentary, analyst notes, "whale moves"
    "other",
)

_WS = re.compile(r"\s+")


def _clean(text: str, limit: int) -> str:
    """Collapse whitespace and truncate.

    Whitespace collapsing is not cosmetic: newline runs and zero-width
    characters are a common way to smuggle a fake 'end of document' boundary
    past a naive prompt template.
    """
    if not text:
        return ""
    text = text.replace("​", "").replace("﻿", "")
    text = _WS.sub(" ", str(text)).strip()
    return text[:limit]


def _utc(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        # Same millisecond-vs-microsecond trap as the kline loader.
        v = float(ts)
        unit = 1e6 if v > 1e14 else (1e3 if v > 1e11 else 1.0)
        return datetime.fromtimestamp(v / unit, tz=timezone.utc)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass(frozen=True)
class NewsItem:
    """One piece of news, with its provenance intact."""

    headline: str
    source: str
    published_at: datetime
    body: str = ""
    ingested_at: Optional[datetime] = None
    event_time: Optional[datetime] = None
    assets: tuple = ()
    url: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headline", _clean(self.headline, 400))
        object.__setattr__(self, "body", _clean(self.body, 4000))
        object.__setattr__(self, "published_at", _utc(self.published_at))
        object.__setattr__(self, "ingested_at", _utc(self.ingested_at))
        object.__setattr__(self, "event_time", _utc(self.event_time))
        if self.published_at is None:
            raise ValueError("published_at is required — an item with no clock is unusable")

    @property
    def id(self) -> str:
        """Content hash. Two aggregators reprinting one story dedupe to one item.

        Deliberately excludes timestamps: the same story republished with a
        revised timestamp is the same story, and counting it twice would
        inflate every news-volume feature.
        """
        payload = f"{self.source}|{self.headline}|{self.body[:500]}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def observable_at(self, safety_lag: timedelta = timedelta(0)) -> datetime:
        """The first moment this item may legitimately be used.

        The LATEST clock we have, plus a safety margin — never ``event_time``.
        If the event happened at 14:00 but we only ingested it at 14:40, then
        14:40 is the truth; acting at 14:00 is trading on information that did
        not exist yet.

        ``safety_lag`` guards against aggregators that backfill or revise
        publication times. It costs a little signal and buys the guarantee
        that a revised timestamp cannot pull an item earlier than you saw it.
        """
        stamps = [self.published_at]
        if self.ingested_at is not None:
            stamps.append(self.ingested_at)
        return max(stamps) + safety_lag

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "headline": self.headline,
            "body": self.body,
            "source": self.source,
            "url": self.url,
            "assets": list(self.assets),
            "published_at": self.published_at.isoformat(),
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NewsItem":
        return cls(
            headline=d.get("headline", ""),
            body=d.get("body", ""),
            source=d.get("source", "unknown"),
            url=d.get("url", ""),
            assets=tuple(d.get("assets", ())),
            published_at=d["published_at"],
            ingested_at=d.get("ingested_at"),
            event_time=d.get("event_time"),
            meta=d.get("meta", {}) or {},
        )


@dataclass(frozen=True)
class NewsScore:
    """The measured content of one item. Bounded numbers only.

    Note what is absent: there is no ``action``, no ``should_buy``, no
    ``confidence_to_trade``. Agent 3 measures; Agent 5 judges. That split is
    also the security boundary — the worst a successful prompt injection can
    do here is move a bounded number, because there is no field in which to
    express an instruction.
    """

    item_id: str
    direction: float        # -1 bearish .. +1 bullish
    magnitude: float        # 0 nothing .. 1 market-moving
    novelty: float          # 0 already priced in .. 1 genuinely new
    credibility: float      # 0 rumour .. 1 primary source
    category: str
    horizon_hours: float    # how long the effect plausibly persists
    assets: tuple = ()
    rationale: str = ""     # trace channel only — never reaches the model
    scorer: str = ""
    injection_suspected: bool = False

    def signed_strength(self) -> float:
        return self.direction * self.magnitude
