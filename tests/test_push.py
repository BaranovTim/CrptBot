"""The push relay — the path that works when the app is not running.

WHAT THESE GUARD, AND WHY EACH ONE EARNED A TEST

  THE SILENT DISAGREEMENT   the relay reimplements the phone's filtering
                            rules in a second language. The failure that
                            matters is not a crash, it is the two drifting
                            apart: "strong only" on the dashboard and every
                            small signal on the lock screen. So the rules are
                            asserted against the exact cases where the levels
                            differ, and against `alert_feed.dart`'s source.

  THE DROPPED EXIT          an exit carries no strength, so grading it by an
                            entry-strength setting rejects every one of them.
                            The app shipped that bug once already; the relay
                            is not going to ship it again.

  THE ENTRY THAT LOOKED     ...like an exit. Every entry alert's title reads
  LIKE ONE                  "FLAT -> BUY", so any exit test that looks at the
                            title lets entries skip the sensitivity gate —
                            the precise notification the setting exists to
                            suppress.

  THE GUESSABLE TOPIC       the topic is the only thing protecting the alert
                            stream. It has to come from a cryptographic
                            source and be long enough to be unguessable.

  THE DOUBLE SUBSCRIPTION   the app re-registers whenever a setting changes.
                            If each registration added a row, changing a
                            setting five times would mean five notifications
                            per alert.

  THE STORM                 a relay that has been offline must not deliver
                            forty messages when it comes back.

  THE DOUBLE NOTIFICATION   both delivery paths run at once — the relay and
                            the app's own polling — so the server records
                            what it pushed and `/api/alerts` reports it. That
                            record is a FACT, never an instruction: a send
                            that failed reports false and the app notifies as
                            it always did. No state silences both.
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.push import (MAX_PER_REFRESH, PushRelay, clears_news_level,
                      clears_sensitivity, is_kind_muted, is_muted, new_topic)

NOW = datetime.now(timezone.utc)


_SEQ = [0]


@dataclass
class FakeAlert:
    """The shape `AlertEngine` produces, minus everything the relay ignores."""

    id: str = ""
    kind: str = "signal"
    title: str = "BTCUSDT 1h: FLAT -> BUY"
    body: str = "EV +0.31%"
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    strength: str = "strong"
    bias: str = ""
    impact: str = ""
    url: str = ""
    detected_at: datetime = field(default_factory=lambda: NOW)
    extra: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            _SEQ[0] += 1
            self.id = f"fake-{_SEQ[0]:04d}"

    @property
    def seq(self) -> int:
        return int(self.detected_at.timestamp() * 1000)


class Recorder:
    """Stands in for urlopen. Records payloads; never touches a socket."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: List[dict] = []
        self.fail = fail

    def __call__(self, req, timeout=None):
        if self.fail:
            raise OSError("connection refused")
        import json as _json
        self.sent.append(_json.loads(req.data.decode()))
        return _Resp()


class _Resp:
    def read(self, n=None):
        return b"ok"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _relay(fail: bool = False):
    d = tempfile.mkdtemp()
    rec = Recorder(fail=fail)
    r = PushRelay(state_path=Path(d) / "push.json", opener=rec)
    return r, rec


def test_a_topic_is_long_and_never_repeats():
    """It is an address AND a password. A short or predictable one hands the
    alert stream to anyone who can enumerate."""
    seen = {new_topic() for _ in range(200)}
    assert len(seen) == 200, "topics collided — that is not a secure source"
    for t in seen:
        assert len(t) >= 32, t
    return True


def test_a_bad_topic_is_refused_and_http_is_refused():
    r, _ = _relay()
    assert r.register("short")["ok"] is False
    assert r.register("has spaces in it and is long")["ok"] is False
    assert r.register(new_topic(), server="http://ntfy.sh")["ok"] is False, \
        "plain http puts the topic, which is the password, on the wire"
    assert r.register(new_topic())["ok"] is True
    return True


def test_one_subscription_per_account():
    """The app re-registers on every settings change. Rows must be replaced,
    not accumulated, or a person who fiddles with a slider gets five copies of
    every alert."""
    r, rec = _relay()
    for _ in range(5):
        r.register(new_topic(), account="tim")
    assert r.count() == 1, r.count()

    r.deliver([FakeAlert()])
    assert len(rec.sent) == 1, rec.sent
    return True


def test_sensitivity_gates_the_push_exactly_as_it_gates_the_screen():
    r, rec = _relay()
    r.register(new_topic(), account="tim", sensitivity="strong")
    r.deliver([FakeAlert(strength="small"), FakeAlert(strength="medium")])
    assert rec.sent == [], "a weak signal reached a phone set to strong only"

    r2, rec2 = _relay()
    r2.register(new_topic(), account="tim", sensitivity="small")
    r2.deliver([FakeAlert(strength="small")])
    assert len(rec2.sent) == 1
    return True


def test_an_exit_is_delivered_however_strict_the_setting():
    """An exit has no strength — there is nothing being opened to grade — so a
    setting that grades entry strength must not touch it. `clearsSensitivity('')`
    is false, which is how every exit was silently dropped once already."""
    r, rec = _relay()
    r.register(new_topic(), account="tim", sensitivity="strong")
    r.deliver([FakeAlert(strength="", title="BTCUSDT 1h: BUY -> FLAT",
                         extra={"from": "BUY", "to": "FLAT"})])
    assert len(rec.sent) == 1, "the exit alert was dropped by the entry gate"
    return True


def test_an_entry_from_flat_is_not_mistaken_for_an_exit():
    """Every entry's title contains the word FLAT — "FLAT -> BUY". Deciding
    exit-ness from the title therefore lets weak ENTRIES past the sensitivity
    gate, which is the one thing that gate is for."""
    r, rec = _relay()
    r.register(new_topic(), account="tim", sensitivity="strong")
    r.deliver([FakeAlert(strength="small", title="BTCUSDT 1h: FLAT -> BUY",
                         extra={"from": "FLAT", "to": "BUY"})])
    assert rec.sent == [], "a weak entry rode in disguised as an exit"
    return True


def test_the_news_levels_are_filters_and_not_a_hierarchy():
    """A STRONG IMPACT story read as MIXED passes `strong` and fails
    `directional`. The two settings ask different questions and collapsing
    them would answer one with the other."""
    assert clears_news_level("MIXED", "STRONG IMPACT", "strong") is True
    assert clears_news_level("MIXED", "STRONG IMPACT", "directional") is False
    assert clears_news_level("BULL", "ALMOST NO IMPACT", "directional") is True
    assert clears_news_level("BULL", "STRONG IMPACT", "none") is False
    assert clears_news_level("", "", "all") is True
    return True


def test_a_muted_coin_wins_over_a_more_specific_rule():
    assert is_muted(["ADAUSDT"], "ADAUSDT", "1h") is True
    assert is_muted(["ADAUSDT:1m"], "ADAUSDT", "1h") is False
    assert is_muted(["ADAUSDT:1m"], "ADAUSDT", "1m") is True
    assert is_kind_muted(["kind:news"], "news") is True
    assert is_kind_muted(["kind:news"], "signal") is False

    r, rec = _relay()
    r.register(new_topic(), account="tim", muted=["BTCUSDT"])
    r.deliver([FakeAlert()])
    assert rec.sent == [], "a muted pair still buzzed the phone"
    return True


def test_stale_alerts_are_not_relayed_after_the_fact():
    """A signal describes a window that has since closed. Waking someone for
    one from this morning is worse than saying nothing."""
    r, rec = _relay()
    r.register(new_topic(), account="tim")
    r.deliver([FakeAlert(detected_at=NOW - timedelta(hours=9))])
    assert rec.sent == []
    return True


def test_a_backlog_is_capped_rather_than_dumped():
    """The reliable outcome of forty notifications at once is the channel
    being muted — taking the one that mattered with it."""
    r, rec = _relay()
    r.register(new_topic(), account="tim")
    r.deliver([FakeAlert(title=f"n{i}") for i in range(30)])
    assert len(rec.sent) == MAX_PER_REFRESH, len(rec.sent)
    return True


def test_a_dead_push_service_never_breaks_detection():
    """`deliver` is called from the alert loop. An exception there would stop
    the engine finding anything at all — trading the reliable path for the
    unreliable one."""
    r, rec = _relay(fail=True)
    r.register(new_topic(), account="tim")
    assert r.deliver([FakeAlert()]) == 0
    sub = r.for_account("tim")
    assert "OSError" in sub["last_error"], sub
    return True


def test_the_python_rules_match_the_dart_rules():
    """The relay's filtering is a SECOND implementation of what
    `alert_feed.dart` does. Two implementations of one rule set drift; this
    reads the Dart and asserts the cases it names still hold here.

    Not a parser — a check that the four news levels and the three
    sensitivities in the app are exactly the ones this module knows about. A
    level added on one side and forgotten on the other is the drift that
    would show up as notifications nobody asked for.
    """
    dart = (Path(__file__).resolve().parent.parent
            / "mobile/lib/api/alert_feed.dart").read_text()
    for level in ("none", "directional", "strong", "all"):
        assert f"case '{level}'" in dart or f"'{level}'" in dart, level
    # the ranking the app uses, mirrored here
    assert "'strong': 3" in dart and "'medium': 2" in dart \
        and "'small': 1" in dart, "the app's ranking changed"
    assert clears_sensitivity("strong", "strong") is True
    assert clears_sensitivity("medium", "strong") is False
    assert clears_sensitivity("strong", "unknown-setting") is True
    assert clears_sensitivity("", "small") is False
    return True


def test_subscriptions_survive_a_restart():
    """A relay that forgets its subscribers on deploy is a relay that stops
    working every time the server is updated, silently."""
    d = Path(tempfile.mkdtemp()) / "push.json"
    rec = Recorder()
    t = new_topic()
    PushRelay(state_path=d, opener=rec).register(t, account="tim",
                                                 sensitivity="small")
    again = PushRelay(state_path=d, opener=rec)
    assert again.count() == 1
    assert again.for_account("tim")["sensitivity"] == "small"
    return True


def test_what_was_pushed_is_reported_back_and_a_failure_is_not():
    """The app skips its own notification for an alert the relay already
    delivered. If that record were optimistic — recorded on attempt rather
    than on success — a push service having a bad afternoon would silence
    BOTH paths at once, which is worse than the duplicate it exists to
    prevent."""
    r, rec = _relay()
    r.register(new_topic(), account="tim")
    a = FakeAlert()
    r.deliver([a])
    assert r.pushed_to("tim") == {a.id}, r.pushed_to("tim")
    # someone else's account learns nothing about it
    assert r.pushed_to("someone-else") == set()
    assert r.pushed_to("") == set()

    dead, _ = _relay(fail=True)
    dead.register(new_topic(), account="tim")
    b = FakeAlert(title="another")
    dead.deliver([b])
    assert dead.pushed_to("tim") == set(), \
        "a failed send was recorded as delivered — both paths would go quiet"
    return True


def test_the_id_record_is_bounded():
    """It is persisted on every save. Unbounded, the state file grows for as
    long as the server runs."""
    from api.push import MAX_PUSHED

    r, _ = _relay()
    r.register(new_topic(), account="tim")
    for i in range(MAX_PUSHED + 50):
        r.deliver([FakeAlert(title=f"n{i}")])
    assert len(r.pushed_to("tim")) <= MAX_PUSHED
    return True


def test_the_topic_is_not_echoed_into_the_id_list_response():
    """`public()` is what the app renders. The pushed-id bookkeeping would
    only make it bigger on every poll."""
    r, _ = _relay()
    r.register(new_topic(), account="tim")
    r.deliver([FakeAlert()])
    assert "pushed" not in r.for_account("tim")
    return True
