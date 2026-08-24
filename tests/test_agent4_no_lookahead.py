"""Agent 4's leak surface: rolling baselines over flow data.

Agent 1's leak was a pivot published early, Agent 2's hid in normalisation,
Agent 3's was a timestamp. Agent 4 inherits Agent 2's problem and adds one of
its own: the large-print threshold. That threshold is estimated from the size
distribution, and if the window used to estimate it includes the bar being
classified, a monster print raises its own bar and can classify itself as
ordinary — hiding exactly the events the feature exists to find.

The tape itself is causal by construction (every print inside bar t is
complete when bar t closes), so this file tests the layer above it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent4 import Agent4Config, FlowAgent
from agent4.schema import FEATURE_COLUMNS
from tests.synthetic import make_bars
from tests.synthetic_tape import (
    make_liquidations,
    make_netflow,
    make_open_interest,
    make_tape,
)

BARS = make_bars(1200)
TAPE = make_tape(BARS, per_bar=120, seed=17)
OI = make_open_interest(BARS)
LIQ = make_liquidations(BARS)
NET = make_netflow(BARS)
AGENT = FlowAgent(Agent4Config())


def _rows_match(a: pd.Series, b: pd.Series):
    bad = []
    for col in FEATURE_COLUMNS:
        x, y = a[col], b[col]
        if pd.isna(x) and pd.isna(y):
            continue
        if pd.isna(x) != pd.isna(y) or not np.isclose(x, y, rtol=1e-9, atol=1e-9):
            bad.append((col, x, y))
    return bad


def _slice(t):
    return dict(tape=TAPE.iloc[:t], open_interest=OI.iloc[:t],
                liquidations=LIQ.iloc[:t], netflow=NET.iloc[:t])


def test_no_lookahead(t_range=range(900, 960), horizon=120):
    long_f = AGENT.compute(BARS.iloc[: max(t_range) + horizon],
                           **_slice(max(t_range) + horizon))
    failures = []
    for t in t_range:
        short = AGENT.compute(BARS.iloc[:t], **_slice(t))
        bad = _rows_match(short.iloc[-1], long_f.iloc[t - 1])
        if bad:
            failures.append((t, bad))
    if failures:
        t, bad = failures[0]
        detail = "\n".join(f"    {c:28} short={x!r}  long={y!r}" for c, x, y in bad[:8])
        raise AssertionError(
            f"LOOKAHEAD DETECTED in {len(failures)}/{len(t_range)} bars.\n"
            f"  First divergence at bar {t}:\n{detail}"
        )
    return True


def test_no_lookahead_without_tape():
    """The kline-only fallback path must be causal too."""
    long_f = AGENT.compute(BARS.iloc[:900], open_interest=OI.iloc[:900])
    for t in range(820, 860):
        short = AGENT.compute(BARS.iloc[:t], open_interest=OI.iloc[:t])
        bad = _rows_match(short.iloc[-1], long_f.iloc[t - 1])
        assert not bad, f"lookahead in the kline fallback at bar {t}: {bad[:4]}"
    return True


def test_no_full_sample_statistics_in_source():
    """Static guard for the shape of the normalisation bug."""
    import re

    pattern = re.compile(r"(?<![\w.])(?:df|s|c|x|v|out|flow|total)\.(mean|std)\(\)")
    offenders = []
    for path in (Path(__file__).resolve().parent.parent / "agent4").glob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "rolling" in line or "ewm" in line:
                continue
            if pattern.search(line):
                offenders.append(f"{path.name}:{i}: {stripped}")
    assert not offenders, (
        "possible full-sample statistic (leaks the future into every row):\n  "
        + "\n  ".join(offenders)
    )
    return True


def test_deterministic():
    a = FlowAgent(Agent4Config()).compute(BARS, **_slice(len(BARS)))
    b = FlowAgent(Agent4Config()).compute(BARS, **_slice(len(BARS)))
    pd.testing.assert_frame_equal(a, b)
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} lookahead tests passed.")
