"""What is worth waking a phone for.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
A notification is an interruption, and the fastest way to make one worthless
is to send it too often. So every alert here is a TRANSITION or a genuinely
new object, never a state. "The recommendation is FLAT" is a state and is
never sent. "The recommendation just became BUY" is a transition and is sent
once. The engine holds the previous value for exactly this reason.

The same rule kills the obvious duplicates: a whale filing is alerted on its
filing id, a calendar event on its key plus which lead window fired, an
intra-bar spike on the bar it happened in. Re-polling never re-sends.

EVERY ALERT CARRIES WHEN IT HAPPENED
------------------------------------
Not when it was noticed. These are different, sometimes by days: an SEC Form 4
discloses a trade that happened up to five days earlier, and a notification
that says only "just now" would imply a freshness the data does not have. So
alerts carry `at` (when the thing happened) alongside `detected_at`, and for
filings both the trade date and the disclosure date.

NOTHING HERE FEEDS A MODEL
--------------------------
Alerts are a human channel. They are derived from the same payload the
dashboard shows and never flow back into features — a scheduled FOMC date is
known to the entire market and is priced, so treating it as signal would be
modelling the calendar rather than the market.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
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


class AlertEngine:
    """Turns the dashboard payload and the feeds into a stream of transitions."""

    def __init__(self, service, pairs=None):
        self.svc = service
        self._log: List[Alert] = []
        self._seen: set = set()
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
            return [(sym, iv) for iv in ivs
                    for sym in self.svc.trained_symbols()]
        except Exception:
            return [(self.svc.symbol, self.svc.interval)]

    # -- public --------------------------------------------------------
    def refresh(self) -> List[Alert]:
        """Look once. Returns only what is new."""
        fresh: List[Alert] = []
        for probe in (self._signal, self._whales, self._news, self._calendar):
            try:
                fresh.extend(probe())
            except Exception as e:                 # one dead feed must not
                log.warning("%s failed: %s", probe.__name__, e)  # kill the rest

        new = [a for a in fresh if a.id not in self._seen]
        for a in new:
            self._seen.add(a.id)
        self._log.extend(new)
        if len(self._log) > MAX_LOG:
            self._log = self._log[-MAX_LOG:]

        # The first poll after a restart would otherwise replay history as if
        # it just happened. Record it, return nothing.
        if not self._primed:
            self._primed = True
            return []
        return new

    def after(self, cursor: Optional[int]) -> List[Alert]:
        """New alerts after an epoch-millisecond cursor.

        No cursor deliberately returns NOTHING. Returning the backlog there
        would make every app launch fire twenty notifications about filings
        from last week — the exact behaviour that teaches someone to swipe an
        app's alerts away without reading them. The caller stores `cursor`
        from the response and passes it back.

        An UNPARSEABLE cursor also returns nothing, not everything. Failing
        open here is what turns one bad request into a notification storm.
        """
        self.refresh()
        if cursor is None:
            return []
        return [a for a in self._log if a.seq > cursor]

    def cursor(self) -> int:
        return int(utc_now().timestamp() * 1000)

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

        # the intra-bar spike, reported once per bar
        live = d.get("live") or {}
        if live.get("beyond_spike_threshold") and self._spiked_bar.get(key) != bar:
            self._spiked_bar[key] = bar
            move = live.get("move_pct")
            atr = live.get("move_atr")
            way = "UP" if (atr or 0) > 0 else "DOWN"
            out.append(Alert(
                id=_hash("spike", d["symbol"], iv, bar),
                kind="spike", severity="high", symbol=d["symbol"],
                interval=iv,
                title=f"{d['symbol']} {iv} spiking {way}",
                body=(f"{move:+.2f}% ({atr:+.2f} ATR) inside the current bar. "
                      f"The windows below were read at the last close."),
                at=utc_now(), detected_at=utc_now()))

        if action == prev or action not in ("BUY", "SELL"):
            return out
        if self._last_signal_bar.get(key) == bar and prev is not None:
            return out                              # one signal per bar, max
        self._last_signal_bar[key] = bar

        ev = rec.get("ev")
        size = rec.get("size_pct")
        out.append(Alert(
            id=_hash("signal", d["symbol"], iv, bar, action),
            kind="signal", severity="high", symbol=d["symbol"],
            interval=iv,
            title=f"{d['symbol']} {iv}: {action}",
            body=(f"{rec.get('detail', '')}"
                  + (f"  EV {ev:+.3f}%" if ev is not None else "")
                  + (f", size {size:.2f}% of equity" if size else "")),
            at=utc_now(), detected_at=utc_now(),
            extra={"bar": bar, "window_ends": str(rec.get("window_ends", ""))}))
        return out

    def _whales(self) -> List[Alert]:
        """New filings. Mechanical ones never alert — they are not a view."""
        out = []
        for w in self.svc.whales(limit=25):
            if w.get("mechanical"):
                continue
            wid = _hash("whale", w["describe"], w["published_at"])
            if wid in self._seen:
                continue
            at = _parse(w.get("event_time")) or _parse(w["published_at"])
            out.append(Alert(
                id=wid, kind="whale",
                severity="high" if (w.get("conviction") or 0) >= 1.0 else "medium",
                title="Whale activity",
                body=f"{w['describe']} — {w['impact']}. {w['note']}.",
                at=at or utc_now(), detected_at=utc_now(),
                extra={"disclosed_at": w["published_at"],
                       "code": w.get("code", ""),
                       "impact": w["impact"]}))
        return out

    def _news(self) -> List[Alert]:
        out = []
        for n in self.svc.news(limit=25):
            nid = _hash("news", n["headline"], n.get("published_at"))
            if nid in self._seen:
                continue
            out.append(Alert(
                id=nid, kind="news", severity="medium",
                title="News released",
                body=f"{n['headline']} ({n.get('source', '')})",
                at=_parse(n.get("published_at")) or utc_now(),
                detected_at=utc_now(), url=n.get("url", "") or ""))
        return out

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
                        self._seen.add(_hash("cal", e.key, other))
                break
        return out


def _parse(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None
