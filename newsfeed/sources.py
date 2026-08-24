"""Where news comes in.

Prefer primary sources with their own clock. An exchange announcement API, an
on-chain block time, or a GitHub commit timestamp is the moment the
information existed; an aggregator's ``published_at`` is when someone got
around to writing about it, and aggregators revise those.

Only stdlib HTTP here, matching ``marketdata/binance.py`` — the project has no
``requests`` dependency and does not need one.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Protocol

from .events import NewsItem

BINANCE_CMS = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&pageNo={page}&pageSize={size}"
)


class NewsSource(Protocol):
    name: str

    def fetch(self) -> List[NewsItem]:
        ...


class BinanceAnnouncements:
    """Exchange announcements — a primary source with its own clock.

    Listings, delistings, and halts move price hard and originate here rather
    than at a news desk, so the timestamp is the event, not the coverage of it.

    Best-effort: this is a public CMS endpoint rather than a documented API,
    so it can change shape or rate-limit without notice. Treat a failure as
    "no new items", never as a reason to fall back to a less honest clock.
    """

    name = "binance-announcements"

    def __init__(self, pages: int = 1, page_size: int = 50, timeout: int = 20):
        self.pages, self.page_size, self.timeout = pages, page_size, timeout

    def fetch(self) -> List[NewsItem]:
        out: List[NewsItem] = []
        now = datetime.now(timezone.utc)
        for page in range(1, self.pages + 1):
            url = BINANCE_CMS.format(page=page, size=self.page_size)
            req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/agent3"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read())
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
                break
            articles = (payload.get("data") or {}).get("articles") or []
            if not articles:
                break
            for a in articles:
                out.append(NewsItem(
                    headline=a.get("title", ""),
                    body="",
                    source="binance-announcements",
                    url=f"https://www.binance.com/en/support/announcement/{a.get('code','')}",
                    published_at=a.get("releaseDate"),
                    ingested_at=now,        # we saw it now, whatever it claims
                    event_time=a.get("releaseDate"),
                    meta={"id": a.get("id"), "code": a.get("code")},
                ))
        return out


class JSONLReplay:
    """Replay a stored corpus. Used for research and by the test suite."""

    name = "jsonl-replay"

    def __init__(self, path: Path):
        self.path = Path(path)

    def fetch(self) -> List[NewsItem]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(NewsItem.from_dict(json.loads(line)))
        return out
