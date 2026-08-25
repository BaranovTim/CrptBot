"""Live data collection: keep a local store current, forever.

    from livefeed import build_collector
    build_collector(["BTCUSDT"], ["1h"]).run_forever()

Or from the shell:

    python3 collect.py --symbol BTCUSDT --interval 1h

This process records. It does not trade. Keeping collection separate from
decision-making means you can restart it without touching a strategy, and
it is the piece that has to run BEFORE forward paper trading - you cannot
forward-test on data you never captured.
"""
from .collector import LiveCollector, build_collector
from .klines import CollectorStats, KlineCollector, interval_delta
from .news import NewsCollector, NewsStats
from .store import BarStore

__all__ = ["BarStore", "KlineCollector", "CollectorStats", "interval_delta",
           "NewsCollector", "NewsStats", "LiveCollector", "build_collector"]
