"""Pivot detector: the confirmation-lag contract."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from agent1.pivots import HIGH, LOW, find_pivots
from tests.synthetic import make_bars


def test_confirmation_lag():
    """No pivot may ever be usable before index + right bars have closed."""
    bars = make_bars(400)
    for right in (2, 3, 5):
        pivots = find_pivots(bars["high"], bars["low"], left=3, right=right)
        assert pivots, "detector found nothing at all"
        for p in pivots:
            assert p.confirmed_at == p.index + right, (
                f"pivot at {p.index} confirmed at {p.confirmed_at}, "
                f"expected {p.index + right}"
            )
    return True


def test_finds_obvious_swing():
    """A single clean spike must register as one swing high at its apex."""
    prices = [10, 11, 12, 13, 20, 13, 12, 11, 10, 11, 12]
    h = pd.Series([p + 0.5 for p in prices], dtype=float)
    l = pd.Series([p - 0.5 for p in prices], dtype=float)

    pivots = find_pivots(h, l, left=2, right=2)
    highs = [p for p in pivots if p.kind == HIGH]
    assert len(highs) == 1, f"expected 1 swing high, got {len(highs)}"
    assert highs[0].index == 4, f"apex at bar 4, detector said {highs[0].index}"
    assert highs[0].confirmed_at == 6
    return True


def test_plateau_breaks_to_earliest():
    """Equal highs must yield exactly one pivot, deterministically the first.

    Equal highs are themselves the signal (resting liquidity). If the count
    wobbled with float noise, the liquidity features would wobble with it.
    """
    prices = [10, 11, 12, 15, 15, 15, 12, 11, 10]
    h = pd.Series([p + 0.5 for p in prices], dtype=float)
    l = pd.Series([p - 0.5 for p in prices], dtype=float)

    highs = [p for p in find_pivots(h, l, left=2, right=2) if p.kind == HIGH]
    assert len(highs) == 1, f"plateau produced {len(highs)} pivots, expected 1"
    assert highs[0].index == 3, "the earliest bar of the plateau should win"
    return True


def test_rejects_short_confirm():
    from agent1.config import Agent1Config
    try:
        Agent1Config(pivot_right=3, confirm_bars=1)
    except ValueError:
        return True
    raise AssertionError("confirm_bars < pivot_right should have been rejected")


if __name__ == "__main__":
    for fn in (test_confirmation_lag, test_finds_obvious_swing,
               test_plateau_breaks_to_earliest, test_rejects_short_confirm):
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll pivot tests passed.")
