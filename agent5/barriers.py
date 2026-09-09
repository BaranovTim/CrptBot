"""Where to put the take profit and the stop loss, and why the ratio is a trap.

THE RESULT THAT DECIDES EVERYTHING BELOW
========================================
For a price following arithmetic Brownian motion with drift `mu` and
volatility `sigma`, starting at 0, the probability of touching +a before -b
is exact and classical (the two-barrier / gambler's-ruin first-passage
problem, via the exponential martingale):

        theta = 2*mu / sigma**2

        P(up first) = (1 - e^(theta*b)) / (e^(-theta*a) - e^(theta*b))

and in the driftless limit mu -> 0 it collapses to something anyone can
check by hand:

        P(up first) = b / (a + b)

VERIFIED, not quoted. `test_barriers.py` runs a properly-scaled random walk
against these formulas; a coarse walk whose single step can leap a barrier
does NOT obey them, which is the mistake that makes most published
simulations of this disagree with the theory.

WHAT THAT MEANS FOR RISK:REWARD
-------------------------------
Put the driftless probability into the expected value of a trade:

        EV = P*a - (1-P)*b
           = [b/(a+b)]*a - [a/(a+b)]*b
           = 0                                    exactly, for EVERY a:b

A 1:1 wins half the time. A 3:1 wins a quarter of the time. A 1:3 wins three
times in four. All three have identically zero expectation before costs, and
identically MINUS THE COSTS after them. Widening the target does not create
profit; it moves the same expectation from "often, small" to "rarely, big".

So the familiar advice to "always take at least 1:2" is not wrong so much as
empty on its own. It buys nothing unless something makes the price more
likely to travel to the target than the random walk says — which is drift,
which is what the model is for.

WHAT THE GEOMETRY ACTUALLY CONTROLS
-----------------------------------
Rearranging EV > threshold gives the probability a trade must clear:

        p_breakeven = (b + cost + threshold) / (a + b)
                    = P_no_edge + (cost + threshold) / span

where span = a + b. The second term is the whole story: THE EDGE YOU NEED
OVER A COIN FLIP DEPENDS ONLY ON THE SPAN, NEVER ON HOW THE SPAN IS SPLIT.

That is why this project's barriers are symmetric and why that costs nothing.
It is also why the short timeframes are hard and the long ones are not: at 1m
the span is ~1% and a 0.10% round trip is a tenth of it, so the model must
beat chance by ten points of probability. At 1d the span is ~8% and the same
fee asks for barely one. The timeframe that looks "quiet" is quiet because
the arithmetic makes it so.

WHAT THE LITERATURE ADDS
------------------------
Kaminski & Lo (Journal of Financial Markets, 2014), "When do stop-loss rules
stop losses?": under a random walk a stop-loss rule always REDUCES expected
return; it adds value only when returns have momentum, and it hurts under
mean reversion. That is the same statement as the algebra above, arrived at
from the other end, and it is the reason this module refuses to pretend a
stop is free insurance. A stop is a claim about the shape of the process.

Lopez de Prado's triple barrier (Advances in Financial Machine Learning,
2018) is what this project already labels with, and it permits asymmetric
multiples. The important discipline it brings is not the asymmetry — it is
that THE LABEL AND THE TRADE MUST SHARE THE GEOMETRY. A probability fitted
against +2/-1 barriers says nothing about a trade taken at +1/-1, because it
is the answer to a different question.

Sources consulted are listed in the project notes; the formulas here are
derived and tested rather than copied.
"""
from __future__ import annotations

import math
from typing import Optional


def no_edge_probability(tp_pct: float, sl_pct: float) -> float:
    """P(take profit first) if price were a driftless random walk.

    The number a model has to BEAT to have shown anything at all. A 33%
    win rate on 2:1 barriers is not a bad model — it is exactly chance, and
    a screen reporting it as a 33% probability without this alongside
    invites the reader to think it is bad.
    """
    span = tp_pct + sl_pct
    if span <= 0:
        return float("nan")
    return sl_pct / span


def breakeven_probability(tp_pct: float, sl_pct: float,
                          cost_pct: float, threshold_pct: float = 0.0) -> float:
    """The probability at which a trade stops losing money.

    Generalises the symmetric formula this project shipped with — for
    tp == sl it reduces to 0.5 + (cost + threshold)/span, identically — to
    barriers that need not match. Kept separate from `no_edge_probability`
    because the difference between them IS the required edge, and naming it
    makes that visible.
    """
    span = tp_pct + sl_pct
    if span <= 0:
        return float("nan")
    return (sl_pct + cost_pct + threshold_pct) / span


def required_edge(tp_pct: float, sl_pct: float,
                  cost_pct: float, threshold_pct: float = 0.0) -> float:
    """How far above chance the model must be, in probability points.

    (cost + threshold) / span — and note what is NOT in it: the ratio. Two
    geometries with the same span demand exactly the same edge however
    lopsided they are.
    """
    span = tp_pct + sl_pct
    if span <= 0:
        return float("nan")
    return (cost_pct + threshold_pct) / span


def edge_over_random(p: float, tp_pct: float, sl_pct: float) -> float:
    """What the model actually claims, above chance, in probability points.

    Positive is the only interesting case, and its SIZE is the thing to
    compare against `required_edge`. This is the honest reading of a
    probability against a set of barriers, and it is what a raw p hides.
    """
    return p - no_edge_probability(tp_pct, sl_pct)


def drift_implied_probability(mu: float, sigma: float,
                              tp_pct: float, sl_pct: float) -> float:
    """P(take profit first) under drift `mu` and volatility `sigma`.

    Both in the same units as the barriers, per unit of the same time. Used
    to ask the reverse question: what drift would justify the probability a
    model is claiming? A model asserting 60% on 1:1 barriers is asserting a
    drift, and that drift can be compared with anything the asset has ever
    actually done.
    """
    a, b = tp_pct, sl_pct
    if a <= 0 or b <= 0 or sigma <= 0:
        return float("nan")
    if abs(mu) < 1e-15:
        return b / (a + b)
    theta = 2.0 * mu / (sigma ** 2)
    # Written to avoid overflow: e^(theta*b) is enormous for a big drift.
    try:
        num = 1.0 - math.exp(theta * b)
        den = math.exp(-theta * a) - math.exp(theta * b)
        if den == 0:
            return float("nan")
        return num / den
    except OverflowError:
        return 1.0 if mu > 0 else 0.0


# Funding on a perpetual, per settlement, as a fraction of notional.
#
# Binance settles every 8 hours and the rate floats; the long-run median on
# major USDT perpetuals sits near 0.01%, which is the figure used when
# nothing better is known. It is a COST OF TIME, unlike the fee, and a
# 1d model holding two days pays it six times — 0.06%, against a round trip
# assumed to be 0.10%. Ignoring it understates the cost of the long
# timeframes by more than half.
DEFAULT_FUNDING_PER_8H_PCT = 0.01
FUNDING_INTERVAL_HOURS = 8.0


def holding_cost_pct(hold_hours: float,
                     round_trip_pct: float,
                     funding_per_8h_pct: Optional[float] = None,
                     side: str = "LONG") -> float:
    """Round-trip fees PLUS the funding a position pays while it is open.

    WHY FUNDING BELONGS IN THE BARRIER MATH
        `required_edge` is (cost + threshold) / span. Funding is part of
        cost, and unlike the fee it grows with the horizon — so it falls
        hardest on exactly the timeframes whose wide spans were supposed to
        make costs negligible. Leaving it out flatters the 4h and 1d models
        against the 1m one.

    SIGN. A positive funding rate is paid BY longs TO shorts, so a short
    earns it. Modelled honestly rather than as an unsigned drag: pretending
    a short pays it would understate short trades in the one direction that
    makes them look worse than they are.
    """
    rate = (DEFAULT_FUNDING_PER_8H_PCT if funding_per_8h_pct is None
            else funding_per_8h_pct)
    settlements = max(0.0, hold_hours) / FUNDING_INTERVAL_HOURS
    funding = rate * settlements
    return round_trip_pct + (funding if side.upper() != "SHORT" else -funding)
