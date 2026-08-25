"""Stages 4 and 5: probability -> expected value -> position size.

There are two formulas in this project and keeping them separate is what
makes the whole design tractable. Stage 2-3 turns data into a probability
and is FITTED. This file turns a probability into a trade and is WRITTEN BY
HAND. Nothing here is learned; nothing here touches outcomes.

EXPECTED VALUE
    EV = p * TP_distance - (1 - p) * SL_distance - round_trip_cost

    Concrete: barriers at +2% / -1%, calibrated p = 0.61, costs 0.1%
        EV = 0.61*2.0 - 0.39*1.0 - 0.1 = +0.73%    -> trade

    Same 61% confidence with barriers at +0.5% / -1%
        EV = 0.305 - 0.39 - 0.1 = -0.185%          -> do not

    This is why probability alone can never be the entry rule, and why the
    barriers used here must be the same ones the labels were built from.
    They are: both come from the same Agent5Config.

POSITION SIZE (fractional Kelly)
    b  = TP / SL                       payoff ratio
    f* = (p*b - (1-p)) / b             full Kelly fraction
    size = kelly_fraction * f* * equity

    Quarter Kelly, not full. Full Kelly is optimal only if p is exactly
    right, and yours never is. Overbetting a slightly-wrong p does not
    reduce returns gracefully - it compounds toward ruin.

LONG ONLY, AND THAT IS NOT A LIMITATION - IT IS HONESTY
    The triple barrier with k_up=2, k_dn=1 asks "did price rise 2 ATR
    before falling 1 ATR" - the payoff geometry of a LONG trade. A low p
    does NOT mean a short with the same 2:1 payoff would win, because for
    a short the barriers are the wrong way round. Shorting needs its own
    model fitted on mirrored labels. Inverting p here would be free money
    on paper and a loss in the market.

RISK LIMITS ARE HARD
    max_position_pct is applied last and cannot be argued with by any
    probability, however confident. The plan is explicit that guardrails
    must sit outside anything that reasons.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .config import Agent5Config


@dataclass
class Decision:
    """One bar's trading decision, with every intermediate number kept."""

    timestamp: object
    probability: float          # calibrated
    ev_pct: float               # expected value after costs, in %
    tp_pct: float
    sl_pct: float
    kelly_fraction: float       # full Kelly f*, before scaling
    position_pct: float         # final size, % of equity, after all limits
    action: str                 # "long" | "flat"
    reason: str

    def __str__(self) -> str:
        if self.action == "flat":
            return (f"[{self.timestamp}] FLAT  p={self.probability:.1%} "
                    f"EV={self.ev_pct:+.3f}%  ({self.reason})")
        return (f"[{self.timestamp}] LONG  p={self.probability:.1%} "
                f"EV={self.ev_pct:+.3f}%  size={self.position_pct:.2f}% of equity "
                f"(TP +{self.tp_pct:.2f}% / SL -{self.sl_pct:.2f}%)")


def expected_value_pct(p, tp_pct, sl_pct, cost_pct: float):
    """EV of a long trade in percent, after round-trip costs."""
    p = np.asarray(p, dtype=float)
    tp = np.asarray(tp_pct, dtype=float)
    sl = np.asarray(sl_pct, dtype=float)
    return p * tp - (1.0 - p) * sl - cost_pct


def kelly_full(p, tp_pct, sl_pct):
    """Full Kelly fraction f* = (p*b - (1-p)) / b, clipped at 0."""
    p = np.asarray(p, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        b = np.asarray(tp_pct, dtype=float) / np.asarray(sl_pct, dtype=float)
        f = (p * b - (1.0 - p)) / b
    f = np.where(np.isfinite(f), f, 0.0)
    return np.clip(f, 0.0, 1.0)          # negative f* means "do not take it"


def decide(
    probability,
    tp_pct,
    sl_pct,
    cfg: Agent5Config,
    timestamps=None,
) -> pd.DataFrame:
    """Vectorised decisions for a whole series of bars."""
    p = np.asarray(probability, dtype=float)
    tp = np.asarray(tp_pct, dtype=float)
    sl = np.asarray(sl_pct, dtype=float)

    ev = expected_value_pct(p, tp, sl, cfg.round_trip_cost_pct)
    f_full = kelly_full(p, tp, sl)
    size = cfg.kelly_fraction * f_full * 100.0        # as % of equity
    size = np.minimum(size, cfg.max_position_pct)     # hard cap, applied last

    tradeable = np.isfinite(ev) & np.isfinite(p)
    take = tradeable & (ev > cfg.ev_threshold_pct) & (size > 0)

    reason = np.where(~tradeable, "no probability",
              np.where(ev <= cfg.ev_threshold_pct,
                       "EV below threshold after costs", "Kelly size is zero"))

    return pd.DataFrame({
        "probability": p,
        "ev_pct": ev,
        "tp_pct": tp,
        "sl_pct": sl,
        "kelly_full": f_full,
        "position_pct": np.where(take, size, 0.0),
        "action": np.where(take, "long", "flat"),
        "reason": np.where(take, "EV clears threshold", reason),
    }, index=timestamps if timestamps is not None else None)


def simulate(decisions: pd.DataFrame, y: np.ndarray,
             cfg: Agent5Config) -> pd.Series:
    """What the decisions would have returned, using the label outcomes.

    NOT A BACKTEST. It reuses the triple-barrier outcome as the trade result,
    which assumes perfect fills at the barrier and ignores overlapping
    positions, funding, partial fills and exchange downtime. It answers one
    narrow question - does the EV rule turn these probabilities into positive
    expectancy - and nothing else. Treat any number from it as an upper bound.
    """
    y = np.asarray(y, dtype=float)
    size = decisions["position_pct"].to_numpy() / 100.0
    tp = decisions["tp_pct"].to_numpy()
    sl = decisions["sl_pct"].to_numpy()
    # win -> +TP, loss -> -SL, both scaled by size, costs charged on any trade
    gross = np.where(y > 0.5, tp, -sl)
    net = size * gross - np.where(size > 0, cfg.round_trip_cost_pct * size, 0.0)
    return pd.Series(net, index=decisions.index, name="return_pct")
