"""Agent 3 — news tracker.

    from agent3 import NewsAgent
    from newsfeed import JSONLNewsStore

    agent = NewsAgent()
    scores = agent.score_items(items)        # slow, offline, once per item
    features = agent.compute(bars, items, scores)   # fast, pure arithmetic
    print(agent.latest(bars, items, scores))
"""
from .agent import NewsAgent, NewsOutput
from .config import DEFAULT_CONFIG, Agent3Config
from .schema import (
    BOUNDED_COLUMNS,
    COLUMN_GROUP,
    COUNT_COLUMNS,
    FEATURE_COLUMNS,
    FEATURE_GROUPS,
    SIGNED_COLUMNS,
    validate_features,
)
from .scorers import CachedScorer, ClaudeScorer, LexiconScorer, NewsScorer

__all__ = [
    "NewsAgent", "NewsOutput", "Agent3Config", "DEFAULT_CONFIG",
    "NewsScorer", "ClaudeScorer", "LexiconScorer", "CachedScorer",
    "FEATURE_COLUMNS", "FEATURE_GROUPS", "COLUMN_GROUP", "SIGNED_COLUMNS",
    "COUNT_COLUMNS", "BOUNDED_COLUMNS", "validate_features",
]
