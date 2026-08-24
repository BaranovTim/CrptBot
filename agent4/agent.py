"""Agent 4 — order flow and positioning.

The fourth thermometer, and the one the plan expects to matter most.

Agents 1 and 2 read price. Order flow is the *cause* of which price is the
effect, so this is the first block that reads something upstream of the thing
it is trying to predict. That is the argument for expecting more from it than
from chart geometry — and it is a hypothesis to be measured, not a promise.

The plan calls this "a data-buying problem, not a modelling one", and the
design takes that literally by splitting the inputs into three tiers:

  free, already downloaded   taker_buy_ratio, trade counts, avg trade size —
                             these ride inside every kline. Agents 1 and 2
                             were kept away from them precisely so this block
                             could claim them in the ablation.
  free, needs downloading    aggTrades (the tape) and open-interest metrics.
                             Large prints, size distribution and crowding all
                             come from here.
  paid                       exchange netflow. Not bundled. Measure whether
                             the free tiers earn their place first.

Every tier degrades to NaN independently, and two coverage columns report
which tiers were actually available — so "the whales were quiet" and "we did
not have the data" never collapse into the same number.

Reads tape, open interest, liquidations, netflow, and the order-flow columns
inside klines. Not candle geometry (Agent 1), not classic indicators
(Agent 2), not news (Agent 3), and not funding rate or realized-volatility
percentile, which belong to the regime block.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from marketdata.aggtrades import align_tape, empty_tape_bars

from .config import DEFAULT_CONFIG, Agent4Config
from .netflow import NetflowProvider, NullNetflowProvider, compute_netflow_features
from .positioning import compute_positioning_features
from .schema import COVERAGE_COLUMNS, FEATURE_COLUMNS, validate_features
from .tape import compute_tape_features, kline_flow_fallback
from core import REQUIRED_OHLCV, check_bars


@dataclass
class FlowOutput:
    timestamp: pd.Timestamp
    features: Dict[str, float]
    trace: List[str] = field(default_factory=list)

    def as_row(self) -> pd.DataFrame:
        return pd.DataFrame([self.features], index=[self.timestamp])[list(FEATURE_COLUMNS)]

    def __str__(self) -> str:
        head = f"[{self.timestamp}] Agent 4"
        lines = [head, "-" * len(head)]
        lines += ["  " + t for t in self.trace] or ["  (no flow data)"]
        return "\n".join(lines)


class FlowAgent:
    def __init__(
        self,
        config: Agent4Config = DEFAULT_CONFIG,
        netflow_provider: Optional[NetflowProvider] = None,
    ):
        self.cfg = config
        self.netflow_provider = netflow_provider or NullNetflowProvider()

    @property
    def warmup_bars(self) -> int:
        """Bars before the rolling baselines are trustworthy.

        Dominated by the large-print threshold window and the open-interest
        z-score. Below this the threshold has not stabilised, so large-print
        columns are NaN rather than wrong.
        """
        return max(self.cfg.print_threshold_bars, self.cfg.oi_z_window,
                   self.cfg.large_print_z_window) + self.cfg.divergence_window

    def compute(
        self,
        bars: pd.DataFrame,
        tape: Optional[pd.DataFrame] = None,
        open_interest: Optional[pd.DataFrame] = None,
        liquidations: Optional[pd.DataFrame] = None,
        netflow: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """Flow features for every bar, indexed by close_time."""
        # Same check as the other agents. Volume and the taker-buy column are
        # what the kline fallback runs on, so a frame without them would
        # quietly produce an all-NaN flow block instead of failing.
        check_bars(bars, REQUIRED_OHLCV, who="Agent 4")

        have_tape = tape is not None and not tape.empty
        aligned = align_tape(tape, bars.index) if have_tape \
            else empty_tape_bars(bars.index)

        flow = compute_tape_features(bars, aligned, self.cfg)
        if not have_tape:
            # Without the tape the histogram is all zeros, which would make
            # every derived column a confident zero. Blank them instead —
            # the coverage column is what carries the information.
            for c in flow.columns:
                flow[c] = np.nan

        kline = kline_flow_fallback(bars, self.cfg)
        pos = compute_positioning_features(bars, open_interest, liquidations, self.cfg)
        net = compute_netflow_features(bars, netflow, self.cfg)

        out = pd.concat([flow, kline, pos, net], axis=1)

        w = self.cfg.coverage_window
        tape_seen = (aligned["buy_notional"] + aligned["sell_notional"]) > 0
        out["tape_coverage_24h"] = tape_seen.rolling(w, min_periods=1).mean() \
            if have_tape else pd.Series(0.0, index=bars.index)
        net_seen = out["exchange_netflow_z"].notna() if "exchange_netflow_z" in out \
            else pd.Series(False, index=bars.index)
        out["netflow_coverage_24h"] = net_seen.rolling(w, min_periods=1).mean()

        out = out[list(FEATURE_COLUMNS)].replace([np.inf, -np.inf], np.nan)
        for c in COVERAGE_COLUMNS:
            out[c] = out[c].fillna(0.0)
        return validate_features(out)

    def latest(self, bars: pd.DataFrame, **kwargs) -> FlowOutput:
        features = self.compute(bars, **kwargs)
        row = features.iloc[-1]
        # Which feeds were actually supplied. Without this the trace cannot
        # tell "the feed is missing" from "this bar was quiet", and those are
        # the two states the whole coverage design exists to separate.
        present = {name: (kwargs.get(name) is not None
                          and not kwargs[name].empty)
                   for name in ("tape", "open_interest", "liquidations", "netflow")}
        return FlowOutput(
            timestamp=features.index[-1],
            features={k: float(row[k]) for k in FEATURE_COLUMNS},
            trace=self._build_trace(row, present),
        )

    # -- trace -------------------------------------------------------------
    def _build_trace(self, row: pd.Series,
                     present: Optional[Dict[str, bool]] = None) -> List[str]:
        present = present or {}
        out: List[str] = []

        def has(k):
            return not pd.isna(row[k])

        cov = row["tape_coverage_24h"]
        if cov < 1.0:
            out.append(
                f"tape coverage {cov:.0%} over 24h"
                + (" — no aggTrades loaded; running on kline order flow only"
                   if cov == 0 else " — gaps in the tape")
            )

        if has("taker_buy_ratio"):
            r = row["taker_buy_ratio"]
            side = "buyers" if r > 0.5 else "sellers"
            out.append(f"taker buy ratio {r:.1%} — {side} aggressing "
                       f"(50% is balanced)")
        if has("aggressor_imbalance"):
            out.append(f"bar aggressor imbalance {row['aggressor_imbalance']:+.2f}")
        if has("ofi_z"):
            out.append(f"order flow imbalance {row['ofi_z']:+.1f} sd")
        if has("cvd_slope_z"):
            out.append(f"CVD slope {row['cvd_slope_z']:+.1f} sd")
        if has("flow_price_corr_20") and row["flow_price_corr_20"] < -0.4:
            out.append(f"flow/price correlation {row['flow_price_corr_20']:+.2f} "
                       f"— price moving against aggressive flow (divergence)")

        if has("large_print_imbalance_1h"):
            out.append(
                f"large prints {row['large_print_imbalance_1h']:+.2f} imbalance, "
                f"{row['large_print_volume_share']:.0%} of bar volume"
                if has("large_print_volume_share") else
                f"large prints {row['large_print_imbalance_1h']:+.2f} imbalance"
            )
        if has("bars_since_large_print"):
            out.append(f"last large print {int(row['bars_since_large_print'])} bars ago")
        if has("max_print_usd_z") and abs(row["max_print_usd_z"]) > 1.5:
            out.append(f"biggest fill this bar {row['max_print_usd_z']:+.1f} sd vs baseline")
        if has("avg_trade_size_usd_z") and abs(row["avg_trade_size_usd_z"]) > 1.5:
            kind = "larger" if row["avg_trade_size_usd_z"] > 0 else "smaller"
            out.append(f"average fill size {row['avg_trade_size_usd_z']:+.1f} sd "
                       f"({kind} than usual — size composition has shifted)")

        if has("oi_change_pct"):
            s = f"open interest {row['oi_change_pct']:+.2%}"
            if has("oi_change_z"):
                s += f" ({row['oi_change_z']:+.1f} sd)"
            out.append(s)
        if has("oi_price_divergence") and row["oi_price_divergence"] < -0.4:
            out.append(f"OI/price correlation {row['oi_price_divergence']:+.2f} "
                       f"— open interest building as price falls (new shorts)")
        if has("liq_imbalance_1h"):
            side = "shorts" if row["liq_imbalance_1h"] > 0 else "longs"
            out.append(f"liquidations {row['liq_imbalance_1h']:+.2f} — {side} "
                       f"being force-closed")
        elif present.get("liquidations"):
            out.append("liquidation feed present, but none in this bar")
        else:
            out.append("no liquidation feed (Binance restricts the history)")

        if has("exchange_netflow_z"):
            out.append(f"exchange netflow {row['exchange_netflow_z']:+.1f} sd, "
                       f"direction {row['exchange_netflow_direction']:+.0f} "
                       f"(+1 = coins leaving exchanges)")
        elif present.get("netflow"):
            out.append("netflow feed present, but no reading for this bar")
        else:
            out.append("no exchange netflow (paid feed; not configured)")

        if not present.get("open_interest") and not has("oi_change_pct"):
            out.append("no open interest feed")

        return out
