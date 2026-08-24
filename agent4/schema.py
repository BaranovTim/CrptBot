"""The output contract of Agent 4.

Same three rules as the other detectors, plus a boundary note.

  SIGN     Every ``*_imbalance``, ``*_direction``, ``ofi_*`` and
           ``aggressor_*`` column is +1-flavoured bullish: positive means
           aggressive buying, or shorts being liquidated. ``taker_buy_ratio``
           is a 0-1 share where 0.5 is balanced, not a signed column.

  MISSING  NaN when the underlying feed is absent. This matters more here
           than anywhere else in the project, because Agent 4's inputs are
           genuinely optional: aggTrades may not be downloaded, open interest
           may not be published, exchange netflow is a paid feed most people
           will not have. A zero would read as "no whales were active", which
           is the opposite of "we cannot see whether they were". The two
           coverage columns say which case you are in.

  UNITS    Ratios, shares, z-scores and bar ages. No raw dollar figures —
           $500k of flow means something different at $20k BTC than at $100k,
           so everything notional is either a share of its own bar or
           standardised against a rolling baseline.

BOUNDARY: funding rate, realized-volatility percentile and volume-vs-baseline
are the REGIME block's, not Agent 4's, even though they sit next to order flow
conceptually. ``avg_trade_size_usd_z`` and ``trade_count_z`` are here because
they describe the *character* of flow (few large prints vs many small ones),
which is a different question from "is volume high right now".
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

FEATURE_GROUPS: "OrderedDict[str, tuple]" = OrderedDict(
    [
        (
            "flow",
            (
                "taker_buy_ratio",          # free from klines; 0.5 = balanced
                "ofi_z",                    # order flow imbalance, standardised
                "aggressor_imbalance",      # this bar: (buy - sell) / total
                "cvd_slope_z",              # cumulative volume delta slope
                "flow_price_corr_20",       # flow/price divergence
            ),
        ),
        (
            "prints",
            (
                "large_print_imbalance_1h",
                "large_print_volume_share",
                "large_print_count_z",
                "max_print_usd_z",
                "bars_since_large_print",
            ),
        ),
        (
            "size",
            (
                "avg_trade_size_usd_z",     # few big fills vs many small ones
                "top_print_share",          # biggest single fill / bar volume
                "trade_count_z",
            ),
        ),
        (
            "positioning",
            (
                "oi_change_pct",
                "oi_change_z",
                "oi_price_divergence",      # OI up while price down = shorts building
                "liq_imbalance_1h",
                "bars_since_liq_spike",
            ),
        ),
        (
            "netflow",
            (
                "exchange_netflow_z",
                "exchange_netflow_direction",
            ),
        ),
        (
            "quality",
            (
                "tape_coverage_24h",        # did we actually see the tape?
                "netflow_coverage_24h",
            ),
        ),
    ]
)

FEATURE_COLUMNS: tuple = tuple(c for cols in FEATURE_GROUPS.values() for c in cols)
COLUMN_GROUP = {c: g for g, cols in FEATURE_GROUPS.items() for c in cols}

# Positive must mean "buyers aggressing" / "shorts under pressure".
SIGNED_COLUMNS = (
    "ofi_z", "aggressor_imbalance", "cvd_slope_z", "large_print_imbalance_1h",
    "liq_imbalance_1h", "exchange_netflow_direction",
)

# Standardised against their own rolling mean, so they centre near zero
# whatever the trend does — excluded from the directional sign test for the
# same reason as Agent 2's obv_slope_z.
SELF_CENTRING_COLUMNS = ("ofi_z", "cvd_slope_z")

DIRECTIONAL_COLUMNS = tuple(
    c for c in SIGNED_COLUMNS if c not in set(SELF_CENTRING_COLUMNS)
)

BOUNDED_COLUMNS = {
    "taker_buy_ratio": (0.0, 1.0),
    "aggressor_imbalance": (-1.0, 1.0),
    "large_print_imbalance_1h": (-1.0, 1.0),
    "large_print_volume_share": (0.0, 1.0),
    "top_print_share": (0.0, 1.0),
    "liq_imbalance_1h": (-1.0, 1.0),
    "flow_price_corr_20": (-1.0, 1.0),
    "exchange_netflow_direction": (-1.0, 1.0),
    "tape_coverage_24h": (0.0, 1.0),
    "netflow_coverage_24h": (0.0, 1.0),
}

# "We looked and saw nothing" is an observed fact; these are never NaN.
COVERAGE_COLUMNS = ("tape_coverage_24h", "netflow_coverage_24h")


def validate_features(df: pd.DataFrame) -> pd.DataFrame:
    got = tuple(df.columns)
    if got != FEATURE_COLUMNS:
        missing = [c for c in FEATURE_COLUMNS if c not in got]
        extra = [c for c in got if c not in FEATURE_COLUMNS]
        raise ValueError(
            "Agent 4 feature schema mismatch.\n"
            f"  expected {len(FEATURE_COLUMNS)} columns, got {len(got)}\n"
            f"  missing: {missing}\n  extra: {extra}"
        )
    bad = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    if bad:
        raise ValueError(f"non-numeric feature columns: {bad}")
    return df
