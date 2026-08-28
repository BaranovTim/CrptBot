"""Scheduled market events — the ones you can know about before they happen.

WHY THIS IS NOT PART OF AGENT 3
-------------------------------
Agent 3 reads news that has ALREADY happened, and its whole discipline is
point-in-time: an item is usable only once `observable_at` has passed, because
anything else is lookahead. This module is the opposite kind of object. A
scheduled event is public knowledge in advance — the Fed publishes next year's
meeting dates today — so there is nothing to gate.

That also means it must never become a feature. "FOMC in 3 hours" is known to
the whole market and is priced; feeding it to Agent 5 as a signal would be
modelling the calendar, not the market. It exists to warn a human, and that is
all it is wired to.

SOURCE
------
`federalreserve.gov` publishes the FOMC calendar as plain HTML with no key and
no rate limit, several years ahead. Statements land at 14:00 America/New_York
on the SECOND day of each meeting — computed through a real timezone rather
than a fixed offset, because half the meetings are EST and half are EDT and a
one-hour error puts the alert on the wrong side of the event.

BLS (CPI, payrolls) returns 403 to anything that is not a browser, so those
cannot be fetched here. `data_cache/calendar.json` is the escape hatch: add
them by hand, or point it at whatever source you trust. The format is
documented in `LocalSource`.
"""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

try:                                    # 3.9+ stdlib, no dependency added
    from zoneinfo import ZoneInfo
except ImportError:                     # pragma: no cover
    ZoneInfo = None                     # type: ignore

import config as project_config
from core import utc_now

log = logging.getLogger(__name__)

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
CACHE = project_config.DATA_CACHE / "schedule"
LOCAL_FILE = project_config.DATA_CACHE / "calendar.json"

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
_ABBR = {m[:3]: i for m, i in _MONTHS.items()}


@dataclass(frozen=True)
class ScheduledEvent:
    """One event that is known to be coming."""

    key: str                 # stable id, so an alert fires once and not again
    title: str
    at: datetime             # UTC, when the market learns something
    impact: str              # high | medium | low
    source: str
    url: str = ""
    note: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["at"] = self.at.isoformat()
        return d

    def minutes_until(self, now: Optional[datetime] = None) -> float:
        return ((self.at - (now or utc_now())).total_seconds()) / 60.0


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _month_number(token: str) -> Optional[int]:
    t = token.strip().lower().rstrip(".")
    return _MONTHS.get(t) or _ABBR.get(t[:3])


def parse_fomc(page: str) -> List[ScheduledEvent]:
    """Meeting dates out of the Fed's calendar page.

    Two shapes have to survive here, and both appear every year:
      "January"  "27-28"   -> both days in January, statement on the 28th
      "Apr/May"  "30-1"    -> spans the month boundary, statement on May 1st
    The giveaway for the second is that the day number goes DOWN.
    """
    out: List[ScheduledEvent] = []
    if ZoneInfo is None:                      # pragma: no cover
        return out
    et = ZoneInfo("America/New_York")

    for panel in re.split(r'(?=<div class="panel panel-default")', page):
        ym = re.search(r"([0-9]{4})\s*FOMC Meetings", panel)
        if not ym:
            continue
        year = int(ym.group(1))
        pairs = re.findall(
            r'fomc-meeting__month[^>]*>(.*?)</div>\s*<div[^>]*'
            r'fomc-meeting__date[^>]*>(.*?)</div>',
            panel, re.S)

        for raw_month, raw_date in pairs:
            months = [_month_number(p) for p in re.split(r"[/\-]", _clean(raw_month))]
            months = [m for m in months if m]
            days = [int(d) for d in re.findall(r"\d+", _clean(raw_date))]
            if not months or not days:
                continue

            end_day = days[-1]
            # a decreasing day range crossed into the next month
            crossed = len(days) > 1 and days[-1] < days[0]
            month = months[-1] if (crossed and len(months) > 1) else months[0]
            end_year = year + 1 if (crossed and month == 1 and months[0] == 12) else year

            try:
                local = datetime(end_year, month, end_day, 14, 0, tzinfo=et)
            except ValueError:
                continue
            at = local.astimezone(ZoneInfo("UTC"))
            out.append(ScheduledEvent(
                key=f"fomc-{at:%Y-%m-%d}",
                title="FOMC rate decision",
                at=at, impact="high", source="federalreserve.gov", url=FOMC_URL,
                note="Statement at 14:00 ET; the press conference follows at 14:30."))
    return out


class FomcSource:
    """Fetches and caches the Fed calendar. It changes a few times a year."""

    def __init__(self, cache_dir: Path = CACHE, ttl_hours: int = 24):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "fomc.json"
        self.ttl = timedelta(hours=ttl_hours)

    def _fresh(self) -> bool:
        if not self.path.exists():
            return False
        age = utc_now() - datetime.fromtimestamp(
            self.path.stat().st_mtime, tz=ZoneInfo("UTC"))
        return age < self.ttl

    def events(self, force: bool = False) -> List[ScheduledEvent]:
        if not force and self._fresh():
            return self._read()
        try:
            req = urllib.request.Request(
                FOMC_URL, headers={"User-Agent": "TradingBot/schedule"})
            with urllib.request.urlopen(req, timeout=20) as r:
                page = r.read().decode("utf-8", errors="replace")
            evs = parse_fomc(page)
            if evs:
                self.path.write_text(
                    json.dumps([e.to_json() for e in evs], indent=1))
            return evs
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError) as e:
            # a stale calendar beats no calendar: these dates are published a
            # year ahead, so yesterday's copy is still correct
            log.warning("FOMC fetch failed (%s), falling back to cache", e)
            return self._read()

    def _read(self) -> List[ScheduledEvent]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text())
        except (ValueError, OSError):
            return []
        return [ScheduledEvent(**{**d, "at": datetime.fromisoformat(d["at"])})
                for d in raw]


class LocalSource:
    """Whatever you add by hand, in `data_cache/calendar.json`.

    BLS blocks scripted access, so CPI and payrolls cannot be fetched here.
    Add them yourself in this shape — `at` must be UTC and ISO-8601:

        [
          {"title": "US CPI (Aug)", "at": "2026-09-11T12:30:00+00:00",
           "impact": "high", "note": "08:30 ET"}
        ]

    Anything malformed is skipped with a warning rather than taking the whole
    calendar down, because a typo in one row should not silence the Fed dates.
    """

    def __init__(self, path: Path = LOCAL_FILE):
        self.path = Path(path)

    def events(self) -> List[ScheduledEvent]:
        if not self.path.exists():
            return []
        try:
            rows = json.loads(self.path.read_text())
        except (ValueError, OSError) as e:
            log.warning("calendar.json unreadable: %s", e)
            return []
        out = []
        for i, d in enumerate(rows if isinstance(rows, list) else []):
            try:
                at = datetime.fromisoformat(str(d["at"]))
                if at.tzinfo is None:
                    raise ValueError("`at` must carry a UTC offset")
                title = str(d["title"])
                out.append(ScheduledEvent(
                    key=str(d.get("key") or f"local-{at:%Y-%m-%dT%H%M}-{title[:24]}"),
                    title=title, at=at.astimezone(ZoneInfo("UTC")),
                    impact=str(d.get("impact", "medium")),
                    source=str(d.get("source", "calendar.json")),
                    url=str(d.get("url", "")), note=str(d.get("note", ""))))
            except (KeyError, ValueError, TypeError) as e:
                log.warning("calendar.json row %d skipped: %s", i, e)
        return out


def upcoming(within_days: int = 21, now: Optional[datetime] = None,
             sources=None) -> List[ScheduledEvent]:
    """Everything scheduled between now and `within_days`, soonest first."""
    now = now or utc_now()
    horizon = now + timedelta(days=within_days)
    srcs = sources if sources is not None else [FomcSource(), LocalSource()]

    seen, out = set(), []
    for s in srcs:
        for e in s.events():
            if e.key in seen or not (now <= e.at <= horizon):
                continue
            seen.add(e.key)
            out.append(e)
    return sorted(out, key=lambda e: e.at)
