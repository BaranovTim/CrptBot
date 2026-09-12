"""Sign in with Google, GitHub, Facebook, Apple.

THE SHAPE: BROWSER ON THE SERVER, POLL FROM THE PHONE
    1. The app opens `/api/auth/oauth/<provider>/start?device=<nonce>` in
       the system browser. The server records the nonce and redirects to
       the provider with a signed `state`.
    2. The person signs in there. The provider redirects back to
       `/api/auth/oauth/<provider>/callback` on this server, which swaps the
       code for a token, reads the profile, finds or creates the account,
       starts a session, and files the session token under the nonce.
    3. Meanwhile the app polls `/api/auth/oauth/poll?device=<nonce>` and
       gets the token once it exists. Once: the entry is deleted on read.

    No custom URL scheme. A deep link back into the app is the obvious
    design and the wrong one here: any app on the phone can register the
    same scheme and receive the callback instead, and the intent filter
    would have to be right on both platforms. Polling needs nothing from
    the OS and cannot be intercepted. The browser page just says "return
    to Vanth".

WHAT EACH PROVIDER NEEDS FROM YOU
    A client id and secret from that provider's developer console, with
    `{PUBLIC_BASE_URL}/api/auth/oauth/<provider>/callback` registered as
    the redirect URI. Put them in .env as OAUTH_<PROVIDER>_CLIENT_ID and
    OAUTH_<PROVIDER>_CLIENT_SECRET. A provider with no credentials is not
    offered -- `available()` is what the app reads to decide which buttons
    to draw, so a half-configured provider never becomes a button that
    fails.

    Apple is different in kind. Its "client secret" is a JWT you sign with
    a private key from the Apple Developer Program (paid), which needs
    ES256 and therefore the `cryptography` package, which is not in the
    image. `available()` leaves Apple out until both exist, and says why.

EMAIL IS THE IDENTITY
    An OAuth sign-in resolves to an email address the provider vouches for,
    and that address is the account -- the same account a password sign-in
    with that email would reach. So somebody who registered with a password
    and later taps "Google" lands in their own account, not a duplicate.
    A provider that will not vouch for the address (GitHub with a private
    email, Facebook without the email scope) is refused rather than trusted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

STATE_TTL = 10 * 60          # start -> callback
TOKEN_TTL = 5 * 60           # callback -> poll
MAX_PENDING = 500

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "google": {
        "label": "Google",
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
        "pkce": True,
    },
    "github": {
        "label": "GitHub",
        "auth": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "emails": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
        "pkce": False,
    },
    "facebook": {
        "label": "Facebook",
        "auth": "https://www.facebook.com/v19.0/dialog/oauth",
        "token": "https://graph.facebook.com/v19.0/oauth/access_token",
        "userinfo": "https://graph.facebook.com/me?fields=id,name,email",
        "scope": "email public_profile",
        "pkce": False,
    },
    "apple": {
        "label": "Apple",
        "auth": "https://appleid.apple.com/auth/authorize",
        "token": "https://appleid.apple.com/auth/token",
        "userinfo": None,                    # the email is in the id_token
        "scope": "name email",
        "pkce": False,
        "needs": ("OAUTH_APPLE_TEAM_ID", "OAUTH_APPLE_KEY_ID",
                  "OAUTH_APPLE_PRIVATE_KEY"),
    },
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _creds(provider: str) -> Tuple[str, str]:
    p = provider.upper()
    return _env(f"OAUTH_{p}_CLIENT_ID"), _env(f"OAUTH_{p}_CLIENT_SECRET")


def unavailable_reason(provider: str) -> Optional[str]:
    """None when the button can be shown; otherwise why not."""
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        return "unknown provider"
    cid, secret = _creds(provider)
    if not cid:
        return f"OAUTH_{provider.upper()}_CLIENT_ID is not set"
    if provider == "apple":
        missing = [n for n in cfg["needs"] if not _env(n)]
        if missing:
            return "Apple needs " + ", ".join(missing)
        try:
            import cryptography  # noqa: F401
        except ImportError:
            return ("Apple's client secret is an ES256 JWT; the "
                    "`cryptography` package is not installed")
    elif not secret:
        return f"OAUTH_{provider.upper()}_CLIENT_SECRET is not set"
    if not _env("PUBLIC_BASE_URL"):
        return "PUBLIC_BASE_URL is not set, so there is no callback URL"
    return None


def available() -> List[Dict[str, str]]:
    """What the app draws buttons for. Order is the order shown."""
    return [{"id": name, "label": cfg["label"]}
            for name, cfg in PROVIDERS.items()
            if unavailable_reason(name) is None]


def _redirect_uri(provider: str) -> str:
    return f"{_env('PUBLIC_BASE_URL').rstrip('/')}/api/auth/oauth/{provider}/callback"


# ------------------------------------------------------------- the flow
class OAuthError(Exception):
    """Shown to the person on the browser page. Never a stack trace."""


class Flow:
    """In-memory state for sign-ins in progress. Bounded and expiring: a
    started-and-abandoned sign-in must not sit here forever, and a flood of
    starts must not grow without limit."""

    def __init__(self, fetch: Optional[Callable[..., Dict[str, Any]]] = None):
        self._lock = threading.Lock()
        self._states: Dict[str, Dict[str, Any]] = {}     # state -> start
        self._tokens: Dict[str, Tuple[str, float]] = {}  # device -> (tok, at)
        self._fetch = fetch or _http_json

    # -- 1. start ----------------------------------------------------
    def start(self, provider: str, device: str) -> str:
        """The URL to send the browser to."""
        why = unavailable_reason(provider)
        if why:
            raise OAuthError(f"{provider} sign-in is not available: {why}")
        device = (device or "").strip()
        if not (16 <= len(device) <= 128) or not device.replace("-", "").replace("_", "").isalnum():
            raise OAuthError("bad device nonce")
        cfg = PROVIDERS[provider]
        cid, _ = _creds(provider)
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48) if cfg["pkce"] else None
        with self._lock:
            self._sweep()
            if len(self._states) >= MAX_PENDING:
                raise OAuthError("too many sign-ins in progress; try again "
                                 "in a few minutes")
            self._states[state] = {"provider": provider, "device": device,
                                   "verifier": verifier, "at": time.time()}
        q = {
            "client_id": cid,
            "redirect_uri": _redirect_uri(provider),
            "response_type": "code",
            "scope": cfg["scope"],
            "state": state,
        }
        if verifier:
            digest = hashlib.sha256(verifier.encode()).digest()
            q["code_challenge"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            q["code_challenge_method"] = "S256"
        if provider == "apple":
            q["response_mode"] = "form_post"
        if provider == "google":
            q["access_type"] = "online"
            q["prompt"] = "select_account"
        return f"{cfg['auth']}?{urllib.parse.urlencode(q)}"

    # -- 2. callback -------------------------------------------------
    def callback(self, provider: str, code: str, state: str,
                 accounts, id_token: Optional[str] = None) -> Dict[str, Any]:
        """Swap the code for an identity, land in an account, file the
        session under the device nonce. Returns the account's public dict."""
        with self._lock:
            self._sweep()
            started = self._states.pop((state or "").strip(), None)
        if started is None or started["provider"] != provider:
            # Expired, replayed, or forged. All three read the same way to
            # the person: start again from the app.
            raise OAuthError("this sign-in has expired or was already used; "
                             "go back to Vanth and try again")
        if not code:
            raise OAuthError("the provider did not return a code")

        cfg = PROVIDERS[provider]
        cid, secret = _creds(provider)
        form = {
            "client_id": cid,
            "client_secret": _client_secret(provider, cid, secret),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _redirect_uri(provider),
        }
        if started["verifier"]:
            form["code_verifier"] = started["verifier"]
        tok = self._fetch(cfg["token"], data=form,
                          headers={"Accept": "application/json"})
        access = tok.get("access_token")
        if not access:
            raise OAuthError(f"{cfg['label']} did not issue a token: "
                             f"{tok.get('error_description') or tok.get('error') or 'no reason given'}")

        email, verified, name, pid = self._identity(provider, access,
                                                    tok.get("id_token"))
        if not email:
            raise OAuthError(f"{cfg['label']} did not share an email address "
                             "for this account, and an email is the account")
        if not verified:
            raise OAuthError(f"{cfg['label']} reports that email as "
                             "unverified; verify it there first")

        u = accounts.get(email)
        if u is None:
            u = accounts.register_external(email, provider=provider,
                                           display_name=name)
        token = accounts.start_session(u)
        with self._lock:
            self._tokens[started["device"]] = (token, time.time())
        log.info("oauth %s signed in %s", provider, email)
        return u.public()

    # -- 3. poll -----------------------------------------------------
    def poll(self, device: str) -> Optional[str]:
        """The session token, once. None while the browser is still busy."""
        with self._lock:
            self._sweep()
            got = self._tokens.pop((device or "").strip(), None)
        return got[0] if got else None

    # -- internals ---------------------------------------------------
    def _sweep(self) -> None:
        now = time.time()
        for k in [k for k, v in self._states.items() if v["at"] + STATE_TTL < now]:
            del self._states[k]
        for k in [k for k, (_, at) in self._tokens.items() if at + TOKEN_TTL < now]:
            del self._tokens[k]

    def _identity(self, provider: str, access: str, id_token: Optional[str]
                  ) -> Tuple[Optional[str], bool, Optional[str], Optional[str]]:
        """(email, email_verified, display name, provider user id)."""
        cfg = PROVIDERS[provider]
        bearer = {"Authorization": f"Bearer {access}",
                  "Accept": "application/json",
                  "User-Agent": "vanth/1"}
        if provider == "google":
            p = self._fetch(cfg["userinfo"], headers=bearer)
            return (p.get("email"), bool(p.get("email_verified")),
                    p.get("name"), p.get("sub"))
        if provider == "github":
            p = self._fetch(cfg["userinfo"], headers=bearer)
            # The profile email is whatever they chose to show publicly,
            # often nothing. The emails endpoint says which are verified.
            emails = self._fetch(cfg["emails"], headers=bearer)
            best = None
            for e in emails if isinstance(emails, list) else []:
                if e.get("verified") and (best is None or e.get("primary")):
                    best = e
            return ((best or {}).get("email"), bool(best),
                    p.get("name") or p.get("login"), str(p.get("id", "")))
        if provider == "facebook":
            p = self._fetch(cfg["userinfo"], headers=bearer)
            # Facebook only returns an email it has verified.
            return (p.get("email"), bool(p.get("email")),
                    p.get("name"), p.get("id"))
        if provider == "apple":
            claims = _jwt_claims_unverified(id_token or "")
            # The id_token is signed by Apple and was received directly from
            # Apple's token endpoint over TLS in this same request, which is
            # what makes reading it without signature verification
            # acceptable here and nowhere else.
            ev = claims.get("email_verified")
            return (claims.get("email"),
                    ev is True or str(ev).lower() == "true",
                    None, claims.get("sub"))
        raise OAuthError("unknown provider")


def _client_secret(provider: str, cid: str, secret: str) -> str:
    if provider != "apple":
        return secret
    # An ES256 JWT, valid up to six months, signed with the .p8 key.
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import (
            decode_dss_signature)
    except ImportError as e:
        raise OAuthError("Apple sign-in needs the cryptography package") from e
    now = int(time.time())
    header = {"alg": "ES256", "kid": _env("OAUTH_APPLE_KEY_ID")}
    payload = {"iss": _env("OAUTH_APPLE_TEAM_ID"), "iat": now,
               "exp": now + 3600, "aud": "https://appleid.apple.com",
               "sub": cid}
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    signing = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(payload).encode())}"
    key = serialization.load_pem_private_key(
        _env("OAUTH_APPLE_PRIVATE_KEY").replace("\\\\n", "\\n").encode(), None)
    der = key.sign(signing.encode(), ec.ECDSA(hashes.SHA256()))
    r, s_ = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s_.to_bytes(32, "big")
    return f"{signing}.{b64(raw)}"


def _jwt_claims_unverified(token: str) -> Dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _http_json(url: str, data: Optional[Dict[str, str]] = None,
               headers: Optional[Dict[str, str]] = None) -> Any:
    body = urllib.parse.urlencode(data).encode() if data else None
    h = dict(headers or {})
    if body:
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=h,
                                 method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
    except Exception as e:
        raise OAuthError(f"could not reach the provider: {e}") from e
    try:
        return json.loads(raw)
    except ValueError:
        # GitHub answers form-encoded unless told otherwise; be tolerant.
        return dict(urllib.parse.parse_qsl(raw.decode(errors="ignore")))


_flow: Optional[Flow] = None


def get_flow() -> Flow:
    global _flow
    if _flow is None:
        _flow = Flow()
    return _flow
