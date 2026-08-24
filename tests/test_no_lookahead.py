"""The test that justifies computing features over the whole series at once.

Compute features on data up to bar t. Compute them again on data up to
t + 100. Compare the row for bar t. An honest detector gives the same numbers
both times: what it knew at t cannot depend on what happened after t.

A detector that peeks — most obviously via pivots marked at the bar where the
extremum occurred rather than the bar it was confirmed — produces different
values for the same bar depending on how much future was in the frame. That
divergence is the only cheap way to catch this class of bug, and this class
of bug produces backtests that look wonderful and never reproduce.

Run it before trusting any change to the detectors:

    python3 tests/test_no_lookahead.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent1 import PatternAgent
from agent1.schema import FEATURE_COLUMNS
from tests.synthetic import make_bars

RTOL = 1e-9
ATOL = 1e-9


def _rows_match(a: pd.Series, b: pd.Series):
    """NaN == NaN here; anything else must agree to floating-point noise."""
    bad = []
    for col in FEATURE_COLUMNS:
        x, y = a[col], b[col]
        if pd.isna(x) and pd.isna(y):
            continue
        if pd.isna(x) != pd.isna(y):
            bad.append((col, x, y))
        elif not np.isclose(x, y, rtol=RTOL, atol=ATOL):
            bad.append((col, x, y))
    return bad


def test_no_lookahead(bars=None, t_range=range(700, 800), horizon=100):
    bars = make_bars(1200) if bars is None else bars
    agent = PatternAgent()

    long_features = agent.compute(bars.iloc[: max(t_range) + horizon])
    failures = []

    for t in t_range:
        short = agent.compute(bars.iloc[:t])
        bad = _rows_match(short.iloc[-1], long_features.iloc[t - 1])
        if bad:
            failures.append((t, bad))

    if failures:
        t, bad = failures[0]
        detail = "\n".join(
            f"    {c:28} short={x!r}  long={y!r}" for c, x, y in bad[:10]
        )
        raise AssertionError(
            f"LOOKAHEAD DETECTED in {len(failures)}/{len(t_range)} bars.\n"
            f"  First divergence at bar {t}:\n{detail}\n"
            f"  A feature changed value for a bar once future bars were added, "
            f"which means it was reading them."
        )
    return True


def test_no_lookahead_wick_mode():
    from agent1 import Agent1Config

    bars = make_bars(900, seed=11)
    agent = PatternAgent(Agent1Config(break_mode="wick"))
    long_features = agent.compute(bars.iloc[:800])
    for t in range(600, 700):
        short = agent.compute(bars.iloc[:t])
        bad = _rows_match(short.iloc[-1], long_features.iloc[t - 1])
        assert not bad, f"lookahead in wick mode at bar {t}: {bad[:5]}"
    return True


def test_deterministic():
    """Same window in, same numbers out. Agent 1 keeps no state between calls."""
    bars = make_bars(600, seed=3)
    a, b = PatternAgent(), PatternAgent()
    f1, f2 = a.compute(bars), b.compute(bars)
    pd.testing.assert_frame_equal(f1, f2)
    f3 = a.compute(bars)          # same instance, called twice
    pd.testing.assert_frame_equal(f1, f3)
    return True


if __name__ == "__main__":
    for fn in (test_no_lookahead, test_no_lookahead_wick_mode, test_deterministic):
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}\n{e}")
            sys.exit(1)
    print("\nAll lookahead tests passed.")
