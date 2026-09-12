"""Outbound email. One job: the "confirm your address" message.

CONFIGURED OR ABSENT, NEVER HALF-WAY
    `configured()` is the single question every caller asks. When it is
    False -- no SMTP settings in .env -- accounts are created already
    verified and no mail is attempted. When True, a new account is held
    unverified until the link in the message is opened.

    That switch is load-bearing. Requiring confirmation while unable to send
    the confirmation would lock every new account out at the door, which is
    the outage a half-configured mailer produces. So the requirement follows
    the capability, and the log says which mode it is in at startup.

WHY SMTP AND NOT A PROVIDER SDK
    Any provider -- Gmail with an app password, Resend, Postmark, SES --
    speaks SMTP, and smtplib is in the standard library. A provider SDK would
    tie the image to one vendor for a message this short.

TESTS NEVER SEND
    `_sender` is swapped for a recorder in tests. Every message in the suite
    is asserted against, none leave the process.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Callable, Optional

log = logging.getLogger(__name__)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def configured() -> bool:
    return bool(_env("SMTP_HOST") and _env("MAIL_FROM"))


def _smtp_send(to: str, subject: str, text: str) -> None:
    host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT") or 587)
    user, password = _env("SMTP_USER"), _env("SMTP_PASS")
    sender = _env("MAIL_FROM")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)

    # 465 is implicit TLS; anything else is expected to offer STARTTLS. A
    # plain-text session is refused rather than attempted: the message
    # carries a token that grants access to the account.
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        if user:
            s.login(user, password)
        s.send_message(msg)


_sender: Callable[[str, str, str], None] = _smtp_send


def send(to: str, subject: str, text: str) -> bool:
    """True if handed to the mail server. Never raises: a registration must
    not fail because the confirmation could not be sent -- the account
    exists, and `resend` is one tap away."""
    if not configured():
        log.warning("mail not configured; would have sent %r to %s",
                    subject, to)
        return False
    try:
        _sender(to, subject, text)
        return True
    except Exception as e:
        log.error("mail to %s failed: %s", to, e)
        return False


def confirmation(to: str, link: str, username: Optional[str] = None) -> bool:
    who = f" {username}" if username else ""
    text = (
        f"Hi{who},\n\n"
        "Open this link to confirm your Vanth account:\n\n"
        f"    {link}\n\n"
        "The link works once and expires in 24 hours. If you did not create "
        "an account, ignore this message and nothing will happen.\n\n"
        "Vanth places no orders and holds no exchange keys.\n"
    )
    return send(to, "Confirm your Vanth account", text)
