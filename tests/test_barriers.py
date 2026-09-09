"""Where take profit and stop loss belong, and the arithmetic that decides it.

WHAT THESE GUARD

  THE RATIO FALLACY   "Always take 1:2" is the most repeated advice in
        trading and, on its own, it is empty: under a driftless walk EVERY
        ratio has exactly zero expectation before costs. If someone later
        "improves" the barriers by widening the target and expects free
        money, this test is the thing that says no.

  THE SPAN IS WHAT MATTERS   The edge a trade needs over chance is
        (cost + threshold) / span and does NOT contain the ratio. Two
        geometries with the same span demand the same edge however lopsided.

  THE FORMULA ITSELF   Checked against a random walk rather than quoted.
        The simulation is scaled so a single step cannot leap a barrier —
        get that wrong and the walk disagrees with the theory, which is the
        usual reason published simulations of this "disprove" it.

  FUNDING IS A COST OF TIME   It grows with the holding period, so it lands
        hardest on the long timeframes whose wide spans were supposed to
        make costs irrelevant. A short EARNS positive funding; modelling it
        as an unsigned drag would understate shorts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent5.barriers import (breakeven_probability, drift_implied_probability,
                             edge_over_random, holding_cost_pct,
                             no_edge_probability, required_edge)


def test_every_risk_reward_ratio_has_the_same_expectation():
    """THE ONE THAT MATTERS. Under a driftless walk, EV is exactly zero for
    every ratio — 1:1, 3:1, 1:3 alike. Widening the target moves expectation
    from "often and small" to "rarely and big" and creates none of it."""
    for tp, sl in ((1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (1.0, 3.0), (0.5, 2.0)):
        p = no_edge_probability(tp, sl)
        ev = p * tp - (1 - p) * sl
        assert abs(ev) < 1e-12, f"TP {tp} / SL {sl} implied EV {ev}"
    return True


def test_the_required_edge_depends_on_the_span_and_not_the_ratio():
    """(cost + threshold)/span contains no ratio term. This is why symmetric
    barriers cost this project nothing, and why the short timeframes are
    hard: at 1m a 0.10% fee is a tenth of a 1% span."""
    a = required_edge(1.0, 1.0, 0.10)
    for tp, sl in ((1.5, 0.5), (0.5, 1.5), (1.9, 0.1)):
        assert abs(required_edge(tp, sl, 0.10) - a) < 1e-12

    # a wider span asks for less edge, in exactly the ratio of the spans
    wide = required_edge(4.0, 4.0, 0.10)
    assert abs(wide - a / 4.0) < 1e-12

    # and the project's own timeframes, sanity-checked against the note in
    # `monitor.py`: 1m needs ~10 points over chance, 1d barely one
    assert required_edge(0.5, 0.5, 0.10) > 0.09
    assert required_edge(4.1, 4.1, 0.10) < 0.015
    return True


def test_breakeven_reduces_to_the_symmetric_formula_it_replaces():
    """The app shipped `0.5 + (threshold + cost)/span` for equal barriers.
    The general form must agree with it exactly, or the number on the
    dashboard would change meaning the day the barriers stopped matching."""
    for half in (0.25, 0.5, 1.5, 4.0):
        old = 0.5 + (0.05 + 0.10) / (2 * half)
        new = breakeven_probability(half, half, 0.10, 0.05)
        assert abs(old - new) < 1e-12, (half, old, new)
    return True


def test_the_first_passage_formula_matches_a_random_walk():
    """Simulated, not quoted.

    The step size is set to a fortieth of the nearer barrier ON PURPOSE: the
    continuous-time result describes a path that cannot jump a barrier, and a
    coarse walk overshoots and disagrees. That mis-scaling is the usual
    reason a simulation appears to refute the formula.
    """
    rng = np.random.default_rng(11)
    sigma = 0.5

    def simulate(mu, a, b, n=20000):
        dt = ((min(a, b) / 40.0) / sigma) ** 2
        x = np.zeros(n)
        alive = np.ones(n, bool)
        up_hits = resolved = 0
        for _ in range(400_000):
            k = int(alive.sum())
            if k == 0:
                break
            x[alive] += mu * dt + sigma * np.sqrt(dt) * rng.standard_normal(k)
            up = alive & (x >= a)
            dn = alive & (x <= -b)
            up_hits += int(up.sum())
            resolved += int(up.sum() + dn.sum())
            alive &= ~(up | dn)
        return up_hits / resolved

    for mu, a, b in ((0.0, 0.01, 0.01), (0.0, 0.02, 0.01),
                     (0.30, 0.02, 0.01), (-0.30, 0.02, 0.01)):
        want = drift_implied_probability(mu, sigma, a, b)
        got = simulate(mu, a, b)
        assert abs(want - got) < 0.015, (mu, a, b, want, got)
    return True


def test_a_probability_is_read_against_chance_not_against_a_half():
    """A 33% win rate on 2:1 barriers is EXACTLY chance, not a bad model.
    Reporting the raw probability without this invites the opposite reading."""
    assert abs(no_edge_probability(2.0, 1.0) - 1 / 3) < 1e-12
    assert abs(edge_over_random(0.333333, 2.0, 1.0)) < 1e-5
    assert edge_over_random(0.40, 2.0, 1.0) > 0.06     # genuinely above chance
    assert edge_over_random(0.55, 1.0, 1.0) > 0.04
    # and a 60% on 1:3 barriers is BELOW chance, which reads as strong and is not
    assert edge_over_random(0.60, 1.0, 3.0) < 0
    return True


def test_funding_is_a_cost_of_time_and_a_short_earns_it():
    """A 1d model holding two days pays funding six times — 0.06% against a
    round trip assumed to be 0.10%. Leaving it out flatters exactly the
    timeframes whose wide spans were supposed to make costs negligible."""
    flat = holding_cost_pct(0.0, 0.10)
    assert abs(flat - 0.10) < 1e-12

    two_days = holding_cost_pct(48.0, 0.10)
    assert abs(two_days - (0.10 + 6 * 0.01)) < 1e-12
    assert two_days > flat * 1.5

    # positive funding is paid by longs TO shorts
    short = holding_cost_pct(48.0, 0.10, side="SHORT")
    assert short < flat
    assert abs(short - (0.10 - 0.06)) < 1e-12

    # and it changes what the long timeframes must clear
    span = 8.2
    assert required_edge(span / 2, span / 2, two_days) > \
           required_edge(span / 2, span / 2, flat)
    return True


def test_drift_is_what_a_probability_is_really_claiming():
    """A model asserting a probability against a geometry is asserting a
    drift. Being able to name it is what makes the claim checkable."""
    # no drift lands exactly on the ratio
    assert abs(drift_implied_probability(0.0, 0.5, 0.02, 0.01) - 1 / 3) < 1e-12
    # upward drift raises it, downward lowers it, monotonically
    up = drift_implied_probability(0.5, 0.5, 0.02, 0.01)
    down = drift_implied_probability(-0.5, 0.5, 0.02, 0.01)
    assert down < 1 / 3 < up
    # and it stays a probability under an absurd drift rather than overflowing
    for mu in (50.0, -50.0, 1e6, -1e6):
        p = drift_implied_probability(mu, 0.5, 0.02, 0.01)
        assert 0.0 <= p <= 1.0, (mu, p)
    return True
