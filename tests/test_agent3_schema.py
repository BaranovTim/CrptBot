"""Agent 3's output contract, and the NaN rule that matters most here."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent3 import Agent3Config, NewsAgent
from agent3.schema import (
    BOUNDED_COLUMNS,
    COUNT_COLUMNS,
    FEATURE_COLUMNS,
    validate_features,
)
from newsfeed.events import NewsItem
from newsfeed.store import JSONLNewsStore
from tests.synthetic import make_bars
from tests.synthetic_news import make_news

BARS = make_bars(900)
ITEMS = make_news(BARS, n=60)
AGENT = NewsAgent(Agent3Config(asset="BTC"))
SCORES = AGENT.score_items(ITEMS)
FEATURES = AGENT.compute(BARS, ITEMS, SCORES)


def test_shape_and_order():
    assert tuple(FEATURES.columns) == FEATURE_COLUMNS
    assert FEATURES.index.equals(BARS.index)
    validate_features(FEATURES)
    return True


def test_no_infinities():
    assert not np.isinf(FEATURES.to_numpy(dtype=float)).any()
    return True


def test_bounded_columns():
    for col, (lo, hi) in BOUNDED_COLUMNS.items():
        v = FEATURES[col].dropna()
        if v.empty:
            continue
        assert v.between(lo, hi).all(), f"{col} escaped [{lo}, {hi}]"
    return True


def test_counts_are_zero_filled_sentiment_is_nan_filled():
    """The distinction this agent turns on.

    ``news_sentiment_6h == 0`` means news exists and reads neutral.
    ``NaN`` means there was no news at all. Those are different market
    states, and a 0-fill would teach the model that silence is neutrality.
    Counts are the mirror: a count of 0 is an observed fact.
    """
    for col in COUNT_COLUMNS:
        assert FEATURES[col].notna().all(), f"{col} is a count and must never be NaN"

    quiet = make_bars(300, seed=99)
    empty = AGENT.compute(quiet, [], {})
    for col in COUNT_COLUMNS:
        assert (empty[col] == 0).all(), f"{col} should be 0 with no news"
    for col in ("news_sentiment_6h", "news_sentiment_24h", "news_direction_last",
                "news_credibility_mean_24h", "news_novelty_last"):
        assert empty[col].isna().all(), (
            f"{col} must be NaN with no news — 0 would mean 'neutral news exists'"
        )
    return True


def test_no_sentinel_values():
    for c in FEATURES.columns:
        assert not (FEATURES[c].dropna() == -999).any(), f"{c} uses a -999 sentinel"
    return True


def test_asset_scoping():
    """ETH-only news must not drive BTC features; untagged news must."""
    bars = make_bars(400)
    when = bars.index[200]
    eth_only = NewsItem(headline="Ethereum upgrade ships", source="protocol.example",
                        assets=("ETH",), published_at=when, ingested_at=when)
    macro = NewsItem(headline="Fed holds rates steady", source="reuters.example",
                     assets=(), published_at=when, ingested_at=when)

    agent = NewsAgent(Agent3Config(asset="BTC"))
    only_eth = agent.compute(bars, [eth_only], agent.score_items([eth_only]))
    assert (only_eth["news_count_24h"] == 0).all(), "ETH-only news leaked into BTC"

    with_macro = agent.compute(bars, [macro], agent.score_items([macro]))
    assert with_macro["news_count_24h"].max() > 0, "market-wide news was dropped"
    return True


def test_decay_reduces_influence_over_time():
    """A single item's weight must fall as it ages, not persist flat."""
    bars = make_bars(400)
    when = bars.index[100]
    item = NewsItem(headline="Major exchange hacked, funds stolen",
                    source="reuters.example", assets=("BTC",),
                    published_at=when, ingested_at=when)
    agent = NewsAgent(Agent3Config(asset="BTC"))
    f = agent.compute(bars, [item], agent.score_items([item]))
    s = f["news_sentiment_24h"].dropna()
    assert len(s) > 3, "item never registered"
    assert abs(s.iloc[-1]) <= abs(s.iloc[0]) + 1e-9, "influence did not decay"
    return True


def test_latest_matches_compute():
    out = AGENT.latest(BARS, ITEMS, SCORES)
    row = FEATURES.iloc[-1]
    assert out.timestamp == FEATURES.index[-1]
    assert set(out.features) == set(FEATURE_COLUMNS)
    for c in FEATURE_COLUMNS:
        a, b = out.features[c], row[c]
        assert (pd.isna(a) and pd.isna(b)) or np.isclose(a, b), f"{c} differs"
    return True


def test_store_roundtrip_and_dedupe(tmp=None):
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        store = JSONLNewsStore(Path(d))
        added = store.append_items(ITEMS)
        assert added == len(ITEMS)
        assert store.append_items(ITEMS) == 0, "re-appending should dedupe to zero"

        loaded = store.load_items()
        assert len(loaded) == len(ITEMS)
        assert {i.id for i in loaded} == {i.id for i in ITEMS}

        # ingested_at survives the round trip — it is the clock that matters
        assert all(i.ingested_at is not None for i in loaded)

        store.save_scores(list(SCORES.values()))
        back = store.load_scores()
        assert set(back) == set(SCORES)

        cutoff = ITEMS[len(ITEMS) // 2].observable_at()
        visible = store.visible_at(cutoff, loaded)
        assert all(i.observable_at() <= cutoff for i in visible)
        assert len(visible) < len(loaded), "PIT query returned everything"
    return True


def test_unscored_items_degrade_toward_no_news():
    """A broken scorer must not fabricate signal.

    Failure returns magnitude 0, so the aggregation weights it to nothing and
    `news_scored_fraction_24h` reports the degradation to Agent 5.
    """
    from agent3.scorers import ClaudeScorer

    s = ClaudeScorer._unscored(ITEMS[0], "api_error")
    assert s.magnitude == 0.0 and s.direction == 0.0 and s.credibility == 0.0
    assert s.scorer.startswith("failed:")

    bars = make_bars(300)
    when = bars.index[100]
    item = NewsItem(headline="Something happened", source="x.example",
                    assets=("BTC",), published_at=when, ingested_at=when)
    agent = NewsAgent(Agent3Config(asset="BTC"))
    f = agent.compute(bars, [item], {item.id: ClaudeScorer._unscored(item, "api_error")})
    assert f["news_count_24h"].max() == 1, "the item should still be counted"
    sent = f["news_sentiment_24h"].dropna()
    assert (sent.abs() < 1e-9).all(), "a failed score produced sentiment"
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} schema tests passed.")
