"""Agent 5 - the judge. The only block in the project that learns.

    from agent5 import JudgeAgent

    judge = JudgeAgent()
    ds = judge.build(bars, warmup=400, agent1=f1, agent2=f2, agent4=f4)
    print(judge.fit(ds, with_ablation=True))
    print(judge.latest(bars, ds.X))
"""
from .ablation import format_ablation, run_ablation
from .agent import JudgeAgent, TrainingReport
from .calibration import (
    Calibrator,
    brier_score,
    calibration_error,
    fit_calibrator,
    reliability_table,
)
from .config import ABLATION_ORDER, DEFAULT_CONFIG, Agent5Config
from .dataset import Dataset, build_dataset
from .decision import Decision, decide, expected_value_pct, kelly_full, simulate
from .labels import LabelResult, triple_barrier
from .metrics import Evaluation, evaluate, shuffle_test
from .model import FitResult, fit_cv, fit_final, grouped_importance, lightgbm_available
from .regime import REGIME_COLUMNS, compute_regime
from .splits import PurgedKFold

__all__ = [
    "JudgeAgent", "TrainingReport", "Agent5Config", "DEFAULT_CONFIG",
    "ABLATION_ORDER", "Dataset", "build_dataset", "triple_barrier",
    "LabelResult", "PurgedKFold", "fit_cv", "fit_final", "FitResult",
    "grouped_importance", "lightgbm_available", "Calibrator", "fit_calibrator",
    "reliability_table", "brier_score", "calibration_error", "evaluate",
    "Evaluation", "shuffle_test", "decide", "Decision", "expected_value_pct",
    "kelly_full", "simulate", "compute_regime", "REGIME_COLUMNS",
    "run_ablation", "format_ablation",
]
