"""Tell the user which features are actually usable.

The failure this prevents: Agent 4 emits 22 columns, but if you run it
without the optional feeds (aggTrades, liquidations, netflow) then 16 of them
are entirely NaN. Nothing errors. You can hand all 22 to Agent 5, train on
them, and never notice that three quarters of the block was empty.

A dead column is not neutral either - it burns a feature slot, dilutes any
importance ranking, and makes an ablation result meaningless. So check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd


@dataclass
class FeatureHealth:
    name: str
    n_rows: int
    n_cols: int
    dead: List[str] = field(default_factory=list)        # all NaN after warmup
    constant: List[str] = field(default_factory=list)    # one value, no signal
    sparse: Dict[str, float] = field(default_factory=dict)   # >50% NaN
    extreme: Dict[str, float] = field(default_factory=dict)  # |value| > 1e4

    @property
    def usable_cols(self) -> int:
        return self.n_cols - len(set(self.dead) | set(self.constant))

    def __str__(self) -> str:
        lines = [f"{self.name}: {self.usable_cols}/{self.n_cols} columns usable, "
                 f"{self.n_rows:,} rows"]
        if self.dead:
            lines.append(f"  DEAD (all NaN - do not feed these to Agent 5): "
                         f"{', '.join(self.dead)}")
        if self.constant:
            lines.append(f"  CONSTANT (no variation, no signal): {', '.join(self.constant)}")
        if self.sparse:
            lines.append("  sparse: " + ", ".join(f"{c} {p:.0%} NaN"
                                                  for c, p in self.sparse.items()))
        if self.extreme:
            lines.append("  large magnitudes (check the scaling): "
                         + ", ".join(f"{c} max {v:.1e}" for c, v in self.extreme.items()))
        if not (self.dead or self.constant or self.sparse or self.extreme):
            lines.append("  all columns healthy")
        return "\n".join(lines)


def feature_report(features: pd.DataFrame, warmup: int = 0,
                   name: str = "features") -> FeatureHealth:
    """Diagnose a feature frame. Cheap - run it every time you build one."""
    # rows before the warmup are expected to be NaN, so they are not evidence
    # of anything and would make every column look broken
    tail = features.iloc[warmup:] if warmup < len(features) else features.iloc[0:0]
    h = FeatureHealth(name=name, n_rows=len(tail), n_cols=features.shape[1])
    if len(tail) == 0:
        h.dead = list(features.columns)
        return h

    for c in features.columns:
        col = tail[c]
        nan_share = float(col.isna().mean())
        if nan_share == 1.0:
            h.dead.append(c)
            continue
        # nunique ignores NaN, so 1 means the column never varies
        if col.nunique(dropna=True) <= 1:
            h.constant.append(c)
            continue
        if nan_share > 0.5:
            h.sparse[c] = nan_share
        biggest = float(col.abs().max())
        if biggest > 1e4:
            h.extreme[c] = biggest
    return h
