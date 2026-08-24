"""Agent 2's output contract: shape, bounds, NaN policy, and sign convention."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent2 import IndicatorAgent
from agent2.schema import BOUNDED_COLUMNS, FEATURE_COLUMNS, validate_features
from tests.synthetic import make_bars

AGENT = IndicatorAgent()
BARS = make_bars(1500)
FEATURES = AGENT.compute(BARS)


def _trending_bars(drift: float, n: int = 900, seed: int = 5) -> pd.DataFrame:
    """A noisy but decisively directional market, for sign-convention checks."""
    bars = make_bars(n, seed=seed)
    ramp = np.exp(drift * np.arange(n))
    for col in ("open", "high", "low", "close"):
        bars[col] = bars[col] * ramp
    return bars


def test_shape_and_order():
    assert tuple(FEATURES.columns) == FEATURE_COLUMNS
    assert len(FEATURES) == len(BARS)
    assert FEATURES.index.equals(BARS.index)
    validate_features(FEATURES)
    return True


def test_no_infinities():
    """inf is what unguarded ATR/std division produces on a flat window."""
    bad = [c for c in FEATURES.columns if np.isinf(FEATURES[c].to_numpy()).any()]
    assert not bad, f"infinite values in {bad}"
    return True


def test_no_sentinel_values():
    for c in FEATURES.columns:
        assert not (FEATURES[c].dropna() == -999).any(), f"{c} uses a -999 sentinel"
    return True


def test_bounded_columns_respect_bounds():
    for col, (lo, hi) in BOUNDED_COLUMNS.items():
        v = FEATURES[col].dropna()
        assert v.between(lo, hi).all(), (
            f"{col} escaped [{lo}, {hi}]: {v.min():.4f}..{v.max():.4f}"
        )
    return True


def test_nothing_is_in_raw_price_units():
    """A feature that scales with the price level is non-stationary.

    Multiply every price by 10 and the features must barely move. If one
    tracks the multiplier, it is in price units and its meaning will drift
    across a multi-year training window.
    """
    scaled = BARS.copy()
    for col in ("open", "high", "low", "close"):
        scaled[col] = scaled[col] * 10.0
    other = AGENT.compute(scaled)

    tail = slice(AGENT.warmup_bars, None)
    offenders = []
    for c in FEATURE_COLUMNS:
        a = FEATURES[c].iloc[tail].to_numpy()
        b = other[c].iloc[tail].to_numpy()
        both = ~np.isnan(a) & ~np.isnan(b)
        if both.sum() == 0:
            continue
        if not np.allclose(a[both], b[both], rtol=1e-6, atol=1e-6):
            offenders.append(c)
    assert not offenders, f"these scale with the price level: {offenders}"
    return True


def test_sign_convention_positive_is_bullish():
    """In a strong uptrend the signed columns must lean positive, and flip.

    This is the guard against the sign bug: if bearish input produced a
    positive number somewhere, the model would learn to read bad news as a
    buy, and nothing else in the test suite would notice.

    Two families are excluded, and the reasons are in schema.py:
    `macd_hist_*` measures momentum acceleration rather than direction (it
    sits near zero in a steady trend either way), and `obv_slope_z` is a
    z-score against its own rolling mean, so it centres near zero whatever
    the trend does. Both are correct behaviour, not sign errors.
    """
    from agent2.schema import DIRECTIONAL_COLUMNS

    directional = list(DIRECTIONAL_COLUMNS)
    assert len(directional) >= 7, "sign test lost too much coverage"
    up = IndicatorAgent().compute(_trending_bars(+0.004))
    down = IndicatorAgent().compute(_trending_bars(-0.004))
    warm = AGENT.warmup_bars

    for c in directional:
        mu_up = up[c].iloc[warm:].mean()
        mu_dn = down[c].iloc[warm:].mean()
        assert mu_up > 0, f"{c} is {mu_up:.4f} in an uptrend — sign convention broken"
        assert mu_dn < 0, f"{c} is {mu_dn:.4f} in a downtrend — sign convention broken"
    return True


def test_features_are_populated():
    tail = FEATURES.iloc[AGENT.warmup_bars:]
    empty = [c for c in tail.columns if tail[c].notna().sum() == 0]
    assert not empty, f"columns never produced a value on 1500 bars: {empty}"
    return True


def test_latest_matches_compute():
    out = AGENT.latest(BARS)
    row = FEATURES.iloc[-1]
    assert out.timestamp == FEATURES.index[-1]
    assert set(out.features) == set(FEATURE_COLUMNS)
    for c in FEATURE_COLUMNS:
        a, b = out.features[c], row[c]
        assert (pd.isna(a) and pd.isna(b)) or np.isclose(a, b), f"{c} differs"
    return True


def test_requires_volume():
    """MFI, CMF and OBV need volume; failing loudly beats silent NaN columns."""
    try:
        AGENT.compute(BARS.drop(columns=["volume"]))
    except ValueError:
        return True
    raise AssertionError("missing volume should have been rejected")


def test_rejects_open_time_index():
    try:
        AGENT.compute(BARS.reset_index(drop=True))
    except TypeError:
        return True
    raise AssertionError("a non-DatetimeIndex should have been rejected")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} schema tests passed.")
