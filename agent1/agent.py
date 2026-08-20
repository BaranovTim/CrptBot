"""Agent 1 — the pattern detector.

A thermometer, not a doctor.  It reads 38.5 and reports 38.5; deciding whether
that is bad is Agent 5's job.

Properties this file is responsible for preserving:

  * Pure.  The same window of candles always yields the same numbers.  No
    state survives between calls; the state machines inside rebuild
    themselves from bar 0 of the window every time.
  * Untrained.  Not one number here is fitted to outcomes.  Every constant
    lives in ``Agent1Config``.  The moment a constant gets chosen by looking
    at what made money, it has become part of Agent 5 and belongs there.
  * Blind to the future.  Enforced by ``tests/test_no_lookahead.py``, not by
    good intentions.
  * Opinionless.  "dist_to_bull_ob_atr = -0.7" says a zone built by the
    bullish-order-block rule sits 0.7 ATR below price.  It does NOT say buy.
    Whether bullish order blocks precede up moves is a question about data,
    and Agent 5 answers it.

Reads only OHLCV.  Not funding, not open interest, not the order book, not
news — those belong to Agents 3 and 4 and the regime block.  The boundary is
not tidiness: if funding leaked in here, the ablation could never tell you
whether the lift came from geometry or from funding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .candles import compute_candles
from .config import DEFAULT_CONFIG, Agent1Config
from .figures import compute_figures
from .fib import compute_fib
from .htf import compute_htf
from .indicators import atr as compute_atr
from .liquidity import compute_liquidity
from .pivots import Pivot, find_pivots
from .schema import FEATURE_COLUMNS, validate_features
from .structure import BOS, CHOCH, SWEEP, StructureEvent, compute_structure
from .zones import compute_zones

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass
class PatternOutput:
    """Two channels, deliberately separated.

    ``features`` goes to the model.  ``trace`` goes to the log and the
    dashboard and must never reach the model — if a human-readable
    explanation could influence the prediction, you could no longer validate
    the explanation independently of the prediction.
    """

    timestamp: pd.Timestamp
    features: Dict[str, float]
    trace: List[str] = field(default_factory=list)

    def as_row(self) -> pd.DataFrame:
        return pd.DataFrame([self.features], index=[self.timestamp])[list(FEATURE_COLUMNS)]

    def __str__(self) -> str:
        head = f"[{self.timestamp}] Agent 1"
        lines = [head, "-" * len(head)]
        lines += ["  " + t for t in self.trace] or ["  (nothing notable)"]
        return "\n".join(lines)


def _validate_bars(bars: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"bars is missing required columns: {missing}")
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise TypeError(
            "bars must be indexed by close_time as a DatetimeIndex. "
            "Indexing by open_time makes the close of the current bar visible "
            "at its open, which is a one-bar look into the future."
        )
    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars index must be sorted ascending")
    if bars.index.has_duplicates:
        raise ValueError("bars index contains duplicate timestamps")
    return bars


class PatternAgent:
    def __init__(self, config: Agent1Config = DEFAULT_CONFIG):
        self.cfg = config

    @property
    def warmup_bars(self) -> int:
        """Rows at the start of a window whose features are not trustworthy.

        Drop these before handing anything to Agent 5, otherwise the first
        few hundred samples of every training window are quietly degraded.
        """
        return self.cfg.atr_period + self.cfg.confirm_bars + 2 * self.cfg.figure_max_sep

    # -- internals ---------------------------------------------------------
    def _compute_all(self, bars: pd.DataFrame):
        cfg = self.cfg
        atr = compute_atr(bars["high"], bars["low"], bars["close"], cfg.atr_period)
        pivots = find_pivots(
            bars["high"], bars["low"], cfg.pivot_left, cfg.pivot_right, cfg.confirm_bars
        )
        struct, events = compute_structure(bars, pivots, atr, cfg.break_mode)
        blocks = [
            struct,
            compute_zones(bars, events, atr, cfg),
            compute_liquidity(bars, pivots, atr, cfg),
            compute_fib(bars, pivots, atr),
            compute_figures(bars, pivots, atr, cfg),
            compute_candles(bars, cfg),
            compute_htf(bars, cfg),
        ]
        features = pd.concat(blocks, axis=1)[list(FEATURE_COLUMNS)]
        return validate_features(features), events, pivots, atr

    # -- public ------------------------------------------------------------
    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Features for every bar in the window, indexed by close_time.

        One pass over the window rather than a loop of window slices: every
        operation inside is causal, so the vectorised path and the slow path
        agree by construction — which is precisely what the no-lookahead test
        checks.
        """
        bars = _validate_bars(bars)
        features, _, _, _ = self._compute_all(bars)
        return features

    def latest(self, bars: pd.DataFrame) -> PatternOutput:
        """Features plus a human-readable trace for the final bar."""
        bars = _validate_bars(bars)
        features, events, pivots, atr = self._compute_all(bars)
        row = features.iloc[-1]
        return PatternOutput(
            timestamp=features.index[-1],
            features={k: float(row[k]) for k in FEATURE_COLUMNS},
            trace=self._build_trace(bars, row, events, atr),
        )

    # -- trace -------------------------------------------------------------
    def _build_trace(
        self, bars: pd.DataFrame, row: pd.Series, events: List[StructureEvent],
        atr: pd.Series,
    ) -> List[str]:
        t = len(bars) - 1
        out: List[str] = []

        def has(k: str) -> bool:
            return not (row[k] is None or (isinstance(row[k], float) and np.isnan(row[k])))

        def word(v: float) -> str:
            return "bullish" if v > 0 else "bearish" if v < 0 else "neutral"

        if has("trend_direction"):
            s = f"trend {word(row['trend_direction'])}"
            if has("trend_direction_4h"):
                s += f", 4h {word(row['trend_direction_4h'])}"
            out.append(s)

        recent = [e for e in events if t - e.bar <= 50]
        for e in reversed(recent):
            age = t - e.bar
            if e.kind in (BOS, CHOCH):
                extra = ""
                if e.kind == BOS and has("consecutive_bos_count"):
                    extra = f", {int(row['consecutive_bos_count'])} in a row"
                out.append(
                    f"{e.kind} {word(e.direction)} {age} bars ago, took out {e.level:.2f}{extra}"
                )
                break
        for e in reversed(recent):
            if e.kind == SWEEP:
                side = "highs" if e.direction < 0 else "lows"
                out.append(
                    f"liquidity sweep of {side} {t - e.bar} bars ago at {e.level:.2f} "
                    f"(wicked through, closed back inside)"
                )
                break

        if has("position_in_range"):
            p = row["position_in_range"]
            label = "premium" if p > 0.5 else "discount"
            out.append(f"price at {p:.2f} of dealing range ({label})")

        if row.get("inside_ob", 0):
            out.append(f"price inside a {word(row['inside_ob'])} order block")
        elif has("dist_to_bull_ob_atr") or has("dist_to_bear_ob_atr"):
            parts = []
            if has("dist_to_bull_ob_atr"):
                parts.append(f"bullish OB {abs(row['dist_to_bull_ob_atr']):.2f} ATR "
                             f"{'below' if row['dist_to_bull_ob_atr'] < 0 else 'above'}")
            if has("dist_to_bear_ob_atr"):
                parts.append(f"bearish OB {abs(row['dist_to_bear_ob_atr']):.2f} ATR "
                             f"{'above' if row['dist_to_bear_ob_atr'] > 0 else 'below'}")
            out.append("nearest order blocks: " + ", ".join(parts))

        if has("dist_to_fvg_atr"):
            out.append(
                f"unfilled {word(row['fvg_direction'])} FVG, size {row['fvg_size_atr']:.2f} ATR, "
                f"{abs(row['dist_to_fvg_atr']):.2f} ATR "
                f"{'above' if row['dist_to_fvg_atr'] > 0 else 'below'}"
            )

        for col, label in (("dist_to_equal_highs_atr", "equal highs"),
                           ("dist_to_equal_lows_atr", "equal lows")):
            if has(col):
                out.append(f"{label} {abs(row[col]):.2f} ATR "
                           f"{'above' if row[col] > 0 else 'below'} — resting liquidity")

        if has("dist_to_fib_618_atr") and abs(row["dist_to_fib_618_atr"]) < 1.0:
            out.append(
                f"0.618 retracement of the last {word(row['fib_leg_direction'])} leg is "
                f"{abs(row['dist_to_fib_618_atr']):.2f} ATR away"
            )

        for col, name in (("w_completion", "double bottom (W)"),
                          ("m_completion", "double top (M)")):
            if has(col) and row[col] > 0.3:
                out.append(f"{name} {row[col] * 100:.0f}% formed")
        if has("hs_completion") and row["hs_completion"] > 0.3:
            kind = "inverse head & shoulders" if row["hs_direction"] > 0 else "head & shoulders"
            out.append(f"{kind} {row['hs_completion'] * 100:.0f}% formed")

        if has("cdl_bull_count_3") and row["cdl_bull_count_3"] > 0:
            out.append(f"{int(row['cdl_bull_count_3'])} bullish candle signals in last 3 bars")
        if has("cdl_bear_count_3") and row["cdl_bear_count_3"] > 0:
            out.append(f"{int(row['cdl_bear_count_3'])} bearish candle signals in last 3 bars")

        return out
