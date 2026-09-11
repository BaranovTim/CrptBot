"""The Stripe webhook — the one endpoint whose caller is not our app.

WHAT THESE GUARD, AND WHY EACH ONE EARNED A TEST

  THE FREE SUBSCRIPTION     the URL is public and its job is to hand out paid
                            access. Without a signature check, "POST this JSON
                            to that URL" IS the subscription. Every rejection
                            test below is the same test: bytes we did not sign
                            must not grant a tier.

  THE REPLAY               a signature over a real "subscription active"
                            event never expires on its own. Anyone who ever
                            observed one delivery could send those exact
                            bytes back forever. The timestamp window is what
                            makes a captured event single-use, so it is
                            asserted in both directions -- an old event and a
                            far-future one.

  THE RE-SERIALISED BODY   Stripe signs the payload byte for byte. Parsing to
                            JSON and dumping it back reorders keys and
                            respaces separators, and every signature fails.
                            The bug reads as "Stripe is sending bad
                            signatures", so the exact-bytes requirement is
                            pinned rather than left as a comment.

  THE ROTATION             changing the signing secret makes Stripe send two
                            v1 signatures at once. Reading only the first
                            drops half the events for the length of the
                            rotation.

  THE FAIL-OPEN            an unconfigured secret must reject everything. The
                            tempting default -- skip verification when no
                            secret is set -- turns a half-finished deployment
                            into an open grant endpoint.

  THE POISONED QUEUE       Stripe retries any non-2xx for days, then disables
                            the endpoint. An event type we do not act on has
                            to be acknowledged, or one uninteresting event
                            stops delivery of the ones that pay.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import time

from api.billing import (SIGNATURE_TOLERANCE, SignatureError, apply_webhook,
                         verify_signature)

SECRET = "whsec_test_not_a_real_secret"


def _signed(payload: dict, secret: str = SECRET, when: float = None,
            body: bytes = None) -> tuple:
    """Return (raw_bytes, header) exactly as Stripe would send them."""
    raw = body if body is not None else json.dumps(payload).encode()
    t = str(int(time.time() if when is None else when))
    sig = hmac.new(secret.encode(), t.encode() + b"." + raw,
                   hashlib.sha256).hexdigest()
    return raw, f"t={t},v1={sig}"


def _event(kind: str = "checkout.session.completed", ident: str = "a@b.com",
           ends: float = None) -> dict:
    obj = {"client_reference_id": ident}
    if ends is not None:
        obj["current_period_end"] = ends
    return {"type": kind, "data": {"object": obj}}


class _Accounts:
    """Records set_tier calls instead of touching the real account store."""

    def __init__(self):
        self.calls = []

    def set_tier(self, ident, tier, ends):
        self.calls.append((ident, tier, ends))


def test_a_genuine_event_is_accepted():
    raw, header = _signed(_event())
    got = verify_signature(raw, header, secret=SECRET)
    assert got["type"] == "checkout.session.completed", got
    return True


def test_a_tampered_payload_is_rejected():
    """The attack this endpoint exists to stop, in its simplest form."""
    raw, header = _signed(_event(ident="victim@example.com"))
    # Same length on purpose. A longer forgery would also be caught by
    # Content-Length, and then this would pass without the HMAC doing
    # any of the work.
    forged = raw.replace(b"victim@example.com", b"robber@example.com")
    assert len(forged) == len(raw) and forged != raw
    try:
        verify_signature(forged, header, secret=SECRET)
    except SignatureError:
        return True
    raise AssertionError("a forged payload was accepted")


def test_a_signature_from_another_secret_is_rejected():
    raw, header = _signed(_event(), secret="whsec_somebody_elses_secret")
    try:
        verify_signature(raw, header, secret=SECRET)
    except SignatureError:
        return True
    raise AssertionError("a foreign signature was accepted")


def test_an_old_event_is_rejected_even_though_its_signature_is_valid():
    """Replay. The signature IS correct here -- age is the only defence."""
    old = time.time() - SIGNATURE_TOLERANCE - 60
    raw, header = _signed(_event(), when=old)
    # It really is a valid signature: the same bytes pass with a clock that
    # says the event is fresh. So this rejection is the window, not the HMAC.
    assert verify_signature(raw, header, secret=SECRET, now=old + 1)
    try:
        verify_signature(raw, header, secret=SECRET)
    except SignatureError:
        return True
    raise AssertionError("a replayed event was accepted")


def test_a_far_future_event_is_rejected():
    """abs(), not a one-sided check: a clock skewed forward is still wrong."""
    raw, header = _signed(_event(), when=time.time() + SIGNATURE_TOLERANCE + 60)
    try:
        verify_signature(raw, header, secret=SECRET)
    except SignatureError:
        return True
    raise AssertionError("an event from the future was accepted")


def test_re_serialising_the_body_breaks_the_signature():
    """Why the route reads rfile itself instead of using _body()."""
    raw, header = _signed(_event())
    round_tripped = json.dumps(json.loads(raw), indent=2).encode()
    assert round_tripped != raw, "test needs the bytes to actually differ"
    assert verify_signature(raw, header, secret=SECRET)
    try:
        verify_signature(round_tripped, header, secret=SECRET)
    except SignatureError:
        return True
    raise AssertionError("parsing and redumping preserved the signature")


def test_both_signatures_are_read_during_a_secret_rotation():
    raw, header = _signed(_event(), secret="whsec_the_old_one")
    _, fresh = _signed(_event(), secret=SECRET)
    # Stripe's shape mid-rotation: one t, two v1, only one of which is ours.
    merged = header + "," + fresh.split(",", 1)[1]
    assert verify_signature(raw, merged, secret=SECRET)
    return True


def test_a_malformed_header_is_rejected_rather_than_crashing():
    raw, _ = _signed(_event())
    for header in ("", "garbage", "t=", "v1=abc", "t=notanumber,v1=abc",
                   "t=1,v1"):
        try:
            verify_signature(raw, header, secret=SECRET)
        except SignatureError:
            continue
        raise AssertionError(f"accepted malformed header {header!r}")
    return True


def test_an_unset_secret_rejects_everything_rather_than_trusting():
    """Fail closed. The alternative is a public grant endpoint."""
    raw, header = _signed(_event())
    was = os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    try:
        verify_signature(raw, header)
    except NotImplementedError:
        return True
    except SignatureError:
        return True
    finally:
        if was is not None:
            os.environ["STRIPE_WEBHOOK_SECRET"] = was
    raise AssertionError("an unconfigured endpoint accepted an event")


def test_a_completed_checkout_grants_pro_until_the_period_end():
    acc = _Accounts()
    ends = time.time() + 30 * 86400
    ident = apply_webhook(_event(ends=ends), acc)
    assert ident == "a@b.com", ident
    assert acc.calls == [("a@b.com", "pro", ends)], acc.calls
    return True


def test_a_cancellation_returns_the_account_to_free_and_keeps_it():
    """Lapsed card, not deleted account. Their history has to survive."""
    acc = _Accounts()
    apply_webhook(_event("customer.subscription.deleted"), acc)
    assert acc.calls == [("a@b.com", "free", None)], acc.calls
    return True


def test_an_event_without_an_account_reference_grants_nothing():
    acc = _Accounts()
    event = {"type": "checkout.session.completed", "data": {"object": {}}}
    assert apply_webhook(event, acc) is None
    assert acc.calls == [], acc.calls
    return True


# ---------------------------------------------------------------- the route

class _FakeHandler:
    """Enough of Handler to drive _stripe_webhook without a socket."""

    def __init__(self, raw: bytes, header: str):
        from api.server import Handler
        self.headers = {"Content-Length": str(len(raw)),
                        "Stripe-Signature": header}
        self.rfile = io.BytesIO(raw)
        self.sent = None
        self._stripe_webhook = Handler._stripe_webhook.__get__(self)

    def _send(self, payload, status=200):
        self.sent = (payload, status)


def _post(raw: bytes, header: str) -> tuple:
    was = os.environ.get("STRIPE_WEBHOOK_SECRET")
    os.environ["STRIPE_WEBHOOK_SECRET"] = SECRET
    try:
        h = _FakeHandler(raw, header)
        h._stripe_webhook()
        return h.sent
    finally:
        if was is None:
            os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        else:
            os.environ["STRIPE_WEBHOOK_SECRET"] = was


def test_the_route_rejects_a_forged_delivery_with_400():
    raw, header = _signed(_event())
    payload, status = _post(raw + b" ", header)
    assert status == 400, (payload, status)
    return True


def test_the_route_acknowledges_an_event_type_it_does_not_act_on():
    """Otherwise Stripe retries it for days and disables the endpoint."""
    raw, header = _signed(_event("invoice.created"))
    payload, status = _post(raw, header)
    assert status == 200, (payload, status)
    assert payload["applied"] is False, payload
    return True


def test_the_route_never_reads_an_unbounded_body():
    raw, header = _signed({}, body=b"x" * (512 * 1024 + 1))
    payload, status = _post(raw, header)
    assert status == 400, (payload, status)
    return True


def test_the_webhook_bypasses_the_auth_gate_deliberately_and_only_it():
    """The gate is skipped for exactly one route, and it is this one.

    A second `return` added above `_gate` later would be an unauthenticated
    endpoint that nobody reviewed, so the shape is pinned here.
    """
    import inspect
    import re

    from api.server import Handler

    src = inspect.getsource(Handler.do_POST)
    before = src.split("self._gate(", 1)[0]
    routes = re.findall(r'route == "(/api/[^"]+)"', before)
    assert routes == ["/api/billing/webhook"], routes

    # And it is not quietly listed as open, which would let a future route
    # inherit the exemption without the signature check.
    gate = inspect.getsource(Handler._gate) + inspect.getsource(Handler)
    assert '"/api/billing/webhook"' not in gate.split("def do_POST")[0], \
        "the webhook is listed in OPEN/FREE; its credential is the signature"
    return True


# ------------------------------------------------- the two landing pages
#
#   THE SILENT SUCCESS    the customer's browser lands here straight from
#                         Stripe carrying no session token. If these paths
#                         are not open, a paying customer's last screen is
#                         a 401 -- the bug these pages were written to fix,
#                         so it gets a test rather than a comment.
#
#   THE FALSE FAILURE     `session_paid` answers None when Stripe cannot be
#                         reached. Reading None as "not paid" tells somebody
#                         whose card was just charged that nothing happened,
#                         and the obvious thing they do next is pay again.
#
#   THE UNCHECKED ID      the session id arrives in a query string and is
#                         interpolated into a URL we then call with our
#                         secret key attached.

def test_the_landing_pages_need_no_session_token():
    from api.server import Handler

    for path in ("/billing/done", "/billing/cancelled"):
        assert path in Handler.OPEN, f"{path} would answer a paying customer 401"
    return True


def test_an_unreachable_stripe_never_reads_as_not_paid():
    import api.billing as billing

    was = billing.session_paid
    billing.session_paid = lambda _: None          # Stripe down / slow
    try:
        state = billing.landing_state("/billing/done", "cs_whatever")
        assert state == billing.PENDING, state
        assert state != billing.CANCELLED, "told a paying customer they were not charged"
    finally:
        billing.session_paid = was
    return True


def test_a_confirmed_payment_says_so_and_a_cancel_says_the_opposite():
    import api.billing as billing

    was = billing.session_paid
    billing.session_paid = lambda _: True
    try:
        assert billing.landing_state("/billing/done", "cs_x") == billing.PAID
        assert billing.landing_state("/billing/cancelled", None) == \
            billing.CANCELLED
    finally:
        billing.session_paid = was
    return True


def test_only_the_paid_page_claims_the_account_is_upgraded():
    """A page that says "you're subscribed" when we do not know is a lie."""
    from api.billing import CANCELLED, PAID, PENDING, landing_html

    assert "already upgraded" in landing_html(PAID)
    for state in (PENDING, CANCELLED):
        assert "already upgraded" not in landing_html(state), state
    # and the one that cannot promise anything says the useful thing instead
    assert "Do not pay again" in landing_html(PENDING)
    return True


def test_a_hostile_session_id_never_reaches_stripe():
    from api.billing import session_paid

    for nasty in ("../../account", "cs_x/../../balance", "", "sk_live_leak",
                  "cs_" + "a" * 500, "cs_x?expand[]=customer"):
        # None without a network call: the regex rejects it before _get runs.
        assert session_paid(nasty) is None, nasty
    return True


def test_the_pages_fetch_nothing_from_the_network():
    """First screen after paying, on a phone, on whatever connection.

    Asserted as "issues no request", not "contains no http" -- the favicon is
    an inline SVG and an SVG carries `xmlns="http://www.w3.org/2000/svg"`,
    which is a namespace NAME that no browser ever fetches. The cruder string
    check failed on it, and loosening it to let that one string through would
    have let a real stylesheet through too. So every src/href is extracted and
    each one has to be a data: URI.
    """
    import re

    from api.billing import CANCELLED, PAID, PENDING, landing_html

    for state in (PAID, PENDING, CANCELLED):
        html = landing_html(state)
        for tag in ("<script", "<img", "<iframe", "@import", "url(http"):
            assert tag not in html, f"{state} page pulls in {tag}"
        for attr, value in re.findall(r'\b(src|href)\s*=\s*"([^"]*)"', html):
            assert value.startswith("data:"), \
                f"{state} page fetches {attr}={value[:60]}"
    return True
