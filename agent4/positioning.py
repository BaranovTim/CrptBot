"""Open interest and liquidations — was positioning crowded before the move?

Order flow says who was aggressing. Positioning says whether there was fuel.
Most crypto spikes are liquidation cascades into a thin book, and the
difference between "real flow" and "a cascade" is visible here and almost
nowhere else.

Two sign conventions that are easy to invert, both pinned by tests:

  A liquidation order with side=SELL means a LONG was force-closed. Longs
  being liquidated is forced selling — bearish. Shorts being liquidated is
  forced buying — bullish. So ``liq_imbalance_1h`` is positive when SHORTS
  are the ones getting hurt.

  Open interest rising while price falls means new shorts are being opened,
  not longs closing. That is emitted as a correlation rather than a signed
  feature, because whether it is bullish (squeeze fuel) or bearish (trend
  confirmation) is exactly the question Agent 5 exists to answer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Agent4Config
from .tape import rolling_z


def compute_positioning_features(
    bars: pd.DataFrame,
    oi: pd.DataFrame,
    liq: pd.DataFrame,
    cfg: Agent4Config,
) -> pd.DataFrame:
    idx = bars.index
    out = pd.DataFrame(index=idx)

    # --- open interest ----------------------------------------------------
    if oi is not None and not oi.empty and "open_interest" in oi.columns:
        series = pd.to_numeric(oi["open_interest"], errors="coerce")
        # percent change, not raw change: open interest grows over the years,
        # so a raw difference means something different in 2023 vs 2026
        change = series.pct_change(cfg.oi_change_bars)
        out["oi_change_pct"] = change.replace([np.inf, -np.inf], np.nan)
        out["oi_change_z"] = rolling_z(out["oi_change_pct"], cfg.oi_z_window)
        # Negative correlation = open interest builds as price falls = new
        # shorts. Continuous, so no threshold is imposed on the model.
        out["oi_price_divergence"] = bars["close"].pct_change().rolling(
            cfg.divergence_window, min_periods=cfg.divergence_window
        ).corr(out["oi_change_pct"])
    else:
        for c in ("oi_change_pct", "oi_change_z", "oi_price_divergence"):
            out[c] = np.nan

    # --- liquidations -----------------------------------------------------
    has_liq = (liq is not None and not liq.empty
               and {"long_liquidated", "short_liquidated"} <= set(liq.columns))
    if has_liq:
        longs = pd.to_numeric(liq["long_liquidated"], errors="coerce").fillna(0.0)
        shorts = pd.to_numeric(liq["short_liquidated"], errors="coerce").fillna(0.0)
        total = longs + shorts # all forced closures this bar
        with np.errstate(invalid="ignore", divide="ignore"):
            # shorts minus longs, so POSITIVE means shorts are the ones being
            # force-closed. that is forced buying, hence bullish
            imb = ((shorts - longs) / total.where(total > 0)).replace(
                [np.inf, -np.inf], np.nan)
        out["liq_imbalance_1h"] = imb.clip(-1.0, 1.0)

        spike = rolling_z(total, cfg.oi_z_window) > cfg.liq_spike_z
        pos = pd.Series(np.arange(len(idx), dtype=float), index=idx)
        last = pos.where(spike.fillna(False)).ffill()
        out["bars_since_liq_spike"] = pos - last
    else:
        # No liquidation feed is the normal case — Binance restricted the
        # historical one. NaN, never 0: "we cannot see liquidations" is not
        # "no liquidations happened".
        out["liq_imbalance_1h"] = np.nan
        out["bars_since_liq_spike"] = np.nan

    return out
