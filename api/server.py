"""A read-only JSON API over the trading stack, on the standard library.

WHY NO FRAMEWORK
----------------
`requirements.txt` is deliberately short - the detectors are geometry, not
machine learning, and the project has kept its dependency list small on
purpose. Adding FastAPI plus uvicorn to serve seven read-only endpoints to
one phone would be the largest dependency decision in the repo, made for the
least important component. `http.server` covers it.

WHAT IT WILL NOT DO
-------------------
No writes, no orders, no keys, no auth. Every endpoint is a GET that reads
what `monitor.py` reads. If this process is compromised the worst outcome is
that someone learns what your terminal already prints.

BINDING, AND WHY A TOKEN IS NOT OPTIONAL OFF-LAN
------------------------------------------------
On a home network, 0.0.0.0 with no auth is defensible: the worst a reachable
attacker learns is what your own terminal prints, and a router stands between
it and the internet.

On a rented server that reasoning collapses. There is no router, the port is
world-reachable, and an open endpoint that proxies Binance calls is a way to
burn someone else's rate limit and bandwidth for free.

So `serve()` REFUSES to bind to anything but loopback without a token. Failing
to start is the correct behaviour: the alternative is a process that looks
healthy while serving the whole internet, and nobody discovers that by
looking at it.

    TRADINGBOT_TOKEN=$(openssl rand -hex 32) python3 serve.py

`/api/health` stays open so a container healthcheck or load balancer can probe
it without holding the secret. It returns no market data.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import socket
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from api.service import get_service
from core import utc_now

log = logging.getLogger(__name__)

# One engine per process: it holds the previous recommendation and the set of
# filings already alerted on, which is what makes an alert a transition rather
# than a repeated state.
def _equity_daily(symbol: str):
    """Ten years of daily bars for one stock, however we can get them.

    The training store first, because a trained symbol already has them on
    disk and reading a file beats a round trip. Otherwise straight from
    Alpaca — the long view is worth showing for a stock nobody has trained,
    and requiring a fit first would make the panel appear only where it was
    least needed.
    """
    from livefeed import BarStore

    try:
        bars = BarStore(symbol, "1d").load()
        if bars is not None and len(bars) > 60:
            return bars
    except Exception:
        pass

    from datetime import datetime, timedelta, timezone

    from marketdata.alpaca import Alpaca, AlpacaError

    try:
        client = Alpaca()
        if not client.configured:
            return None
        start = datetime.now(timezone.utc) - timedelta(days=3650)
        return client.bars([symbol], start=start).get(symbol.upper())
    except AlpacaError as e:
        log.warning("horizon %s: %s", symbol, e)
        return None


def _screener_table(market: str) -> dict:
    """The right universe for the market. Two tables, never one merged.

    A crypto perpetual has no P/E and a stock has no funding rate, so a
    combined table would be half nulls in both directions and every filter
    would need to know which kind of row it was looking at.
    """
    if market == "crypto":
        from screener import crypto as _c

        return _c.load()
    from screener import universe as _u

    return _u.load()


_ENGINE = None


def get_engine(svc=None):
    """The one alert engine, running its own refresh loop.

    STARTED ON CREATION, not on the first request. Detection is a background
    job: a transition is defined against the previous observation, so an
    engine that only looks when a phone asks has no previous observation to
    compare against and reports nothing. That is why signal and news
    notifications almost never arrived — they were never detected, not merely
    undelivered.
    """
    global _ENGINE
    if _ENGINE is None:
        from api.alerts import AlertEngine

        _ENGINE = AlertEngine(svc if svc is not None else get_service())
        _ENGINE.start()
    return _ENGINE

# Shared secret. Empty means loopback-only operation; `serve()` refuses any
# other binding without one.
TOKEN = ""


def _now_iso() -> str:
    return utc_now().isoformat()


def _push_default() -> str:
    """Which push relay the app should register against.

    Overridable so a self-hosted ntfy can replace the public one without
    shipping a build — the public server sees the topic and the alert text,
    and someone who would rather it did not should be able to point this
    elsewhere with an environment variable.
    """
    from api.push import DEFAULT_SERVER

    return os.environ.get("PUSH_SERVER") or DEFAULT_SERVER


def _stripe_ready() -> bool:
    """Is Stripe actually wired, or is the paywall a shop window?

    The app asks so it can say "payments are not set up yet" instead of
    opening a checkout that 501s.
    """
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def lan_ip() -> str:
    """Best guess at the address a phone on the same wifi should use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))          # no packet is actually sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "TradingBot/1.0"

    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # the app may run from a Flutter web build during development
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200) -> None:
        """For the two Stripe landing pages, and nothing else.

        The API speaks JSON to the app; these two are opened by a person's
        browser after a payment, and a browser shown `{"ok": true}` has told
        that person nothing about whether their money arrived.
        """
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:                       # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, *")
        self.end_headers()

    # -- auth ----------------------------------------------------------
    #
    # Two kinds of caller, one header.
    #
    #   the shared TRADINGBOT_TOKEN  -> full access, no account. This is the
    #                                   operator's key and how the collector,
    #                                   curl and the pre-configured build all
    #                                   reach the API. Keeping it working
    #                                   means adding accounts did not break
    #                                   the deployment that already existed.
    #   a session token              -> whatever that account is entitled to.
    #
    # Both arrive as `Authorization: Bearer <x>`, so the app never had to
    # learn a second scheme.

    # Reachable with no credential at all. Health must stay open for the
    # container probe, and a login form obviously cannot require a session.
    # The two Stripe landing pages are open because they must be: the
    # customer arrives on them straight from Stripe's payment page, in
    # whatever browser their phone opened, carrying no session token. They
    # read nothing and grant nothing -- the upgrade is the webhook's job,
    # not this page's -- so being open costs nothing.
    OPEN = ("/", "/api", "/api/health",
            "/api/auth/login", "/api/auth/register", "/api/billing/plans",
            "/billing/done", "/billing/cancelled")

    # Reachable by a signed-in account with no subscription. The chart is
    # here because the paywall is meant to show the graph and withhold the
    # analysis — a paywall that renders an empty screen tells you nothing
    # about what you would be buying.
    # /api/calendar is free deliberately. The date of a US payrolls release
    # is a public fact published by the government, not analysis anyone is
    # paying for, and warning an unsubscribed user that the market is about
    # to move is the right thing to do regardless of whether they pay.
    FREE = OPEN + ("/api/chart", "/api/calendar", "/api/me",
                   "/api/auth/logout", "/api/billing/checkout",
                   # The CATALOGUE is free; the RESULTS are not. It describes
                   # which controls exist and what the presets contain, which
                   # is the thing an unsubscribed user needs in order to see
                   # what they would be buying. It contains no market data.
                   "/api/screener/catalogue")

    def _bearer(self) -> str:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return header[len(prefix):].strip() if header.startswith(prefix) else ""

    def _principal(self):
        """(is_operator, user_or_None) for this request.

        `(False, None)` means nobody — the caller presented nothing valid.
        """
        token = self._bearer()
        if TOKEN and token and hmac.compare_digest(token, TOKEN):
            # constant time: a plain == leaks the shared secret one byte at a
            # time to anyone willing to measure
            return True, None
        if token:
            from api.accounts import get_accounts
            u = get_accounts().session_user(token)
            if u is not None:
                return False, u
        return False, None

    def _gate(self, route: str):
        """None if the request may proceed, else (payload, status).

        Returns 402 rather than 403 for a signed-in account without a
        subscription. They are different situations and the app draws
        different screens for them: 401 is "sign in", 402 is "this is what
        you would be buying", 403 would mean "never, for you".
        """
        if route in self.OPEN:
            return None
        if not TOKEN:
            return None                     # loopback-only mode; see serve()

        operator, user = self._principal()
        if operator:
            return None
        if user is None:
            return ({"error": "unauthorised",
                     "hint": "sign in, or send Authorization: Bearer <token>"},
                    401)
        if route in self.FREE or user.entitled:
            return None
        return ({"error": "subscription required",
                 "tier": user.tier,
                 "entitled": False,
                 "hint": "this endpoint needs an active subscription"}, 402)

    def do_GET(self) -> None:                           # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        q = parse_qs(parsed.query)

        denied = self._gate(route)
        if denied is not None:
            self._send(denied[0], status=denied[1])
            return

        def arg(name: str, default: int) -> int:
            try:
                return int(q.get(name, [default])[0])
            except (TypeError, ValueError):
                return default

        def opt(name: str) -> Optional[str]:
            v = q.get(name, [None])[0]
            return v or None

        svc = get_service()
        try:
            if route in ("/", "/api", "/api/health"):
                self._send({"ok": True, "service": "tradingbot",
                            "trades": False,
                            "endpoints": ["/api/coins", "/api/dashboard",
                                          "/api/chart", "/api/whales",
                                          "/api/news", "/api/training",
                                          "/api/alerts", "/api/calendar",
                                          "/api/timeframes",
                                          "/api/consensus", "/api/symbols",
                                          "/api/me", "/api/auth/login",
                                          "/api/auth/register",
                                          "/api/billing/plans",
                                          "/api/indicator",
                                          "/api/track",
                                          "/api/screener/catalogue",
                                          "/api/screener",
                                          "/api/screener/setups",
                                          "/api/stock",
                                          "/api/stock/quotes",
                                          "/api/stock/search",
                                          "/api/horizon", "/api/train",
                                          "/api/train/status", "/api/push",
                                          "/api/range", "/api/signals"]})
            elif route == "/api/me":
                operator, user = self._principal()
                if operator:
                    # the shared key is not an account. Say so rather than
                    # inventing a username the app would then display.
                    self._send({"identifier": "operator", "tier": "admin",
                                "entitled": True, "operator": True})
                else:
                    self._send(user.public() if user else
                               {"identifier": None, "tier": "free",
                                "entitled": False})
            elif route in ("/billing/done", "/billing/cancelled"):
                from api.billing import landing_html, landing_state
                self._send_html(landing_html(
                    landing_state(route, opt("session_id"))))
            elif route == "/api/billing/plans":
                from api.billing import plans
                self._send({"plans": plans(), "provider": "stripe",
                            "configured": _stripe_ready()})
            elif route == "/api/coins":
                raw = q.get("symbols", [None])[0]
                picked = [x for x in (raw or "").split(",") if x.strip()] or None
                self._send({"coins": svc.coins(picked)})
            elif route == "/api/symbols":
                self._send({"symbols": svc.symbols(q=opt("q"),
                                                  limit=arg("limit", 60))})
            elif route == "/api/timeframes":
                self._send({"timeframes": svc.timeframes(opt("symbol"))})
            elif route == "/api/dashboard":
                self._send(svc.dashboard(symbol=opt("symbol"),
                                         interval=opt("interval")))
            elif route == "/api/push":
                from api.push import get_relay

                operator, user = self._principal()
                account = "operator" if operator else (
                    user.identifier if user else "")
                self._send({
                    "subscription": get_relay().for_account(account),
                    "server": _push_default(),
                })
            elif route == "/api/train/status":
                from api.trainer import get_trainer

                self._send(get_trainer().status(symbol=opt("symbol")))
            elif route == "/api/horizon":
                from agent5.longhorizon import report

                sym = (opt("symbol") or svc.symbol).upper()
                market = opt("market") or "crypto"
                if market == "stocks":
                    bars = _equity_daily(sym)
                else:
                    bars = svc._bars(sym, "1d")
                self._send(report(sym, bars))
            elif route == "/api/consensus":
                self._send(svc.consensus(opt("symbol")))
            elif route == "/api/stock":
                from screener import detail as _detail
                from screener import universe as _u

                sym = opt("symbol")
                if not sym:
                    self._send({"error": "symbol is required"}, status=400)
                    return
                self._send(_detail.detail(sym, _u.load(),
                                          interval=opt("interval") or "1d"))
            elif route == "/api/stock/quotes":
                from screener import detail as _detail
                from screener import universe as _u

                raw = q.get("symbols", [""])[0]
                wanted = [x for x in raw.split(",") if x.strip()]
                self._send({"quotes": _detail.quotes(wanted, _u.load())})
            elif route == "/api/stock/search":
                from screener import detail as _detail
                from screener import universe as _u

                self._send({"results": _detail.search(
                    opt("q") or "", _u.load(), limit=arg("limit", 40))})
            elif route == "/api/screener/catalogue":
                from screener.filters import catalogue

                market = opt("market") or "stocks"
                table = _screener_table(market)
                self._send({**catalogue(market),
                            # so the page can say how old the table is rather
                            # than presenting an overnight snapshot as live
                            "built_at": table.get("built_at"),
                            "symbols": table.get("symbols", 0)})
            elif route == "/api/screener/setups":
                from screener.engine import run as _run
                from screener.filters import (UNAVAILABLE as _UNAV,
                                              presets_for)

                market = opt("market") or "stocks"
                table = _screener_table(market)
                rows = table.get("rows") or {}
                top = arg("top", 3)

                # ONE request for every preset, not one per preset.
                #
                # The setup carousel needs a count for each strategy pill and
                # the leading matches behind it. Asking separately would be
                # seven round trips to draw one row of chips, on a phone, over
                # mobile data.
                out = []
                for p in presets_for(market).values():
                    fs = [f for f in p.filters if f.field not in _UNAV]
                    r = _run(rows, fs, limit=top, sort_by="quote_volume"
                             if market == "crypto" else "market_cap")
                    out.append({
                        "id": p.id, "name": p.name, "note": p.note,
                        "count": r["matched"],
                        "filters": [f.to_json() for f in fs],
                        "dropped": [f.to_json() for f in p.filters
                                    if f.field in _UNAV],
                        "rows": r["rows"],
                    })
                # Most matches first: a strategy finding nothing today is
                # still listed, but it should not lead.
                out.sort(key=lambda x: -x["count"])
                self._send({"setups": out, "market": market,
                            "built_at": table.get("built_at"),
                            "scanned": len(rows)})
            elif route == "/api/screener":
                from screener.engine import run as _run
                from screener.filters import (UNAVAILABLE as _UNAVAILABLE,
                                              Filter, presets_for)

                market = opt("market") or "stocks"
                PRESETS_BY_ID = presets_for(market)
                table = _screener_table(market)
                rows = table.get("rows") or {}

                preset = opt("preset")
                raw = q.get("filters", [None])[0]
                if preset and preset in PRESETS_BY_ID:
                    # The judgeable criteria only — same list the catalogue
                    # hands the app, so running a preset by name and running
                    # the rows it filled in cannot disagree.
                    filters = [f for f in PRESETS_BY_ID[preset].filters
                               if f.field not in _UNAVAILABLE]
                elif raw:
                    try:
                        filters = [Filter.from_json(f)
                                   for f in json.loads(raw)]
                    except (ValueError, KeyError, TypeError) as e:
                        self._send({"error": f"bad filters: {e}"}, status=400)
                        return
                else:
                    filters = []

                result = _run(rows, filters,
                              limit=arg("limit", 200),
                              sort_by=opt("sort"),
                              descending=(opt("dir") or "desc") != "asc",
                              include_unknown=(opt("unknown") == "1"))
                self._send({**result,
                            "built_at": table.get("built_at"),
                            "market": market,
                            "preset": preset,
                            "filters": [f.to_json() for f in filters]})
            elif route == "/api/track":
                self._send(svc.track(symbol=opt("symbol"),
                                     interval=opt("interval")))
            elif route == "/api/indicator":
                key = opt("key") or "rsi"
                try:
                    self._send(svc.indicator(key, symbol=opt("symbol"),
                                             interval=opt("interval"),
                                             n=arg("n", 96)))
                except KeyError as e:
                    self._send({"error": str(e), "path": route}, status=404)
            elif route == "/api/signals":
                self._send(svc.live_signals())
            elif route == "/api/range":
                self._send(svc.price_range(symbol=opt("symbol"),
                                           interval=opt("interval") or "1m",
                                           since=opt("since")))
            elif route == "/api/chart":
                self._send(svc.chart(symbol=opt("symbol"),
                                     interval=opt("interval"),
                                     n=arg("n", 96)))
            elif route == "/api/whales":
                sym = opt("symbol")
                if (opt("market") or "crypto") == "stocks" and sym:
                    from screener import detail as _detail

                    self._send({"events": _detail.insiders(
                        sym, limit=arg("limit", 25))})
                else:
                    self._send({"events": svc.whales(limit=arg("limit", 20))})
            elif route == "/api/news":
                market = opt("market") or "crypto"
                sym = opt("symbol")
                items = svc.news(limit=arg("limit", 20), symbol=sym,
                                 market=market)
                if market == "stocks" and sym:
                    # Filings for THIS company, fetched on demand and merged
                    # ahead of the market-wide feed. The collector cannot
                    # pre-fetch them: which symbols anyone follows is a
                    # device preference the server never sees.
                    from screener import detail as _detail

                    items = _detail.stock_news(sym) + items
                self._send({"items": items[:arg("limit", 20)]})
            elif route == "/api/alerts":
                raw = q.get("after", [None])[0]
                try:
                    cursor = int(raw) if raw not in (None, "") else None
                except (TypeError, ValueError):
                    cursor = None          # fail closed: no cursor, no backlog
                engine = get_engine(svc)
                alerts = engine.after(cursor)
                # WHICH OF THESE ALREADY REACHED THE PHONE ANOTHER WAY.
                #
                # Both delivery paths run at once — the relay and this poll —
                # so without saying so, one event would post two
                # notifications. This is a statement of fact about what was
                # sent, not an instruction to stay silent: an alert the relay
                # failed to send comes back false and the app notifies
                # exactly as it always did.
                from api.push import get_relay

                operator, user = self._principal()
                pushed = get_relay().pushed_to(
                    "operator" if operator else (
                        user.identifier if user else ""))
                out = []
                for a in alerts:
                    d = a.to_json()
                    d["pushed"] = a.id in pushed
                    out.append(d)
                self._send({"alerts": out,
                            "cursor": engine.cursor(),
                            "server_time": _now_iso()})
            elif route == "/api/calendar":
                from newsfeed.schedule import next_major, upcoming
                days = arg("days", 21)
                major = next_major(within_days=max(days, 45))
                # `next_major` is carried separately from the list because the
                # two answer different questions: the list is a calendar in
                # time order, this is "the next thing that will actually move
                # the market", which may be three weeks down the list.
                self._send({"events": [e.to_json()
                                       for e in upcoming(within_days=days)],
                            "next_major": major.to_json() if major else None})
            elif route == "/api/training":
                self._send(svc.training(opt("symbol") or svc.symbol,
                                        interval=opt("interval")))
            else:
                self._send({"error": "not found", "path": route}, status=404)
        except FileNotFoundError as e:
            # an untrained timeframe is a normal answer, not a server fault.
            # 409 so the app can tell "not fitted yet" apart from "broken",
            # and route the user to the training screen rather than an error
            self._send({"error": str(e), "path": route,
                        "untrained": True}, status=409)
        except Exception as e:                # a 500 with the cause beats a hang
            log.error("%s failed: %s", route, e)
            traceback.print_exc()
            self._send({"error": str(e), "path": route}, status=500)

    # -- writes --------------------------------------------------------
    #
    # This handler used to be GET-only, and a test enforced it. That test
    # existed because the whole security argument was "a compromise yields
    # what your terminal already prints". Accounts change that, so the
    # argument is narrowed rather than dropped:
    #
    #   MARKET DATA IS STILL READ-ONLY. Nothing below writes a bar, a model
    #   or a watchlist. The only mutable state this process owns is the
    #   account table, and every route here names an account operation.
    #
    # `test_writes_touch_accounts_and_nothing_else` holds that line.
    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return {}
        if n <= 0 or n > 64 * 1024:      # a login form is not 64KB
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, UnicodeDecodeError):
            return {}

    def _stripe_webhook(self) -> None:
        """Stripe's events. Authenticated by signature, not by session."""
        from api.accounts import get_accounts
        from api.billing import SignatureError, apply_webhook, verify_signature

        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0 or n > 512 * 1024:
            self._send({"error": "empty or oversized payload"}, status=400)
            return
        raw = self.rfile.read(n)

        try:
            event = verify_signature(raw, self.headers.get(
                "Stripe-Signature", ""))
        except SignatureError as e:
            # 400, and deliberately not retryable: a bad signature will not
            # become good on the fifth attempt. Logged at warning because on
            # a public URL this is either a misconfiguration or somebody
            # probing for exactly the hole this check closes.
            log.warning("stripe webhook rejected: %s", e)
            self._send({"error": str(e)}, status=400)
            return
        except NotImplementedError as e:
            self._send({"error": str(e)}, status=501)
            return

        ident = apply_webhook(event, get_accounts())
        # 200 even when nothing was applied. Stripe retries any non-2xx for
        # days and then disables the endpoint, so an event type we do not
        # act on must still be acknowledged rather than left to poison the
        # delivery queue for the events we do care about. A genuine failure
        # inside apply_webhook raises instead, and the 500 asks for a retry
        # that might actually succeed.
        self._send({"ok": True, "applied": bool(ident)})

    def do_POST(self) -> None:                          # noqa: N802
        route = urlparse(self.path).path.rstrip("/") or "/"

        # Stripe first: before `_gate`, and before `_body`. Both matter.
        #
        # Stripe holds no session token, so its credential is the signature
        # on the payload; `_gate` knows only about bearer tokens and would
        # 401 every delivery. And the signature covers the exact bytes on
        # the wire, which `_body` throws away when it parses -- so this
        # route has to read `rfile` itself.
        #
        # NOT added to OPEN for that reason. This is not an open endpoint,
        # it is one with a different credential, and listing it as open
        # would invite someone to reuse that entry for a route where the
        # signature check does not exist.
        if route == "/api/billing/webhook":
            self._stripe_webhook()
            return

        denied = self._gate(route)
        if denied is not None:
            self._send(denied[0], status=denied[1])
            return

        from api.accounts import AuthError, get_accounts
        from api.throttle import client_ip, get_throttle, retry_payload
        acc = get_accounts()
        body = self._body()
        ident = str(body.get("identifier") or "")
        password = str(body.get("password") or "")

        # Only the credential routes. Throttling logout or checkout would
        # punish normal use to defend against nothing.
        ip = client_ip(self)
        throttle = get_throttle()
        if route in ("/api/auth/login", "/api/auth/register"):
            wait = throttle.check(ip, ident)
            if wait is not None:
                payload, status = retry_payload(wait)
                self._send(payload, status=status)
                return

        try:
            if route == "/api/train":
                # Gated like every other paid endpoint by `_gate` above, and
                # deliberately NOT throttled by the login limiter: the queue
                # itself is the limiter — one fit at a time, duplicates
                # refused, MAX_QUEUE enforced.
                from api.trainer import get_trainer

                operator, user = self._principal()
                r = get_trainer().submit(
                    str(body.get("symbol") or ""),
                    str(body.get("interval") or ""),
                    market=str(body.get("market") or "crypto"),
                    # The daily cap is per account, so it needs to know which
                    # one. The operator key is one identity like any other.
                    account="operator" if operator else (
                        user.identifier if user else "anon"))
                self._send(r, status=200 if r.get("ok") else 409)
                return
            if route in ("/api/push/subscribe", "/api/push/unsubscribe",
                         "/api/push/test"):
                from api.push import get_relay

                operator, user = self._principal()
                account = "operator" if operator else (
                    user.identifier if user else "")
                relay = get_relay()
                topic = str(body.get("topic") or "")
                if route == "/api/push/unsubscribe":
                    r = relay.forget(topic)
                elif route == "/api/push/test":
                    r = relay.test(topic)
                else:
                    r = relay.register(
                        topic, account=account,
                        server=str(body.get("server") or ""
                                   ) or _push_default(),
                        sensitivity=str(body.get("sensitivity") or "strong"),
                        news=str(body.get("news") or "all"),
                        muted=[str(x) for x in (body.get("muted") or [])
                               if isinstance(x, str)][:200],
                        positions=[p for p in (body.get("positions") or [])
                                   if isinstance(p, dict)][:200])
                self._send(r, status=200 if r.get("ok") else 400)
                return
            if route == "/api/train/cancel":
                from api.trainer import get_trainer

                r = get_trainer().cancel(str(body.get("id") or ""))
                self._send(r, status=200 if r.get("ok") else 409)
                return
            if route == "/api/auth/register":
                u = acc.register(ident, password)
                throttle.forget(ip, ident)
                self._send({"token": acc.start_session(u),
                            "user": u.public()}, status=201)
            elif route == "/api/auth/login":
                u = acc.verify(ident, password)
                throttle.forget(ip, ident)   # a mistyped password must not
                self._send({"token": acc.start_session(u),   # count against
                            "user": u.public()})             # the real owner
            elif route == "/api/auth/logout":
                acc.end_session(self._bearer())
                self._send({"ok": True})
            elif route == "/api/billing/checkout":
                from api.billing import checkout_session
                _, user = self._principal()
                self._send(checkout_session(user, body.get("plan")))
            else:
                self._send({"error": "not found", "path": route}, status=404)
        except AuthError as e:
            # 400, not 500: the caller gave something this endpoint rejects,
            # and the message is written to be shown to a person.
            self._send({"error": str(e), "path": route}, status=400)
        except NotImplementedError as e:
            # billing before Stripe keys exist. 501 says "the server has not
            # implemented this", which is exactly true and lets the app show
            # a real explanation instead of a generic failure.
            self._send({"error": str(e), "path": route}, status=501)
        except Exception as e:
            log.error("%s failed: %s", route, e)
            traceback.print_exc()
            self._send({"error": str(e), "path": route}, status=500)

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)


LOOPBACK = ("127.0.0.1", "localhost", "::1")


def serve(host: str = "0.0.0.0", port: int = 8787,
          token: Optional[str] = None) -> None:
    global TOKEN
    TOKEN = token or os.environ.get("TRADINGBOT_TOKEN") or ""

    if host not in LOOPBACK and not TOKEN:
        raise SystemExit(
            "\n  REFUSING TO START.\n\n"
            f"  Binding to {host} exposes this API beyond the machine it runs\n"
            "  on, and it has no authentication. On a home network that is\n"
            "  defensible; on a rented server it is an open endpoint that\n"
            "  proxies Binance calls with your rate limit.\n\n"
            "  Either set a token:\n"
            "    TRADINGBOT_TOKEN=$(openssl rand -hex 32) python3 serve.py\n\n"
            "  or keep it on this machine only:\n"
            "    python3 serve.py --host 127.0.0.1\n")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    httpd = ThreadingHTTPServer((host, port), Handler)
    ip = lan_ip()
    print(f"\n  TradingBot API on http://{ip}:{port}")
    print(f"    iOS simulator     http://localhost:{port}")
    print(f"    Android emulator  http://10.0.2.2:{port}")
    print(f"    physical phone    http://{ip}:{port}   (same wifi)")
    print(f"\n  auth: {'Bearer token REQUIRED' if TOKEN else 'none (loopback only)'}")
    print(f"  read-only. it does not trade. Ctrl-C to stop\n")

    # Build every trained timeframe before a phone asks for one. A cold build
    # is 10-46s on a single core, and the first request after a restart would
    # otherwise pay all of it while the app sits on a timeout.
    try:
        get_service().warm()
        # the forward record has to accumulate whether or not anyone is
        # looking, or it measures when the app gets opened rather than how
        # the model performs
        get_service().start_recorder()
        # Alerts are detected on a clock too, for the same reason: an engine
        # that only looks when the app polls can only report what changed
        # while somebody was watching.
        get_engine()
    except Exception as e:                  # a warm failure is not fatal - the
        log.warning("warm-up skipped: %s", e)   # request path still builds

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
