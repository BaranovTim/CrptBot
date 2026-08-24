"""Agent 2 must not read the future either — but its leak looks different.

Agent 1's leak was structural: a pivot published before the bars confirming
it had closed. Agent 2 has no pivots. Its characteristic leak hides in the
NORMALISATION layer, and it is much quieter:

    z = (x - x.mean()) / x.std()          # <-- the whole future, in every row

That line looks like ordinary preprocessing. It raises nothing, produces
perfectly plausible numbers, and improves the backtest. Every standardisation
in agent2 is therefore a rolling window, and this test is what keeps it that
way: compute on bars[:t], compute on bars[:t+100], compare the row for bar t.
A full-sample statistic changes when 100 bars are appended; a rolling one
does not.

    python3 tests/test_agent2_no_lookahead.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent2 import IndicatorAgent
from agent2.schema import FEATURE_COLUMNS
from tests.synthetic import make_bars

RTOL = ATOL = 1e-9


def _rows_match(a: pd.Series, b: pd.Series):
    bad = []
    for col in FEATURE_COLUMNS:
        x, y = a[col], b[col]
        if pd.isna(x) and pd.isna(y):
            continue
        if pd.isna(x) != pd.isna(y) or not np.isclose(x, y, rtol=RTOL, atol=ATOL):
            bad.append((col, x, y))
    return bad


def test_no_lookahead(bars=None, t_range=range(900, 1000), horizon=100):
    bars = make_bars(1400) if bars is None else bars
    agent = IndicatorAgent()

    long_features = agent.compute(bars.iloc[: max(t_range) + horizon])
    failures = []
    for t in t_range:
        short = agent.compute(bars.iloc[:t])
        bad = _rows_match(short.iloc[-1], long_features.iloc[t - 1])
        if bad:
            failures.append((t, bad))

    if failures:
        t, bad = failures[0]
        detail = "\n".join(f"    {c:28} short={x!r}  long={y!r}" for c, x, y in bad[:10])
        raise AssertionError(
            f"LOOKAHEAD DETECTED in {len(failures)}/{len(t_range)} bars.\n"
            f"  First divergence at bar {t}:\n{detail}\n"
            f"  A feature changed value for a bar once future bars were added."
        )
    return True


def test_no_lookahead_coarse_htf():
    """A 1d higher timeframe over hourly bars: coarser join, same guarantee.

    Windows start past ``required_bars`` so the HTF block is actually
    populated — below that it returns NaN, which is safe but tests nothing.
    """
    from agent2 import Agent2Config

    bars = make_bars(2600, seed=31)
    agent = IndicatorAgent(Agent2Config(htf_rule="1d"))
    need = agent.required_bars(bars)
    assert need > agent.warmup_bars, "a 1d HTF over 1h bars must raise the requirement"

    long_features = agent.compute(bars.iloc[: need + 300])
    for t in range(need + 100, need + 160):
        short = agent.compute(bars.iloc[:t])
        bad = _rows_match(short.iloc[-1], long_features.iloc[t - 1])
        assert not bad, f"lookahead with a 1d HTF at bar {t}: {bad[:5]}"
    return True


def test_short_window_yields_nan_not_a_wrong_value():
    """Too little history must produce NaN, never a plausible wrong number.

    Failing conservatively is what makes ``required_bars`` a sizing warning
    rather than a silent corruption.
    """
    from agent2 import Agent2Config

    bars = make_bars(400, seed=31)
    agent = IndicatorAgent(Agent2Config(htf_rule="1d"))
    assert len(bars) < agent.required_bars(bars)
    out = agent.compute(bars)
    assert out["rsi_14_4h"].isna().all(), "HTF returned values without enough HTF bars"
    return True


def test_deterministic():
    bars = make_bars(700, seed=13)
    a, b = IndicatorAgent(), IndicatorAgent()
    pd.testing.assert_frame_equal(a.compute(bars), b.compute(bars))
    pd.testing.assert_frame_equal(a.compute(bars), a.compute(bars))
    return True


def test_no_full_sample_statistics_in_source():
    """A static grep for the shape of the bug, alongside the behavioural test.

    Catches a full-column .mean()/.std() the moment it is written, rather
    than waiting for someone to run the slow comparison above.
    """
    import re

    pkg = Path(__file__).resolve().parent.parent / "agent2"
    pattern = re.compile(r"(?<!rolling\(\))(?<![\w.])(?:df|s|c|x|v|out)\.(mean|std)\(\)")
    offenders = []
    for path in pkg.glob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            if pattern.search(line) and "rolling" not in line and "ewm" not in line:
                offenders.append(f"{path.name}:{i}: {stripped}")
    assert not offenders, (
        "possible full-sample statistic (leaks the future into every row):\n  "
        + "\n  ".join(offenders)
    )
    return True


if __name__ == "__main__":
    for fn in (test_no_lookahead, test_no_lookahead_coarse_htf,
               test_short_window_yields_nan_not_a_wrong_value,
               test_deterministic, test_no_full_sample_statistics_in_source):
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}\n{e}")
            sys.exit(1)
    print("\nAll Agent 2 lookahead tests passed.")
