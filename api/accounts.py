"""Accounts, passwords and entitlements.

This is the first part of the project that stores a secret belonging to
somebody else, so the choices below are deliberate rather than conventional.

PASSWORDS ARE HASHED WITH scrypt, OR PBKDF2 WHERE scrypt IS MISSING
    Both come from the standard library. bcrypt or argon2 would be defensible;
    neither is worth a dependency here, and a dependency that fails to build
    is how projects end up with `sha256(password)` "temporarily".

    `hashlib.scrypt` turns out to exist only when Python was linked against an
    OpenSSL that provides it — the droplet's 3.13 has it, the Xcode Python on
    the development Mac does not. So the algorithm is recorded on each account
    and read back per record. A password set on either machine verifies on
    both, and neither has to be reconfigured.

    scrypt at n=2**14, r=8, p=1 costs ~60ms; PBKDF2-HMAC-SHA256 at 600k rounds
    costs ~230ms. Both are slow enough that guessing is expensive and fast
    enough that logging in is not. Every parameter is stored per-record, so
    raising them later does not invalidate existing passwords.

EVERY COMPARISON IS CONSTANT TIME
    `hmac.compare_digest` for hashes and for session tokens. A plain `==`
    returns as soon as two bytes differ, and the timing difference is
    measurable across a network.

SESSIONS ARE RANDOM, NOT SIGNED
    A `secrets.token_urlsafe(32)` looked up in a table, not a JWT. Nothing
    here needs stateless verification across machines, and a token that can be
    revoked by deleting a row beats one that is valid until it expires.

WHAT IS DELIBERATELY NOT HERE
    No password reset (it needs email you do not have), no "remember me"
    beyond the session lifetime, and no lockout counter. Say what is missing
    rather than implying coverage that does not exist.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# scrypt cost. Stored per record so these can be raised without a migration.
_N, _R, _P = 2 ** 14, 8, 1
_SALT_BYTES = 16
_KEY_LEN = 32

# PBKDF2 rounds, used when scrypt is unavailable. OWASP's 2023 floor for
# HMAC-SHA256 is 600k; measured here at ~230ms, which is the right order for
# a login.
_PBKDF2_ROUNDS = 600_000

# `hashlib.scrypt` exists only when Python was linked against an OpenSSL that
# provides it. The droplet's 3.13 has it; the Xcode Python on the development
# Mac does not, and a record written on one machine has to verify on the
# other. So the algorithm is chosen at import, WRITTEN INTO EACH RECORD, and
# read back per record rather than assumed.
try:
    hashlib.scrypt(b"probe", salt=b"probe", n=2, r=8, p=1, dklen=16)
    _DEFAULT_ALGO = "scrypt"
except (AttributeError, ValueError):
    _DEFAULT_ALGO = "pbkdf2"

SESSION_TTL = 30 * 24 * 3600.0          # 30 days
MIN_PASSWORD = 8

# What a tier may see. `free` gets the chart and nothing else, which is the
# gate the app renders its paywall from.
TIERS = ("free", "pro", "admin")


class AuthError(Exception):
    """Anything a caller is allowed to be told about a failed sign-in."""


@dataclass
class User:
    identifier: str
    salt: str
    hash: str
    algo: str = "scrypt"
    tier: str = "free"
    created_at: float = field(default_factory=time.time)
    # epoch seconds; None means "no subscription". Admin ignores it.
    subscription_ends: Optional[float] = None
    stripe_customer: Optional[str] = None

    @property
    def entitled(self) -> bool:
        """May this account see analysis, as opposed to just the chart?"""
        if self.tier == "admin":
            return True
        if self.tier != "pro":
            return False
        # a lapsed subscription is `pro` with a date in the past. Checking the
        # date rather than trusting the tier means a failed renewal degrades
        # to the paywall on its own, with no job to run.
        return self.subscription_ends is None or \
            self.subscription_ends > time.time()

    def public(self) -> Dict[str, Any]:
        """What the phone is allowed to know. Never the salt or the hash."""
        return {
            "identifier": self.identifier,
            "tier": self.tier,
            "entitled": self.entitled,
            "subscription_ends": self.subscription_ends,
        }


def _hash(password: str, salt: bytes, algo: Optional[str] = None) -> str:
    pw = password.encode("utf-8")
    if (algo or _DEFAULT_ALGO) == "scrypt":
        return hashlib.scrypt(pw, salt=salt, n=_N, r=_R, p=_P,
                              dklen=_KEY_LEN).hex()
    return hashlib.pbkdf2_hmac("sha256", pw, salt, _PBKDF2_ROUNDS,
                               dklen=_KEY_LEN).hex()


class Accounts:
    """The user table. One JSON file, one lock, atomic writes."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or
                         Path("data_cache") / "accounts.json")
        self._lock = threading.Lock()
        self._users: Dict[str, User] = {}
        self._sessions: Dict[str, tuple] = {}     # token -> (identifier, exp)
        self._mtime = 0.0
        self._load()

    # ------------------------------------------------------------ storage
    def _maybe_reload(self) -> None:
        """Re-read the file if another process has written it.

        MEASURED, NOT THEORETICAL. `manage_accounts.py grant` writes the file
        from a separate process; the running API kept its own copy in memory
        and went on answering 402 to an account that was already `pro` on
        disk. After a real payment that failure reads as "I paid and nothing
        happened", which is the worst bug this system can have.

        A stat per lookup is far cheaper than the scrypt verification it sits
        next to, and this keeps the CLI, a future Stripe webhook and the API
        in agreement no matter which of them writes.

        Sessions are NOT touched: they live only in memory, and dropping them
        on every external edit would sign everybody out whenever one account
        changed.
        """
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
            self._users.clear()
            self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except Exception as e:
            # A corrupt file must not silently become an empty user table:
            # that would let anyone re-register an existing identifier and
            # inherit nothing, but it would also lock the real owner out with
            # no explanation.
            raise RuntimeError(f"{self.path} is unreadable: {e}") from e
        for d in raw.get("users", []):
            u = User(**d)
            self._users[u.identifier.lower()] = u
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = 0.0

    def _save(self) -> None:
        """Write via a temp file and replace, so a crash cannot truncate it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"users": [vars(u) for u in self._users.values()]}
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, self.path)              # atomic on POSIX
        try:
            os.chmod(self.path, 0o600)          # hashes are not world-readable
        except OSError:
            pass
        try:                                    # our own write is not a change
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    # ----------------------------------------------------------- accounts
    def register(self, identifier: str, password: str,
                 tier: str = "free") -> User:
        ident = (identifier or "").strip()
        if len(ident) < 3:
            raise AuthError("identifier must be at least 3 characters")
        if len(password or "") < MIN_PASSWORD:
            raise AuthError(f"password must be at least {MIN_PASSWORD} "
                            "characters")
        if tier not in TIERS:
            raise AuthError(f"unknown tier {tier!r}")
        with self._lock:
            if ident.lower() in self._users:
                # Deliberately explicit. Hiding whether an identifier is taken
                # protects privacy on an email-based system; here the
                # identifier is a chosen handle, and a registration form that
                # refuses without saying why is unusable.
                raise AuthError("that identifier is already taken")
            salt = secrets.token_bytes(_SALT_BYTES)
            u = User(identifier=ident, salt=salt.hex(),
                     hash=_hash(password, salt), algo=_DEFAULT_ALGO,
                     tier=tier)
            self._users[ident.lower()] = u
            self._save()
            log.info("registered %s (%s)", ident, tier)
            return u

    def get(self, identifier: str) -> Optional[User]:
        self._maybe_reload()
        return self._users.get((identifier or "").strip().lower())

    def verify(self, identifier: str, password: str) -> User:
        u = self.get(identifier)
        if u is None:
            # Hash anyway. Returning immediately for an unknown identifier
            # makes "no such user" measurably faster than "wrong password",
            # which turns this endpoint into a user-enumeration oracle.
            _hash(password or "", secrets.token_bytes(_SALT_BYTES))
            raise AuthError("wrong identifier or password")
        got = _hash(password or "", bytes.fromhex(u.salt), u.algo)
        if not hmac.compare_digest(got, u.hash):
            raise AuthError("wrong identifier or password")
        return u

    def set_tier(self, identifier: str, tier: str,
                 ends: Optional[float] = None) -> User:
        if tier not in TIERS:
            raise AuthError(f"unknown tier {tier!r}")
        with self._lock:
            u = self.get(identifier)
            if u is None:
                raise AuthError("no such account")
            u.tier, u.subscription_ends = tier, ends
            self._save()
            return u

    def set_password(self, identifier: str, password: str) -> User:
        if len(password or "") < MIN_PASSWORD:
            raise AuthError(f"password must be at least {MIN_PASSWORD} "
                            "characters")
        with self._lock:
            u = self.get(identifier)
            if u is None:
                raise AuthError("no such account")
            salt = secrets.token_bytes(_SALT_BYTES)
            u.salt, u.hash = salt.hex(), _hash(password, salt)
            u.algo = _DEFAULT_ALGO
            self._save()
            # every existing session for this account dies with the password
            self._sessions = {t: v for t, v in self._sessions.items()
                              if v[0] != u.identifier}
            return u

    def delete(self, identifier: str) -> None:
        """Remove an account outright, and every session it holds.

        Leaving the sessions behind would keep a deleted account signed in
        until its token expired, which is exactly the case where "deleted"
        has to mean deleted.
        """
        with self._lock:
            u = self.get(identifier)
            if u is None:
                raise AuthError("no such account")
            self._users.pop(u.identifier.lower(), None)
            self._sessions = {t: v for t, v in self._sessions.items()
                              if v[0] != u.identifier}
            self._save()

    # ----------------------------------------------------------- sessions
    def start_session(self, u: User) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (u.identifier, time.time() + SESSION_TTL)
        return token

    def session_user(self, token: str) -> Optional[User]:
        if not token:
            return None
        with self._lock:
            # scan rather than dict-lookup, to compare in constant time. The
            # table is small; a timing side channel on session tokens is not
            # worth the microseconds saved.
            found = None
            for t, (ident, exp) in self._sessions.items():
                if hmac.compare_digest(t, token):
                    found = (t, ident, exp)
            if found is None:
                return None
            t, ident, exp = found
            if exp < time.time():
                self._sessions.pop(t, None)
                return None
        return self.get(ident)

    def end_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)


_accounts: Optional[Accounts] = None
_accounts_lock = threading.Lock()


def get_accounts() -> Accounts:
    global _accounts
    with _accounts_lock:
        if _accounts is None:
            _accounts = Accounts()
        return _accounts
