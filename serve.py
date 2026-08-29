#!/usr/bin/env python3
"""Serve the read-only JSON API the mobile app reads.

    python3 serve.py                 # 0.0.0.0:8787
    python3 serve.py --port 9000

It records nothing and trades nothing. Run `collect.py` alongside it if you
want the bars it serves to stay current.
"""
from __future__ import annotations

import argparse

from api.server import serve


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read-only TradingBot JSON API.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--token", default=None,
                   help="shared secret clients must send as "
                        "'Authorization: Bearer <token>'. Also read from "
                        "TRADINGBOT_TOKEN. Required unless --host is loopback.")
    a = p.parse_args(argv)
    serve(a.host, a.port, token=a.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
