"""Agent 5 - the judge. The only block that learns.

Agents 1-4 measure. This one decides. A thermometer reads 38.5 and does not
know whether that is bad; the doctor looks at 38.5 alongside everything else
and concludes. Agent 5 is the doctor, and there is exactly one of it.

WHAT HAPPENS ON EVERY BAR, IN ORDER
    1. bar closes
    2. the detectors assemble their ~100 features
    3. features -> model -> p_raw
    4. p_raw -> isotonic map -> p_calibrated      <- this is the "61%"
    5. p_calibrated + barriers + costs -> EV
    6. EV over threshold -> Kelly size -> risk limits -> order
    7. log features, probability, decision, timestamp
    8. when a barrier hits, record the outcome; that row becomes training data

Steps 2-7 involve NO LEARNING. The coefficients are frozen and inference is
arithmetic on a fixed function, in microseconds. Training happens offline,
on a schedule, from the accumulated log - with a champion/challenger gate
deciding whether new coefficients actually replace the ones now trading.

The plan is explicit that a model updating its weights on every tick is a
model chasing noise. Predicting is real-time; training is batch. That is why
`fit()` and `predict()` are separate methods and why `predict()` refuses to
run until a model has been fitted and frozen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .ablation import format_ablation, run_ablation
from .calibration import Calibrator, fit_calibrator
from .config import DEFAULT_CONFIG, Agent5Config
from .dataset import Dataset, build_dataset
from .decision import decide, simulate
from .labels import triple_barrier
from .metrics import Evaluation, evaluate, shuffle_test
from .model import FitResult, fit_cv, fit_final, grouped_importance, resolve_model


@dataclass
class TrainingReport:
    """Everything a fit produced. Read this before trusting the model."""

    evaluation: Evaluation
    fit: FitResult
    dataset_summary: str
    block_importance: pd.Series = field(default_factory=pd.Series)
    coefficients: Optional[pd.Series] = None
    ablation: pd.DataFrame = field(default_factory=pd.DataFrame)

    def __str__(self) -> str:
        L = ["=" * 66, "AGENT 5 TRAINING REPORT", "=" * 66,
             self.dataset_summary, "",
             f"model: {self.fit.kind}  ({len(self.fit.feature_names)} features)", "",
             str(self.evaluation), ""]
        if len(self.block_importance):
            L.append("block importance (AUC lost when the block is shuffled):")
            L.append("this is the operational N / W / P / I - measured, not computed")
            for b, v in self.block_importance.items():
                L.append(f"  {b:<9} {v:+.4f}")
            L.append("")
        if self.coefficients is not None and len(self.coefficients):
            L.append("top logistic coefficients (standardised):")
            for name, v in self.coefficients.head(10).items():
                L.append(f"  {name:<30} {v:+.3f}")
            L.append("")
        if not self.ablation.empty:
            L.append(format_ablation(self.ablation))
            L.append("")
        L.append("=" * 66)
        return "\n".join(L)


class JudgeAgent:
    """Fit once, freeze, then predict. Never both at the same time."""

    def __init__(self, config: Agent5Config = DEFAULT_CONFIG):
        self.cfg = config
        self.model = None
        self.columns: List[str] = []
        self.calibrator = Calibrator()
        self.report: Optional[TrainingReport] = None
        self._fitted = False

    # ---------------------------------------------------------------- fit
    def build(self, bars: pd.DataFrame, warmup: int = 0, **blocks) -> Dataset:
        """Assemble the training table from bars plus any detector frames."""
        return build_dataset(bars, self.cfg, warmup=warmup, **blocks)

    def fit(self, dataset: Dataset, columns: Optional[List[str]] = None,
            with_shuffle: bool = True, with_ablation: bool = False,
            with_importance: bool = True) -> TrainingReport:
        """Cross-validate, calibrate, evaluate, then freeze a final model."""
        cols = list(columns) if columns else list(dataset.X.columns)

        # 1. purged CV -> out-of-fold predictions. the only honest scores
        fit = fit_cv(dataset, self.cfg, cols)

        # 2. calibration, fitted on OOF only
        y = dataset.y.to_numpy(float)
        w = dataset.weight.to_numpy(float)
        self.calibrator = fit_calibrator(fit.oof, y, w)

        # 3. the checks that stop the scoreboard lying
        sh = shuffle_test(dataset, self.cfg, cols) if with_shuffle else None
        ev = evaluate(dataset, fit, self.calibrator.transform(fit.oof),
                      self.cfg, shuffle_auc=sh)

        imp = (grouped_importance(fit, dataset, self.cfg)
               if with_importance else pd.Series(dtype=float))
        abl = (run_ablation(dataset, self.cfg, with_shuffle=False)
               if with_ablation else pd.DataFrame())

        # 4. the artifact that goes live: refit on everything, then freeze.
        #    never scored - it has seen every row
        self.model, self.columns = fit_final(dataset, self.cfg, cols)
        self._fitted = True

        self.report = TrainingReport(
            evaluation=ev, fit=fit, dataset_summary=dataset.summary(),
            block_importance=imp, coefficients=fit.coefficients, ablation=abl)
        return self.report

    # ------------------------------------------------------------ predict
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Calibrated probability per row. Frozen arithmetic, no learning."""
        if not self._fitted:
            raise RuntimeError(
                "JudgeAgent is not fitted. call fit() first - predict() must "
                "never trigger training, because a model that learns on every "
                "bar is a model chasing noise")
        missing = [c for c in self.columns if c not in X.columns]
        if missing:
            raise ValueError(
                f"missing {len(missing)} feature columns the model was fitted "
                f"on: {missing[:6]}{'...' if len(missing) > 6 else ''}. "
                f"the column set and its ORDER must match exactly, or values "
                f"land on the wrong features and nothing raises")
        # a DataFrame, not an array - see the note in model.fit_cv about
        # LightGBM feature names
        raw = self.model.predict_proba(X[self.columns].astype(float))[:, 1]
        return self.calibrator.transform(raw)

    def decide(self, bars: pd.DataFrame, X: pd.DataFrame) -> pd.DataFrame:
        """Full pipeline: features -> probability -> EV -> size."""
        lab = triple_barrier(bars, self.cfg)
        p = self.predict_proba(X)
        idx = X.index
        return decide(p, lab.tp_pct.reindex(idx).to_numpy(),
                      lab.sl_pct.reindex(idx).to_numpy(), self.cfg, timestamps=idx)

    def latest(self, bars: pd.DataFrame, X: pd.DataFrame) -> str:
        """Human-readable read of the final bar."""
        d = self.decide(bars, X).iloc[-1]
        head = f"[{X.index[-1]}] Agent 5"
        lines = [head, "-" * len(head),
                 f"  calibrated probability {d['probability']:.1%}",
                 f"  barriers  TP +{d['tp_pct']:.2f}%  SL -{d['sl_pct']:.2f}%",
                 f"  EV after costs {d['ev_pct']:+.3f}%  "
                 f"(threshold {self.cfg.ev_threshold_pct:+.2f}%)",
                 f"  full Kelly {d['kelly_full']:.3f} -> "
                 f"{self.cfg.kelly_fraction:.2f} Kelly = "
                 f"{d['position_pct']:.2f}% of equity",
                 f"  DECISION: {str(d['action']).upper()} - {d['reason']}"]
        if self.report and self.report.evaluation.warnings:
            lines.append("  (model warnings are open - see the training report)")
        return "\n".join(lines)

    # --------------------------------------------------------------- io
    def save(self, path) -> None:
        """Freeze to disk: model, calibrator, column order, config.

        THROUGH A TEMP FILE AND A RENAME, like every other write in this
        project. `joblib.dump` straight to the destination has a window —
        small, because these files are tens of kilobytes — where a crash, a
        shutdown or a killed training run leaves a TRUNCATED file sitting at
        the real name.

        That failure is silent and confusing: `is_trained()` only asks whether
        the path exists, so the app would offer the timeframe, the server
        would try to load it, and the error would surface as a broken
        dashboard rather than as a missing model. A rename is atomic on POSIX,
        so the file is either the previous one or the complete new one, never
        half of either.
        """
        import joblib

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            joblib.dump({
                "model": self.model,
                "calibrator": self.calibrator,
                "columns": self.columns,
                "config": self.cfg,
            }, tmp)
            tmp.replace(path)
        except BaseException:
            # BaseException, not Exception: the case this exists for includes
            # KeyboardInterrupt and SystemExit, which is precisely how a
            # training run gets stopped by hand.
            tmp.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path) -> "JudgeAgent":
        import joblib

        blob = joblib.load(Path(path))
        agent = cls(blob["config"])
        agent.model = blob["model"]
        agent.calibrator = blob["calibrator"]
        agent.columns = blob["columns"]
        agent._fitted = True
        return agent
