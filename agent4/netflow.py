"""Exchange netflow — the one input worth paying for, and not yet.

Coins moving ONTO exchanges is supply arriving at the venue where it can be
sold; coins moving OFF is accumulation into cold storage. Netflow spikes are,
along with large prints, one of the two whale signals that actually hold up.

What does NOT hold up is wallet-following. Named-whale tracking is lagging by
construction — you see the transfer after it lands — and it is actively gamed
by people who know they are being watched, who will happily move funds to
produce a signal. This module deliberately has no wallet-tracking path.

**No paid client is bundled here, on purpose.** Netflow needs Glassnode,
Nansen or Arkham, all of which cost real money per month. Buying that feed
before you can measure whether Agent 4 contributes anything is spending
money to answer a question your evaluation harness will answer for free.
Build the harness, run the ablation with the free tier (tape + open
interest), and buy the data only if the block earns it.

So the default provider returns nothing and the netflow columns are NaN,
with ``netflow_coverage_24h`` reporting that honestly. Implement
``NetflowProvider`` against whichever vendor you eventually choose; nothing
else has to change.
"""
from __future__ import annotations

from typing import Optional, Protocol

import numpy as np
import pandas as pd

from .config import Agent4Config
from .tape import rolling_z


class NetflowProvider(Protocol):
    name: str

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Return a frame indexed UTC with a ``netflow_usd`` column.

        POSITIVE = net INFLOW to exchanges (supply arriving to be sold).
        Vendors differ on this sign — check yours and normalise here rather
        than downstream, where a flip would be invisible.
        """
        ...


class NullNetflowProvider:
    """The default. No data, honestly reported."""

    name = "none"

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame(
            columns=["netflow_usd"],
            index=pd.DatetimeIndex([], tz="UTC", name="time"),
        )


def compute_netflow_features(
    bars: pd.DataFrame, netflow: Optional[pd.DataFrame], cfg: Agent4Config
) -> pd.DataFrame:
    idx = bars.index
    out = pd.DataFrame(index=idx)

    if netflow is None or netflow.empty or "netflow_usd" not in netflow.columns:
        out["exchange_netflow_z"] = np.nan
        out["exchange_netflow_direction"] = np.nan
        return out

    flow = pd.to_numeric(netflow["netflow_usd"], errors="coerce")
    out["exchange_netflow_z"] = rolling_z(flow, cfg.oi_z_window)
    # Inflow is bearish, so the bullish-positive direction is the NEGATIVE of
    # the flow's sign. Getting this backwards makes the model read incoming
    # supply as a buy signal.
    out["exchange_netflow_direction"] = -np.sign(flow).replace(0.0, np.nan)
    return out
