"""The output contract of Agent 3.

Same rules as Agents 1 and 2:

  SIGN     ``news_sentiment_*`` and ``news_direction_last`` are +1-flavoured
           bullish. Counts, magnitudes and novelties are unsigned.

  MISSING  Absent is NaN, never 0. This distinction is unusually sharp here:
           ``news_sentiment_6h = 0`` means news exists and it is neutral;
           ``NaN`` means there was no news at all. Those are entirely
           different market states and collapsing them would be a real loss.
           The count columns are the mirror image — a 24h count of 0 is an
           observed fact, so they are 0-filled.

  UNITS    Everything is a bounded ratio, a count, or a bar age. Nothing is in
           price or currency units, so nothing drifts with the price level.
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

FEATURE_GROUPS: "OrderedDict[str, tuple]" = OrderedDict(
    [
        (
            "sentiment",
            (
                "news_sentiment_1h",
                "news_sentiment_6h",
                "news_sentiment_24h",
                "news_sentiment_dispersion_24h",   # do the items agree?
                "news_direction_last",
            ),
        ),
        (
            "salience",
            (
                "news_max_magnitude_6h",
                "news_magnitude_mean_24h",
                "news_novelty_max_6h",
                "news_novelty_last",
                "bars_since_news",
            ),
        ),
        (
            "volume",
            (
                "news_count_6h",
                "news_count_24h",
                "news_count_z_24h",               # unusual news volume
                "news_credibility_mean_24h",
            ),
        ),
        (
            "category",
            (
                "news_cat_regulatory_24h",
                "news_cat_listing_24h",
                "news_cat_security_24h",
                "news_cat_macro_24h",
            ),
        ),
        (
            "quality",
            (
                "news_scored_fraction_24h",       # is the pipeline healthy?
            ),
        ),
    ]
)

FEATURE_COLUMNS: tuple = tuple(c for cols in FEATURE_GROUPS.values() for c in cols)
COLUMN_GROUP = {c: g for g, cols in FEATURE_GROUPS.items() for c in cols}

SIGNED_COLUMNS = (
    "news_sentiment_1h", "news_sentiment_6h", "news_sentiment_24h",
    "news_direction_last",
)

# Observed facts rather than measurements: "no news in the window" is a real,
# fully-known state, so these are 0 rather than NaN.
COUNT_COLUMNS = (
    "news_count_6h", "news_count_24h",
    "news_cat_regulatory_24h", "news_cat_listing_24h",
    "news_cat_security_24h", "news_cat_macro_24h",
)

BOUNDED_COLUMNS = {
    "news_sentiment_1h": (-1.0, 1.0),
    "news_sentiment_6h": (-1.0, 1.0),
    "news_sentiment_24h": (-1.0, 1.0),
    "news_sentiment_dispersion_24h": (0.0, 1.0),
    "news_direction_last": (-1.0, 1.0),
    "news_max_magnitude_6h": (0.0, 1.0),
    "news_magnitude_mean_24h": (0.0, 1.0),
    "news_novelty_max_6h": (0.0, 1.0),
    "news_novelty_last": (0.0, 1.0),
    "news_credibility_mean_24h": (0.0, 1.0),
    "news_scored_fraction_24h": (0.0, 1.0),
}


def validate_features(df: pd.DataFrame) -> pd.DataFrame:
    got = tuple(df.columns)
    if got != FEATURE_COLUMNS:
        missing = [c for c in FEATURE_COLUMNS if c not in got]
        extra = [c for c in got if c not in FEATURE_COLUMNS]
        raise ValueError(
            "Agent 3 feature schema mismatch.\n"
            f"  expected {len(FEATURE_COLUMNS)} columns, got {len(got)}\n"
            f"  missing: {missing}\n  extra: {extra}"
        )
    bad = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    if bad:
        raise ValueError(f"non-numeric feature columns: {bad}")
    return df
