"""Agent 2 — the indicator analyser.

The second thermometer.  Like Agent 1 it measures and does not judge, and it
carries the same four properties: pure, untrained, blind to the future,
opinionless.

What is different about Agent 2 is worth stating plainly, because it changes
what you should expect from it.

Indicators are lagged transforms of price.  RSI is a smoothed ratio of recent
up moves to down moves; MACD is the difference of two smoothed prices.  They
contain no information that is not already in the candles Agent 1 also reads
— they are a re-encoding, not a new source.  So the honest expectation is
that this block adds little once order flow is present, because order flow is
the cause and price is the effect.

That is not an argument for skipping it.  It is the argument for building it
cheaply and measuring it: Agent 2 is iteration 2 of the ablation, the first
block tested against the regime-only floor, and "indicators did not beat the
floor" is a genuinely useful result that costs one afternoon to obtain.

Reads OHLCV.  Not the order book, not funding, not news.  Volume enters only
through classic indicators (MFI, CMF, OBV) — never through
``taker_buy_volume``, ``number_of_trades`` or ``quote_volume``, which are
Agent 4's order-flow territory.  Blur that line and the ablation can no
longer tell you which block earned the lift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import DEFAULT_CONFIG, Agent2Config
from .htf import compute_htf
from .schema import FEATURE_COLUMNS, validate_features

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass
class IndicatorOutput:
    """Two channels, kept apart for the same reason as in Agent 1.

    ``features`` goes to the model.  ``trace`` goes to logs and dashboards and
    must never reach the model — an explanation that can influence the
    prediction cannot be validated independently of it.
    """

    timestamp: pd.Timestamp
    features: Dict[str, float]
    trace: List[str] = field(default_factory=list)

    def as_row(self) -> pd.DataFrame:
        return pd.DataFrame([self.features], index=[self.timestamp])[list(FEATURE_COLUMNS)]

    def __str__(self) -> str:
        head = f"[{self.timestamp}] Agent 2"
        lines = [head, "-" * len(head)]
        lines += ["  " + t for t in self.trace] or ["  (nothing notable)"]
        return "\n".join(lines)


def _validate_bars(bars: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"bars is missing required columns: {missing}")
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise TypeError(
            "bars must be indexed by close_time as a DatetimeIndex. Indexing by "
            "open_time makes the bar's own close visible at its open."
        )
    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars index must be sorted ascending")
    if bars.index.has_duplicates:
        raise ValueError("bars index contains duplicate timestamps")
    return bars


class IndicatorAgent:
    def __init__(self, config: Agent2Config = DEFAULT_CONFIG):
        self.cfg = config

    @property
    def warmup_bars(self) -> int:
        """Base-timeframe rows whose values are not yet trustworthy.

        Dominated by the 200-bar EMA and the OBV z-score window; a shorter
        warmup leaves the first few hundred rows of every training window
        quietly degraded.

        This covers the base timeframe only. For a window that also satisfies
        the higher-timeframe block, use ``required_bars(bars)`` — with a
        coarse HTF that number is far larger.
        """
        cfg = self.cfg
        return max(
            cfg.ema_baseline,
            cfg.obv_z_window + cfg.obv_diff,
            cfg.ema_slow + cfg.macd_signal,
        ) + cfg.slope_lookback

    @property
    def htf_warmup_bars(self) -> int:
        """Higher-timeframe bars needed before the HTF block yields values."""
        cfg = self.cfg
        return max(cfg.ema_slow + cfg.macd_signal, cfg.rsi_period, cfg.atr_period) + 5

    def required_bars(self, bars: pd.DataFrame) -> int:
        """Minimum window length for every column to be computable.

        The higher-timeframe block needs ``htf_warmup_bars`` HTF bars, which
        is that many multiples of the base interval. A 1d HTF over hourly
        candles needs ~960 base bars, not the ~210 that ``warmup_bars``
        reports.

        This matters operationally, not just theoretically. Feed a live
        window shorter than this and the HTF columns come back NaN while the
        backtest — run over full history — had them populated. That is a
        backtest/live divergence produced entirely by window sizing.
        """
        from .htf import infer_interval

        base = self.warmup_bars
        if len(bars) < 3:
            return base
        interval = infer_interval(bars.index)
        try:
            htf_td = pd.Timedelta(pd.tseries.frequencies.to_offset(self.cfg.htf_rule))
        except (ValueError, TypeError):
            return base                      # non-fixed frequency; cannot convert
        if interval <= pd.Timedelta(0) or htf_td <= pd.Timedelta(0):
            return base
        ratio = max(1, int(np.ceil(htf_td / interval)))
        return int(max(base, self.htf_warmup_bars * ratio))

    # -- feature construction ---------------------------------------------
    def _compute_frame(self, bars: pd.DataFrame) -> pd.DataFrame:
        cfg = self.cfg
        o, h, l = bars["open"], bars["high"], bars["low"]
        c, v = bars["close"], bars["volume"]

        a = ind.atr(h, l, c, cfg.atr_period)

        # --- trend --------------------------------------------------------
        ema_f = ind.ema(c, cfg.ema_fast)
        ema_s = ind.ema(c, cfg.ema_slow)
        ema_base = ind.ema(c, cfg.ema_baseline)
        ema_sl = ind.ema(c, cfg.ema_slope)
        macd_line, macd_sig, macd_hist = ind.macd(
            c, cfg.ema_fast, cfg.ema_slow, cfg.macd_signal
        )
        adx_v, plus_di, minus_di = ind.adx(h, l, c, cfg.adx_period)

        # --- momentum -----------------------------------------------------
        rsi_v = ind.rsi(c, cfg.rsi_period)
        stoch_k, stoch_d = ind.stochastic(h, l, c, cfg.stoch_k, cfg.stoch_d)

        # --- volatility ---------------------------------------------------
        bb_up, bb_mid, bb_lo = ind.bollinger(c, cfg.bb_period, cfg.bb_std)
        kc_up, _, kc_lo = ind.keltner(h, l, c, cfg.kc_period, cfg.kc_mult)
        tr = ind.true_range(h, l, c)

        # --- volume -------------------------------------------------------
        obv_v = ind.obv(c, v)
        obv_change = obv_v.diff(cfg.obv_diff)

        # --- mean reversion / divergence ----------------------------------
        sma_z = ind.sma(c, cfg.zscore_period)
        sd_z = c.rolling(cfg.zscore_period, min_periods=cfg.zscore_period).std(ddof=0)
        # Divergence expressed as a continuous correlation rather than a
        # boolean pattern match.  A boolean would need swing detection, which
        # is Agent 1's machinery — importing it here would make the two
        # blocks impossible to ablate apart.  Correlation near -1 means price
        # and RSI have been pulling in opposite directions.
        rsi_corr = c.rolling(cfg.divergence_window,
                             min_periods=cfg.divergence_window).corr(rsi_v)
        since_cross, cross_dir = ind.bars_since_sign_change(macd_hist)

        frame = pd.DataFrame(
            {
                "ema_spread_atr": ind.safe_div(ema_f - ema_s, a),
                "price_vs_ema200_atr": ind.safe_div(c - ema_base, a),
                "ema50_slope_atr": ind.safe_div(
                    ema_sl - ema_sl.shift(cfg.slope_lookback), a
                ),
                "adx_14": adx_v,
                "di_spread_14": plus_di - minus_di,
                "macd_hist_atr": ind.safe_div(macd_hist, a),

                "rsi_14": rsi_v,
                "rsi_slope_3": rsi_v - rsi_v.shift(cfg.rsi_slope_lookback),
                "stoch_k_14": stoch_k,
                "stoch_kd_spread": stoch_k - stoch_d,
                "cci_20": ind.cci(h, l, c, cfg.cci_period),
                "roc_10_atr": ind.safe_div(c - c.shift(cfg.roc_period), a),

                "atr_pct": ind.safe_div(a, c),
                "tr_vs_atr": ind.safe_div(tr, a),
                "bb_position": ind.safe_div(c - bb_lo, bb_up - bb_lo),
                "bb_width_atr": ind.safe_div(bb_up - bb_lo, a),
                "bb_kc_ratio": ind.safe_div(bb_up - bb_lo, kc_up - kc_lo),

                "mfi_14": ind.mfi(h, l, c, v, cfg.mfi_period),
                "cmf_20": ind.cmf(h, l, c, v, cfg.cmf_period),
                "obv_slope_z": ind.rolling_z(obv_change, cfg.obv_z_window),

                "zscore_close_20": ind.safe_div(c - sma_z, sd_z),
                "rsi_price_corr_20": rsi_corr,
                "bars_since_macd_cross": since_cross,
                "macd_cross_direction": cross_dir,
            },
            index=bars.index,
        )

        htf = compute_htf(bars, cfg)
        out = pd.concat([frame, htf], axis=1)[list(FEATURE_COLUMNS)]
        return validate_features(out.replace([np.inf, -np.inf], np.nan))

    # -- public ------------------------------------------------------------
    def compute(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Indicator features for every bar, indexed by close_time."""
        return self._compute_frame(_validate_bars(bars))

    def latest(self, bars: pd.DataFrame) -> IndicatorOutput:
        bars = _validate_bars(bars)
        features = self._compute_frame(bars)
        row = features.iloc[-1]
        return IndicatorOutput(
            timestamp=features.index[-1],
            features={k: float(row[k]) for k in FEATURE_COLUMNS},
            trace=self._build_trace(row),
        )

    # -- trace -------------------------------------------------------------
    def _build_trace(self, row: pd.Series) -> List[str]:
        out: List[str] = []

        def has(k: str) -> bool:
            return not pd.isna(row[k])

        if has("adx_14"):
            strength = ("no trend" if row["adx_14"] < 20 else
                        "trending" if row["adx_14"] < 40 else "strongly trending")
            bias = ""
            if has("di_spread_14"):
                bias = f", {'bulls' if row['di_spread_14'] > 0 else 'bears'} in control"
            out.append(f"ADX {row['adx_14']:.0f} — {strength}{bias}")

        if has("price_vs_ema200_atr"):
            d = row["price_vs_ema200_atr"]
            out.append(f"price {abs(d):.2f} ATR {'above' if d > 0 else 'below'} the 200 EMA")

        if has("macd_hist_atr"):
            s = f"MACD histogram {row['macd_hist_atr']:+.2f} ATR"
            if has("bars_since_macd_cross") and has("macd_cross_direction"):
                d = "bullish" if row["macd_cross_direction"] > 0 else "bearish"
                s += f", last {d} cross {int(row['bars_since_macd_cross'])} bars ago"
            out.append(s)

        if has("rsi_14"):
            zone = ("oversold" if row["rsi_14"] < 30 else
                    "overbought" if row["rsi_14"] > 70 else "neutral")
            slope = ""
            if has("rsi_slope_3"):
                slope = f", {row['rsi_slope_3']:+.1f} over 3 bars"
            out.append(f"RSI {row['rsi_14']:.1f} ({zone}){slope}")

        if has("rsi_price_corr_20") and row["rsi_price_corr_20"] < -0.5:
            out.append(
                f"price/RSI correlation {row['rsi_price_corr_20']:+.2f} over 20 bars "
                f"— divergence forming"
            )

        if has("stoch_k_14"):
            out.append(f"Stochastic %K {row['stoch_k_14']:.0f}, "
                       f"%K-%D {row['stoch_kd_spread']:+.1f}")

        if has("bb_position"):
            where = ("above the upper band" if row["bb_position"] > 1 else
                     "below the lower band" if row["bb_position"] < 0 else
                     f"{row['bb_position']:.0%} across the bands")
            out.append(f"Bollinger: {where}")
        if has("bb_kc_ratio") and row["bb_kc_ratio"] < 1.0:
            out.append(f"Bollinger inside Keltner (ratio {row['bb_kc_ratio']:.2f}) "
                       f"— volatility squeeze")

        if has("atr_pct"):
            s = f"ATR {row['atr_pct']:.2%} of price"
            if has("tr_vs_atr"):
                s += f", this bar's range {row['tr_vs_atr']:.1f}x ATR"
            out.append(s)

        if has("mfi_14"):
            out.append(f"MFI {row['mfi_14']:.0f}, CMF {row['cmf_20']:+.2f}")
        if has("obv_slope_z") and abs(row["obv_slope_z"]) > 1.5:
            out.append(f"OBV {row['obv_slope_z']:+.1f} sd — unusual "
                       f"{'accumulation' if row['obv_slope_z'] > 0 else 'distribution'}")

        if has("zscore_close_20") and abs(row["zscore_close_20"]) > 1.5:
            out.append(f"price {row['zscore_close_20']:+.1f} sd from its 20-bar mean")

        if has("rsi_14_4h"):
            out.append(f"4h context: RSI {row['rsi_14_4h']:.0f}, "
                       f"MACD hist {row['macd_hist_atr_4h']:+.2f} ATR")

        return out
