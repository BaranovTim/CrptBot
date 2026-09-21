#!/usr/bin/env python3
"""Rolling two-bar monitor: up/down odds, entry, exit time, TP and SL.

    python3 monitor.py                 # live, follows the collector
    python3 monitor.py --once          # print one screen and exit
    python3 monitor.py --replay 40     # replay the last 40 stored bars

WHAT IT SHOWS
-------------
Two overlapping analyses are alive at any moment, each spanning two bars:

    bar   N     N+1   N+2   N+3
    A     |-----------|                 started at N-1 close, 1 bar left
    B           |-----------|           started at N   close, 2 bars left

When a bar closes, A resolves, B moves to "1 bar left", and a fresh C starts.
They share their middle bar, which is what you asked for.

WHY TWO MODELS
--------------
A model trained with max_hold_bars=2 answers "within the next 2 bars". Once
one of those bars has closed, the remaining question is a 1-bar question -
a different question, and reading the 2-bar model there would be wrong. So
analysis B (2 bars left) uses the h2 model and analysis A (1 bar left) uses
the h1 model. Both are trained with SYMMETRIC barriers, which is what makes
"chance down" equal to 1 - "chance up" honestly; with a 2:1 payoff it would
not.

WHAT "ENTER NOW OR WAIT" CAN AND CANNOT MEAN
--------------------------------------------
It compares the expected value available NOW against the threshold. It does
NOT forecast that a better entry is coming - nothing here can see the next
bar's features before that bar exists. "WAIT" means "this is not worth
trading at the moment, and it will be re-read when the next bar closes".
Any tool that claims to know a better price is coming is guessing.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import config as project_config
from core import utc_now

BOX = "=" * 72
DASH = "-" * 72


# --------------------------------------------------------------- news
@dataclass
class NewsWatcher:
    """Agent 3, watching for anything new since the last screen."""

    asset: str = "BTC"
    # injectable so tests can point at a temporary store instead of the real
    # one - and so a caller can watch a different corpus without patching
    store: object = None
    _seen: set = field(default_factory=set)
    _primed: bool = False

    def poll(self) -> List[dict]:
        """New items since last call, already scored. Empty on the first call."""
        from agent3 import Agent3Config, NewsAgent
        from newsfeed.store import JSONLNewsStore

        try:
            store = self.store if self.store is not None else JSONLNewsStore()
            items = store.load_items()
        except Exception:
            return []
        if not items:
            return []

        fresh = [i for i in items if i.id not in self._seen]
        for i in items:
            self._seen.add(i.id)

        # the first poll only learns what already exists - announcing a
        # three-day-old headline as breaking news would be nonsense
        if not self._primed:
            self._primed = True
            return []
        if not fresh:
            return []

        agent = NewsAgent(Agent3Config(asset=self.asset))
        scores = agent.score_items(fresh)
        out = []
        for item in fresh:
            sc = scores.get(item.id)
            if sc is None:
                continue
            out.append({"headline": item.headline, "source": item.source,
                        "direction": sc.direction, "magnitude": sc.magnitude,
                        "novelty": sc.novelty, "score": sc})
        return out


def news_verdict(direction: float, magnitude: float) -> Tuple[str, str]:
    """(impact label, trade side) from a scored item."""
    if magnitude < 0.15:
        return "NO IMPACT", "NO ACTION"
    if magnitude < 0.35:
        return "SMALL IMPACT", "NO ACTION"
    if direction > 0.15:
        return "BULLISH", "BUY"
    if direction < -0.15:
        return "BEARISH", "SELL"
    return "SMALL IMPACT", "NO ACTION"


# -------------------------------------------------------------- whales
@dataclass
class WhaleWatcher:
    """Watches SEC filings for insiders and treasuries actually trading.

    Polled on a slow timer in a background thread. Filings are not a
    high-frequency feed - a few per day across the whole watchlist - and an
    8-second EDGAR sweep must not stall the bar countdown.
    """

    min_proximity: float = 0.55
    min_usd: float = 100_000.0
    poll_seconds: int = 900
    store: object = None
    _seen: set = field(default_factory=set)
    _primed: bool = False
    _pending: list = field(default_factory=list)
    _lock: object = None
    _thread: object = None
    _stop: bool = False

    def start(self) -> None:
        """Begin polling in the background. Safe to call once."""
        import threading

        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        import time as _t
        while not self._stop:
            try:
                found = self._fetch()
                if found:
                    with self._lock:
                        self._pending.extend(found)
            except Exception:            # noqa: BLE001 - never kill the monitor
                pass
            for _ in range(self.poll_seconds):
                if self._stop:
                    return
                _t.sleep(1)

    def _fetch(self) -> List:
        from whalefeed import EdgarSource, WhaleStore, entities

        src = EdgarSource(entities=entities(min_proximity=self.min_proximity),
                          since_days=14, max_filings_per_entity=3)
        events = src.fetch()
        store = self.store if self.store is not None else WhaleStore()
        store.append(events)

        fresh = [e for e in events if e.id not in self._seen]
        for e in events:
            self._seen.add(e.id)

        # first sweep only learns what already exists - announcing a filing
        # from last week as if it just happened would be nonsense
        if not self._primed:
            self._primed = True
            return []
        # size floor: a $900 award is technically a transaction and is noise
        return [e for e in fresh if e.amount_usd >= self.min_usd]

    def drain(self) -> List:
        """Take whatever the background thread has found since last call."""
        if self._lock is None:
            return []
        with self._lock:
            out, self._pending = self._pending, []
        return out

    def stop(self) -> None:
        self._stop = True


def whale_verdict(event) -> Tuple[str, str]:
    """(impact label, side) for one insider or treasury transaction.

    Two things have to be true before this counts as a signal at all:

      CONVICTION  the transaction code must reflect a decision. An option
                  exercise or a tax withholding is mechanical - the largest
                  filing in a typical week is usually code F, which is
                  shares sold automatically to cover tax on vesting and
                  says nothing whatsoever about anyone's view.

      PROXIMITY   the entity's trading must bear on CRYPTO. A Coinbase
                  officer selling COIN is a statement about COIN equity,
                  not about bitcoin.
    """
    conviction = event.conviction
    proximity = float(event.meta.get("crypto_proximity", 0.5))

    if conviction <= 0.05:
        return "MECHANICAL - NOT A VIEW", "NO ACTION"
    weight = conviction * proximity
    if weight < 0.25 or event.amount_usd < 250_000:
        return "SMALL IMPACT", "NO ACTION"
    if event.action == "BUY":
        return ("STRONG BULLISH" if weight >= 0.5 else "BULLISH"), "BUY"
    return ("STRONG BEARISH" if weight >= 0.5 else "BEARISH"), "SELL"


def connection_hint(errors: int, last_ok) -> str:
    """Plain words for what is otherwise a cryptic OS error.

    urllib surfaces a DNS failure as "nodename nor servname provided, or not
    known", which reads like a bug in this program. It is almost always the
    laptop's wifi dropping or the machine waking from sleep.
    """
    if errors <= 0:
        return ""
    since = ""
    if last_ok is not None:
        mins = int((utc_now() - last_ok).total_seconds() // 60)
        since = f", last success {mins}m ago"
    return f"OFFLINE - no network ({errors} failed poll{'s' if errors > 1 else ''}{since})"


# ----------------------------------------------------------- analysis
LIVE_READOUT_COLUMNS = ("rsi_14", "atr_pct", "trend_direction",
                        "trend_direction_4h", "vol_pctile",
                        "volume_vs_baseline", "taker_buy_ratio")


def indicator_snapshot(X: Optional[pd.DataFrame]) -> Dict[str, float]:
    """The closed-bar indicator values worth showing beside a live price.

    Read straight out of the CACHED feature frame, so they cost nothing and
    change once per bar. They describe the last CLOSED bar, never the
    forming one - which is the only honest thing to put next to a moving
    price, and the reason the status line labels the two halves apart.
    """
    if X is None or len(X) == 0:
        return {}
    row = X.iloc[-1]
    return {c: float(row[c]) for c in LIVE_READOUT_COLUMNS if c in X.columns}


def atr_price_estimate(bars: pd.DataFrame, X: Optional[pd.DataFrame],
                       period: int = 14) -> float:
    """ATR in price units for the newest closed bar.

    Prefers Agent 2's `atr_pct`, which is already computed and cached. Falls
    back to computing it here because a model trained WITHOUT the Agent 2
    block does not carry that column, and then every intra-bar threshold
    would divide by NaN and the watch would silently never fire. A feature
    that quietly does nothing is worse than one that is switched off.
    """
    if X is not None and "atr_pct" in X.columns:
        v = float(X["atr_pct"].iloc[-1])
        if np.isfinite(v) and v > 0:
            return v * float(bars["close"].iloc[-1])
    prev = bars["close"].shift(1)
    tr = pd.concat([bars["high"] - bars["low"],
                    (bars["high"] - prev).abs(),
                    (bars["low"] - prev).abs()], axis=1).max(axis=1)
    v = float(tr.tail(period).mean())
    return v if np.isfinite(v) and v > 0 else float("nan")


def heartbeat(remaining_s: float, reading, snap: Dict[str, float],
              last_seen: pd.Timestamp, watching: str, offline: str) -> str:
    """The one-line live readout between bar closes.

    Everything from `reading` describes the FORMING bar and moves second to
    second. Everything from `snap` describes the last CLOSED bar and is
    frozen until the next close. Keeping them distinguishable matters: an
    RSI printed next to a live price invites you to read it as current, and
    it is not - it is the RSI of a bar that finished some time ago.
    """
    mm, ss = divmod(max(0, int(remaining_s)), 60)
    parts = [f"  {mm:02d}:{ss:02d} to close"]
    if reading is not None:
        parts.append(f"{reading.price:,.2f}")
        parts.append(f"{reading.move_pct:+.2f}%")
        parts.append(f"{reading.move_atr:+.2f}atr")
        if np.isfinite(reading.volume_pace):
            parts.append(f"vol {reading.volume_pace:.1f}x")
        if np.isfinite(reading.taker_buy_ratio):
            parts.append(f"tkr {reading.taker_buy_ratio:.2f}")
    else:
        parts.append(f"(last {last_seen:%H:%M} UTC)")
    if np.isfinite(snap.get("rsi_14", np.nan)):
        parts.append(f"rsi {snap['rsi_14']:.0f}")     # last CLOSED bar
    if np.isfinite(snap.get("trend_direction_4h", np.nan)):
        parts.append("4h " + ("up" if snap["trend_direction_4h"] > 0 else "dn"))
    parts.append(f"{offline} - retrying" if offline else f"watching {watching}...")
    return "   ".join(parts)


@dataclass
class Analysis:
    """One two-bar window, re-read as each of its bars closes."""

    name: str
    opened_at: pd.Timestamp        # bar close that started it
    ends_at: pd.Timestamp          # bar close where its window finishes
    bars_left: int

    p_up: float = float("nan")

    # The probability this timeframe would need for an entry to be worth its
    # costs, so "FLAT" is a measurement rather than a mood. Follows from the
    # barrier span and the fixed round-trip fee - see `evaluate`.
    p_needed: float = float("nan")

    # Which sensitivity this entry qualifies for: strong | medium | small,
    # or "" when the expected value does not clear breakeven. The app shows
    # or withholds the call based on the level the user picked.
    strength: str = ""

    entry: float = float("nan")
    tp_price: float = float("nan")
    sl_price: float = float("nan")
    ev_long: float = float("nan")
    ev_short: float = float("nan")
    action: str = "WAIT"
    reason: str = ""
    diagnostic: str = ""
    size_pct: float = 0.0

    # the barriers BEFORE the short flip, so resolution() does not have to
    # know which way round the trade is, and "" when no side was proposed
    upper: float = float("nan")
    lower: float = float("nan")
    side: str = ""
    # Structure models decide by RANK, not by expected value: where this
    # bar's score sits among the last TRAIL_BARS scores of the same model
    # (0.97 = higher than 97% of them). NaN on an ATR model. See
    # `_evaluate_structure` for why a fixed threshold does not survive.
    rank: float = float("nan")
    geometry: str = "atr"
    # what the followed traders did, and what it did to the call
    # (api/service._smart_overlay). "" when nothing applied.
    smart_note: str = ""
    smart_effect: str = ""      # raised | confirmed | lowered | withdrawn

    @property
    def p_down(self) -> float:
        return 1.0 - self.p_up

    def resolution(self, high: float, low: float) -> Tuple[str, str]:
        """What price has ALREADY done to this window's barriers.

        An observation, not a forecast. It reads the extremes actually
        reached since the anchor close - no model, no features, no forming
        bar fed to anything. This is the honest half of an intra-bar update:
        whatever the odds were, if the level is gone the window is decided.
        """
        if not (np.isfinite(self.upper) and np.isfinite(self.lower)):
            return "OPEN", ""
        hit_up = bool(np.isfinite(high) and high >= self.upper)
        hit_dn = bool(np.isfinite(low) and low <= self.lower)

        if hit_up and hit_dn:
            # the ambiguous bar. OHLC cannot say which barrier came first,
            # and Agent 5's labeller resolves that tie as a LOSS. This
            # reports it the same way rather than inventing the kinder
            # answer - if the live screen were more generous than the
            # training labels, every statistic gathered here would flatter
            # the model against its own data
            return "BOTH", ("both barriers touched - OHLC cannot say which "
                            "came first, counted a LOSS (the labeller's "
                            "convention)")
        if hit_up:
            verdict = {"LONG": "WIN", "SHORT": "LOSS"}.get(self.side, "")
            tail = (f" - a {verdict} for the {self.side}" if verdict
                    else " - no position was proposed in this window")
            return "UPPER", f"upper barrier {self.upper:,.2f} touched{tail}"
        if hit_dn:
            verdict = {"LONG": "LOSS", "SHORT": "WIN"}.get(self.side, "")
            tail = (f" - a {verdict} for the {self.side}" if verdict
                    else " - no position was proposed in this window")
            return "LOWER", f"lower barrier {self.lower:,.2f} touched{tail}"
        return "OPEN", ""

    def render(self, now: pd.Timestamp, interval: pd.Timedelta) -> str:
        remaining = self.ends_at - now
        mins = max(0, int(remaining.total_seconds() // 60))
        head = (f" {self.name}   window {self.opened_at:%H:%M} -> "
                f"{self.ends_at:%H:%M} UTC   ({self.bars_left} bar"
                f"{'s' if self.bars_left != 1 else ''} left, ~{mins}m)")
        L = [head, DASH]

        if np.isnan(self.p_up):
            L.append("   not enough history to read this window")
            return "\n".join(L)

        L.append(f"   UP   {self.p_up:6.1%}          DOWN {self.p_down:6.1%}")
        L.append(f"   entry {self.entry:,.2f}   "
                 f"TP {self.tp_price:,.2f}   SL {self.sl_price:,.2f}")
        L.append(f"   EV  long {self.ev_long:+.3f}%   short {self.ev_short:+.3f}%")
        L.append(f"   ACTION: {self.action}"
                 + (f"  ({self.size_pct:.2f}% of equity)"
                    if self.size_pct > 0 else ""))
        L.append(f"   {self.reason}")
        L.append(f"   CLOSE BY: {self.ends_at:%Y-%m-%d %H:%M} UTC")
        return "\n".join(L)


# The trailing window a structure model ranks its score against: 90 days of
# 4h bars. Long enough that a decile is ~54 bars, short enough to follow the
# score distribution as it drifts.
TRAIL_BARS = 540

# The rank a structure entry must clear for each sensitivity. Measured on
# research/structure_levels.py (5 coins, 4h, 16 bars): the top decile netted
# +0.12%/trade and the top 5% +0.22% after fees, 10 of 10 books positive at
# the top 5%. "small" is the loosest cut still measured positive.
STRUCTURE_RANKS = (("strong", 0.95), ("medium", 0.90), ("small", 0.85))

_LEVELS_CACHE: dict = {}


def _structure_labels(judge, bars: pd.DataFrame):
    """The per-bar barriers for this model's side, memoised on the newest
    bar so the long and the short model do not each rebuild the levels.

    KEYED ON THE BARS' CONTENT, NOT ONLY THEIR SHAPE. The first key was
    (length, last close time, side, hold) -- and every coin seeded from the
    same date has the same length and the same last close time. For a day
    BTC, ETH, ADA, BNB and DOGE took turns wearing each other's levels:
    DOGE's card showed a target +1.23% away that was ADA's +1.23%, then
    BNB's +3.18%, applied to DOGE's own price. The probability and the
    rank were never touched (they come from the features, not from here);
    the target, the stop, the EV and the size were another coin's. The
    last bar's own prices and volume are in the key now, which two coins
    cannot share.
    """
    from agent5.labels import triple_barrier

    last = bars.iloc[-1]
    key = (len(bars), bars.index[-1],
           float(last["close"]), float(last["high"]), float(last["low"]),
           float(last.get("volume", 0.0)),
           judge.cfg.side, judge.cfg.max_hold_bars)
    hit = _LEVELS_CACHE.get(key)
    if hit is None:
        if len(_LEVELS_CACHE) > 16:
            _LEVELS_CACHE.clear()
        hit = _LEVELS_CACHE[key] = triple_barrier(bars, judge.cfg)
    return hit


def _evaluate_structure(judge, bars: pd.DataFrame, X: pd.DataFrame,
                        a: "Analysis") -> "Analysis":
    """One side's structure model over the newest row.

    WHY RANK AND NOT EXPECTED VALUE
        The EV rule (p x TP - (1-p) x SL - cost > threshold) was measured
        on these labels and it LOSES: the isotonic probability is honest on
        average and the target/stop distances are close to equal, so it
        opens far too many trades at 50-ish percent. What made money was
        the top decile of the model's own score. A fixed cut from the
        training data did not survive either -- it gave zero calls on half
        the coins in the held-out year as the score distribution drifted --
        so the cut is the quantile of the model's LAST TRAIL_BARS scores,
        recomputed every bar. Causal: every score in the window is from a
        bar that has closed.
    """
    from agent5.decision import expected_value_pct, kelly_full

    cfg = judge.cfg
    lab = _structure_labels(judge, bars)
    tp_pct = float(lab.tp_pct.iloc[-1])
    sl_pct = float(lab.sl_pct.iloc[-1])
    entry = float(bars["close"].iloc[-1])
    a.geometry = "structure"
    if not (np.isfinite(tp_pct) and np.isfinite(sl_pct)):
        return a

    long = cfg.side == "long"
    side = "LONG" if long else "SHORT"
    tail = X.iloc[-min(TRAIL_BARS, len(X)):]
    # RANK ON THE RAW SCORE, READ THE CALIBRATED ONE. See JudgeAgent.raw_scores
    # for why the two must not be the same array. Ties -- still possible on
    # a logistic model -- count half, so a repeated score ranks in the
    # middle of its run rather than under it.
    raw = judge.raw_scores(tail) if hasattr(judge, "raw_scores") else judge.predict_proba(tail)
    p = float(judge.predict_proba(tail.iloc[[-1]])[0])
    cur = float(raw[-1])
    past = raw[:-1]
    past = past[np.isfinite(past)]
    a.rank = (float((past < cur).mean() + 0.5 * (past == cur).mean())
              if len(past) >= 50 else float("nan"))

    a.entry = entry
    a.p_up = p if long else 1.0 - p          # p is P(this side's target first)
    a.upper = entry * (1 + (tp_pct if long else sl_pct) / 100.0)
    a.lower = entry * (1 - (sl_pct if long else tp_pct) / 100.0)
    a.tp_price = a.upper if long else a.lower
    a.sl_price = a.lower if long else a.upper
    ev = float(expected_value_pct(p, tp_pct, sl_pct, cfg.round_trip_cost_pct))
    if long:
        a.ev_long = ev
    else:
        a.ev_short = ev

    a.strength = ""
    for label, cut in STRUCTURE_RANKS:
        if np.isfinite(a.rank) and a.rank >= cut:
            a.strength = label
            break
    lvl = "the next swing" if long else "the last swing"
    stop = "the last swing" if long else "the next swing"
    if a.strength:
        f = float(kelly_full(p, tp_pct, sl_pct))
        a.size_pct = min(cfg.kelly_fraction * max(f, 0.05) * 100.0, cfg.max_position_pct)
        a.action = f"ENTER {side} NOW"
        a.side = side
        a.reason = (f"A {a.strength} signal — this reading is stronger than "
                    f"{100 * a.rank:.0f}% of the last 90 days'. Target at {lvl} "
                    f"({'+' if long else '-'}{tp_pct:.2f}%), stop at {stop}.")
        a.diagnostic = (f"p(target first) {p:.3f}; rank {a.rank:.3f} over "
                        f"{len(past)} bars; EV {ev:+.3f}% after costs; barriers on structure")
        return a
    a.action = "WAIT"
    if np.isfinite(a.rank):
        a.reason = (f"No {side.lower()} entry: this reading is weaker than "
                    f"{100 * (1 - a.rank):.0f}% of the last 90 days'; it calls only "
                    f"from the strongest {100 * (1 - STRUCTURE_RANKS[-1][1]):.0f}%.")
        a.diagnostic = (f"p(target first) {p:.3f}; rank {a.rank:.3f} over "
                        f"{len(past)} bars; EV {ev:+.3f}%; barriers on structure")
    else:
        a.reason = f"No {side.lower()} entry: not enough recent history to rank this reading."
        a.diagnostic = f"p(target first) {p:.3f}; fewer than 50 trailing scores"
    return a


def evaluate(judge, bars: pd.DataFrame, X: pd.DataFrame,
             name: str, opened_at, ends_at, bars_left: int) -> Analysis:
    """Run one frozen model over the newest row and fill in an Analysis."""
    from agent5.decision import expected_value_pct, kelly_full
    from agent5.labels import triple_barrier

    a = Analysis(name=name, opened_at=opened_at, ends_at=ends_at,
                 bars_left=bars_left)
    if getattr(judge.cfg, "geometry", "atr") == "structure":
        return _evaluate_structure(judge, bars, X, a)

    p_up = float(judge.predict_proba(X.iloc[[-1]])[0])
    lab = triple_barrier(bars, judge.cfg)
    tp_pct = float(lab.tp_pct.iloc[-1])
    sl_pct = float(lab.sl_pct.iloc[-1])
    entry = float(bars["close"].iloc[-1])
    if not np.isfinite(tp_pct) or not np.isfinite(sl_pct):
        return a

    cfg = judge.cfg
    a.p_up = p_up
    a.entry = entry
    # symmetric barriers, so the same distance serves both directions
    a.tp_price = entry * (1 + tp_pct / 100.0)
    a.sl_price = entry * (1 - sl_pct / 100.0)
    a.upper = entry * (1 + tp_pct / 100.0)
    a.lower = entry * (1 - sl_pct / 100.0)

    # a short is the mirror image, and only because the barriers are
    # symmetric. with a 2:1 payoff this arithmetic would be wrong
    a.ev_long = float(expected_value_pct(p_up, tp_pct, sl_pct,
                                         cfg.round_trip_cost_pct))
    a.ev_short = float(expected_value_pct(1 - p_up, tp_pct, sl_pct,
                                          cfg.round_trip_cost_pct))

    best_ev, side = ((a.ev_long, "LONG") if a.ev_long >= a.ev_short
                     else (a.ev_short, "SHORT"))
    p_side = p_up if side == "LONG" else 1 - p_up

    # THREE SENSITIVITIES, ALL STILL PROFITABLE AFTER COSTS.
    #
    #   strong   EV > the configured threshold (0.05%). The original rule.
    #   medium   EV > 40% of it (0.02%). Fires more often, smaller margin.
    #   small    EV > 0. Breakeven after fees — the loosest defensible bar.
    #
    # There is deliberately no fourth level. Going below zero would need only
    # p_up 0.530 at a 1.67% span, which these models DO reach — and every one
    # of those trades loses money on average. A setting that produces more
    # signals by producing losing ones is not a feature.
    #
    # The strongest level cleared is recorded rather than applied here, so the
    # app can render at whichever sensitivity the user has chosen without the
    # server having to know who is asking.
    levels = (("strong", cfg.ev_threshold_pct),
              ("medium", cfg.ev_threshold_pct * 0.4),
              ("small", 0.0))
    a.strength = ""
    for label, bar in levels:
        if best_ev > bar:
            a.strength = label
            break

    if a.strength:
        f = float(kelly_full(p_side, tp_pct, sl_pct))
        a.size_pct = min(cfg.kelly_fraction * f * 100.0, cfg.max_position_pct)
        if a.size_pct > 0:
            a.action = f"ENTER {side} NOW"
            a.side = side
            # WHAT THE CARD SAYS. For a person, not for the person who wrote
            # the model: the strength, and what the trade is expected to
            # make per trade after fees. Everything else is `diagnostic`.
            a.reason = (f"A {a.strength} signal — expected "
                        f"{best_ev:+.2f}% per trade after fees.")
            a.diagnostic = f"EV {best_ev:+.3f}% after costs"
            if side == "SHORT":
                a.tp_price = entry * (1 - tp_pct / 100.0)
                a.sl_price = entry * (1 + sl_pct / 100.0)
            return a

    a.action = "WAIT"

    # Say what would have to be TRUE, not merely that it is not.
    #
    # "EV below threshold" is unfalsifiable from the user's chair and reads
    # as the app having no opinion. The useful number is the probability this
    # timeframe would need, which falls straight out of the barrier width and
    # the fixed round-trip cost:
    #
    #     EV = s(2p-1) - c > threshold   ->   p > 0.5 + (threshold + c) / 2s
    #
    # This is why short timeframes look permanently flat, and it is not a
    # miscalibration to be tuned away. Fees are a fixed 0.10% of price. On a
    # 1d trade the barriers sit ~10.9% apart, fees are noise, and 0.514 is
    # enough. On a 1m trade they are ~1.4% apart, fees eat 7% of the whole
    # span, and the same decision needs 0.607. Lowering the bar there would
    # mean entering trades with negative expected value after costs.
    span = tp_pct + sl_pct
    if span > 0:
        needed = 0.5 + (cfg.ev_threshold_pct + cfg.round_trip_cost_pct) / span
        a.p_needed = float(needed)
        side_word = "up" if side == "LONG" else "down"
        # The card: one sentence a person can act on. The arithmetic that
        # justifies it -- the probability this timeframe would need, the
        # span, why shorter timeframes need more -- was on the card for a
        # long time and read as noise to everyone but its author. It is
        # kept, in `diagnostic`, for the terminal and the training screen.
        lean = "up" if p_side >= 0.5 else "down"
        if abs(p_side - 0.5) < 0.02:
            a.reason = ("No entry: the model sees no clear direction here "
                        "right now.")
        else:
            a.reason = (f"No entry: the model leans {lean} "
                        f"({p_side:.0%}), but not by enough to be worth "
                        f"the fees at this timeframe.")
        a.diagnostic = (
            f"p({side_word}) {p_side:.3f}; this timeframe needs {needed:.3f} "
            f"to cover {cfg.round_trip_cost_pct:.2f}% costs across a "
            f"{span:.2f}% barrier span. Shorter timeframes need MORE, not "
            f"less - the same fee is a bigger share of a smaller move")
    else:
        a.reason = "No entry: not enough edge to be worth the fees here."
        a.diagnostic = (f"best EV {best_ev:+.3f}% is below the "
                        f"{cfg.ev_threshold_pct:+.2f}% threshold")
    return a


# ------------------------------------------------------------ screen
class Monitor:
    def __init__(self, symbol: str, interval: str, h1_path: Path, h2_path: Path,
                 asset: str = "BTC"):
        from agent5 import JudgeAgent
        from livefeed import interval_delta

        self.symbol, self.interval = symbol, interval
        self.delta = interval_delta(interval)
        self.h1 = JudgeAgent.load(h1_path)
        self.h2 = JudgeAgent.load(h2_path)
        self.asset = asset
        self.news = NewsWatcher(asset=asset)
        # what each frozen model was actually asked. h1 is the shorter
        # horizon, h2 the longer one; at 1h that is 1 and 2 bars, at 1m it is
        # 120 and 240
        self.hold1 = int(getattr(self.h1.cfg, "max_hold_bars", 1))
        self.hold2 = int(getattr(self.h2.cfg, "max_hold_bars", 2))
        self._features_cache: Optional[Tuple[pd.Timestamp, pd.DataFrame]] = None

    # -- features ------------------------------------------------------
    def live_window(self, bars: pd.DataFrame) -> pd.DataFrame:
        """The tail of `bars` that a LIVE read actually needs.

        Prediction uses exactly one row — the newest. Computing 172,000 rows
        of history to use the last of them is pure waste, and it is waste that
        repeats: the feature cache is keyed on the newest bar, so a 1m
        collector invalidates it every sixty seconds. Measured before this
        existed, the server burned 75% of a core doing nothing else.

        The detectors are causal, so a long enough tail reproduces the newest
        row exactly. "Long enough" was measured rather than guessed — on
        172,767 one-minute bars, against the full-history computation:

            tail     time    worst feature drift   p_up
            16,000   1.8s    9.3e-03               identical to 6 dp
            24,000   1.8s    7.9e-04               identical to 6 dp
            40,000   2.9s    5.6e-06               identical to 6 dp
            172,767  31.5s   -                     -

        The only columns that drift at all are the HTF EMA pair (`rsi_14_4h`,
        `macd_hist_atr_4h`), and they never converge exactly by construction —
        an EMA has infinite impulse response, so it approaches the
        full-history value exponentially instead of reaching it. Four times
        the warmup keeps that below 1e-5, far under anything the model
        resolves.

        TRAINING STILL USES EVERYTHING. This is the live read only; `train.py`
        calls the agents directly on the full history.
        """
        need = self.required_bars(bars)
        want = max(need * 4, 20_000)
        return bars if len(bars) <= want else bars.tail(want)

    def required_bars(self, bars: pd.DataFrame) -> int:
        """How many bars the slowest detector needs before it emits anything."""
        from agent1 import PatternAgent
        from agent2 import IndicatorAgent
        from agent4 import FlowAgent

        return max(
            (getattr(a, "required_bars", lambda _b: a.warmup_bars)(bars)
             for a in (PatternAgent(), IndicatorAgent(), FlowAgent())),
            default=1000)

    def features(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Detector features for these CLOSED bars, cached per closing bar."""
        stamp = bars.index[-1]
        if self._features_cache and self._features_cache[0] == stamp:
            return self._features_cache[1]
        X = self._compute_features(self.live_window(bars))
        self._features_cache = (stamp, X)
        return X

    def provisional_features(self, bars: pd.DataFrame, forming) -> pd.DataFrame:
        """Features with the FORMING bar appended as though it had closed.

        READ THIS BEFORE USING THE OUTPUT.

        Both models were fitted on closed bars, and every closed bar spans a
        full interval. This frame's last row does not: at minute 10 of an
        hour its high, low and volume describe ten minutes, and the ATR,
        RSI and structure computed over it are all shifted accordingly. A
        probability read from this row is therefore NOT calibrated - the
        isotonic map that makes "61%" mean "61 times in 100" was fitted on
        out-of-fold predictions over closed bars only.

        So this exists for exactly one purpose: to show how far the anchored
        read has drifted since its bar closed. It is a staleness warning
        with a number attached, not a better forecast. It is never stored,
        never journalled, and never counted in any accuracy statistic.

        The distortion shrinks as the bar fills - at minute 55 of 60 this is
        nearly the closed bar - which is why every caller prints the elapsed
        fraction next to the number.
        """
        return self._compute_features(
            self.live_window(self._merge_forming(bars, forming)))

    def _merge_forming(self, bars: pd.DataFrame, forming) -> pd.DataFrame:
        """Closed bars plus the forming one, as a single frame."""
        row = forming.as_row().reindex(columns=bars.columns)
        merged = pd.concat([bars, row])
        # a forming bar shares no close_time with a stored one, but a race at
        # the boundary could duplicate it. keep the later (live) copy
        return merged[~merged.index.duplicated(keep="last")].sort_index()

    def _compute_features(self, bars: pd.DataFrame) -> pd.DataFrame:
        """The detector pipeline. No caching, no opinion about closedness."""
        from agent1 import PatternAgent
        from agent2 import IndicatorAgent
        from agent3 import Agent3Config, NewsAgent
        from agent4 import FlowAgent
        from agent5.dataset import build_dataset
        from agent5.labels import LabelResult
        from newsfeed.store import JSONLNewsStore

        # Agent 4's tape and derivatives inputs, read from the SAME store
        # training fills. This matters more than it looks: Agent 4's rolling
        # baselines span hundreds of bars, so serving needs the tape across
        # the whole live window, not just the newest bar. Training with the
        # tape and serving without it would leave the model predicting on
        # zeros where it learned on real flow — no exception, no failed test,
        # just a model quietly worse than its backtest.
        #
        # backfill=False: a serving request must never block on a download.
        # Whatever the collector and the training run have put on disk is
        # what it gets, and `tape_coverage_24h` tells the model how much
        # that was.
        try:
            from marketdata.flow_inputs import flow_inputs
            extra = flow_inputs(self.symbol, self.interval, bars,
                                backfill=False)
        except Exception as e:
            # monitor.py has no logger by design - it is a library used by
            # both the CLI and the API. A print here would pollute the API's
            # stdout, and a missing tape is already reported to the model
            # through Agent 4's coverage column.
            _ = e
            extra = {}

        frames, warmups = {}, []
        for key, agent in (("agent1", PatternAgent()), ("agent2", IndicatorAgent()),
                           ("agent4", FlowAgent())):
            need = getattr(agent, "required_bars", lambda _b: agent.warmup_bars)(bars)
            if len(bars) > need:
                frames[key] = (agent.compute(bars, **extra) if key == "agent4"
                               else agent.compute(bars))
                warmups.append(agent.warmup_bars)

        # agent 3 must run whenever the model was trained with it, even on an
        # empty news store. the frozen model expects an exact column set and
        # ORDER - a missing block does not degrade gracefully, it refuses,
        # which is the correct behaviour: silently reordering features lands
        # values on the wrong columns and nothing raises
        if any(c.startswith("news_") for c in self.h2.columns):
            a3 = NewsAgent(Agent3Config(asset=self.asset))
            try:
                items = JSONLNewsStore().load_items()
            except Exception:
                items = []
            frames["agent3"] = a3.compute(bars, items, a3.score_items(items))
            warmups.append(a3.warmup_bars)

        n = len(bars)
        # predicting, not training: labels do not exist for the newest bars
        # and build_dataset would drop exactly the rows we care about
        blank = LabelResult(
            y=pd.Series(0.0, index=bars.index),
            t1=pd.Series(np.arange(n, dtype=float), index=bars.index),
            weight=pd.Series(1.0, index=bars.index),
            touch=pd.Series("n/a", index=bars.index),
            tp_pct=pd.Series(np.nan, index=bars.index),
            sl_pct=pd.Series(np.nan, index=bars.index))

        ds = build_dataset(bars, self.h2.cfg, warmup=max(warmups, default=0),
                           labels=blank, **frames)

        # fail here, with the actual cause, rather than 80 lines later with
        # "missing 81 feature columns". the real problem is almost always
        # too little history, and the fix is one command
        missing = [c for c in self.h2.columns if c not in ds.X.columns]
        if missing:
            need = max(
                (getattr(a, "required_bars", lambda _b: a.warmup_bars)(bars)
                 for a in (PatternAgent(), IndicatorAgent(), FlowAgent())),
                default=0)
            raise SystemExit(
                f"only {len(bars):,} bars available, but the detectors need "
                f"about {need:,} before they produce features\n"
                f"  ({len(missing)} of the model's columns could not be built)\n\n"
                f"  seed the live store from history, once:\n"
                f"    python3 collect.py --seed 2024-01-01\n\n"
                f"  then keep it current:\n"
                f"    python3 collect.py")

        return ds.X

    # -- one screen ----------------------------------------------------
    def screen(self, bars: pd.DataFrame, news_items: List[dict],
               now: Optional[pd.Timestamp] = None,
               whale_events: Optional[List] = None) -> str:
        now = now or utc_now()
        last_close = bars.index[-1]
        price = float(bars["close"].iloc[-1])

        L = [BOX,
             f" {self.symbol} {self.interval}    last closed bar "
             f"{last_close:%Y-%m-%d %H:%M} UTC    price {price:,.2f}",
             f" now {now:%Y-%m-%d %H:%M:%S} UTC",
             BOX]

        # a window whose end time has already passed is not a forecast, it is
        # history. without this you can read "1 bar left, ~0m" on data that
        # went stale hours ago and act on an expired signal
        stale_by = now - (last_close + self.delta)
        if stale_by > pd.Timedelta(0):
            L.append("")
            L.append(f" !! STALE: the newest bar closed "
                     f"{str(stale_by).split('.')[0]} past its follower.")
            L.append("    the collector is not keeping up - these windows have "
                     "already expired.")
            L.append("    start it:  python3 collect.py")

        restarted = False

        # whales first: a filing is evidence about an actor, and it should be
        # on screen before the odds it might inform
        for e in (whale_events or []):
            impact, side = whale_verdict(e)
            if impact.startswith("MECHANICAL"):
                continue                    # not worth a headline
            L.append("")
            L.append(" *** WHALE ACTIVITY DETECTED ***")
            L.append(f"   {e.describe()}")
            L.append(f"   whale move is {impact}")
            L.append(f"   {side}")
            lag = ""
            if e.event_time is not None:
                hrs = (e.published_at - e.event_time).total_seconds() / 3600
                lag = f", disclosed {hrs:.0f}h later"
            L.append(f"   (traded {e.event_time:%Y-%m-%d}{lag}  "
                     f"code {e.transaction_code}  "
                     f"conviction {e.conviction:.2f})")
            L.append("   NOTE: filings are lagging evidence, not a trigger - "
                     "the analyses below are unchanged")

        if news_items:
            L.append("")
            L.append(" *** NEWS RELEASED, updating information for both analysis ***")
            for item in news_items:
                impact, side = news_verdict(item["direction"], item["magnitude"])
                L.append(f'   "{item["headline"][:64]}"  ({item["source"]})')
                L.append(f"   news are {impact}")
                L.append(f"   {side}")
                L.append(f"   (direction {item['direction']:+.2f}  "
                         f"magnitude {item['magnitude']:.2f}  "
                         f"novelty {item['novelty']:.2f})")
            L.append("   previous analyses ended - restarting both from this bar")
            restarted = True

        X = self.features(bars)

        # A: opened one bar ago, one bar left. B: opened now, two bars left.
        # after news both restart, so A is dropped and only a fresh B stands
        # The horizons come from the FITTED MODELS, not from the numbers 1
        # and 2. At 1h those are still 1 and 2 bars. At 1m they are 120 and
        # 240, because +/-1 ATR over two minutes is a scalping target smaller
        # than the round-trip fee — see core/timeframes.BARRIERS. Hardcoding
        # the old constants here would have silently drawn a two-minute window
        # over a model that was asked a four-hour question.
        n1, n2 = self.hold1, self.hold2
        analyses = []
        if not restarted:
            analyses.append(evaluate(
                self.h1, bars, X, "ANALYSIS A",
                opened_at=last_close - self.delta,
                ends_at=last_close + n1 * self.delta, bars_left=n1))
        analyses.append(evaluate(
            self.h2, bars, X, "ANALYSIS B" if not restarted else "ANALYSIS A (new)",
            opened_at=last_close,
            ends_at=last_close + n2 * self.delta, bars_left=n2))

        for a in analyses:
            L.append("")
            L.append(a.render(now, self.delta))

        L.append("")
        L.append(DASH)
        acting = [a for a in analyses if a.action.startswith("ENTER")]
        if acting:
            best = max(acting, key=lambda a: max(a.ev_long, a.ev_short))
            L.append(f" SUMMARY: {best.action} - {best.name}, "
                     f"close by {best.ends_at:%H:%M} UTC")
        else:
            L.append(" SUMMARY: NO TRADE NOW - neither window clears the EV "
                     "threshold.")
            L.append("          re-reads automatically when the next bar closes.")
        L.append(BOX)
        return "\n".join(L)

    # -- intra-bar ------------------------------------------------------
    def spike_screen(self, bars: pd.DataFrame, forming, spike,
                     now: Optional[pd.Timestamp] = None,
                     p_delta: float = 0.10,
                     provisional: bool = True) -> str:
        """What a mid-bar move did to the two live windows.

        Two different kinds of statement come out of here, and they are kept
        visually apart because one is far stronger than the other:

          RESOLVED     price reached a barrier. An observation. No model
                       involved, and it does not care what the odds said.
          provisional  the model re-read with the forming bar treated as
                       closed. Uncalibrated, never recorded - a staleness
                       warning, not a replacement forecast.
        """
        now = now or utc_now()
        last_close = bars.index[-1]
        way = "UP" if spike.direction > 0 else "DOWN"

        L = [BOX,
             f" *** SPIKE: {self.symbol} {way} {spike.move_pct:+.2f}% "
             f"inside the forming bar ***",
             f" {now:%Y-%m-%d %H:%M:%S} UTC   bar closes "
             f"{forming.close_time:%H:%M} UTC",
             BOX]
        L.extend(spike.describe())

        X = self.features(bars)                     # cached, closed bars only
        pairs = [
            (evaluate(self.h1, bars, X, "ANALYSIS A",
                      opened_at=last_close - self.delta,
                      ends_at=last_close + self.hold1 * self.delta,
                      bars_left=self.hold1), self.h1),
            (evaluate(self.h2, bars, X, "ANALYSIS B",
                      opened_at=last_close,
                      ends_at=last_close + self.hold2 * self.delta,
                      bars_left=self.hold2), self.h2),
        ]

        merged = prov_X = None
        prov_failed = ""

        def provisional_frame():
            """The provisional feature frame, computed at most once and only
            if some window is still open enough to need it.

            This is the expensive half - ~1.7s over 23,000 bars - and a spike
            violent enough to resolve BOTH windows has nothing left to
            re-read. Doing it lazily means the worst case (a real
            dislocation) is also the cheapest.
            """
            nonlocal merged, prov_X, prov_failed
            if prov_X is None and not prov_failed:
                try:
                    merged = self._merge_forming(bars, forming)
                    prov_X = self.provisional_features(bars, forming)
                except Exception as e:      # never let a re-read kill the loop
                    prov_failed = str(e)
            return prov_X

        any_resolved = False
        showed_provisional = False
        for a, model in pairs:
            L.append("")
            L.append(f" {a.name}   window {a.opened_at:%H:%M} -> "
                     f"{a.ends_at:%H:%M} UTC   ({a.bars_left} bar"
                     f"{'s' if a.bars_left != 1 else ''} left)")
            if np.isnan(a.p_up):
                L.append("   not enough history to read this window")
                continue

            L.append(f"   anchored UP {a.p_up:6.1%}   entry {a.entry:,.2f}   "
                     f"upper {a.upper:,.2f}   lower {a.lower:,.2f}")

            state, detail = a.resolution(forming.high, forming.low)
            if state != "OPEN":
                any_resolved = True
                L.append(f"   RESOLVED: {detail}")
                L.append("   this window is decided - the percentage above is "
                         "history, not a forecast")
                continue

            if not provisional or provisional_frame() is None:
                continue
            prov = evaluate(model, merged, prov_X, a.name,
                            opened_at=a.opened_at, ends_at=a.ends_at,
                            bars_left=a.bars_left)
            if np.isnan(prov.p_up):
                continue
            showed_provisional = True
            shift = prov.p_up - a.p_up
            material = abs(shift) >= p_delta
            verdict = (f"MATERIAL, past the {p_delta:.0%} threshold" if material
                       else "within noise - the anchored read stands")
            L.append(f"   provisional UP {prov.p_up:6.1%}   "
                     f"({shift * 100:+.1f} points - {verdict})")
            if material:
                L.append(f"   at the live price its barriers would be "
                         f"{prov.upper:,.2f} / {prov.lower:,.2f}, "
                         f"EV long {prov.ev_long:+.3f}%  short {prov.ev_short:+.3f}%")

        if prov_failed:
            L.append("")
            L.append(f"   (provisional re-read unavailable: {prov_failed})")
        if showed_provisional:
            L.append("")
            L.append("   PROVISIONAL means the forming bar was treated as closed.")
            L.append(f"   It is {forming.elapsed_fraction(now):.0%} of the way "
                     f"through, so its ATR, RSI and structure are all computed")
            L.append("   over a partial interval. Both models were fitted on "
                     "whole ones, so this number")
            L.append("   is NOT calibrated and is not recorded anywhere. It "
                     "says the anchored read has")
            L.append("   drifted - it does not replace it.")

        L.append("")
        L.append(DASH)
        if any_resolved:
            L.append(" SUMMARY: at least one window hit a barrier intra-bar. "
                     "Nothing here is a new entry.")
        else:
            L.append(" SUMMARY: both windows still open. This is a warning, "
                     "not a signal.")
        L.append(f"          the next calibrated read is at "
                 f"{forming.close_time:%H:%M} UTC when the bar closes.")
        L.append(BOX)
        return "\n".join(L)


# --------------------------------------------------------------- cli
def load_bars(args) -> pd.DataFrame:
    if args.history:
        from marketdata import load_klines
        return load_klines(args.symbol, args.interval, start=args.start,
                           end=None, cache_dir=project_config.DATA_CACHE)
    from livefeed import BarStore
    bars = BarStore(args.symbol, args.interval).load()
    if bars.empty:
        raise SystemExit(
            f"no collected bars for {args.symbol} {args.interval}.\n"
            f"  run the collector:  python3 collect.py\n"
            f"  or use history:     python3 monitor.py --history")
    return bars


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Rolling two-bar trading monitor.")
    p.add_argument("--symbol", default=project_config.SYMBOL)
    p.add_argument("--interval", default=project_config.INTERVAL)
    p.add_argument("--asset", default="BTC", help="asset for news scoping")
    p.add_argument("--h1", default="output/judge_h1.joblib",
                   help="1-bar-horizon model")
    p.add_argument("--h2", default="output/judge_h2.joblib",
                   help="2-bar-horizon model")
    p.add_argument("--history", action="store_true",
                   help="use downloaded history instead of the live store")
    p.add_argument("--start", default=project_config.HISTORY_START)
    p.add_argument("--once", action="store_true", help="one screen, then exit")
    p.add_argument("--replay", type=int, default=0,
                   help="replay the last N stored bars, one screen each")
    p.add_argument("--poll", type=int, default=20,
                   help="seconds between checks for a new bar or news")
    p.add_argument("--no-fetch", action="store_true",
                   help="do not fetch bars; rely on collect.py to update the "
                        "store. only useful if you run the collector separately")
    p.add_argument("--market", default=project_config.MARKET,
                   help="futures/um (default) or spot")
    p.add_argument("--no-whales", action="store_true",
                   help="do not watch SEC filings for insider/treasury trades")
    p.add_argument("--whale-every", type=int, default=900,
                   help="seconds between SEC sweeps (default 900). filings are "
                        "not a fast feed - a few a day across the whole list")
    p.add_argument("--whale-min-usd", type=float, default=100_000.0,
                   help="ignore transactions smaller than this")
    p.add_argument("--watchlist", action="store_true",
                   help="print the tracked entities and exit")
    # -- intra-bar watching. thresholds are DEFINITIONS of the word "spike",
    #    not parameters: do not tune them on trading outcomes, tune them on
    #    how often you want to be interrupted
    p.add_argument("--no-spikes", action="store_true",
                   help="do not watch the forming bar between closes")
    p.add_argument("--spike-atr", type=float, default=0.75,
                   help="alert when price moves this many ATR from the last "
                        "close inside one bar (default 0.75). keep it below "
                        "the model's barrier distance or the alert arrives "
                        "after the window is already decided")
    p.add_argument("--jolt-atr", type=float, default=0.6,
                   help="alert when price moves this many ATR within five "
                        "minutes, catching a fast move that then retraces "
                        "(default 0.6)")
    p.add_argument("--vol-pace", type=float, default=3.0,
                   help="alert when volume arrives at this multiple of its "
                        "usual pace for this point in the bar (default 3.0)")
    p.add_argument("--p-delta", type=float, default=0.10,
                   help="how far the provisional probability must move from "
                        "the anchored one to be called material (default 0.10)")
    p.add_argument("--no-provisional", action="store_true",
                   help="on a spike report resolved barriers only, and never "
                        "re-read the models with the forming bar treated as "
                        "closed")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.watchlist:
        from whalefeed import describe
        print(describe())
        return 0
    for path, label in ((Path(args.h1), "--h1"), (Path(args.h2), "--h2")):
        if not path.exists():
            raise SystemExit(
                f"no model at {path} ({label}).\n"
                f"  train the pair:\n"
                f"    python3 main.py --judge --hold 2 --k-up 1 --k-dn 1 "
                f"--save-model output/judge_h2.joblib --start 2023-01-01\n"
                f"    python3 main.py --judge --hold 1 --k-up 1 --k-dn 1 "
                f"--save-model output/judge_h1.joblib --start 2023-01-01")

    mon = Monitor(args.symbol, args.interval, Path(args.h1), Path(args.h2),
                  asset=args.asset)
    bars = load_bars(args)

    if args.replay:
        n = min(args.replay, max(1, len(bars) - 400))
        for k in range(n, 0, -1):
            window = bars.iloc[:len(bars) - k + 1]
            print(mon.screen(window, [], now=window.index[-1]))
            print()
        return 0

    # the collector logs "poll failed: <urlopen error ...>" straight to
    # stderr, which lands on top of the \r countdown and reads like a crash.
    # the monitor shows connection state itself, so silence the raw stream
    import logging
    logging.getLogger("livefeed").setLevel(logging.ERROR)
    logging.getLogger("whalefeed").setLevel(logging.ERROR)

    whales = None
    if not args.no_whales:
        whales = WhaleWatcher(min_usd=args.whale_min_usd,
                              poll_seconds=args.whale_every)
        whales.start()

    mon.news.poll()                       # prime, so old items are not "breaking"
    print(mon.screen(bars, []))
    if args.once:
        return 0

    last_seen = bars.index[-1]

    # THE MONITOR FETCHES ITS OWN BARS.
    #
    # It used to only read the store, which meant it silently depended on
    # collect.py running in another terminal. Without that, no new bar ever
    # appeared and the screen sat unchanged forever - looking broken while
    # behaving exactly as written.
    #
    # Now it pulls the closed bar itself. The store dedupes, so running
    # collect.py alongside is still fine - they just both write the same
    # bar and the second write is a no-op.
    refresher = None
    if not args.history and not args.no_fetch:
        from livefeed import KlineCollector
        refresher = KlineCollector(args.symbol, args.interval,
                                   market=args.market)

    interval_seconds = self_delta = mon.delta.total_seconds()
    tty = sys.stdout.isatty()
    width = max(78, min(shutil.get_terminal_size((100, 24)).columns - 1, 160))
    net_fails = 0
    last_net_ok = utc_now()
    print(f"\nwatching {args.symbol} {args.interval} - Ctrl-C to stop")
    if refresher is None and not args.history:
        print("  (--no-fetch: relying on collect.py to update the store)")

    # INTRA-BAR WATCHING.
    #
    # Between closes the monitor was blind. On 1h bars that is up to 59
    # minutes in which a 4% move is invisible and the analysis on screen
    # keeps quoting an entry price that stopped existing forty minutes ago.
    #
    # The cheap measurement gates the expensive one: one weight-1 REST call
    # per poll watches the forming bar, and only a crossed threshold pays
    # for a 107-feature recompute.
    forming_feed = watcher = spike_cfg = None
    vol_baseline = float("nan")
    if not args.no_spikes and not args.history:
        from livefeed import FormingBarFeed, SpikeConfig, SpikeWatcher
        spike_cfg = SpikeConfig(spike_atr=args.spike_atr,
                                jolt_atr=args.jolt_atr,
                                volume_pace_mult=args.vol_pace)
        forming_feed = FormingBarFeed(args.symbol, args.interval,
                                      market=args.market)
        watcher = SpikeWatcher(spike_cfg)
        watcher.reset(bars.index[-1], float(bars["close"].iloc[-1]))
        vol_baseline = SpikeWatcher.volume_baseline(bars, spike_cfg)
        print(f"  intra-bar watch: spike {args.spike_atr:.2f} ATR, "
              f"jolt {args.jolt_atr:.2f} ATR/5min, volume {args.vol_pace:.1f}x")

        # A spike threshold at or above the barrier distance is not a warning,
        # it is a post-mortem: by the time it fires the barrier has been
        # touched and the window is already decided. Silent when misconfigured
        # is the failure mode this project keeps designing against, so say it.
        barrier = min(mon.h1.cfg.k_up, mon.h1.cfg.k_dn,
                      mon.h2.cfg.k_up, mon.h2.cfg.k_dn)
        if args.spike_atr >= barrier:
            print(f"  NOTE: --spike-atr {args.spike_atr:.2f} is not below the "
                  f"models' barrier distance ({barrier:.2f} ATR), so a MOVE "
                  f"alert will\n        only arrive once a barrier is already "
                  f"touched. --spike-atr {barrier * 0.75:.2f} would warn first.")

    try:
        while True:
            now = utc_now()
            # the next bar closes one interval after the newest stored one
            next_close = last_seen + mon.delta
            due = (now - next_close).total_seconds()

            # only hit the API once the bar we are waiting for has closed.
            # polling every 20s would work too, but this is one request per
            # bar instead of 180, and the exchange needs a moment to finalise
            if refresher is not None and due >= 2:
                before_err = refresher.stats.errors
                refresher.poll_once()
                if refresher.stats.errors > before_err:
                    net_fails += 1
                else:
                    if net_fails:
                        if tty:
                            print("\r" + " " * 78 + "\r", end="")
                        print(f"  network recovered at {utc_now():%H:%M:%S} UTC "
                              f"- any bars missed while offline were backfilled")
                    net_fails = 0
                    last_net_ok = utc_now()

            fresh_news = mon.news.poll()
            fresh_whales = whales.drain() if whales else []
            bars = load_bars(args)
            new_bar = bars.index[-1] > last_seen

            if new_bar or fresh_news or fresh_whales:
                if tty:
                    print("\r" + " " * width + "\r", end="")  # clear countdown
                last_seen = bars.index[-1]
                print()
                print(mon.screen(bars, fresh_news, whale_events=fresh_whales))
                if watcher is not None:
                    # the closed bar is the new anchor. the old trail measured
                    # from a price that is now history, and keeping it would
                    # report the new bar's move from the wrong place
                    watcher.reset(last_seen, float(bars["close"].iloc[-1]))
                    # via the instance, not the class: the name `SpikeWatcher`
                    # is only bound inside the enabling branch above, and a
                    # guard that ever moves would turn that into UnboundLocalError
                    vol_baseline = watcher.volume_baseline(bars, watcher.cfg)
            else:
                reading = None
                if watcher is not None:
                    forming = forming_feed.fetch(now)
                    if forming is not None:
                        X = mon.features(bars)          # cached: free
                        reading, spike = watcher.observe(
                            forming, atr_price_estimate(bars, X),
                            vol_baseline, now=now)
                        if spike is not None:
                            if tty:
                                print("\r" + " " * width + "\r", end="")
                            print()
                            print(mon.spike_screen(
                                bars, forming, spike, now=now,
                                p_delta=args.p_delta,
                                provisional=not args.no_provisional))
                if tty:
                    # a visible heartbeat. without it the process looks hung
                    # for a full hour, which is indistinguishable from a crash
                    line = heartbeat(
                        max(0.0, -due), reading,
                        indicator_snapshot(mon.features(bars)), last_seen,
                        "news + filings" if whales else "news",
                        connection_hint(net_fails, last_net_ok))
                    print("\r" + line.ljust(width)[:width], end="", flush=True)

            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        if whales:
            whales.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
