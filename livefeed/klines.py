"""Live kline collection: poll or stream, then always reconcile.

TWO TRANSPORTS, ONE GUARANTEE
-----------------------------
    poll       REST /fapi/v1/klines on a timer. No dependencies, no
               reconnect logic, and for 1h bars it costs 24 requests a day
               against a 2400/min weight budget - completely free in
               practice. This is the default because it cannot silently
               half-work.

    stream     Binance websocket. Push instead of polling, sub-second
               latency, and the right answer for 1s/1m bars or many
               symbols. Needs the `websockets` package.

The guarantee is the same either way: after every cycle the store is
reconciled against REST, so a hole is filled rather than remembered.

WHY GAP-FILL IS NOT OPTIONAL
----------------------------
A Binance websocket drops roughly once every 24 hours. That is documented
behaviour, not a failure. A collector that reconnects without backfilling
leaves a hole every single day, and nothing surfaces it - the frame still
loads, the agents still run, and every `bars_since_*` feature quietly
understates elapsed time. So: reconnect, then ask REST what was missed.

THE FORMING BAR
---------------
Both transports hand you the bar currently being built. The websocket marks
it (`x: false`); REST just returns it as the last row. Neither is stored -
`BarStore.append` refuses anything whose close_time has not passed. That is
the guard that keeps live features identical to backtest features.

RATE LIMITS ARE WEIGHT-BASED
----------------------------
Not request-count based. Every response carries the weight consumed this
minute; this reads it and slows down before Binance does. Ignoring it earns
a 429, then a 418 IP ban with escalating duration.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from core import utc_now

from marketdata.binance import FAPI_BASE, KLINE_COLUMNS, _to_utc

from .store import BarStore

log = logging.getLogger("livefeed.klines")

# how long one bar of each interval lasts
INTERVAL_MS = {
    "1s": 1_000, "1m": 60_000, "3m": 180_000, "5m": 300_000,
    "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000,
}

WS_BASE = "wss://fstream.binance.com/ws"
WEIGHT_SOFT_LIMIT = 1500       # back off well before the 2400/min ceiling


def interval_delta(interval: str) -> pd.Timedelta:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval {interval!r}; "
                         f"expected one of {sorted(INTERVAL_MS)}")
    return pd.Timedelta(milliseconds=INTERVAL_MS[interval])


@dataclass
class CollectorStats:
    polls: int = 0
    bars_written: int = 0
    gaps_filled: int = 0
    reconnects: int = 0
    errors: int = 0
    last_bar: Optional[pd.Timestamp] = None
    last_weight: int = 0

    def __str__(self) -> str:
        return (f"polls={self.polls} bars={self.bars_written} "
                f"gaps_filled={self.gaps_filled} reconnects={self.reconnects} "
                f"errors={self.errors} weight={self.last_weight} "
                f"last_bar={self.last_bar}")


class KlineCollector:
    """Keeps a BarStore current for one symbol/interval."""

    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1h",
                 market: str = "futures/um", store: Optional[BarStore] = None,
                 store_dir: Optional[Path] = None):
        self.symbol = symbol.upper()
        self.interval = interval
        self.delta = interval_delta(interval)
        self.market = market
        self.store = store or (BarStore(self.symbol, interval, store_dir)
                               if store_dir else BarStore(self.symbol, interval))
        self.stats = CollectorStats()
        self._weight_used = 0

    # ------------------------------------------------------------- REST
    def _rest_klines(self, start_ms: Optional[int] = None,
                     end_ms: Optional[int] = None, limit: int = 1500) -> pd.DataFrame:
        """One REST call. Returns a frame indexed by close_time, forming bar included."""
        base = (f"{FAPI_BASE}/fapi/v1/klines" if self.market.startswith("futures")
                else "https://api.binance.com/api/v3/klines")
        params = [f"symbol={self.symbol}", f"interval={self.interval}",
                  f"limit={min(limit, 1500)}"]
        if start_ms is not None:
            params.append(f"startTime={int(start_ms)}")
        if end_ms is not None:
            params.append(f"endTime={int(end_ms)}")
        url = base + "?" + "&".join(params)

        req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/livefeed"})
        with urllib.request.urlopen(req, timeout=30) as r:
            # weight, not request count. read it and self-throttle before
            # Binance does it for us with a 429 and then an IP ban
            hdr = r.headers.get("X-MBX-USED-WEIGHT-1M")
            if hdr and hdr.isdigit():
                self._weight_used = int(hdr)
                self.stats.last_weight = self._weight_used
            payload = json.loads(r.read())

        if self._weight_used > WEIGHT_SOFT_LIMIT:
            log.warning("weight %s of 2400 this minute - pausing 10s",
                        self._weight_used)
            time.sleep(10)

        if not payload:
            return pd.DataFrame()
        df = pd.DataFrame(payload, columns=KLINE_COLUMNS[:len(payload[0])])
        df["open_time"] = _to_utc(df["open_time"])
        df["close_time"] = _to_utc(df["close_time"])
        for c in ("open", "high", "low", "close", "volume", "quote_volume",
                  "number_of_trades", "taker_buy_base_volume",
                  "taker_buy_quote_volume"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.drop(columns=["ignore"], errors="ignore")
        return df.set_index("close_time").sort_index()

    # -------------------------------------------------------- one cycle
    def poll_once(self, now: Optional[pd.Timestamp] = None) -> int:
        """Fetch recent bars and store the closed ones. Returns bars written."""
        now = now or utc_now()
        self.stats.polls += 1
        try:
            df = self._rest_klines(limit=100)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            self.stats.errors += 1
            log.warning("poll failed: %s", e)
            return 0
        if df.empty:
            return 0
        written = self.store.append(df, now=now)
        self.stats.bars_written += written
        last = self.store.last_close_time()
        if last is not None:
            self.stats.last_bar = last
        return written

    def backfill(self, since: Optional[pd.Timestamp] = None,
                 now: Optional[pd.Timestamp] = None, max_calls: int = 50) -> int:
        """Fill everything between the newest stored bar and now.

        Called on startup and after every reconnect. This is the half that
        makes a dropped websocket a non-event instead of a permanent hole.
        """
        now = now or utc_now()
        anchor = since or self.store.last_close_time()
        if anchor is None:
            # empty store: seed with the most recent bars rather than trying
            # to pull all of history - that is what marketdata.load_klines is for
            written = self.poll_once(now=now)
            self.stats.gaps_filled += written
            return written

        start_ms = int((anchor + pd.Timedelta(milliseconds=1)).timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)
        if start_ms >= end_ms:
            return 0

        total = 0
        for _ in range(max_calls):
            try:
                df = self._rest_klines(start_ms=start_ms, end_ms=end_ms)
            except (urllib.error.URLError, urllib.error.HTTPError,
                    TimeoutError, OSError) as e:
                self.stats.errors += 1
                log.warning("backfill call failed: %s", e)
                break
            if df.empty:
                break
            written = self.store.append(df, now=now)
            total += written
            newest = df.index.max()
            if newest is None or len(df) < 2:
                break
            nxt = int((newest + pd.Timedelta(milliseconds=1)).timestamp() * 1000)
            if nxt <= start_ms:            # no forward progress, stop
                break
            start_ms = nxt
            if start_ms >= end_ms:
                break

        self.stats.gaps_filled += total
        self.stats.bars_written += 0       # already counted inside append
        if total:
            log.info("backfilled %d bars for %s %s", total, self.symbol, self.interval)
        last = self.store.last_close_time()
        if last is not None:
            self.stats.last_bar = last
        return total

    def repair_gaps(self, now: Optional[pd.Timestamp] = None) -> int:
        """Re-fetch any interior holes the store reports."""
        now = now or utc_now()
        gaps = self.store.find_gaps(self.delta)
        filled = 0
        for after, before in gaps:
            try:
                df = self._rest_klines(
                    start_ms=int((after + pd.Timedelta(milliseconds=1)).timestamp() * 1000),
                    end_ms=int(before.timestamp() * 1000))
            except (urllib.error.URLError, urllib.error.HTTPError,
                    TimeoutError, OSError):
                self.stats.errors += 1
                continue
            filled += self.store.append(df, now=now)
        if filled:
            self.stats.gaps_filled += filled
            log.info("repaired %d missing bars", filled)
        return filled

    # -------------------------------------------------------- websocket
    def stream_forever(self, on_bar: Optional[Callable] = None,
                       stop: Optional[Callable[[], bool]] = None) -> None:
        """Websocket loop with reconnect and backfill on every reconnect.

        Blocks. Requires the `websockets` package; raises a clear error if
        it is absent rather than silently degrading to polling.
        """
        try:
            import asyncio

            import websockets
        except ImportError as e:
            raise RuntimeError(
                "stream mode needs the `websockets` package "
                "(pip install websockets). Use transport='poll' instead - "
                "for 1h bars polling is entirely adequate."
            ) from e

        url = f"{WS_BASE}/{self.symbol.lower()}@kline_{self.interval}"

        async def run():
            backoff = 1
            while not (stop and stop()):
                try:
                    async with websockets.connect(url, ping_interval=20,
                                                  ping_timeout=20) as ws:
                        # a reconnect means we were away. ask REST what we
                        # missed BEFORE trusting the live stream again
                        self.backfill()
                        backoff = 1
                        log.info("websocket connected: %s", url)
                        while not (stop and stop()):
                            raw = await asyncio.wait_for(ws.recv(), timeout=90)
                            msg = json.loads(raw)
                            k = msg.get("k") or {}
                            # `x` is Binance's own "this kline is closed" flag.
                            # everything else is the bar still forming
                            if not k.get("x"):
                                continue
                            self._store_ws_kline(k)
                            if on_bar:
                                on_bar(self.stats.last_bar)
                except Exception as e:            # noqa: BLE001 - any drop reconnects
                    if stop and stop():
                        break
                    self.stats.reconnects += 1
                    self.stats.errors += 1
                    log.warning("websocket dropped (%s); reconnecting in %ds",
                                type(e).__name__, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)

        asyncio.run(run())

    def _store_ws_kline(self, k: dict) -> None:
        """Turn one closed websocket kline into a stored bar."""
        row = pd.DataFrame([{
            "open_time": _to_utc(pd.Series([k["t"]])).iloc[0],
            "open": float(k["o"]), "high": float(k["h"]),
            "low": float(k["l"]), "close": float(k["c"]),
            "volume": float(k["v"]),
            "close_time": _to_utc(pd.Series([k["T"]])).iloc[0],
            "quote_volume": float(k["q"]),
            "number_of_trades": int(k["n"]),
            "taker_buy_base_volume": float(k["V"]),
            "taker_buy_quote_volume": float(k["Q"]),
        }]).set_index("close_time")
        written = self.store.append(row)
        self.stats.bars_written += written
        if written:
            self.stats.last_bar = row.index[0]
            log.info("stored %s %s bar %s", self.symbol, self.interval, row.index[0])

    # ------------------------------------------------------------- poll
    def poll_forever(self, stop: Optional[Callable[[], bool]] = None,
                     on_bar: Optional[Callable] = None) -> None:
        """Polling loop. Sleeps until just after each bar closes."""
        self.backfill()
        while not (stop and stop()):
            before = self.stats.bars_written
            self.poll_once()
            if on_bar and self.stats.bars_written > before:
                on_bar(self.stats.last_bar)
            self._sleep_to_next_close(stop)

    def _sleep_to_next_close(self, stop: Optional[Callable[[], bool]] = None) -> None:
        """Wait until a couple of seconds after the next bar closes.

        Polling on the bar boundary rather than on a fixed timer means one
        request per bar instead of many, and the small delay lets the
        exchange finalise the candle before we ask for it.
        """
        now = utc_now()
        step = self.delta
        next_close = (now.floor(step) + step)
        wait = (next_close - now).total_seconds() + 2.0
        wait = max(1.0, min(wait, step.total_seconds() + 5))
        # sleep in slices so a stop signal is noticed promptly
        deadline = time.time() + wait
        while time.time() < deadline:
            if stop and stop():
                return
            time.sleep(min(1.0, deadline - time.time()))
