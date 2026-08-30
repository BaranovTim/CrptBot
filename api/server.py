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
_ENGINE = None

# Shared secret. Empty means loopback-only operation; `serve()` refuses any
# other binding without one.
TOKEN = ""


def _now_iso() -> str:
    return utc_now().isoformat()


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
    OPEN = ("/", "/api", "/api/health",
            "/api/auth/login", "/api/auth/register", "/api/billing/plans")

    # Reachable by a signed-in account with no subscription. The chart is
    # here because the paywall is meant to show the graph and withhold the
    # analysis — a paywall that renders an empty screen tells you nothing
    # about what you would be buying.
    # /api/calendar is free deliberately. The date of a US payrolls release
    # is a public fact published by the government, not analysis anyone is
    # paying for, and warning an unsubscribed user that the market is about
    # to move is the right thing to do regardless of whether they pay.
    FREE = OPEN + ("/api/chart", "/api/calendar", "/api/me",
                   "/api/auth/logout", "/api/billing/checkout")

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
                                          "/api/billing/plans"]})
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
            elif route == "/api/consensus":
                self._send(svc.consensus(opt("symbol")))
            elif route == "/api/chart":
                self._send(svc.chart(symbol=opt("symbol"),
                                     interval=opt("interval"),
                                     n=arg("n", 96)))
            elif route == "/api/whales":
                self._send({"events": svc.whales(limit=arg("limit", 20))})
            elif route == "/api/news":
                self._send({"items": svc.news(limit=arg("limit", 20))})
            elif route == "/api/alerts":
                from api.alerts import AlertEngine
                global _ENGINE
                if _ENGINE is None:
                    _ENGINE = AlertEngine(svc)
                raw = q.get("after", [None])[0]
                try:
                    cursor = int(raw) if raw not in (None, "") else None
                except (TypeError, ValueError):
                    cursor = None          # fail closed: no cursor, no backlog
                alerts = _ENGINE.after(cursor)
                self._send({"alerts": [a.to_json() for a in alerts],
                            "cursor": _ENGINE.cursor(),
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

    def do_POST(self) -> None:                          # noqa: N802
        route = urlparse(self.path).path.rstrip("/") or "/"

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
    except Exception as e:                  # a warm failure is not fatal - the
        log.warning("warm-up skipped: %s", e)   # request path still builds

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
