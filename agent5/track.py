"""The forward record: what the model said, before the outcome existed.

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR
    It does not improve the model. It measures whether the model's
    cross-validated AUC survives contact with time.

    Purged K-fold with an embargo is the strongest offline test this project
    can run, and it can still flatter a model: the feature set was chosen
    while looking at the whole history, the barriers were tuned on it, and
    every fold's "future" was already in the file when the code was written.
    None of that is recoverable by being careful. The only test that cannot
    be contaminated is one where the prediction is written down before the
    outcome exists.

THE TWO RULES THAT MAKE IT WORTH ANYTHING
    1. APPEND ONLY, AND NEVER REWRITTEN. A record that can be edited after
       the fact measures nothing. Predictions are appended as JSON lines and
       resolution is a separate field filled in later; no row is ever
       rewritten except to attach the outcome that row was waiting for.

    2. RESOLVED BY THE SAME RULE THAT TRAINED IT. The outcome is computed by
       calling `triple_barrier` — the actual labelling function, with the
       config read off the fitted model — not by a reimplementation here.
       If the forward record used even a slightly different rule, comparing
       it to the backtest would be comparing two different questions, and
       the difference would look like a finding.

WHY THE MODEL FINGERPRINT IS STORED
    Retraining changes the thing being measured. A record that mixes
    predictions from three different models into one hit rate is not a track
    record of anything. Each row carries the fingerprint of the model that
    produced it, so the metrics can be grouped by it and a retrain starts a
    new series rather than quietly polluting the old one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from core import utc_now

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("data_cache") / "track"


@dataclass
class Prediction:
    """One forecast, written before its outcome was knowable."""

    symbol: str
    interval: str
    horizon: str                 # h1 | h2
    bar_close: str               # ISO, the CLOSED bar the call was made on
    logged_at: str               # ISO, when we wrote it down
    model_id: str                # fingerprint of the fitted model
    p_up: float
    action: str
    ev: Optional[float] = None
    close: Optional[float] = None
    atr: Optional[float] = None
    k_up: Optional[float] = None
    k_dn: Optional[float] = None
    max_hold_bars: Optional[int] = None

    # filled in later, by resolve(). Never anything else.
    outcome: Optional[float] = None      # 1.0 up-barrier first, 0.0 otherwise
    touch: Optional[str] = None          # upper | lower | timeout | ambiguous
    resolved_at: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.interval}|{self.horizon}|{self.bar_close}"


def model_fingerprint(path: Path) -> str:
    """Short, stable id for a fitted model file.

    Content-based rather than mtime-based: rsyncing a model to the droplet
    changes its timestamp and not its behaviour, and a fingerprint that moved
    on deployment would split one series in two for no reason.
    """
    try:
        h = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return h[:12]
    except OSError:
        return "unknown"


class TrackRecord:
    """Append-only prediction log for one symbol and interval."""

    def __init__(self, symbol: str, interval: str,
                 directory: Path = DEFAULT_DIR):
        self.symbol = symbol.upper()
        self.interval = interval
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.symbol}_{interval}.jsonl"

    # --------------------------------------------------------------- read
    def load(self) -> List[Prediction]:
        if not self.path.exists():
            return []
        out = []
        for i, line in enumerate(self.path.read_text().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Prediction(**json.loads(line)))
            except (ValueError, TypeError) as e:
                # one malformed line must not destroy the record; a track
                # record that refuses to load is a track record you stop
                # keeping
                log.warning("%s line %d skipped: %s", self.path.name, i, e)
        return out

    def _keys(self) -> set:
        return {p.key for p in self.load()}

    # -------------------------------------------------------------- write
    def record(self, pred: Prediction) -> bool:
        """Append unless this exact bar and horizon is already recorded.

        The dashboard rebuilds many times per bar, and every rebuild produces
        the same forecast for the same closed bar. Logging each one would
        multiply the sample count without adding a single independent
        observation, and every metric downstream would be wrong in the
        flattering direction.
        """
        if pred.key in self._keys():
            return False
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(pred)) + "\n")
        return True

    def _rewrite(self, rows: Iterable[Prediction]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for p in rows:
                f.write(json.dumps(asdict(p)) + "\n")
        os.replace(tmp, self.path)

    # ----------------------------------------------------------- resolve
    def resolve(self, bars: pd.DataFrame, cfg_for: Dict[str, Any]) -> int:
        """Attach outcomes to every prediction whose window has now closed.

        `cfg_for` maps horizon -> Agent5Config, taken from the fitted models,
        so the barrier geometry is exactly the one that produced the label
        the model was trained on.
        """
        from agent5.labels import triple_barrier

        rows = self.load()
        if not rows or bars is None or bars.empty:
            return 0

        # one labelling pass per horizon, not per row
        labels = {}
        for horizon, cfg in cfg_for.items():
            if cfg is None:
                continue
            try:
                labels[horizon] = triple_barrier(bars, cfg)
            except Exception as e:
                log.warning("cannot label %s %s: %s",
                            self.symbol, horizon, e)

        resolved = 0
        for p in rows:
            if p.outcome is not None:
                continue
            lab = labels.get(p.horizon)
            if lab is None:
                continue
            ts = pd.Timestamp(p.bar_close)
            if ts not in lab.y.index:
                continue
            y = lab.y.loc[ts]
            if y is None or (isinstance(y, float) and math.isnan(y)):
                continue                      # window has not closed yet
            p.outcome = float(y)
            t = lab.touch.loc[ts]
            p.touch = None if t is None else str(t)
            p.resolved_at = utc_now().isoformat()
            resolved += 1

        if resolved:
            self._rewrite(rows)
        return resolved

    # ----------------------------------------------------------- metrics
    def metrics(self, model_id: Optional[str] = None) -> Dict[str, Any]:
        """What the record actually says. Resolved rows only."""
        rows = [p for p in self.load() if p.outcome is not None]
        if model_id:
            rows = [p for p in rows if p.model_id == model_id]

        pending = len([p for p in self.load() if p.outcome is None])
        out: Dict[str, Any] = {
            "symbol": self.symbol,
            "interval": self.interval,
            "resolved": len(rows),
            "pending": pending,
        }
        if not rows:
            out["note"] = ("nothing resolved yet — a forward record is only "
                           "worth reading once its windows have closed")
            return out

        y = np.array([p.outcome for p in rows], float)
        p_up = np.array([p.p_up for p in rows], float)

        out["first"] = min(p.bar_close for p in rows)
        out["last"] = max(p.bar_close for p in rows)
        out["base_rate"] = float(y.mean())
        out["auc"] = _auc(y, p_up)
        out["brier"] = float(np.mean((p_up - y) ** 2))

        # calibration, coarse on purpose: with a few hundred rows, ten bins
        # is ten noisy numbers. Three says the thing worth knowing — whether
        # confident calls are actually better than unconfident ones.
        bins = [(0.0, 0.45, "below 0.45"), (0.45, 0.55, "0.45-0.55"),
                (0.55, 1.01, "above 0.55")]
        cal = []
        for lo, hi, label in bins:
            m = (p_up >= lo) & (p_up < hi)
            if m.sum():
                cal.append({"band": label, "n": int(m.sum()),
                            "predicted": float(p_up[m].mean()),
                            "actual": float(y[m].mean())})
        out["calibration"] = cal

        # only the calls it actually told you to act on
        acted = [p for p in rows if p.action.upper().startswith("ENTER")]
        if acted:
            ay = np.array([p.outcome for p in acted], float)
            out["acted"] = {"n": len(acted), "hit_rate": float(ay.mean())}
        else:
            out["acted"] = {"n": 0, "hit_rate": None}

        out["models"] = sorted({p.model_id for p in rows})
        return out


def _auc(y: np.ndarray, score: np.ndarray) -> Optional[float]:
    """Rank AUC without sklearn, so metrics never depend on a fitted model."""
    if len(np.unique(y)) < 2:
        return None
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), float)
    ranks[order] = np.arange(1, len(score) + 1)
    # average ranks within ties, or identical scores would bias the estimate
    s = score[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = ranks[order[i:j + 1]].mean()
        i = j + 1
    n1 = float((y == 1).sum())
    n0 = float((y == 0).sum())
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
