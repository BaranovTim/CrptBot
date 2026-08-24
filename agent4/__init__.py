"""Agent 4 — order flow and positioning.

    from agent4 import FlowAgent
    from marketdata.aggtrades import load_tape_bars
    from marketdata.derivatives import load_open_interest, resample_to_bars

    tape = load_tape_bars("BTCUSDT", "1h", start="2026-05-01")
    oi   = resample_to_bars(load_open_interest("BTCUSDT", "2026-05-01"), bars.index)

    agent = FlowAgent()
    features = agent.compute(bars, tape=tape, open_interest=oi)
    print(agent.latest(bars, tape=tape, open_interest=oi))
"""
from .agent import FlowAgent, FlowOutput
from .config import DEFAULT_CONFIG, Agent4Config
from .netflow import NetflowProvider, NullNetflowProvider
from .schema import (
    BOUNDED_COLUMNS,
    COLUMN_GROUP,
    COVERAGE_COLUMNS,
    DIRECTIONAL_COLUMNS,
    FEATURE_COLUMNS,
    FEATURE_GROUPS,
    SIGNED_COLUMNS,
    validate_features,
)

__all__ = [
    "FlowAgent", "FlowOutput", "Agent4Config", "DEFAULT_CONFIG",
    "NetflowProvider", "NullNetflowProvider",
    "FEATURE_COLUMNS", "FEATURE_GROUPS", "COLUMN_GROUP", "SIGNED_COLUMNS",
    "DIRECTIONAL_COLUMNS", "COVERAGE_COLUMNS", "BOUNDED_COLUMNS",
    "validate_features",
]
