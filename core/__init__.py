"""Shared infrastructure. Not an agent.

The agents deliberately never import each other, because the ablation plan
deletes one block at a time and an agent that stops importing when its
neighbour is removed cannot be ablated.

This module is different: it is infrastructure, like `marketdata/`. It is
never deleted during an ablation, so depending on it costs nothing, and
having ONE input validator beats four copies that drift apart.
"""
from .health import FeatureHealth, feature_report
from .validation import (
    REQUIRED_OHLCV,
    check_bars,
    describe_bars_problem,
)

__all__ = ["check_bars", "describe_bars_problem", "REQUIRED_OHLCV",
           "feature_report", "FeatureHealth"]
