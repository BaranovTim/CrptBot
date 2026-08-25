"""The regime block: what kind of market is this, right now?

These are the features the plan puts at iteration 1 of the ablation - the
floor every agent block must beat. They answer "is it volatile, is it busy,
is it trending, is positioning expensive" without any opinion on direction.

Replaces the "mood of the coin" idea from the original formula: instead of
one vague mood scalar, measurable regime numbers the model can interact with.
Every one is causal (rolling / shift only) and unit-free.

btc_corr_30d from the original plan is skipped: this is a single-asset
pipeline, and a coin's correlation with itself is 1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Agent5Config

REGIME_COLUMNS = (
    "vol_pctile",           # where current realized vol sits vs its own month
    "volume_vs_baseline",   # dollar volume relative to its 30-day median
    "trend_strength",       # efficiency ratio: |net move| / path length, 0..1
    "funding_z",            # funding rate vs its own baseline (NaN without data)
    "hod_sin", "hod_cos",   # hour of day as a circle, so 23:00 and 00:00 are close
    "dow",                  # day of week 0-6
)


def compute_regime(
    bars: pd.DataFrame,
    cfg: Agent5Config,
    funding: pd.Series = None,
) -> pd.DataFrame:
    """One row per bar, indexed like ``bars``. Causal throughout."""
    idx = bars.index
    close = bars["close"].astype(float)
    out = pd.DataFrame(index=idx)

    # realized volatility = rolling std of log returns...
    ret = np.log(close).diff()
    vol = ret.rolling(cfg.vol_window, min_periods=cfg.vol_window).std(ddof=0)
    # ...then ranked inside its own trailing window. rolling rank is causal:
    # the rank of TODAY's value among the last N days, nothing from the future
    out["vol_pctile"] = vol.rolling(
        cfg.vol_pctile_window, min_periods=cfg.vol_pctile_window // 4
    ).rank(pct=True)

    # busy or quiet, in dollars so the answer does not drift with price level.
    # median rather than mean because volume spikes are enormous outliers
    dollar_vol = (bars["quote_volume"] if "quote_volume" in bars.columns
                  else bars["volume"] * close).astype(float)
    base = dollar_vol.rolling(
        cfg.volume_baseline_window, min_periods=cfg.volume_baseline_window // 4
    ).median()
    out["volume_vs_baseline"] = (dollar_vol / base.where(base > 0)).replace(
        [np.inf, -np.inf], np.nan)

    # efficiency ratio: how much of the path actually went somewhere.
    # 1.0 = straight line (strong trend), near 0 = pure chop
    net = close.diff(cfg.trend_window).abs()
    path = close.diff().abs().rolling(cfg.trend_window,
                                      min_periods=cfg.trend_window).sum()
    out["trend_strength"] = (net / path.where(path > 0)).replace(
        [np.inf, -np.inf], np.nan).clip(0.0, 1.0)

    # positioning cost. optional feed - NaN when absent, and the coverage of
    # this column tells Agent 5 whether it ever had the data
    if funding is not None and len(funding):
        f = funding.reindex(idx, method="ffill")
        mu = f.rolling(cfg.funding_z_window, min_periods=cfg.funding_z_window // 4).mean()
        sd = f.rolling(cfg.funding_z_window, min_periods=cfg.funding_z_window // 4).std(ddof=0)
        out["funding_z"] = ((f - mu) / sd.where(sd > 1e-12)).replace(
            [np.inf, -np.inf], np.nan)
    else:
        out["funding_z"] = np.nan

    # time of day on a circle: 23:00 and 00:00 are neighbours, and crypto has
    # a real intraday seasonality (sessions, funding times, US open)
    hour = idx.hour + idx.minute / 60.0
    out["hod_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["hod_cos"] = np.cos(2 * np.pi * hour / 24.0)
    out["dow"] = idx.dayofweek.astype(float)

    return out[list(REGIME_COLUMNS)]
