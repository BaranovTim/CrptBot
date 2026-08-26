"""Triple-barrier labels: hand-built paths where the right answer is known.

The label is the ground truth everything trains on. A subtle bug here does
not crash anything - it teaches the model a slightly wrong game, and every
metric downstream is then measured against the wrong answers.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent5.config import Agent5Config
from agent5.labels import triple_barrier
from tests.synthetic import make_bars

CFG = Agent5Config(k_up=2.0, k_dn=1.0, max_hold_bars=10, atr_period=5)


def _flat_bars(n, price=100.0, spread=1.0):
    """Bars with constant ATR ~= spread, easy to reason about by hand."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": price, "high": price + spread / 2, "low": price - spread / 2,
        "close": price, "volume": 1.0,
    }, index=idx, dtype=float)
    return df


def test_upper_barrier_wins():
    bars = _flat_bars(40)
    # ATR settles to ~1.0 (constant true range 1.0). barriers from bar 20:
    # upper = 102, lower = 99. make bar 23 spike through the upper
    bars.iloc[23, bars.columns.get_loc("high")] = 103.0
    res = triple_barrier(bars, CFG)
    assert res.y.iloc[20] == 1.0, f"expected win, got {res.y.iloc[20]}"
    assert res.touch.iloc[20] == "upper"
    assert res.t1.iloc[20] == 23, f"resolved at {res.t1.iloc[20]}, expected 23"
    return True


def test_lower_barrier_loses():
    bars = _flat_bars(40)
    bars.iloc[22, bars.columns.get_loc("low")] = 98.0    # through 99
    res = triple_barrier(bars, CFG)
    assert res.y.iloc[20] == 0.0
    assert res.touch.iloc[20] == "lower"
    assert res.t1.iloc[20] == 22
    return True


def test_first_touch_wins_not_biggest():
    """A stop at bar 22 beats a take-profit at bar 25. Order matters."""
    bars = _flat_bars(40)
    bars.iloc[22, bars.columns.get_loc("low")] = 98.0     # stop first...
    bars.iloc[25, bars.columns.get_loc("high")] = 105.0   # ...huge win later
    res = triple_barrier(bars, CFG)
    assert res.y.iloc[20] == 0.0, "the earlier touch must decide the label"
    assert res.t1.iloc[20] == 22
    return True


def test_ambiguous_bar_is_a_loss():
    """One bar through BOTH barriers counts as a loss, never a win.

    The bar cannot tell us which side was hit first, and assuming the
    friendly answer is how backtests get confident and accounts get empty.
    """
    bars = _flat_bars(40)
    bars.iloc[24, bars.columns.get_loc("high")] = 103.0
    bars.iloc[24, bars.columns.get_loc("low")] = 97.0
    res = triple_barrier(bars, CFG)
    assert res.y.iloc[20] == 0.0
    assert res.touch.iloc[20] == "ambiguous"
    return True


def test_timeout_takes_exit_sign():
    up = _flat_bars(60)
    # drift the closes up slightly, never enough to reach a barrier
    drift = np.linspace(0, 0.3, 60)
    for col in ("open", "high", "low", "close"):
        up[col] = up[col] + drift
    res = triple_barrier(up, CFG)
    assert res.touch.iloc[30] == "timeout"
    assert res.y.iloc[30] == 1.0, "positive drift at timeout should label 1"
    assert res.t1.iloc[30] == 30 + CFG.max_hold_bars
    return True


def test_tail_bars_get_no_label():
    """Samples whose window runs past the data end must be NaN, not partial."""
    bars = _flat_bars(40)
    res = triple_barrier(bars, CFG)
    tail = res.y.iloc[-CFG.max_hold_bars:]
    assert tail.isna().all(), "bars near the end were labelled with a peeked window"
    return True


def test_uniqueness_weights_shrink_with_overlap():
    """Overlapping labels share credit; isolated labels keep weight 1."""
    bars = make_bars(600)
    res = triple_barrier(bars, Agent5Config(max_hold_bars=24, atr_period=14))
    w = res.weight.dropna()
    assert (w > 0).all() and (w <= 1.0 + 1e-9).all()
    # with 24-bar windows sampled every bar, weights should sit well below 1
    # (many labels resolve early, so overlap is less than the full 24x). the
    # broken case this guards against reads ~1.0: no overlap accounting at all
    assert w.median() < 0.5, f"median weight {w.median():.3f} - overlap not counted"
    ess = res.effective_sample_size()
    n = int(res.labelled.sum())
    # 563 hourly samples with up-to-24-bar overlapping windows carry a few
    # dozen truly independent observations, not 500. both bounds matter:
    # too high means overlap is not counted, too low means weights collapsed
    assert n / 50 < ess < n / 3, f"ESS {ess:.0f} of {n} labelled samples"
    return True


def test_labels_use_only_the_future():
    """Changing PAST bars must not change a label; changing future bars must."""
    bars = make_bars(300)
    cfg = Agent5Config(max_hold_bars=12, atr_period=5)
    base = triple_barrier(bars, cfg)

    past = bars.copy()
    past.iloc[50, past.columns.get_loc("high")] *= 1.5    # well before t=200
    res_past = triple_barrier(past, cfg)
    assert res_past.y.iloc[200] == base.y.iloc[200], \
        "a change 150 bars in the past moved a label"

    future = bars.copy()
    future.iloc[205, future.columns.get_loc("high")] = bars["close"].iloc[200] * 2
    res_future = triple_barrier(future, cfg)
    assert res_future.y.iloc[200] == 1.0, "a huge future spike should win the label"
    return True


def test_live_bar_has_barriers_but_no_label():
    """The bar you actually trade must have TP/SL even with no label.

    Barrier distances are k * ATR / close - arithmetic on the current bar,
    needing no future. Labels need the full window. Gating both on the label
    condition left the newest bars with NaN barriers, so EV was NaN and the
    pipeline could never emit a live decision.
    """
    bars = make_bars(400)
    res = triple_barrier(bars, Agent5Config(max_hold_bars=24, atr_period=14))

    tail = slice(-24, None)                       # unlabellable by construction
    assert res.y.iloc[tail].isna().all(), "tail bars should carry no label"
    assert res.tp_pct.iloc[tail].notna().all(), \
        "the live bar has no take-profit distance - EV cannot be computed"
    assert res.sl_pct.iloc[tail].notna().all(), \
        "the live bar has no stop distance"
    assert (res.tp_pct.iloc[tail] > 0).all()

    # and the ratio still matches the configured payoff
    ratio = (res.tp_pct.iloc[-1] / res.sl_pct.iloc[-1])
    assert abs(ratio - 2.0) < 1e-9, f"payoff ratio {ratio:.3f}, expected 2.0"
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} label tests passed.")
