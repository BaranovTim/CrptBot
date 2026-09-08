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
import os
import queue
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
from core import (TIMEFRAMES, htf_for, interval_seconds, is_trained,
                  model_paths, utc_now)

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
        # one lock per pair, so a slow build blocks only its own timeframe
        self._locks: Dict[Tuple[str, str], threading.Lock] = {}
        self._building: set = set()
        self._build_cost: Dict[Tuple[str, str], float] = {}
        self._last_seen: Dict[Tuple[str, str], float] = {}
        self._charts: Dict[Tuple[str, str, int, Any], Dict[str, Any]] = {}
        self._bars_cache: Dict[Tuple[str, str], Tuple[Any, Any]] = {}
        # BUMP THIS when the dashboard payload gains or renames a field.
        # v1 -> v2 on 2026-08-31, when `strength` and `p_needed` were added:
        # the restored cache kept serving payloads without them and the new
        # app read null for both. The versioned name is the whole defence
        # against a cache outliving the shape it was written for.
        #
        # v2 -> v3 on 2026-09-05, when `levels` gained `side` and the signed
        # `tp_offset_pct`/`sl_offset_pct`. Same failure, caught the same way:
        # the deploy went out, the payload came back without the new fields,
        # and the app would have fallen back to drawing a short's levels the
        # long way round — the exact bug being fixed.
        self._dash_dir = Path("data_cache") / "dashcache.v3"
        self._refresh_q: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._monitors: Dict[Tuple[str, str], Any] = {}
        self._tickers: Dict[str, _Cached] = {}
        self._dash: Dict[Tuple[str, str], _Cached] = {}
        self._forming: Dict[Tuple[str, str], Any] = {}
        # LAST. It populates `self._dash`, so every field it touches has to
        # exist first — it did not, and the broad except below turned that
        # AttributeError into 24 "corrupt cache file" warnings instead of a
        # crash, which is why it failed silently.
        self._load_dash_cache()

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
            self._evict_monitors()
        return self._monitors[key]

    # Each Monitor holds two fitted LightGBM models. Unbounded, this grew one
    # entry per pair AND timeframe — 14 symbols across six timeframes is 84
    # of them, all resident, on a box with 967MB.
    MAX_MONITORS = int(os.environ.get("MAX_MONITORS", "24"))

    def _evict_monitors(self) -> None:
        """Keep the most recently asked-for monitors, drop the rest.

        Rebuilding one is a `joblib.load` of two small files, so losing a
        cold pair costs milliseconds the next time it is asked for — far
        cheaper than the alternative, which was the whole process dying.
        """
        if len(self._monitors) <= self.MAX_MONITORS:
            return
        order = sorted(self._monitors,
                       key=lambda k: self._last_seen.get(k, 0.0))
        for k in order[:len(self._monitors) - self.MAX_MONITORS]:
            self._monitors.pop(k, None)

    def _bars(self, symbol: Optional[str] = None,
              interval: Optional[str] = None) -> pd.DataFrame:
        """The bar store, cached until the files underneath it change.

        THIS WAS THE REAL COST. Every caller re-read and re-parsed the whole
        store: 175,000 rows of CSV for a 1m pair, measured at 2.4-5.9s
        depending on timeframe, on every chart request and every dashboard
        build. On a one-core box with four pairs being prefetched that is
        tens of seconds of work queued behind whatever the phone is waiting
        for, which is what produced the client timeouts.

        Validated by MTIME AND SIZE of the files, not by a clock. A stat is
        microseconds against seconds of parsing, and it is exact: the moment
        the collector appends a bar the signature changes and the next read
        reloads. A time-based TTL would either serve a stale bar or reload
        for nothing.

        The frame is returned as-is rather than copied. Callers here treat it
        as read-only; copying 175,000 rows per call would give back most of
        what the cache saves.
        """
        sym, iv = self._pair(symbol, interval)
        from livefeed import BarStore

        store = BarStore(sym, iv)
        try:
            sig = tuple(sorted(
                (f.name, f.stat().st_mtime_ns, f.stat().st_size)
                for f in store.dir.glob("*.csv")))
        except OSError:
            sig = None                      # unreadable: fall through to load

        if sig is not None:
            with self._lock:
                hit = self._bars_cache.get((sym, iv))
            if hit is not None and hit[0] == sig:
                return hit[1]

        bars = store.load()
        if sig is not None:
            with self._lock:
                self._bars_cache[(sym, iv)] = (sig, bars)
                self._evict_bars()
        return bars

    # How much memory the bar cache may hold, in megabytes.
    #
    # MEASURED, and the reason this exists at all. The comment that used to
    # sit here said one frame per pair "adds no meaningful memory". That was
    # true of four pairs and false of fourteen: a single 1m frame is 36MB
    # (185,760 rows), so 14 symbols on 1m alone is over 500MB before the
    # other five timeframes. The cache was unbounded, the droplet has 967MB,
    # and the API met the OOM killer at 724-802MB five times.
    #
    # A build itself peaks around 300MB, so the builds were never the
    # problem — what accumulated between them was.
    BARS_BUDGET_MB = float(os.environ.get("BARS_BUDGET_MB", "180"))

    def _evict_bars(self) -> None:
        """Drop least-recently-asked-for bar frames until under budget.

        Caller holds `_lock`. Recency comes from `_last_seen`, which
        `dashboard()` already stamps — a pair nobody has opened is the right
        thing to lose, and reloading it is a disk read rather than a rebuild.
        """
        def mb(frame) -> float:
            try:
                return float(frame.memory_usage(deep=True).sum()) / 1e6
            except Exception:
                return 0.0

        total = sum(mb(v[1]) for v in self._bars_cache.values())
        if total <= self.BARS_BUDGET_MB:
            return
        # oldest attention first; never evict what we just inserted
        order = sorted(self._bars_cache,
                       key=lambda k: self._last_seen.get(k, 0.0))
        for k in order:
            if total <= self.BARS_BUDGET_MB or len(self._bars_cache) <= 1:
                break
            total -= mb(self._bars_cache[k][1])
            self._bars_cache.pop(k, None)

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

    # ------------------------------------------------- surviving a restart
    #
    # Building a dashboard costs 15-60s on the droplet, and until this existed
    # every restart threw all 24 of them away. Warm-up then saturated the one
    # core for ten minutes, and any request landing in that window queued
    # behind a cold build and blew through the app's 20s timeout - which is
    # exactly the "timeout when I reopen the app" people were seeing.
    #
    # The payloads are small JSON and already carry their own staleness flag,
    # so writing them out and reading them back on boot means the app is
    # answered IMMEDIATELY after a restart, from the last known good copy,
    # while the real rebuild happens behind it. Nothing is served that the
    # process would not have served a moment before it restarted.

    def _dash_path(self, key: Tuple[str, str]) -> Path:
        return self._dash_dir / f"{key[0]}_{key[1]}.json"

    def _load_dash_cache(self) -> None:
        if not self._dash_dir.exists():
            return
        loaded = 0
        for f in self._dash_dir.glob("*.json"):
            try:
                raw = json.loads(f.read_text())
                sym, iv = raw["symbol"], raw["interval"]
                self._dash[(sym, iv)] = _Cached(float(raw["at"]),
                                                raw["payload"])
                loaded += 1
            except (KeyError, ValueError, TypeError, OSError) as e:
                # A malformed or half-written file must not stop the rest
                # loading. Deliberately NOT a bare Exception: that swallowed
                # an AttributeError from this method being called too early
                # and reported it as a corrupt cache, 24 times.
                log.warning("dash cache %s ignored: %s", f.name, e)
        if loaded:
            log.info("restored %d dashboards from disk", loaded)

    def _save_dash(self, key: Tuple[str, str], value: Dict[str, Any],
                   at: float) -> None:
        try:
            self._dash_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dash_path(key).with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {"symbol": key[0], "interval": key[1], "at": at,
                 "payload": value}, allow_nan=False))
            os.replace(tmp, self._dash_path(key))
        except (OSError, ValueError) as e:
            # a cache that cannot be written is a slow restart, not a failure
            log.warning("could not persist %s %s: %s", key[0], key[1], e)

    # -------------------------------------------------------- dashboard
    def _dash_ttl(self, key: Tuple[str, str]) -> float:
        """How long a built dashboard is served before a refresh is kicked off.

        A quarter of the bar period -- the expensive part only changes when a
        bar closes, and the phone gets its tick-by-tick price from the Binance
        websocket, not from here.

        But ALSO at least four times what the last build actually cost. On a
        laptop a 1m build is ~3s and this is irrelevant. On the one-core
        droplet it is 27s, against a 30s bar-derived TTL -- which would leave
        the box rebuilding 1m continuously and starving every other timeframe.
        Measuring instead of assuming means the same code adapts to whatever
        hardware it lands on rather than needing a config knob per host.
        """
        floor = interval_seconds(key[1]) / 4.0
        cost = self._build_cost.get(key, 0.0)
        return min(max(floor, cost * 4.0, 30.0), 900.0)

    def _key_lock(self, key: Tuple[str, str]) -> threading.Lock:
        """One lock per (symbol, interval), so a slow 1m build cannot block 4h."""
        with self._lock:
            lk = self._locks.get(key)
            if lk is None:
                lk = self._locks[key] = threading.Lock()
            return lk

    def _build_and_store(self, key: Tuple[str, str]) -> Dict[str, Any]:
        with self._key_lock(key):
            # somebody may have finished building while we queued on the lock
            with self._lock:
                hit = self._dash.get(key)
            if hit and time.time() - hit.at < 1.0:
                return hit.value
            t0 = time.time()
            v = self._build_dashboard(*key)
            cost = time.time() - t0
            now = time.time()
            with self._lock:
                self._dash[key] = _Cached(now, v)
                self._build_cost[key] = cost
            self._save_dash(key, v, now)
            return v

    # How long after somebody last asked for a pair it keeps refreshing itself.
    #
    # MEASURED, and the reason this exists: warming all 24 pairs and then
    # refreshing all 24 forever does not fit on one core. The 1m tier alone
    # needs ~30s of rebuild per coin against a 120s TTL - 100% of a core for
    # four coins, before the other five timeframes or the collector get a
    # look in. CPU sat at 100% and everything queued behind it.
    #
    # So refresh follows attention. A pair nobody has opened for half an hour
    # stops rebuilding and simply goes stale; the next request serves the
    # stale copy instantly and schedules one rebuild. Nothing is lost except
    # work nobody was waiting for.
    ATTENTION_WINDOW = 1800.0

    def _refresh_soon(self, key: Tuple[str, str]) -> None:
        """Queue `key` for rebuild off the request path. Caller holds _lock.

        Queued, not spawned. Six timeframes refreshing concurrently on a
        single core makes all six slower than doing them one after another,
        and briefly doubles resident memory. One worker drains this serially.
        """
        if key in self._building:
            return
        # only keep refreshing what somebody is actually looking at
        seen = self._last_seen.get(key, 0.0)
        if time.time() - seen > self.ATTENTION_WINDOW:
            return
        self._building.add(key)
        self._refresh_q.put(key)
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._refresh_loop,
                                            daemon=True, name="refresh")
            self._worker.start()

    def _refresh_loop(self) -> None:
        while True:
            try:
                key = self._refresh_q.get(timeout=300)
            except queue.Empty:
                return                      # idle: let the thread go
            try:
                self._build_and_store(key)
            except Exception as e:          # a failed refresh must never take
                log.warning("refresh %s %s: %s", key[0], key[1], e)
            finally:                        # down the copy already being served
                with self._lock:
                    self._building.discard(key)

    def dashboard(self, symbol: Optional[str] = None,
                  interval: Optional[str] = None,
                  ttl: Optional[float] = None) -> Dict[str, Any]:
        """Everything the dashboard screen renders, in one payload.

        Stale-while-revalidate, and NOT under the global lock. Both matter on
        a one-core box, where building this costs 10-46s per timeframe:

          - building inside `self._lock` meant one slow 1m build stalled every
            other request in the app, so switching to 1m made 4h time out too.
          - a 5s TTL against a 46s build meant the cache was always expired,
            so it rebuilt forever and never recovered.

        Now an expired entry is still returned immediately and refreshed
        behind the request. Only a pair that has never been built blocks, and
        `warm()` exists so that normally happens at boot rather than under a
        phone waiting on it.
        """
        key = self._pair(symbol, interval)
        ttl = self._dash_ttl(key) if ttl is None else ttl
        with self._lock:
            self._last_seen[key] = time.time()
            hit = self._dash.get(key)
            if hit:
                if time.time() - hit.at >= ttl:
                    self._refresh_soon(key)
                return hit.value
        return self._build_and_store(key)

    def trained_symbols(self) -> List[str]:
        """Every pair with at least one fitted model, from `output/`.

        The server has no watchlist - that lives on each device - so the
        honest definition of "a pair this server serves" is "a pair it has a
        model for".
        """
        from core import model_paths

        found = set()
        out = Path("output")
        if not out.exists():
            return [self.symbol]
        for f in out.glob("judge_*_h1.joblib"):
            # judge_<SYMBOL>_<interval>_h1.joblib
            parts = f.stem.split("_")
            if len(parts) >= 4:
                found.add(parts[1].upper())
        # the legacy judge_h1.joblib carries no symbol; it is BTCUSDT's
        if (out / "judge_h1.joblib").exists():
            found.add(self.symbol)
        del model_paths
        return sorted(found) or [self.symbol]

    def warm(self, symbol: Optional[str] = None) -> None:
        """Build every trained pair and timeframe in the background at startup.

        This used to warm ONE symbol - whichever was the service default - and
        the effect was measured on the droplet: BTCUSDT answered in 14ms while
        the first tap on ETHUSDT 15m took 76 SECONDS, because it was still
        cold. Adding coins quietly made the app feel broken for three
        quarters of them.

        CHEAPEST INTERVAL FIRST, ACROSS ALL SYMBOLS. Warming symbol-by-symbol
        would leave the last pair cold for the whole run; going interval by
        interval means every pair has its 1d, then its 4h, then its 1h, and so
        on. The slow ones (1m costs ~100s each) come last, when the screens
        people actually open are already warm.
        """
        syms = [symbol.upper()] if symbol else self.trained_symbols()

        def run() -> None:
            # dearest last: 1m is ~100s a pair, 1d is ~22s
            order = ["1d", "4h", "1h", "15m", "5m", "1m"]
            t_start = time.time()
            done = 0
            for tf in order:
                for sym in syms:
                    if not is_trained(sym, tf):
                        continue
                    try:
                        t0 = time.time()
                        self._build_and_store((sym, tf))
                        done += 1
                        log.info("warmed %s %s in %.1fs (%d done)",
                                 sym, tf, time.time() - t0, done)
                    except Exception as e:
                        log.warning("warm %s %s: %s", sym, tf, e)
            log.info("warm-up complete: %d pairs in %.0fs",
                     done, time.time() - t_start)

        threading.Thread(target=run, daemon=True, name="warm").start()

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

        # THE FORWARD RECORD.
        #
        # Written here because this is the moment the forecast exists and the
        # outcome does not. Anything reconstructed later — however carefully —
        # is a backtest wearing a track record's clothes.
        #
        # Deduped on the closed bar, so the dozens of dashboard rebuilds per
        # bar produce one row, not dozens of copies of the same call inflating
        # every metric downstream.
        self._record_forward(symbol, interval, mon, last_close, close_price,
                             a, b)

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
            "levels": _levels(chosen, price),
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
    def price_range(self, symbol: Optional[str] = None,
                    interval: str = "1m",
                    since: Optional[str] = None) -> Dict[str, Any]:
        """The high and low since `since`, plus the latest close.

        WHY THE EXTREMES AND NOT THE CURRENT PRICE
            The app closes a logged trade when price touched the take profit
            or the stop loss it recorded. "Touched" is a fact about the whole
            interval since the entry, not about the moment somebody happened
            to open the app: a wick through a stop at 3am that retraced by
            morning still took the trade out, and comparing the current price
            would miss it every time.

        1m by default, because resolution is the entire point. A touch that
        lasted ninety seconds is invisible on a 1h candle.
        """
        sym, iv = self._pair(symbol, interval)
        bars = self._bars(sym, iv)
        if bars.empty:
            return {"symbol": sym, "interval": iv, "bars": 0,
                    "high": None, "low": None, "last": None}
        window = bars
        if since:
            try:
                cut = pd.Timestamp(since)
                if cut.tzinfo is None:
                    cut = cut.tz_localize("UTC")
                # The bar CONTAINING the entry is excluded: its extremes
                # include movement from before the trade existed, and using
                # them would close a position on a wick that predates it.
                window = bars[bars.index > cut]
            except (ValueError, TypeError):
                pass
        if window.empty:
            # No bar has closed since the entry yet. Not an error — there is
            # simply nothing to judge against, and a caller must not read
            # that as "nothing was touched".
            return {"symbol": sym, "interval": iv, "bars": 0,
                    "high": None, "low": None,
                    "last": _num(bars["close"].iloc[-1])}
        return {
            "symbol": sym,
            "interval": iv,
            "bars": int(len(window)),
            "from": window.index[0].isoformat(),
            "to": window.index[-1].isoformat(),
            "high": _num(window["high"].max()),
            "low": _num(window["low"].min()),
            "last": _num(window["close"].iloc[-1]),
        }

    def chart(self, symbol: Optional[str] = None,
              interval: Optional[str] = None, n: int = 96) -> Dict[str, Any]:
        """The last `n` closes. Cached, because it was the slowest thing here.

        MEASURED, and it was the cause of the app's timeouts. This returned at
        most 500 points but re-read and re-parsed the WHOLE bar store to get
        them - 175,000 rows of CSV for a 1m pair - on every single call:

            1m 5.9s   5m 5.3s   15m 3.8s   1h 2.6s   4h 3.0s   1d 2.4s

        while the dashboard beside it answered in 7ms. Worse, the app
        prefetches its neighbouring pairs, so opening a screen fired six of
        these and put 11.4s of work on a one-core box, queued behind the
        request the phone was actually waiting on. The 20s client timeout did
        not stand a chance.

        Keyed on the newest bar, so it is exact rather than time-based: a new
        bar closes, the key changes, the chart rebuilds. Nothing serves a
        stale candle.
        """
        sym, iv = self._pair(symbol, interval)
        want = max(2, min(n, 500))
        bars = self._bars(sym, iv)
        if bars.empty:
            return {"points": [], "interval": iv}

        key = (sym, iv, want, bars.index[-1])
        with self._lock:
            hit = self._charts.get(key)
            if hit is not None:
                return hit

        tail = bars.tail(want)
        out = {
            "interval": iv,
            "points": [{"t": t.isoformat(), "c": _num(c)}
                       for t, c in zip(tail.index, tail["close"])],
        }
        with self._lock:
            # bounded: one entry per pair, timeframe and length, and the key
            # moves every time a bar closes. Without a cap a 1m pair would
            # leave a dead entry behind every minute.
            if len(self._charts) > 64:
                self._charts.clear()
            self._charts[key] = out
        return out

    # ------------------------------------------------------ forward record
    #
    # RECORDED ON A CLOCK, NOT ON ATTENTION.
    #
    # Dashboards only rebuild when somebody looks at them - that is what keeps
    # the one-core box idle. If the forward record inherited that, it would
    # contain exactly the bars Tim happened to open the app for, and people
    # open trading apps when something is happening. The sample would be
    # biased toward volatile bars and every metric computed from it would be
    # measuring that bias.
    #
    # So a separate loop wakes on each bar close and records for every pair,
    # whether or not anyone is watching. Only the timeframes named here: 1h
    # is where the measured edge is, and four builds an hour at ~20s each is
    # about 2% of one core. Recording 1m for four symbols would be 100%.
    # 1m and 5m are deliberately absent and cannot be added on this box: a
    # build takes ~24s on the single shared vCPU (measured: 24 pairs warmed
    # in 587s) and a 1m bar closes every 60s, so four coins could never keep
    # up. These four cost about 14% of the core.
    RECORD_INTERVALS = ("15m", "1h", "4h", "1d")

    def record_symbols(self) -> List[str]:
        """Which pairs the recorder and the alert engine keep warm.

        NOT "everything with a model", which is what it used to be and what
        killed this box. `trained_symbols()` reads `output/`, so rsyncing ten
        new coins' models onto the server silently took the warm set from 6
        symbols to 14 — 56 dashboards on one core. A single cold build peaks
        near 750MB against 967MB of RAM, so the recorder walked into the OOM
        killer three times in an hour and took the API down with it each time.

        Bounded here, by name, through the environment. Pairs left out are
        still fully served — they are built when someone asks for them, and
        cached afterwards. What they lose is being pre-warmed and being
        watched for alerts, which is a real cost and the reason this is a
        setting rather than a hardcoded four.
        """
        raw = os.environ.get("RECORD_SYMBOLS", "").strip()
        if raw:
            want = [s.strip().upper() for s in raw.split(",") if s.strip()]
            have = set(self.trained_symbols())
            return [s for s in want if s in have]
        return self.trained_symbols()

    def start_recorder(self, intervals: Optional[Tuple[str, ...]] = None,
                       symbols: Optional[List[str]] = None) -> None:
        ivs = tuple(intervals or self.RECORD_INTERVALS)
        syms = symbols or self.record_symbols()

        # The last bar we actually recorded, per pair.
        #
        # The loop wakes on the SHORTEST interval in the set, so with 15m in
        # the list it wakes four times an hour. Rebuilding everything on each
        # wake would rebuild 1d ninety-six times a day to record the same
        # bar once — the dedupe would drop the duplicates, but the CPU would
        # already have been spent. Each pair is rebuilt only when its own bar
        # has closed.
        seen: Dict[Tuple[str, str], Any] = {}

        def loop() -> None:
            import time as _t
            while True:
                try:
                    _t.sleep(self._sleep_to_next_close(ivs))
                    built = []
                    for iv in ivs:
                        for sym in syms:
                            if not is_trained(sym, iv):
                                continue
                            try:
                                bars = self._bars(sym, iv)
                                if bars.empty:
                                    continue
                                last = bars.index[-1]
                                if seen.get((sym, iv)) == last:
                                    continue          # no new bar for this one
                                # a real build, which records as a side
                                # effect and refreshes the cache too
                                self._build_and_store((sym, iv))
                                seen[(sym, iv)] = last
                                built.append(f"{sym} {iv}")
                            except Exception as e:
                                log.warning("record %s %s: %s", sym, iv, e)
                    if built:
                        log.info("forward record: %s", ", ".join(built))
                except Exception as e:
                    log.warning("recorder loop: %s", e)
                    _t.sleep(60)

        threading.Thread(target=loop, daemon=True, name="recorder").start()
        log.info("forward recorder started for %s on %s",
                 ", ".join(ivs), ", ".join(syms))

    @staticmethod
    def _sleep_to_next_close(intervals: Tuple[str, ...]) -> float:
        """Seconds until the next bar closes, plus a margin.

        The margin is not politeness: a bar labelled 13:00-13:59:59.999 is
        only in the store once the collector has fetched and written it, and
        building at exactly 14:00:00 would read the previous bar and record a
        forecast for a bar that has already been recorded.
        """
        now = utc_now().timestamp()
        step = min(interval_seconds(iv) for iv in intervals)
        return (step - (now % step)) + 45.0


    def _record_forward(self, symbol: str, interval: str, mon,
                        last_close, close_price: float, a, b) -> None:
        """Log both horizons' calls for this closed bar, and settle old ones.

        Never raises into the dashboard. A track record that can take the app
        down is a track record that gets switched off.
        """
        # ONLY the intervals the clock covers.
        #
        # `_build_dashboard` runs for any reason — somebody opening the app,
        # a cache refresh, warm-up. Recording those would put sporadic,
        # attention-driven rows in the file for every other timeframe, and a
        # record that is dense when you were watching and empty when you were
        # not measures your habits, not the model. Observed immediately: a
        # 1d row appeared for a timeframe the recorder does not cover.
        #
        # For 1h this changes nothing — the clock has already written that
        # bar and `record()` dedupes — but it guarantees every row in the
        # file arrived on a schedule.
        if interval not in self.RECORD_INTERVALS:
            return
        try:
            from agent5.track import Prediction, TrackRecord, model_fingerprint
            from core import model_paths, utc_now

            h1p, h2p = model_paths(symbol, interval)
            rec = TrackRecord(symbol, interval)
            now_iso = utc_now().isoformat()
            wrote = 0
            for horizon, analysis, path, model in (
                    ("h1", a, h1p, mon.h1), ("h2", b, h2p, mon.h2)):
                p_up = getattr(analysis, "p_up", None)
                if p_up is None or not np.isfinite(p_up):
                    continue
                cfg = getattr(model, "cfg", None)
                wrote += rec.record(Prediction(
                    symbol=symbol, interval=interval, horizon=horizon,
                    bar_close=last_close.isoformat(), logged_at=now_iso,
                    model_id=model_fingerprint(path),
                    p_up=float(p_up),
                    action=str(getattr(analysis, "action", "")),
                    ev=_num(getattr(analysis, "ev", float("nan"))),
                    close=close_price,
                    k_up=getattr(cfg, "k_up", None),
                    k_dn=getattr(cfg, "k_dn", None),
                    max_hold_bars=getattr(cfg, "max_hold_bars", None)))

            # settle whatever has come due. Cheap: it only relabels when
            # there is something unresolved, and only once per new bar.
            if wrote:
                bars = self._bars(symbol, interval)
                rec.resolve(bars, {"h1": getattr(mon.h1, "cfg", None),
                                   "h2": getattr(mon.h2, "cfg", None)})
        except Exception as e:
            log.warning("forward record %s %s: %s", symbol, interval, e)

    def track(self, symbol: Optional[str] = None,
              interval: Optional[str] = None) -> Dict[str, Any]:
        """The forward record for one pair, resolved up to now."""
        from agent5.track import TrackRecord

        sym, iv = self._pair(symbol, interval)
        rec = TrackRecord(sym, iv)
        try:
            mon = self._mon(sym, iv)
            rec.resolve(self._bars(sym, iv),
                        {"h1": getattr(mon.h1, "cfg", None),
                         "h2": getattr(mon.h2, "cfg", None)})
        except Exception as e:
            log.warning("track resolve %s %s: %s", sym, iv, e)
        return rec.metrics()

    # ------------------------------------------------- indicator history
    #
    # The dashboard tiles showed one number each with no sense of where it
    # came from: RSI 53.9 says nothing about whether it has been climbing for
    # a day or just snapped back from 70.
    #
    # The series come straight out of the SAME cached feature frame the
    # dashboard already built, so this costs a slice and no computation.
    # Overlaying them on the price chart was the alternative and would have
    # been worse: RSI is 0-100, volume is a ratio around 1, and structure is
    # +1/-1 — sharing a price axis with BTC at 78,000 would either flatten
    # them to a line or need a second axis nobody reads correctly.

    # tile key -> (feature column, human label, unit, sensible fixed bounds)
    INDICATOR_SERIES = {
        "rsi": ("rsi_14", "RSI (14)", "", (0.0, 100.0)),
        "vol": ("vol_pctile", "Volatility percentile", "%", (0.0, 1.0)),
        "volume": ("volume_vs_baseline", "Volume vs baseline", "x", None),
        "htf": ("trend_direction_4h", "Higher-timeframe structure", "",
                (-1.0, 1.0)),
        "flow": ("taker_buy_ratio", "Taker buy ratio", "", (0.0, 1.0)),
        "atr": ("atr_pct", "ATR (% of price)", "%", None),
    }

    def indicator(self, key: str, symbol: Optional[str] = None,
                  interval: Optional[str] = None,
                  n: int = 96) -> Dict[str, Any]:
        """Recent history of one indicator, for the tile to expand into."""
        spec = self.INDICATOR_SERIES.get(key)
        if spec is None:
            raise KeyError(f"unknown indicator {key!r}")
        column, label, unit, bounds = spec

        sym, iv = self._pair(symbol, interval)
        mon = self._mon(sym, iv)
        bars = self._bars(sym, iv)
        if bars.empty:
            return {"key": key, "label": label, "points": []}

        X = mon.features(mon.live_window(bars))
        if X is None or column not in X.columns:
            # a model fitted without that block simply has no such column;
            # an empty series is the honest answer, not a fabricated one
            return {"key": key, "label": label, "unit": unit, "points": [],
                    "note": "not produced by this model"}

        want = max(2, min(n, 500))
        tail = X[column].tail(want)
        pts = [{"t": t.isoformat(), "v": _num(v)}
               for t, v in zip(tail.index, tail.to_numpy())]
        finite = [p["v"] for p in pts if p["v"] is not None]
        return {
            "key": key,
            "label": label,
            "unit": unit,
            "interval": iv,
            "symbol": sym,
            "points": pts,
            # bounds so a sparkline is comparable between visits rather than
            # rescaling to whatever happens to be on screen
            "min": bounds[0] if bounds else (min(finite) if finite else None),
            "max": bounds[1] if bounds else (max(finite) if finite else None),
            "explain": _INDICATOR_HELP.get(key, ""),
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
                # the filing itself on sec.gov, so "explain this" can end at
                # the primary source rather than at our paraphrase of it
                "url": getattr(e, "url", "") or "",
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

    def news(self, limit: int = 20,
             symbol: Optional[str] = None,
             market: str = "crypto") -> List[Dict[str, Any]]:
        try:
            from newsfeed.store import JSONLNewsStore

            if market == "stocks":
                # A SEPARATE STORE, not a filter over one.
                #
                # The crypto feed's asset tagger matches tickers as words —
                # "ADA" inside "Canada". Pointing it at 13,000 US symbols
                # would tag half the news wrongly, because two and three
                # letter equity tickers collide with ordinary English far
                # worse than crypto's handful do.
                items = JSONLNewsStore(
                    Path("data_cache") / "news_equities").load_items()
            else:
                items = JSONLNewsStore().load_items()
        except Exception as e:
            log.warning("news store unreadable: %s", e)
            return []
        # Sort THEN filter. Slicing the newest 20 first and filtering after
        # would return "the newest 20 overall that happen to mention SOL",
        # which is usually nothing at all.
        items = sorted(items,
                       key=lambda i: getattr(i, "published_at", None) or utc_now(),
                       reverse=True)

        asset = None
        if symbol:
            if market == "stocks":
                asset = symbol.upper().strip() or None
            else:
                base = symbol.upper().replace("USDT", "").replace("USD", "")
                asset = base or None

        # Direction and size, from Agent 3's offline scorer.
        #
        # It is a keyword count, not judgement, and it says so: on 40 real
        # headlines it produces a reading for 24 and stays silent on the rest.
        # Silence is reported as "NO READING" rather than dressed up as
        # NEUTRAL — "we cannot tell" and "this is balanced news" are
        # different claims and only one of them is true.
        #
        # `ClaudeScorer` in the same module does the job properly and needs
        # an API key. Until then the app labels what it can and admits the
        # rest.
        scored = {}
        try:
            from agent3.scorers import LexiconScorer

            for it, sc in zip(items, LexiconScorer().score(items)):
                scored[id(it)] = sc
        except Exception as e:
            log.warning("news scoring unavailable: %s", e)

        out = []
        for it in items:
            assets = tuple(getattr(it, "assets", ()) or ())
            macro = str((getattr(it, "meta", {}) or {}).get("macro", "0")) == "1"
            if asset is not None and assets and asset not in assets and not macro:
                # Macro headlines belong to EVERY coin: "Fed holds rates" is
                # about SOL too, and filing it under nothing would hide the
                # items most likely to move the market. Untagged items are
                # kept rather than dropped — the tagger not recognising a
                # coin is not evidence the story is irrelevant.
                continue
            sc = scored.get(id(it))
            direction = float(getattr(sc, "direction", 0.0) or 0.0) if sc else 0.0
            magnitude = float(getattr(sc, "magnitude", 0.0) or 0.0) if sc else 0.0
            if magnitude <= 0:
                bias, impact = "NO READING", "NO READING"
            else:
                bias = ("BULL" if direction > 0.15
                        else "BEAR" if direction < -0.15 else "MIXED")
                impact = ("STRONG IMPACT" if magnitude >= 0.5
                          else "MEDIUM IMPACT" if magnitude >= 0.25
                          else "ALMOST NO IMPACT")

            out.append({
                "headline": getattr(it, "headline", str(it)),
                "source": getattr(it, "source", ""),
                # The publisher's own excerpt, which is what an RSS
                # description is for. NOT the article body: reproducing that
                # in full would be republishing someone else's work, which is
                # why the link goes at the bottom of every summary.
                "summary": (getattr(it, "body", "") or "")[:400],
                # the article itself. Without this the app can tell you
                # something happened and not show you what.
                "url": getattr(it, "url", "") or "",
                "assets": list(assets),
                "macro": macro,
                "bias": bias,
                "impact": impact,
                "direction": _num(direction),
                "magnitude": _num(magnitude),
                "published_at": getattr(it, "published_at", None)
                and it.published_at.isoformat(),
            })
            if len(out) >= limit:
                break
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
# Written for somebody who has not read a textbook, and honest about what
# each one cannot tell you. A tile that explains itself beats a tile that
# looks authoritative.
_INDICATOR_HELP = {
    "rsi": "Momentum over the last 14 bars, 0-100. Above 70 is usually called "
           "overbought and below 30 oversold — but a strong trend can sit at "
           "an extreme for a long time, so this describes speed, not a turn.",
    "vol": "Where current volatility sits against its own recent range. High "
           "means moves are larger than usual, which widens both the target "
           "and the stop; it says nothing about direction.",
    "volume": "Traded volume against a 30-day baseline. Above 1x means more "
              "participation than normal. A move on low volume is easier to "
              "reverse than the same move on high volume.",
    "htf": "Direction of the last confirmed break of structure on the higher "
           "timeframe: +1 after a higher high, -1 after a lower low. It is "
           "the chart's shape, not a recommendation.",
    "flow": "Share of volume executed by buyers lifting the offer. Above 0.5 "
            "means buyers were more aggressive over that bar.",
    "atr": "Average true range as a percentage of price — the size of a "
           "typical bar. Targets and stops are set in multiples of this, "
           "which is why they widen when it does.",
}


def _num(v) -> Optional[float]:
    """NaN and inf are not JSON. None means 'absent', which is the truth."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None



def _levels(a, price) -> Dict[str, Any]:
    """The take-profit and stop-loss the app draws, and the distances it
    re-anchors them by.

    SEPARATE FROM `dashboard` BECAUSE THE DIRECTION IS EASY TO GET WRONG
        `tp_pct`/`sl_pct` were magnitudes with no direction in them, and the
        app moved take-profit UP and stop-loss DOWN from the live price
        whatever the call said. On a SELL that put the target and the stop on
        each other's side of the price — with the word SELL printed directly
        above them. The prices were always right; the distances the app
        actually draws were not.
    """
    return {
        "current": _num(price),
        # the SAME window the recommendation speaks for
        "window_bars": a.bars_left,
        "p_up": _num(a.p_up),
        "take_profit": _num(a.tp_price),
        "stop_loss": _num(a.sl_price),
        # WHICH WAY THE TRADE GOES, or "" when none is proposed.
        #
        # Without this the app could not re-anchor the levels
        # correctly: it was moving take-profit UP and stop-loss DOWN
        # from the live price whatever the call was, so a SELL showed
        # its target above the price and its stop below — both on the
        # wrong side, and exactly backwards for the trade named right
        # above them. The prices in `take_profit`/`stop_loss` were
        # always right; the DISTANCES were not, and the app draws the
        # distances whenever a websocket price is available, which on
        # crypto is essentially always.
        "side": a.side,
        # the barriers as distances rather than prices. the app
        # re-anchors them to the websocket price so TP/SL track the
        # market between bars — the DISTANCE is what the model fixed
        # at the close, the price it is measured from is not
        "anchor": _num(a.entry),
        # MAGNITUDES, kept exactly as they were. An app built before
        # `side` existed reads these and behaves as it always has,
        # rather than putting a stop on the wrong side of the price
        # during the window between deploying the server and
        # installing the build.
        "tp_pct": _num((a.upper / a.entry - 1.0) * 100.0)
        if _num(a.entry) else None,
        "sl_pct": _num((1.0 - a.lower / a.entry) * 100.0)
        if _num(a.entry) else None,
        # SIGNED, and measured off the actual level rather than the
        # long-oriented barrier — so `price * (1 + pct/100)` is right
        # for both directions and there is no convention to remember.
        "tp_offset_pct": _num((a.tp_price / a.entry - 1.0)
                              * 100.0) if _num(a.entry) else None,
        "sl_offset_pct": _num((a.sl_price / a.entry - 1.0)
                              * 100.0) if _num(a.entry) else None,
    }


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

    # WHY THIS CAN READ "BULL" WHILE THE RECOMMENDATION SAYS WAIT
    #
    # They measure different things and are not in conflict. Structure is a
    # fact about the chart: the direction of the last confirmed break of a
    # swing high or low. `trend_direction` is +1 or -1 by construction and is
    # NEVER 0 - before the first break it is NaN and this tile is omitted
    # entirely - so it cannot say FLAT, and forcing it to would be inventing
    # a state the indicator does not have.
    #
    # The recommendation is a probability and an expected value, and it says
    # WAIT whenever the edge does not clear costs. A market can be in an
    # uptrend that is not worth paying to enter.
    #
    # What the tile CAN honestly do is stop overstating. Once price has drifted
    # back to the middle of the range, the last break is no longer being
    # extended, and "RANGE" describes that better than a direction does.
    d4 = snap.get("trend_direction_4h")
    if d4 is not None and np.isfinite(d4):
        pos = snap.get("position_in_range_4h")
        mid = (pos is not None and np.isfinite(pos) and 0.4 <= pos <= 0.6)
        if mid:
            value, tone = "RANGE", "flat"
            note = (f"Last {htf} break was {'up' if d4 > 0 else 'down'}, but "
                    f"price sits mid-range and is not extending it")
        else:
            value = "BULL" if d4 > 0 else "BEAR"
            tone = "up" if d4 > 0 else "down"
            note = (f"Direction of the last confirmed {htf} break. This is "
                    f"the chart's shape, not the recommendation — a trend can "
                    f"be real and still not worth paying to enter")
        out.append({"key": "htf", "label": f"{htf.upper()} STRUCTURE",
                    "value": value, "note": note, "tone": tone})

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
                # strong | medium | small. The server reports the strongest
                # level this entry clears; the app decides whether the user's
                # chosen sensitivity is satisfied. Computing it once here and
                # filtering there means one calculation serves every setting.
                "strength": getattr(a, "strength", "") or "",
                "p_needed": _num(getattr(a, "p_needed", float("nan"))),
                "window_ends": a.ends_at.isoformat()}
    return {"action": "FLAT", "tone": "flat",
            "detail": a.reason or "No window clears the EV threshold after costs.",
            "strength": "",
            "p_needed": _num(getattr(a, "p_needed", float("nan"))),
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
