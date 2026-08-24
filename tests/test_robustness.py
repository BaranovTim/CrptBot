"""Throw broken data at every agent and check they all react the same way.

Written after a probe found four different behaviours on the SAME bad input:
an empty frame crashed Agent 2 with an IndexError, was silently accepted by
Agents 3 and 4, and was cleanly rejected by Agent 1. Duplicate timestamps
were caught by two agents and quietly double-counted by the other two.

Inconsistency is worse than any one missing check, because it means you
cannot hold "what the agents accept" in your head - you would need four
separate answers. So these tests assert uniform behaviour, not just absence
of crashes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent1 import PatternAgent
from agent2 import IndicatorAgent
from agent3 import Agent3Config, NewsAgent
from agent4 import FlowAgent
from core import check_bars, describe_bars_problem, feature_report
from tests.synthetic import make_bars
from tests.synthetic_news import make_news
from tests.synthetic_tape import make_open_interest, make_tape

BARS = make_bars(400)
NEWS = make_news(BARS, n=20)
_A3 = NewsAgent(Agent3Config(asset="BTC"))
SCORES = _A3.score_items(NEWS)


def _computers(bars):
    """One callable per agent, all computing on the same bars."""
    return {
        "agent1": lambda: PatternAgent().compute(bars),
        "agent2": lambda: IndicatorAgent().compute(bars),
        "agent3": lambda: NewsAgent(Agent3Config(asset="BTC")).compute(bars, NEWS, SCORES),
        "agent4": lambda: FlowAgent().compute(bars),
    }


def _all_reject(bars, why):
    """Every agent must refuse this input, and say so with an exception."""
    for name, fn in _computers(bars).items():
        try:
            fn()
        except (ValueError, TypeError):
            continue                      # a clear refusal is the pass condition
        raise AssertionError(f"{name} accepted {why} instead of rejecting it")
    return True


def _all_succeed(bars, why):
    """Every agent must handle this input without raising."""
    for name, fn in _computers(bars).items():
        try:
            fn()
        except Exception as e:
            raise AssertionError(f"{name} crashed on {why}: {type(e).__name__}: {e}")
    return True


# ---- inputs every agent must refuse --------------------------------------
def test_empty_frame_rejected_everywhere():
    return _all_reject(BARS.iloc[:0], "an empty frame")


def test_duplicate_timestamps_rejected_everywhere():
    """Duplicates double-count volume and corrupt every rolling window."""
    dup = pd.concat([BARS.iloc[:100], BARS.iloc[99:200]])
    return _all_reject(dup, "duplicate timestamps")


def test_unsorted_index_rejected_everywhere():
    shuffled = BARS.iloc[np.r_[50:100, 0:50, 100:400]]
    return _all_reject(shuffled, "an unsorted index")


def test_tz_naive_index_rejected_everywhere():
    """A naive index holding local time silently shifts sessions and days."""
    naive = BARS.copy()
    naive.index = naive.index.tz_localize(None)
    return _all_reject(naive, "a timezone-naive index")


# ---- inputs every agent must survive -------------------------------------
def test_tiny_frames_survive():
    for n in (1, 2, 5, 30):
        _all_succeed(BARS.iloc[:n], f"{n} bars")
    return True


def test_degenerate_prices_survive():
    flat = BARS.copy()
    for c in ("open", "high", "low", "close"):
        flat[c] = 50_000.0
    _all_succeed(flat, "a completely flat price series")

    zero = BARS.copy()
    zero["volume"] = 0.0
    _all_succeed(zero, "zero volume")

    huge = BARS.copy()
    tiny = BARS.copy()
    for c in ("open", "high", "low", "close"):
        huge[c] = huge[c] * 1e9
        tiny[c] = tiny[c] * 1e-12
    _all_succeed(huge, "extreme prices")
    _all_succeed(tiny, "sub-cent prices")
    return True


def test_nan_holes_survive_and_do_not_leak_backwards():
    """A gap in the feed must not change features for EARLIER bars.

    If it did, the hole would be reaching backwards in time, which is the
    same class of error as a lookahead leak pointed the other way.
    """
    holed = BARS.copy()
    holed.iloc[300:310, holed.columns.get_loc("close")] = np.nan
    _all_succeed(holed, "a NaN block in close")

    for name, A in (("agent1", PatternAgent()), ("agent2", IndicatorAgent()),
                    ("agent4", FlowAgent())):
        clean, dirty = A.compute(BARS), A.compute(holed)
        before = slice(0, 290)
        leaked = [c for c in clean.columns
                  if not np.allclose(clean[c].iloc[before].fillna(-1e9),
                                     dirty[c].iloc[before].fillna(-1e9))]
        assert not leaked, f"{name}: a later NaN hole changed earlier rows: {leaked}"
    return True


def test_non_utc_index_is_converted_not_miscomputed():
    """tz-aware but not UTC is the sneaky version of the timezone bug.

    It passes every obvious check and then cuts days and 4h buckets on LOCAL
    midnight. Measured before the fix: 451/500 bars wrong for Agent 1's Asian
    session, 812 wrong 4h values for Agent 2.
    """
    east = BARS.copy()
    east.index = east.index.tz_convert("America/New_York")

    for name, A, cols in (
        ("agent1", PatternAgent(),
         ("dist_to_asia_high_atr", "dist_to_pdh_atr", "dist_to_pdl_atr")),
        ("agent2", IndicatorAgent(), ("rsi_14_4h", "macd_hist_atr_4h")),
    ):
        u, e = A.compute(BARS), A.compute(east)
        for c in cols:
            same = np.allclose(u[c].fillna(-1e9), e[c].fillna(-1e9))
            assert same, f"{name}: {c} differs on a non-UTC index"
    return True


# ---- structural guarantees -----------------------------------------------
def test_agents_never_import_each_other():
    """The ablation deletes one block at a time; imports would break that."""
    import re

    root = Path(__file__).resolve().parent.parent
    pattern = re.compile(r"^\s*(?:from|import)\s+(agent[1-4])", re.MULTILINE)
    for pkg in ("agent1", "agent2", "agent3", "agent4"):
        for path in (root / pkg).glob("*.py"):
            for other in pattern.findall(path.read_text()):
                assert other == pkg, (
                    f"{path.relative_to(root)} imports {other} - deleting "
                    f"{other} during an ablation would break {pkg}"
                )
    return True


def test_repeated_calls_are_identical():
    """No state may survive between calls, on any agent."""
    for name, A, kw in (("agent1", PatternAgent(), {}),
                        ("agent2", IndicatorAgent(), {}),
                        ("agent4", FlowAgent(), {})):
        first = A.compute(BARS, **kw)
        A.compute(BARS.iloc[:120], **kw)       # a different window in between
        again = A.compute(BARS, **kw)
        pd.testing.assert_frame_equal(first, again,
                                      obj=f"{name} changed across calls")
    a3 = NewsAgent(Agent3Config(asset="BTC"))
    f1 = a3.compute(BARS, NEWS, SCORES)
    a3.compute(BARS.iloc[:120], NEWS, SCORES)
    pd.testing.assert_frame_equal(f1, a3.compute(BARS, NEWS, SCORES))
    return True


def test_corrupt_feed_is_reported_not_silently_used():
    """high < low is legal to compute on and produces garbage. Say so."""
    swapped = BARS.copy()
    swapped["high"], swapped["low"] = BARS["low"], BARS["high"]
    note = describe_bars_problem(swapped)
    assert note and "high < low" in note, f"corrupt bars not reported: {note}"
    assert describe_bars_problem(BARS) is None, "clean bars flagged as broken"
    return True


def test_gaps_are_reported():
    """bars_since_* counts BARS, not hours. Gaps break that equivalence."""
    gapped = pd.concat([BARS.iloc[:200], BARS.iloc[350:]])
    note = describe_bars_problem(gapped)
    assert note and "gap" in note, f"time gap not reported: {note}"
    return True


def test_feature_report_catches_dead_columns():
    """The trap: 16 of Agent 4's 22 columns are NaN without the optional feeds."""
    a4 = FlowAgent()
    bare = feature_report(a4.compute(BARS), a4.warmup_bars, "no feeds")
    assert len(bare.dead) > 10, "dead columns not detected"
    assert bare.usable_cols < 8

    full = feature_report(
        a4.compute(BARS, tape=make_tape(BARS, per_bar=80),
                   open_interest=make_open_interest(BARS)),
        a4.warmup_bars, "with feeds")
    assert len(full.dead) < len(bare.dead), "feeds did not revive any column"
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} robustness tests passed.")
