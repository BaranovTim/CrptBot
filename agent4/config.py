"""Configuration for Agent 4."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Agent4Config:
    # --- large prints -----------------------------------------------------
    # "Large" is defined relative to the recent size distribution, not as a
    # fixed dollar figure. A $500k print was a whale in 2020 and is ordinary
    # now; a fixed threshold silently changes meaning across a multi-year
    # training window, which is the same non-stationarity trap as feeding a
    # raw MACD to Agent 2.
    # Top 0.5% of trailing PRINTS BY COUNT — a percentile of the size
    # distribution, not of dollar volume. Chosen by sweeping the value and
    # looking at feature distribution: at 0.05 large prints capture ~85% of
    # bar volume (almost no discrimination), at 0.005 they capture ~50% with
    # the highest variance in both large-print columns.
    #
    # Tune it on distribution shape if you like. Do NOT tune it on trading
    # outcomes — a threshold chosen because it made money is a fitted
    # parameter, and fitted parameters belong to Agent 5, not to a detector.
    large_print_top_share: float = 0.005
    print_threshold_bars: int = 168       # trailing window for the threshold
    large_print_z_window: int = 336

    # --- flow -------------------------------------------------------------
    flow_z_window: int = 168
    cvd_slope_bars: int = 12
    divergence_window: int = 20

    # --- positioning ------------------------------------------------------
    oi_change_bars: int = 1
    oi_z_window: int = 336
    liq_spike_z: float = 2.0

    # --- coverage ---------------------------------------------------------
    coverage_window: int = 24

    def __post_init__(self) -> None:
        if not 0.0 < self.large_print_top_share < 1.0:
            raise ValueError("large_print_top_share must be in (0, 1)")


DEFAULT_CONFIG = Agent4Config()
