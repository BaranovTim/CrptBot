"""Configuration for Agent 1.

These are constants, not data. Nothing here is fitted from history — if a
number in this file ever starts being chosen by looking at outcomes, it has
silently become part of Agent 5 and must move there.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Agent1Config:
    # --- pivots -----------------------------------------------------------
    pivot_left: int = 3
    pivot_right: int = 3
    # A pivot at bar i is usable from bar i + confirm_bars and never earlier.
    # Keep this equal to pivot_right unless you know why you want a lag.
    confirm_bars: int = 3

    # --- ATR --------------------------------------------------------------
    atr_period: int = 14

    # --- structure --------------------------------------------------------
    # "close": a level is broken only by a bar close beyond it (quieter).
    # "wick":  a level is broken by any trade beyond it (catches sweeps).
    break_mode: str = "close"

    # --- zones ------------------------------------------------------------
    max_ob_lookback: int = 30      # bars back from a break to hunt the order block
    max_active_zones: int = 40     # cap the tracked zone lists
    fvg_min_size_atr: float = 0.05  # ignore microscopic imbalances

    # --- liquidity --------------------------------------------------------
    equal_level_tol_atr: float = 0.15   # how close two swings must be to be "equal"
    equal_level_lookback: int = 8       # how many recent swings to cluster over
    asia_session_utc: tuple = (0, 8)    # [start_hour, end_hour) in UTC

    # --- figures ----------------------------------------------------------
    figure_tol_atr: float = 0.5     # symmetry tolerance for W / M / H&S
    figure_min_sep: int = 5         # min bars between the two shoulders/bottoms
    figure_max_sep: int = 120       # max bars

    # --- candles ----------------------------------------------------------
    candle_window: int = 3          # cdl_*_count_N aggregation window
    use_pandas_ta_classic: bool = False  # True -> use its 62 CDL patterns if installed

    # --- higher timeframe -------------------------------------------------
    htf_rule: str = "4h"

    def __post_init__(self) -> None:
        if self.break_mode not in ("close", "wick"):
            raise ValueError("break_mode must be 'close' or 'wick'")
        if self.confirm_bars < self.pivot_right:
            raise ValueError(
                "confirm_bars must be >= pivot_right, otherwise a pivot would be "
                "published before the bars that prove it exists have closed"
            )


DEFAULT_CONFIG = Agent1Config()
