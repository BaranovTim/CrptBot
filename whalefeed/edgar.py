"""SEC EDGAR: who bought or sold, how much, and exactly when.

Free, official, no API key. The best-quality "important person traded"
data available anywhere at zero cost, because filing it is a legal
obligation rather than a courtesy.

WHAT WE READ
------------
Form 4   an officer or director's own trade. Exact share count, exact
         price, exact date. Due within two business days.
8-K      a material corporate event - this is where a treasury holder
         announces buying bitcoin.
13F/13D  institutional positions. Real, but 45 days stale by law.

THE TWO CLOCKS EDGAR GIVES US
-----------------------------
`transactionDate` is when the trade happened. `acceptanceDateTime` is when
the filing hit EDGAR and became public. They differ by up to two business
days, and using the first would hand a backtest two free days on every
filing. `observable_at` therefore keys on acceptance, never on the trade
date - the same rule the news feed follows.

RATE LIMIT AND ETIQUETTE
------------------------
SEC asks for at most 10 requests/second and a User-Agent that identifies
you with a contact address. Both are honoured here. Set WHALEFEED_UA to
your own address; the default is generic and SEC may throttle it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Dict, List, Optional, Sequence

from core import utc_now

from .events import WhaleEvent
from .watchlist import WATCHLIST, Entity

log = logging.getLogger("whalefeed.edgar")

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

# SEC asks that automated clients identify themselves with a contact address
USER_AGENT = os.environ.get(
    "WHALEFEED_UA", "TradingBot research (set WHALEFEED_UA to your email)")
MIN_INTERVAL = 0.12          # ~8 req/s, under the 10/s the SEC asks for

_last_call = [0.0]


def _get(url: str, timeout: int = 25) -> Optional[bytes]:
    """One throttled request. Returns None on any failure - never raises."""
    wait = MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError) as e:
        log.debug("edgar fetch failed %s: %s", url, e)
        return None


def _tag(xml: str, name: str) -> List[str]:
    return re.findall(rf"<{name}>(.*?)</{name}>", xml, re.S)


def _first(xml: str, name: str, default: str = "") -> str:
    hits = _tag(xml, name)
    return re.sub(r"<[^>]+>|\s+", " ", hits[0]).strip() if hits else default


def _num(text: str) -> float:
    try:
        return float(re.sub(r"[^0-9.\-]", "", text) or 0.0)
    except ValueError:
        return 0.0


def parse_form4(xml: str, entity: Entity, accepted, url: str,
                now=None) -> List[WhaleEvent]:
    """Turn one Form 4 XML document into individual transactions.

    A single filing usually holds several - an option exercise plus the
    sale that funds it, for instance - and each becomes its own event so
    the transaction codes stay distinguishable.
    """
    now = now or utc_now()
    owner = _first(xml, "rptOwnerName") or entity.name
    title = _first(xml, "officerTitle")

    # nonDerivative rows are ordinary share transactions - the ones that
    # mean something. derivative rows are options and grants
    blocks = re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
                        xml, re.S)
    out: List[WhaleEvent] = []
    for b in blocks:
        code = _first(b, "transactionCode")
        shares = _num(_first(b, "transactionShares"))
        price = _num(_first(b, "transactionPricePerShare"))
        ad = _first(b, "transactionAcquiredDisposedCode").upper()
        tdate = _first(b, "transactionDate")
        if shares <= 0:
            continue
        # A = acquired, D = disposed. this is the direction, and it is
        # separate from the code, which is the REASON
        action = "BUY" if ad.startswith("A") else "SELL"
        out.append(WhaleEvent(
            actor=owner, actor_kind="insider", action=action,
            asset=entity.ticker, amount_usd=shares * price,
            quantity=shares, price=price, transaction_code=code,
            title=title, source="sec-form4", url=url,
            event_time=tdate or None,       # when they traded
            published_at=accepted,          # when it became public
            ingested_at=now,                # when we saw it
            meta={"cik": entity.cik, "entity": entity.name,
                  "crypto_proximity": entity.crypto_proximity}))
    return out


def fetch_entity(entity: Entity, forms: Sequence[str] = ("4",),
                 max_filings: int = 6, since_days: int = 30,
                 now=None) -> List[WhaleEvent]:
    """Recent filings for one entity, parsed into events."""
    now = now or utc_now()
    raw = _get(SUBMISSIONS.format(cik=entity.cik))
    if raw is None:
        return []
    try:
        sub = json.loads(raw)
    except json.JSONDecodeError:
        return []

    recent = sub.get("filings", {}).get("recent", {})
    rows = list(zip(recent.get("form", []), recent.get("accessionNumber", []),
                    recent.get("primaryDocument", []),
                    recent.get("acceptanceDateTime", [])))

    cutoff = now - timedelta(days=since_days)
    events: List[WhaleEvent] = []
    seen = 0
    for form, acc, doc, accepted in rows:
        if form not in forms or seen >= max_filings:
            continue
        try:
            accepted_ts = WhaleEvent(
                actor="x", actor_kind="insider", action="BUY", asset="x",
                amount_usd=0, source="probe", published_at=accepted).published_at
        except Exception:
            continue
        if accepted_ts < cutoff:
            break                      # rows are newest-first; older is done
        seen += 1

        acc_clean = acc.replace("-", "")
        # the primaryDocument is the styled view; the raw XML sits beside it
        doc_xml = doc.split("/")[-1] if "/" in doc else doc
        url = ARCHIVE.format(cik=entity.cik, acc=acc_clean, doc=doc_xml)
        body = _get(url)
        if body is None:
            continue
        try:
            events.extend(parse_form4(body.decode("utf-8", "ignore"),
                                      entity, accepted, url, now=now))
        except Exception as e:                       # noqa: BLE001
            log.debug("form4 parse failed for %s: %s", url, e)
    return events


class EdgarSource:
    """Polls the watchlist for new insider filings."""

    name = "sec-edgar"

    def __init__(self, entities: Optional[Sequence[Entity]] = None,
                 forms: Sequence[str] = ("4",), since_days: int = 30,
                 max_filings_per_entity: int = 4):
        self.entities = list(entities) if entities else list(WATCHLIST)
        self.forms = tuple(forms)
        self.since_days = since_days
        self.max_filings = max_filings_per_entity

    def fetch(self, now=None) -> List[WhaleEvent]:
        now = now or utc_now()
        out: List[WhaleEvent] = []
        for e in self.entities:
            try:
                out.extend(fetch_entity(e, self.forms, self.max_filings,
                                        self.since_days, now=now))
            except Exception as exc:                 # noqa: BLE001
                # one unreachable entity must not stop the rest
                log.warning("edgar fetch failed for %s: %s", e.ticker, exc)
        return out
