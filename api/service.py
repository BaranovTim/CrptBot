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
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

import config as project_config
from core import utc_now

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
        self.symbol, self.interval, self.asset = symbol, interval, asset
        self._lock = threading.Lock()
        self._monitor = None
        self._tickers: Dict[str, _Cached] = {}
        self._dash: Optional[_Cached] = None
        self._forming_feed = None

    # -- lazily built, because loading two models costs seconds ---------
    def _mon(self):
        if self._monitor is None:
            from monitor import Monitor
            self._monitor = Monitor(
                self.symbol, self.interval,
                MODEL_DIR / "judge_h1.joblib", MODEL_DIR / "judge_h2.joblib",
                asset=self.asset)
        return self._monitor

    def _bars(self) -> pd.DataFrame:
        from livefeed import BarStore
        return BarStore(self.symbol, self.interval).load()

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
    def trained_pairs(self) -> List[str]:
        have = ((MODEL_DIR / "judge_h1.joblib").exists()
                and (MODEL_DIR / "judge_h2.joblib").exists())
        return [self.symbol] if have else []

    def coins(self) -> List[Dict[str, Any]]:
        trained = set(self.trained_pairs())
        out = []
        for sym, name, short in UNIVERSE:
            t = self.ticker(sym)
            out.append({
                "symbol": sym,
                "name": name,
                "short": short,
                "pair": f"{short} / USDT",
                "price": _num(t["price"]),
                "change_pct": _num(t["change_pct"]),
                "trained": sym in trained,
            })
        return out

    # -------------------------------------------------------- dashboard
    def dashboard(self, ttl: float = 5.0) -> Dict[str, Any]:
        """Everything the dashboard screen renders, in one payload.

        Cached briefly: the heavy part is the feature frame, which `Monitor`
        already caches per closing bar, but the barrier arithmetic and the
        forming-bar fetch are re-done and a phone polling every second should
        not drive one REST call per poll.
        """
        with self._lock:
            if self._dash and time.time() - self._dash.at < ttl:
                return self._dash.value
            v = self._build_dashboard()
            self._dash = _Cached(time.time(), v)
            return v

    def _build_dashboard(self) -> Dict[str, Any]:
        from monitor import (atr_price_estimate, evaluate, indicator_snapshot)

        mon = self._mon()
        bars = self._bars()
        now = utc_now()
        if bars.empty:
            raise RuntimeError("no collected bars - run: python3 collect.py --seed 2024-01-01")

        X = mon.features(bars)
        snap = indicator_snapshot(X)
        last_close = bars.index[-1]
        close_price = float(bars["close"].iloc[-1])

        a = evaluate(mon.h1, bars, X, "ANALYSIS A",
                     opened_at=last_close - mon.delta,
                     ends_at=last_close + mon.delta, bars_left=1)
        b = evaluate(mon.h2, bars, X, "ANALYSIS B", opened_at=last_close,
                     ends_at=last_close + 2 * mon.delta, bars_left=2)

        # the same staleness rule the terminal uses. a window whose follower
        # has already closed is history, and the app must not paint it green
        stale_by = (now - (last_close + mon.delta)).total_seconds()
        stale = stale_by > 0

        live = self._live(bars, X, now)
        price = live.get("price") if live else None
        if price is None or not np.isfinite(price):
            price = close_price

        return {
            "symbol": self.symbol,
            "pair": f"{self.asset} / USDT",
            "interval": self.interval,
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
            "change_pct": _num(self.ticker(self.symbol)["change_pct"]),
            "live": live,
            "indicators": _indicators(snap),
            "analyses": [_analysis(a), _analysis(b)],
            "recommendation": _recommendation(b, a, stale),
            "levels": {
                "current": _num(price),
                "take_profit": _num(b.tp_price),
                "stop_loss": _num(b.sl_price),
            },
            "calibration_note": (
                "Probability is the chance a long entered at this bar's close "
                "reaches +1 ATR before -1 ATR within the window. On the last "
                "evaluation this feature set scored at chance, so treat it as "
                "a measurement of the model, not a forecast of the market."),
        }

    def _live(self, bars, X, now) -> Optional[Dict[str, Any]]:
        """The forming bar, if it is reachable. None is a normal answer."""
        from livefeed import FormingBarFeed, SpikeConfig, SpikeWatcher
        from monitor import atr_price_estimate

        if self._forming_feed is None:
            self._forming_feed = FormingBarFeed(self.symbol, self.interval)
        f = self._forming_feed.fetch(now)
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

    # ------------------------------------------------------------ chart
    def chart(self, n: int = 96) -> Dict[str, Any]:
        bars = self._bars()
        if bars.empty:
            return {"points": [], "interval": self.interval}
        tail = bars.tail(max(2, min(n, 500)))
        return {
            "interval": self.interval,
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
    def training(self, symbol: str) -> Dict[str, Any]:
        trained = symbol in set(self.trained_pairs())
        if not trained:
            return {
                "symbol": symbol,
                "trained": False,
                "title": "Untrained Model",
                "detail": ("No fitted model exists for this pair. Training "
                           "needs years of history, purged cross-validation "
                           "and an honest scoreboard - it is a batch job, not "
                           "a button."),
                "command": (f"python3 main.py --judge --hold 2 --k-up 1 "
                            f"--k-dn 1 --save-model output/judge_h2.joblib "
                            f"--start 2023-01-01"),
            }
        mon = self._mon()
        return {
            "symbol": symbol,
            "trained": True,
            "title": "Model Fitted",
            "horizons": [
                {"name": "h1", "bars": mon.h1.cfg.max_hold_bars,
                 "k_up": mon.h1.cfg.k_up, "k_dn": mon.h1.cfg.k_dn,
                 "features": len(mon.h1.columns)},
                {"name": "h2", "bars": mon.h2.cfg.max_hold_bars,
                 "k_up": mon.h2.cfg.k_up, "k_dn": mon.h2.cfg.k_dn,
                 "features": len(mon.h2.columns)},
            ],
            "detail": ("Fitted, calibrated and frozen. Retraining is offline "
                       "and gated - a model that updates on every tick is "
                       "chasing noise."),
        }


# ------------------------------------------------------------- helpers
def _num(v) -> Optional[float]:
    """NaN and inf are not JSON. None means 'absent', which is the truth."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _indicators(snap: Dict[str, float]) -> List[Dict[str, Any]]:
    """The two cards the design shows, plus regime. Values are last CLOSED bar."""
    out = []
    rsi = snap.get("rsi_14")
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
                    "value": f"{rsi:.1f}", "note": note, "tone": tone})

    d4 = snap.get("trend_direction_4h")
    if d4 is not None and np.isfinite(d4):
        out.append({"key": "htf", "label": "4H STRUCTURE",
                    "value": "BULL" if d4 > 0 else "BEAR",
                    "note": "Higher-timeframe trend",
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


def _recommendation(primary, secondary, stale: bool) -> Dict[str, Any]:
    """The big card. Whatever the backend actually decided - never a default BUY."""
    if stale:
        return {"action": "STALE", "tone": "flat",
                "detail": ("The newest bar is older than its follower. These "
                           "windows have expired - start the collector.")}
    a = primary if primary.action.startswith("ENTER") else (
        secondary if secondary.action.startswith("ENTER") else primary)
    if a.action.startswith("ENTER"):
        tone = "up" if a.side == "LONG" else "down"
        word = "BUY" if a.side == "LONG" else "SELL"
        return {"action": word, "tone": tone, "detail": a.reason,
                "size_pct": _num(a.size_pct),
                "ev": _num(max(a.ev_long, a.ev_short)),
                "window_ends": a.ends_at.isoformat()}
    return {"action": "FLAT", "tone": "flat",
            "detail": a.reason or "No window clears the EV threshold after costs.",
            "ev": _num(max(a.ev_long, a.ev_short)),
            "window_ends": a.ends_at.isoformat()}


_SERVICE: Optional[TradingService] = None


def get_service() -> TradingService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = TradingService()
    return _SERVICE
