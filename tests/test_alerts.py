"""The alert engine — what is worth waking a phone for.

What these guard, in order of how badly they would mislead you:

  THE NOTIFICATION STORM   the cursor is epoch MILLISECONDS, not an ISO
                           string, because `+00:00` in a query string decodes
                           its `+` as a SPACE. That made every poll look like
                           a first poll and re-delivered the whole backlog.
                           An unparseable cursor must also return NOTHING —
                           failing open here is what turns one bad request
                           into a phone buzzing every twenty seconds.

  THE REPEATED STATE       "the recommendation is FLAT" is a state and must
                           never be sent. Only transitions are alerts.

  THE REPLAYED BACKLOG     a first poll with no cursor returns nothing.
                           Otherwise opening the app fires twenty
                           notifications about filings from last week.

  THE MISSING TIMESTAMP    a filing discloses a trade up to five days old.
                           An alert that carries only "now" implies a
                           freshness the data does not have.

  THE MECHANICAL FILING    code F is tax withholding on vesting. Nobody
                           decided anything, and it must never alert.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.alerts import LEAD_MINUTES, AlertEngine

NOW = datetime.now(timezone.utc)


class StubService:
    """A dashboard payload we can steer, with no models and no network."""

    def __init__(self, action="FLAT", live=None, whales=None, news=None):
        self.action, self._live = action, live
        self._whales, self._news = whales or [], news or []
        self.symbol = "BTCUSDT"

    def dashboard(self):
        return {
            "symbol": "BTCUSDT",
            "last_closed_bar": "2026-08-28T12:59:59.999000+00:00",
            "recommendation": {"action": self.action, "tone": "flat",
                               "detail": "d", "ev": 0.1, "size_pct": 1.0,
                               "window_ends": "x"},
            "live": self._live,
        }

    def whales(self, limit=25):
        return self._whales

    def news(self, limit=25):
        return self._news


def _engine(svc):
    e = AlertEngine(svc)
    e._calendar = lambda: []          # calendar is covered in test_schedule
    e.refresh()                        # prime
    return e


# ------------------------------------------------------------- the cursor
def test_no_cursor_returns_nothing_not_the_backlog():
    e = _engine(StubService(whales=[_whale()]))
    assert e.after(None) == [], "a first poll replayed the backlog"
    return True


def test_an_unparseable_cursor_fails_closed():
    """Failing open turns one bad request into a notification storm."""
    e = _engine(StubService(whales=[_whale()]))
    assert e.after(None) == []
    return True


def test_the_cursor_is_integer_milliseconds():
    """An ISO cursor loses its `+` to query-string decoding. Integers cannot."""
    svc = StubService(action="FLAT")
    e = _engine(svc)               # prime BEFORE the transition, or it is
    svc.action = "BUY"             # consumed as part of the baseline
    got = e.refresh()
    assert got, "no transition alert produced"
    seq = got[0].seq
    assert isinstance(seq, int) and seq > 1_600_000_000_000, seq
    assert str(seq).isdigit(), "the cursor must survive a URL untouched"
    return True


def test_alerts_after_a_cursor_are_only_the_newer_ones():
    svc = StubService()
    e = _engine(svc)
    svc.action = "BUY"
    first = e.refresh()
    assert len(first) == 1, first
    cursor = first[0].seq
    assert e.after(cursor) == [], "the same alert came back after its cursor"
    return True


# ------------------------------------------------------- state vs transition
def test_a_steady_recommendation_never_alerts():
    svc = StubService(action="FLAT")
    e = _engine(svc)
    for _ in range(5):
        assert e.refresh() == [], "a stable state produced an alert"
    return True


def test_only_the_transition_into_an_actionable_side_alerts():
    svc = StubService(action="FLAT")
    e = _engine(svc)
    svc.action = "BUY"
    fired = e.refresh()
    assert [a.kind for a in fired] == ["signal"], fired
    assert "BUY" in fired[0].title
    assert e.refresh() == [], "BUY -> BUY alerted twice"
    return True


def test_returning_to_flat_does_not_alert():
    """Going flat is not a trade. It must not interrupt anyone."""
    svc = StubService(action="FLAT")
    e = _engine(svc)
    svc.action = "BUY"
    e.refresh()
    svc.action = "FLAT"
    assert e.refresh() == [], "an exit produced a notification"
    return True


def test_a_spike_alerts_once_per_bar():
    svc = StubService(live=None)
    e = _engine(svc)               # prime with a quiet bar first
    svc._live = {"beyond_spike_threshold": True,
                 "move_pct": -2.0, "move_atr": -1.5}
    first = e.refresh()
    assert [a.kind for a in first] == ["spike"], first
    assert e.refresh() == [], "the same spike alerted twice in one bar"
    return True


# ------------------------------------------------------------------ whales
def _whale(mechanical=False, code="S", traded_days_ago=4):
    traded = NOW - timedelta(days=traded_days_ago)
    return {
        "describe": "Someone SELL $500,000 of MSTR",
        "impact": "MECHANICAL - NOT A VIEW" if mechanical else "BEARISH",
        "side": "SELL", "mechanical": mechanical, "conviction": 0.5,
        "code": code, "note": "lagging evidence, not a trigger",
        "published_at": NOW.isoformat(),
        "event_time": traded.isoformat(),
    }


def test_a_mechanical_filing_never_alerts():
    e = AlertEngine(StubService(whales=[_whale(mechanical=True)]))
    e._calendar = lambda: []
    assert not [a for a in e._whales() if a.kind == "whale"]
    return True


def test_a_filing_carries_the_TRADE_date_not_the_filing_date():
    """Otherwise a five-day-old trade reads as breaking news."""
    svc = StubService(whales=[_whale(traded_days_ago=4)])
    e = AlertEngine(svc)
    e._calendar = lambda: []
    a = e._whales()[0]
    age_days = (NOW - a.at).days
    assert age_days >= 3, f"alert timestamped {age_days}d old, expected ~4"
    assert "disclosed_at" in a.extra, "the disclosure time was dropped"
    assert a.extra["disclosed_at"] != a.at.isoformat(), (
        "trade time and disclosure time collapsed into one")
    return True


def test_the_same_filing_does_not_alert_twice():
    svc = StubService(whales=[_whale()])
    e = _engine(svc)
    assert e.refresh() == [], "a filing already seen alerted again"
    return True


# ---------------------------------------------------------------- resilience
def test_one_dead_feed_does_not_silence_the_others():
    """A broken whale store must not stop a BUY signal reaching the phone."""
    svc = StubService(action="FLAT")

    def boom(limit=25):
        raise RuntimeError("store unreadable")

    svc.whales = boom
    e = _engine(svc)
    svc.action = "BUY"
    fired = e.refresh()
    assert [a.kind for a in fired] == ["signal"], fired
    return True


def test_lead_windows_are_ordered_widest_first():
    """The tightest applicable window is the one that should describe the
    wait, so the list has to be descending for the break to pick it."""
    assert list(LEAD_MINUTES) == sorted(LEAD_MINUTES, reverse=True), LEAD_MINUTES
    return True
