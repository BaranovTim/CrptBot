"""Crypto headlines from publisher RSS feeds.

WHY NOT TRADINGVIEW, WHICH IS WHAT WAS ASKED FOR
    TradingView's news panel is not a public API, and most of what it shows is
    licensed content — the screenshot that prompted this has Dow Jones
    Newswires in it. Scraping that feed would be taking someone else's paid
    licence and redistributing it through an app you intend to charge for.
    That is the kind of thing that ends a product rather than delays it.

    What TradingView is doing is aggregating publishers. So this aggregates
    the same publishers, from the RSS feeds they publish for exactly this
    purpose. Same headlines, no licensing problem.

WHY THE STORE WAS NEARLY EMPTY
    The only source wired up was Binance's announcement CMS — listings,
    delistings, halts. Those are high-quality and rare: one item in two days,
    which is what happened. Measured on 2026-08-31 these five feeds carry 143
    items between them at any moment.

WHAT THIS IS AND IS NOT GOOD FOR
    Headlines, for reading. NOT for training Agent 3: RSS serves a rolling
    window of recent items, so there is no history to fit on, and a feature
    built from "whatever the feed held when we happened to poll" would be
    biased by our own uptime. Agent 3 stays disconnected until there is a
    real archive.
"""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

from .events import NewsItem

log = logging.getLogger(__name__)

# Verified reachable 2026-08-31, with the item counts they returned.
FEEDS: Tuple[Tuple[str, str], ...] = (
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cryptonews", "https://cryptonews.com/news/feed/"),
    ("newsbtc", "https://www.newsbtc.com/feed/"),
)

# Which coins a headline is about. Ticker alone is too eager — "ADA" appears
# inside ordinary words and "SOL" is a common substring — so tickers are
# matched only as standalone words, while the full names can match anywhere.
ASSET_WORDS: Dict[str, Tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "SOL": ("solana", "sol"),
    "ADA": ("cardano", "ada"),
    "XRP": ("ripple", "xrp"),
    "DOGE": ("dogecoin", "doge"),
}

# Headlines about the market as a whole, which belong to every coin rather
# than none. Without this, "Fed holds rates" would be filed under nothing and
# never shown, though it is exactly the kind of thing that moves everything.
MACRO_WORDS = ("fed", "fomc", "inflation", "cpi", "rate cut", "rate hike",
               "payrolls", "sec ", "etf", "regulation", "tariff",
               "crypto market", "risk assets")


def tag_assets(text: str) -> Tuple[str, ...]:
    """Which assets a headline mentions. Empty means none identified."""
    low = text.lower()
    found = []
    for asset, words in ASSET_WORDS.items():
        for w in words:
            if len(w) <= 4:
                # ticker: standalone word only, or "ada" matches "Canada"
                if re.search(rf"\b{re.escape(w)}\b", low):
                    found.append(asset)
                    break
            elif w in low:
                found.append(asset)
                break
    return tuple(found)


def is_macro(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in MACRO_WORDS)


def _parsed_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)          # RFC 822, what RSS uses
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()


class RssSource:
    """One publisher's feed."""

    def __init__(self, name: str, url: str, timeout: int = 20):
        self.name, self.url, self.timeout = name, url, timeout

    @staticmethod
    def _opener() -> "urllib.request.OpenerDirector":
        """Follows 308, which older urllib does not.

        Python added 308 handling in 3.11; the droplet runs 3.13 and the
        development Mac runs 3.9, so CoinDesk resolved on one machine and
        failed on the other. A feed that works only in production is a feed
        nobody tests.
        """
        class _Redirect(urllib.request.HTTPRedirectHandler):
            def http_error_308(self, req, fp, code, msg, headers):
                return self.http_error_301(req, fp, 301, msg, headers)

        return urllib.request.build_opener(_Redirect)

    def fetch(self) -> List[NewsItem]:
        try:
            req = urllib.request.Request(
                self.url,
                # Several publishers return 403 to a default urllib agent.
                # This is a plain identification, not an attempt to look like
                # a browser to get past a paywall.
                headers={"User-Agent": "ThusIldy/1.0 (+news reader)"})
            with self._opener().open(req, timeout=self.timeout) as r:
                raw = r.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            # one publisher being down must never stop the others
            log.warning("rss %s: %s", self.name, e)
            return []

        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as e:
            log.warning("rss %s is not valid XML: %s", self.name, e)
            return []

        out: List[NewsItem] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            when = _parsed_date(item.findtext("pubDate"))
            if when is None:
                # No timestamp means no honest clock for it, and this project
                # does not invent one. Skipping is better than stamping it
                # with the moment we happened to fetch it.
                continue
            body = _strip_html(item.findtext("description") or "")[:600]
            assets = tag_assets(f"{title} {body}")
            out.append(NewsItem(
                headline=title,
                source=self.name,
                published_at=when,
                body=body,
                url=(item.findtext("link") or "").strip(),
                assets=assets,
                meta={"macro": "1" if is_macro(f"{title} {body}") else "0"},
            ))
        return out


class CryptoRss:
    """All the publisher feeds, as one source."""

    name = "crypto-rss"

    def __init__(self, feeds=FEEDS, timeout: int = 20):
        self.sources = [RssSource(n, u, timeout) for n, u in feeds]

    def fetch(self) -> List[NewsItem]:
        seen, out = set(), []
        for src in self.sources:
            for item in src.fetch():
                # The same story is syndicated across outlets. Dedupe on the
                # headline rather than the URL, which differs per publisher.
                key = re.sub(r"[^a-z0-9]+", "", item.headline.lower())[:80]
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
        out.sort(key=lambda i: i.published_at, reverse=True)
        return out
