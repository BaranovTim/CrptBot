"""Fills -> trades. One episode is the position leaving zero and coming
back to it (or flipping); every fill between belongs to it.

Exact, not inferred: every Hyperliquid fill states `startPosition`, the
position before it, so the fill's role (open / add / reduce / close /
flip) is arithmetic. The same code as research/trader_patterns.py, kept
here so the tracker's selection and the research read one definition.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from marketdata.hyperliquid import symbol_for


@dataclass
class Episode:
    coin: str
    symbol: str                 # Binance symbol, "" when not a perpetual we map
    side: str                   # LONG | SHORT
    t_open_ms: int
    t_close_ms: int
    entry_vwap: float
    exit_vwap: float
    max_notional: float
    pnl: float                  # realised, net of fees
    n_entry: int
    n_exit: int
    maker_entry: float          # share of entry size on resting orders
    liquidated: bool

    @property
    def hold_hours(self) -> float:
        return (self.t_close_ms - self.t_open_ms) / 3.6e6

    @property
    def ret_pct(self) -> float:
        s = 1.0 if self.side == "LONG" else -1.0
        return 100.0 * s * (self.exit_vwap / self.entry_vwap - 1.0)


def reconstruct(fills: List[dict]) -> List[Episode]:
    by_coin: Dict[str, List[dict]] = defaultdict(list)
    for f in fills:
        by_coin[str(f.get("coin", ""))].append(f)
    out: List[Episode] = []
    for coin, fs in by_coin.items():
        sym = symbol_for(coin) or ""
        fs.sort(key=lambda x: (int(x.get("time", 0)), x.get("tid", 0)))
        cur: Optional[dict] = None

        def finish(ep: dict) -> None:
            e_sz = sum(s for _, s, _ in ep["entry"]); x_sz = sum(s for _, s, _ in ep["exit"])
            if e_sz <= 0 or x_sz <= 0:
                return
            out.append(Episode(
                coin=coin, symbol=sym, side=ep["side"],
                t_open_ms=ep["t_open"], t_close_ms=ep["t_close"],
                entry_vwap=sum(p * s for p, s, _ in ep["entry"]) / e_sz,
                exit_vwap=sum(p * s for p, s, _ in ep["exit"]) / x_sz,
                max_notional=ep["max_notional"], pnl=ep["pnl"] - ep["fees"],
                n_entry=len(ep["entry"]), n_exit=len(ep["exit"]),
                maker_entry=sum(s for _, s, m in ep["entry"] if m) / e_sz,
                liquidated=ep["liq"]))

        for f in fs:
            try:
                px, sz = float(f["px"]), float(f["sz"])
                start = float(f.get("startPosition", 0) or 0)
                pnl = float(f.get("closedPnl", 0) or 0)
                fee = float(f.get("fee", 0) or 0)
                t = int(f["time"])
            except (TypeError, ValueError, KeyError):
                continue
            buy = f.get("side") == "B"
            maker = not bool(f.get("crossed", True))
            liq = bool(f.get("liquidation"))
            end = start + (sz if buy else -sz)
            if abs(end) < 1e-12:
                end = 0.0
            if start == 0:
                role = "open"
            elif end == 0:
                role = "close"
            elif np.sign(end) == np.sign(start):
                role = "open" if abs(end) > abs(start) else "close"
            else:
                role = "flip"
            if role == "open":
                if cur is None:
                    cur = {"side": "LONG" if buy else "SHORT", "t_open": t, "t_close": t,
                           "entry": [], "exit": [], "max_notional": 0.0,
                           "pnl": 0.0, "fees": 0.0, "liq": False}
                cur["entry"].append((px, sz, maker)); cur["fees"] += fee
                cur["max_notional"] = max(cur["max_notional"], abs(end) * px)
            elif role == "close":
                if cur is None:
                    continue                      # history starts mid-position
                cur["exit"].append((px, sz, maker)); cur["pnl"] += pnl; cur["fees"] += fee
                cur["liq"] |= liq; cur["t_close"] = t
                if end == 0:
                    finish(cur); cur = None
            else:
                closing, opening = abs(start), abs(end)
                if cur is not None:
                    cur["exit"].append((px, closing, maker)); cur["pnl"] += pnl
                    cur["fees"] += fee * closing / sz; cur["liq"] |= liq; cur["t_close"] = t
                    finish(cur)
                cur = {"side": "LONG" if end > 0 else "SHORT", "t_open": t, "t_close": t,
                       "entry": [(px, opening, maker)], "exit": [],
                       "max_notional": opening * px, "pnl": 0.0,
                       "fees": fee * opening / sz, "liq": False}
    return out
