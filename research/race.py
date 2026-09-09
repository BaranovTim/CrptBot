"""Walk every exit rule through real bars and score them the same way.

THE ONE THING THIS FILE MUST NOT GET WRONG
    Look-ahead. A rule sees bars up to and including t and nothing after; the
    outcome is then resolved by walking FORWARD from t+1. The rules in
    `exit_rules.py` are all built from rolling windows for that reason — a
    `.max()` over the whole series would leak the future into the stop and
    every result here would be fiction.

THE AMBIGUOUS BAR
    One bar can span both barriers, and OHLC cannot say which came first.
    Counted as a LOSS, matching `agent5/labels.py`. Anything kinder inflates
    every rule that uses a wide stop, which is most of the interesting ones.

WHAT IS MEASURED
    Hit rate, and next to it the rate a driftless random walk would give for
    the same geometry — `sl / (tp + sl)`. The difference between those two is
    the only evidence a rule carries any information. Expected value after
    costs is the thing that decides the race.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class Result:
    rule: str
    symbol: str
    interval: str
    trades: int
    wins: int
    timeouts: int
    hit_rate: float
    chance_rate: float          # sl/(tp+sl), the no-drift baseline
    edge: float                 # hit_rate - chance_rate
    ev_pct: float               # mean % per trade after costs
    ev_r: float                 # mean outcome in units of risk
    median_span_pct: float

    @property
    def label(self) -> str:
        return f"{self.rule} · {self.symbol} {self.interval}"


def run(bars: pd.DataFrame, tp: np.ndarray, sl: np.ndarray, *,
        max_hold: int, cost_pct: float, step: int = 1,
        side: str = "LONG") -> Optional[Dict[str, float]]:
    """Resolve one rule over one series. Vectorised where it can be.

    `step` samples every Nth bar. Overlapping windows are not independent —
    two entries a bar apart share almost all of their outcome — so the honest
    count of observations comes from spacing them at least one holding period
    apart, which is what the caller does.
    """
    high = bars["high"].values
    low = bars["low"].values
    close = bars["close"].values
    n = len(close)
    if n < max_hold + 5:
        return None

    short = side.upper() == "SHORT"
    outcomes: List[float] = []      # in units of risk (R)
    pct: List[float] = []
    wins = timeouts = 0
    spans: List[float] = []

    for i in range(20, n - max_hold - 1, step):
        t, s = float(tp[i]), float(sl[i])
        if not (np.isfinite(t) and np.isfinite(s)) or t <= 0 or s <= 0:
            continue
        entry = close[i]
        if entry <= 0:
            continue
        # For a short the barriers mirror: the target is BELOW and the stop
        # ABOVE. Getting this backwards would score every short as its own
        # opposite, which is the single easiest way to produce a beautiful
        # and entirely false result.
        up_barrier = entry + (s if short else t)
        dn_barrier = entry - (t if short else s)

        w = slice(i + 1, i + 1 + max_hold)
        hs, ls = high[w], low[w]
        hit_up = np.flatnonzero(hs >= up_barrier)
        hit_dn = np.flatnonzero(ls <= dn_barrier)
        first_up = hit_up[0] if hit_up.size else np.inf
        first_dn = hit_dn[0] if hit_dn.size else np.inf

        span_pct = (t + s) / entry * 100.0
        spans.append(span_pct)
        tp_pct_i = t / entry * 100.0
        sl_pct_i = s / entry * 100.0

        if first_up == np.inf and first_dn == np.inf:
            timeouts += 1
            exit_px = close[min(i + max_hold, n - 1)]
            move = (exit_px - entry) / entry * 100.0
            r = (-move if short else move)
            pct.append(r - cost_pct)
            outcomes.append((r - cost_pct) / sl_pct_i)
            continue

        # SAME BAR, BOTH BARRIERS -> loss. See the module docstring.
        won = (first_up < first_dn) if not short else (first_dn < first_up)
        if first_up == first_dn:
            won = False
        if won:
            wins += 1
            pct.append(tp_pct_i - cost_pct)
            outcomes.append((tp_pct_i - cost_pct) / sl_pct_i)
        else:
            pct.append(-sl_pct_i - cost_pct)
            outcomes.append((-sl_pct_i - cost_pct) / sl_pct_i)

    if len(pct) < 30:
        return None
    decided = len(pct) - timeouts
    med_tp = float(np.median([t for t in tp[20:n - max_hold - 1:step] if np.isfinite(t) and t > 0]))
    med_sl = float(np.median([s for s in sl[20:n - max_hold - 1:step] if np.isfinite(s) and s > 0]))
    chance = med_sl / (med_tp + med_sl) if (med_tp + med_sl) > 0 else float("nan")
    return {
        "trades": len(pct),
        "wins": wins,
        "timeouts": timeouts,
        "hit_rate": wins / decided if decided else float("nan"),
        "chance_rate": chance,
        "ev_pct": float(np.mean(pct)),
        "ev_r": float(np.mean(outcomes)),
        "median_span_pct": float(np.median(spans)),
    }
