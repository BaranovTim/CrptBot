"""Shared infrastructure. Not an agent.

The agents deliberately never import each other, because the ablation plan
deletes one block at a time and an agent that stops importing when its
neighbour is removed cannot be ablated.

This module is different: it is infrastructure, like `marketdata/`. It is
never deleted during an ablation, so depending on it costs nothing, and
having ONE input validator beats four copies that drift apart.
"""
from .health import FeatureHealth, feature_report
from .timeframes import (
    BARRIERS,
    GEOMETRY,
    geometry_for,
    slot_side,
    HTF_FOR,
    TIMEFRAMES,
    barriers_for,
    bars_per_day,
    history_start,
    htf_for,
    interval_seconds,
    is_trained,
    model_paths, eval_path, beats_shuffle, model_usable,
    pandas_rule,
)
from .validation import (
    REQUIRED_OHLCV,
    check_bars,
    describe_bars_problem,
    utc_now,
)

__all__ = ["check_bars", "describe_bars_problem", "REQUIRED_OHLCV",
           "feature_report", "FeatureHealth", "utc_now",
           "TIMEFRAMES", "HTF_FOR", "BARRIERS", "GEOMETRY", "geometry_for", "slot_side",
           "barriers_for", "pandas_rule", "htf_for", "history_start",
           "interval_seconds", "bars_per_day", "model_paths", "is_trained"]
