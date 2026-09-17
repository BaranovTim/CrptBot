"""Notifications that reach a phone with the app closed.

WHAT WAS ACTUALLY WRONG
-----------------------
Nothing in the app. The background job, the Info.plist declarations, the
BGTaskScheduler identifier and the AppDelegate handler are all correct and all
verified. The problem is upstream of every line of that code: on iOS, a
BGAppRefreshTask runs WHEN iOS DECIDES, and for an app that is not opened
dozens of times a day iOS decides "a few times a day, maybe". Swipe the app
out of the switcher and it decides "never, until you open it again".

So the symptom was exactly what you would predict — alerts arriving in a batch
the moment the app is reopened, timestamped with when they actually happened.
The phone was not failing to check. It was never being woken up to check.

WHY THIS IS NOT SOLVED BY TRYING HARDER ON THE PHONE
----------------------------------------------------
The only mechanism on iOS that wakes an app on someone else's schedule is a
push through APNs, and the Push Notifications capability is an entitlement
tied to an App ID — which needs a paid Apple Developer account. Background
modes are a plist declaration and are free; push is not. There is no version
of "poll harder" that fixes this, because the app does not get to run.

WHAT THIS DOES INSTEAD
----------------------
Moves the delivery off the app entirely. The alert engine already runs
server-side on a 30-second loop, so the server knows about a signal flip
within half a minute of the bar that produced it — long before the phone does.
This relays that to a notification service whose OWN app already holds a valid
APNs entitlement, and which therefore can wake a phone that has ours closed,
force-quit, or in Low Power Mode.

The service is ntfy (https://ntfy.sh): free, no account, and the client is on
the App Store. A topic name is the only address, which means the topic name is
also the only secret — anyone who knows it can read the alerts and post to it.
So topics are generated as 160 bits of randomness by the app and never derived
from a username, a symbol, or anything guessable.

WHAT THE SERVER HAD TO LEARN
----------------------------
The filtering rules. Sensitivity, news level and the mute list all lived on the
device, which was correct while the device did the filtering. Now that the
relay decides, the phone ships its settings up with the subscription — the
phone is still the source of truth, the server is just applying its answer.
Otherwise "only strong signals" on the dashboard would arrive as every signal
on the lock screen.

WHAT IS DELIBERATELY NOT SENT
-----------------------------
The account identifier, the bearer token, prices, positions or anything about
the person. A relayed alert carries what the notification shows and nothing
else: a title, a line of body, and the symbol it concerns. Someone who learns
a topic learns which coins the owner watches. That is the whole exposure, and
it is the reason the topic is random rather than convenient.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import utc_now

log = logging.getLogger(__name__)

STATE_PATH = Path("data_cache") / "push.v1.json"

DEFAULT_SERVER = "https://ntfy.sh"

# One relay may not send more than this from a single refresh.
#
# Same reasoning as the app's `maxCatchUp`: a phone that comes back after an
# outage must not receive forty notifications, because the reliable outcome of
# that is the whole channel being muted and the one that mattered going with
# it. Lower than the app's six, because this fires unprompted rather than when
# someone has just opened the app.
MAX_PER_REFRESH = 4

# How many delivered ids to remember per subscription.
#
# Only has to outlive the app's catch-up window — `maxBacklogAge` is six hours
# and the engine's own log is capped at 300 — so a few hundred is generous.
MAX_PUSHED = 400

# Nothing older than this is worth interrupting for after the fact. A signal
# describes a window that has since closed.
MAX_AGE = timedelta(hours=6)

# A whole refresh's relay gets this long, total. The alert loop runs every 30
# seconds on a single core and must not be held up by someone else's TLS
# handshake.
TIMEOUT = 6.0

# A topic is an address AND a password, so it is generated, never chosen.
# 32 hex characters is 128 bits; the prefix is only there to make the string
# recognisable in a list of subscriptions on the phone.
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def new_topic() -> str:
    """A fresh, unguessable topic. Used by the app; kept here so both ends
    agree on the shape."""
    return "vanth-" + secrets.token_hex(16)


# --------------------------------------------------------------- filtering
#
# These three mirror `alert_feed.dart` exactly. They are duplicated rather
# than shared because one runs in Dart on a phone and one runs in Python on a
# server, and the alternative to duplication is not sharing — it is the server
# quietly applying different rules from the screen. The tests assert the two
# agree on the cases that differ.

_ORDER = {"strong": 3, "medium": 2, "small": 1}


def clears_sensitivity(strength: str, setting: str) -> bool:
    s = _ORDER.get(strength or "", 0)
    if s == 0:
        return False                       # no strength is not a call
    return s >= _ORDER.get(setting or "", 3)   # unknown setting = strictest


def clears_news_level(bias: str, impact: str, level: str) -> bool:
    if level == "none":
        return False
    if level == "directional":
        return bias in ("BULL", "BEAR")
    if level == "strong":
        return impact == "STRONG IMPACT"
    return True                            # "all", and anything unrecognised


def is_muted(muted: List[str], symbol: str, interval: str) -> bool:
    """The app's key scheme, read back: `SYMBOL`, `SYMBOL:interval`,
    `kind:name`. A coin-level mute wins over anything more specific."""
    if not symbol:
        return False
    keys = set(muted or ())
    sym = symbol.strip().upper()
    if sym in keys:
        return True
    return bool(interval) and f"{sym}:{interval}" in keys


def is_kind_muted(muted: List[str], kind: str) -> bool:
    return f"kind:{(kind or '').lower()}" in set(muted or ())


MAX_POSITIONS = 50


def _clean_positions(raw) -> List[Dict[str, Any]]:
    """Keep only what the line needs, in a shape nothing downstream has to
    defend against. One entry per symbol -- a second open entry on the same
    coin is still one "you hold this"."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in raw or ():
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").strip().upper()
        side = str(p.get("side") or "").strip().upper()
        try:
            entry = float(p.get("entry"))
        except (TypeError, ValueError):
            continue
        if not sym or side not in ("LONG", "SHORT") or not entry > 0:
            continue
        if sym not in out and len(out) < MAX_POSITIONS:
            out[sym] = {"symbol": sym, "side": side, "entry": entry}
    return list(out.values())


LEVELS = ("strong", "medium", "small")


def _clean_overrides(raw) -> Dict[str, str]:
    """SYMBOL -> level, and only levels the filter understands. Anything
    else off the wire is dropped rather than stored, so a garbage value can
    never make `clears_sensitivity` answer for a setting that does not
    exist."""
    out: Dict[str, str] = {}
    for k, v in (raw or {}).items() if isinstance(raw, dict) else ():
        sym = str(k).strip().upper()
        lvl = str(v).strip().lower()
        if _SYMBOL.match(sym) and lvl in LEVELS and len(out) < 200:
            out[sym] = lvl
    return out


# Letters and digits, at least one letter, 3-20 long -- what a pair looks
# like. Anything else off the wire is not a coin and is not stored.
_SYMBOL = re.compile(r"^(?=.*[A-Z])[A-Z0-9]{3,20}$")


def level_for(sub, symbol: str) -> str:
    """The level that applies to this coin on this phone."""
    return sub.overrides.get((symbol or "").upper(), sub.sensitivity)


def entry_line(side: str, entry: float) -> str:
    """The line the phone shows too. Mirrored in `notifications.dart`, and
    the price goes through the same formatter the rest of the alert uses so
    the entry does not read as a different number from the levels above it.
    """
    from api.alerts import _price_text
    return f"Open entry: {side} @ {_price_text(entry)}"


def held_line(sub, symbol: str) -> Optional[str]:
    for p in sub.positions:
        if p.get("symbol") == symbol:
            return entry_line(p["side"], p["entry"])
    return None


@dataclass
class Subscription:
    """One phone, and the settings it filters by."""

    topic: str
    server: str = DEFAULT_SERVER
    # Which account registered it. Used so a second registration from the
    # same account REPLACES the first rather than doubling the notifications.
    account: str = ""
    sensitivity: str = "strong"
    news: str = "all"
    # The device's mute list, shipped verbatim — symbols, symbol:interval
    # pairs and kind: keys all in one flat list, exactly as the app stores it.
    muted: List[str] = field(default_factory=list)
    # The phone's OPEN trade log, shipped the same way as `muted`: a list of
    # {symbol, side, entry}. Used for exactly one thing -- a line in a signal
    # notification saying "you hold this" -- because the log lives on the
    # phone and the notification text is built here, so the relay cannot
    # know otherwise. Re-shipped whenever an entry opens or closes.
    positions: List[Dict[str, Any]] = field(default_factory=list)
    # Per-coin signal-strength overrides, SYMBOL -> level. The general
    # `sensitivity` applies to every coin not in here. Same reason as the
    # rest of this record: the phone decides, the relay applies the same
    # decision to the alerts the phone is not awake to see.
    overrides: Dict[str, str] = field(default_factory=dict)
    # "Silence everything" on the phone. Held here as well, because the
    # relay is the path that works while the app is closed -- a silence the
    # phone keeps and the server ignores is not one.
    silenced: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Observability, because a relay that silently stops is worse than one
    # that never started. The app reads these back.
    last_ok: Optional[float] = None
    last_error: str = ""
    sent: int = 0
    # The ids this subscription has already been pushed.
    #
    # WHY THE SERVER HAS TO REMEMBER THIS
    #     Both paths still run: the relay, and the app's own polling. Without
    #     a record, every alert that reached the phone twice — once through
    #     ntfy and once through the app — would show up as two notifications
    #     for one event, which is its own kind of broken.
    #
    #     Reported back through `/api/alerts` so the app can skip posting a
    #     notification it knows already arrived. It is a FACT about what was
    #     sent, not an instruction to stay silent, which matters: if the relay
    #     failed, the flag is false and the app notifies exactly as it always
    #     did. There is no state in which both go quiet.
    pushed: List[str] = field(default_factory=list)

    def public(self) -> dict:
        d = asdict(self)
        # The topic IS the secret, but it is the caller's own topic and the
        # app has to display it to be useful. Returned only to the account
        # that registered it.
        #
        # The id list is bookkeeping and is not: it would only make the
        # response bigger every poll.
        d.pop("pushed", None)
        return d


def _wants(sub: Subscription, a) -> bool:
    """Would this subscription's owner have seen this on their screen?"""
    if sub.silenced:
        return False
    kind = getattr(a, "kind", "")
    if is_kind_muted(sub.muted, kind):
        return False
    if is_muted(sub.muted, getattr(a, "symbol", ""), getattr(a, "interval", "")):
        return False
    if utc_now() - a.detected_at > MAX_AGE:
        return False
    # An exit carries no strength — there is no expected value to grade when
    # nothing is being opened — so grading it by an ENTRY-strength setting
    # would reject every one of them. The app learned this the hard way; the
    # relay is not going to learn it again.
    if kind == "signal" and not _is_exit(a):
        if not clears_sensitivity(getattr(a, "strength", ""),
                                  level_for(sub, getattr(a, "symbol", ""))):
            return False
    if kind == "news":
        if not clears_news_level(getattr(a, "bias", ""),
                                 getattr(a, "impact", ""), sub.news):
            return False
    return True


def _is_exit(a) -> bool:
    """A signal returning to FLAT — the same test `Alert.isExit` makes.

    Only `extra["to"]`. A title match would be worse than nothing: every
    entry alert reads "FLAT -> BUY", so "FLAT is in the title" is true for
    entries too, and an entry that skipped the sensitivity gate is exactly
    the notification the setting exists to suppress.
    """
    extra = getattr(a, "extra", None) or {}
    return str(extra.get("to", "")).upper() == "FLAT"


_RANK = {"signal": 0, "calendar": 1}


def _priority(a) -> int:
    """ntfy priority. 5 max, 4 high, 3 default, 2 low.

    A signal is the thing this app exists to tell you and gets high; max is
    reserved for nothing, because a channel where everything is max is a
    channel where nothing is. News sits at default so it lands in the shade
    without a sound on most configurations.
    """
    kind = getattr(a, "kind", "")
    if kind == "signal":
        return 5 if getattr(a, "strength", "") == "strong" else 4
    if kind == "calendar":
        return 4
    if kind == "smart":
        return 4                    # a followed trader acting is worth a sound
    return 3


_TAGS = {
    "signal": "chart_with_upwards_trend",
    "calendar": "calendar",
    "news": "newspaper",
    "whale": "whale",
    "smart": "whale",
}


class PushRelay:
    """The subscriptions, and the sending."""

    def __init__(self, state_path: Optional[Path] = None,
                 opener=None) -> None:
        self._lock = threading.Lock()
        self._subs: Dict[str, Subscription] = {}
        self._path = Path(state_path) if state_path else STATE_PATH
        # Injected in tests. Nothing here should ever reach the network from
        # a test run, and a module that can only be tested with a real HTTP
        # server does not get tested.
        self._open = opener or urllib.request.urlopen
        self._load()

    # ------------------------------------------------------------ registry
    def register(self, topic: str, *, account: str = "",
                 server: str = DEFAULT_SERVER,
                 sensitivity: str = "strong", news: str = "all",
                 muted: Optional[List[str]] = None,
                 positions: Optional[List[Dict[str, Any]]] = None,
                 overrides: Optional[Dict[str, str]] = None,
                 silenced: bool = False,
                 ) -> Dict[str, Any]:
        topic = (topic or "").strip()
        if not TOPIC_RE.match(topic):
            return {"ok": False,
                    "error": "a topic is 16-64 characters of letters, digits, "
                             "dashes or underscores"}
        server = (server or DEFAULT_SERVER).strip().rstrip("/")
        if not server.startswith("https://"):
            # http would put the alerts, and the topic that grants access to
            # them, on the wire in clear text.
            return {"ok": False, "error": "the push server must be https"}

        with self._lock:
            # ONE SUBSCRIPTION PER ACCOUNT.
            #
            # The app re-registers whenever a setting changes, which is the
            # whole point — the server has to learn the new sensitivity. If
            # each registration added a row, changing a setting five times
            # would mean five notifications for every alert.
            if account:
                for t, s in list(self._subs.items()):
                    if s.account == account and t != topic:
                        self._subs.pop(t, None)

            existing = self._subs.get(topic)
            sub = Subscription(
                topic=topic, server=server, account=account,
                sensitivity=sensitivity or "strong", news=news or "all",
                muted=list(muted or ()),
                positions=_clean_positions(positions),
                overrides=_clean_overrides(overrides),
                silenced=bool(silenced),
                created_at=existing.created_at if existing else time.time(),
                last_ok=existing.last_ok if existing else None,
                sent=existing.sent if existing else 0,
                # CARRIED OVER. The app re-registers whenever a setting
                # changes, so rebuilding this empty threw away the delivery
                # record every few minutes — `sent: 30` beside
                # `pushed: 0` was the tell.
                pushed=list(existing.pushed) if existing else [],
            )
            self._subs[topic] = sub
            self._save()
        return {"ok": True, "subscription": sub.public()}

    def forget(self, topic: str) -> Dict[str, Any]:
        with self._lock:
            gone = self._subs.pop((topic or "").strip(), None) is not None
            if gone:
                self._save()
        return {"ok": gone} if gone else {"ok": False, "error": "no such topic"}

    def for_account(self, account: str) -> Optional[dict]:
        with self._lock:
            for s in self._subs.values():
                if s.account == account:
                    return s.public()
        return None

    def pushed_to(self, account: str) -> set:
        """Alert ids this account has already had pushed to it.

        Empty for an account with no relay, which is the right answer: it
        makes `pushed` false everywhere and the app behaves exactly as it did
        before any of this existed.
        """
        if not account:
            return set()
        with self._lock:
            for s in self._subs.values():
                if s.account == account:
                    return set(s.pushed)
        return set()

    def count(self) -> int:
        with self._lock:
            return len(self._subs)

    # -------------------------------------------------------------- sending
    def deliver(self, alerts) -> int:
        """Relay what each subscriber would have seen. Returns messages sent.

        Never raises. This is called from the alert engine's own loop, and a
        push service having a bad afternoon must not stop detection.
        """
        with self._lock:
            subs = list(self._subs.values())
        if not subs or not alerts:
            return 0

        sent = 0
        for sub in subs:
            wanted = [a for a in alerts if _wants(sub, a)]
            if not wanted:
                continue
            wanted.sort(key=lambda a: (_RANK.get(a.kind, 2), -a.seq))
            for a in wanted[:MAX_PER_REFRESH]:
                if self._post(sub, a):
                    sent += 1
                    sub.pushed.append(a.id)
            if len(sub.pushed) > MAX_PUSHED:
                del sub.pushed[:-MAX_PUSHED]
        if sent:
            with self._lock:
                self._save()
        return sent

    def test(self, topic: str) -> Dict[str, Any]:
        """Send one message so the owner can confirm the chain works.

        Worth having as its own route: "I set it up and nothing came" has
        four possible causes — a typo in the topic, the ntfy app not
        subscribed, iOS permissions, or genuinely nothing having happened —
        and only this separates the last one from the first three.
        """
        with self._lock:
            sub = self._subs.get((topic or "").strip())
        if sub is None:
            return {"ok": False, "error": "that topic is not registered"}
        ok = self._send(sub, {
            "topic": sub.topic,
            "title": "Vanth is connected",
            "message": ("Alerts will arrive here even with the app closed. "
                        "This is the only test message."),
            "priority": 4,
            "tags": ["white_check_mark"],
        })
        with self._lock:
            self._save()
        return {"ok": ok, "error": sub.last_error if not ok else ""}

    def _post(self, sub: Subscription, a) -> bool:
        body = a.body or ""
        # FIRST line, not last. Android's collapsed notification shows the
        # title and one line of body; when you are holding the coin the call
        # just changed on, "you hold this" is the one line that should be
        # visible before you expand anything. Signals only: a news item
        # about a coin you hold is not a decision about your position.
        if getattr(a, "kind", "") == "signal":
            held = held_line(sub, getattr(a, "symbol", ""))
            if held:
                body = held + ("\n" + body if body else "")
        payload = {
            "topic": sub.topic,
            "title": a.title[:120],
            # ntfy renders the body as one block; the alert's own body
            # already carries the event time and any attached context.
            "message": body[:900] or a.title,
            "priority": _priority(a),
            "tags": [_TAGS.get(a.kind, "bell")],
        }
        if a.url:
            payload["click"] = a.url
        return self._send(sub, payload)

    def _send(self, sub: Subscription, payload: dict) -> bool:
        req = urllib.request.Request(
            sub.server + "/",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": "vanth-relay/1"},
            method="POST",
        )
        try:
            with self._open(req, timeout=TIMEOUT) as r:
                r.read(512)
            sub.last_ok = time.time()
            sub.last_error = ""
            sub.sent += 1
            return True
        except Exception as e:
            # Recorded rather than raised. The app shows `last_error`, which
            # turns "my notifications stopped" into a sentence naming the
            # cause.
            sub.last_error = f"{type(e).__name__}: {e}"[:200]
            log.warning("push: %s failed: %s", sub.topic[:16], sub.last_error)
            return False

    # ------------------------------------------------------------ persistence
    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {"subs": [asdict(s) for s in self._subs.values()]}, indent=1))
            tmp.replace(self._path)
            # The topic is a bearer secret in a file. 0600 so a second account
            # on the box cannot read the address of someone's notifications.
            os.chmod(self._path, 0o600)
        except Exception as e:
            log.warning("push: could not save subscriptions: %s", e)

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            raw = json.loads(self._path.read_text())
            for d in raw.get("subs", []):
                d = {k: v for k, v in d.items()
                     if k in Subscription.__dataclass_fields__}
                s = Subscription(**d)
                self._subs[s.topic] = s
        except Exception as e:
            log.warning("push: state ignored: %s", e)
            self._subs = {}


_RELAY: Optional[PushRelay] = None
_RELAY_LOCK = threading.Lock()


def get_relay() -> PushRelay:
    global _RELAY
    with _RELAY_LOCK:
        if _RELAY is None:
            _RELAY = PushRelay()
    return _RELAY
