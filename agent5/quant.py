"""The quant block: what the systematic-trading literature says predicts
crypto returns, as causal features for the judge.

The other blocks read the chart (agent1), the indicators (agent2) and the
tape (agent4). None of them carries the effects that the factor literature
has actually documented in crypto, and the judge cannot learn a signal it
was never shown:

  TIME-SERIES MOMENTUM      Liu & Tsyvinski (2021), Liu, Tsyvinski & Wu
                            (2022, J. Finance): trailing one- to four-week
                            returns predict the next week's, and a market /
                            size / momentum three-factor model prices the
                            cross-section. Nothing here looks back further
                            than ~10 bars.
  SHORT-TERM REVERSAL       the last day's return reverses in the smaller,
                            less liquid coins and continues in the largest
                            (Dobrynskaya-style "Up or down?", 2021).
  BITCOIN LEADS             altcoins respond to BTC's move with a lag that
                            grows with illiquidity (Granger-causal, high-
                            frequency evidence). A single-coin pipeline has
                            no way to see BTC at all.
  CARRY                     the funding rate is the price of the crowd's
                            positioning; extremes mean-revert. The regime
                            block has a funding_z column that no training
                            run ever filled (see marketdata/funding.py).
  VOLATILITY STATE          Moreira & Muir (2017): scaling by recent
                            variance raises Sharpe in most factor portfolios;
                            in crypto momentum the evidence is mixed, which
                            is a reason to let the model decide from the
                            number rather than hard-code the scaling.
  BREAKOUT / DRAWDOWN       Donchian position and distance from the rolling
                            extreme are the oldest trend-following inputs
                            there are (Carver, Systematic Trading).
  BREADTH                   how many coins are moving together -- the
                            market factor read across the whole list.

EVERY COLUMN IS CAUSAL. Only `shift`, `rolling`, `diff` and a backward
`merge_asof`; no centred windows, no full-column normalisation. Lookbacks
are given in HOURS so the same feature means the same thing on 15m and 4h.
Momentum is expressed as a t-statistic (return over its own volatility for
that horizon), which is unit-free across coins and timeframes and is the
form the pooled daily model needs.

WHAT IS NOT HERE, ON PURPOSE. No cross-sectional ranking of the coin
against the others (that is a portfolio construction, not a feature of one
coin's next bar), no price levels (they would let a pooled model tell
coins apart), no HMM regimes (a fitted state is a look-ahead unless it is
refit inside every fold).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# lookbacks in hours; converted to bars per timeframe
MOM_HOURS = (24, 72, 168, 336, 672)
VOL_HOURS = 672          # the volatility a momentum score is measured against
BETA_HOURS = 720
DONCHIAN_HOURS = 168
DRAWDOWN_HOURS = 672
FUNDING_WINDOW = 90      # settlements (~30 days at 8h)

QUANT_COLUMNS = (
    *[f"mom_{h}h" for h in MOM_HOURS],     # TSMOM t-stats
    "rev_1bar_z",                         # last bar, vol-scaled (reversal)
    "rev_24h_z",                          # last day, vol-scaled
    "rv_ratio_24h_168h",                  # is vol expanding or contracting
    "donchian_pos_168h",                  # 0 at the week's low, 1 at its high
    "dd_from_high_672h",                  # distance below the month's high
    "up_from_low_672h",                   # distance above the month's low
    "btc_ret_1bar_z", "btc_ret_4bar_z",   # BTC lead-lag
    "btc_mom_24h", "btc_mom_168h",        # the market factor's own momentum
    "rel_mom_168h",                       # coin momentum minus BTC's
    "beta_btc_720h", "corr_btc_720h",
    "funding_rate_pct",                   # last settled rate, % per settlement
    "funding_ma_3d_pct",
    "funding_z_30d",
    "breadth_24h", "breadth_168h",        # share of coins up over the window
)


def _bars_for(hours: float, hours_per_bar: float) -> int:
    return max(1, int(round(hours / hours_per_bar)))


def _mom_tstat(logc: pd.Series, n: int, rv: pd.Series) -> pd.Series:
    """Return over n bars divided by the volatility of an n-bar move."""
    move = logc - logc.shift(n)
    scale = (rv * np.sqrt(n)).where(rv > 0)
    return (move / scale).replace([np.inf, -np.inf], np.nan).clip(-5, 5)


def compute_quant(
    bars: pd.DataFrame,
    hours_per_bar: float,
    btc: Optional[pd.DataFrame] = None,
    funding: Optional[pd.Series] = None,
    market: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """One row per bar, indexed like ``bars``.

    ``btc``     BTCUSDT bars on the same timeframe (may BE ``bars``).
    ``funding`` settled rates indexed by settlement time (marketdata.funding).
    ``market``  close prices of every coin in the list, one column each, on
                the same timeframe -- for breadth.
    Missing inputs leave their columns NaN; the coverage tells the judge.
    """
    idx = bars.index
    out = pd.DataFrame(index=idx)
    close = bars["close"].astype(float)
    logc = np.log(close)
    ret = logc.diff()
    n_vol = _bars_for(VOL_HOURS, hours_per_bar)
    rv = ret.rolling(n_vol, min_periods=n_vol // 4).std(ddof=0)

    for h in MOM_HOURS:
        out[f"mom_{h}h"] = _mom_tstat(logc, _bars_for(h, hours_per_bar), rv)
    out["rev_1bar_z"] = (ret / rv.where(rv > 0)).clip(-5, 5)
    out["rev_24h_z"] = _mom_tstat(logc, _bars_for(24, hours_per_bar), rv)

    n24, n168 = _bars_for(24, hours_per_bar), _bars_for(168, hours_per_bar)
    # a volatility needs at least two returns: on the daily timeframe the
    # "24h" window is one bar, so the short leg of the ratio becomes two days
    n_short = max(2, n24)
    rv24 = ret.rolling(n_short, min_periods=max(2, n_short // 2)).std(ddof=0)
    rv168 = ret.rolling(n168, min_periods=n168 // 4).std(ddof=0)
    out["rv_ratio_24h_168h"] = (rv24 / rv168.where(rv168 > 0)).replace(
        [np.inf, -np.inf], np.nan).clip(0, 10)

    nd = _bars_for(DONCHIAN_HOURS, hours_per_bar)
    hi = bars["high"].astype(float).rolling(nd, min_periods=nd // 2).max()
    lo = bars["low"].astype(float).rolling(nd, min_periods=nd // 2).min()
    out["donchian_pos_168h"] = ((close - lo) / (hi - lo).where(hi > lo)).clip(0, 1)
    nm = _bars_for(DRAWDOWN_HOURS, hours_per_bar)
    hi_m = close.rolling(nm, min_periods=nm // 2).max()
    lo_m = close.rolling(nm, min_periods=nm // 2).min()
    out["dd_from_high_672h"] = (close / hi_m.where(hi_m > 0) - 1.0).clip(-1, 0)
    out["up_from_low_672h"] = (close / lo_m.where(lo_m > 0) - 1.0).clip(0, 10)

    # --- the market factor: BTC ---------------------------------------
    if btc is not None and len(btc):
        bc = np.log(btc["close"].astype(float)).reindex(idx, method="ffill")
        bret = bc.diff()
        brv = bret.rolling(n_vol, min_periods=n_vol // 4).std(ddof=0)
        out["btc_ret_1bar_z"] = (bret / brv.where(brv > 0)).clip(-5, 5)
        out["btc_ret_4bar_z"] = _mom_tstat(bc, 4, brv)
        out["btc_mom_24h"] = _mom_tstat(bc, n24, brv)
        out["btc_mom_168h"] = _mom_tstat(bc, n168, brv)
        out["rel_mom_168h"] = out["mom_168h"] - out["btc_mom_168h"]
        nb = _bars_for(BETA_HOURS, hours_per_bar)
        cov = ret.rolling(nb, min_periods=nb // 4).cov(bret)
        var = bret.rolling(nb, min_periods=nb // 4).var(ddof=0)
        out["beta_btc_720h"] = (cov / var.where(var > 0)).replace(
            [np.inf, -np.inf], np.nan).clip(-5, 5)
        out["corr_btc_720h"] = ret.rolling(nb, min_periods=nb // 4).corr(bret)
    else:
        for c in ("btc_ret_1bar_z", "btc_ret_4bar_z", "btc_mom_24h", "btc_mom_168h",
                  "rel_mom_168h", "beta_btc_720h", "corr_btc_720h"):
            out[c] = np.nan

    # --- carry -----------------------------------------------------------
    if funding is not None and len(funding):
        from marketdata.funding import align_to_bars
        f = funding.sort_index()
        # statistics on the settlement series itself, then aligned: a 30-day
        # window is 90 settlements whatever the bar size
        ma = f.rolling(9, min_periods=3).mean()
        mu = f.rolling(FUNDING_WINDOW, min_periods=FUNDING_WINDOW // 3).mean()
        sd = f.rolling(FUNDING_WINDOW, min_periods=FUNDING_WINDOW // 3).std(ddof=0)
        z = ((f - mu) / sd.where(sd > 1e-12)).replace([np.inf, -np.inf], np.nan)
        out["funding_rate_pct"] = align_to_bars(f * 100.0, idx)
        out["funding_ma_3d_pct"] = align_to_bars(ma * 100.0, idx)
        out["funding_z_30d"] = align_to_bars(z, idx).clip(-5, 5)
    else:
        out["funding_rate_pct"] = np.nan
        out["funding_ma_3d_pct"] = np.nan
        out["funding_z_30d"] = np.nan

    # --- breadth -----------------------------------------------------------
    if market is not None and market.shape[1] >= 3:
        m = np.log(market.astype(float)).reindex(idx, method="ffill")
        for h, name in ((24, "breadth_24h"), (168, "breadth_168h")):
            n = _bars_for(h, hours_per_bar)
            up = (m - m.shift(n)) > 0
            have = (m - m.shift(n)).notna()
            out[name] = (up.sum(axis=1) / have.sum(axis=1).where(have.sum(axis=1) > 0))
    else:
        out["breadth_24h"] = np.nan
        out["breadth_168h"] = np.nan

    return out[list(QUANT_COLUMNS)]
