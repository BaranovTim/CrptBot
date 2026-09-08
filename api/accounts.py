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

SESSIONS ARE RANDOM, NOT SIGNED - AND STORED AS HASHES
    A `secrets.token_urlsafe(32)` looked up in a table, not a JWT. Nothing
    here needs stateless verification across machines, and a token that can be
    revoked by deleting a row beats one that is valid until it expires.

    The table is persisted, because it was not and that was wrong: sessions
    lived only in memory, so every `docker compose up -d` signed out every
    account. Harmless while the only user was the operator; on a subscription
    product it means each deploy makes every customer re-enter a password.

    Only the SHA-256 of each token is written. A session token is a bearer
    credential - whoever holds it is signed in - so a readable sessions file
    would be as good as a password file. Hashing costs one hash per request
    and makes the file worthless if copied.

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
import re
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

SESSION_TTL = 30 * 24 * 3600.0          # 30 days, from LAST USE

# How stale a session may get before using it extends it. Renewing on every
# request would mean a disk write per request; renewing only past the halfway
# point costs one write every ~15 days per device and makes the expiry
# effectively "30 days of not opening the app" rather than "30 days since you
# typed your password".
_RENEW_AFTER = SESSION_TTL / 2
MIN_PASSWORD = 8

# WHAT A PASSWORD HAS TO CLEAR, AND WHY IT IS NOT A CHARACTER-CLASS RULE.
#
# "One upper, one digit, one symbol" is the familiar rule and it is a bad one:
# it makes `Password1!` legal and `correct horse battery staple` illegal, when
# the second is enormously stronger. NIST dropped composition rules in 800-63B
# for exactly that reason and recommends a length floor plus a check against
# known-breached and obvious passwords, which is what this does.
#
# The list is short on purpose. A real breach corpus is millions of entries and
# belongs behind an API; this catches the handful that a person actually types
# when asked to invent something quickly.
_COMMON_PASSWORDS = frozenset("""
password password1 password123 12345678 123456789 1234567890 qwerty123
qwertyuiop letmein00 iloveyou1 admin123 welcome1 welcome123 abc12345
football1 baseball1 dragon123 sunshine1 princess1 trustno1 monkey123
passw0rd p@ssword p@ssw0rd changeme letmein123 starwars1 whatever1
thusildy thusildy1 tradingbot bitcoin1 bitcoin123 crypto123
""".split())


def password_problem(password: str, identifier: str = "") -> Optional[str]:
    """Why this password is not acceptable, or None if it is.

    Returns a sentence to show a person, not an error code. A rule you cannot
    read is a rule you cannot satisfy, and the usual result is `Password1!`.
    """
    pw = password or ""
    if len(pw) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(pw) > 256:
        # Not a strength rule — a bound. scrypt over a megabyte of input is a
        # free way to make the login endpoint expensive to serve.
        return "That is longer than 256 characters."
    if pw.lower() in _COMMON_PASSWORDS:
        return "That is one of the most commonly used passwords. Pick another."
    if len(set(pw)) <= 2:
        return "That is only one or two different characters repeated."
    # Runs like 12345678 or abcdefgh.
    if len(pw) >= 4:
        deltas = {ord(b) - ord(a) for a, b in zip(pw, pw[1:])}
        if deltas <= {1} or deltas <= {-1}:
            return "That is a straight run of characters. Pick something less predictable."
    ident = (identifier or "").strip().lower()
    local = ident.split("@")[0]
    if local and len(local) >= 3 and local in pw.lower():
        return "Do not put your email address in your password."
    return None


# WHAT COUNTS AS AN EMAIL HERE.
#
# Deliberately permissive. The only address that truly validates is one that
# receives a message, which is what confirmation is for — a clever regex
# rejects legitimate addresses (plus-tags, new TLDs, unicode domains) and
# catches nothing a typo-ing human does. This rejects what cannot be an
# address at all and leaves the rest to the confirmation email.
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")


def email_problem(identifier: str) -> Optional[str]:
    ident = (identifier or "").strip()
    if not ident:
        return "Enter your email address."
    if len(ident) > 254:
        return "That address is too long."
    if not _EMAIL_RE.match(ident):
        return "That does not look like an email address."
    return None

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
        self.sessions_path = self.path.with_name(
            self.path.stem + "_sessions.json")
        self._lock = threading.Lock()
        self._users: Dict[str, User] = {}
        self._sessions: Dict[str, tuple] = {}     # token -> (identifier, exp)
        self._mtime = 0.0
        self._load()
        self._load_sessions()

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
        # EMAIL, NOT A HANDLE.
        #
        # Stored lower-cased because addresses are compared that way in
        # practice, and because the lookup table is already keyed on the
        # lower-cased form — a `Tim@x.com` that could not sign in as
        # `tim@x.com` would be a very confusing bug.
        ident = (identifier or "").strip().lower()
        bad = email_problem(ident)
        if bad:
            raise AuthError(bad)
        bad = password_problem(password, ident)
        if bad:
            raise AuthError(bad)
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
            self._sessions = {d: v for d, v in self._sessions.items()
                              if v[0] != u.identifier}
            self._save_sessions()
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
            self._sessions = {d: v for d, v in self._sessions.items()
                              if v[0] != u.identifier}
            self._save_sessions()
            self._save()

    # ----------------------------------------------------------- sessions
    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _load_sessions(self) -> None:
        if not self.sessions_path.exists():
            return
        try:
            raw = json.loads(self.sessions_path.read_text())
        except Exception as e:
            # Losing the session table signs everyone out, which is
            # recoverable. Refusing to start over it would not be.
            log.warning("sessions unreadable, starting empty: %s", e)
            return
        now = time.time()
        self._sessions = {d: (ident, exp)
                          for d, (ident, exp) in raw.items() if exp > now}

    def _save_sessions(self) -> None:
        try:
            self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.sessions_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._sessions))
            os.replace(tmp, self.sessions_path)
            os.chmod(self.sessions_path, 0o600)
        except OSError as e:
            # An unsaved session still works until restart. Do not fail a
            # sign-in over it.
            log.warning("could not persist sessions: %s", e)

    def _prune(self) -> None:
        # drop expired rows; caller holds the lock
        now = time.time()
        self._sessions = {d: v for d, v in self._sessions.items()
                          if v[1] > now}

    def start_session(self, u: User) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[self._digest(token)] = (u.identifier,
                                                   time.time() + SESSION_TTL)
            self._prune()
            self._save_sessions()
        return token                  # the only moment the raw token exists

    def session_user(self, token: str) -> Optional[User]:
        if not token:
            return None
        want = self._digest(token)
        with self._lock:
            # Constant-time even though both sides are already hashes: the
            # comparison is cheap, and the habit is what keeps a plain `==`
            # from creeping back in somewhere it would matter.
            found = None
            for d, (ident, exp) in self._sessions.items():
                if hmac.compare_digest(d, want):
                    found = (d, ident, exp)
            if found is None:
                return None
            d, ident, exp = found
            now = time.time()
            if exp < now:
                self._sessions.pop(d, None)
                self._save_sessions()
                return None
            # Sliding expiry: someone who opens the app every day should never
            # be asked for a password again. A fixed window from sign-in logs
            # active users out on a schedule they cannot see a reason for.
            if exp - now < _RENEW_AFTER:
                self._sessions[d] = (ident, now + SESSION_TTL)
                self._save_sessions()
        return self.get(ident)

    def end_session(self, token: str) -> None:
        with self._lock:
            if self._sessions.pop(self._digest(token), None) is not None:
                self._save_sessions()


_accounts: Optional[Accounts] = None
_accounts_lock = threading.Lock()


def get_accounts() -> Accounts:
    global _accounts
    with _accounts_lock:
        if _accounts is None:
            _accounts = Accounts()
        return _accounts
