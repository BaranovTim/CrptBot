"""The ablation: which blocks actually earn their place?

This is the operational answer to the original N / W / P / I question. You
do not compute those weights - you measure what breaks when a block is
removed. The plan's order runs from cheapest to most expensive:

    1. regime only     the floor. what do volatility and time-of-day predict
                       on their own? usually higher than people expect
    2. + agent 2       do indicators beat the floor?
    3. + agent 1       do patterns add on top?
    4. + agent 4       does order flow add?
    5. + agent 3       does news add?

Each step is a full run through the harness, and the shuffle test is re-run
at every step because leakage arrives with new data sources, not new models.

A block that does not move out-of-fold AUC has not earned its complexity,
its runtime, or - for Agents 3 and 4 - its data bill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .calibration import fit_calibrator
from .config import ABLATION_ORDER, Agent5Config
from .dataset import Dataset
from .metrics import evaluate, shuffle_test
from .model import fit_cv


@dataclass
class AblationStep:
    blocks: List[str]
    added: str
    n_features: int
    auc: float
    auc_spread: float
    delta: float                # change in AUC from adding this block
    shuffle_auc: float
    effective_n: float
    warnings: List[str]


def run_ablation(ds: Dataset, cfg: Agent5Config,
                 order: Optional[List[str]] = None,
                 with_shuffle: bool = True) -> pd.DataFrame:
    """Add blocks one at a time; report what each one bought."""
    order = list(order) if order else [b for b in ABLATION_ORDER
                                       if ds.blocks.get(b)]
    steps: List[AblationStep] = []
    used: List[str] = []
    prev_auc = np.nan

    for block in order:
        cols = ds.columns_for(used + [block])
        if not cols:
            continue
        used.append(block)

        fit = fit_cv(ds, cfg, cols)
        cal = fit_calibrator(fit.oof, ds.y.to_numpy(float),
                             ds.weight.to_numpy(float))
        sh = shuffle_test(ds, cfg, cols) if with_shuffle else float("nan")
        ev = evaluate(ds, fit, cal.transform(fit.oof), cfg, shuffle_auc=sh)

        delta = ev.auc - prev_auc if not np.isnan(prev_auc) else np.nan
        steps.append(AblationStep(
            blocks=list(used), added=block, n_features=len(cols),
            auc=ev.auc, auc_spread=ev.auc_spread, delta=delta,
            shuffle_auc=sh, effective_n=ev.effective_n, warnings=ev.warnings))
        prev_auc = ev.auc

    return pd.DataFrame([{
        "added": s.added,
        "blocks": "+".join(s.blocks),
        "features": s.n_features,
        "auc": round(s.auc, 4),
        "spread": round(s.auc_spread, 4),
        "delta_auc": round(s.delta, 4) if not np.isnan(s.delta) else np.nan,
        "shuffle": round(s.shuffle_auc, 4) if not np.isnan(s.shuffle_auc) else np.nan,
        "warnings": len(s.warnings),
    } for s in steps])


def format_ablation(table: pd.DataFrame) -> str:
    if table.empty:
        return "(no blocks to ablate)"
    lines = ["ablation - does each block earn its place?", ""]
    lines.append(f"{'added':<9}{'feats':>6}{'AUC':>8}{'spread':>8}"
                 f"{'delta':>8}{'shuffle':>9}")
    for _, r in table.iterrows():
        d = "" if pd.isna(r["delta_auc"]) else f"{r['delta_auc']:+.4f}"
        s = "" if pd.isna(r["shuffle"]) else f"{r['shuffle']:.3f}"
        lines.append(f"{r['added']:<9}{r['features']:>6}{r['auc']:>8.4f}"
                     f"{r['spread']:>8.4f}{d:>8}{s:>9}")
    lines.append("")
    lines.append("read the delta column: a block that does not move AUC has not")
    lines.append("earned its complexity, runtime, or data cost.")
    return "\n".join(lines)
