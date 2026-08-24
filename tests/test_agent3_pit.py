"""Point-in-time discipline: the news equivalent of the pivot lookahead test.

Agent 1's leak was a pivot published before the bars confirming it closed.
Agent 2's hid in the normalisation layer. Agent 3's is a timestamp: using the
moment an event *happened* rather than the moment we could *know* about it.

It is the quietest of the three, because every row still has a plausible
timestamp and nothing raises. A news API reports an event at 14:00 that it
published at 14:20 and we ingested at 14:40. Keying on 14:00 hands the
backtest forty minutes of free foresight on every single item — and news
lands precisely on the moves you are trying to predict, so that foresight is
worth a great deal and will never reproduce live.

    python3 tests/test_agent3_pit.py
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent3 import Agent3Config, NewsAgent
from agent3.schema import FEATURE_COLUMNS
from newsfeed.events import NewsItem
from tests.synthetic import make_bars
from tests.synthetic_news import make_news

BARS = make_bars(900)
ITEMS = make_news(BARS, n=60)
AGENT = NewsAgent(Agent3Config(asset="BTC"))
SCORES = AGENT.score_items(ITEMS)


def _rows_match(a: pd.Series, b: pd.Series):
    bad = []
    for col in FEATURE_COLUMNS:
        x, y = a[col], b[col]
        if pd.isna(x) and pd.isna(y):
            continue
        if pd.isna(x) != pd.isna(y) or not np.isclose(x, y, rtol=1e-9, atol=1e-9):
            bad.append((col, x, y))
    return bad


def test_no_lookahead_on_bars():
    """Extending the bar index must not change any earlier row."""
    long_f = AGENT.compute(BARS.iloc[:800], ITEMS, SCORES)
    for t in range(700, 760):
        short = AGENT.compute(BARS.iloc[:t], ITEMS, SCORES)
        bad = _rows_match(short.iloc[-1], long_f.iloc[t - 1])
        assert not bad, f"row changed when future bars were added at {t}: {bad[:4]}"
    return True


def test_item_invisible_before_observable_at():
    """The direct test: an item may not move any bar before it was knowable.

    Compute the whole frame with and without one item, and compare every row
    that closes before that item's ``observable_at``. Those rows must be
    byte-identical — the item did not exist yet as far as anyone could tell.
    """
    cfg = Agent3Config(asset="BTC")
    agent = NewsAgent(cfg)
    lag = timedelta(seconds=cfg.safety_lag_seconds)

    mid = BARS.index[len(BARS) // 2]
    bomb = NewsItem(
        headline="SEC approves everything, market rallies hard",
        source="sec.gov", assets=("BTC",),
        event_time=mid - timedelta(hours=6),      # "happened" much earlier
        published_at=mid - timedelta(hours=3),    # published earlier
        ingested_at=mid,                          # but only knowable here
    )
    assert bomb.observable_at(lag) == mid + lag, (
        f"observable_at is {bomb.observable_at(lag)}, expected {mid + lag} — "
        f"the gate has drifted toward event_time or published_at"
    )

    without = agent.compute(BARS, ITEMS, agent.score_items(ITEMS))
    with_items = list(ITEMS) + [bomb]
    with_ = agent.compute(BARS, with_items, agent.score_items(with_items))

    before = BARS.index < bomb.observable_at(lag)
    assert before.sum() > 100, "test window too small to be meaningful"
    diffs = []
    for i in np.flatnonzero(before):
        bad = _rows_match(without.iloc[i], with_.iloc[i])
        if bad:
            diffs.append((BARS.index[i], bad[:3]))
    assert not diffs, (
        f"an item affected {len(diffs)} bars that closed before it was "
        f"observable — first at {diffs[0][0]}: {diffs[0][1]}"
    )

    after = np.flatnonzero(~before)
    assert any(_rows_match(without.iloc[i], with_.iloc[i]) for i in after[:50]), \
        "the item never showed up at all — the test proved nothing"
    return True


def test_event_time_is_never_the_gate():
    """``event_time`` must not be able to pull an item earlier."""
    cfg = Agent3Config(asset="BTC", safety_lag_seconds=0)
    lag = timedelta(0)
    mid = BARS.index[len(BARS) // 2]

    early_event = NewsItem(
        headline="Ancient event surfaced late", source="reuters.example",
        assets=("BTC",),
        event_time=mid - timedelta(days=30),
        published_at=mid, ingested_at=mid,
    )
    assert early_event.observable_at(lag) == mid, (
        "observable_at drifted toward event_time — that is the leak"
    )

    late_ingest = NewsItem(
        headline="Published early, seen late", source="reuters.example",
        assets=("BTC",),
        published_at=mid, ingested_at=mid + timedelta(hours=2),
    )
    assert late_ingest.observable_at(lag) == mid + timedelta(hours=2), (
        "observable_at used published_at when we demonstrably saw it later"
    )
    return True


def test_safety_lag_delays_visibility():
    """The configured margin actually pushes items later, not earlier."""
    item = ITEMS[0]
    base = item.observable_at(timedelta(0))
    assert item.observable_at(timedelta(seconds=60)) == base + timedelta(seconds=60)
    return True


def test_naive_index_rejected():
    """A tz-naive bar index would silently shift every item by the UTC offset."""
    naive = BARS.copy()
    naive.index = naive.index.tz_localize(None)
    try:
        AGENT.compute(naive, ITEMS, SCORES)
    except ValueError:
        return True
    raise AssertionError("a timezone-naive bar index should have been rejected")


def test_deterministic():
    a = AGENT.compute(BARS, ITEMS, SCORES)
    b = NewsAgent(Agent3Config(asset="BTC")).compute(BARS, ITEMS, SCORES)
    pd.testing.assert_frame_equal(a, b)
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} point-in-time tests passed.")
