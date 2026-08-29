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

import json
import logging
import os
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
        "price_env": "STRIPE_PRICE_MONTHLY",
    },
    {
        "id": "yearly",
        "name": "Yearly",
        "amount": 19000,
        "currency": "eur",
        "period": "year",
        "note": "two months free",
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
DISCLOSURE = (
    "This is analysis software, not financial advice, and it places no "
    "orders. Model accuracy is currently at or near chance on backtests "
    "(AUC 0.43-0.53, where 0.5 is a coin flip). Do not trade money you "
    "cannot afford to lose."
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


def apply_webhook(event: Dict[str, Any], accounts) -> Optional[str]:
    """Grant or revoke from a verified Stripe event. Returns the identifier.

    NOT called by anything yet: it needs a public endpoint to receive events
    and `STRIPE_WEBHOOK_SECRET` to verify their signature. It is written now
    so the shape of the grant is settled, and so the one rule that matters is
    recorded — **verify the signature before acting**. An unverified webhook
    endpoint is a free-subscription button for anyone who finds the URL.
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
