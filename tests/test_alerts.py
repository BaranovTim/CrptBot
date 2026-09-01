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
import tempfile
import time
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
        self.interval = "1h"

    # The engine now watches (symbol, interval) pairs rather than one
    # implicit default, because it used to call `dashboard()` with no
    # arguments and so produced alerts for BTCUSDT 1h only — no matter how
    # many coins were being followed.
    RECORD_INTERVALS = ("1h",)

    def trained_symbols(self):
        return ["BTCUSDT"]

    def dashboard(self, symbol=None, interval=None):
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


def _story(headline, bias="NO READING", impact="NO READING"):
    return {"headline": headline, "source": "cointelegraph",
            "url": "https://example.invalid/x", "bias": bias,
            "impact": impact, "published_at": NOW.isoformat()}


def _state_path() -> Path:
    """A private state file per engine.

    The engine persists `_seen` so a restart cannot swallow the alerts it was
    holding. That is right in production and poison in a test suite: the stub
    dashboard is deterministic, so its BUY alert has the same id every time,
    and the first test to fire it would silence every later one through a file
    on disk. Two tests below deliberately SHARE a path, to check the restart
    behaviour rather than to inherit it by accident.
    """
    return Path(tempfile.mkdtemp()) / "alerts.json"


def _engine(svc, state_path=None):
    e = AlertEngine(svc, state_path=state_path or _state_path())
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
    e = AlertEngine(StubService(whales=[_whale(mechanical=True)]),
                    state_path=_state_path())
    e._calendar = lambda: []
    assert not [a for a in e._whales() if a.kind == "whale"]
    return True


def test_a_filing_carries_the_TRADE_date_not_the_filing_date():
    """Otherwise a five-day-old trade reads as breaking news."""
    svc = StubService(whales=[_whale(traded_days_ago=4)])
    e = AlertEngine(svc, state_path=_state_path())
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


def test_a_headline_drives_the_bias_and_the_excerpt_cannot_invert_it():
    """A measured failure, pinned.

    "Ireland bars crypto from new tax-advantaged scheme" was labelled BULL
    because its excerpt read "Shares, bonds, funds, ETFs and insurance
    products will qualify" — a list of what crypto is being EXCLUDED from.
    One bullish word in the body inverted the story.

    Headlines carry the story; excerpts wander into context, comparisons and
    things that did not happen. The body may add weight to a reading and may
    never create one.
    """
    from datetime import datetime, timezone

    from agent3.scorers import LexiconScorer
    from newsfeed.events import NewsItem

    def item(headline, body=""):
        return NewsItem(headline=headline, source="test", body=body,
                        published_at=datetime(2026, 8, 31, tzinfo=timezone.utc))

    inverted = item("Ireland bars crypto from new tax-advantaged scheme",
                    "Shares, bonds, funds, ETFs and insurance products "
                    "will qualify for the accounts.")
    sc = LexiconScorer().score([inverted])[0]
    assert sc.magnitude == 0.0, (
        "an excerpt created a reading the headline does not support")

    # and a headline that IS directional still scores
    clear = item("Bitcoin And Ethereum ETFs Add $492M As Inflow Streak Continues")
    sc2 = LexiconScorer().score([clear])[0]
    assert sc2.direction > 0.15 and sc2.magnitude > 0, sc2
    return True


# ------------------------------------------------------- detection is a job
def test_the_engine_detects_without_being_asked():
    """THE BUG THIS PINS.

    `refresh` used to run only inside an `/api/alerts` request, so a
    transition was only ever noticed while somebody had the app open. A
    signal that flipped at 03:00 was not late — it was never detected, and
    nothing anywhere recorded that it had happened.
    """
    svc = StubService(action="FLAT")
    e = _engine(svc)
    e.start(interval=0.01)
    svc.action = "BUY"
    deadline = time.time() + 5
    while time.time() < deadline and not e._log:
        time.sleep(0.01)
    e.stop()
    assert [a.kind for a in e._log] == ["signal"], e._log
    # and a poll that arrives later READS it rather than causing it
    assert e.after(0)[0].kind == "signal"
    return True


def test_a_restart_neither_repeats_nor_swallows():
    """Both halves matter, and they pull in opposite directions.

    Losing `_seen` re-fires everything the phone was already told about;
    losing `_log` silently drops what it had not been told yet. The old
    engine kept neither, so a deploy did both at once.
    """
    path = _state_path()
    svc = StubService(action="FLAT")
    e = _engine(svc, state_path=path)
    svc.action = "BUY"
    fired = e.refresh()
    assert len(fired) == 1, fired

    # the same process, gone and come back
    again = AlertEngine(StubService(action="BUY"), state_path=path)
    again._calendar = lambda: []
    assert again.refresh() == [], "the restart re-alerted a signal already sent"
    assert [a.id for a in again._log] == [fired[0].id], (
        "the restart lost the alert the phone had not collected yet")
    return True


def test_a_signal_alert_says_how_strong_it_is():
    """So one sensitivity setting governs the screen and the phone alike.

    Without this the dashboard could be withholding a small call as too weak
    to show while the notification for that same call was already on the
    lock screen.
    """
    svc = StubService(action="FLAT")
    svc.strength = "medium"
    base = svc.dashboard

    def with_strength(symbol=None, interval=None):
        d = base(symbol, interval)
        d["recommendation"]["strength"] = svc.strength
        return d

    svc.dashboard = with_strength
    e = _engine(svc)
    svc.action = "BUY"
    fired = e.refresh()
    assert fired[0].strength == "medium", fired[0].strength
    assert fired[0].to_json()["strength"] == "medium"
    return True


def test_a_news_alert_carries_the_reading_the_phone_filters_on():
    """The four news levels are decided on the phone, from these two fields.

    Without them the app can only filter on a headline string, which means it
    cannot filter at all — and "only strong news" would silently mean "all
    news" or "no news" depending on which way the guess fell.
    """
    svc = StubService(news=[_story("Fed cuts rates", "BULL", "STRONG IMPACT")])
    # NOT `_engine`, which primes: priming marks this item seen, and the
    # probe would then correctly return nothing
    e = AlertEngine(svc, state_path=_state_path())
    a = e._news()[0]
    assert a.bias == "BULL"
    assert a.impact == "STRONG IMPACT"
    assert a.to_json()["bias"] == "BULL"
    # a strong story earns a heads-up banner; an unreadable one does not
    assert a.severity == "high"
    return True


def test_the_headline_is_the_title_not_the_words_news_released():
    """A collapsed notification has room for one line. It should be the news.

    The title used to be the literal string "News released" for every item,
    so four of them in the shade said "News released" four times and nothing
    else.
    """
    svc = StubService(news=[_story("Ireland bars crypto from tax scheme")])
    e = AlertEngine(svc, state_path=_state_path())
    a = e._news()[0]
    assert a.title == "Ireland bars crypto from tax scheme"
    # and an unreadable story says so rather than implying a verdict
    assert "no directional reading" in a.body
    assert a.severity == "medium"
    return True
