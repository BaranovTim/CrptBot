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

    def __init__(self, action="FLAT", live=None, whales=None, news=None,
                 strength="strong", levels=None, smart_note=""):
        self.action, self._live = action, live
        self.smart_note = smart_note
        # The notification body is built from these. They were absent from
        # the stub while the body was prose, so the redesign onto strength
        # and levels arrived with three lines nothing exercised.
        self.strength = strength
        self.levels = {"take_profit": 79607.25, "stop_loss": 77100.5,
                       "tp_offset_pct": 2.41, "sl_offset_pct": -1.83,
                       "side": "LONG"} if levels is None else levels
        # Steerable, because "one signal per bar" means a second transition
        # inside the same bar is correctly suppressed — a test that wants to
        # see one has to move the clock.
        self._bar = "2026-08-28T12:59:59.999000+00:00"
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
            "last_closed_bar": self._bar,
            "recommendation": {"action": self.action, "tone": "flat",
                               "detail": "d", "ev": 0.1, "size_pct": 1.0,
                               "strength": self.strength,
                               "smart_note": self.smart_note,
                               "window_ends": "x"},
            "levels": self.levels,
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


def test_a_spike_no_longer_interrupts_anyone():
    """Deliberately removed.

    "It moved 2% in three minutes" is a state of the market, not a change in
    what to do about it, and it fired many times over for every call that
    actually changed. The move is still on the dashboard; it just does not
    buzz a phone.
    """
    svc = StubService(live=None)
    e = _engine(svc)
    svc._live = {"beyond_spike_threshold": True,
                 "move_pct": -2.0, "move_atr": -1.5}
    assert e.refresh() == [], "a spike still produced an alert"
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


def test_a_filing_is_context_now_and_still_carries_the_TRADE_date():
    """Filings no longer notify on their own — they ride along with the next
    action change. The trade date still matters: a five-day-old trade offered
    as context for a call made today would imply a freshness it does not have.
    """
    svc = StubService(whales=[_whale(traded_days_ago=4)])
    e = AlertEngine(svc, state_path=_state_path())
    e._calendar = lambda: []

    assert e._whales() == [], "a filing produced a standalone alert"
    assert len(e._context) == 1, e._context
    entry = e._context[0]
    assert entry["kind"] == "whale"
    age_days = (NOW - entry["at"]).days
    assert age_days >= 3, f"context timestamped {age_days}d old, expected ~4"
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


def test_news_is_context_and_carries_its_reading_into_the_text():
    """A headline no longer interrupts anyone. Its BULL/BEAR reading rides
    into the context line, so the one notification you do get says what was
    happening rather than just that something was."""
    svc = StubService(news=[_story("Fed cuts rates", "BULL", "STRONG IMPACT")])
    e = AlertEngine(svc, state_path=_state_path())
    e._calendar = lambda: []

    assert e._news() == [], "a headline produced a standalone alert"
    assert len(e._context) == 1, e._context
    text = e._context[0]["text"]
    assert "Fed cuts rates" in text
    assert "BULL" in text and "STRONG IMPACT" in text, text
    return True


def test_an_unreadable_headline_does_not_get_a_fabricated_label():
    svc = StubService(news=[_story("Something happened")])
    e = AlertEngine(svc, state_path=_state_path())
    e._calendar = lambda: []
    e._news()
    assert "NO READING" not in e._context[0]["text"]
    assert e._context[0]["text"] == "Something happened"
    return True


def test_every_transition_alerts_including_the_exit_to_flat():
    """THE CHANGE THIS PINS.

    The old rule returned early unless the new action was BUY or SELL, so
    being told to CLOSE a position never notified — only being told to open
    one. An exit is a decision too, and it is the one you most want to hear
    about while you are holding something.
    """
    svc = StubService(action="FLAT")
    e = _engine(svc)

    svc.action = "BUY"
    opened = e.refresh()
    assert [a.kind for a in opened] == ["signal"], opened
    assert "FLAT" in opened[0].title and "BUY" in opened[0].title
    assert opened[0].severity == "high"

    # and back out again — this used to be silent
    svc.action = "FLAT"
    svc._bar = "2026-08-28T13:59:59.999000+00:00"      # a new bar
    closed = e.refresh()
    assert [a.kind for a in closed] == ["signal"], closed
    assert closed[0].extra["from"] == "BUY"
    assert closed[0].extra["to"] == "FLAT"
    # closing is worth telling you; it is not an emergency
    assert closed[0].severity == "medium"
    return True


def test_a_steady_call_still_never_alerts():
    svc = StubService(action="BUY")
    e = _engine(svc)
    for _ in range(5):
        assert e.refresh() == [], "an unchanged call produced an alert"
    return True


def test_context_rides_along_and_never_claims_to_be_the_cause():
    """News is not an input to the model — see the note at the top of
    `train.py`. So the wording has to be temporal, not causal. Saying
    "because of" would be inventing a mechanism that does not exist.
    """
    svc = StubService(action="FLAT",
                      news=[_story("SEC approves spot ETF", "BULL",
                                   "STRONG IMPACT")])
    e = _engine(svc)
    svc.action = "BUY"
    fired = e.refresh()

    assert len(fired) == 1, fired
    body = fired[0].body
    # A FLAG, not the headline. The body is four lines now; quoting a story
    # in it was most of the "too much text" the notification was cut down
    # from. What survives is the part that makes you go and look.
    assert "News that could affect: yes" in body, body
    assert "SEC approves spot ETF" not in body, body
    # The rule that outlives the redesign: temporal, never causal. "could
    # affect" is a thing to check; "because of" would invent a mechanism the
    # model does not have.
    for forbidden in ("because", "caused", "due to", "driven by"):
        assert forbidden not in body.lower(), (forbidden, body)
    return True


def test_context_older_than_the_window_is_not_attached():
    """A headline from last week is history, not context for a call made
    today."""
    import datetime as _dt

    from api.alerts import CONTEXT_WINDOW

    svc = StubService(action="FLAT")
    e = _engine(svc)
    e._context.append({"kind": "news", "text": "ancient news",
                       "at": NOW - CONTEXT_WINDOW - _dt.timedelta(hours=1)})
    svc.action = "SELL"
    fired = e.refresh()
    # Asserted on the FLAG, not on the absence of the text. With the body
    # reduced to yes/no, "ancient news" is absent from every alert whether
    # the window works or not -- so checking for the string would pass
    # without testing anything.
    assert "News that could affect: no" in fired[0].body, fired[0].body
    return True


# The same table lives in `mobile/test/widget_test.dart`. Two runtimes format
# the same price -- Python for the notification, Dart for the dashboard behind
# it -- and a level that reads differently in the two is the bug this pins.
# Change one, change the other, or one of these two tests fails.
PRICE_TABLE = [
    (79607.25, "79,607.25"),
    (2517.085, "2,517.09"),
    (0.82615, "0.8262"),
    (0.003624, "0.003624"),
    (0.00001234, "0.00001234"),
    (0.0, "0.00"),
    (-1234.5, "-1,234.50"),
]


def test_prices_in_notifications_match_the_app_digit_for_digit():
    from api.alerts import _price_text

    for value, want in PRICE_TABLE:
        got = _price_text(value)
        assert got == want, f"{value}: {got!r} != {want!r}"
    assert _price_text(None) == "\u2014"
    return True


def test_an_entry_notification_is_four_lines_and_no_prose():
    """What Tim asked for, pinned. Strength, the two levels, the news flag."""
    svc = StubService(action="FLAT")
    e = _engine(svc)
    svc.action = "BUY"
    a = e.refresh()[0]

    assert a.title == "BTCUSDT: 1h; FLAT \u2192 BUY", a.title
    lines = a.body.split("\n")
    assert lines == ["STRONG",
                     "Take profit 79,607.25; +2.41%",
                     "Stop loss 77,100.50",
                     "News that could affect: no"], lines
    # The prose that used to be here and is not coming back.
    for gone in ("EV ", "size ", "% of equity", "Around the same time"):
        assert gone not in a.body, (gone, a.body)
    return True


def test_an_exit_notification_carries_no_target_or_stop():
    """Closing a position has no take-profit. Printing the model's barriers
    under the word FLAT would read as a new trade in the opposite direction.
    """
    svc = StubService(action="FLAT")
    e = _engine(svc)
    svc.action = "BUY"
    e.refresh()
    svc.action = "FLAT"
    svc._bar = "2026-08-28T13:59:59.999000+00:00"
    a = e.refresh()[0]

    assert "FLAT" in a.title, a.title
    assert "Take profit" not in a.body, a.body
    assert "Stop loss" not in a.body, a.body
    assert "News that could affect:" in a.body, a.body
    return True


def test_a_dashboard_coming_back_to_life_is_not_a_signal():
    """STALE -> FLAT is the cache being rebuilt, not the market moving. It
    arrived as a notification on every coin after every deploy."""
    svc = StubService(action="STALE")
    e = _engine(svc)
    svc.action = "FLAT"
    svc._bar = "2026-08-28T13:59:59.999000+00:00"
    assert e.refresh() == [], "STALE -> FLAT buzzed"
    # and FLAT -> FLAT across a rebuild is equally silent
    svc._bar = "2026-08-28T14:59:59.999000+00:00"
    assert e.refresh() == []
    # but STALE -> a real call is exactly what you want to hear
    svc.action = "BUY"
    svc._bar = "2026-08-28T15:59:59.999000+00:00"
    fired = e.refresh()
    assert len(fired) == 1 and "BUY" in fired[0].title, fired
    # and a call being withdrawn still buzzes as the exit it is
    svc.action = "FLAT"
    svc._bar = "2026-08-28T16:59:59.999000+00:00"
    fired = e.refresh()
    assert len(fired) == 1 and "FLAT" in fired[0].title, fired
    return True


def test_the_smart_money_line_sits_under_the_strength():
    """When the followed traders changed or confirmed a call, the
    notification says so, right after the strength and before the levels."""
    svc = StubService(action="FLAT",
                      smart_note="Smart money agrees: 2 followed traders opened LONG in the last 24h.")
    e = _engine(svc)
    svc.action = "BUY"
    a = e.refresh()[0]
    lines = a.body.split("\n")
    assert lines[0] == "STRONG" and lines[1].startswith("Smart money agrees"), lines
    assert lines[2].startswith("Take profit"), lines
    return True
