"""The output contract of Agent 1.

Two rules govern every column here, and breaking either one is a bug:

  1. SIGN.  Every ``*_direction`` / ``trend_*`` / ``inside_*`` column is
     +1 = bullish, -1 = bearish, 0 = none.  Every ``dist_to_*`` column is
     ``(level - close) / atr`` — positive means the level sits ABOVE the
     current price.  One rule, no exceptions, so a sign flip is visible.

  2. MISSING.  When a thing does not exist (no unfilled FVG, no equal highs),
     the value is NaN — never 0, never -999.  Zero means "price is standing
     exactly on it", which is the opposite of "it isn't there".  LightGBM
     handles NaN natively and learns which branch to send it down.

The column list is fixed and ordered.  If it silently becomes 32 columns on
live data one day, the model will map values onto the wrong features and
nothing will raise.  ``validate_features`` is what stops that.
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

# Grouped so Agent 5 can report permutation importance per block — that
# grouping is the operational version of the N / W / P / I weights.
FEATURE_GROUPS: "OrderedDict[str, tuple]" = OrderedDict(
    [
        (
            "structure",
            (
                "trend_direction",
                "bars_since_bos",
                "bars_since_choch",
                "consecutive_bos_count",
                "position_in_range",
                "range_size_atr",
                "bars_since_sweep",
                "sweep_direction",
            ),
        ),
        (
            "zones",
            (
                "dist_to_bull_ob_atr",
                "dist_to_bear_ob_atr",
                "inside_ob",
                "nearest_ob_age_bars",
                "dist_to_fvg_atr",
                "fvg_size_atr",
                "fvg_direction",
                "nearest_fvg_age_bars",
            ),
        ),
        (
            "liquidity",
            (
                "dist_to_equal_highs_atr",
                "dist_to_equal_lows_atr",
                "dist_to_pdh_atr",
                "dist_to_pdl_atr",
                "dist_to_asia_high_atr",
                "dist_to_asia_low_atr",
            ),
        ),
        (
            "fib",
            (
                "fib_position",
                "fib_leg_direction",
                "dist_to_fib_618_atr",
            ),
        ),
        (
            "figures",
            (
                "w_completion",
                "m_completion",
                "hs_completion",
                "hs_direction",
            ),
        ),
        (
            "candles",
            (
                "cdl_bull_count_3",
                "cdl_bear_count_3",
            ),
        ),
        (
            "htf",
            (
                "trend_direction_4h",
                "position_in_range_4h",
            ),
        ),
    ]
)

FEATURE_COLUMNS: tuple = tuple(c for cols in FEATURE_GROUPS.values() for c in cols)

# Which block each column belongs to; handy when scoring feature importance.
COLUMN_GROUP = {c: g for g, cols in FEATURE_GROUPS.items() for c in cols}


def validate_features(df: pd.DataFrame) -> pd.DataFrame:
    """Assert the frame matches the contract exactly, then return it."""
    got = tuple(df.columns)
    if got != FEATURE_COLUMNS:
        missing = [c for c in FEATURE_COLUMNS if c not in got]
        extra = [c for c in got if c not in FEATURE_COLUMNS]
        raise ValueError(
            "Agent 1 feature schema mismatch.\n"
            f"  expected {len(FEATURE_COLUMNS)} columns, got {len(got)}\n"
            f"  missing: {missing}\n"
            f"  extra:   {extra}\n"
            f"  reordered: {missing == [] and extra == []}"
        )
    bad = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    if bad:
        raise ValueError(f"non-numeric feature columns: {bad}")
    return df


def empty_features(index: pd.Index) -> pd.DataFrame:
    """An all-NaN frame with the right shape — the starting point for a pass."""
    return pd.DataFrame(float("nan"), index=index, columns=list(FEATURE_COLUMNS))
