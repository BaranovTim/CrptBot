"""The read model behind the mobile app.

WHAT THIS IS NOT
----------------
Not a trading server. It reads the same stores `monitor.py` reads, runs the
same frozen models, and returns JSON. It places no orders, holds no keys and
mutates nothing except its own caches. Everything it can do, you could do by
reading the terminal.

WHY IT REUSES `Monitor` RATHER THAN REIMPLEMENTING IT
-----------------------------------------------------
Two code paths computing "the probability" is how a phone and a terminal end
up disagreeing about the same bar. `Monitor` already owns the horizon rule
(h1 with one bar left, h2 with two), the feature cache, the staleness guard
and the barrier arithmetic. The app is a second view of that one object, not
a second implementation.

THE ONE-TRAINED-PAIR FACT
-------------------------
Models exist for BTCUSDT and nothing else. That is not a gap to paper over -
the app surfaces it, because a coin with no fitted model has no honest
probability to show. `coins()` reports `trained` per pair and the app routes
untrained ones to the training screen instead of a dashboard.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import config as project_config
from core import TIMEFRAMES, htf_for, is_trained, model_paths, utc_now

log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"

# The pairs the app offers. BTCUSDT is the only one with fitted models; the
# rest are listed so the training screen has something true to say about them.
UNIVERSE = [
    ("BTCUSDT", "Bitcoin", "BTC"),
    ("ETHUSDT", "Ethereum", "ETH"),
    ("SOLUSDT", "Solana", "SOL"),
    ("ADAUSDT", "Cardano", "ADA"),
]

MODEL_DIR = project_config.OUTPUT_DIR


def _ticker(symbol: str, timeout: int = 10) -> Dict[str, float]:
    """24h price and change. Public endpoint, no key, weight 1."""
    url = f"{FAPI}/fapi/v1/ticker/24hr?symbol={symbol}"
    req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/api"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return {"price": float(d["lastPrice"]),
            "change_pct": float(d["priceChangePercent"])}


@dataclass
class _Cached:
    at: float
    value: Any


class TradingService:
    """One instance, shared by every request. Caches are per-bar, not per-call."""

    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1h",
                 asset: str = "BTC"):
        # the DEFAULT pair. Every read below can be pointed at another one,
        # because the app now has a timeframe selector and each timeframe is
        # a separately fitted model
        self.symbol, self.interval, self.asset = symbol, interval, asset
        self._lock = threading.Lock()
        self._monitors: Dict[Tuple[str, str], Any] = {}
        self._tickers: Dict[str, _Cached] = {}
        self._dash: Dict[Tuple[str, str], _Cached] = {}
        self._forming: Dict[Tuple[str, str], Any] = {}

    def _pair(self, symbol: Optional[str], interval: Optional[str]):
        return (symbol or self.symbol).upper(), interval or self.interval

    # -- lazily built, and CACHED PER PAIR ------------------------------
    #
    # Loading two models plus computing 100+ features over the history costs
    # seconds. Six timeframes means six of those, so they are built on first
    # request and kept — a phone flicking between 1m and 4h must not pay for
    # a rebuild each time.
    def _mon(self, symbol: Optional[str] = None, interval: Optional[str] = None):
        key = self._pair(symbol, interval)
        if key not in self._monitors:
            from monitor import Monitor
            h1, h2 = model_paths(*key)
            if not (h1.exists() and h2.exists()):
                raise FileNotFoundError(
                    f"no fitted model for {key[0]} {key[1]}. "
                    f"train it:  python3 train.py --intervals {key[1]}")
            self._monitors[key] = Monitor(key[0], key[1], h1, h2,
                                          asset=self.asset)
        return self._monitors[key]

    def _bars(self, symbol: Optional[str] = None,
              interval: Optional[str] = None) -> pd.DataFrame:
        from livefeed import BarStore
        return BarStore(*self._pair(symbol, interval)).load()

    # ---------------------------------------------------- the universe
    def symbols(self, q: Optional[str] = None,
                limit: int = 60, ttl: float = 21600.0) -> List[Dict[str, Any]]:
        """Every USD-M perpetual Binance is currently trading.

        Ordered by 24h quote volume, not alphabetically. A search for "b"
        should offer BTC before BAKE — alphabetical order buries every pair
        anyone actually wants behind three-letter tokens nobody has heard of.

        Cached for six hours: `exchangeInfo` changes when a contract is listed
        or delisted, which is not something to re-download per keystroke.
        """
        hit = self._tickers.get("__universe__")
        if not hit or time.time() - hit.at > ttl:
            rows = self._fetch_universe()
            if rows:
                self._tickers["__universe__"] = _Cached(time.time(), rows)
            elif hit:
                rows = hit.value          # stale beats empty
            else:
                return []
        else:
            rows = hit.value

        if q:
            needle = q.strip().upper()
            rows = [r for r in rows
                    if needle in r["symbol"] or needle in r["base"]]
        return rows[:max(1, min(limit, 500))]

    def _fetch_universe(self) -> List[Dict[str, Any]]:
        try:
            req = urllib.request.Request(
                f"{FAPI}/fapi/v1/exchangeInfo",
                headers={"User-Agent": "TradingBot/api"})
            with urllib.request.urlopen(req, timeout=25) as r:
                info = json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError) as e:
            log.warning("exchangeInfo failed: %s", e)
            return []

        live = [x for x in info.get("symbols", [])
                if x.get("status") == "TRADING"
                and x.get("contractType") == "PERPETUAL"
                and x.get("quoteAsset") == "USDT"]

        volume: Dict[str, float] = {}
        try:
            req = urllib.request.Request(
                f"{FAPI}/fapi/v1/ticker/24hr",
                headers={"User-Agent": "TradingBot/api"})
            with urllib.request.urlopen(req, timeout=25) as r:
                for t in json.loads(r.read()):
                    volume[t["symbol"]] = float(t.get("quoteVolume") or 0.0)
        except Exception as e:          # ordering is a nicety, not a contract
            log.warning("24hr ticker sweep failed, falling back to A-Z: %s", e)

        rows = [{"symbol": x["symbol"], "base": x["baseAsset"],
                 "quote": x["quoteAsset"],
                 "volume_24h": _num(volume.get(x["symbol"]))}
                for x in live]
        rows.sort(key=lambda r: (-(r["volume_24h"] or 0.0), r["symbol"]))
        return rows

    def ticker(self, symbol: str, ttl: float = 10.0) -> Dict[str, float]:
        """Cached for `ttl` seconds so a pull-to-refresh storm cannot rate-limit us."""
        hit = self._tickers.get(symbol)
        if hit and time.time() - hit.at < ttl:
            return hit.value
        try:
            v = _ticker(symbol)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError, KeyError) as e:
            log.warning("ticker %s failed: %s", symbol, e)
            v = hit.value if hit else {"price": float("nan"),
                                       "change_pct": float("nan")}
        self._tickers[symbol] = _Cached(time.time(), v)
        return v

    # ------------------------------------------------------------ coins
    def trained_intervals(self, symbol: Optional[str] = None) -> List[str]:
        sym = (symbol or self.symbol).upper()
        return [tf for tf in TIMEFRAMES if is_trained(sym, tf)]

    def trained_pairs(self) -> List[str]:
        """Symbols with at least one fitted timeframe."""
        return [sym for sym, _, _ in UNIVERSE if self.trained_intervals(sym)]

    def cost_drag(self, symbol: str, interval: str) -> Dict[str, Any]:
        """How much of the TP-to-SL span the round trip in fees eats.

        The number that decides whether a timeframe is tradeable at all, and
        it has nothing to do with how good the model is. On BTCUSDT the median
        one-minute ATR is about 0.046% of price, so a +1/-1 ATR barrier pair
        spans ~0.092% — while the round trip costs 0.100%. The barriers sit
        INSIDE the fees. An AUC of 0.99 would still lose money there.

        This is why the fast timeframes carry a warning in the app rather than
        just a probability.
        """
        from agent5.config import Agent5Config

        bars = self._bars(symbol, interval)

        # the barriers the model was ACTUALLY fitted with, not the class
        # defaults. Agent5Config defaults to k_up=2/k_dn=1 while every model
        # here is trained symmetric at 1/1 — a 3-ATR span versus a 2-ATR one,
        # which is the difference between "fees are 73% of the range" and
        # "fees are 109% of it". The second one is a different verdict.
        cfg = Agent5Config()
        if is_trained(symbol, interval):
            try:
                cfg = self._mon(symbol, interval).h2.cfg
            except Exception:
                pass
        if bars.empty or len(bars) < 60:
            return {"span_pct": None, "cost_pct": cfg.round_trip_cost_pct,
                    "cost_share": None, "verdict": "unknown"}

        prev = bars["close"].shift(1)
        tr = pd.concat([bars["high"] - bars["low"],
                        (bars["high"] - prev).abs(),
                        (bars["low"] - prev).abs()], axis=1).max(axis=1)
        atr_pct = float((tr.rolling(14).mean() / bars["close"]).median() * 100)
        span = (cfg.k_up + cfg.k_dn) * atr_pct
        share = cfg.round_trip_cost_pct / span if span > 0 else float("inf")
        verdict = ("untradeable" if share >= 0.5
                   else "marginal" if share >= 0.25 else "workable")
        return {
            "span_pct": _num(span),
            "cost_pct": _num(cfg.round_trip_cost_pct),
            "cost_share": _num(share),
            "verdict": verdict,
            "note": (f"Fees are {share:.0%} of the whole TP-to-SL span at "
                     f"{interval}."
                     + (" The barriers sit inside the round trip, so no "
                        "accuracy makes this profitable."
                        if verdict == "untradeable" else
                        " Costs take a large bite; the edge has to clear "
                        "them before anything is left."
                        if verdict == "marginal" else
                        " Costs are a minor drag at this timeframe.")),
        }

    def timeframes(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """What the selector renders: every timeframe, and whether it is real.

        An untrained timeframe is still listed. Hiding it would make the
        selector look complete when it is not, and the whole point of the
        badge is that only a fitted model has an honest probability.
        """
        from livefeed import BarStore

        sym = (symbol or self.symbol).upper()
        out = []
        for tf in TIMEFRAMES:
            try:
                stored = BarStore(sym, tf).count()
            except Exception:
                stored = 0
            row = {
                "interval": tf,
                "label": tf.upper(),
                "trained": is_trained(sym, tf),
                "htf": htf_for(tf),
                "bars": stored,
            }
            try:
                row["cost"] = self.cost_drag(sym, tf)
            except Exception:
                row["cost"] = None
            out.append(row)
        return out

    def coins(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Rows for the market screen.

        `symbols` comes from the PHONE, not from a stored watchlist here. The
        API is read-only on purpose — no writes, no auth, nothing worth
        attacking — and a watchlist is a per-device preference anyway. Adding
        a POST endpoint to hold it would have traded that property away for a
        list of four strings.
        """
        known = {r["symbol"]: r for r in self.symbols(limit=500)}
        if symbols:
            wanted = []
            for sym in symbols[:40]:
                sym = sym.strip().upper()
                if not sym:
                    continue
                base = known.get(sym, {}).get("base") or sym.replace("USDT", "")
                wanted.append((sym, base, base))
        else:
            wanted = list(UNIVERSE)

        out = []
        for sym, name, short in wanted:
            t = self.ticker(sym)
            tfs = self.trained_intervals(sym)
            out.append({
                "symbol": sym,
                "name": name,
                "short": short,
                "pair": f"{short} / USDT",
                "price": _num(t["price"]),
                "change_pct": _num(t["change_pct"]),
                "trained": bool(tfs),
                "trained_intervals": tfs,
                # a delisted or mistyped pair returns no price and no bars.
                # Saying so beats a row of dashes the user cannot explain -
                # PEPEUSDT does not exist as a perpetual, 1000PEPEUSDT does
                "listed": sym in known,
            })
        return out

    # -------------------------------------------------------- dashboard
    def dashboard(self, symbol: Optional[str] = None,
                  interval: Optional[str] = None,
                  ttl: float = 5.0) -> Dict[str, Any]:
        """Everything the dashboard screen renders, in one payload.

        Cached briefly: the heavy part is the feature frame, which `Monitor`
        already caches per closing bar, but the barrier arithmetic and the
        forming-bar fetch are re-done and a phone polling every second should
        not drive one REST call per poll.
        """
        key = self._pair(symbol, interval)
        with self._lock:
            hit = self._dash.get(key)
            if hit and time.time() - hit.at < ttl:
                return hit.value
            v = self._build_dashboard(*key)
            self._dash[key] = _Cached(time.time(), v)
            return v

    def _build_dashboard(self, symbol: str, interval: str) -> Dict[str, Any]:
        from monitor import (atr_price_estimate, evaluate, indicator_snapshot)

        mon = self._mon(symbol, interval)
        bars = self._bars(symbol, interval)
        now = utc_now()
        if bars.empty:
            raise RuntimeError(
                f"no collected bars for {symbol} {interval} - run: "
                f"python3 train.py --intervals {interval}")

        X = mon.features(bars)
        snap = indicator_snapshot(X)
        htf = htf_for(interval)
        last_close = bars.index[-1]
        close_price = float(bars["close"].iloc[-1])

        # horizons off the FITTED models, never the constants 1 and 2.
        # `monitor.py` learned this the same way: at 1m the models are asked a
        # 120/240-bar question, and drawing a two-minute window over a
        # four-hour prediction is the kind of mismatch nothing raises about.
        n1, n2 = mon.hold1, mon.hold2
        a = evaluate(mon.h1, bars, X, "ANALYSIS A",
                     opened_at=last_close - mon.delta,
                     ends_at=last_close + n1 * mon.delta, bars_left=n1)
        b = evaluate(mon.h2, bars, X, "ANALYSIS B", opened_at=last_close,
                     ends_at=last_close + n2 * mon.delta, bars_left=n2)

        # the same staleness rule the terminal uses. a window whose follower
        # has already closed is history, and the app must not paint it green
        # stale once the shorter window's own span has fully elapsed
        stale_by = (now - (last_close + n1 * mon.delta)).total_seconds()
        stale = stale_by > 0

        chosen = _choose(b, a) if not stale else b
        live = self._live(symbol, interval, bars, X, now)
        price = live.get("price") if live else None
        if price is None or not np.isfinite(price):
            price = close_price

        short = next((sh for sy, _, sh in UNIVERSE if sy == symbol), symbol)
        return {
            "symbol": symbol,
            "pair": f"{short} / USDT",
            "interval": interval,
            "htf": htf,
            "timeframes": self.timeframes(symbol),
            "generated_at": now.isoformat(),
            "last_closed_bar": last_close.isoformat(),
            "stale": stale,
            "stale_seconds": max(0.0, stale_by),
            # the badge. it records and advises - it does not place orders,
            # and the label says so rather than implying autonomy
            "status": {
                "active": not stale,
                "label": "WATCHING" if not stale else "STALE",
                "detail": ("reading every closed bar" if not stale
                           else "the collector is not keeping up"),
                "trades": False,
            },
            "price": _num(price),
            "close_price": _num(close_price),
            "change_pct": _num(self.ticker(symbol)["change_pct"]),
            "live": live,
            "indicators": _indicators(snap, interval=interval, htf=htf),
            "analyses": [_analysis(a), _analysis(b)],
            "recommendation": _recommendation(b, a, stale),
            "levels": {
                "current": _num(price),
                # the SAME window the recommendation speaks for
                "window_bars": chosen.bars_left,
                "p_up": _num(chosen.p_up),
                "take_profit": _num(chosen.tp_price),
                "stop_loss": _num(chosen.sl_price),
                # the barriers as distances rather than prices. the app
                # re-anchors them to the websocket price so TP/SL track the
                # market between bars — the DISTANCE is what the model fixed
                # at the close, the price it is measured from is not
                "anchor": _num(chosen.entry),
                "tp_pct": _num((chosen.upper / chosen.entry - 1.0) * 100.0)
                if _num(chosen.entry) else None,
                "sl_pct": _num((1.0 - chosen.lower / chosen.entry) * 100.0)
                if _num(chosen.entry) else None,
            },
            "calibration_note": (
                "Probability is the chance a long entered at this bar's close "
                "reaches +1 ATR before -1 ATR within the window. On the last "
                "evaluation this feature set scored at chance, so treat it as "
                "a measurement of the model, not a forecast of the market."),
        }

    def _live(self, symbol, interval, bars, X, now) -> Optional[Dict[str, Any]]:
        """The forming bar, if it is reachable. None is a normal answer."""
        from livefeed import FormingBarFeed, SpikeConfig, SpikeWatcher
        from monitor import atr_price_estimate

        key = (symbol, interval)
        if key not in self._forming:
            self._forming[key] = FormingBarFeed(symbol, interval)
        f = self._forming[key].fetch(now)
        if f is None:
            return None

        cfg = SpikeConfig()
        w = SpikeWatcher(cfg)
        w.reset(bars.index[-1], float(bars["close"].iloc[-1]))
        r = w.read(f, atr_price_estimate(bars, X),
                   SpikeWatcher.volume_baseline(bars, cfg), now=now)
        if r is None:
            return {"price": _num(f.close), "elapsed": _num(f.elapsed_fraction(now))}
        return {
            "price": _num(r.price),
            "move_pct": _num(r.move_pct),
            "move_atr": _num(r.move_atr),
            "volume_pace": _num(r.volume_pace),
            "taker_buy_ratio": _num(r.taker_buy_ratio),
            "elapsed": _num(r.elapsed),
            "bar_closes_at": f.close_time.isoformat(),
            # a single reading cannot know about the re-arm, so this reports
            # "past the threshold", not "an alert just fired"
            "beyond_spike_threshold": bool(abs(r.move_atr) >= cfg.spike_atr),
        }

    # -------------------------------------------------------- consensus
    def consensus(self, symbol: Optional[str] = None,
                  ttl: float = 30.0) -> Dict[str, Any]:
        """What every fitted timeframe says, side by side.

        This is a multi-timeframe VIEW, not a multi-timeframe model, and the
        difference matters. Each row is an independent model answering its own
        question over its own horizon — the 1m model is not being told what
        the 4h model thinks. Feeding one into the other is a real technique,
        but it would need its own purged evaluation to know whether it helped,
        and stacking a signal onto a feature set that currently scores at
        chance would mostly be a way to launder noise.

        What this IS good for is the thing a human does anyway: noticing when
        the timeframes disagree. Rows that all say the same thing are one
        piece of evidence repeated; rows that conflict are a reason to wait.
        The `agreement` field measures exactly that and nothing more.
        """
        sym = (symbol or self.symbol).upper()
        key = (sym, "__consensus__")
        with self._lock:
            hit = self._dash.get(key)
            if hit and time.time() - hit.at < ttl:
                return hit.value

        rows = []
        for tf in self.trained_intervals(sym):
            try:
                d = self.dashboard(symbol=sym, interval=tf)
            except Exception as e:
                log.warning("consensus %s %s: %s", sym, tf, e)
                continue
            a = d["analyses"][-1] if d["analyses"] else {}
            rows.append({
                "interval": tf,
                "htf": htf_for(tf),
                "p_up": a.get("p_up"),
                "action": d["recommendation"]["action"],
                "tone": d["recommendation"]["tone"],
                "stale": d["stale"],
                "ev": d["recommendation"].get("ev"),
            })

        ups = [r["p_up"] for r in rows
               if r["p_up"] is not None and not r["stale"]]
        agreement = None
        if len(ups) >= 2:
            # the share leaning the same way as the majority. 1.0 is every
            # timeframe agreeing, 0.5 is a dead split
            up = sum(1 for p in ups if p > 0.5)
            agreement = max(up, len(ups) - up) / len(ups)

        value = {
            "symbol": sym,
            "rows": rows,
            "agreement": _num(agreement),
            "note": ("Independent models, one per timeframe — not one model "
                     "reading several. Agreement is a description of these "
                     "rows, not evidence about the market."),
        }
        with self._lock:
            self._dash[key] = _Cached(time.time(), value)
        return value

    # ------------------------------------------------------------ chart
    def chart(self, symbol: Optional[str] = None,
              interval: Optional[str] = None, n: int = 96) -> Dict[str, Any]:
        sym, iv = self._pair(symbol, interval)
        bars = self._bars(sym, iv)
        if bars.empty:
            return {"points": [], "interval": iv}
        tail = bars.tail(max(2, min(n, 500)))
        return {
            "interval": iv,
            "points": [{"t": t.isoformat(), "c": _num(c)}
                       for t, c in zip(tail.index, tail["close"])],
        }

    # ----------------------------------------------------------- whales
    def whales(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            from whalefeed.store import WhaleStore
            events = WhaleStore().load()
        except Exception as e:
            log.warning("whale store unreadable: %s", e)
            return []
        from monitor import whale_verdict

        out = []
        events = [e for e in events if e.published_at is not None]
        for e in sorted(events, key=lambda x: x.published_at, reverse=True):
            impact, side = whale_verdict(e)
            out.append({
                "describe": e.describe(),
                "impact": impact,
                "side": side,
                "mechanical": impact.startswith("MECHANICAL"),
                "conviction": _num(getattr(e, "conviction", float("nan"))),
                "code": getattr(e, "transaction_code", ""),
                "published_at": e.published_at.isoformat(),
                # when the trade actually happened, which is up to five days
                # before it was disclosed. a notification that shows only the
                # filing time implies a freshness this data does not have
                "event_time": (e.event_time.isoformat()
                               if getattr(e, "event_time", None) else None),
                # filings lag by 24-120h and are gameable. the app repeats
                # that next to every one of them rather than in a footnote
                "note": "lagging evidence, not a trigger",
            })
            if len(out) >= limit:
                break
        return out

    def news(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            from newsfeed.store import JSONLNewsStore
            items = JSONLNewsStore().load_items()
        except Exception as e:
            log.warning("news store unreadable: %s", e)
            return []
        out = []
        for it in items[-limit:][::-1]:
            out.append({
                "headline": getattr(it, "headline", str(it)),
                "source": getattr(it, "source", ""),
                "published_at": getattr(it, "published_at", None)
                and it.published_at.isoformat(),
            })
        return out

    # --------------------------------------------------------- training
    def training(self, symbol: str,
                 interval: Optional[str] = None) -> Dict[str, Any]:
        sym, iv = self._pair(symbol, interval)
        if not is_trained(sym, iv):
            return {
                "symbol": sym,
                "interval": iv,
                "htf": htf_for(iv),
                "trained": False,
                "timeframes": self.timeframes(sym),
                "title": "Untrained Model",
                "detail": (f"No fitted model exists for {sym} {iv}. Every "
                           f"timeframe is its own model: patterns and "
                           f"indicators mean different things at 1m and 1d, "
                           f"so one fit cannot serve both. Training is a "
                           f"batch job with purged cross-validation and an "
                           f"honest scoreboard - not a button."),
                "command": f"python3 train.py --symbol {sym} --intervals {iv}",
                "cost": self.cost_drag(sym, iv),
            }
        mon = self._mon(sym, iv)
        return {
            "symbol": sym,
            "interval": iv,
            "htf": htf_for(iv),
            "trained": True,
            "timeframes": self.timeframes(sym),
            "title": "Model Fitted",
            "horizons": [
                {"name": "h1", "bars": mon.h1.cfg.max_hold_bars,
                 "k_up": mon.h1.cfg.k_up, "k_dn": mon.h1.cfg.k_dn,
                 "features": len(mon.h1.columns)},
                {"name": "h2", "bars": mon.h2.cfg.max_hold_bars,
                 "k_up": mon.h2.cfg.k_up, "k_dn": mon.h2.cfg.k_dn,
                 "features": len(mon.h2.columns)},
            ],
            "cost": self.cost_drag(sym, iv),
            "detail": (f"Fitted, calibrated and frozen for the {iv} "
                       f"timeframe, with {htf_for(iv)} as higher-timeframe "
                       f"context. Retraining is offline and gated - a model "
                       f"that updates on every tick is chasing noise."),
        }


# ------------------------------------------------------------- helpers
def _num(v) -> Optional[float]:
    """NaN and inf are not JSON. None means 'absent', which is the truth."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _indicators(snap: Dict[str, float], interval: str = "1h",
                htf: str = "4h") -> List[Dict[str, Any]]:
    """The two cards the design shows, plus regime. Values are last CLOSED bar.

    The HTF card is LABELLED with the higher timeframe actually in use, which
    varies per base: 1m reads 15m, 15m reads 4h, 1d reads 1w. Agent 1's column
    is named `trend_direction_4h` whatever `htf_rule` is set to, so the name
    carries no information about what it holds — hardcoding "4H STRUCTURE"
    from it meant the 1d screen displayed the WEEKLY trend under a 4H label,
    and then disagreed with the 1h screen for a reason no user could see.
    """
    out = []
    rsi = snap.get("rsi_14")
    # RSI(14) spans 14 BARS, which is 14 minutes at 1m and 14 hours at 1h.
    # Saying which makes the number mean something.
    if rsi is not None and np.isfinite(rsi):
        if rsi >= 70:
            note, tone = "Overbought", "warn"
        elif rsi >= 60:
            note, tone = "Approaching Overbought", "up"
        elif rsi <= 30:
            note, tone = "Oversold", "warn"
        elif rsi <= 40:
            note, tone = "Approaching Oversold", "down"
        else:
            note, tone = "Neutral Range", "flat"
        out.append({"key": "rsi", "label": "RSI MOMENTUM",
                    "value": f"{rsi:.1f}",
                    "note": f"{note} · 14 × {interval} bars", "tone": tone})

    d4 = snap.get("trend_direction_4h")
    if d4 is not None and np.isfinite(d4):
        out.append({"key": "htf", "label": f"{htf.upper()} STRUCTURE",
                    "value": "BULL" if d4 > 0 else "BEAR",
                    "note": f"Trend on {htf} bars, above the {interval} chart",
                    "tone": "up" if d4 > 0 else "down"})

    vp = snap.get("vol_pctile")
    if vp is not None and np.isfinite(vp):
        out.append({"key": "vol", "label": "VOLATILITY",
                    "value": f"{vp * 100:.0f}%", "note": "Percentile of recent range",
                    "tone": "flat"})

    vb = snap.get("volume_vs_baseline")
    if vb is not None and np.isfinite(vb):
        out.append({"key": "volume", "label": "VOLUME",
                    "value": f"{vb:.2f}x", "note": "Against 30-day baseline",
                    "tone": "up" if vb > 1 else "flat"})
    return out


def _analysis(a) -> Dict[str, Any]:
    return {
        "name": a.name,
        "bars_left": a.bars_left,
        "opened_at": a.opened_at.isoformat(),
        "ends_at": a.ends_at.isoformat(),
        "p_up": _num(a.p_up),
        "p_down": _num(1.0 - a.p_up) if _num(a.p_up) is not None else None,
        "entry": _num(a.entry),
        "upper": _num(a.upper),
        "lower": _num(a.lower),
        "take_profit": _num(a.tp_price),
        "stop_loss": _num(a.sl_price),
        "ev_long": _num(a.ev_long),
        "ev_short": _num(a.ev_short),
        "action": a.action,
        "side": a.side,
        "size_pct": _num(a.size_pct),
        "reason": a.reason,
    }


def _choose(primary, secondary):
    """Which window the card speaks for.

    An entering window wins over a waiting one. This is its own function
    because the LEVELS have to describe the same window: showing a BUY from
    the 1-bar model above a probability and a TP/SL from the 2-bar model put
    "BUY" next to "51.7%" on screen, which reads as incoherent because the two
    numbers were answering different questions.
    """
    if primary.action.startswith("ENTER"):
        return primary
    return secondary if secondary.action.startswith("ENTER") else primary


def _recommendation(primary, secondary, stale: bool) -> Dict[str, Any]:
    """The big card. Whatever the backend actually decided - never a default BUY."""
    if stale:
        return {"action": "STALE", "tone": "flat",
                "detail": ("The newest bar is older than its follower. These "
                           "windows have expired - start the collector.")}
    a = _choose(primary, secondary)
    if a.action.startswith("ENTER"):
        tone = "up" if a.side == "LONG" else "down"
        word = "BUY" if a.side == "LONG" else "SELL"
        return {"action": word, "tone": tone, "detail": a.reason,
                "size_pct": _num(a.size_pct),
                "ev": _num(max(a.ev_long, a.ev_short)),
                "window_bars": a.bars_left,
                "p_up": _num(a.p_up),
                "window_ends": a.ends_at.isoformat()}
    return {"action": "FLAT", "tone": "flat",
            "detail": a.reason or "No window clears the EV threshold after costs.",
            "ev": _num(max(a.ev_long, a.ev_short)),
            "window_bars": a.bars_left,
            "p_up": _num(a.p_up),
            "window_ends": a.ends_at.isoformat()}


_SERVICE: Optional[TradingService] = None


def get_service() -> TradingService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = TradingService()
    return _SERVICE
