"""Assemble one training table from the detector blocks.

This is the 95% of the work the plan warns about: "training is the last 5%,
the rest is building the dataset, and that is where it breaks".

Every row is one moment in time carrying:
    X        ~100 feature columns from Agents 1-4 plus the regime block
    y        did this trade win (triple barrier)
    weight   uniqueness, so overlapping labels do not inflate the sample count
    t1       when the label resolved, for purging
    block    which agent each column came from, for the ablation

Rows are dropped for exactly three reasons, and each is recorded:
    warmup      rolling windows had not filled yet
    unlabelled  the label window ran past the end of the data
    all-NaN     no block produced a single usable number

Nothing is imputed. NaN reaches the model intact, because in this project
NaN means "this thing does not exist right now" (no live order block, no
news, no tape) and that is information. LightGBM handles it natively; the
logistic path has to fill, and does so explicitly and visibly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import Agent5Config
from .labels import LabelResult, triple_barrier
from .regime import REGIME_COLUMNS, compute_regime


@dataclass
class Dataset:
    X: pd.DataFrame                 # features, NaN preserved
    y: pd.Series                    # 1.0 win / 0.0 loss
    weight: pd.Series               # uniqueness weights
    t1: np.ndarray                  # label resolution, as bar positions
    positions: np.ndarray           # bar position of each sample
    blocks: Dict[str, List[str]]    # block name -> its columns
    dropped: Dict[str, int] = field(default_factory=dict)
    index: pd.DatetimeIndex = None  # close_time of each sample

    def __len__(self) -> int:
        return len(self.X)

    def columns_for(self, blocks) -> List[str]:
        """Column list for a subset of blocks - the ablation's selector."""
        out: List[str] = []
        for b in blocks:
            out.extend(self.blocks.get(b, []))
        return [c for c in self.X.columns if c in set(out)]

    def summary(self) -> str:
        lines = [
            f"{len(self):,} samples x {self.X.shape[1]} features",
            f"  win rate       {self.y.mean():.1%}",
            f"  effective size {self.weight.sum():.0f} independent observations",
        ]
        for b, cols in self.blocks.items():
            if cols:
                filled = self.X[cols].notna().mean().mean()
                lines.append(f"  {b:<8} {len(cols):>3} cols  {filled:5.1%} populated")
        if self.dropped:
            lines.append("  dropped: " + ", ".join(f"{k}={v:,}"
                                                   for k, v in self.dropped.items()))
        return "\n".join(lines)


def build_dataset(
    bars: pd.DataFrame,
    cfg: Agent5Config,
    agent1: Optional[pd.DataFrame] = None,
    agent2: Optional[pd.DataFrame] = None,
    agent3: Optional[pd.DataFrame] = None,
    agent4: Optional[pd.DataFrame] = None,
    funding: Optional[pd.Series] = None,
    warmup: int = 0,
    labels: Optional[LabelResult] = None,
) -> Dataset:
    """Join detector outputs to labels. Every block is optional."""
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise TypeError("bars must be indexed by close_time as a DatetimeIndex")

    blocks: Dict[str, List[str]] = {}
    frames: List[pd.DataFrame] = []

    # regime always exists - it is the ablation floor every block must beat
    regime = compute_regime(bars, cfg, funding=funding)
    blocks["regime"] = list(REGIME_COLUMNS)
    frames.append(regime)

    for name, frame in (("agent1", agent1), ("agent2", agent2),
                        ("agent3", agent3), ("agent4", agent4)):
        if frame is None or frame.empty:
            blocks[name] = []
            continue
        aligned = frame.reindex(bars.index)
        # a column name appearing twice would silently overwrite - the four
        # agents were built with disjoint names, but a future block might not be
        clash = [c for c in aligned.columns
                 if any(c in cols for cols in blocks.values())]
        if clash:
            raise ValueError(f"{name} column names collide with an earlier block: {clash}")
        blocks[name] = list(aligned.columns)
        frames.append(aligned)

    X = pd.concat(frames, axis=1)

    lab = labels if labels is not None else triple_barrier(bars, cfg)

    n_before = len(X)
    keep = pd.Series(True, index=bars.index)
    dropped: Dict[str, int] = {}

    # 1. warmup: rolling windows unfilled. these rows are not wrong, they are
    #    unformed, and training on them teaches the model about its own warmup
    if warmup > 0:
        w = min(warmup, len(X) - 1)
        keep.iloc[:w] = False
        dropped["warmup"] = int(w)

    # 2. unlabelled: the label window ran past the end of the data
    unlabelled = lab.y.isna()
    dropped["unlabelled"] = int((unlabelled & keep).sum())
    keep &= ~unlabelled

    # 3. rows where no block produced anything at all
    empty = X.isna().all(axis=1)
    dropped["all_nan"] = int((empty & keep).sum())
    keep &= ~empty

    # 4. thin the sample index if requested (event sampling is a later upgrade)
    if cfg.sample_every > 1:
        stride = pd.Series(False, index=bars.index)
        stride.iloc[::cfg.sample_every] = True
        dropped["stride"] = int((keep & ~stride).sum())
        keep &= stride

    pos_all = np.arange(len(bars), dtype=float)
    sel = keep.to_numpy()

    ds = Dataset(
        X=X.loc[keep],
        y=lab.y.loc[keep].astype(float),
        weight=lab.weight.loc[keep].astype(float),
        t1=lab.t1.to_numpy(float)[sel],
        positions=pos_all[sel],
        blocks=blocks,
        dropped={k: v for k, v in dropped.items() if v},
        index=bars.index[sel],
    )
    if len(ds) == 0:
        raise ValueError(
            f"no usable samples from {n_before} bars. "
            f"dropped: {dropped}. more history, or a shorter warmup/hold."
        )
    return ds
