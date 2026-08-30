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
from datetime import date, datetime, timedelta
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
_MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November",
                "December"]


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

    # How far ahead this deserves to be shouted about. Higher wins.
    #
    # `impact` is about the event; this is about how much warning is useful.
    # Non-Farm Payrolls is the single largest scheduled volatility event for
    # crypto - the whole market repositions ahead of it - so it earns a day's
    # notice, while a routine high-impact print earns an hour.
    priority: int = 0

    # True when the date was COMPUTED rather than read from a publisher.
    # BLS serves 403 to anything scripted (re-tested 2026-08-30, including
    # with a browser User-Agent), so payroll dates are derived from BLS's own
    # rule and can be wrong when a holiday shifts them. Being a week out on
    # NFP is worse than having no entry, so this is surfaced, not hidden.
    estimated: bool = False

    @property
    def lead_minutes(self) -> tuple:
        """The warning windows this event gets, most distant first."""
        if self.priority >= 100:            # NFP
            return (1440, 120, 30, 5)      # a day, two hours, half an hour, 5
        if self.priority >= 80:             # FOMC and the like
            return (720, 60, 10)           # half a day, an hour, ten minutes
        if self.impact == "high":
            return (60, 5)
        return (30,)

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
                at=at, impact="high", priority=80,
                source="federalreserve.gov", url=FOMC_URL,
                note="Statement at 14:00 ET; the press conference follows at 14:30."))
    return out


class FomcSource:
    """Fetches and caches the Fed calendar. It changes a few times a year."""

    def __init__(self, cache_dir: Path = CACHE, ttl_hours: int = 24):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        # VERSIONED. The cache holds serialised ScheduledEvents, so adding a
        # field to that dataclass makes every existing row silently supply the
        # default instead — which is exactly what happened when `priority`
        # was added: FOMC kept reporting priority 0 from a cache written
        # before the field existed, and would have done for 24 hours.
        # Bump this whenever ScheduledEvent gains or renames a field.
        self.path = self.dir / "fomc.v2.json"
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
        try:
            return [ScheduledEvent(**{**d, "at": datetime.fromisoformat(d["at"])})
                    for d in raw]
        except TypeError as e:
            # a cache written by a different shape of the dataclass. Treat it
            # as absent rather than crashing the whole calendar.
            log.warning("fomc cache has an old shape (%s); ignoring", e)
            return []


class NfpSource:
    """US Non-Farm Payrolls — the Employment Situation release.

    WHY IT IS COMPUTED AND NOT FETCHED
        BLS returns 403 to scripted requests, browser User-Agent or not
        (re-tested 2026-08-30). There is no keyless feed for the release
        calendar, so the date comes from BLS's own published rule:

            "released on the third Friday following the conclusion of the
             reference week, i.e. the week which includes the 12th"

        with weeks running Sunday to Saturday.

    WHY EVERY ONE IS MARKED ESTIMATED
        That rule is what BLS says it *normally* does, and holidays move it.
        Checked against a case it gets wrong: the December 2024 report was
        released on 10 January 2025, while the rule gives 3 January. One
        week early on the biggest scheduled move of the month is worse than
        no entry at all — so the app says "estimated", and a row in
        `calendar.json` carrying the same key silently replaces it.

    TIME OF DAY is not estimated: 08:30 America/New_York has been fixed for
    decades, and the zone handles daylight saving so the UTC instant shifts
    with it rather than drifting an hour twice a year.
    """

    RELEASE_ET = (8, 30)

    def __init__(self, months_ahead: int = 4):
        self.months_ahead = months_ahead

    @staticmethod
    def release_for(ref_year: int, ref_month: int) -> datetime:
        """When the report covering `ref_year`-`ref_month` is published."""
        twelfth = date(ref_year, ref_month, 12)
        # BLS weeks are Sunday-Saturday. Python's weekday(): Monday=0..Sunday=6
        days_to_saturday = (5 - twelfth.weekday()) % 7
        week_ends = twelfth + timedelta(days=days_to_saturday)
        # first Friday strictly after the reference week closes, then +2 weeks
        days_to_friday = (4 - week_ends.weekday()) % 7 or 7
        third_friday = week_ends + timedelta(days=days_to_friday + 14)
        et = ZoneInfo("America/New_York")
        local = datetime(third_friday.year, third_friday.month,
                         third_friday.day, *NfpSource.RELEASE_ET, tzinfo=et)
        return local.astimezone(ZoneInfo("UTC"))

    def events(self, now: Optional[datetime] = None) -> List[ScheduledEvent]:
        now = now or utc_now()
        out = []
        y, m = now.year, now.month
        for i in range(-1, self.months_ahead):
            mm = m + i
            yy, mm = y + (mm - 1) // 12, (mm - 1) % 12 + 1
            at = self.release_for(yy, mm)
            label = f"{_MONTH_NAMES[mm - 1]} {yy}"
            out.append(ScheduledEvent(
                key=f"nfp-{yy}-{mm:02d}",
                title=f"US Non-Farm Payrolls ({label})",
                at=at,
                impact="high",
                priority=100,
                estimated=True,
                source="BLS release rule",
                url="https://www.bls.gov/schedule/news_release/empsit.htm",
                note="08:30 ET. The largest scheduled volatility event of the "
                     "month — the whole market repositions around it. Date is "
                     "estimated from the BLS rule and can shift for holidays."))
        return out


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
                    priority=int(d.get("priority", 0)),
                    estimated=bool(d.get("estimated", False)),
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
    # LocalSource FIRST, deliberately. Dedupe keeps whichever key it sees
    # first, so anything you enter by hand beats a computed estimate carrying
    # the same key — which is how a wrong payroll date gets corrected without
    # touching code.
    srcs = sources if sources is not None else [
        LocalSource(), NfpSource(), FomcSource()]

    seen, out = set(), []
    for s in srcs:
        for e in s.events():
            if e.key in seen or not (now <= e.at <= horizon):
                continue
            seen.add(e.key)
            out.append(e)
    # Chronological, because it is a calendar and the next thing to happen is
    # the next row. Priority decides how loudly each one is announced, not
    # where it sits in the list; re-sorting by importance would put an event
    # three weeks out above one starting in ten minutes.
    return sorted(out, key=lambda e: e.at)


def next_major(within_days: int = 45, now: Optional[datetime] = None,
               sources=None) -> Optional[ScheduledEvent]:
    """The soonest high-priority event, for the app to pin to the top.

    Separate from `upcoming()` on purpose: that stays a plain chronological
    calendar, and this answers the different question of "what is the next
    thing that will actually move the market".
    """
    best = [e for e in upcoming(within_days, now, sources) if e.priority >= 100]
    return best[0] if best else None
