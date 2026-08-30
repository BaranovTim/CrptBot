"""Rate limiting for the endpoints that guess-ability makes dangerous.

WHY THIS APPEARS NOW
    On Tailscale the only callers were devices already on a private network,
    and a login endpoint reachable by nobody needs no brake. Putting the API
    on the public internet changes that in one specific way: `/api/auth/login`
    becomes a place where anyone in the world can try passwords, as fast as
    the box will answer, forever.

    scrypt makes each guess cost ~60ms, which is real but not enough on its
    own — that is still ~1.4 million guesses a day from a single client.

WHAT IS LIMITED, AND WHAT IS NOT
    Only the auth routes. Rate-limiting the read endpoints would throttle the
    app's own 20-second alert poll and the dashboard refresh, punishing normal
    use to defend against something that is not a threat: the read endpoints
    are already gated by tier, and a subscriber hammering their own analysis
    costs one CPU, not an account.

TWO WINDOWS, NOT ONE
    Per-IP, so one attacker cannot grind through a dictionary.
    Per-identifier, so a botnet spread across many addresses cannot grind
    through one account either. The second is the one that actually protects a
    specific user, and it is the one a naive per-IP-only limiter misses.

FAILING OPEN, DELIBERATELY
    If the table is somehow unusable the request proceeds. A brake that
    bricks sign-in when it breaks is worse than the attack it prevents, and
    the account itself is still behind a password.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

# Attempts allowed per window. Ten failures in five minutes is far more than a
# person mistyping a password and far less than anything automated.
IP_LIMIT, IP_WINDOW = 10, 300.0
ID_LIMIT, ID_WINDOW = 8, 300.0

# Stop the table growing without bound from a spray of distinct addresses.
MAX_KEYS = 10_000


class Throttle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def _check(self, key: str, limit: int, window: float) -> Optional[float]:
        """None if allowed, else seconds until the next attempt is allowed."""
        now = time.time()
        with self._lock:
            if len(self._hits) > MAX_KEYS:
                # cheapest correct answer: drop everything and start again.
                # It briefly forgives attackers mid-window, which is a far
                # smaller problem than unbounded memory on a 1GB box.
                self._hits.clear()
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return max(0.0, window - (now - q[0]))
            q.append(now)
            return None

    def check(self, ip: str, identifier: str = "") -> Optional[float]:
        try:
            wait = self._check(f"ip:{ip}", IP_LIMIT, IP_WINDOW)
            if wait is not None:
                return wait
            if identifier:
                return self._check(f"id:{identifier.strip().lower()}",
                                   ID_LIMIT, ID_WINDOW)
            return None
        except Exception:
            return None                     # fail open; see the module note

    def forget(self, ip: str, identifier: str = "") -> None:
        """Clear the counters after a SUCCESSFUL sign-in.

        Without this, somebody who mistypes their password a few times and
        then gets it right still carries the failures, and a shared office
        address could lock out a person who has done nothing wrong.
        """
        with self._lock:
            self._hits.pop(f"ip:{ip}", None)
            if identifier:
                self._hits.pop(f"id:{identifier.strip().lower()}", None)


_throttle = Throttle()


def get_throttle() -> Throttle:
    return _throttle


def client_ip(handler) -> str:
    """The caller's address, honouring a proxy only when one is in front.

    Behind Caddy every request arrives from 127.0.0.1, so limiting on the
    socket address alone would put the entire internet in one bucket and lock
    everybody out together. `X-Forwarded-For` carries the real client.

    It is trusted ONLY when the connection is from loopback. From anywhere
    else the header is attacker-controlled, and honouring it would let one
    client mint a fresh identity per request and bypass the limit entirely.
    """
    peer = handler.client_address[0] if handler.client_address else "?"
    if peer in ("127.0.0.1", "::1"):
        fwd = handler.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


def retry_payload(wait: float) -> Tuple[dict, int]:
    return ({"error": "too many attempts",
             "hint": f"wait {int(wait) + 1}s and try again",
             "retry_after": int(wait) + 1}, 429)
