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

BINDING
-------
Defaults to 0.0.0.0 so a phone on the same wifi can reach it, and prints the
LAN address to point the app at. That is also why it must never grow a write
endpoint without authentication in front of it.
"""
from __future__ import annotations

import json
import logging
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


def _now_iso() -> str:
    return utc_now().isoformat()


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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self) -> None:                           # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        q = parse_qs(parsed.query)

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
                                          "/api/consensus", "/api/symbols"]})
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
                from newsfeed.schedule import upcoming
                days = arg("days", 21)
                self._send({"events": [e.to_json()
                                       for e in upcoming(within_days=days)]})
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

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)


def serve(host: str = "0.0.0.0", port: int = 8787) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    httpd = ThreadingHTTPServer((host, port), Handler)
    ip = lan_ip()
    print(f"\n  TradingBot API on http://{ip}:{port}")
    print(f"    iOS simulator     http://localhost:{port}")
    print(f"    Android emulator  http://10.0.2.2:{port}")
    print(f"    physical phone    http://{ip}:{port}   (same wifi)")
    print(f"\n  read-only. it does not trade. Ctrl-C to stop\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
