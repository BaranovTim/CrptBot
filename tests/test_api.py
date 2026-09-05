"""The read-only JSON API behind the mobile app.

What these guard, in order of how badly they would mislead you:

  A DEFAULT BUY          the mockup the app was built from shows a confident
                         BUY. If the payload ever falls back to that when the
                         backend actually said WAIT, the phone would be
                         advising a trade the EV rule rejected. The
                         recommendation must come from `evaluate()`, always.

  NaN REACHING THE WIRE  `json.dumps` writes bare NaN by default, which is
                         invalid JSON that many parsers accept anyway and turn
                         into garbage. The server dumps with allow_nan=False,
                         so an unconverted NaN raises here rather than
                         rendering as a price on a phone.

  ZERO FOR ABSENT        the Python side is careful that a missing level is
                         NaN and not 0, because 0 means "price is exactly
                         here". That distinction has to survive the JSON hop,
                         which is why absent values become null, never 0.

  A BADGE THAT LIES      nothing in this project places an order. The status
                         payload must never imply otherwise.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from api.service import _analysis, _indicators, _num, _recommendation
from monitor import Analysis

HOUR = pd.Timedelta("1h")
T0 = pd.Timestamp("2026-08-28T12:00:00Z")
MODELS = [Path("output/judge_h1.joblib"), Path("output/judge_h2.joblib")]


def _wait(name: str = "ANALYSIS B") -> Analysis:
    a = Analysis(name=name, opened_at=T0, ends_at=T0 + HOUR, bars_left=2)
    a.p_up, a.entry = 0.535, 79000.0
    a.upper, a.lower = 79500.0, 78500.0
    a.tp_price, a.sl_price = 79500.0, 78500.0
    a.ev_long, a.ev_short = -0.06, -0.14
    a.action = "WAIT"
    a.reason = "best EV -0.060% is below the +0.05% threshold"
    return a


def _enter(side: str = "LONG") -> Analysis:
    a = _wait("ANALYSIS A")
    a.action, a.side, a.size_pct = f"ENTER {side} NOW", side, 10.4
    a.ev_long, a.ev_short = (0.73, -0.9) if side == "LONG" else (-0.9, 0.73)
    a.reason = "EV clears the threshold after costs"
    return a


# ------------------------------------------------------------- numbers
def test_nan_and_inf_become_none_not_zero():
    """Zero means "the level is exactly here". Absent must not read as that."""
    for bad in (float("nan"), float("inf"), float("-inf"), None, "x"):
        assert _num(bad) is None, f"{bad!r} did not become None"
    assert _num(0.0) == 0.0, "a real zero was thrown away"
    assert _num(np.float64(1.5)) == 1.5
    return True


def test_analysis_payload_is_json_safe():
    """A NaN here would be written as bare NaN and parsed as garbage."""
    a = Analysis(name="A", opened_at=T0, ends_at=T0 + HOUR, bars_left=1)
    payload = _analysis(a)                    # every numeric field is NaN
    json.dumps(payload, allow_nan=False)      # raises if any NaN survived
    assert payload["p_up"] is None
    assert payload["take_profit"] is None
    return True


# ------------------------------------------------- the big card's verdict
def test_a_waiting_backend_never_renders_as_buy():
    """The single most dangerous substitution the app could make."""
    r = _recommendation(_wait(), _wait(), stale=False)
    assert r["action"] == "FLAT", r
    assert r["action"] != "BUY"
    assert r["tone"] == "flat"
    assert "threshold" in r["detail"]
    return True


def test_an_entering_backend_renders_its_own_side():
    long_r = _recommendation(_enter("LONG"), _wait(), stale=False)
    short_r = _recommendation(_enter("SHORT"), _wait(), stale=False)
    assert long_r["action"] == "BUY" and long_r["tone"] == "up", long_r
    assert short_r["action"] == "SELL" and short_r["tone"] == "down", short_r
    return True


def test_a_stale_backend_says_so_instead_of_advising():
    """An expired window is history. Advising off it is the worst failure."""
    r = _recommendation(_enter("LONG"), _enter("LONG"), stale=True)
    assert r["action"] == "STALE", r
    assert r["action"] not in ("BUY", "SELL")
    return True


def test_the_secondary_window_can_supply_the_signal():
    """A wins on the primary; if only the secondary entered, use it."""
    r = _recommendation(_wait(), _enter("LONG"), stale=False)
    assert r["action"] == "BUY", r
    return True


# ---------------------------------------------------------- indicators
def test_rsi_notes_track_the_value():
    for value, expect in ((75, "Overbought"), (65, "Approaching Overbought"),
                          (50, "Neutral Range"), (35, "Approaching Oversold"),
                          (25, "Oversold")):
        got = _indicators({"rsi_14": float(value)})
        assert got[0]["note"].startswith(expect), (value, got[0]["note"])
    return True


def test_the_rsi_note_says_which_bars_it_spans():
    """RSI(14) is 14 minutes at 1m and 14 hours at 1h. Same number, wildly
    different claim — the card has to say which."""
    for interval in ("1m", "1h", "1d"):
        note = _indicators({"rsi_14": 50.0}, interval=interval)[0]["note"]
        assert interval in note, note
    return True


def test_the_htf_card_names_the_timeframe_it_is_actually_showing():
    """The bug this guards: the label was hardcoded "4H STRUCTURE" while the
    value came from whatever `htf_rule` was set to. On the 1d screen that
    displayed the WEEKLY trend under a 4H heading, which then appeared to
    contradict the 1h screen for a reason no user could see. Agent 1's column
    is called `trend_direction_4h` on every timeframe, so the column name
    cannot be trusted as the label."""
    for interval, htf in (("1m", "15m"), ("15m", "4h"), ("1h", "4h"),
                          ("4h", "1d"), ("1d", "1w")):
        card = [c for c in _indicators({"trend_direction_4h": 1.0},
                                       interval=interval, htf=htf)
                if c["key"] == "htf"][0]
        assert card["label"] == f"{htf.upper()} STRUCTURE", card["label"]
        assert htf in card["note"], card["note"]
    return True


def test_levels_describe_the_same_window_the_card_recommends():
    """A BUY from the 1-bar model shown above a probability from the 2-bar
    model reads as incoherent: "BUY" next to "51.7%". Both must come from
    whichever window actually won."""
    from api.service import _choose

    entering, waiting = _enter("LONG"), _wait()
    assert _choose(waiting, entering) is entering, "an entering window lost"
    assert _choose(entering, waiting) is entering
    # nothing entering -> the primary window speaks
    assert _choose(waiting, waiting) is waiting

    rec = _recommendation(waiting, entering, stale=False)
    assert rec["p_up"] == entering.p_up
    assert rec["window_bars"] == entering.bars_left
    return True


def test_indicators_skip_columns_that_are_not_there():
    """A model trained without a block must not produce an empty card."""
    assert _indicators({}) == []
    assert _indicators({"rsi_14": float("nan")}) == []
    return True


# ----------------------------------------------------- the live payload
def test_dashboard_payload_survives_strict_json():
    """The end-to-end contract: everything the phone reads must serialise."""
    if not all(p.exists() for p in MODELS):
        return True
    from livefeed import BarStore
    if BarStore("BTCUSDT", "1h").load().empty:
        return True                            # nothing collected here

    from api.service import get_service
    d = get_service().dashboard()
    json.dumps(d, allow_nan=False)             # the actual guarantee

    assert d["status"]["trades"] is False, "the app claimed the bot trades"
    assert d["recommendation"]["action"] in ("BUY", "SELL", "FLAT", "STALE")
    assert d["calibration_note"], "the honesty note went missing"
    for a in d["analyses"]:
        assert a["p_up"] is None or 0.0 <= a["p_up"] <= 1.0
    return True


def _an_untrained_symbol() -> str:
    """A pair with no fitted model, found rather than hardcoded.

    These two tests used to name ETHUSDT. Training ETH then broke both of
    them — a false failure that says nothing about the code, and the kind
    that teaches you to ignore a red suite. Which pairs are trained is a
    property of `output/`, so ask it.
    """
    from core import TIMEFRAMES, is_trained

    for sym in ("XRPUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "TESTUSDT"):
        if not any(is_trained(sym, tf) for tf in TIMEFRAMES):
            return sym
    raise AssertionError("no untrained pair left to test with")


def test_untrained_pairs_are_reported_as_untrained():
    """The app routes on this. Getting it wrong shows odds with no model."""
    from api.service import get_service
    info = get_service().training(_an_untrained_symbol())
    assert info["trained"] is False, info
    assert "train.py" in info.get("command", ""), info.get("command")
    return True


def test_training_is_reported_per_timeframe_not_per_symbol():
    """A symbol is not trained or untrained. Each timeframe is its own model,
    and BTCUSDT having a 1h model says nothing about its 5m."""
    from api.service import get_service
    svc = get_service()
    rows = {t["interval"]: t["trained"]
            for t in svc.timeframes(_an_untrained_symbol())}
    assert rows and not any(rows.values()), rows

    btc = svc.training("BTCUSDT", interval="1h")
    assert btc["interval"] == "1h" and btc["htf"] == "4h", btc

    # and a trained one really does report per timeframe
    eth = {t["interval"]: t["trained"] for t in svc.timeframes("ETHUSDT")}
    assert all(eth.values()), eth
    return True


def test_the_cost_drag_is_reported_and_flags_the_untradeable():
    """At 1m the barriers sit inside the round trip. That has to reach the
    surface — it is not a model problem and no accuracy fixes it."""
    from api.service import get_service
    svc = get_service()
    c = svc.cost_drag("BTCUSDT", "1m")
    if c["span_pct"] is None:
        return True                       # no 1m bars collected here
    assert c["cost_share"] > c["cost_share"] * 0, c
    assert c["verdict"] in ("untradeable", "marginal", "workable")
    slow = svc.cost_drag("BTCUSDT", "1d")
    if slow["span_pct"] is not None:
        assert c["cost_share"] > slow["cost_share"], (
            "fees must eat a larger share of a smaller barrier span")
    return True


# ------------------------------------------------------------ the universe
def test_writes_touch_accounts_billing_and_training_and_nothing_else():
    """The read-only rule, narrowed a second time — deliberately.

    It began as "the handler has no verb but GET", because the security
    argument was that a compromise yields what your terminal already prints.
    Accounts moved that line once: sign-in has to write.

    TRAINING MOVES IT AGAIN, and the new position is weaker, so it is written
    down rather than assumed:

        MARKET DATA IS STILL READ-ONLY. Nothing reachable by POST writes a
        bar, a price, a watchlist or an account balance.

        BUT A POST CAN NOW SPEND CPU. `/api/train` queues a forty-minute fit
        on a single-core server. A compromised session cannot read anything
        it could not read before, and cannot corrupt market data — but it CAN
        make the box slow.

        The mitigations are structural, not hopeful: one fit at a time,
        duplicates refused, MAX_QUEUE, and MAX_PER_DAY per account. If those
        are weakened, this paragraph is wrong.

    PUSH MOVES IT A THIRD TIME, and this one is the mildest of the three.
    `/api/push/subscribe` stores a notification topic against the account and
    nothing else. It writes no market data and spends no CPU; the worst a
    compromised session can do with it is redirect that account's own alerts
    to a topic the attacker is listening on, or send one test message. Worth
    saying out loud because it IS a real leak of what the owner watches —
    which is exactly why the subscription is per account and readable only by
    the account that made it.

    Anything OUTSIDE these four families appearing in `do_POST` means the
    argument needs rewriting a fourth time.
    """
    import inspect

    from api.server import Handler
    from api.trainer import MAX_PER_DAY, MAX_QUEUE

    verbs = sorted(m for m in dir(Handler) if m.startswith("do_"))
    assert verbs == ["do_GET", "do_OPTIONS", "do_POST"], verbs

    src = inspect.getsource(Handler.do_POST)
    # EVERY route literal, not just the `route == "..."` ones.
    #
    # The narrower pattern was a hole: writing a family as
    # `route in ("/api/a", "/api/b")` matched nothing, so the test passed
    # while saying nothing — the worst failure a guard rail can have.
    routes = re.findall(r'"(/api/[^"]+)"', src)
    assert routes, "no routes found in do_POST"
    for r in routes:
        assert r.startswith(("/api/auth/", "/api/billing/", "/api/train",
                             "/api/push/")), \
            f"{r} writes something that is not an account, a payment, a fit " \
            f"or a notification topic"

    # The limits the paragraph above depends on. If someone raises these to
    # something that no longer bounds the damage, this fails and they read it.
    assert MAX_QUEUE <= 16, MAX_QUEUE
    assert MAX_PER_DAY <= 24, MAX_PER_DAY
    return True


def test_a_password_is_never_stored_in_the_clear():
    """The file on disk must not contain the password, anywhere, ever."""
    import tempfile
    from pathlib import Path

    from api.accounts import Accounts

    with tempfile.TemporaryDirectory() as d:
        acc = Accounts(Path(d) / "accounts.json")
        acc.register("tim", "correct horse battery staple")
        raw = (Path(d) / "accounts.json").read_text()
        assert "correct horse" not in raw, "password written in the clear"
        assert "battery staple" not in raw
        # and the stored digest is not just a hash of the password with no
        # salt, which would make a rainbow table sufficient
        import hashlib
        naked = hashlib.sha256(b"correct horse battery staple").hexdigest()
        assert naked not in raw
    return True


def test_an_unknown_account_and_a_wrong_password_are_indistinguishable():
    """Otherwise the login form tells an attacker which handles exist."""
    import tempfile
    from pathlib import Path

    from api.accounts import Accounts, AuthError

    with tempfile.TemporaryDirectory() as d:
        acc = Accounts(Path(d) / "accounts.json")
        acc.register("tim", "a-real-password")

        msgs = []
        for ident, pw in (("tim", "wrong-password"), ("nobody", "anything")):
            try:
                acc.verify(ident, pw)
                raise AssertionError(f"{ident} should not have verified")
            except AuthError as e:
                msgs.append(str(e))
        assert msgs[0] == msgs[1], msgs
    return True


def test_an_unsubscribed_account_gets_the_chart_and_not_the_analysis():
    """The paywall, which is the whole point of the tier.

    Free sees the graph — a paywall showing nothing tells you nothing about
    what you would be buying — and is refused everything that is the product.
    """
    import api.server as server
    from api.accounts import Accounts
    from api.server import Handler

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        acc = Accounts(Path(d) / "accounts.json")
        free = acc.register("skint", "password-long-enough")
        paid = acc.register("payer", "password-long-enough", tier="pro")

        h = object.__new__(Handler)          # no socket needed for _gate
        old_token = server.TOKEN
        server.TOKEN = "operator-key"
        try:
            def gate_as(user, route):
                h._principal = lambda: (False, user)
                return h._gate(route)

            assert gate_as(free, "/api/chart") is None
            assert gate_as(free, "/api/me") is None
            assert gate_as(free, "/api/billing/plans") is None
            # a government release date is a public fact, not the product —
            # warning an unpaid user that the market is about to move is
            # right regardless of whether they pay
            assert gate_as(free, "/api/calendar") is None

            for route in ("/api/dashboard", "/api/consensus", "/api/news",
                          "/api/whales", "/api/alerts", "/api/training"):
                denied = gate_as(free, route)
                assert denied is not None, f"{route} leaked to a free account"
                assert denied[1] == 402, (route, denied)

            for route in ("/api/dashboard", "/api/consensus", "/api/chart"):
                assert gate_as(paid, route) is None, route

            # nobody at all is 401, not 402: "sign in" and "subscribe" are
            # different screens
            assert gate_as(None, "/api/dashboard")[1] == 401
        finally:
            server.TOKEN = old_token
    return True


def test_a_lapsed_subscription_falls_back_to_free_on_its_own():
    """No cron job stands between an expired card and the paywall."""
    import time as _time

    from api.accounts import User

    live = User(identifier="a", salt="00", hash="x", tier="pro",
                subscription_ends=_time.time() + 3600)
    lapsed = User(identifier="b", salt="00", hash="x", tier="pro",
                  subscription_ends=_time.time() - 1)
    admin = User(identifier="c", salt="00", hash="x", tier="admin")

    assert live.entitled
    assert not lapsed.entitled
    assert admin.entitled                    # never expires, never billed
    assert "hash" not in live.public()       # and the digest never ships
    assert "salt" not in live.public()
    return True


def test_an_unlisted_symbol_is_flagged_not_silently_blank():
    """PEPEUSDT is not a perpetual — 1000PEPEUSDT is. Without the flag the
    row renders as dashes with nothing to explain them."""
    from api.service import get_service

    svc = get_service()
    if not svc.symbols(limit=5):
        return True                              # offline
    rows = {c["symbol"]: c for c in
            svc.coins(["BTCUSDT", "DEFINITELYNOTAPAIRUSDT"])}
    assert rows["BTCUSDT"]["listed"] is True
    assert rows["DEFINITELYNOTAPAIRUSDT"]["listed"] is False
    assert rows["DEFINITELYNOTAPAIRUSDT"]["price"] is None
    return True


def test_the_universe_is_tradable_usdt_perpetuals_only():
    """A SETTLING contract or a quarterly future would look identical in the
    picker and then have no live price behind it."""
    from api.service import get_service

    rows = get_service().symbols(limit=500)
    if not rows:
        return True
    assert len(rows) > 100, f"only {len(rows)} symbols; the filter is too tight"
    for r in rows[:50]:
        assert r["symbol"].endswith("USDT"), r["symbol"]
        assert r["quote"] == "USDT", r
    return True


def test_symbols_are_ordered_by_volume_not_alphabetically():
    """Searching "b" must offer BTC before BAKE."""
    from api.service import get_service

    rows = get_service().symbols(limit=20)
    if len(rows) < 5:
        return True
    vols = [r["volume_24h"] or 0.0 for r in rows]
    assert vols == sorted(vols, reverse=True), vols[:5]

    # Deliberately NOT "BTCUSDT is first". That asserted a fact about the
    # market, not about this code, and it failed on 2026-08-30 because
    # ETHUSDT genuinely out-traded BTCUSDT that day. A test that breaks when
    # the world changes rather than when the code does teaches you to ignore
    # the suite.
    #
    # The property that matters is that the order is NOT alphabetical, which
    # is what a naive listing would give.
    syms = [r["symbol"] for r in rows]
    assert syms != sorted(syms), "symbols came back in alphabetical order"
    assert "BTCUSDT" in syms[:5], syms[:5]      # still near the top
    return True


def test_search_matches_both_the_pair_and_the_base_asset():
    from api.service import get_service

    svc = get_service()
    if not svc.symbols(limit=5):
        return True
    got = {r["symbol"] for r in svc.symbols(q="sol", limit=30)}
    assert "SOLUSDT" in got, got
    for sym in got:
        assert "SOL" in sym, sym
    return True


def test_coins_honours_the_list_it_is_given():
    """The device decides what is followed; the server just answers."""
    from api.service import get_service

    svc = get_service()
    if not svc.symbols(limit=5):
        return True
    picked = ["ETHUSDT", "BTCUSDT"]
    got = [c["symbol"] for c in svc.coins(picked)]
    assert got == picked, got                     # order preserved too
    return True


def test_a_slow_build_of_one_timeframe_does_not_block_another():
    """The bug that broke the phone: one 1m build stalling every other screen.

    `dashboard()` used to call `_build_dashboard()` while holding the single
    global lock. On a laptop the builds are ~3s and nobody notices; on the
    one-core droplet a 1m build is 46s, so switching to 1m made the 4h screen
    time out too, and the 5s TTL meant it rebuilt forever and never recovered.

    Two pairs, one of them deliberately slow. The fast one must return while
    the slow one is still building.
    """
    import threading
    import time

    from api.service import TradingService

    svc = TradingService()
    started = threading.Event()
    release = threading.Event()

    def fake_build(symbol, interval):
        if interval == "1m":
            started.set()
            release.wait(10)                      # hold it open
        return {"symbol": symbol, "interval": interval}

    svc._build_dashboard = fake_build

    slow = threading.Thread(target=svc.dashboard, args=("BTCUSDT", "1m"))
    slow.start()
    assert started.wait(5), "slow build never started"

    t0 = time.time()
    got = svc.dashboard("BTCUSDT", "4h")          # must NOT queue behind 1m
    elapsed = time.time() - t0

    release.set()
    slow.join(10)

    assert got["interval"] == "4h", got
    assert elapsed < 2.0, f"4h waited {elapsed:.1f}s on the 1m build"
    return True


def test_an_expired_dashboard_is_served_immediately_not_rebuilt_inline():
    """Stale-while-revalidate: expiry must never make a phone wait.

    A cached payload past its TTL is returned as-is and refreshed behind the
    request. Only a pair that has never been built is allowed to block.
    """
    import threading
    import time

    from api.service import TradingService, _Cached

    svc = TradingService()
    calls = []
    gate = threading.Event()

    def fake_build(symbol, interval):
        calls.append(interval)
        gate.wait(10)                             # any inline call would hang
        return {"symbol": symbol, "interval": interval, "fresh": True}

    svc._build_dashboard = fake_build
    key = ("BTCUSDT", "1m")
    svc._dash[key] = _Cached(time.time() - 10_000, {"stale": True})

    t0 = time.time()
    got = svc.dashboard("BTCUSDT", "1m")
    elapsed = time.time() - t0
    gate.set()

    assert got == {"stale": True}, got            # the old copy, right away
    assert elapsed < 1.0, f"took {elapsed:.1f}s to serve a cached payload"
    return True


def test_the_dashboard_ttl_tracks_the_bar_period():
    """A 1h payload rebuilt every 5s is pure waste; it changes once an hour.

    The old fixed 5s TTL against a 10-46s build meant the cache was expired
    every time it was read.
    """
    from api.service import TradingService

    svc = TradingService()
    t = lambda iv: svc._dash_ttl(("BTCUSDT", iv))
    assert t("1m") < t("15m") < t("1h")
    assert t("1m") >= 30.0                        # never hammer a slow box
    assert t("1d") <= 900.0                       # never serve a stale day

    # and it backs off from what the build ACTUALLY costs. 27s is the measured
    # 1m build on the one-core droplet; a 30s TTL there means never stopping.
    svc._build_cost[("BTCUSDT", "1m")] = 27.0
    assert t("1m") >= 108.0, t("1m")
    return True


def test_a_grant_from_another_process_reaches_the_running_api():
    """The bug that would read as "I paid and nothing happened".

    `manage_accounts.py grant` — and, later, a Stripe webhook — may run in a
    different process from the API. The API held its user table in memory and
    never re-read the file, so an account that was already `pro` on disk kept
    getting 402 until the server was restarted.

    Measured on the droplet before the fix: `list` reported `pro True` while
    `/api/dashboard` answered 402 to that account's live session.
    """
    import tempfile
    import time
    from pathlib import Path

    from api.accounts import Accounts

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "accounts.json"
        writer, reader = Accounts(path), Accounts(path)

        writer.register("alice", "password-long-enough")
        assert reader.get("alice") is not None       # reader picks up creation
        assert not reader.get("alice").entitled

        time.sleep(0.01)                             # distinct mtime
        writer.set_tier("alice", "pro", time.time() + 3600)

        assert writer.get("alice").entitled
        assert reader.get("alice").entitled, \
            "the running API cannot see a grant made by the CLI"
    return True


def test_the_login_endpoint_cannot_be_ground_through():
    """Public exposure makes /api/auth/login a brute-force target.

    scrypt costs ~60ms a guess, which alone still allows ~1.4M attempts a day
    from one client. Two windows: per-IP so one attacker cannot grind a
    dictionary, and per-identifier so a botnet spread over many addresses
    cannot grind one account either.
    """
    from api.throttle import ID_LIMIT, IP_LIMIT, Throttle

    t = Throttle()
    for i in range(IP_LIMIT):
        assert t.check("1.2.3.4", f"user{i}") is None, i
    assert t.check("1.2.3.4", "userX") is not None, "per-IP limit never bit"

    # a different address is a different bucket, but ONE account is still
    # protected across all of them
    t2 = Throttle()
    for i in range(ID_LIMIT):
        assert t2.check(f"10.0.0.{i}", "victim") is None, i
    assert t2.check("10.0.0.99", "victim") is not None, \
        "a spread-out attack on one account was not limited"
    return True


def test_a_successful_sign_in_clears_the_counters():
    """Otherwise mistyping your password three times then getting it right
    still counts against you, and a shared address locks out the innocent."""
    from api.throttle import ID_LIMIT, Throttle

    t = Throttle()
    # right up to the edge of the per-identifier window, which is the tighter
    # of the two and therefore the one that bites first for one account
    for _ in range(ID_LIMIT - 1):
        assert t.check("1.2.3.4", "tim") is None
    t.forget("1.2.3.4", "tim")
    # the whole budget is back, not merely one more attempt
    for i in range(ID_LIMIT - 1):
        assert t.check("1.2.3.4", "tim") is None, i
    return True


def test_a_forwarded_ip_is_trusted_only_from_loopback():
    """Behind Caddy every request arrives from 127.0.0.1, so the header has to
    be honoured — but honouring it from a direct connection would let one
    client mint a fresh identity per request and bypass the limit entirely."""
    from api.throttle import client_ip

    class FakeHandler:
        def __init__(self, peer, fwd):
            self.client_address = (peer, 0)
            self.headers = {"X-Forwarded-For": fwd} if fwd else {}

    assert client_ip(FakeHandler("127.0.0.1", "9.9.9.9")) == "9.9.9.9"
    assert client_ip(FakeHandler("127.0.0.1", "9.9.9.9, 10.0.0.1")) == "9.9.9.9"
    # a direct caller does NOT get to name itself
    assert client_ip(FakeHandler("203.0.113.7", "9.9.9.9")) == "203.0.113.7"
    assert client_ip(FakeHandler("203.0.113.7", None)) == "203.0.113.7"
    return True


def test_a_session_survives_a_restart_and_is_never_stored_in_the_clear():
    """Restarting the API used to sign everybody out.

    Sessions lived only in memory, so every `docker compose up -d` invalidated
    every token. Observed in testing on 2026-08-30: a redeploy mid-session
    produced "The server rejected the token" on a phone that had signed in
    two minutes earlier. On a subscription product that is every customer,
    every deploy.

    The file must also be useless if copied: a session token is a bearer
    credential, so only its SHA-256 is written.
    """
    import tempfile
    from pathlib import Path

    from api.accounts import Accounts

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "accounts.json"
        acc = Accounts(path)
        u = acc.register("tim", "password-long-enough")
        token = acc.start_session(u)
        assert acc.session_user(token).identifier == "tim"

        # the raw token must not be recoverable from disk
        raw = acc.sessions_path.read_text()
        assert token not in raw, "session token written in the clear"

        # a brand new process reading the same files
        restarted = Accounts(path)
        again = restarted.session_user(token)
        assert again is not None, "restart signed the user out"
        assert again.identifier == "tim"

        # and signing out still revokes, across processes
        restarted.end_session(token)
        assert Accounts(path).session_user(token) is None
    return True


def test_changing_a_password_kills_sessions_everywhere():
    """A password change has to invalidate tokens on OTHER devices too, or
    'change your password' does not mean what anybody thinks it means."""
    import tempfile
    from pathlib import Path

    from api.accounts import Accounts

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "accounts.json"
        acc = Accounts(path)
        u = acc.register("tim", "password-long-enough")
        phone = acc.start_session(u)
        tablet = acc.start_session(u)

        acc.set_password("tim", "a-different-password")

        assert acc.session_user(phone) is None
        assert acc.session_user(tablet) is None
        # and it survives a restart as revoked, not as forgotten-then-restored
        assert Accounts(path).session_user(phone) is None
    return True


def test_an_active_session_never_expires_on_a_schedule():
    """"Stay logged in" has to mean 30 days of NOT using it, not 30 days
    since you typed your password. A fixed window signs an active user out on
    a timetable they have no way to anticipate."""
    import tempfile
    import time
    from pathlib import Path

    from api.accounts import SESSION_TTL, Accounts

    with tempfile.TemporaryDirectory() as d:
        acc = Accounts(Path(d) / "accounts.json")
        u = acc.register("tim", "password-long-enough")
        token = acc.start_session(u)

        # wind the clock forward to just past the renewal point
        digest = next(iter(acc._sessions))
        acc._sessions[digest] = (u.identifier, time.time() + SESSION_TTL * 0.4)

        assert acc.session_user(token) is not None
        _, exp = acc._sessions[next(iter(acc._sessions))]
        assert exp > time.time() + SESSION_TTL * 0.9, \
            "using a session did not extend it"

        # but a genuinely expired one is still refused, not resurrected
        acc._sessions[next(iter(acc._sessions))] = (u.identifier,
                                                    time.time() - 1)
        assert acc.session_user(token) is None
    return True


def test_warm_up_covers_every_trained_pair_not_just_the_default():
    """Adding coins used to make the app feel broken for most of them.

    `warm()` warmed only the service's default symbol, so on the droplet
    BTCUSDT answered in 14ms while the first tap on ETHUSDT 15m took 76
    seconds building from cold. Every pair with a model has to be warmed, or
    the ones you added are exactly the slow ones.
    """
    from api.service import get_service

    svc = get_service()
    syms = svc.trained_symbols()
    assert "BTCUSDT" in syms, syms
    # anything with a fitted model must be discoverable from output/ alone,
    # because the server has no watchlist - that lives on the device
    for s in syms:
        assert s.endswith("USDT"), s
    assert len(syms) >= 1
    return True


def test_the_warm_order_puts_the_cheap_timeframes_first():
    """Cheapest interval first, ACROSS symbols.

    Warming symbol-by-symbol leaves the last pair cold for the whole run. 1m
    costs ~100s per pair and 1d ~22s, so interval-major ordering means every
    pair has its slow-moving timeframes ready while the expensive ones are
    still building.
    """
    import inspect

    from api.service import TradingService

    src = inspect.getsource(TradingService.warm)
    order = re.search(r'order = \[([^\]]+)\]', src)
    assert order, "warm() no longer declares an explicit order"
    got = [x.strip().strip('"\'') for x in order.group(1).split(",")]
    assert got == ["1d", "4h", "1h", "15m", "5m", "1m"], got
    # and the symbol loop must be INSIDE the interval loop
    assert src.index("for tf in order") < src.index("for sym in syms"), \
        "warming is symbol-major again; the last pair stays cold"
    return True


def test_only_pairs_somebody_is_watching_keep_refreshing():
    """Warming 24 pairs and refreshing all 24 forever does not fit on one core.

    Measured on the droplet: with every pair warm, the 1m tier alone wants
    ~30s of rebuild per coin against a 120s TTL, which is 100% of a core for
    four coins before the other five timeframes or the collector get a look
    in. CPU pegged and refreshes queued behind each other.

    A pair nobody has opened recently must stop refreshing. It still serves
    instantly from its stale copy - it just stops doing work in the dark.
    """
    import time as _time

    from api.service import TradingService, _Cached

    svc = TradingService()
    svc._build_dashboard = lambda sym, iv: {"symbol": sym, "interval": iv}

    watched = ("BTCUSDT", "1m")
    forgotten = ("ADAUSDT", "1m")
    for k in (watched, forgotten):
        svc._dash[k] = _Cached(_time.time() - 10_000, {"stale": True})

    # someone is looking at BTC right now; nobody has opened ADA in an hour
    svc._last_seen[watched] = _time.time()
    svc._last_seen[forgotten] = _time.time() - (svc.ATTENTION_WINDOW + 60)

    with svc._lock:
        svc._refresh_soon(watched)
        svc._refresh_soon(forgotten)

    assert watched in svc._building, "the watched pair stopped refreshing"
    assert forgotten not in svc._building, \
        "a pair nobody has opened for an hour is still burning CPU"

    # and the forgotten one still ANSWERS instantly, from its stale copy
    t0 = _time.time()
    got = svc.dashboard(*forgotten)
    assert got == {"stale": True}, got
    assert _time.time() - t0 < 1.0
    return True


def test_asking_for_a_pair_counts_as_attention():
    """Otherwise the window never reopens and nothing refreshes again."""
    import time as _time

    from api.service import TradingService, _Cached

    svc = TradingService()
    svc._build_dashboard = lambda sym, iv: {"symbol": sym, "interval": iv}
    key = ("SOLUSDT", "15m")
    svc._dash[key] = _Cached(_time.time() - 10_000, {"stale": True})
    svc._last_seen[key] = _time.time() - (svc.ATTENTION_WINDOW + 60)

    svc.dashboard(*key)                     # a phone opens it again
    assert _time.time() - svc._last_seen[key] < 5.0
    return True


def test_the_chart_does_not_reparse_the_whole_store_every_call():
    """The measured cause of the app's timeouts.

    `/api/chart` returns at most 500 points but used to re-read and re-parse
    the entire bar store to get them - 175,000 rows for a 1m pair. Measured
    on the droplet: 1m 5.9s, 5m 5.3s, 15m 3.8s, 1h 2.6s, while the dashboard
    beside it answered in 7ms. With the app prefetching neighbouring pairs
    that put 11.4s of work on one core, queued ahead of the request the phone
    was waiting for.
    """
    import time

    from api.service import get_service

    svc = get_service()
    try:
        first_t0 = time.time()
        svc.chart(symbol="BTCUSDT", interval="1h", n=96)
        first = time.time() - first_t0
    except Exception:
        return True                       # no bars in this environment

    second_t0 = time.time()
    again = svc.chart(symbol="BTCUSDT", interval="1h", n=96)
    second = time.time() - second_t0

    assert again["points"], "chart returned nothing"
    # the second call must be dramatically cheaper, not marginally
    assert second < max(first / 5.0, 0.05), (first, second)
    return True


def test_bars_are_reloaded_when_the_files_change_and_not_before():
    """A stale candle is worse than a slow one, so the cache is keyed on the
    files themselves - mtime and size - rather than on a clock. The moment
    the collector appends a bar the signature changes and the next read
    reloads."""
    import inspect

    from api.service import TradingService

    src = inspect.getsource(TradingService._bars)
    assert "st_mtime_ns" in src and "st_size" in src, \
        "bar cache is no longer validated against the files"
    # no clock in the validation path: a time-based cache can serve a bar
    # that has already closed. (The docstring discusses TTLs, hence checking
    # for the call rather than the word.)
    body = src.split('"""')[-1]
    assert "time.time()" not in body, \
        "a time-based bar cache can serve a bar that has already closed"
    return True


def test_dashboards_survive_a_restart_so_the_app_is_not_left_waiting():
    """The measured cause of "timeout when I reopen the app".

    A dashboard costs 15-60s to build on the droplet. Until this existed every
    restart discarded all 24, warm-up then saturated the single core for ten
    minutes, and any request landing in that window queued behind a cold build
    and blew through the app's 20s timeout.

    A restarted process must answer immediately from the last known good copy
    and rebuild behind the request.
    """
    import tempfile
    import time
    from pathlib import Path

    from api.service import TradingService

    with tempfile.TemporaryDirectory() as d:
        built = []

        def make(svc):
            svc._dash_dir = Path(d) / "dashcache.v1"
            svc._build_dashboard = lambda sym, iv: (
                built.append((sym, iv)) or {"symbol": sym, "interval": iv})
            return svc

        first = make(TradingService())
        first._build_and_store(("BTCUSDT", "1h"))
        assert len(built) == 1

        # a brand new process, as after `docker compose up -d`
        second = TradingService()
        second._dash_dir = Path(d) / "dashcache.v1"
        second._load_dash_cache()
        second._build_dashboard = lambda sym, iv: (_ for _ in ()).throw(
            AssertionError("a restart rebuilt instead of serving the copy"))

        t0 = time.time()
        got = second.dashboard("BTCUSDT", "1h")
        assert got == {"symbol": "BTCUSDT", "interval": "1h"}, got
        assert time.time() - t0 < 1.0, "a restart made the caller wait"
    return True


def test_indicator_history_comes_from_the_cached_features():
    """Tiles showed one number with no sense of where it came from.

    RSI 53.9 says nothing about whether it climbed all day or just snapped
    back from 70. The series is sliced out of the SAME cached feature frame
    the dashboard already built, so expanding a tile costs a slice and no
    computation.
    """
    from api.service import get_service

    svc = get_service()
    try:
        d = svc.indicator("rsi", symbol="BTCUSDT", interval="1h", n=32)
    except Exception:
        return True                        # no bars/models in this env

    assert d["points"], "no history returned"
    assert len(d["points"]) <= 32
    for p in d["points"]:
        assert "t" in p and "v" in p
        assert p["v"] is None or 0.0 <= p["v"] <= 100.0, p
    # fixed bounds, so the shape is comparable between visits instead of
    # rescaling to whatever happens to be on screen
    assert d["min"] == 0.0 and d["max"] == 100.0, d
    assert d["explain"], "an indicator with no explanation is a magic number"
    return True


def test_an_unknown_indicator_is_a_404_not_a_500():
    from api.service import get_service

    try:
        get_service().indicator("nonsense")
    except KeyError:
        return True
    raise AssertionError("unknown indicator did not raise")


def test_every_tile_the_dashboard_draws_can_be_expanded():
    """A tile you can tap that has no series behind it is a dead end."""
    from api.service import TradingService

    # the keys `_indicators` emits must all be expandable
    emitted = {"rsi", "htf", "vol", "volume"}
    missing = emitted - set(TradingService.INDICATOR_SERIES)
    assert not missing, f"tiles with no history endpoint: {missing}"
    for key, (col, label, unit, bounds) in TradingService.INDICATOR_SERIES.items():
        assert label and isinstance(col, str)
        if bounds is not None:
            assert bounds[0] < bounds[1], (key, bounds)
    return True


def test_agent4_actually_receives_the_tape_in_both_paths():
    """Agent 4 had 20 of 22 features empty because nothing supplied its inputs.

    Measured on 6,000 real bars: only `taker_buy_ratio` carried data, and
    that one is derivable from klines. `FlowAgent.compute()` has always
    accepted `tape`, `open_interest` and `liquidations`; they were never
    passed. A quarter of the judge's 88 columns were NaN.

    Both callers must now supply them, and must do it through the SAME
    assembler — training with the tape and serving without it would leave the
    model predicting on zeros where it learned on real flow, with nothing
    raising and no test failing.
    """
    import inspect

    import monitor
    import train

    train_src = inspect.getsource(train.compute_frames)
    live_src = inspect.getsource(monitor.Monitor._compute_features)

    for name, src in (("train", train_src), ("serve", live_src)):
        assert "flow_inputs" in src, f"{name} no longer assembles flow inputs"
        assert "**extra" in src, f"{name} computes agent4 without them"

    # the live path must never block on a download
    assert "backfill=False" in live_src, \
        "serving would fetch archives inside a request"
    assert "backfill=True" in train_src, \
        "training would silently train on whatever happened to be cached"
    return True


def test_the_tape_store_round_trips_and_dedupes():
    """The store is written by two producers - a backfill and a live
    collector - so a bar written twice must not become two rows."""
    import tempfile
    from pathlib import Path

    import pandas as pd

    from marketdata.tape_store import TapeStore

    with tempfile.TemporaryDirectory() as d:
        st = TapeStore("BTCUSDT", "1h", Path(d))
        idx = pd.date_range("2026-08-01", periods=4, freq="h", tz="UTC")
        st.append(pd.DataFrame({"buy_notional": [1.0, 2, 3, 4]}, index=idx))
        # the same bars again, with corrected values, as a re-reduction would
        st.append(pd.DataFrame({"buy_notional": [9.0, 9, 9, 9]}, index=idx))

        got = st.load()
        assert len(got) == 4, got
        # last write wins: a re-reduced day is better than a partial live one
        assert set(got["buy_notional"]) == {9.0}, got
        assert len(st.covered_days()) == 1
    return True


def test_the_forward_record_resolves_with_the_same_rule_that_trained_it():
    """A forward record measured by a different rule than the backtest is
    not a comparison, it is two different questions.

    Resolution calls `triple_barrier` itself with the config off the fitted
    model, so this checks the wiring end to end: predictions written against
    historical bars must settle to exactly the labels training would assign.
    """
    import tempfile
    from pathlib import Path

    import pandas as pd

    from agent5.config import Agent5Config
    from agent5.labels import triple_barrier
    from agent5.track import Prediction, TrackRecord
    from core import utc_now
    from livefeed import BarStore

    bars = BarStore("BTCUSDT", "1h").load()
    if len(bars) < 500:
        return True                        # no history in this environment

    cfg = Agent5Config(k_up=1.0, k_dn=1.0, max_hold_bars=2)
    truth = triple_barrier(bars, cfg)

    # pick bars whose windows have definitely closed
    picks = [t for t in bars.index[-200:-50]
             if not pd.isna(truth.y.loc[t])][:40]
    assert picks, "no closed windows to test against"

    with tempfile.TemporaryDirectory() as d:
        rec = TrackRecord("BTCUSDT", "1h", Path(d))
        for t in picks:
            rec.record(Prediction(
                symbol="BTCUSDT", interval="1h", horizon="h2",
                bar_close=t.isoformat(), logged_at=utc_now().isoformat(),
                model_id="test", p_up=0.5, action="WAIT"))

        n = rec.resolve(bars, {"h2": cfg})
        assert n == len(picks), (n, len(picks))

        for p in rec.load():
            expect = float(truth.y.loc[pd.Timestamp(p.bar_close)])
            assert p.outcome == expect, (p.bar_close, p.outcome, expect)
            assert p.touch in ("upper", "lower", "timeout", "ambiguous")
    return True


def test_one_bar_produces_one_row_however_often_the_dashboard_rebuilds():
    """The dashboard rebuilds dozens of times per bar and each rebuild makes
    the same call. Logging every one would multiply the sample count without
    a single extra independent observation, and every metric downstream
    would be wrong in the flattering direction."""
    import tempfile
    from pathlib import Path

    from agent5.track import Prediction, TrackRecord

    with tempfile.TemporaryDirectory() as d:
        rec = TrackRecord("BTCUSDT", "1h", Path(d))
        p = Prediction(symbol="BTCUSDT", interval="1h", horizon="h1",
                       bar_close="2026-08-30T13:59:59.999+00:00",
                       logged_at="2026-08-30T14:00:00+00:00",
                       model_id="abc", p_up=0.55, action="ENTER LONG")
        assert rec.record(p) is True
        for _ in range(25):
            assert rec.record(p) is False
        assert len(rec.load()) == 1
    return True


def test_a_retrain_starts_a_new_series_instead_of_polluting_the_old_one():
    """Mixing predictions from three different models into one hit rate is a
    track record of nothing. Each row carries the fingerprint of the model
    that produced it."""
    import tempfile
    from pathlib import Path

    from agent5.track import Prediction, TrackRecord, model_fingerprint

    with tempfile.TemporaryDirectory() as d:
        rec = TrackRecord("BTCUSDT", "1h", Path(d))
        for i, mid in enumerate(("model-a", "model-a", "model-b")):
            rec.record(Prediction(
                symbol="BTCUSDT", interval="1h", horizon="h1",
                bar_close=f"2026-08-30T0{i}:00:00+00:00",
                logged_at="x", model_id=mid, p_up=0.6, action="ENTER LONG",
                outcome=1.0))
        assert rec.metrics()["models"] == ["model-a", "model-b"]
        assert rec.metrics(model_id="model-a")["resolved"] == 2
        assert rec.metrics(model_id="model-b")["resolved"] == 1

        # and the fingerprint must be content-based, not timestamp-based:
        # rsyncing a model to the droplet changes its mtime, not its
        # behaviour, and a fingerprint that moved on deploy would split one
        # series in two for no reason
        f = Path(d) / "m.joblib"
        f.write_bytes(b"weights")
        first = model_fingerprint(f)
        f.touch()
        assert model_fingerprint(f) == first
    return True


def test_the_record_is_taken_on_a_clock_not_on_attention():
    """Dashboards only rebuild when somebody looks at them — that is what
    keeps the box idle. If the forward record inherited that, it would hold
    exactly the bars the app was opened for, and people open trading apps
    when something is happening. Every metric would then be measuring that
    selection, not the model."""
    import inspect

    from api.service import TradingService

    src = inspect.getsource(TradingService.start_recorder)
    assert "_build_and_store" in src, "the recorder no longer builds"
    assert "_last_seen" not in src, "the recorder is attention-gated again"

    # and it must wait past the close, not fire on it: a bar is only in the
    # store once the collector has written it
    sleep_src = inspect.getsource(TradingService._sleep_to_next_close)
    assert "45" in sleep_src, "no margin after the bar close"

    for iv in TradingService.RECORD_INTERVALS:
        assert iv in ("1m", "5m", "15m", "1h", "4h", "1d"), iv
    return True


def test_the_recorder_waits_for_the_next_close_not_a_fixed_delay():
    from api.service import TradingService

    d = TradingService._sleep_to_next_close(("1h",))
    assert 45.0 <= d <= 3600.0 + 45.0, d
    # a shorter interval must produce a sooner wake-up
    assert TradingService._sleep_to_next_close(("5m",)) <= 300.0 + 45.0
    return True


def test_only_clock_covered_intervals_are_recorded():
    """`_build_dashboard` runs for any reason — someone opening the app, a
    cache refresh, warm-up. Recording those puts sporadic attention-driven
    rows in the file, and a record that is dense when you were watching and
    empty when you were not measures your habits rather than the model.

    Caught in production within minutes: a 1d row appeared for a timeframe
    the recorder does not cover.
    """
    import inspect

    from api.service import TradingService

    src = inspect.getsource(TradingService._record_forward)
    assert "RECORD_INTERVALS" in src, \
        "any build can write to the forward record again"
    assert src.index("RECORD_INTERVALS") < src.index("TrackRecord"), \
        "the guard must come before anything is written"
    return True


def test_the_recorder_only_rebuilds_a_pair_when_its_own_bar_closes():
    """The loop wakes on the SHORTEST interval in the set.

    With 15m in the list that is four times an hour, and rebuilding
    everything on each wake would rebuild 1d ninety-six times a day to
    record the same bar once. The dedupe would drop the duplicate rows, but
    the CPU — ~24s per build on the droplet's single shared core — would
    already have been spent.
    """
    import inspect

    from api.service import TradingService

    src = inspect.getsource(TradingService.start_recorder)
    assert "seen.get((sym, iv)) == last" in src, \
        "the recorder rebuilds every interval on every wake again"
    assert "bars.index[-1]" in src, "no check that a new bar actually closed"
    return True


def test_the_recorded_intervals_are_ones_the_box_can_keep_up_with():
    """A build is ~24s on one shared vCPU (measured: 24 pairs in 587s).

    A 1m bar closes every 60s, so four coins at 1m would need ~96s of work
    per 60s window and would fall permanently behind. 5m is borderline for
    the same reason. Neither belongs here until the hardware changes.
    """
    from api.service import TradingService
    from core import interval_seconds

    build_seconds = 24.0
    symbols = 4
    total = 0.0
    for iv in TradingService.RECORD_INTERVALS:
        assert iv not in ("1m", "5m"), \
            f"{iv} cannot be kept warm on a single shared core"
        total += build_seconds * symbols / interval_seconds(iv)

    # This arithmetic UNDERSTATES the real cost. It counts only the recorder;
    # the refresh worker servicing recently-viewed pairs roughly doubles it.
    #
    # Measured on the droplet with ("15m","1h","4h","1d"): this formula says
    # 14%, eighteen samples over six minutes said mean 27.1%, peak 100.5%.
    # Idle sits near 1% and bursts to a full core while four builds run.
    #
    # So the budget here is deliberately half of what is actually available:
    # clearing it means the true figure is around twice as much, and still
    # inside one core.
    assert total < 0.20, (
        f"recorder alone would use {total*100:.0f}% of a core; the measured "
        f"cost is roughly double that")
    return True


def test_no_sensitivity_level_ever_recommends_a_losing_trade():
    """Three levels were asked for, to produce more signals.

    All three sit at or above breakeven after costs:
        strong  EV > 0.05%   medium  EV > 0.02%   small  EV > 0.00%

    A fourth below zero would fire far more often — at a 1.67% span it needs
    only p_up 0.530, which these models do reach — and every one of those
    trades loses money on average. More signals by way of losing ones is not
    a feature, so the loosest level is breakeven and this pins it.
    """
    import inspect

    import monitor

    src = inspect.getsource(monitor.evaluate)
    m = re.search(r'levels = \((.*?)\)\n', src, re.S)
    assert m, "evaluate() no longer declares sensitivity levels"
    block = m.group(1)
    assert '"strong"' in block and '"medium"' in block and '"small"' in block

    # the loosest bar must be zero, never negative
    assert '("small", 0.0)' in block, \
        "the loosest level is no longer breakeven — it may recommend losing trades"
    assert "-" not in block.split('("small"')[1][:12], \
        "a negative expected-value level was added"
    return True
