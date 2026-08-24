"""The output contract: shape, sign convention, and the NaN rule."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent1 import PatternAgent
from agent1.schema import FEATURE_COLUMNS, validate_features
from tests.synthetic import make_bars

AGENT = PatternAgent()
BARS = make_bars(1000)
FEATURES = AGENT.compute(BARS)


def test_shape_and_order():
    assert tuple(FEATURES.columns) == FEATURE_COLUMNS
    assert len(FEATURES) == len(BARS)
    assert FEATURES.index.equals(BARS.index)
    validate_features(FEATURES)
    return True


def test_no_infinities():
    """Inf is the failure mode of ATR normalisation on a flat window."""
    inf_cols = [c for c in FEATURES.columns if np.isinf(FEATURES[c].to_numpy()).any()]
    assert not inf_cols, f"infinite values in {inf_cols} — ATR division not guarded"
    return True


def test_no_sentinel_values():
    """Absence is NaN. Never 0, never -999.

    Zero means 'price is standing exactly on it' — the opposite of 'it isn't
    there'. A sentinel would be learned as a real magnitude.
    """
    for c in FEATURES.columns:
        vals = FEATURES[c].dropna()
        assert not (vals == -999).any(), f"{c} uses a -999 sentinel"
    return True


def test_direction_columns_are_ternary():
    for c in ("trend_direction", "fvg_direction", "sweep_direction",
              "inside_ob", "hs_direction", "fib_leg_direction",
              "trend_direction_4h"):
        vals = set(FEATURES[c].dropna().unique())
        assert vals <= {-1.0, 0.0, 1.0}, f"{c} carries non-ternary values: {sorted(vals)}"
    return True


def test_completion_fractions_bounded():
    for c in ("w_completion", "m_completion", "hs_completion"):
        v = FEATURES[c].dropna()
        assert v.between(0.0, 1.0).all(), f"{c} escaped [0,1]: {v.min()}..{v.max()}"
    return True


def test_inside_ob_is_observed_not_missing():
    """'Not inside a zone' is a fact, so this column has no NaN after warmup."""
    tail = FEATURES["inside_ob"].iloc[AGENT.warmup_bars:]
    assert tail.notna().all(), "inside_ob should never be NaN once ATR has warmed up"
    return True


def test_features_are_populated():
    """Guard against a schema that is technically valid and entirely empty."""
    tail = FEATURES.iloc[AGENT.warmup_bars:]
    empty = [c for c in tail.columns if tail[c].notna().sum() == 0]
    assert not empty, f"columns never produced a value on 1000 bars: {empty}"
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


def test_rejects_open_time_index():
    """Indexing by open_time hands you the bar's own close an hour early."""
    bad = BARS.reset_index(drop=True)
    try:
        AGENT.compute(bad)
    except TypeError:
        return True
    raise AssertionError("a non-DatetimeIndex should have been rejected")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} schema tests passed.")
