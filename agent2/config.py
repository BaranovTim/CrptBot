"""Configuration for Agent 2.

Constants, not data. The instant a period here is chosen by checking which
value made money, it has stopped being configuration and become a fitted
parameter — which belongs in Agent 5, not in a detector.

The defaults are the conventional ones (14 for RSI, 12/26/9 for MACD, 20/2
for Bollinger) on purpose. Not because they are optimal — they are not — but
because tuning them against outcomes here is exactly the double-counting the
architecture exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Agent2Config:
    # --- trend ------------------------------------------------------------
    ema_fast: int = 12
    ema_slow: int = 26
    ema_baseline: int = 200
    ema_slope: int = 50
    slope_lookback: int = 10
    macd_signal: int = 9
    adx_period: int = 14

    # --- momentum ---------------------------------------------------------
    rsi_period: int = 14
    rsi_slope_lookback: int = 3
    stoch_k: int = 14
    stoch_d: int = 3
    cci_period: int = 20
    roc_period: int = 10

    # --- volatility -------------------------------------------------------
    atr_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    kc_period: int = 20
    kc_mult: float = 2.0

    # --- volume -----------------------------------------------------------
    mfi_period: int = 14
    cmf_period: int = 20
    obv_diff: int = 20
    obv_z_window: int = 100

    # --- mean reversion / divergence --------------------------------------
    zscore_period: int = 20
    divergence_window: int = 20

    # --- higher timeframe -------------------------------------------------
    htf_rule: str = "4h"

    def __post_init__(self) -> None:
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be shorter than ema_slow")
        if self.obv_z_window <= self.obv_diff:
            raise ValueError("obv_z_window must exceed obv_diff")


DEFAULT_CONFIG = Agent2Config()
