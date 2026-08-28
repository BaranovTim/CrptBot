"""Scheduled market events.

What these guard, in order of how badly they would mislead you:

  THE WRONG HOUR         FOMC statements land at 14:00 America/New_York. Half
                         the year that is 19:00 UTC and half it is 18:00. A
                         fixed offset puts the warning an hour out — which,
                         for a 60-minute lead, means it arrives as the event
                         is happening.

  THE WRONG DAY          a meeting runs two days and the statement is on the
                         SECOND. "Apr/May 30-1" ends on 1 May, not 30 April,
                         and a naive parse books it a day early.

  ONE BAD ROW            calendar.json is hand-edited. A typo in one entry
                         must not take the Fed dates down with it.

  A NAIVE TIMESTAMP      an `at` with no offset is ambiguous by up to a day.
                         Guessing UTC would silently shift every alert.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newsfeed.schedule import (
    FomcSource,
    LocalSource,
    ScheduledEvent,
    parse_fomc,
    upcoming,
)

PANEL = """
<div class="panel panel-default"><div class="panel-heading"><h4>{year} FOMC Meetings</h4></div>
{rows}
</div>
"""
ROW = ('<div class="fomc-meeting__month"><strong>{month}</strong></div>'
       '<div class="fomc-meeting__date">{date}</div>')


def _page(year, pairs):
    return PANEL.format(year=year,
                        rows="\n".join(ROW.format(month=m, date=d) for m, d in pairs))


def test_statement_is_on_the_second_day():
    evs = parse_fomc(_page(2026, [("January", "27-28")]))
    assert len(evs) == 1, evs
    assert evs[0].at.astimezone(timezone.utc).day == 28, evs[0].at
    return True


def test_daylight_saving_is_resolved_through_a_real_timezone():
    """14:00 ET is 19:00 UTC in winter and 18:00 UTC in summer."""
    winter = parse_fomc(_page(2026, [("January", "27-28")]))[0]
    summer = parse_fomc(_page(2026, [("June", "16-17")]))[0]
    assert winter.at.astimezone(timezone.utc).hour == 19, winter.at
    assert summer.at.astimezone(timezone.utc).hour == 18, summer.at
    return True


def test_a_meeting_spanning_two_months_ends_in_the_second():
    """"Apr/May 30-1" is 1 May. Booking 30 April warns a day early."""
    ev = parse_fomc(_page(2024, [("Apr/May", "30-1")]))[0]
    utc = ev.at.astimezone(timezone.utc)
    assert (utc.month, utc.day) == (5, 1), utc
    return True


def test_a_meeting_inside_one_month_does_not_roll_over():
    ev = parse_fomc(_page(2026, [("March", "17-18*")]))[0]
    utc = ev.at.astimezone(timezone.utc)
    assert (utc.month, utc.day) == (3, 18), utc
    return True


def test_keys_are_stable_so_an_alert_fires_once():
    a = parse_fomc(_page(2026, [("January", "27-28")]))[0]
    b = parse_fomc(_page(2026, [("January", "27-28")]))[0]
    assert a.key == b.key, "the same meeting produced two ids"
    return True


def test_local_source_skips_bad_rows_but_keeps_good_ones():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "calendar.json"
        good = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        p.write_text(json.dumps([
            {"title": "fine", "at": good},
            {"title": "no at"},                       # missing key
            {"at": good},                             # missing title
            {"title": "naive", "at": "2026-09-01T12:00:00"},   # no offset
            {"title": "garbage", "at": "not a date"},
        ]))
        evs = LocalSource(p).events()
    assert len(evs) == 1, [e.title for e in evs]
    assert evs[0].title == "fine"
    return True


def test_a_naive_timestamp_is_rejected_not_assumed_utc():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "calendar.json"
        p.write_text(json.dumps([{"title": "x", "at": "2026-09-01T12:00:00"}]))
        assert LocalSource(p).events() == []
    return True


def test_upcoming_filters_to_the_horizon_and_sorts():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)

    class Fake:
        def events(self):
            return [
                ScheduledEvent("c", "later", now + timedelta(days=5), "high", "t"),
                ScheduledEvent("a", "past", now - timedelta(days=1), "high", "t"),
                ScheduledEvent("b", "soon", now + timedelta(hours=2), "high", "t"),
                ScheduledEvent("d", "beyond", now + timedelta(days=90), "high", "t"),
            ]

    got = upcoming(within_days=21, now=now, sources=[Fake()])
    assert [e.title for e in got] == ["soon", "later"], [e.title for e in got]
    return True


def test_duplicate_keys_across_sources_appear_once():
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ev = ScheduledEvent("same", "one", now + timedelta(hours=1), "high", "t")

    class Fake:
        def events(self):
            return [ev]

    got = upcoming(within_days=21, now=now, sources=[Fake(), Fake()])
    assert len(got) == 1, got
    return True


def test_the_real_fed_page_parses(**_):
    """Network test: the parse is regex over HTML someone else controls."""
    evs = FomcSource().events()
    if not evs:
        return True                       # offline, and the cache is empty
    assert len(evs) >= 8, f"only {len(evs)} meetings — the page shape changed?"
    hours = {e.at.astimezone(timezone.utc).hour for e in evs}
    assert hours <= {18, 19}, f"unexpected statement hours {hours}"
    return True
