"""Equity news: per-ticker and market-wide.

WHY THESE SOURCES
    Same reasoning as the crypto feeds in `rss.py`. These are RSS feeds the
    publishers put out FOR syndication — headline, excerpt, link back. That is
    what RSS is for, and it is the reason this app reads feeds rather than
    scraping Finviz or Yahoo's pages, which their terms forbid and which would
    be a real problem for a product with paying subscribers.

    Verified reachable 2026-09-02 with the item counts they returned.

TWO KINDS OF FEED, AND THEY ARE NOT INTERCHANGEABLE
    PER-TICKER   SEC company filings. NOT Yahoo's per-symbol RSS, which was
                 the obvious choice and does not work: measured on 2026-09-02,
                 ZERO of the twenty items in NVDA's feed mentioned NVDA. It
                 carried a story about a Florida bakery. It is generic market
                 content wearing a ticker's name, and shipping it as "news
                 about NVDA" would be a lie the user would catch immediately.

                 An 8-K is the opposite: a company telling the regulator
                 something material happened, with a code saying what kind.
                 Earnings, a new agreement, an executive leaving. Official,
                 free, public domain, and unambiguously about that company.

    MARKET-WIDE  MarketWatch, CNBC, Nasdaq. Nothing tags these to a ticker,
                 so they are filed as macro and shown for every stock — the
                 same treatment `rss.py` gives "Fed holds rates". Guessing a
                 ticker from a headline would be inventing an attribution.

WHY THE PER-TICKER FEEDS ARE FETCHED FOR THE WATCHLIST ONLY
    One request per symbol. Thirteen thousand symbols would be thirteen
    thousand requests to fill a page nobody has open. The watchlist is a
    handful, and it is exactly the set the user said they care about.
"""
from __future__ import annotations

import logging
from typing import Iterable, List, Sequence, Tuple

from newsfeed.events import NewsItem
from newsfeed.rss import RssSource, is_macro

log = logging.getLogger(__name__)

# Market-wide equity news. No ticker attribution, so these are macro.
MARKET_FEEDS: Tuple[Tuple[str, str], ...] = (
    ("marketwatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("cnbc", "https://search.cnbc.com/rs/search/combinedcms/view.xml"
             "?partnerId=wrss01&id=20910258"),
    ("nasdaq", "https://www.nasdaq.com/feed/rssoutbound?category=Markets"),
)

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# What an 8-K item code means, in English.
#
# The code is the whole point: "8-K filed" tells you nothing, "results of
# operations" tells you earnings just landed. Only the codes worth waking up
# for are mapped; anything else is reported as a filing without a claim about
# what it contains.
ITEM_CODES = {
    "1.01": "entered a material agreement",
    "1.02": "terminated a material agreement",
    "1.03": "bankruptcy or receivership",
    "2.01": "completed an acquisition or disposal",
    "2.02": "reported results of operations",
    "2.03": "took on a material financial obligation",
    "2.04": "triggered a financial obligation",
    "2.05": "committed to exit or dispose of a business",
    "2.06": "material impairment",
    "3.01": "listing or compliance issue",
    "4.01": "changed auditors",
    "4.02": "prior financial statements can no longer be relied on",
    "5.01": "change in control",
    "5.02": "director or officer change",
    "5.03": "amended its charter or bylaws",
    "7.01": "Reg FD disclosure",
    "8.01": "other material event",
}

# Forms worth surfacing. A 13F or an N-PX is a disclosure obligation, not news.
INTERESTING_FORMS = ("8-K", "10-Q", "10-K", "S-1", "424B4", "SC 13D")

FORM_TITLES = {
    "10-Q": "filed a quarterly report",
    "10-K": "filed an annual report",
    "S-1": "filed to register new shares",
    "424B4": "priced an offering",
    "SC 13D": "an activist stake was disclosed",
}


class TickerNews:
    """Material events per company, from SEC filings.

    One request per ticker, and the SEC asks for 10 requests/second at most —
    so this is for a watchlist, never for a universe.
    """

    name = "equity-filings"

    def __init__(self, symbols: Sequence[str], timeout: int = 20,
                 limit_per_symbol: int = 12):
        self.symbols = [s.upper().strip() for s in symbols if s and s.strip()]
        self.timeout = timeout
        self.limit = limit_per_symbol

    def fetch(self) -> List[NewsItem]:
        import json
        import time
        import urllib.request
        from datetime import datetime, timezone

        from marketdata.edgar import UA, ticker_map

        try:
            ciks = ticker_map()
        except Exception as e:
            log.warning("no ticker map, per-symbol news unavailable: %s", e)
            return []

        out: List[NewsItem] = []
        for sym in self.symbols:
            cik = ciks.get(sym)
            if cik is None:
                continue
            time.sleep(0.12)                       # SEC fair-access pacing
            try:
                req = urllib.request.Request(
                    SUBMISSIONS.format(cik=cik), headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    doc = json.load(r)
            except Exception as e:
                log.warning("filings %s: %s", sym, e)
                continue

            name = doc.get("name") or sym
            recent = (doc.get("filings") or {}).get("recent") or {}
            forms = recent.get("form") or []
            dates = recent.get("filingDate") or []
            items = recent.get("items") or []
            accns = recent.get("accessionNumber") or []

            kept = 0
            for i, form in enumerate(forms):
                if kept >= self.limit:
                    break
                if form not in INTERESTING_FORMS:
                    continue
                try:
                    when = datetime.strptime(dates[i], "%Y-%m-%d").replace(
                        tzinfo=timezone.utc)
                except (IndexError, ValueError):
                    continue

                codes = [c.strip() for c in (items[i] if i < len(items) else "")
                         .split(",") if c.strip()]
                described = [ITEM_CODES[c] for c in codes if c in ITEM_CODES]
                if described:
                    what = "; ".join(described)
                elif form in FORM_TITLES:
                    what = FORM_TITLES[form]
                else:
                    # An 8-K whose codes are all unmapped. Say that it was
                    # filed rather than inventing a description of it.
                    what = f"filed an {form}"

                accn = (accns[i] if i < len(accns) else "").replace("-", "")
                out.append(NewsItem(
                    headline=f"{name}: {what}",
                    body=f"SEC {form} filed {dates[i]}."
                         + (f" Items {', '.join(codes)}." if codes else ""),
                    source=f"sec:{form}",
                    url=(f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                         f"{accn}/" if accn else
                         "https://www.sec.gov/edgar/browse/?CIK=%d" % cik),
                    assets=(sym,),
                    published_at=when, ingested_at=None, event_time=when,
                    meta={"macro": "0", "market": "stocks", "symbol": sym,
                          "form": form, "names_symbol": "1"}))
                kept += 1
        return out


class MarketNews:
    """Market-wide equity headlines. Filed as macro; they belong to no ticker."""

    name = "equity-market"

    def __init__(self, feeds=MARKET_FEEDS, timeout: int = 20):
        self.sources = [RssSource(n, u, timeout) for n, u in feeds]

    def fetch(self) -> List[NewsItem]:
        seen, out = set(), []
        for src in self.sources:
            for item in src.fetch():
                key = item.headline.lower().strip()[:80]
                if key in seen:
                    continue
                seen.add(key)
                out.append(NewsItem(
                    headline=item.headline, body=item.body,
                    source=item.source, url=item.url,
                    # NO TICKER GUESSED. Nothing in these feeds attributes a
                    # story to a symbol, and inferring one from the words in a
                    # headline would be an attribution the source never made.
                    assets=(),
                    published_at=item.published_at,
                    ingested_at=item.ingested_at,
                    event_time=item.event_time,
                    meta={"macro": "1", "market": "stocks"}))
        return out


def collect(watchlist: Iterable[str]) -> List[NewsItem]:
    """Everything for a watchlist plus the market-wide feeds."""
    items: List[NewsItem] = []
    try:
        items.extend(MarketNews().fetch())
    except Exception as e:                    # one dead feed, not the run
        log.warning("equity market news failed: %s", e)
    syms = [s for s in watchlist if s]
    if syms:
        try:
            items.extend(TickerNews(syms).fetch())
        except Exception as e:
            log.warning("equity ticker news failed: %s", e)
    items.sort(key=lambda i: i.published_at, reverse=True)
    return items
