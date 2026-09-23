"""Stage 2: the fitted model, and the out-of-fold predictions it produces.

"Fitting" is mechanical: there are unknown numbers and rows where the answer
is already known; find the numbers that make the output match those answers.

TWO MODELS, ON PURPOSE
----------------------
LOGISTIC is fitted first even though it is usually weaker. Its coefficients
ARE the N/W/P/I weights from the original formula - reading them tells you
which blocks contribute anything - and with this signal-to-noise it is
frequently not beaten. Heavy L2 is not optional: the features are strongly
correlated, and without shrinkage you get +8.3 on one column and -8.1 on a
near-duplicate, cancelling, meaningless individually, unstable across folds.

LIGHTGBM finds interactions the logistic cannot. A tree that splits on
vol_pctile and then on an agent-1 column has discovered "patterns matter
differently in high volatility" - the m() term from the original formula,
learned instead of assumed. Its defaults assume real signal; ours mostly is
not, so min_data_in_leaf is raised by more than an order of magnitude.

THE OUTPUT THAT MATTERS IS `oof`
--------------------------------
Every row gets a prediction from a model that never saw it during training.
That vector is what metrics are computed on and what calibration is fitted
to. In-sample predictions look wonderful and mean nothing.

EARLY STOPPING NEEDS ITS OWN SPLIT
----------------------------------
If the set watched for early stopping is the test fold, the number of trees
was chosen using test data and the test score is contaminated. Each training
fold is therefore split again - train / inner-validation - with a purge gap
between them so the validation labels do not overlap the inner training
labels either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import Agent5Config
from .dataset import Dataset
from .splits import PurgedKFold


def lightgbm_available() -> bool:
    try:
        import lightgbm  # noqa: F401
        return True
    except Exception:
        return False


def resolve_model(cfg: Agent5Config) -> str:
    if cfg.model == "auto":
        return "lightgbm" if lightgbm_available() else "logistic"
    if cfg.model == "lightgbm" and not lightgbm_available():
        raise RuntimeError(
            "model='lightgbm' but the package will not import. "
            "On macOS this is usually libomp: `brew install libomp`. "
            "Use model='logistic' to proceed without it."
        )
    return cfg.model


@dataclass
class FitResult:
    """One complete cross-validated fit."""

    oof: np.ndarray                      # out-of-fold probability per sample
    fold_auc: List[float]                # per-fold AUC - read these, not the mean
    train_auc: List[float]               # in-sample, to measure the overfit gap
    models: List[object] = field(default_factory=list)
    kind: str = ""
    feature_names: List[str] = field(default_factory=list)
    coefficients: Optional[pd.Series] = None    # logistic only: the N/W/P/I weights
    n_trees: List[int] = field(default_factory=list)

    @property
    def mean_auc(self) -> float:
        return float(np.mean(self.fold_auc)) if self.fold_auc else float("nan")

    @property
    def auc_spread(self) -> float:
        """Std across folds. 0.61/0.49/0.58/0.51/0.57 averages fine and is noise."""
        return float(np.std(self.fold_auc)) if self.fold_auc else float("nan")

    @property
    def overfit_gap(self) -> float:
        if not self.train_auc:
            return float("nan")
        return float(np.mean(self.train_auc) - np.mean(self.fold_auc))


def _inner_split(n_train: int, positions: np.ndarray, t1: np.ndarray,
                 frac: float) -> Tuple[np.ndarray, np.ndarray]:
    """Split a training fold into (inner-train, inner-val) with a purge gap."""
    if n_train < 20:
        return np.arange(n_train), np.arange(0)
    cut = int(n_train * (1 - frac))
    val = np.arange(cut, n_train)
    if len(val) == 0:
        return np.arange(n_train), np.arange(0)
    # purge: drop inner-train samples whose labels run into the validation block
    val_start = positions[cut]
    inner = np.arange(cut)
    inner = inner[t1[inner] < val_start]
    if len(inner) < 10:
        return np.arange(n_train), np.arange(0)
    return inner, val


def _fit_logistic(Xtr, ytr, wtr, cfg):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    # logistic cannot take NaN. imputing with the MEDIAN of the training fold
    # only - never the full column - or the fill value itself leaks test data.
    # this is a real information loss: "no order block exists" becomes "an
    # order block at the median distance". LightGBM does not pay this cost,
    # which is one reason it is the default when available.
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(C=cfg.logistic_c, max_iter=cfg.max_iter,
                                   class_weight="balanced")),
    ]).fit(Xtr, ytr, clf__sample_weight=wtr)


class SeedBag:
    """Several fits of one model, differing only in their seed, averaged.

    Quacks like the classifier it wraps (`predict_proba`, `n_features_in_`)
    so a JudgeAgent holding one scores, saves and loads exactly as before.
    Averaging probabilities, not votes: the monitor RANKS these scores, and
    a mean keeps them continuous where a vote would tie."""

    def __init__(self, members: list):
        if not members:
            raise ValueError("a bag needs at least one member")
        self.members = list(members)

    def predict_proba(self, X) -> np.ndarray:
        return np.mean([m.predict_proba(X) for m in self.members], axis=0)

    @property
    def n_features_in_(self) -> int:
        return getattr(self.members[0], "n_features_in_", 0)

    def __len__(self) -> int:
        return len(self.members)


def _fit_lightgbm(Xtr, ytr, wtr, Xval, yval, wval, cfg):
    import lightgbm as lgb

    params = dict(cfg.lgbm_params)
    model = lgb.LGBMClassifier(**params)
    if len(Xval):
        model.fit(
            Xtr, ytr, sample_weight=wtr,
            eval_set=[(Xval, yval)], eval_sample_weight=[wval],
            eval_metric="auc",
            callbacks=[lgb.early_stopping(cfg.early_stopping_rounds, verbose=False),
                       lgb.log_evaluation(0)],
        )
    else:
        model.fit(Xtr, ytr, sample_weight=wtr)
    return model


def fit_cv(
    ds: Dataset,
    cfg: Agent5Config,
    columns: Optional[List[str]] = None,
) -> FitResult:
    """Purged cross-validated fit. Returns out-of-fold predictions."""
    from sklearn.metrics import roc_auc_score

    cols = list(columns) if columns else list(ds.X.columns)
    if not cols:
        raise ValueError("no feature columns selected")

    # keep X as a DataFrame throughout. LightGBM stamps feature names at fit
    # time; predicting with a bare array then makes sklearn warn that the
    # names are missing. passing frames on both sides keeps them consistent
    # and lets LightGBM report real column names in its own diagnostics
    X = ds.X[cols].astype(float)
    y = ds.y.to_numpy(float)
    w = ds.weight.to_numpy(float)
    n = len(y)

    kind = resolve_model(cfg)
    oof = np.full(n, np.nan)
    fold_auc: List[float] = []
    train_auc: List[float] = []
    models: List[object] = []
    n_trees: List[int] = []

    cv = PurgedKFold(cfg.n_splits, ds.t1, cfg.embargo_pct)
    for tr, te in cv.split(ds.positions):
        if len(tr) < 20 or len(np.unique(y[tr])) < 2:
            continue                            # fold unusable, skip honestly

        if kind == "lightgbm":
            inner, val = _inner_split(len(tr), ds.positions[tr], ds.t1[tr],
                                      cfg.inner_val_frac)
            itr = tr[inner]
            ival = tr[val] if len(val) and len(np.unique(y[tr[val]])) > 1 else tr[:0]
            model = _fit_lightgbm(X.iloc[itr], y[itr], w[itr],
                                  X.iloc[ival], y[ival], w[ival], cfg)
            n_trees.append(int(getattr(model, "best_iteration_", 0)
                               or model.n_estimators_))
        else:
            model = _fit_logistic(X.iloc[tr], y[tr], w[tr], cfg)

        oof[te] = model.predict_proba(X.iloc[te])[:, 1]
        models.append(model)

        if len(np.unique(y[te])) > 1:
            fold_auc.append(float(roc_auc_score(y[te], oof[te], sample_weight=w[te])))
        p_tr = model.predict_proba(X.iloc[tr])[:, 1]
        if len(np.unique(y[tr])) > 1:
            train_auc.append(float(roc_auc_score(y[tr], p_tr, sample_weight=w[tr])))

    coefficients = None
    if kind == "logistic" and models:
        # average the standardised coefficients across folds. these ARE the
        # N/W/P/I weights: not computed from anything, fitted from history
        coefs = np.mean([m.named_steps["clf"].coef_[0] for m in models], axis=0)
        coefficients = pd.Series(coefs, index=cols).sort_values(key=np.abs,
                                                               ascending=False)

    return FitResult(oof=oof, fold_auc=fold_auc, train_auc=train_auc,
                     models=models, kind=kind, feature_names=cols,
                     coefficients=coefficients, n_trees=n_trees)


def fit_final(ds: Dataset, cfg: Agent5Config,
              columns: Optional[List[str]] = None) -> Tuple[object, List[str]]:
    """Refit on ALL samples - the artifact that goes live.

    Cross-validation measures; this produces. It is never scored, because it
    has seen everything: any number computed from it would be in-sample.
    """
    cols = list(columns) if columns else list(ds.X.columns)
    X = ds.X[cols].astype(float)
    y = ds.y.to_numpy(float)
    w = ds.weight.to_numpy(float)
    kind = resolve_model(cfg)

    if kind == "lightgbm":
        inner, val = _inner_split(len(y), ds.positions, ds.t1, cfg.inner_val_frac)
        ival = val if len(val) and len(np.unique(y[val])) > 1 else np.arange(0)
        seeds = tuple(getattr(cfg, "bag_seeds", ()) or ())
        if seeds:
            import dataclasses
            members = []
            for sd in seeds:
                prm = dict(cfg.lgbm_params); prm["seed"] = int(sd)
                members.append(_fit_lightgbm(X.iloc[inner], y[inner], w[inner],
                                             X.iloc[ival], y[ival], w[ival],
                                             dataclasses.replace(cfg, lgbm_params=prm)))
            model = SeedBag(members)
        else:
            model = _fit_lightgbm(X.iloc[inner], y[inner], w[inner],
                                  X.iloc[ival], y[ival], w[ival], cfg)
    else:
        model = _fit_logistic(X, y, w, cfg)
    return model, cols


def grouped_importance(fit: FitResult, ds: Dataset, cfg: Agent5Config,
                       n_repeats: int = 5, seed: int = 0) -> pd.Series:
    """Permutation importance summed per block - the operational N/W/P/I.

    You do not compute those weights, you measure what breaks when a block is
    shuffled. Permutation rather than a tree's built-in importance because the
    built-in one is biased toward high-cardinality columns.
    """
    from sklearn.metrics import roc_auc_score

    if not fit.models:
        return pd.Series(dtype=float)
    rng = np.random.default_rng(seed)
    X = ds.X[fit.feature_names].astype(float)
    y = ds.y.to_numpy(float)
    w = ds.weight.to_numpy(float)
    ok = ~np.isnan(fit.oof)
    if len(np.unique(y[ok])) < 2:
        return pd.Series(dtype=float)
    base = roc_auc_score(y[ok], fit.oof[ok], sample_weight=w[ok])

    name_to_block = {c: b for b, cols in ds.blocks.items() for c in cols}
    out: Dict[str, float] = {}
    for block, cols in ds.blocks.items():
        idx = [fit.feature_names.index(c) for c in cols if c in fit.feature_names]
        if not idx:
            continue
        drops = []
        values = X.to_numpy(float)
        for _ in range(n_repeats):
            arr = values.copy()
            perm = rng.permutation(len(arr))
            arr[:, idx] = arr[perm][:, idx]    # break this block, keep the rest
            Xp = pd.DataFrame(arr, index=X.index, columns=X.columns)
            preds = np.full(len(y), np.nan)
            cv = PurgedKFold(cfg.n_splits, ds.t1, cfg.embargo_pct)
            for (tr, te), model in zip(cv.split(ds.positions), fit.models):
                preds[te] = model.predict_proba(Xp.iloc[te])[:, 1]
            m = ~np.isnan(preds)
            drops.append(base - roc_auc_score(y[m], preds[m], sample_weight=w[m]))
        out[block] = float(np.mean(drops))
    return pd.Series(out).sort_values(ascending=False)
