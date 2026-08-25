"""Triple-barrier labels and uniqueness weights.

THE LABEL
---------
A sample at bar t enters at t's close and watches three barriers:

    upper     close_t + k_up * ATR_t     hit first -> y = 1 (the trade won)
    lower     close_t - k_dn * ATR_t     hit first -> y = 0 (stopped out)
    vertical  t + max_hold_bars          neither hit -> y = sign of the
                                          return at exit (the simple option
                                          the plan says to start with)

This bakes the payoff into the label: the model learns "would this trade
have won", not an abstract direction. A 90% chance of +0.05% loses money
after fees; a 55% chance of +3% does not - which is why probability alone
can never be the entry rule, and why the barriers match the ones the
decision stage actually trades.

LABELS LOOK FORWARD - ON PURPOSE
--------------------------------
Everything else in this project is forbidden from reading the future.
Labels are the one exception: a label IS a statement about the future,
that is its job. The discipline is different here:

  * every label records t1, the bar where it resolved. Cross-validation
    purges any training sample whose [t, t1] window overlaps the test
    period - otherwise the model trains on outcomes that reveal test data.
  * samples whose window would run past the end of the data get NO label
    (NaN), never a partial one. A label that peeked at "the data so far"
    would systematically favour whatever the final bars did.

THE AMBIGUOUS BAR
-----------------
With OHLC bars, one bar can touch both barriers (high above the upper AND
low below the lower). The bar does not say which happened first. We count
it as a LOSS. Pessimistic on purpose: the honest alternative is dropping
the sample, but dropping selectively removes exactly the most volatile
moments, and a backtest that quietly deletes its scariest bars is how you
build confidence that evaporates live.

UNIQUENESS WEIGHTS
------------------
Sampling every bar with 24-bar labels means each outcome is shared by ~24
overlapping samples. Training that treats them as independent thinks it
has 24x the data it really has. Each sample is weighted by the average of
1 / (number of labels active) over its own window - the Lopez de Prado
average-uniqueness scheme. The sum of weights is then an honest count of
independent observations.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Agent5Config


@dataclass
class LabelResult:
    """Everything the training stage needs, aligned on the same index."""

    y: pd.Series          # 1.0 win / 0.0 loss, NaN where no label exists
    t1: pd.Series         # bar POSITION where the label resolved (float, NaN unlabelled)
    weight: pd.Series     # uniqueness weight, NaN where unlabelled
    touch: pd.Series      # "upper" | "lower" | "timeout" | "ambiguous" | NaN
    tp_pct: pd.Series     # take-profit distance in % of entry price
    sl_pct: pd.Series     # stop-loss distance in % of entry price

    @property
    def labelled(self) -> pd.Series:
        return self.y.notna()

    def effective_sample_size(self) -> float:
        """Sum of uniqueness weights - the honest count of independent samples.

        NOT Kish's (sum w)^2 / sum(w^2): that formula measures how uneven the
        weights are, and returns n for perfectly uniform weights no matter how
        much the labels overlap. Here 24 fully-overlapping samples at weight
        1/24 each must add up to ~1 independent observation, and a plain sum
        is exactly what does that.
        """
        w = self.weight.dropna().to_numpy()
        return float(w.sum()) if len(w) else 0.0


def _atr(bars: pd.DataFrame, period: int) -> pd.Series:
    # own copy of ATR (Wilder). agent5 must not import from agent1/agent2 -
    # the ablation deletes those wholesale
    h, l, c = bars["high"], bars["low"], bars["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def triple_barrier(bars: pd.DataFrame, cfg: Agent5Config) -> LabelResult:
    """Label every bar that has ATR and a complete future window."""
    n = len(bars)
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    atr = _atr(bars, cfg.atr_period).to_numpy(float)

    y = np.full(n, np.nan)
    t1 = np.full(n, np.nan)
    touch = np.full(n, None, dtype=object)

    upper = close + cfg.k_up * atr        # per-sample barrier levels
    lower = close - cfg.k_dn * atr

    # a bar is labellable only if its ATR exists and its FULL window fits
    # inside the data. samples near the end get no label at all - a partial
    # window would bias labels toward whatever the last bars did
    can = (~np.isnan(atr)) & (atr > 0)
    can &= np.arange(n) + cfg.max_hold_bars < n

    # resolve all samples together, one horizon step at a time: at step h we
    # look at bar t+h for every still-pending sample. max_hold iterations of
    # vectorised numpy instead of a python loop per sample
    pending = can.copy()
    idx_all = np.arange(n)
    for h in range(1, cfg.max_hold_bars + 1):
        if not pending.any():
            break
        t = idx_all[pending]
        ahead = t + h                                    # the bar being examined
        hit_up = high[ahead] >= upper[t]
        hit_dn = low[ahead] <= lower[t]

        both = hit_up & hit_dn                           # the ambiguous bar
        if both.any():
            sel = t[both]
            y[sel] = 0.0                                 # counted as a loss - see docstring
            t1[sel] = ahead[both]
            touch[sel] = "ambiguous"
        only_up = hit_up & ~hit_dn
        if only_up.any():
            sel = t[only_up]
            y[sel] = 1.0
            t1[sel] = ahead[only_up]
            touch[sel] = "upper"
        only_dn = hit_dn & ~hit_up
        if only_dn.any():
            sel = t[only_dn]
            y[sel] = 0.0
            t1[sel] = ahead[only_dn]
            touch[sel] = "lower"
        pending[t[hit_up | hit_dn]] = False

    # whoever is still pending timed out: label = sign of the return at exit
    if pending.any():
        t = idx_all[pending]
        exit_close = close[t + cfg.max_hold_bars]
        y[t] = (exit_close > close[t]).astype(float)
        t1[t] = t + cfg.max_hold_bars
        touch[t] = "timeout"

    weight = _uniqueness_weights(y, t1, n)

    with np.errstate(invalid="ignore", divide="ignore"):
        tp_pct = np.where(can, 100.0 * cfg.k_up * atr / close, np.nan)
        sl_pct = np.where(can, 100.0 * cfg.k_dn * atr / close, np.nan)

    ix = bars.index
    return LabelResult(
        y=pd.Series(y, index=ix, name="y"),
        t1=pd.Series(t1, index=ix, name="t1"),
        weight=pd.Series(weight, index=ix, name="weight"),
        touch=pd.Series(touch, index=ix, name="touch"),
        tp_pct=pd.Series(tp_pct, index=ix, name="tp_pct"),
        sl_pct=pd.Series(sl_pct, index=ix, name="sl_pct"),
    )


def _uniqueness_weights(y: np.ndarray, t1: np.ndarray, n: int) -> np.ndarray:
    """Average-uniqueness weights.

    concurrency[b] = how many labels are watching bar b. each sample's
    weight is the average of 1/concurrency over its own window [t, t1].
    fully overlapping k samples each get ~1/k; a lone sample gets 1.
    """
    labelled = ~np.isnan(y)
    concurrency = np.zeros(n)
    for t in np.flatnonzero(labelled):
        concurrency[t: int(t1[t]) + 1] += 1.0

    weight = np.full(n, np.nan)
    for t in np.flatnonzero(labelled):
        window = concurrency[t: int(t1[t]) + 1]
        weight[t] = float(np.mean(1.0 / window[window > 0]))
    return weight
