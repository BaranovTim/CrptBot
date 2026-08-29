#!/usr/bin/env python3
"""Create and manage ThusIldy accounts.

    python3 manage_accounts.py create-admin tim
    python3 manage_accounts.py grant alice --days 30
    python3 manage_accounts.py revoke alice
    python3 manage_accounts.py passwd tim
    python3 manage_accounts.py list

The password is read with `getpass` — never from an argument, never from the
environment. A password on a command line ends up in shell history, in `ps`
output for every other user on the box, and in any process listing a
monitoring agent happens to collect.

`grant` exists so subscriptions can be handed out before Stripe is wired, and
so you can comp somebody without touching the payment system.
"""
from __future__ import annotations

import argparse
import getpass
import sys
import time

from api.accounts import AuthError, get_accounts


def _read_password(confirm: bool = True) -> str:
    pw = getpass.getpass("password: ")
    if confirm and pw != getpass.getpass("again: "):
        sys.exit("passwords did not match")
    return pw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("create-admin", "create", "passwd"):
        p = sub.add_parser(name)
        p.add_argument("identifier")

    g = sub.add_parser("grant")
    g.add_argument("identifier")
    g.add_argument("--days", type=int, default=30,
                   help="length of the subscription (default 30)")

    r = sub.add_parser("revoke")
    r.add_argument("identifier")

    d = sub.add_parser("delete")
    d.add_argument("identifier")

    sub.add_parser("list")

    a = ap.parse_args()
    acc = get_accounts()

    try:
        if a.cmd in ("create-admin", "create"):
            tier = "admin" if a.cmd == "create-admin" else "free"
            u = acc.register(a.identifier, _read_password(), tier=tier)
            print(f"created {u.identifier} ({u.tier})")
            if tier == "admin":
                print("full access, no subscription needed, never expires")

        elif a.cmd == "passwd":
            acc.set_password(a.identifier, _read_password())
            print(f"password changed for {a.identifier}; "
                  "existing sessions signed out")

        elif a.cmd == "grant":
            ends = time.time() + a.days * 86400
            u = acc.set_tier(a.identifier, "pro", ends)
            print(f"{u.identifier} -> pro until "
                  f"{time.strftime('%Y-%m-%d', time.localtime(ends))}")

        elif a.cmd == "revoke":
            u = acc.set_tier(a.identifier, "free", None)
            print(f"{u.identifier} -> free")

        elif a.cmd == "delete":
            acc.delete(a.identifier)
            print(f"{a.identifier} deleted, sessions revoked")

        elif a.cmd == "list":
            users = sorted(acc._users.values(), key=lambda u: u.created_at)
            if not users:
                print("no accounts yet")
                return 0
            print(f"{'identifier':<20} {'tier':<7} {'entitled':<9} until")
            for u in users:
                until = "-" if not u.subscription_ends else time.strftime(
                    "%Y-%m-%d", time.localtime(u.subscription_ends))
                print(f"{u.identifier:<20} {u.tier:<7} "
                      f"{str(u.entitled):<9} {until}")
    except AuthError as e:
        sys.exit(str(e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
