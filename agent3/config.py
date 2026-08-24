"""Configuration for Agent 3."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Agent3Config:
    # --- asset scoping ----------------------------------------------------
    asset: str = "BTC"
    # An item tagged for other assets only is excluded. Items with no asset
    # tag are treated as market-wide and kept: "Fed hikes 50bp" carries no
    # ticker but moves everything.
    include_untagged: bool = True

    # --- point-in-time discipline ----------------------------------------
    # Added to max(published_at, ingested_at). Aggregators backfill and revise
    # publication times, so a margin here buys the guarantee that a later
    # revision cannot pull an item earlier than the moment you saw it.
    # Cost: you act a minute late. Benefit: the backtest is honest.
    safety_lag_seconds: int = 60

    # --- aggregation windows ---------------------------------------------
    short_window_hours: float = 1.0
    mid_window_hours: float = 6.0
    long_window_hours: float = 24.0

    # Floor on the decay half-life. Above this, each item decays on its own
    # scored horizon, so a regulatory decision stays in the average far longer
    # than a liquidation headline.
    min_half_life_hours: float = 1.0
    baseline_bars: int = 720          # rolling window for the news-count z-score


DEFAULT_CONFIG = Agent3Config()
