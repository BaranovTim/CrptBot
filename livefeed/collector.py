"""The supervisor: runs the collectors together and stays up.

One thread per job, a shared stop flag, and clean shutdown on Ctrl-C or
SIGTERM. Threads rather than asyncio because the work is almost entirely
network-blocking and the code stays readable; nothing here is CPU-bound.

Failure policy is deliberate: a collector that throws must not take the
process down. A news source going offline should not stop bars being
recorded, and a bad poll should be retried on the next cycle rather than
ending the run. Errors are counted and surfaced in `status()`, because a
collector that is silently failing is worse than one that is loudly down.

This process does NOT trade. It records. Keeping collection separate from
decision-making means you can restart the collector without touching a
running strategy, and it is the piece the plan puts before paper trading -
you cannot forward-test on data you never captured.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import pandas as pd

from core import utc_now

from .klines import KlineCollector, interval_delta
from .news import NewsCollector

log = logging.getLogger("livefeed.collector")


@dataclass
class LiveCollector:
    """Runs kline and news collection until stopped."""

    klines: List[KlineCollector] = field(default_factory=list)
    news: Optional[NewsCollector] = None
    transport: str = "poll"                 # "poll" | "stream"
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _threads: List[threading.Thread] = field(default_factory=list, repr=False)
    started_at: Optional[pd.Timestamp] = None

    # ------------------------------------------------------------- control
    def stop(self) -> None:
        self._stop.set()

    def stopping(self) -> bool:
        return self._stop.is_set()

    def install_signal_handlers(self) -> None:
        """Ctrl-C and SIGTERM stop the loops rather than killing mid-write."""
        def handler(signum, frame):
            log.info("signal %s received - finishing the current cycle", signum)
            self.stop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass          # not the main thread; caller handles shutdown

    # --------------------------------------------------------------- run
    def _guarded(self, name: str, fn: Callable) -> Callable:
        """Wrap a worker so one failing job cannot take the process down."""
        def wrapper():
            while not self.stopping():
                try:
                    fn()
                    return                       # clean exit means stop was set
                except Exception as e:           # noqa: BLE001
                    log.exception("%s crashed: %s - restarting in 5s", name, e)
                    for _ in range(5):
                        if self.stopping():
                            return
                        time.sleep(1)
        return wrapper

    def start(self) -> None:
        """Launch every job on its own thread. Returns immediately."""
        self.started_at = utc_now()

        for kc in self.klines:
            name = f"klines:{kc.symbol}:{kc.interval}"
            if self.transport == "stream":
                job = (lambda c=kc: c.stream_forever(stop=self.stopping))
            else:
                job = (lambda c=kc: c.poll_forever(stop=self.stopping))
            t = threading.Thread(target=self._guarded(name, job),
                                 name=name, daemon=True)
            t.start()
            self._threads.append(t)

        if self.news is not None:
            t = threading.Thread(
                target=self._guarded("news",
                                     lambda: self.news.poll_forever(stop=self.stopping)),
                name="news", daemon=True)
            t.start()
            self._threads.append(t)

    def run_forever(self, status_every: int = 300) -> None:
        """Start everything and block, printing status periodically."""
        self.install_signal_handlers()
        self.start()
        log.info("collector running (%s transport) - Ctrl-C to stop", self.transport)
        last_status = time.time()
        try:
            while not self.stopping():
                time.sleep(1)
                if time.time() - last_status >= status_every:
                    log.info("\n%s", self.status())
                    last_status = time.time()
        except KeyboardInterrupt:
            self.stop()
        finally:
            self.shutdown()

    def shutdown(self, timeout: float = 15.0) -> None:
        self.stop()
        for t in self._threads:
            t.join(timeout=timeout)
        log.info("collector stopped\n%s", self.status())

    # ------------------------------------------------------------ status
    def status(self) -> str:
        lines = ["live collector status"]
        if self.started_at is not None:
            up = utc_now() - self.started_at
            lines.append(f"  uptime {str(up).split('.')[0]}")
        for kc in self.klines:
            lines.append(f"  {kc.symbol} {kc.interval}: {kc.stats}")
            lines.append("    " + kc.store.status(
                interval_delta(kc.interval)).replace("\n", "\n    "))
        if self.news is not None:
            lines.append(f"  news: {self.news.stats}")
        return "\n".join(lines)


def build_collector(symbols: List[str], intervals: List[str],
                    with_news: bool = True, transport: str = "poll",
                    market: str = "futures/um",
                    news_interval: int = 300) -> LiveCollector:
    """Convenience constructor for the CLI."""
    klines = [KlineCollector(s, i, market=market)
              for s in symbols for i in intervals]
    news = NewsCollector(interval_seconds=news_interval) if with_news else None
    return LiveCollector(klines=klines, news=news, transport=transport)
