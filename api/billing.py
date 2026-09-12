"""Subscriptions, and the Stripe wiring behind them.

STATUS: THE CODE IS HERE, THE ACCOUNT IS NOT
    Stripe needs an account in your name, business verification and a bank
    account, none of which I can create for you. Until `STRIPE_SECRET_KEY` is
    set, `checkout_session` raises `NotImplementedError` with a message written
    to be read by a person, and `/api/billing/plans` reports
    `configured: false` so the app says "payments are not set up yet" rather
    than opening a checkout that fails.

    Everything else works now: accounts, sign-in, tiers, and the gate that
    withholds analysis from an unsubscribed account. Granting somebody access
    is `set_tier(identifier, "pro", ends)` — Stripe's only job here is to call
    that automatically after a payment.

THE PART THAT NEEDS A PUBLIC SERVER
    Stripe confirms payment by POSTing a webhook to your API. It cannot reach
    a Tailscale address, so on the current deployment the last step of a real
    purchase has nowhere to land. Publishing checkout without that would take
    money and grant nothing.

    Two ways out when you get there: a public HTTPS domain (Caddy in front,
    see DEPLOY.md), or polling `checkout.sessions.retrieve` after the app
    returns from the browser. The webhook is the one that survives a phone
    dying mid-payment, which is why it is the default below.

WHY urllib AND NOT THE STRIPE SDK
    Two calls, both plain form-encoded POSTs. `pip install stripe` pulls a
    dependency into an image that currently builds from four, to save about
    thirty lines.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"

# What is on sale. Prices are in minor units, as Stripe wants them, and the
# ids are ours — the Stripe Price id is looked up from the environment so the
# same build works against test and live keys.
PLANS: List[Dict[str, Any]] = [
    {
        "id": "monthly",
        "name": "Monthly",
        "amount": 1900,
        "currency": "eur",
        "period": "month",
        # One line, shown under the plan name. It says what the subscription
        # IS and what it is not, and it names no number -- claiming an
        # accuracy here that DISCLOSURE then qualifies two paragraphs later
        # would be the kind of small dishonesty that earns a chargeback.
        "description": (
            "Full access to Vanth, billed monthly. Cancel any time; the "
            "subscription runs to the end of the period you have paid for."),
        "price_env": "STRIPE_PRICE_MONTHLY",
    },
    {
        "id": "yearly",
        "name": "Yearly",
        "amount": 19000,
        "currency": "eur",
        "period": "year",
        "note": "two months free",
        "description": (
            "Full access to Vanth for a year, at the price of ten months. "
            "Best if you already know the analysis is worth having."),
        "price_env": "STRIPE_PRICE_YEARLY",
    },
]

# What a subscription actually unlocks. This list is shown on the paywall, so
# it must describe what the product does and nothing it does not.
INCLUDED = [
    "Model probability and expected value on every timeframe",
    "Six independently fitted timeframes, 1m through 1d",
    "Whale filings and news, with the time each happened",
    "Alerts for entries, spikes and scheduled releases",
]

# Said on the paywall, deliberately. Somebody about to pay is entitled to know
# what the numbers behind the paywall are worth.
# Updated 2026-08-30 after fitting four symbols across six timeframes. The
# earlier wording ("at or near chance, AUC 0.43-0.53") was drawn partly from a
# BTC 1h fit that used a shorter window than the rest; refitted on the same
# 2023-2026 span it scores 0.540, not 0.430. Saying less than is true is as
# much a misrepresentation as saying more, so this states both halves.
DISCLOSURE = (
    "This is analysis software, not financial advice, and it places no "
    "orders. Most timeframes score at or near chance on backtests (AUC "
    "0.46-0.53, where 0.5 is a coin flip). The 1-hour models are the "
    "exception: 0.52-0.54 across BTC, ETH, SOL and ADA over 2023-2026, with "
    "clean shuffle controls. That is a small and consistent edge, not a "
    "reliable one -- it has not been validated on a period held out from "
    "fitting, and no result here has been shown to survive trading costs. "
    "Do not trade money you cannot afford to lose."
)


def plans() -> List[Dict[str, Any]]:
    """The catalogue, with the Stripe ids stripped out."""
    return [{k: v for k, v in p.items() if k != "price_env"} | {
        "included": INCLUDED,
        "disclosure": DISCLOSURE,
    } for p in PLANS]


def _plan(plan_id: Optional[str]) -> Dict[str, Any]:
    for p in PLANS:
        if p["id"] == (plan_id or "monthly"):
            return p
    raise ValueError(f"no such plan: {plan_id!r}")


def _key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise NotImplementedError(
            "Payments are not set up. Create a Stripe account, then set "
            "STRIPE_SECRET_KEY and the STRIPE_PRICE_* ids in .env. Until "
            "then, subscriptions can be granted by hand with "
            "manage_accounts.py grant.")
    return key


def _post(path: str, form: Dict[str, str]) -> Dict[str, Any]:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        f"{STRIPE_API}{path}", data=data,
        headers={"Authorization": f"Bearer {_key()}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # Stripe puts a human-readable reason in the body. Losing it and
        # showing "HTTP 400" would make every payment failure unreadable.
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        raise RuntimeError(f"stripe {path}: {detail or e}") from e


def _get(path: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{STRIPE_API}{path}",
        headers={"Authorization": f"Bearer {_key()}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# `cs_` then Stripe's own id characters, and nothing else. This string comes
# out of a query parameter and goes into a URL we then call with our secret
# key attached, so it is validated rather than trusted -- an unchecked value
# here lets a stranger point our authenticated request at any Stripe path.
_SESSION_ID = re.compile(r"^cs_[A-Za-z0-9_]{1,200}$")


def session_paid(session_id: str) -> Optional[bool]:
    """True paid, False not, None unknown. `None` is a real answer.

    The landing page must not assert "you are subscribed" on the strength of
    somebody opening a URL. But it must not call a real payment a failure
    either, just because Stripe was slow for ten seconds -- so a lookup that
    cannot complete says "unknown" and the page stays honest about it.
    """
    if not _SESSION_ID.match(session_id or "") or not os.environ.get(
            "STRIPE_SECRET_KEY", ""):
        return None
    try:
        got = _get(f"/checkout/sessions/{session_id}")
    except Exception as e:
        log.warning("could not read checkout session: %s", e)
        return None
    return got.get("payment_status") == "paid" or got.get(
        "status") == "complete"


# The three things a person can arrive at this page having done. "PENDING"
# is not a hedge, it is the honest answer when Stripe could not be reached:
# their money may well have left, and telling them it did not would be worse
# than telling them we are still checking.
PAID, PENDING, CANCELLED = "paid", "pending", "cancelled"

_COPY = {
    PAID: ("&#10003;", "#34d399", "Payment received",
           "You're subscribed. Reopen Vanth \u2014 your account is "
           "already upgraded.",
           "Stripe has emailed your receipt."),
    PENDING: ("&#8226;", "#fbbf24", "Payment is being confirmed",
              "This normally takes a few seconds. Reopen Vanth and pull "
              "down to refresh; if it still shows Free in a minute or two, "
              "nothing is lost \u2014 get in touch and we will sort it.",
              "Do not pay again. A second checkout would charge you twice."),
    CANCELLED: ("&#8592;", "#94a3b8", "No payment was taken",
                "You closed the checkout before it finished, so nothing was "
                "charged. Reopen Vanth whenever you want to try again.",
                "Your account is unchanged."),
}


def landing_state(route: str, session_id: Optional[str]) -> str:
    """Which page to show. Separate from the handler so it can be tested.

    The `is True` is the whole point: `session_paid` answers None when it
    could not reach Stripe, and None must never be read as "not paid" by
    someone whose card was charged eight seconds ago.
    """
    if route.rstrip("/").endswith("cancelled"):
        return CANCELLED
    return PAID if session_paid(session_id or "") is True else PENDING


# The torch, inline. A data URI and not a file because these pages are
# served to somebody who has just paid, possibly on a bad connection, and
# every external reference is one more thing that can hang -- the same reason
# there is no font and no script here.
FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Crect width='64' height='64' rx='14' fill='%23111317'/%3E"
    "%3Cpath d='M32 12C41 23 41 30 32 37C23 30 23 23 32 12Z' fill='%2300FFAB'/%3E"
    "%3Cpath d='M32 20C36 25 36 29 32 32C28 29 28 25 32 20Z' fill='%23111317'/%3E"
    "%3Cpath d='M23 39h18M32 41v12' stroke='%238B90A0' stroke-width='4'"
    " stroke-linecap='round'/%3E%3C/svg%3E"
)


def landing_html(state: str) -> str:
    """The page Stripe sends the customer to. See `landing_html_custom`."""
    mark, colour, title, body, foot = _COPY.get(state, _COPY[PENDING])
    return landing_html_custom(mark=mark, colour=colour, title=title,
                               body=body, foot=foot)


def landing_html_custom(*, mark: str, colour: str, title: str, body: str,
                        foot: str) -> str:
    """A one-card page for a browser that arrived from somewhere else --
    Stripe's checkout, or the confirmation link in an email. Deliberately
    self-contained.

    No fonts, no scripts, no CDN. It is the first thing somebody sees after
    handing over money or clicking a link in their mail, on whatever browser
    their phone opened, possibly on a bad connection -- so it has to render
    from the one response, with nothing left to fetch that could fail or
    hang. `body` and `title` are trusted server strings, never user input.
    """
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vanth</title>
<link rel="icon" href="{FAVICON}">
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center;
         justify-content: center; background: #0b0f14; color: #e6edf3;
         font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI",
               Roboto, sans-serif; padding: 24px; box-sizing: border-box; }}
  .card {{ width: 100%; max-width: 26rem; background: #121821;
          border: 1px solid #1f2937; border-radius: 18px; padding: 32px 28px;
          text-align: center; }}
  .mark {{ width: 56px; height: 56px; margin: 0 auto 20px; border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          font-size: 26px; color: #0b0f14; background: {colour}; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 12px; letter-spacing: -0.01em; }}
  p {{ margin: 0 0 18px; color: #9fb0c0; }}
  .foot {{ margin: 0; padding-top: 18px; border-top: 1px solid #1f2937;
          font-size: 0.85rem; color: #64748b; }}
  .brand {{ margin-top: 22px; font-size: 0.8rem; letter-spacing: 0.14em;
           text-transform: uppercase; color: #475569; }}
</style></head>
<body><div class="card">
  <div class="mark">{mark}</div>
  <h1>{title}</h1>
  <p>{body}</p>
  <p class="foot">{foot}</p>
  <div class="brand">Vanth</div>
</div></body></html>"""


def checkout_session(user, plan_id: Optional[str] = None) -> Dict[str, Any]:
    """A Stripe Checkout URL for this account to open in a browser.

    The account is carried in `client_reference_id` so the webhook can find it
    again. Trusting anything the browser sends back instead would mean a
    subscription could be granted by whoever can craft a return URL.
    """
    if user is None:
        raise ValueError("sign in before subscribing")
    p = _plan(plan_id)
    price = os.environ.get(p["price_env"], "")
    if not price:
        raise NotImplementedError(
            f"{p['price_env']} is not set. Create the {p['name']} price in "
            "the Stripe dashboard and put its price_... id in .env.")

    base = os.environ.get("PUBLIC_BASE_URL", "")
    if not base:
        raise NotImplementedError(
            "PUBLIC_BASE_URL is not set. Stripe has to send the customer "
            "back to a reachable address after payment, and it cannot reach "
            "a Tailscale one.")

    s = _post("/checkout/sessions", {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "client_reference_id": user.identifier,
        "success_url": f"{base}/billing/done?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/billing/cancelled",
    })
    return {"url": s.get("url"), "session": s.get("id")}


# How long after Stripe signs an event we still accept it. Stripe's own
# libraries use 300s and so do we.
#
# This is not paranoia about clock drift, it is the replay defence. The
# signature over a real "subscription active" event stays valid forever;
# without a timestamp check, anyone who ever observed one delivery could
# POST those same bytes back for free access, for years. Rejecting old
# timestamps is what makes a captured event a single-use thing.
SIGNATURE_TOLERANCE = 300


class SignatureError(Exception):
    """The payload was not signed by Stripe, or was signed too long ago."""


def verify_signature(raw: bytes, header: str, secret: Optional[str] = None,
                     now: Optional[float] = None) -> Dict[str, Any]:
    """Return the parsed event, or raise. NEVER parse `raw` before this.

    `raw` must be the exact bytes off the wire. Stripe signs the payload
    byte for byte, so decoding to JSON and re-serialising -- reordering a
    key, changing float formatting, restyling whitespace -- produces a
    different string and every signature fails. That is the whole reason
    this route reads the body itself instead of using `_body()`.
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "") if secret is None \
        else secret
    if not secret:
        raise NotImplementedError(
            "STRIPE_WEBHOOK_SECRET is not set. Until it is, this endpoint "
            "cannot tell a real Stripe event from anyone who found the URL, "
            "so it accepts nothing.")

    timestamp, sigs = "", []
    for piece in header.split(","):
        key, _, value = piece.partition("=")
        key, value = key.strip(), value.strip()
        if key == "t":
            timestamp = value
        elif key == "v1":
            # A list, not one value: during a secret rotation Stripe signs
            # with both the old and the new secret and sends both. Reading
            # only the first would drop half the events mid-rotation.
            sigs.append(value)
    if not timestamp or not sigs:
        raise SignatureError("malformed Stripe-Signature header")
    try:
        sent = int(timestamp)
    except ValueError:
        raise SignatureError("malformed timestamp in Stripe-Signature")

    age = (time.time() if now is None else now) - sent
    if abs(age) > SIGNATURE_TOLERANCE:
        raise SignatureError(
            f"event timestamp is {int(abs(age))}s away, outside the "
            f"{SIGNATURE_TOLERANCE}s window")

    # The signed string is "<timestamp>.<body>", and the timestamp goes in
    # exactly as it arrived rather than reformatted from the int above.
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw,
                        hashlib.sha256).hexdigest()
    # compare_digest, not ==. String equality returns early at the first
    # wrong byte, and the timing difference leaks the signature one byte at
    # a time to anyone willing to send enough requests.
    # `isascii` before comparing, not defensiveness for its own sake:
    # compare_digest raises TypeError on a non-ASCII str, which would leave
    # the route returning 500 to a hand-crafted header -- and a 500 is the
    # one answer that tells Stripe to keep retrying.
    if not any(s.isascii() and hmac.compare_digest(expected, s) for s in sigs):
        raise SignatureError("signature does not match")

    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise SignatureError("signed payload was not JSON")


def apply_webhook(event: Dict[str, Any], accounts) -> Optional[str]:
    """Grant or revoke from a verified Stripe event. Returns the identifier.

    Callers MUST have verified the event first: `verify_signature` above is
    the only thing standing between this and anyone who finds the URL, since
    what it does is hand out paid access.
    """
    kind = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    ident = obj.get("client_reference_id") or obj.get("metadata", {}).get(
        "identifier")
    if not ident:
        log.warning("stripe %s carried no account reference", kind)
        return None

    if kind in ("checkout.session.completed",
                "customer.subscription.updated"):
        ends = obj.get("current_period_end")
        accounts.set_tier(ident, "pro",
                          float(ends) if ends else time.time() + 31 * 86400)
        return ident
    if kind in ("customer.subscription.deleted",
                "invoice.payment_failed"):
        # back to free, not deleted. Their account and history survive a
        # lapsed card.
        accounts.set_tier(ident, "free", None)
        return ident
    return None
