"""Agent 1 — pattern and smart-money geometry detector.

Usage:

    from agent1 import PatternAgent

    agent = PatternAgent()
    features = agent.compute(bars)   # DataFrame, one row per bar -> Agent 5
    print(agent.latest(bars))        # human-readable read of the last bar
"""
from .agent import PatternAgent, PatternOutput
from .config import DEFAULT_CONFIG, Agent1Config
from .schema import COLUMN_GROUP, FEATURE_COLUMNS, FEATURE_GROUPS, validate_features

__all__ = [
    "PatternAgent",
    "PatternOutput",
    "Agent1Config",
    "DEFAULT_CONFIG",
    "FEATURE_COLUMNS",
    "FEATURE_GROUPS",
    "COLUMN_GROUP",
    "validate_features",
]
