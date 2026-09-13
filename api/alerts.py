"""What is worth waking a phone for.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
A notification is an interruption, and the fastest way to make one worthless
is to send it too often. So every alert here is a TRANSITION, never a state.
"The recommendation is FLAT" is a state and is never sent. "The recommendation
just became BUY" is a transition and is sent once. The engine holds the
previous value for exactly this reason.

ONE THING INTERRUPTS YOU: THE CALL CHANGING
-------------------------------------------
Every transition between FLAT, BUY and SELL now notifies, INCLUDING the exit
back to FLAT — closing a position is a decision as much as opening one, and
the old code deliberately stayed silent on it.

Everything else that used to buzz no longer does:

  SPIKES     removed. "It moved 2% in three minutes" is a state of the market,
             not a change in what to do about it, and it fired far more often
             than any call changed.

  NEWS and   these no longer notify on their own. They are collected as
  WHALES     CONTEXT and attached to the next action change, so the question
             a notification answers is "the call changed, and here is what was
             in the news around then" rather than "a headline exists".

             Both are still in the app in full — this changes what interrupts
             you, not what you can read.

WHAT THIS DELIBERATELY DOES NOT CLAIM
-------------------------------------
That the news CAUSED the change. It cannot: news is not an input to the
model — see the note at the top of `train.py`, where Agent 3 is left
disconnected because one story per day against 175,000 one-minute bars is a
column the fit ignores. So the attached headlines are labelled as what was
happening at the time, and nothing stronger. Presenting coincidence as cause
is the one thing this module must not do.

The same rule kills the obvious duplicates: a calendar event is alerted on its
key plus which lead window fired. Re-polling never re-sends.

EVERY ALERT CARRIES WHEN IT HAPPENED
------------------------------------
Not when it was noticed. These are different, sometimes by days: an SEC Form 4
discloses a trade that happened up to five days earlier, and a notification
that says only "just now" would imply a freshness the data does not have. So
alerts carry `at` (when the thing happened) alongside `detected_at`, and for
filings both the trade date and the disclosure date.

THE ENGINE RUNS ON ITS OWN CLOCK
--------------------------------
It used to refresh only inside an `/api/alerts` request, which made detection
a side effect of somebody looking. Since a transition is defined against the
PREVIOUS observation, that meant the phone could only ever be told about
changes that happened during the seconds the app was open — and every cold
start re-primed the engine, swallowing everything that had accumulated while
it was closed. News and signal alerts were therefore almost never delivered.

So `start()` runs the probes on a timer from process start, and the log is
persisted. Detection is now continuous and independent of whether anyone is
holding a phone; a poll only READS what has already been noticed.

NOTHING HERE FEEDS A MODEL
--------------------------
Alerts are a human channel. They are derived from the same payload the
dashboard shows and never flow back into features — a scheduled FOMC date is
known to the entire market and is priced, so treating it as signal would be
modelling the calendar rather than the market.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from core import utc_now

log = logging.getLogger(__name__)

# How far ahead of a scheduled event to warn. Two windows, deliberately: the
# hour lets you flatten or size down, the five minutes is the "it is about to
# happen" nudge. More than two and it becomes nagging.
# Fallback only. Each event now names its own windows through
# `ScheduledEvent.lead_minutes`, because one tuple cannot serve both a routine
# print and Non-Farm Payrolls: NFP is the largest scheduled volatility event
# of the month and an hour's notice is not enough to act on, while giving
# every minor release a day's notice would be noise.
LEAD_MINUTES = (60, 5)

MAX_LOG = 300

# How often the engine looks, when it is running its own loop.
#
# A warm refresh reads 16 cached dashboards plus the news and whale stores and
# costs 0.3-0.9s measured on the droplet's single core, so 30s is roughly 2%
# of one core. Fast enough that a signal is noticed within half a minute of
# the bar that produced it; slow enough to be invisible in the CPU graph.
REFRESH_SECONDS = 30

# Where the log survives a restart.
#
# Without this, a deploy or a reboot emptied `_seen` and `_log`, and the next
# refresh re-primed from scratch — so everything that happened around the
# restart was silently swallowed rather than delivered.
STATE_PATH = Path("data_cache") / "alerts.v1.json"

# `_seen` never shrinks on its own, so persisting it needs a bound. 4000 is
# far more than the ~50 ids any single refresh can produce, since the probes
# only ever look at the newest 25 filings and 25 headlines — an id can never
# fall out of this window while the thing it names is still visible.
MAX_SEEN = 4000

# How far back a headline still counts as "around the time the call changed".
# Six hours because that is roughly the horizon of the timeframes that produce
# most signals; older than that and it is history, not context.
CONTEXT_WINDOW = timedelta(hours=6)

# How many headlines ride along with one alert. Three fits a notification
# without turning it into a newsletter.
MAX_CONTEXT = 3


@dataclass
class Alert:
    id: str
    kind: str                  # signal | whale | news | spike | calendar
    title: str
    body: str
    at: datetime               # when the underlying event happened / happens
    detected_at: datetime
    severity: str = "medium"   # high | medium | low
    # Which timeframe produced it, "" when the alert belongs to no timeframe
    # (a filing, a macro release). The app mutes per symbol AND per interval,
    # which it cannot do if the alert does not say which one it came from.
    interval: str = ""
    # strong | medium | small for a signal, "" for everything else.
    #
    # Carried so the phone can apply the SAME sensitivity setting to
    # notifications that it applies to the dashboard. Without it, a user who
    # chose "only strong signals" still got woken by every small one — the
    # screen and the notification would be telling them different things.
    strength: str = ""
    # BULL | BEAR | MIXED | NO READING, and STRONG/MEDIUM/ALMOST NO IMPACT,
    # for a news alert. "" for every other kind.
    #
    # Same reason as `strength`: the phone filters news by direction and by
    # impact, and it cannot do that from a headline string. Agent 3's scorer
    # has already worked both out for the dashboard card, so they are carried
    # rather than recomputed — one calculation, and the card and the
    # notification can never disagree about the same story.
    bias: str = ""
    impact: str = ""
    symbol: str = ""
    url: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def seq(self) -> int:
        """Detection time as epoch milliseconds — the cursor clients page on.

        Deliberately NOT the ISO string. A timestamp like
        `2026-08-28T14:16:04+00:00` in a query string has its `+` decoded as a
        SPACE, so it arrives as `...04 00:00`, fails to parse, and a naive
        handler falls through to "return everything" — which means every poll
        re-delivers the whole backlog and the phone buzzes forever. An integer
        has no such edge.
        """
        return int(self.detected_at.timestamp() * 1000)

    def to_json(self) -> dict:
        d = asdict(self)
        d["at"] = self.at.isoformat()
        d["detected_at"] = self.detected_at.isoformat()
        d["seq"] = self.seq
        return d


def _hash(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _price_text(v) -> str:
    """A price with enough digits to act on. Mirrors `format.dart`'s
    `priceText`, thresholds and trimming included.

    A SECOND COPY, KNOWINGLY. The phone formats prices in Dart and this
    formats them in Python, and the two have to agree or the same level
    reads differently in the notification and on the dashboard behind it.
    Shared code is not available across the two runtimes, so the rule is
    written twice and `test_alerts` pins them to the same answers.

    Fixed decimals are the bug this avoids: 1000PEPEUSDT trades at 0.003624
    and `%.4f` renders it 0.0036, throwing away the digits the coin moves in.
    """
    if v is None:
        return "\u2014"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "\u2014"
    a = abs(v)
    if a >= 1000:
        dp = 2
    elif a >= 1 or a == 0:
        dp = 4 if a else 2
    else:
        dp = min(8, max(2, -(math.floor(math.log10(a)) + 1) + 4))
    out = f"{v:,.{dp}f}"
    if "." in out and dp > 2:
        whole, frac = out.split(".")
        while len(frac) > 2 and frac.endswith("0"):
            frac = frac[:-1]
        out = f"{whole}.{frac}"
    return out


class AlertEngine:
    """Turns the dashboard payload and the feeds into a stream of transitions."""

    def __init__(self, service, pairs=None, state_path=None):
        self.svc = service
        self._log: List[Alert] = []
        # A DICT used as an ordered set, not a `set`.
        #
        # Persisting it needs a bound, and a bound needs an order: trimming an
        # unordered set keeps an arbitrary subset, which would drop ids seen a
        # minute ago while keeping ones from last week — and every alert whose
        # id was dropped fires again.
        self._seen: Dict[str, None] = {}
        # PER PAIR, not global.
        #
        # These were three scalars, which was only correct while the engine
        # watched exactly one pair. It watched `svc.dashboard()` with no
        # arguments - the default, BTCUSDT 1h - so following four coins
        # produced alerts for one of them and no alerts at all for the other
        # three. Sharing one `_last_action` across pairs would be worse than
        # the bug it replaces: ETH flipping to BUY would suppress BTC's
        # identical flip, and which one you were told about would depend on
        # iteration order.
        self._last_action: Dict[tuple, Optional[str]] = {}
        self._last_signal_bar: Dict[tuple, Optional[str]] = {}
        self._spiked_bar: Dict[tuple, Optional[str]] = {}
        self._primed = False
        self._pairs = pairs
        # Recent headlines and filings, held for attachment to the next
        # action change rather than sent on their own. Bounded: this is
        # context for one notification, not an archive.
        self._context: List[dict] = []
        # `refresh` now runs from the background loop AND from request
        # threads. Without this, two refreshes can interleave between the
        # `_seen` check and the `_seen.add`, and the same alert is delivered
        # twice — or worse, `_last_action` is written by one thread and read
        # by the other mid-probe, so a transition is compared against the
        # wrong previous value and silently lost.
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state_path = Path(state_path) if state_path else STATE_PATH
        self._load_state()

    def pairs(self) -> List[tuple]:
        """(symbol, interval) pairs to watch.

        Defaults to every trained symbol on the intervals the recorder keeps
        warm on a clock. Watching all six timeframes for four symbols would
        be 24 dashboard reads per poll on a one-core box; these are the ones
        already built every bar, so reading them is free.
        """
        if self._pairs is not None:
            return list(self._pairs)
        try:
            ivs = getattr(self.svc, "RECORD_INTERVALS", ("1h",))
            # The RECORDER's set, not every trained symbol. Watching a pair
            # nothing keeps warm means building it cold on the alert loop's
            # own thread — 160s and ~750MB a time, every 30 seconds, which is
            # how the API met the OOM killer.
            syms = (self.svc.record_symbols()
                    if hasattr(self.svc, "record_symbols")
                    else self.svc.trained_symbols())
            return [(sym, iv) for iv in ivs for sym in syms]
        except Exception:
            return [(self.svc.symbol, self.svc.interval)]

    # -- the loop ------------------------------------------------------
    def start(self, interval: float = REFRESH_SECONDS) -> None:
        """Refresh forever, in the background.

        THE BUG THIS FIXES
            `refresh()` used to run only inside an `/api/alerts` request. A
            transition is defined against the previous observation, so with
            nobody polling there was no previous observation to compare
            against — the app could only be told about changes that happened
            while it was open and in the foreground, which on a phone is a
            few minutes a day. Every other signal flip and every headline
            went unnoticed, not undelivered: they were never detected at all.

        Idempotent, so calling it twice does not start two loops.
        """
        if self._thread is not None and self._thread.is_alive():
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    new = self.refresh()
                    if new:
                        log.info("alerts: %d new (%s)", len(new),
                                 ", ".join(sorted({a.kind for a in new})))
                        # RELAYED HERE, not from the phone.
                        #
                        # This is the moment the alert exists, and it is the
                        # only moment at which anything in this system knows
                        # about it while the phone is asleep. iOS will not run
                        # our background task on any schedule worth relying
                        # on — see `api/push.py` — so a channel that does not
                        # depend on our app being alive has to be fed from
                        # the server's own loop.
                        self._relay(new)
                except Exception as e:
                    # the loop outlives any single bad refresh; a feed that
                    # is down for an hour must not end alerting for the day
                    log.warning("alert loop: %s", e)
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, daemon=True,
                                        name="alerts")
        self._thread.start()
        log.info("alert engine started, refreshing every %.0fs", interval)

    @staticmethod
    def _relay(new: List["Alert"]) -> None:
        """Hand the batch to the push relay. Never raises.

        Detection and delivery are separate concerns and separate failure
        modes: a push service being unreachable must leave the log, the
        cursor and `/api/alerts` working exactly as they do today. The phone
        polling remains the primary path — this is a second one that survives
        the app being closed.
        """
        try:
            from api.push import get_relay

            relay = get_relay()
            if relay.count():
                sent = relay.deliver(new)
                if sent:
                    log.info("alerts: relayed %d push message(s)", sent)
        except Exception as e:
            log.warning("alerts: push relay failed: %s", e)

    def stop(self) -> None:
        self._stop.set()

    # -- public --------------------------------------------------------
    def refresh(self) -> List[Alert]:
        """Look once. Returns only what is new."""
        fresh: List[Alert] = []
        for probe in (self._signal, self._whales, self._news, self._calendar):
            try:
                fresh.extend(probe())
            except Exception as e:                 # one dead feed must not
                log.warning("%s failed: %s", probe.__name__, e)  # kill the rest

        with self._lock:
            new = [a for a in fresh if a.id not in self._seen]
            for a in new:
                self._seen[a.id] = None
            self._log.extend(new)
            if len(self._log) > MAX_LOG:
                self._log = self._log[-MAX_LOG:]

            # The first poll of a NEW engine would otherwise replay history as
            # if it had just happened. Record it, return nothing.
            #
            # Restored state counts as primed: after a restart the engine
            # already knows what it had seen, so the first refresh is an
            # ordinary one and anything genuinely new during the downtime is
            # real news that deserves to be delivered.
            first = not self._primed
            self._primed = True
            if new or first:
                self._save_state()
            if first:
                return []
        return new

    def after(self, cursor: Optional[int]) -> List[Alert]:
        """Alerts detected after an epoch-millisecond cursor.

        No cursor deliberately returns NOTHING. Returning the backlog there
        would make every app launch fire twenty notifications about filings
        from last week — the exact behaviour that teaches someone to swipe an
        app's alerts away without reading them. The caller stores `cursor`
        from the response and passes it back.

        An UNPARSEABLE cursor also returns nothing, not everything. Failing
        open here is what turns one bad request into a notification storm.

        NEVER REFRESHES. Detection belongs to `start()`'s loop, for two
        reasons that both bit:

          1. A request that primed a cold engine would swallow exactly the
             backlog it was asking for.
          2. A cold refresh builds a dashboard per watched pair and was
             MEASURED at over ten minutes on an empty cache. Inside a request
             that is a phone waiting on a socket until it times out, then
             asking again, and a queue of duplicate refreshes behind it.

        Before the first refresh completes this answers empty, which is the
        truth: nothing has been observed yet.
        """
        if cursor is None:
            return []
        with self._lock:
            return [a for a in self._log if a.seq > cursor]

    def cursor(self) -> int:
        return int(utc_now().timestamp() * 1000)

    # -- persistence ---------------------------------------------------
    #
    # A restart used to empty `_seen` and `_log`, so the next refresh primed
    # from scratch and everything around the restart was swallowed. Deploys
    # are frequent enough that this was a routine way to lose a day's alerts.

    def _load_state(self) -> None:
        try:
            raw = json.loads(self._state_path.read_text())
        except (OSError, ValueError):
            return                       # no state yet is the normal case
        try:
            self._seen = dict.fromkeys(raw.get("seen", []))
            self._last_action = {tuple(k.split("|")): v
                                 for k, v in raw.get("last_action", {}).items()}
            self._last_signal_bar = {
                tuple(k.split("|")): v
                for k, v in raw.get("last_signal_bar", {}).items()}
            self._spiked_bar = {tuple(k.split("|")): v
                                for k, v in raw.get("spiked_bar", {}).items()}
            self._log = [_alert_from_json(a) for a in raw.get("log", [])]
            self._primed = bool(raw.get("primed", False))
            log.info("alerts: restored %d in the log, %d ids seen",
                     len(self._log), len(self._seen))
        except (KeyError, TypeError, ValueError) as e:
            # a half-written or older file starts clean rather than crashing
            log.warning("alert state ignored: %s", e)
            self._seen, self._log, self._primed = {}, [], False

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            # trimmed to the newest ids: an id can only matter while the thing
            # it names is still inside the 25 newest filings or headlines
            seen = list(self._seen)[-MAX_SEEN:]
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "seen": seen,
                "primed": self._primed,
                "last_action": {"|".join(k): v
                                for k, v in self._last_action.items()},
                "last_signal_bar": {"|".join(k): v
                                    for k, v in self._last_signal_bar.items()},
                "spiked_bar": {"|".join(k): v
                               for k, v in self._spiked_bar.items()},
                "log": [a.to_json() for a in self._log[-MAX_LOG:]],
            }))
            os.replace(tmp, self._state_path)
        except (OSError, ValueError, TypeError) as e:
            # losing persistence costs a swallowed batch after the next
            # restart, which is bad, but not as bad as failing the refresh
            log.warning("could not persist alert state: %s", e)

    # -- probes --------------------------------------------------------
    def _signal(self) -> List[Alert]:
        """Fires when a recommendation CHANGES into an actionable side."""
        out: List[Alert] = []
        for sym, iv in self.pairs():
            try:
                out.extend(self._signal_for(sym, iv))
            except Exception as e:
                log.warning("signal %s %s: %s", sym, iv, e)
        return out

    def _signal_for(self, sym: str, iv: str) -> List[Alert]:
        key = (sym, iv)
        d = self.svc.dashboard(symbol=sym, interval=iv)
        rec = d["recommendation"]
        action, bar = rec["action"], d["last_closed_bar"]
        prev = self._last_action.get(key)
        self._last_action[key] = action

        out: List[Alert] = []

        # NO SPIKE ALERT. See the module docstring: a 2% move inside a bar is
        # a state of the market, not a change in what to do about it, and it
        # fired many times for every call that actually changed.

        # EVERY transition among the three, including back to FLAT.
        #
        # The old rule was `action not in ("BUY","SELL"): return` — so being
        # told to close a position never notified, only being told to open
        # one. An exit is a decision too, and the one you most want to hear
        # about while you are holding something.
        # A transition is worth a buzz when it is a DECISION: a call
        # appearing or flipping (anything -> BUY/SELL), or a call being
        # withdrawn (BUY/SELL -> FLAT, the exit). It is NOT one when the
        # dashboard merely came back to life: STALE -> FLAT, or the first
        # reading after a restart, says nothing about the market and used to
        # arrive as "STALE -> FLAT" on every coin after every deploy.
        calls = ("BUY", "SELL")
        is_entry = action in calls and action != prev
        is_exit = action == "FLAT" and prev in calls
        if prev is None or not (is_entry or is_exit):
            return out
        if self._last_signal_bar.get(key) == bar:
            return out                              # one signal per bar, max
        self._last_signal_bar[key] = bar

        ev = rec.get("ev")
        size = rec.get("size_pct")
        closing = action == "FLAT"
        ctx = self._recent_context()

        # FOUR LINES, AT MOST. The old body carried `detail`, EV, position
        # size and the headlines themselves; on a lock screen that is three
        # lines of prose before the two numbers you actually act on.
        #
        # News is reported as a FLAG, not as text. Still never "because of":
        # news is not an input to the model, so the wording is "could
        # affect" — a thing to go and check, not an explanation of the call.
        levels = d.get("levels") or {}
        lines = []
        strength = str(rec.get("strength") or "").upper()
        if strength:
            lines.append(strength)
        if not closing:
            # Omitted entirely on an exit: closing a position has no target
            # and no stop, and printing the model's barriers next to the word
            # FLAT would read as a new trade.
            tp, sl = levels.get("take_profit"), levels.get("stop_loss")
            tp_pct = levels.get("tp_offset_pct")
            if tp is not None:
                lines.append(f"Take profit {_price_text(tp)}" + (
                    f"; {tp_pct:+.2f}%" if tp_pct is not None else ""))
            if sl is not None:
                lines.append(f"Stop loss {_price_text(sl)}")
        lines.append("News that could affect: " + ("yes" if ctx else "no"))
        body = "\n".join(lines)

        out.append(Alert(
            id=_hash("signal", d["symbol"], iv, bar, action),
            kind="signal",
            # Opening a position is worth a heads-up banner. Closing one is
            # worth telling you, but it is not an emergency.
            severity="medium" if closing else "high",
            symbol=d["symbol"], interval=iv,
            # so the phone can apply the same sensitivity gate to the
            # notification that it applies to the dashboard card
            strength=str(rec.get("strength") or ""),
            title=(f"{d['symbol']}: {iv}; {prev} \u2192 {action}"),
            body=body,
            at=utc_now(), detected_at=utc_now(),
            extra={"bar": bar, "window_ends": str(rec.get("window_ends", "")),
                   "from": str(prev), "to": action,
                   "context": " · ".join(c["text"] for c in ctx)}))
        return out

    def _recent_context(self) -> List[dict]:
        """Headlines and filings from the last few hours, newest first."""
        cutoff = utc_now() - CONTEXT_WINDOW
        with self._lock:
            fresh = [c for c in self._context if c["at"] >= cutoff]
            self._context = fresh[-60:]          # bounded, see __init__
        return list(reversed(fresh))[:MAX_CONTEXT]

    def _whales(self) -> List[Alert]:
        """New filings, collected as CONTEXT rather than sent.

        Returns nothing. A filing on its own no longer interrupts anyone; it
        is held and attached to the next action change. Mechanical filings are
        skipped entirely — tax withholding on vesting is not a view, so it is
        not even context.
        """
        for w in self.svc.whales(limit=25):
            if w.get("mechanical"):
                continue
            wid = _hash("whale", w["describe"], w["published_at"])
            if wid in self._seen:
                continue
            self._seen[wid] = None
            at = _parse(w.get("event_time")) or _parse(w["published_at"])
            with self._lock:
                self._context.append({
                    "kind": "whale",
                    "text": str(w["describe"])[:90],
                    "at": at or utc_now(),
                })
        return []

    def _news(self) -> List[Alert]:
        """Headlines, collected as CONTEXT rather than sent.

        Returns nothing, and that is the whole change: seventy headlines a day
        was seventy interruptions for something that is not a decision. The
        News tab still carries every one of them — `/api/news` is untouched.
        Only what buzzes your phone is different.
        """
        for n in self.svc.news(limit=25):
            nid = _hash("news", n["headline"], n.get("published_at"))
            if nid in self._seen:
                continue
            self._seen[nid] = None
            bias = str(n.get("bias") or "")
            impact = str(n.get("impact") or "")
            reading = " ".join(x for x in (bias, impact)
                               if x and x != "NO READING")
            with self._lock:
                self._context.append({
                    "kind": "news",
                    "text": (f"{n['headline'][:80]}"
                             + (f" [{reading}]" if reading else "")),
                    "at": _parse(n.get("published_at")) or utc_now(),
                })
        return []

    def _calendar(self) -> List[Alert]:
        """Warns BEFORE a scheduled event, once per lead window."""
        from newsfeed.schedule import upcoming

        now = utc_now()
        out = []
        # 3 days, not 2: the top-priority events warn a full day ahead, and a
        # 2-day horizon left no margin for a clock that wakes up late.
        for e in upcoming(within_days=3, now=now):
            mins = e.minutes_until(now)
            leads = e.lead_minutes
            for lead in leads:
                # fire when we are inside the window but have not passed the
                # event; the id pins it to this lead so it cannot repeat
                if not (0 < mins <= lead):
                    continue
                cid = _hash("cal", e.key, lead)
                if cid in self._seen:
                    continue
                # "in 1440 min" is unreadable. Say it the way a person would.
                if mins >= 90:
                    when = f"in {mins / 60:.0f}h"
                else:
                    when = f"in {int(round(mins))} min"
                out.append(Alert(
                    id=cid, kind="calendar",
                    severity="high" if e.impact == "high" else "medium",
                    title=f"{e.title} {when}",
                    body=(e.note or "Scheduled event.")
                         + " Known to the whole market — expect volatility, "
                           "not direction."
                         + (" Date is estimated; confirm it."
                            if e.estimated else ""),
                    at=e.at, detected_at=now, url=e.url,
                    extra={"impact": e.impact, "lead_minutes": str(lead),
                           "priority": str(e.priority),
                           "estimated": str(e.estimated).lower()}))

                # Every OTHER lead window that also applies right now is moot:
                # the user has just been told. Without this, an event first
                # seen inside the tightest window fires once per lead a few
                # seconds apart, with near-identical text — which reads as the
                # app malfunctioning rather than as two deliberate warnings.
                # NB: skip `lead` itself. `refresh()` filters `fresh`
                # against `_seen` AFTER this runs, so marking the lead being
                # fired would delete the very alert just created — the
                # notification would never be sent and nothing would say why.
                for other in leads:
                    if other != lead and 0 < mins <= other:
                        self._seen[_hash("cal", e.key, other)] = None
                break
        return out


def _alert_from_json(d: dict) -> Alert:
    """Rebuild a persisted alert.

    `seq` is a property derived from `detected_at`, so it is dropped rather
    than passed to the constructor — writing it back would be inventing a
    second source of truth for the cursor everything pages on.
    """
    d = dict(d)
    d.pop("seq", None)
    d["at"] = _parse(d.get("at")) or utc_now()
    d["detected_at"] = _parse(d.get("detected_at")) or utc_now()
    known = {f for f in Alert.__dataclass_fields__}
    return Alert(**{k: v for k, v in d.items() if k in known})


def _parse(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None
