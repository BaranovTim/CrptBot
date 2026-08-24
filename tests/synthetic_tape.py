"""Deterministic fake trade tape, so tests never touch the network."""
from __future__ import annotations

import numpy as np
import pandas as pd

from marketdata.aggtrades import aggregate_tape


def make_raw_prints(bars: pd.DataFrame, per_bar: int = 400, seed: int = 11,
                    buy_bias: float = 0.5, whale_rate: float = 0.01) -> pd.DataFrame:
    """aggTrades-shaped prints, with a heavy-tailed size distribution.

    Log-normal sizes plus an occasional whale, because a distribution with no
    tail would make large-print detection trivially easy and prove nothing.
    """
    rng = np.random.default_rng(seed)
    interval = bars.index[1] - bars.index[0]
    rows = []
    for close in bars.index:
        start_ms = int((close - interval).timestamp() * 1000) + 1
        end_ms = int(close.timestamp() * 1000)
        # Vary the print count per bar. A constant count has zero variance,
        # which makes trade_count_z undefined — and real tape is never that
        # tidy, so a constant would let a broken z-score pass unnoticed.
        n = max(5, int(rng.poisson(per_bar)))
        qty = rng.lognormal(-3.0, 1.4, n)
        whales = rng.random(n) < whale_rate
        qty[whales] *= rng.uniform(50, 400, whales.sum())
        rows.append(pd.DataFrame({
            "price": float(bars.loc[close, "close"]) * (1 + rng.normal(0, 2e-4, n)),
            "quantity": qty,
            "transact_time": rng.integers(start_ms, max(start_ms + 1, end_ms), n),
            # m=True means the buyer was the maker -> an aggressive SELL.
            "is_buyer_maker": rng.random(n) >= buy_bias,
        }))
    return pd.concat(rows, ignore_index=True)


def make_tape(bars: pd.DataFrame, **kw) -> pd.DataFrame:
    tape = aggregate_tape(make_raw_prints(bars, **kw), bars.index)
    return tape.reindex(bars.index).fillna(0.0)


def make_open_interest(bars: pd.DataFrame, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    oi = 50_000 * np.exp(np.cumsum(rng.normal(0, 0.004, len(bars))))
    return pd.DataFrame({"open_interest": oi,
                         "open_interest_usd": oi * bars["close"].to_numpy()},
                        index=bars.index)


def make_liquidations(bars: pd.DataFrame, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(bars)
    longs = np.where(rng.random(n) < 0.08, rng.lognormal(12, 1.5, n), 0.0)
    shorts = np.where(rng.random(n) < 0.08, rng.lognormal(12, 1.5, n), 0.0)
    return pd.DataFrame({"long_liquidated": longs, "short_liquidated": shorts},
                        index=bars.index)


def make_netflow(bars: pd.DataFrame, seed: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"netflow_usd": rng.normal(0, 5e6, len(bars))},
                        index=bars.index)
