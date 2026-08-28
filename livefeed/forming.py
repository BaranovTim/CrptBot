"""The bar currently being built - the one every other path deliberately refuses.

WHY THIS IS SEPARATE FROM THE COLLECTOR
---------------------------------------
`BarStore.append` drops any bar whose close_time has not passed, and
`KlineCollector` never hands one out. That guard is load-bearing: storing a
forming bar makes live features differ from backtest features with nothing
raising, which is the exact divergence this project exists to prevent.

Nothing here weakens it. The forming bar is fetched, measured, and thrown
away. It is never written to the store, and every number derived from it is
marked provisional at the point it reaches a human.

WHAT IT IS FOR
--------------
On 1h bars the monitor is blind for up to 59 minutes. A 4% move at minute 10
is invisible until the hour closes, while the analysis on screen keeps
quoting an entry price that stopped existing forty minutes ago. This watches
that gap.

THE MEASUREMENT IS CHEAP. THE RE-READ IS NOT.
---------------------------------------------
One REST call per poll costs weight 1 - about 3 per minute against a 2400/min
budget, free in practice. Recomputing 107 features over 23,000 bars costs
~1.7 seconds. So the cheap thing runs on a timer and *gates* the expensive
thing: no spike, no recompute. That ordering is the whole reason this module
holds no model and imports no agent.

THRESHOLDS ARE DEFINITIONS, NOT PARAMETERS
------------------------------------------
`SpikeConfig` follows the same rule as every `Agent*Config`: these numbers
say what the word "spike" *means*. They are not to be tuned on trading
outcomes - the moment one is picked because it made money it is fitted, and
fitted things belong to Agent 5 where they can be cross-validated. Tune them
on how often you want to be interrupted, and nothing else.

WHY MOVES ARE MEASURED IN ATR, NEVER IN PERCENT
-----------------------------------------------
A 1% move is a yawn in one regime and a dislocation in another. Percent is
reported because humans read it, but every *threshold* is in ATR so that the
same config means the same thing in a calm week and a violent one.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple

import numpy as np
import pandas as pd

from core import utc_now
from marketdata.binance import FAPI_BASE, KLINE_COLUMNS, _to_utc

log = logging.getLogger(__name__)

SPOT_BASE = "https://api.binance.com"


# ------------------------------------------------------------ the bar
@dataclass(frozen=True)
class FormingBar:
    """A snapshot of the in-progress candle. Never stored, never trained on."""

    open_time: pd.Timestamp
    close_time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float = float("nan")
    number_of_trades: float = float("nan")
    taker_buy_base_volume: float = float("nan")

    def elapsed_fraction(self, now: Optional[pd.Timestamp] = None) -> float:
        """How far through this bar we are, 0..1."""
        now = now or utc_now()
        span = (self.close_time - self.open_time).total_seconds()
        if span <= 0:
            return float("nan")
        return float(np.clip((now - self.open_time).total_seconds() / span, 0.0, 1.0))

    @property
    def taker_buy_ratio(self) -> float:
        """Aggressive-buy share so far. 0.5 is balanced, >0.5 buyers crossing.

        This is the same quantity Agent 4 reads, computed on a partial bar -
        so it is noisier early in the bar, not wrong.
        """
        if not np.isfinite(self.volume) or self.volume <= 0:
            return float("nan")
        return float(self.taker_buy_base_volume / self.volume)

    def as_row(self) -> pd.DataFrame:
        """The bar as a one-row frame indexed by close_time, as if it had closed.

        Used ONLY to produce a provisional read. Feeding this to a model that
        was fitted on closed bars is exactly the thing this project refuses to
        do silently - so every caller must label the result provisional and
        must not persist it.
        """
        row = {"open_time": self.open_time, "open": self.open, "high": self.high,
               "low": self.low, "close": self.close, "volume": self.volume,
               "quote_volume": self.quote_volume,
               "number_of_trades": self.number_of_trades,
               "taker_buy_base_volume": self.taker_buy_base_volume,
               "taker_buy_quote_volume": float("nan")}
        df = pd.DataFrame([row], index=pd.DatetimeIndex([self.close_time],
                                                        name="close_time"))
        return df


# ------------------------------------------------------------- config
@dataclass
class SpikeConfig:
    """What counts as a spike. Definitions, not fitted parameters."""

    spike_atr: float = 0.75
    """Move from the last CLOSED price, in ATR.

    This MUST sit below the model's barrier distance (`k_up` / `k_dn`, both
    1.0 ATR as trained) or the alert stops being a warning and becomes a
    post-mortem: at 1.0 ATR the barrier has already been touched and the
    window is decided, so there is nothing left to warn about. 0.75 gives
    three quarters of the way there.

    Retraining with different barriers means revisiting this. `monitor.py`
    checks the two against each other at startup and says so rather than
    letting the mismatch pass quietly."""

    jolt_atr: float = 0.6
    """Move within `jolt_seconds`, in ATR. Catches a fast move that then
    retraces, which `spike_atr` alone would miss entirely."""

    jolt_seconds: int = 300

    volume_pace_mult: float = 3.0
    """Projected full-bar volume over the recent median. Volume arriving at
    3x the usual rate is worth surfacing even when price has not moved yet -
    that is often absorption, and it precedes the move rather than following
    it."""

    min_elapsed_for_pace: float = 0.05
    """Below this, projecting full-bar volume divides by a tiny number and
    reports nonsense. Three minutes of an hour."""

    rearm_atr: float = 0.5
    """Price must travel this much further (or back) before the same bar can
    alert again. Without it a genuine 3-ATR move alerts on every single poll
    and the screen becomes unreadable at exactly the moment it matters."""

    volume_baseline_bars: int = 168
    """Bars behind the volume median. A week of hours - long enough to be
    stable, short enough to track a changing regime."""


# -------------------------------------------------------------- event
@dataclass(frozen=True)
class Reading:
    """The state of the forming bar right now. Taken every poll, spike or not.

    Separating this from `Spike` means the live readout and the spike test
    are computed by the same code from the same snapshot. If they were
    computed separately they would drift, and the status line would
    eventually disagree with the alert printed next to it.
    """

    at: pd.Timestamp
    price: float
    anchor_close: float
    move_atr: float
    move_pct: float
    jolt_atr: float
    volume_pace: float
    taker_buy_ratio: float
    elapsed: float

    @property
    def direction(self) -> int:
        return int(np.sign(self.move_atr)) if np.isfinite(self.move_atr) else 0


@dataclass
class Spike:
    """A Reading that crossed a threshold. A measurement, not a verdict."""

    kinds: List[str]
    reading: Reading

    # the handful of fields callers actually render
    at = property(lambda self: self.reading.at)
    price = property(lambda self: self.reading.price)
    anchor_close = property(lambda self: self.reading.anchor_close)
    move_atr = property(lambda self: self.reading.move_atr)
    move_pct = property(lambda self: self.reading.move_pct)
    jolt_atr = property(lambda self: self.reading.jolt_atr)
    volume_pace = property(lambda self: self.reading.volume_pace)
    taker_buy_ratio = property(lambda self: self.reading.taker_buy_ratio)
    elapsed = property(lambda self: self.reading.elapsed)
    direction = property(lambda self: self.reading.direction)

    def describe(self) -> List[str]:
        way = "UP" if self.direction > 0 else "DOWN" if self.direction < 0 else "FLAT"
        L = [f"   {way} {self.move_pct:+.2f}%  ({self.move_atr:+.2f} ATR "
             f"from the {self.anchor_close:,.2f} close)",
             f"   price {self.price:,.2f}   bar {self.elapsed:.0%} elapsed   "
             f"triggered by {'+'.join(self.kinds)}"]
        if np.isfinite(self.jolt_atr) and abs(self.jolt_atr) >= 0.01:
            L.append(f"   last 5 min: {self.jolt_atr:+.2f} ATR")
        if np.isfinite(self.volume_pace):
            L.append(f"   volume arriving at {self.volume_pace:.1f}x the "
                     f"usual pace for this point in the bar")
        if np.isfinite(self.taker_buy_ratio):
            who = ("buyers crossing the spread"
                   if self.taker_buy_ratio > 0.55 else
                   "sellers crossing the spread"
                   if self.taker_buy_ratio < 0.45 else "balanced")
            L.append(f"   taker flow {self.taker_buy_ratio:.2f} - {who}")
        return L


# --------------------------------------------------------------- feed
class FormingBarFeed:
    """Fetches the in-progress bar. Holds no state beyond its own settings."""

    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1h",
                 market: str = "futures/um", timeout: int = 15):
        self.symbol, self.interval = symbol, interval
        self.market, self.timeout = market, timeout
        self.errors = 0

    def fetch(self, now: Optional[pd.Timestamp] = None) -> Optional[FormingBar]:
        """The bar whose close_time is still in the future, or None.

        Returns None on any network failure rather than raising: an intra-bar
        readout going quiet for a poll is a cosmetic problem, and the caller
        already has to survive the laptop's wifi dropping.
        """
        now = now or utc_now()
        base = (f"{FAPI_BASE}/fapi/v1/klines"
                if self.market.startswith("futures")
                else f"{SPOT_BASE}/api/v3/klines")
        url = (f"{base}?symbol={self.symbol}&interval={self.interval}&limit=2")
        req = urllib.request.Request(
            url, headers={"User-Agent": "TradingBot/forming"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError, ValueError) as e:
            self.errors += 1
            log.warning("forming-bar fetch failed: %s", e)
            return None

        if not payload:
            return None
        df = pd.DataFrame(payload, columns=KLINE_COLUMNS[:len(payload[0])])
        df["open_time"] = _to_utc(df["open_time"])
        df["close_time"] = _to_utc(df["close_time"])
        for c in ("open", "high", "low", "close", "volume", "quote_volume",
                  "number_of_trades", "taker_buy_base_volume"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # the forming one is whichever row has not closed yet. asking for the
        # last row instead would silently hand back a CLOSED bar in the second
        # or two around the boundary, and the caller would compare it against
        # itself and see a 0.00% move
        live = df[df["close_time"] > now]
        if live.empty:
            return None
        r = live.iloc[-1]
        return FormingBar(
            open_time=r["open_time"], close_time=r["close_time"],
            open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
            close=float(r["close"]), volume=float(r["volume"]),
            quote_volume=float(r.get("quote_volume", np.nan)),
            number_of_trades=float(r.get("number_of_trades", np.nan)),
            taker_buy_base_volume=float(r.get("taker_buy_base_volume", np.nan)))


# ------------------------------------------------------------ watcher
class SpikeWatcher:
    """Turns a stream of forming-bar snapshots into spike events.

    Pure measurement: it holds no model, imports no agent, and has no opinion
    about what a spike means for price. `SpikeWatcher` says "3.4 ATR down in
    six minutes". Whether that is a buy is Agent 5's question.
    """

    def __init__(self, cfg: Optional[SpikeConfig] = None):
        self.cfg = cfg or SpikeConfig()
        self._trail: Deque[Tuple[pd.Timestamp, float]] = deque()
        self._anchor_close: float = float("nan")
        self._anchor_time: Optional[pd.Timestamp] = None
        self._last_fired_move: Optional[float] = None

    def reset(self, anchor_time: pd.Timestamp, anchor_close: float) -> None:
        """Re-anchor on a newly closed bar. Clears the trail and the re-arm."""
        self._anchor_time = anchor_time
        self._anchor_close = float(anchor_close)
        self._trail.clear()
        self._last_fired_move = None

    # -- helpers -------------------------------------------------------
    def _jolt(self, now: pd.Timestamp, price: float, atr_price: float) -> float:
        cutoff = now - pd.Timedelta(seconds=self.cfg.jolt_seconds)
        while self._trail and self._trail[0][0] < cutoff:
            self._trail.popleft()
        if not self._trail:
            return 0.0
        return float((price - self._trail[0][1]) / atr_price)

    @staticmethod
    def volume_baseline(bars: pd.DataFrame, cfg: SpikeConfig) -> float:
        """Median full-bar volume over the recent past.

        Median rather than mean on purpose: volume has a heavy right tail, and
        one flush would drag a mean up far enough to hide the next one.
        """
        if "volume" not in bars.columns or bars.empty:
            return float("nan")
        v = bars["volume"].tail(cfg.volume_baseline_bars)
        v = v[v > 0]
        return float(v.median()) if len(v) else float("nan")

    def _pace(self, forming: FormingBar, elapsed: float, baseline: float) -> float:
        """Projected full-bar volume over the baseline.

        Assumes volume arrives evenly through the bar, which it does not -
        there is a real lull mid-bar and a rush around the close. So this
        reads slightly hot early and slightly cold late. It is a smoke alarm,
        not an instrument; the threshold is set well above the distortion.
        """
        if (not np.isfinite(baseline) or baseline <= 0
                or elapsed < self.cfg.min_elapsed_for_pace):
            return float("nan")
        return float((forming.volume / elapsed) / baseline)

    # -- measure -------------------------------------------------------
    def read(self, forming: FormingBar, atr_price: float,
             volume_baseline: float = float("nan"),
             now: Optional[pd.Timestamp] = None) -> Optional[Reading]:
        """Measure the forming bar. No thresholds, no side effects worth
        naming - it only prunes the stale tail of its own trail.

        Returns None when the measurement would be meaningless: no usable
        ATR to normalise by, or no anchor yet.
        """
        now = now or utc_now()
        if not np.isfinite(atr_price) or atr_price <= 0:
            return None
        if not np.isfinite(self._anchor_close) or self._anchor_close <= 0:
            return None

        price = forming.close
        elapsed = forming.elapsed_fraction(now)
        return Reading(
            at=now, price=price, anchor_close=self._anchor_close,
            move_atr=float((price - self._anchor_close) / atr_price),
            move_pct=float((price / self._anchor_close - 1.0) * 100.0),
            jolt_atr=self._jolt(now, price, atr_price),
            volume_pace=self._pace(forming, elapsed, volume_baseline),
            taker_buy_ratio=forming.taker_buy_ratio, elapsed=elapsed)

    # -- decide --------------------------------------------------------
    def observe(self, forming: FormingBar, atr_price: float,
                volume_baseline: float = float("nan"),
                now: Optional[pd.Timestamp] = None
                ) -> Tuple[Optional[Reading], Optional[Spike]]:
        """Record a snapshot. Returns (reading, spike) - spike is None unless
        a threshold was crossed AND the re-arm allows it."""
        r = self.read(forming, atr_price, volume_baseline, now)
        if r is None:
            return None, None
        self._trail.append((r.at, r.price))

        kinds: List[str] = []
        if abs(r.move_atr) >= self.cfg.spike_atr:
            kinds.append("MOVE")
        if abs(r.jolt_atr) >= self.cfg.jolt_atr:
            kinds.append("JOLT")
        if np.isfinite(r.volume_pace) and r.volume_pace >= self.cfg.volume_pace_mult:
            kinds.append("VOLUME")
        if not kinds:
            return r, None

        # re-arm, keyed on the price move, so one violent candle produces a
        # handful of alerts as it develops rather than one every single poll.
        # without this the screen becomes unreadable at exactly the moment it
        # matters most
        if (self._last_fired_move is not None
                and abs(r.move_atr - self._last_fired_move) < self.cfg.rearm_atr):
            return r, None
        self._last_fired_move = r.move_atr
        return r, Spike(kinds=kinds, reading=r)
