"""Deterministic fake news, so tests never touch the network or an API key."""
from __future__ import annotations

from datetime import timedelta
from typing import List

import numpy as np
import pandas as pd

from newsfeed.events import NewsItem

HEADLINES = [
    ("SEC approves spot Bitcoin ETF applications", "sec.gov", ("BTC",)),
    ("Major exchange hacked, $200M stolen from hot wallet", "coindesk.example", ("BTC", "ETH")),
    ("Binance announces new BTCUSDT perpetual tiers", "binance-announcements", ("BTC",)),
    ("Fed holds rates steady, signals cuts ahead", "reuters.example", ()),
    ("Analyst says Bitcoin could rally to $100k", "cryptoblog.example", ("BTC",)),
    ("Regulator opens investigation into stablecoin issuer", "reuters.example", ()),
    ("Layer-2 protocol upgrade ships on schedule", "protocol.example", ("ETH",)),
    ("Exchange delisting three low-volume pairs", "binance-announcements", ("BTC",)),
]


def make_news(bars: pd.DataFrame, n: int = 60, seed: int = 5,
              ingest_lag_minutes: int = 25) -> List[NewsItem]:
    """Items scattered across the window, each published before we saw it.

    ``ingest_lag_minutes`` is the point: every item is published a while
    before it is ingested, and ``event_time`` sits earlier still. A detector
    that keys on the wrong clock gets that much free foresight, and the PIT
    test is what catches it.
    """
    rng = np.random.default_rng(seed)
    start, end = bars.index[0], bars.index[-1]
    span = (end - start).total_seconds()

    items: List[NewsItem] = []
    for k in range(n):
        offset = float(rng.uniform(0.05, 0.95)) * span
        published = start + timedelta(seconds=offset)
        headline, source, assets = HEADLINES[k % len(HEADLINES)]
        items.append(NewsItem(
            headline=f"{headline} (#{k})",
            body="Reference body text for scoring.",
            source=source,
            assets=assets,
            event_time=published - timedelta(minutes=90),   # happened earlier
            published_at=published,                          # reported later
            ingested_at=published + timedelta(minutes=ingest_lag_minutes),
        ))
    items.sort(key=lambda i: i.published_at)
    return items
