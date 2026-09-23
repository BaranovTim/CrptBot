"""The live record of the 4h calls, traded the way the app tells a person to.

WHY THIS EXISTS
    Every number the app has quoted about its calls came from the
    walk-forward: out of time, honest, and still a backtest. The question a
    person actually asks -- "how often has it been right since I started
    using it?" -- needs the calls as they happened: which orders were
    placed, which filled, which reached the target, which were stopped.

    The monitor already replays each side's recent calls through the
    serving policy (monitor.resting_orders) at every closed bar, once per
    sensitivity level, and hands the orders over as `Analysis.order_book`.
    This keeps them. Nothing is reconstructed after the fact: an order
    enters the ledger the first time a closed-bar read sees it, and a
    finished one is never rewritten.

WHAT IS COUNTED
    A TRADE is an order that filled. It ends at the target, the stop, or
    the time limit, and its return is from the fill, less 0.10% for the
    round trip. An order that EXPIRED unfilled is not a trade (nobody was
    in it), and one REPLACED by a newer call's order is the same order,
    moved -- neither counts toward the hit rate. Each sensitivity level is
    its own record: a person on "strong" never saw the smaller calls.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

COST_PCT = 0.10
LEVELS = ("strong", "medium", "small")
FINAL = ("target", "stop", "timeout", "expired", "replaced")
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data_cache" / "ledger" / "orders.v1.json"


def _iso(t) -> Optional[str]:
    if t is None:
        return None
    try:
        return pd.Timestamp(t).isoformat()
    except (TypeError, ValueError):
        return None


class OrderLedger:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._rows: Dict[str, dict] = {}
        self.since: Optional[str] = None
        self._load()

    # ------------------------------------------------------------ storage
    def _load(self) -> None:
        try:
            blob = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        self._rows = {k: v for k, v in (blob.get("orders") or {}).items() if isinstance(v, dict)}
        self.since = blob.get("since")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".part")
        tmp.write_text(json.dumps({"since": self.since, "orders": self._rows}))
        tmp.replace(self.path)

    # ------------------------------------------------------------- record
    def record(self, symbol: str, interval: str, book: Dict[str, List[dict]],
               pool: str = "", not_before=None, now=None) -> int:
        """Keep every order in `book` ({level: [order rows]}). Returns how many
        rows were new or changed. A finished order is final: a later replay
        whose window no longer reaches back to its start cannot rewrite it.

        `not_before`: orders placed before it are left out. It is the time
        the model was installed -- the score book behind older ranks was
        back-filled by that model, in-sample, so an order it "placed" then
        is a backtest, not a record."""
        changed = 0
        floor = _iso(not_before)
        with self._lock:
            for level, rows in (book or {}).items():
                if level not in LEVELS:
                    continue
                for r in rows or ():
                    placed = _iso(r.get("placed_at"))
                    if placed is None or (floor is not None and placed < floor):
                        continue
                    side = "long" if r.get("long") else "short"
                    key = f"{symbol}|{interval}|{side}|{level}|{placed}"
                    old = self._rows.get(key)
                    if old is not None and old.get("state") in FINAL:
                        continue
                    row = {"symbol": symbol, "interval": interval, "side": side, "level": level,
                           "placed_at": placed, "state": r.get("state"),
                           "limit": r.get("limit"), "stop": r.get("stop"), "target": r.get("target"),
                           "rank": r.get("rank"), "fill": r.get("fill"),
                           "filled_at": _iso(r.get("filled_at")), "exit": r.get("exit"),
                           "closed_at": _iso(r.get("closed_at")), "ret_pct": r.get("ret_pct"),
                           "taken": bool(r.get("taken")), "scale_price": r.get("scale_price"),
                           "pool": pool}
                    if old is None:
                        row["seen_at"] = _iso(now if now is not None else pd.Timestamp.now(tz="UTC"))
                    else:
                        row["seen_at"] = old.get("seen_at")
                    if row != old:
                        self._rows[key] = row
                        changed += 1
                        if self.since is None or (row["seen_at"] or "") < self.since:
                            self.since = row["seen_at"]
            if changed:
                self._save()
        return changed

    # ------------------------------------------------------------ summary
    def rows(self) -> List[dict]:
        with self._lock:
            return [dict(v) for v in self._rows.values()]

    @staticmethod
    def _stats(rows: Iterable[dict]) -> dict:
        rows = list(rows)
        trades = [r for r in rows if r.get("state") in ("target", "stop", "timeout")
                  and r.get("ret_pct") is not None]
        nets = [float(r["ret_pct"]) - COST_PCT for r in trades]
        wins = [x for x in nets if x > 0]
        return {
            "orders": len([r for r in rows if r.get("state") != "replaced"]),
            "filled": len(trades) + len([r for r in rows if r.get("state") == "filled"]),
            "expired": len([r for r in rows if r.get("state") == "expired"]),
            "open_orders": len([r for r in rows if r.get("state") == "open"]),
            "in_trade": len([r for r in rows if r.get("state") == "filled"]),
            "closed": len(trades),
            "targets": len([r for r in trades if r["state"] == "target"]),
            # a stop AFTER the scale-out is the entry: the part taken off
            # halfway made the trade a win, so it is not counted as a stop
            "stops": len([r for r in trades if r["state"] == "stop" and not r.get("taken")]),
            "back_to_entry": len([r for r in trades if r["state"] == "stop" and r.get("taken")]),
            "partials": len([r for r in rows if r.get("taken")]),
            "timeouts": len([r for r in trades if r["state"] == "timeout"]),
            "wins": len(wins),
            "win_rate": (len(wins) / len(nets)) if nets else None,
            "avg_net_pct": (sum(nets) / len(nets)) if nets else None,
            "sum_net_pct": sum(nets) if nets else 0.0,
        }

    def summary(self, recent: int = 20) -> dict:
        rows = self.rows()
        by_level = {lv: self._stats(r for r in rows if r.get("level") == lv) for lv in LEVELS}
        last = {}
        for lv in LEVELS:
            done = sorted((r for r in rows if r.get("level") == lv
                           and r.get("state") in ("target", "stop", "timeout")),
                          key=lambda r: r.get("closed_at") or "", reverse=True)
            last[lv] = [dict(r, net_pct=(float(r["ret_pct"]) - COST_PCT)
                             if r.get("ret_pct") is not None else None) for r in done[:recent]]
        return {"since": self.since, "cost_pct": COST_PCT, "levels": by_level, "recent": last}


def pool_installed_at(pool: str) -> Optional[pd.Timestamp]:
    """`crypto-4h-20260922T191908Z` -> 2026-09-22 19:19:08 UTC."""
    try:
        return pd.Timestamp(pd.to_datetime(str(pool).rsplit("-", 1)[-1], format="%Y%m%dT%H%M%SZ", utc=True))
    except (ValueError, TypeError):
        return None
