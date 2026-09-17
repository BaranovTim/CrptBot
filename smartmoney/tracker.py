"""Watching the followed addresses, and saying what changed.

AN EVENT IS A DIFFERENCE, NOT A STATE
    "0xe867… is long BTC" is a state and is never announced. "0xe867… just
    opened a BTC long" is a difference between two polls and is announced
    once. The same rule the alert engine applies to the model's calls.

    opened    a coin appears in the book
    closed    a coin leaves it
    flipped   the side changes
    added     size grows by half or more (a conviction add, not a top-up)
    reduced   size shrinks by half or more without closing

    The first poll after a selection PRIMES: it records the books and
    announces nothing, because "here are 25 positions that already exist"
    is the notification storm the app must never send.

WHAT IS RECORDED WITH EACH EVENT
    The price of the coin on our own 1m store at the moment we saw it,
    when available. That is the follower's entry, not the trader's, and it
    is what `research/` needs to answer whether following pays. Without it
    this feed could never be scored, and an unscored feed is a rumour.

THE THREAD
    One daemon thread: reselects from the leaderboard once a day (the
    board is refreshed at most every 6 hours, and reading 150 candidates'
    fills is 150 calls), polls the followed books every minute, persists
    after every change. All venue calls degrade to "no change" on failure;
    the loop never dies on a bad minute.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core import utc_now
from marketdata.binance import DEFAULT_CACHE
from marketdata.hyperliquid import (clearinghouse_state, positions_from_state,
                                    short_address, symbol_for)

from .select import TraderStats, refresh_leaderboard, select_traders

log = logging.getLogger(__name__)

STATE_PATH = Path("data_cache") / "smartmoney.v1.json"
POLL_SECONDS = 60.0
RESELECT_SECONDS = 24 * 3600.0
MAX_EVENTS = 1000
SIZE_STEP = 0.5             # +50% is an add, -50% a reduce
NOTIFY_KINDS = ("opened", "closed", "flipped")


@dataclass
class Event:
    id: str
    kind: str                   # opened | closed | flipped | added | reduced
    address: str
    coin: str
    symbol: str                 # Binance symbol, "" when the coin is not one
    side: str                   # LONG | SHORT (the side after the change)
    size: float
    notional: float             # USD, after the change (before it, for closed)
    entry: Optional[float]
    leverage: Optional[float]
    at: str                     # ISO, when we saw it
    price_at: Optional[float]   # our 1m close at detection, for scoring
    trader: dict = field(default_factory=dict)   # the stats that got them followed

    def to_json(self) -> dict:
        return asdict(self)

    @property
    def seq(self) -> int:
        try:
            return int(datetime.fromisoformat(self.at).timestamp() * 1000)
        except ValueError:
            return 0


def diff_books(old: Dict[str, dict], new: Dict[str, dict]) -> List[dict]:
    """The differences between two books as (kind, coin, position) rows.
    Pure; the tracker wraps them into Events."""
    out = []
    for coin, pos in new.items():
        prev = old.get(coin)
        if prev is None:
            out.append({"kind": "opened", "coin": coin, **pos})
        elif prev.get("side") != pos.get("side"):
            out.append({"kind": "flipped", "coin": coin, **pos})
        else:
            a, b = float(prev.get("size") or 0), float(pos.get("size") or 0)
            if a > 0 and b >= a * (1 + SIZE_STEP):
                out.append({"kind": "added", "coin": coin, **pos})
            elif a > 0 and b <= a * (1 - SIZE_STEP):
                out.append({"kind": "reduced", "coin": coin, **pos})
    for coin, prev in old.items():
        if coin not in new:
            out.append({"kind": "closed", "coin": coin, **prev})
    return out


class Tracker:
    def __init__(self, state_path: Optional[Path] = None,
                 cache_dir: Path = DEFAULT_CACHE,
                 state_fn: Callable[[str], Optional[dict]] = clearinghouse_state,
                 select_fn=None, price_fn: Optional[Callable[[str], Optional[float]]] = None):
        self._state_path = Path(state_path) if state_path else STATE_PATH
        self._cache_dir = Path(cache_dir)
        self._state_fn = state_fn
        self._select_fn = select_fn or self._default_select
        self._price_fn = price_fn
        self.traders: List[TraderStats] = []
        self.books: Dict[str, Dict[str, dict]] = {}       # address -> coin -> position
        self.events: List[Event] = []
        self.selected_at: Optional[str] = None
        self.polled_at: Optional[str] = None
        self.last_error: str = ""
        self._primed_for: set = set()                     # addresses seen once
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._load()

    # ----------------------------------------------------------- selection
    def _default_select(self) -> List[TraderStats]:
        path = refresh_leaderboard(self._cache_dir)
        if path is None:
            return []
        return select_traders(path, pause=lambda: time.sleep(0.25))

    def reselect(self) -> int:
        """Rebuild the followed set. Keeps the books of anyone who stays,
        so a reselection does not re-announce their existing positions."""
        try:
            chosen = self._select_fn()
        except Exception as e:
            self.last_error = f"select: {e}"
            log.warning("smartmoney: selection failed: %s", e)
            return len(self.traders)
        if not chosen:
            # a venue that answered nothing keeps yesterday's list rather
            # than emptying the feed
            return len(self.traders)
        with self._lock:
            keep = {t.address for t in chosen}
            self.traders = chosen
            self.books = {a: b for a, b in self.books.items() if a in keep}
            self._primed_for &= keep
            self.selected_at = utc_now().isoformat()
            self._save()
        log.info("smartmoney: following %d traders", len(chosen))
        return len(chosen)

    # --------------------------------------------------------------- polling
    def poll(self, now: Optional[datetime] = None) -> List[Event]:
        """Read every followed book once; return the new events."""
        now = now or utc_now()
        fresh: List[Event] = []
        with self._lock:
            traders = list(self.traders)
        for t in traders:
            state = None
            try:
                state = self._state_fn(t.address)
            except Exception as e:
                log.warning("smartmoney: state for %s failed: %s", t.address, e)
            if state is None:
                continue                          # unchanged until we can read it
            new = positions_from_state(state)
            with self._lock:
                old = self.books.get(t.address, {})
                primed = t.address in self._primed_for
                self.books[t.address] = new
                self._primed_for.add(t.address)
            if not primed:
                continue
            for d in diff_books(old, new):
                fresh.append(self._event(t, d, now))
        with self._lock:
            self.polled_at = now.isoformat()
            if fresh:
                self.events.extend(fresh)
                self.events = self.events[-MAX_EVENTS:]
            self._save()
        return fresh

    def _event(self, t: TraderStats, d: dict, now: datetime) -> Event:
        symbol = symbol_for(d["coin"]) or ""
        price = None
        if symbol and self._price_fn is not None:
            try:
                price = self._price_fn(symbol)
            except Exception:
                price = None
        ident = f"{t.address}|{d['coin']}|{d['kind']}|{int(now.timestamp())}"
        return Event(
            id=ident, kind=d["kind"], address=t.address, coin=d["coin"],
            symbol=symbol, side=str(d.get("side") or ""),
            size=float(d.get("size") or 0), notional=float(d.get("notional") or 0),
            entry=d.get("entry"), leverage=d.get("leverage"),
            at=now.isoformat(), price_at=price,
            trader={"win_rate": t.win_rate, "pnl_30d": t.pnl_30d,
                    "closed_trades": t.closed_trades, "score": t.score,
                    "account_value": t.account_value,
                    "display_name": t.display_name},
        )

    # ---------------------------------------------------------------- reads
    def consensus(self, symbol: str) -> dict:
        """Who among the followed holds this coin, and which way."""
        sym = (symbol or "").upper()
        rows = []
        with self._lock:
            by_addr = {t.address: t for t in self.traders}
            for addr, book in self.books.items():
                for coin, pos in book.items():
                    if symbol_for(coin) == sym:
                        t = by_addr.get(addr)
                        rows.append({
                            "address": addr, "short": short_address(addr),
                            "name": t.display_name if t else "",
                            "side": pos["side"], "notional": pos["notional"],
                            "entry": pos["entry"], "leverage": pos["leverage"],
                            "upnl": pos.get("upnl"),
                            "win_rate": t.win_rate if t else None,
                            "pnl_30d": t.pnl_30d if t else None,
                        })
            tracked = len(self.traders)
        rows.sort(key=lambda r: -(r["notional"] or 0))
        longs = [r for r in rows if r["side"] == "LONG"]
        shorts = [r for r in rows if r["side"] == "SHORT"]
        return {
            "symbol": sym, "tracked": tracked,
            "long": len(longs), "short": len(shorts),
            "long_notional": sum(r["notional"] or 0 for r in longs),
            "short_notional": sum(r["notional"] or 0 for r in shorts),
            "holders": rows,
        }

    def recent(self, symbol: Optional[str] = None, limit: int = 20) -> List[dict]:
        sym = (symbol or "").upper()
        with self._lock:
            evs = [e for e in reversed(self.events) if not sym or e.symbol == sym]
        return [e.to_json() for e in evs[:limit]]

    def after(self, cursor_ms: int) -> List[Event]:
        with self._lock:
            return [e for e in self.events if e.seq > cursor_ms]

    def status(self) -> dict:
        with self._lock:
            return {
                "tracked": len(self.traders),
                "selected_at": self.selected_at,
                "polled_at": self.polled_at,
                "events": len(self.events),
                "last_error": self.last_error,
                "traders": [{
                    "address": t.address, "short": short_address(t.address),
                    "name": t.display_name, "score": t.score,
                    "win_rate": t.win_rate, "closed_trades": t.closed_trades,
                    "profit_factor": t.profit_factor, "pnl_30d": t.pnl_30d,
                    "roi_30d": t.roi_30d, "account_value": t.account_value,
                    "weeks_positive": t.weeks_positive,
                    "weeks_covered": t.weeks_covered, "coins": t.coins,
                    "open": sorted(self.books.get(t.address, {}).keys()),
                } for t in self.traders],
            }

    # ---------------------------------------------------------------- loop
    def start(self, poll_seconds: float = POLL_SECONDS,
              reselect_seconds: float = RESELECT_SECONDS) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        def loop() -> None:
            next_select = 0.0
            if self.selected_at:
                try:
                    next_select = (datetime.fromisoformat(self.selected_at).timestamp()
                                   + reselect_seconds)
                except ValueError:
                    next_select = 0.0
            while not self._stop.is_set():
                try:
                    if time.time() >= next_select or not self.traders:
                        self.reselect()
                        next_select = time.time() + (reselect_seconds if self.traders
                                                     else 900.0)
                    new = self.poll()
                    if new:
                        log.info("smartmoney: %d event(s): %s", len(new),
                                 ", ".join(f"{e.kind} {e.coin}" for e in new[:6]))
                except Exception as e:
                    self.last_error = str(e)
                    log.warning("smartmoney loop: %s", e)
                self._stop.wait(poll_seconds)

        self._thread = threading.Thread(target=loop, daemon=True, name="smartmoney")
        self._thread.start()
        log.info("smartmoney tracker started")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ persistence
    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "selected_at": self.selected_at,
                "polled_at": self.polled_at,
                "traders": [t.to_json() for t in self.traders],
                "books": self.books,
                "primed": sorted(self._primed_for),
                "events": [e.to_json() for e in self.events[-MAX_EVENTS:]],
            }))
            os.replace(tmp, self._state_path)
        except (OSError, ValueError, TypeError) as e:
            log.warning("smartmoney: could not persist: %s", e)

    def _load(self) -> None:
        try:
            raw = json.loads(self._state_path.read_text())
        except (OSError, ValueError):
            return
        try:
            self.selected_at = raw.get("selected_at")
            self.polled_at = raw.get("polled_at")
            self.traders = [TraderStats.from_json(t) for t in raw.get("traders", [])]
            self.books = {a: dict(b) for a, b in raw.get("books", {}).items()}
            self._primed_for = set(raw.get("primed", []))
            self.events = [Event(**e) for e in raw.get("events", [])]
            log.info("smartmoney: restored %d traders, %d events",
                     len(self.traders), len(self.events))
        except (KeyError, TypeError, ValueError) as e:
            log.warning("smartmoney state ignored: %s", e)
            self.traders, self.books, self.events = [], {}, []
            self._primed_for = set()


_TRACKER: Optional[Tracker] = None


def get_tracker(price_fn: Optional[Callable[[str], Optional[float]]] = None) -> Tracker:
    """The process-wide tracker, started on first use. `SMARTMONEY=0`
    disables it (no thread, empty answers) for hosts without the memory
    or the wish."""
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = Tracker(price_fn=price_fn)
        if os.environ.get("SMARTMONEY", "1") != "0":
            _TRACKER.start()
    return _TRACKER
