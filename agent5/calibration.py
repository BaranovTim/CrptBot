"""Stage 3: turn a score into a probability you can defend.

The model's raw output is a number between 0 and 1 that correlates with the
outcome but is systematically off - because of class imbalance, sample
weighting and regularisation. It is not yet a probability.

Isotonic regression fixes this by learning a monotonic step function on
out-of-fold predictions:

    model says 0.72   ->   isotonic lookup   ->   0.61

0.61 is what gets shown and what the EV formula uses. The property being
bought: every time the calibrated output says 61%, roughly 61% of those
trades should win. That property is the entire justification for putting a
number on a screen, and - given the plan's legal notes about advertising
accuracy - the only defensible way to publish one.

Fitted on OUT-OF-FOLD predictions only. Fitting calibration on in-sample
predictions calibrates the model's memory, not its skill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Calibrator:
    """Wraps an isotonic fit, with a safe passthrough when it cannot be fitted."""

    iso: Optional[object] = None
    fitted: bool = False
    n_samples: int = 0

    def transform(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, dtype=float)
        if not self.fitted or self.iso is None:
            return p
        out = np.full(len(p), np.nan)
        ok = ~np.isnan(p)
        out[ok] = self.iso.predict(p[ok])
        # isotonic can map to exactly 0 or 1; both are indefensible claims
        # about the future, and 0 breaks Kelly sizing outright
        return np.clip(out, 1e-6, 1 - 1e-6)


def fit_calibrator(oof: np.ndarray, y: np.ndarray,
                   weight: Optional[np.ndarray] = None) -> Calibrator:
    from sklearn.isotonic import IsotonicRegression

    ok = ~np.isnan(oof)
    if weight is not None:
        ok &= ~np.isnan(weight)
    p, t = np.asarray(oof)[ok], np.asarray(y)[ok]
    w = np.asarray(weight)[ok] if weight is not None else None

    # too few points, or only one class - isotonic would fit a constant and
    # claim perfect confidence. passthrough is the honest fallback
    if len(p) < 50 or len(np.unique(t)) < 2:
        return Calibrator(fitted=False, n_samples=len(p))

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(p, t, sample_weight=w)
    return Calibrator(iso=iso, fitted=True, n_samples=len(p))


def reliability_table(p: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Predicted vs actual, bucketed. The table that shows if 61% means 61%."""
    ok = ~np.isnan(p)
    p, y = np.asarray(p)[ok], np.asarray(y)[ok]
    if len(p) == 0:
        return pd.DataFrame(columns=["predicted", "actual", "n"])
    edges = np.linspace(0, 1, bins + 1)
    which = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        m = which == b
        if m.sum() == 0:
            continue
        rows.append({"bucket": f"{edges[b]:.1f}-{edges[b+1]:.1f}",
                     "predicted": float(p[m].mean()),
                     "actual": float(y[m].mean()),
                     "n": int(m.sum())})
    return pd.DataFrame(rows)


def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    """Mean squared error of the probabilities. Lower is better; 0.25 = coin flip."""
    ok = ~np.isnan(p)
    if ok.sum() == 0:
        return float("nan")
    return float(np.mean((np.asarray(p)[ok] - np.asarray(y)[ok]) ** 2))


def calibration_error(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error: average |predicted - actual| across buckets."""
    tbl = reliability_table(p, y, bins)
    if tbl.empty:
        return float("nan")
    w = tbl["n"] / tbl["n"].sum()
    return float((w * (tbl["predicted"] - tbl["actual"]).abs()).sum())
