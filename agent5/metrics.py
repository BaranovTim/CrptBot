"""Evaluation. The scoreboard, and the tests that stop it lying.

The plan's three checks before moving on to calibration:
  1. the gap between train AUC and out-of-fold AUC
  2. out-of-fold AUC above ~0.53 on MOST folds, not on average
  3. the shuffle test returns nothing

Point 2 matters more than it sounds. Folds of 0.61 / 0.49 / 0.58 / 0.51 /
0.57 average to a respectable 0.55, and the spread says you caught noise.
Stable 0.54 / 0.53 / 0.55 / 0.54 is worth far more than a better mean.

The shuffle test is re-run after every feature block is added, because
leakage usually arrives with a new DATA SOURCE, not with a new model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .calibration import brier_score, calibration_error, reliability_table
from .config import Agent5Config
from .dataset import Dataset
from .model import FitResult, fit_cv


@dataclass
class Evaluation:
    auc: float
    auc_folds: List[float]
    auc_spread: float
    overfit_gap: float
    brier_raw: float
    brier_calibrated: float
    calibration_error_raw: float
    calibration_error_calibrated: float
    n_samples: int
    effective_n: float
    win_rate: float
    reliability: pd.DataFrame = field(default_factory=pd.DataFrame)
    shuffle_auc: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        L = [
            f"samples {self.n_samples:,} ({self.effective_n:.0f} effective) "
            f"win rate {self.win_rate:.1%}",
            f"AUC {self.auc:.3f}  folds ["
            + " ".join(f"{a:.3f}" for a in self.auc_folds)
            + f"]  spread {self.auc_spread:.3f}",
            f"overfit gap (train - oof) {self.overfit_gap:+.3f}",
            f"Brier {self.brier_raw:.4f} -> {self.brier_calibrated:.4f} after calibration",
            f"calibration error {self.calibration_error_raw:.3f} -> "
            f"{self.calibration_error_calibrated:.3f}",
        ]
        if self.shuffle_auc is not None:
            L.append(f"shuffle test AUC {self.shuffle_auc:.3f} (0.50 = clean)")
        for w in self.warnings:
            L.append(f"  WARNING: {w}")
        return "\n".join(L)


def evaluate(ds: Dataset, fit: FitResult, calibrated: np.ndarray,
             cfg: Agent5Config, shuffle_auc: Optional[float] = None) -> Evaluation:
    from sklearn.metrics import roc_auc_score

    y = ds.y.to_numpy(float)
    w = ds.weight.to_numpy(float)
    ok = ~np.isnan(fit.oof)
    auc = (float(roc_auc_score(y[ok], fit.oof[ok], sample_weight=w[ok]))
           if len(np.unique(y[ok])) > 1 else float("nan"))

    ev = Evaluation(
        auc=auc,
        auc_folds=list(fit.fold_auc),
        auc_spread=fit.auc_spread,
        overfit_gap=fit.overfit_gap,
        brier_raw=brier_score(fit.oof, y),
        brier_calibrated=brier_score(calibrated, y),
        calibration_error_raw=calibration_error(fit.oof, y),
        calibration_error_calibrated=calibration_error(calibrated, y),
        n_samples=len(ds),
        effective_n=float(ds.weight.sum()),
        win_rate=float(ds.y.mean()),
        reliability=reliability_table(calibrated, y),
        shuffle_auc=shuffle_auc,
    )

    # the checks the plan says to make before trusting anything
    if ev.overfit_gap > 0.15:
        ev.warnings.append(
            f"train AUC exceeds out-of-fold by {ev.overfit_gap:.2f} - "
            f"raise min_data_in_leaf and lower num_leaves until the gap closes")
    if ev.auc_spread > 0.05:
        ev.warnings.append(
            f"fold AUCs vary by {ev.auc_spread:.3f} - a good mean over unstable "
            f"folds is noise, not signal")
    if ev.effective_n < 500:
        ev.warnings.append(
            f"only {ev.effective_n:.0f} independent observations after uniqueness "
            f"weighting - too few for {len(fit.feature_names)} features")
    if shuffle_auc is not None and shuffle_auc > 0.55:
        ev.warnings.append(
            f"shuffle test scored {shuffle_auc:.3f} instead of ~0.50 - "
            f"there is leakage in the pipeline, not signal in the features")
    if not np.isnan(ev.auc) and ev.auc < 0.52:
        ev.warnings.append(
            f"AUC {ev.auc:.3f} is at chance - this feature set predicts nothing")
    return ev


def shuffle_test(ds: Dataset, cfg: Agent5Config,
                 columns: Optional[List[str]] = None, seed: int = 0) -> float:
    """Refit with the labels shuffled. An honest pipeline returns ~0.50.

    Anything meaningfully above chance means information is reaching the
    model through a path other than the features - overlapping labels, a
    normalisation fitted on the whole column, a lookahead in a detector.
    Re-run after adding any new data source.
    """
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    shuffled = ds.y.copy()
    shuffled.iloc[:] = rng.permutation(shuffled.to_numpy())

    probe = Dataset(X=ds.X, y=shuffled, weight=ds.weight, t1=ds.t1,
                    positions=ds.positions, blocks=ds.blocks, index=ds.index)
    fit = fit_cv(probe, cfg, columns)
    ok = ~np.isnan(fit.oof)
    yv = shuffled.to_numpy(float)
    if ok.sum() == 0 or len(np.unique(yv[ok])) < 2:
        return float("nan")
    return float(roc_auc_score(yv[ok], fit.oof[ok],
                               sample_weight=ds.weight.to_numpy(float)[ok]))
