"""The output contract of Agent 2.

Same three rules as Agent 1, plus one that is specific to indicators and is
the single biggest trap in this file.

  1. SIGN.  Signed columns are +1-flavoured bullish: positive means up.
     Bounded oscillators (RSI, Stochastic, MFI, ADX, bb_position) carry no
     sign — they are levels, and 30 is not "negative".  Which is which is
     recorded in SIGNED_COLUMNS / BOUNDED_COLUMNS below, so a sign flip in a
     signed column is catchable by test rather than by eyeball.

  2. MISSING.  Absent or undefined is NaN.  Never 0, never -999.  An RSI
     that cannot be computed yet is not an RSI of zero.

  3. UNITS.  Nothing may be emitted in raw price units.  A MACD histogram of
     50 is enormous for BTC at 20k and noise at 100k; feed the raw value in
     and you have handed the model a feature whose meaning drifts with the
     price level across your whole training window.  Everything in price
     units is divided by ATR (``*_atr``) or by close (``*_pct``).  This is
     the indicator equivalent of Agent 1's "normalise by ATR, not price".

     A consequence worth knowing before you add a column: with the default
     12/26 periods, ``ema_spread_atr`` IS the MACD line divided by ATR — the
     same number, not an approximation.  Adding a separate ``macd_line_atr``
     would put a perfect duplicate into the model.  With correlated inputs a
     logistic regression answers duplicates with huge cancelling
     coefficients (+8.3 on one, -8.1 on the other), which are meaningless
     individually and unstable across folds.

  4. NORMALISATION MUST BE CAUSAL.  This is Agent 2's characteristic bug and
     it is quiet.  A z-score or percentile computed against full-sample mean
     and standard deviation leaks the entire future into every row — the
     numbers look perfectly reasonable, nothing raises, and the backtest
     improves.  Every standardisation here is a ROLLING window.  There is no
     ``.mean()`` over a whole column anywhere in this package, and
     ``tests/test_agent2_no_lookahead.py`` is what keeps it that way.
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

FEATURE_GROUPS: "OrderedDict[str, tuple]" = OrderedDict(
    [
        (
            "trend",
            (
                "ema_spread_atr",        # (EMA12 - EMA26) / ATR == the MACD LINE
                "price_vs_ema200_atr",   # (close - EMA200) / ATR
                "ema50_slope_atr",       # EMA50 rise over 10 bars, in ATR
                "adx_14",                # 0-100, strength only, no direction
                "di_spread_14",          # +DI minus -DI
                "macd_hist_atr",         # MACD histogram / ATR -- acceleration
            ),
        ),
        (
            "momentum",
            (
                "rsi_14",
                "rsi_slope_3",           # RSI change over 3 bars
                "stoch_k_14",
                "stoch_kd_spread",       # %K minus %D
                "cci_20",
                "roc_10_atr",            # 10-bar change measured in ATRs
            ),
        ),
        (
            "volatility",
            (
                "atr_pct",               # ATR / close
                "tr_vs_atr",             # this bar's true range / ATR
                "bb_position",           # 0 at lower band, 1 at upper, unclipped
                "bb_width_atr",          # band width / ATR
                "bb_kc_ratio",           # Bollinger width / Keltner width; <1 = squeeze
            ),
        ),
        (
            "volume",
            (
                "mfi_14",
                "cmf_20",                # -1..1
                "obv_slope_z",           # rolling z-score of the OBV change
            ),
        ),
        (
            "meanrev",
            (
                "zscore_close_20",       # (close - SMA20) / rolling sd
                "rsi_price_corr_20",     # divergence, as a continuous correlation
                "bars_since_macd_cross",
                "macd_cross_direction",
            ),
        ),
        (
            "htf",
            (
                "rsi_14_4h",
                "macd_hist_atr_4h",
            ),
        ),
    ]
)

FEATURE_COLUMNS: tuple = tuple(c for cols in FEATURE_GROUPS.values() for c in cols)
COLUMN_GROUP = {c: g for g, cols in FEATURE_GROUPS.items() for c in cols}

# Positive must mean bullish in every one of these.
SIGNED_COLUMNS = (
    "ema_spread_atr", "price_vs_ema200_atr", "ema50_slope_atr", "di_spread_14",
    "rsi_slope_3", "stoch_kd_spread", "cci_20", "roc_10_atr",
    "cmf_20", "obv_slope_z", "zscore_close_20", "macd_cross_direction",
)

# Signed, but NOT direction indicators, so they are excluded from the
# uptrend/downtrend sign test.
#
#   macd_hist_atr / macd_hist_atr_4h -- the histogram is the MACD line minus
#     its own signal line, i.e. whether momentum is speeding up or slowing
#     down. It sits near zero in a steady trend of either direction, and it
#     is perfectly normal for it to be positive while price falls (a decline
#     that is decelerating). Read it as "momentum is turning", never as
#     "price is going up".
#
#   obv_slope_z, rsi_slope_3 -- the change in a bounded or self-standardised
#     quantity. Over a long window these MUST average about zero: RSI cannot
#     drift past 100, so the sum of its 3-bar changes telescopes to at most
#     one bounded quantity spread over thousands of bars. The instantaneous
#     sign is still meaningful (RSI rising is bullish); the long-run mean
#     simply carries no directional information.
#   macd_cross_direction -- the direction of a HISTOGRAM cross, so it inherits
#     the histogram's meaning: which way momentum last turned, not which way
#     price is going. Measured at +0.23 in both a strong uptrend and a strong
#     downtrend.
#
#   stoch_kd_spread -- %K minus its own moving average: same self-centring
#     argument as rsi_slope_3.
#
#   cmf_20 -- directional, but a price ramp cannot test it: scaling OHLC
#     uniformly leaves the close's position inside each bar unchanged. Its
#     sign convention is verified directly instead, in
#     tests/test_agent2_indicators.py::test_cmf_endpoints (+1 when every bar
#     closes on its high, -1 on its low).
ACCELERATION_COLUMNS = ("macd_hist_atr", "macd_hist_atr_4h", "macd_cross_direction")
SELF_CENTRING_COLUMNS = ("obv_slope_z", "rsi_slope_3", "stoch_kd_spread")
UNTESTABLE_BY_RAMP = ("cmf_20",)

# The columns whose sign the uptrend/downtrend test can actually check.
DIRECTIONAL_COLUMNS = tuple(
    c for c in SIGNED_COLUMNS
    if c not in set(ACCELERATION_COLUMNS) | set(SELF_CENTRING_COLUMNS)
    | set(UNTESTABLE_BY_RAMP)
)

# Levels, not directions. Asking whether these are "positive" is meaningless.
BOUNDED_COLUMNS = {
    "rsi_14": (0.0, 100.0),
    "stoch_k_14": (0.0, 100.0),
    "mfi_14": (0.0, 100.0),
    "adx_14": (0.0, 100.0),
    "rsi_14_4h": (0.0, 100.0),
    "cmf_20": (-1.0, 1.0),
    "rsi_price_corr_20": (-1.0, 1.0),
    "macd_cross_direction": (-1.0, 1.0),
}


def validate_features(df: pd.DataFrame) -> pd.DataFrame:
    got = tuple(df.columns)
    if got != FEATURE_COLUMNS:
        missing = [c for c in FEATURE_COLUMNS if c not in got]
        extra = [c for c in got if c not in FEATURE_COLUMNS]
        raise ValueError(
            "Agent 2 feature schema mismatch.\n"
            f"  expected {len(FEATURE_COLUMNS)} columns, got {len(got)}\n"
            f"  missing: {missing}\n  extra: {extra}"
        )
    bad = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    if bad:
        raise ValueError(f"non-numeric feature columns: {bad}")
    return df
