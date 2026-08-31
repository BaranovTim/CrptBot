"""Live news collection.

Agent 3's whole point-in-time defence rests on `ingested_at` being honest:
the moment WE saw an item, not the moment it claims to have happened. That
field is only meaningful if something actually stamps it in real time, and
that something is this file.

A backfilled corpus can never recover it. If you download three years of
headlines today, every one of them was "ingested" today, and the only
truthful timestamp left is `published_at` - which aggregators revise. So
running this collector from now on is what makes future research honest,
even though it does nothing for the past.

Polling, not streaming: news sources have no push feed, and an announcement
is not latency-critical at the minute scale. The exchange announcement API
is preferred over aggregators because it is a primary source with its own
clock.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from newsfeed.events import NewsItem
from newsfeed.rss import CryptoRss
from newsfeed.sources import BinanceAnnouncements, NewsSource
from newsfeed.store import JSONLNewsStore

log = logging.getLogger("livefeed.news")


@dataclass
class NewsStats:
    polls: int = 0
    items_seen: int = 0
    items_new: int = 0
    errors: int = 0
    last_poll: Optional[datetime] = None

    def __str__(self) -> str:
        return (f"polls={self.polls} seen={self.items_seen} "
                f"new={self.items_new} errors={self.errors}")


class NewsCollector:
    """Polls one or more sources into the append-only news store."""

    def __init__(self, sources: Optional[Sequence[NewsSource]] = None,
                 store: Optional[JSONLNewsStore] = None,
                 interval_seconds: int = 300):
        # Binance announcements ALONE produced one item in two days: listings
        # and halts are high quality and rare. The publisher feeds carry the
        # headlines people actually read — measured at 98 unique items across
        # five outlets — so both run, exchange first because its timestamp is
        # the event rather than the coverage of it.
        self.sources = list(sources) if sources else [
            BinanceAnnouncements(pages=1), CryptoRss()]
        self.store = store or JSONLNewsStore()
        self.interval_seconds = interval_seconds
        self.stats = NewsStats()

    def poll_once(self, now: Optional[datetime] = None) -> int:
        """Fetch every source once; store whatever is new. Returns new count."""
        now = now or datetime.now(timezone.utc)
        self.stats.polls += 1
        self.stats.last_poll = now

        fetched: List[NewsItem] = []
        for source in self.sources:
            try:
                items = source.fetch()
            except Exception as e:            # noqa: BLE001 - a bad source must
                self.stats.errors += 1        # not take the collector down
                log.warning("source %s failed: %s",
                            getattr(source, "name", source), e)
                continue
            for item in items:
                # stamp arrival ourselves if the source did not. this is the
                # clock Agent 3 gates on, and the only one that cannot be
                # revised out from under us later
                if item.ingested_at is None:
                    item = NewsItem(
                        headline=item.headline, body=item.body,
                        source=item.source, url=item.url, assets=item.assets,
                        published_at=item.published_at, ingested_at=now,
                        event_time=item.event_time, meta=item.meta)
                fetched.append(item)

        self.stats.items_seen += len(fetched)
        # the store dedupes on content hash, so re-seeing the same headline
        # every poll costs nothing and never double-counts
        added = self.store.append_items(fetched)
        self.stats.items_new += added
        if added:
            log.info("stored %d new news items", added)
        return added

    def poll_forever(self, stop: Optional[Callable[[], bool]] = None,
                     on_items: Optional[Callable[[int], None]] = None) -> None:
        while not (stop and stop()):
            added = self.poll_once()
            if on_items and added:
                on_items(added)
            deadline = time.time() + self.interval_seconds
            while time.time() < deadline:
                if stop and stop():
                    return
                time.sleep(min(1.0, deadline - time.time()))
