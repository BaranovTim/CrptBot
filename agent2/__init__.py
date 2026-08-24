"""Agent 2 — indicator analyser.

    from agent2 import IndicatorAgent

    agent = IndicatorAgent()
    features = agent.compute(bars)   # DataFrame, one row per bar -> Agent 5
    print(agent.latest(bars))        # human-readable read of the last bar
"""
from .agent import IndicatorAgent, IndicatorOutput
from .config import DEFAULT_CONFIG, Agent2Config
from .schema import (
    BOUNDED_COLUMNS,
    COLUMN_GROUP,
    FEATURE_COLUMNS,
    FEATURE_GROUPS,
    SIGNED_COLUMNS,
    validate_features,
)

__all__ = [
    "IndicatorAgent", "IndicatorOutput", "Agent2Config", "DEFAULT_CONFIG",
    "FEATURE_COLUMNS", "FEATURE_GROUPS", "COLUMN_GROUP", "SIGNED_COLUMNS",
    "BOUNDED_COLUMNS", "validate_features",
]
