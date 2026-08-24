"""Order flow features from the per-bar tape histograms.

The histogram design pays off here. Because each bar stores its trade-size
distribution rather than just a total, "what counts as a large print" is a
question answered at feature-computation time, from data already on disk —
so re-tuning the threshold costs milliseconds instead of a re-download.

Every threshold is derived from a TRAILING window and explicitly excludes the
bar being classified. Estimating the threshold from a window that includes
bar t and then using it to classify bar t is self-referential: an unusually
large print raises the bar it is measured against, which is a quiet way of
hiding exactly the events you are hunting for.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from marketdata.aggtrades import (BUY_CNT_COLS, BUY_COLS, N_BUCKETS,
                                  SELL_CNT_COLS, SELL_COLS)

from .config import Agent4Config


def rolling_z(s: pd.Series, n: int) -> pd.Series:
    """Standardise against a ROLLING window — never the whole column.

    Duplicated deliberately rather than imported from agent2: the ablation
    deletes one block at a time, so an agent that stops importing when its
    neighbour is removed cannot be ablated.
    """
    # min_periods must never exceed the window, or pandas raises. Clamping
    # here rather than validating in config keeps short windows usable for
    # experiments instead of turning them into a crash.
    mp = min(n, max(20, n // 8))
    mu = s.rolling(n, min_periods=mp).mean()
    sd = s.rolling(n, min_periods=mp).std(ddof=0)
    return ((s - mu) / sd.where(sd > 1e-12)).replace([np.inf, -np.inf], np.nan)


def large_print_threshold_bucket(tape: pd.DataFrame, cfg: Agent4Config) -> pd.Series:
    """Lowest bucket that counts as a "large print", per bar.

    Defined as a percentile of the print-SIZE distribution: the threshold is
    the smallest size above which the top ``large_print_top_share`` of
    *prints by count* fall. Counting prints rather than dollars matters on a
    heavy tail, where a single top bucket can hold 5% of all volume while
    containing a handful of prints across hundreds of bars — a threshold set
    that way fires almost never and the feature is dead on arrival.

    ``shift(1)`` keeps it honest: the threshold for bar t is built only from
    bars strictly before t, so an unusually large print cannot raise the bar
    it is being measured against.
    """
    # add up buy-side and sell-side print counts per size bucket.
    # we count PRINTS here, not dollars - "large" is a percentile of trade
    # size, and counting dollars gives a wildly different (useless) answer
    counts = tape[BUY_CNT_COLS].to_numpy() + tape[SELL_CNT_COLS].to_numpy()
    trailing = (
        pd.DataFrame(counts, index=tape.index)
        .rolling(cfg.print_threshold_bars,
                 min_periods=min(cfg.print_threshold_bars,
                                 max(24, cfg.print_threshold_bars // 8)))
        .sum()
        .shift(1)                      # exclude the bar being classified
    )
    arr = trailing.to_numpy()
    grand = np.nansum(arr, axis=1, keepdims=True)

    with np.errstate(invalid="ignore", divide="ignore"):
        # Share of PRINTS at or above each bucket.
        # running total starting from the BIGGEST bucket downwards, so
        # share_from_top[b] answers "what fraction of prints are at least
        # this big?". flip -> cumsum -> flip back does that in one pass
        share_from_top = np.flip(np.nancumsum(np.flip(arr, axis=1), axis=1), axis=1)
        share_from_top = np.where(grand > 0, share_from_top / grand, np.nan)

    # Lowest bucket still capturing at least the target share of prints.
    # share_from_top is non-increasing, so that is the last True in each row.
    # walk down until we have captured at least the target share of prints.
    # share_from_top only ever decreases, so the LAST True in each row is the
    # smallest bucket that still qualifies. argmax on the reversed row finds
    # it, and the arithmetic turns that back into a normal index
    qualifies = share_from_top >= cfg.large_print_top_share
    last_true = qualifies.shape[1] - 1 - np.argmax(qualifies[:, ::-1], axis=1)
    # no trailing data yet -> N_BUCKETS, which means "unknown" downstream
    idx = np.where(qualifies.any(axis=1), last_true, N_BUCKETS)
    idx = np.where(np.isnan(grand).ravel() | (grand.ravel() <= 0), N_BUCKETS, idx)
    return pd.Series(idx, index=tape.index, dtype=float)


def _mask_from_bucket(bucket_idx: pd.Series) -> np.ndarray:
    """(n_bars, N_BUCKETS) boolean mask of buckets counting as large."""
    # broadcast bucket numbers (a row) against each bar's threshold (a
    # column) to get a per-bar True/False mask of "this bucket counts as large"
    cols = np.arange(N_BUCKETS)[None, :]
    return cols >= bucket_idx.to_numpy()[:, None]


def compute_tape_features(
    bars: pd.DataFrame, tape: pd.DataFrame, cfg: Agent4Config
) -> pd.DataFrame:
    """Flow, large-print and size-distribution columns."""
    idx = bars.index
    buy = tape["buy_notional"].astype(float)
    sell = tape["sell_notional"].astype(float)
    total = buy + sell # all aggressive volume this bar
    prints = tape["buy_prints"].astype(float) + tape["sell_prints"].astype(float)

    out = pd.DataFrame(index=idx)

    # --- aggregate flow ---------------------------------------------------
    with np.errstate(invalid="ignore", divide="ignore"):
        imbalance = ((buy - sell) / total.where(total > 0)).replace(
            [np.inf, -np.inf], np.nan)
    out["aggressor_imbalance"] = imbalance
    out["ofi_z"] = rolling_z(buy - sell, cfg.flow_z_window)

    # cumulative volume delta: keep a running total of aggressive buying minus
    # aggressive selling. the total drifts forever, so we only ever use its
    # slope over the last N bars, standardised (same treatment OBV gets in Agent 2)
    cvd = (buy - sell).cumsum()
    out["cvd_slope_z"] = rolling_z(cvd.diff(cfg.cvd_slope_bars), cfg.flow_z_window)

    # Divergence as a continuous correlation rather than a pattern match:
    # price rising while CVD falls means the move is not backed by aggressive
    # buying. Same construction as Agent 2's rsi_price_corr_20.
    out["flow_price_corr_20"] = bars["close"].rolling(
        cfg.divergence_window, min_periods=cfg.divergence_window
    ).corr(cvd)

    # --- large prints -----------------------------------------------------
    bucket_idx = large_print_threshold_bucket(tape, cfg)
    mask = _mask_from_bucket(bucket_idx)
    # multiply the notional histogram by the mask and sum: this adds up only
    # the dollars that came from prints big enough to count as large
    big_buy = (tape[BUY_COLS].to_numpy() * mask).sum(axis=1)
    big_sell = (tape[SELL_COLS].to_numpy() * mask).sum(axis=1)
    big_total = big_buy + big_sell # volume from large prints only
    unknown = bucket_idx >= N_BUCKETS          # not enough history to set a threshold yet

    with np.errstate(invalid="ignore", divide="ignore"):
        lp_imb = np.where(big_total > 0, (big_buy - big_sell) / big_total, np.nan)
        lp_share = np.where(total.to_numpy() > 0,
                            big_total / total.to_numpy(), np.nan)
    out["large_print_imbalance_1h"] = pd.Series(lp_imb, index=idx).mask(unknown)
    out["large_print_volume_share"] = pd.Series(lp_share, index=idx).mask(unknown)

    big_count = ((tape[BUY_CNT_COLS].to_numpy()
                  + tape[SELL_CNT_COLS].to_numpy()) * mask).sum(axis=1)
    out["large_print_count_z"] = rolling_z(
        pd.Series(big_count, index=idx).mask(unknown), cfg.large_print_z_window)

    out["max_print_usd_z"] = rolling_z(
        tape["max_print_notional"].astype(float), cfg.large_print_z_window)

    seen = (pd.Series(big_total, index=idx) > 0) & ~unknown # did a large print actually happen on this bar
    pos = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    last = pos.where(seen).ffill()
    out["bars_since_large_print"] = pos - last

    # --- size distribution ------------------------------------------------
    with np.errstate(invalid="ignore", divide="ignore"):
        avg_size = (total / prints.where(prints > 0)).replace([np.inf, -np.inf], np.nan)
        top_share = (tape["max_print_notional"].astype(float)
                     / total.where(total > 0)).replace([np.inf, -np.inf], np.nan)
    out["avg_trade_size_usd_z"] = rolling_z(avg_size, cfg.flow_z_window)
    out["top_print_share"] = top_share.clip(0.0, 1.0)
    out["trade_count_z"] = rolling_z(prints, cfg.flow_z_window)

    return out


def kline_flow_fallback(bars: pd.DataFrame, cfg: Agent4Config) -> pd.DataFrame:
    """Order flow available from klines alone, with no aggTrades download.

    Binance ships ``taker_buy_base_volume``, ``number_of_trades`` and
    ``quote_volume`` inside every kline. Their ratios are genuine order-flow
    imbalance, free, and already downloaded — which is why Agents 1 and 2
    were kept away from them: this is the block that gets to use them, and
    the ablation can only attribute their contribution if nothing upstream
    quietly consumed them first.

    Coarser than the tape (bar-level, no size distribution), but it means
    Agent 4 degrades to reduced resolution rather than to nothing.
    """
    out = pd.DataFrame(index=bars.index)
    vol = bars.get("volume")
    tbb = bars.get("taker_buy_base_volume")
    if vol is None or tbb is None:
        out["taker_buy_ratio"] = np.nan
        return out
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = (tbb / vol.where(vol > 0)).replace([np.inf, -np.inf], np.nan)
    out["taker_buy_ratio"] = ratio.clip(0.0, 1.0)
    return out
