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
@dataclass
class Analysis:
    """One two-bar window, re-read as each of its bars closes."""

    name: str
    opened_at: pd.Timestamp        # bar close that started it
    ends_at: pd.Timestamp          # bar close where its window finishes
    bars_left: int

    p_up: float = float("nan")
    entry: float = float("nan")
    tp_price: float = float("nan")
    sl_price: float = float("nan")
    ev_long: float = float("nan")
    ev_short: float = float("nan")
    action: str = "WAIT"
    reason: str = ""
    size_pct: float = 0.0

    @property
    def p_down(self) -> float:
        return 1.0 - self.p_up

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


def evaluate(judge, bars: pd.DataFrame, X: pd.DataFrame,
             name: str, opened_at, ends_at, bars_left: int) -> Analysis:
    """Run one frozen model over the newest row and fill in an Analysis."""
    from agent5.decision import expected_value_pct, kelly_full
    from agent5.labels import triple_barrier

    a = Analysis(name=name, opened_at=opened_at, ends_at=ends_at,
                 bars_left=bars_left)

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

    # a short is the mirror image, and only because the barriers are
    # symmetric. with a 2:1 payoff this arithmetic would be wrong
    a.ev_long = float(expected_value_pct(p_up, tp_pct, sl_pct,
                                         cfg.round_trip_cost_pct))
    a.ev_short = float(expected_value_pct(1 - p_up, tp_pct, sl_pct,
                                          cfg.round_trip_cost_pct))

    best_ev, side = ((a.ev_long, "LONG") if a.ev_long >= a.ev_short
                     else (a.ev_short, "SHORT"))
    p_side = p_up if side == "LONG" else 1 - p_up

    if best_ev > cfg.ev_threshold_pct:
        f = float(kelly_full(p_side, tp_pct, sl_pct))
        a.size_pct = min(cfg.kelly_fraction * f * 100.0, cfg.max_position_pct)
        if a.size_pct > 0:
            a.action = f"ENTER {side} NOW"
            a.reason = (f"EV {best_ev:+.3f}% clears the "
                        f"{cfg.ev_threshold_pct:+.2f}% threshold after costs")
            if side == "SHORT":
                a.tp_price = entry * (1 - tp_pct / 100.0)
                a.sl_price = entry * (1 + sl_pct / 100.0)
            return a

    a.action = "WAIT"
    a.reason = (f"best EV {best_ev:+.3f}% is below the "
                f"{cfg.ev_threshold_pct:+.2f}% threshold - re-read when the "
                f"next bar closes")
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
        self._features_cache: Optional[Tuple[pd.Timestamp, pd.DataFrame]] = None

    # -- features ------------------------------------------------------
    def features(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Detector features for these bars, cached per closing bar."""
        stamp = bars.index[-1]
        if self._features_cache and self._features_cache[0] == stamp:
            return self._features_cache[1]

        from agent1 import PatternAgent
        from agent2 import IndicatorAgent
        from agent3 import Agent3Config, NewsAgent
        from agent4 import FlowAgent
        from agent5.dataset import build_dataset
        from agent5.labels import LabelResult
        from newsfeed.store import JSONLNewsStore

        frames, warmups = {}, []
        for key, agent in (("agent1", PatternAgent()), ("agent2", IndicatorAgent()),
                           ("agent4", FlowAgent())):
            need = getattr(agent, "required_bars", lambda _b: agent.warmup_bars)(bars)
            if len(bars) > need:
                frames[key] = agent.compute(bars)
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

        self._features_cache = (stamp, ds.X)
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
        analyses = []
        if not restarted:
            analyses.append(evaluate(
                self.h1, bars, X, "ANALYSIS A",
                opened_at=last_close - self.delta,
                ends_at=last_close + self.delta, bars_left=1))
        analyses.append(evaluate(
            self.h2, bars, X, "ANALYSIS B" if not restarted else "ANALYSIS A (new)",
            opened_at=last_close,
            ends_at=last_close + 2 * self.delta, bars_left=2))

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
    net_fails = 0
    last_net_ok = utc_now()
    print(f"\nwatching {args.symbol} {args.interval} - Ctrl-C to stop")
    if refresher is None and not args.history:
        print("  (--no-fetch: relying on collect.py to update the store)")

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
                    print("\r" + " " * 78 + "\r", end="")   # clear countdown
                last_seen = bars.index[-1]
                print()
                print(mon.screen(bars, fresh_news, whale_events=fresh_whales))
            elif tty:
                # a visible heartbeat. without it the process looks hung for
                # a full hour, which is indistinguishable from a crash
                wait = max(0, -due)
                mm, ss = divmod(int(wait), 60)
                watching = "news + filings" if whales else "news"
                offline = connection_hint(net_fails, last_net_ok)
                if offline:
                    line = (f"  next bar closes in {mm:02d}:{ss:02d}  "
                            f"{offline} - retrying")
                else:
                    line = (f"  next bar closes in {mm:02d}:{ss:02d}  "
                            f"(last {last_seen:%H:%M} UTC)  watching {watching}...")
                print("\r" + line.ljust(78)[:78], end="", flush=True)

            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        if whales:
            whales.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
