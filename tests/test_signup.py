"""Registration, confirmation, and signing in with a provider.

WHAT THESE GUARD, AND WHY EACH ONE EARNED A TEST

  THE LOCKOUT             `email_verified` defaults True. Every account that
                          existed before the field did was created with no
                          confirmation step; loading them as unverified
                          would lock all of them out at once, silently, on
                          the deploy that added the feature.

  THE HALF-CONFIGURED     requiring confirmation while unable to send it
  MAILER                  turns every registration into a dead account. So
                          the requirement follows the capability: no SMTP
                          settings, no hold.

  THE ENUMERATION         "that email is unconfirmed" told to somebody with
                          the wrong password confirms the account exists. So
                          the password is checked first, and the resend
                          route answers the same sentence whatever the
                          address is.

  THE ONE-SHOT LINK       a confirmation token that keeps working is a login
                          link sitting in a mailbox.

  THE SECOND SIGN-UP      an OAuth sign-in with an email that already has a
                          password account must land in THAT account, not
                          make a second one.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from api import mail
from api.accounts import Accounts, AuthError, username_problem

PW = "correct horse battery staple"


def _acc():
    d = tempfile.mkdtemp()
    return Accounts(Path(d) / "accounts.json"), Path(d) / "accounts.json"


class _Mailbox:
    """Records what would have been sent. Nothing leaves the process."""

    def __init__(self):
        self.sent = []

    def __call__(self, to, subject, text):
        self.sent.append((to, subject, text))


def _with_mailer(fn):
    """Run `fn(mailbox)` with SMTP configured and the sender recorded."""
    box = _Mailbox()
    was = {k: os.environ.get(k) for k in ("SMTP_HOST", "MAIL_FROM")}
    real = mail._sender
    os.environ["SMTP_HOST"], os.environ["MAIL_FROM"] = "smtp.test", "v@test"
    mail._sender = box
    try:
        return fn(box)
    finally:
        mail._sender = real
        for k, v in was.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _without_mailer(fn):
    was = {k: os.environ.pop(k, None) for k in ("SMTP_HOST", "MAIL_FROM")}
    try:
        return fn()
    finally:
        for k, v in was.items():
            if v is not None:
                os.environ[k] = v


# ------------------------------------------------------------- usernames

def test_username_rules_are_what_the_form_says():
    assert username_problem("tim") is not None                # 3 chars
    assert username_problem("timo") is None
    assert username_problem("tim_dimytch") is None
    assert username_problem("4tim") is not None               # starts digit
    assert username_problem("tim@x") is not None              # no '@' ever
    assert username_problem("t" * 25) is not None
    return True


def test_usernames_are_unique_ignoring_case():
    acc, _ = _acc()
    _without_mailer(lambda: acc.register("a@x.com", PW, username="Timo"))
    try:
        _without_mailer(lambda: acc.register("b@x.com", PW, username="timo"))
    except AuthError as e:
        assert "username" in str(e), e
        return True
    raise AssertionError("the same username was registered twice")


def test_emails_are_unique_and_the_message_names_the_reason():
    acc, _ = _acc()
    _without_mailer(lambda: acc.register("Tim@X.com", PW, username="timo"))
    try:
        _without_mailer(lambda: acc.register("tim@x.com", PW, username="other"))
    except AuthError as e:
        assert "email" in str(e), e
        return True
    raise AssertionError("one email registered two accounts")


def test_the_confirm_field_is_checked_on_the_server_too():
    acc, _ = _acc()
    try:
        _without_mailer(lambda: acc.register("a@x.com", PW, username="timo",
                                             confirm=PW + "x"))
    except AuthError as e:
        assert "match" in str(e), e
        return True
    raise AssertionError("mismatched passwords were accepted")


def test_a_username_is_required_for_new_accounts():
    """At the FORM. The store still takes none, for the CLI and for every
    account that predates usernames."""
    acc, _ = _acc()
    _without_mailer(lambda: acc.register("cli@x.com", PW))       # allowed
    try:
        _without_mailer(lambda: acc.register("a@x.com", PW,
                                             require_username=True))
    except AuthError as e:
        assert "username" in str(e), e
        return True
    raise AssertionError("an account was created with no username")


def test_you_can_sign_in_with_either_email_or_username():
    acc, _ = _acc()
    _without_mailer(lambda: acc.register("tim@x.com", PW, username="timo"))
    assert acc.verify("tim@x.com", PW).identifier == "tim@x.com"
    assert acc.verify("TIMO", PW).identifier == "tim@x.com"
    return True


# ---------------------------------------------------------- confirmation

def test_without_a_mailer_accounts_are_born_verified():
    """The alternative is a registration form that produces dead accounts."""
    acc, _ = _acc()
    u = _without_mailer(lambda: acc.register("a@x.com", PW, username="timo"))
    assert u.email_verified is True
    assert u.verify_token is None
    assert acc.verify("a@x.com", PW)          # signs straight in
    return True


def test_with_a_mailer_the_account_waits_for_the_link():
    def go(box):
        acc, _ = _acc()
        u = acc.register("a@x.com", PW, username="timo")
        assert u.email_verified is False
        assert u.verify_token and len(u.verify_token) >= 32
        try:
            acc.verify("a@x.com", PW)
        except AuthError as e:
            assert "confirm" in str(e).lower(), e
        else:
            raise AssertionError("an unconfirmed account signed in")
        acc.confirm_email(u.verify_token)
        assert acc.verify("a@x.com", PW).email_verified is True
        return True
    return _with_mailer(go)


def test_the_link_works_once():
    def go(box):
        acc, _ = _acc()
        u = acc.register("a@x.com", PW, username="timo")
        tok = u.verify_token
        acc.confirm_email(tok)
        try:
            acc.confirm_email(tok)
        except AuthError:
            return True
        raise AssertionError("a used confirmation link still worked")
    return _with_mailer(go)


def test_the_link_expires():
    def go(box):
        acc, _ = _acc()
        u = acc.register("a@x.com", PW, username="timo")
        u.verify_sent_at = time.time() - 25 * 3600
        try:
            acc.confirm_email(u.verify_token)
        except AuthError as e:
            assert "expired" in str(e), e
            return True
        raise AssertionError("a day-old link was accepted")
    return _with_mailer(go)


def test_the_wrong_password_never_reveals_an_unconfirmed_account():
    def go(box):
        acc, _ = _acc()
        acc.register("a@x.com", PW, username="timo")
        try:
            acc.verify("a@x.com", "not the password")
        except AuthError as e:
            assert "confirm" not in str(e).lower(), \
                f"leaked that the account exists: {e}"
            return True
        raise AssertionError("wrong password accepted")
    return _with_mailer(go)


def test_resend_answers_the_same_for_every_address():
    def go(box):
        acc, _ = _acc()
        acc.register("a@x.com", PW, username="timo")
        # unknown, unconfirmed, and (after confirming) confirmed: the
        # ROUTE says the same sentence for all three; here we check the
        # store side gives the caller nothing to distinguish them by
        # except whether a mail should go out
        assert acc.new_verify_token("nobody@x.com") is None
        u = acc.new_verify_token("a@x.com")
        assert u is not None and u.verify_token
        acc.confirm_email(u.verify_token)
        assert acc.new_verify_token("a@x.com") is None
        return True
    return _with_mailer(go)


def test_accounts_from_before_the_feature_still_sign_in():
    """THE LOCKOUT. A stored account with no `email_verified` key at all
    must load as verified."""
    acc, path = _acc()
    _without_mailer(lambda: acc.register("old@x.com", PW, username="oldie"))
    import json
    raw = json.loads(path.read_text())
    for u in (raw if isinstance(raw, list) else raw.get("users", [])):
        for k in ("email_verified", "verify_token", "verify_sent_at",
                  "username", "oauth_provider"):
            u.pop(k, None)
    path.write_text(json.dumps(raw))
    again = Accounts(path)
    u = again.verify("old@x.com", PW)
    assert u.email_verified is True
    assert u.username is None
    return True


def test_the_mail_carries_a_link_and_nothing_secret_beyond_it():
    def go(box):
        acc, _ = _acc()
        u = acc.register("a@x.com", PW, username="timo")
        link = f"https://example/api/auth/verify?token={u.verify_token}"
        assert mail.confirmation("a@x.com", link, "timo") is True
        to, subject, text = box.sent[0]
        assert to == "a@x.com"
        assert link in text
        assert PW not in text
        assert "24 hours" in text
        return True
    return _with_mailer(go)


# ----------------------------------------------------------------- oauth

def _fake_provider(profile, token=None, emails=None):
    """A transport that answers the three calls a sign-in makes."""
    calls = []

    def fetch(url, data=None, headers=None):
        calls.append((url, data))
        if data and "code" in data:
            return token or {"access_token": "at-1"}
        if "emails" in url:
            return emails or []
        return profile
    return fetch, calls


def _oauth_env(fn, provider="google"):
    p = provider.upper()
    keys = {f"OAUTH_{p}_CLIENT_ID": "cid", f"OAUTH_{p}_CLIENT_SECRET": "sec",
            "PUBLIC_BASE_URL": "https://vanth.test"}
    was = {k: os.environ.get(k) for k in keys}
    os.environ.update(keys)
    try:
        return fn()
    finally:
        for k, v in was.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_a_provider_with_no_credentials_is_not_offered():
    from api.oauth import available, unavailable_reason
    for k in list(os.environ):
        if k.startswith("OAUTH_"):
            os.environ.pop(k)
    assert available() == []
    assert "CLIENT_ID" in unavailable_reason("google")
    assert "CLIENT_ID" in unavailable_reason("apple")
    # Apple with every credential present still needs ES256, which the
    # standard library cannot do. The reason has to say so, or the next
    # person spends an hour on "but I set all the variables".
    keys = {"OAUTH_APPLE_CLIENT_ID": "x", "OAUTH_APPLE_TEAM_ID": "t",
            "OAUTH_APPLE_KEY_ID": "k", "OAUTH_APPLE_PRIVATE_KEY": "p",
            "PUBLIC_BASE_URL": "https://v.test"}
    os.environ.update(keys)
    try:
        import cryptography  # noqa: F401
        assert unavailable_reason("apple") is None
    except ImportError:
        assert "cryptography" in unavailable_reason("apple")
    finally:
        for k in keys:
            os.environ.pop(k, None)
    return True


def test_google_sign_in_end_to_end_with_a_fake_transport():
    def go():
        from api.oauth import Flow
        fetch, calls = _fake_provider(
            {"sub": "g1", "email": "Tim@Gmail.com", "email_verified": True,
             "name": "Tim D"})
        acc, _ = _acc()
        flow = Flow(fetch=fetch)
        device = "device-nonce-0123456789abcdef"
        url = flow.start("google", device)
        assert url.startswith("https://accounts.google.com/")
        assert "code_challenge=" in url and "state=" in url
        state = dict(__import__("urllib.parse").parse.parse_qsl(
            url.split("?", 1)[1]))["state"]

        assert flow.poll(device) is None            # browser still busy
        pub = flow.callback("google", "the-code", state, acc)
        assert pub["identifier"] == "tim@gmail.com"
        assert pub["email_verified"] is True
        assert pub["username"] == "Tim_D"
        # PKCE: the verifier went back with the code
        assert any(d and "code_verifier" in d for _, d in calls)

        tok = flow.poll(device)
        assert tok, "no session was filed for the phone"
        assert flow.poll(device) is None, "the token was handed out twice"
        return True
    return _oauth_env(go)


def test_an_oauth_email_that_already_has_an_account_lands_in_it():
    """THE SECOND SIGN-UP."""
    def go():
        from api.oauth import Flow
        acc, _ = _acc()
        _without_mailer(lambda: acc.register("tim@x.com", PW, username="timo"))
        fetch, _ = _fake_provider(
            {"sub": "g1", "email": "tim@x.com", "email_verified": True})
        flow = Flow(fetch=fetch)
        url = flow.start("google", "device-nonce-0123456789abcdef")
        state = dict(__import__("urllib.parse").parse.parse_qsl(
            url.split("?", 1)[1]))["state"]
        pub = flow.callback("google", "c", state, acc)
        assert pub["username"] == "timo", "a second account was created"
        assert len(acc._users) == 1
        # and the password still works: linking did not replace it
        assert acc.verify("timo", PW)
        return True
    return _oauth_env(go)


def test_an_unverified_or_missing_email_is_refused():
    def go():
        from api.oauth import Flow, OAuthError
        acc, _ = _acc()
        for profile in ({"sub": "1", "email": "a@x.com", "email_verified": False},
                        {"sub": "2"}):
            fetch, _ = _fake_provider(profile)
            flow = Flow(fetch=fetch)
            url = flow.start("google", "device-nonce-0123456789abcdef")
            state = dict(__import__("urllib.parse").parse.parse_qsl(
                url.split("?", 1)[1]))["state"]
            try:
                flow.callback("google", "c", state, acc)
            except OAuthError:
                continue
            raise AssertionError(f"accepted {profile}")
        assert acc._users == {}
        return True
    return _oauth_env(go)


def test_a_replayed_or_forged_state_is_refused():
    def go():
        from api.oauth import Flow, OAuthError
        acc, _ = _acc()
        fetch, _ = _fake_provider(
            {"sub": "1", "email": "a@x.com", "email_verified": True})
        flow = Flow(fetch=fetch)
        url = flow.start("google", "device-nonce-0123456789abcdef")
        state = dict(__import__("urllib.parse").parse.parse_qsl(
            url.split("?", 1)[1]))["state"]
        flow.callback("google", "c", state, acc)
        for bad in (state, "forged", ""):
            try:
                flow.callback("google", "c", bad, acc)
            except OAuthError:
                continue
            raise AssertionError(f"state {bad!r} was accepted")
        return True
    return _oauth_env(go)


def test_github_uses_the_verified_primary_email_not_the_public_one():
    def go():
        from api.oauth import Flow
        acc, _ = _acc()
        fetch, _ = _fake_provider(
            {"id": 7, "login": "timd", "email": None, "name": None},
            emails=[{"email": "old@x.com", "verified": True, "primary": False},
                    {"email": "tim@x.com", "verified": True, "primary": True},
                    {"email": "spoof@x.com", "verified": False, "primary": False}])
        flow = Flow(fetch=fetch)
        url = flow.start("github", "device-nonce-0123456789abcdef")
        state = dict(__import__("urllib.parse").parse.parse_qsl(
            url.split("?", 1)[1]))["state"]
        pub = flow.callback("github", "c", state, acc)
        assert pub["identifier"] == "tim@x.com", pub
        assert pub["username"] == "timd"
        return True
    return _oauth_env(go, "github")


def test_an_oauth_account_cannot_be_signed_into_with_a_password():
    acc, _ = _acc()
    u = acc.register_external("a@x.com", provider="google", display_name="A B")
    assert u.oauth_provider == "google" and u.email_verified
    for guess in ("", "password", "a@x.com", u.hash):
        try:
            acc.verify("a@x.com", guess)
        except AuthError:
            continue
        raise AssertionError(f"password {guess!r} opened an OAuth account")
    return True
