"""Live collector: the guarantees that keep live data equal to backtest data.

Three failures this file exists to prevent, all of them silent:

  THE FORMING BAR    storing the candle currently being built. Its close is
                     just the current price. A backtest never sees such a
                     row, so every feature computed from it differs live -
                     and nothing raises.

  THE INVISIBLE GAP  a websocket drops about once a day by design. Without
                     backfill that is a hole per day, and `bars_since_*`
                     silently understates elapsed time for months.

  THE LOST CLOCK     news `ingested_at` must be stamped when WE see an item.
                     Agent 3's entire point-in-time defence rests on it, and
                     a backfill can never recover it.

No network: a fake REST layer feeds known bars so the assertions are exact.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from core import utc_now
from livefeed import BarStore, KlineCollector, NewsCollector, interval_delta
from newsfeed.events import NewsItem
from newsfeed.store import JSONLNewsStore

HOUR = pd.Timedelta("1h")


def _bars(start: str, n: int, price: float = 100.0) -> pd.DataFrame:
    """n consecutive 1h bars, indexed by close_time like Binance labels them."""
    open_time = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    close_time = open_time + HOUR - pd.Timedelta(milliseconds=1)
    return pd.DataFrame({
        "open_time": open_time, "open": price, "high": price + 1,
        "low": price - 1, "close": price, "volume": 10.0,
        "quote_volume": 1000.0, "number_of_trades": 50,
        "taker_buy_base_volume": 5.0, "taker_buy_quote_volume": 500.0,
    }, index=close_time)


class FakeREST(KlineCollector):
    """A collector whose REST layer serves a fixed frame. No network."""

    def __init__(self, frame: pd.DataFrame, store: BarStore):
        super().__init__("BTCUSDT", "1h", store=store)
        self.frame = frame
        self.calls = 0

    def _rest_klines(self, start_ms=None, end_ms=None, limit=1500):
        self.calls += 1
        df = self.frame
        if start_ms is not None:
            df = df[df.index >= pd.Timestamp(start_ms, unit="ms", tz="UTC")]
        if end_ms is not None:
            df = df[df.index <= pd.Timestamp(end_ms, unit="ms", tz="UTC")]
        return df.head(limit)


def _store(tmp: str) -> BarStore:
    return BarStore("BTCUSDT", "1h", Path(tmp))


# ------------------------------------------------------- the forming bar
def test_forming_bar_is_never_stored():
    """The guard. A bar whose close_time has not passed must not persist."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-01", 5)
        # pretend "now" sits inside the last bar - it is still forming
        now = bars.index[-1] - pd.Timedelta(minutes=30)

        written = store.append(bars, now=now)
        assert written == 4, f"wrote {written} bars, expected 4 closed ones"
        assert store.last_close_time() == bars.index[-2], \
            "the forming bar was persisted"

        # once it closes, it stores - and the earlier ones do not duplicate
        after = store.append(bars, now=bars.index[-1])
        assert after == 1, f"wrote {after} on the second pass, expected 1"
        assert store.count() == 5
    return True


def test_append_is_idempotent():
    """Re-storing the same bars must add nothing. Polls overlap constantly."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-01", 24)
        now = bars.index[-1]
        assert store.append(bars, now=now) == 24
        assert store.append(bars, now=now) == 0, "duplicate bars were written"
        assert store.append(bars.iloc[10:], now=now) == 0
        assert store.count() == 24
    return True


def test_store_survives_restart():
    """A new store object on the same directory sees everything."""
    with tempfile.TemporaryDirectory() as tmp:
        bars = _bars("2026-08-01", 30)
        BarStore("BTCUSDT", "1h", Path(tmp)).append(bars, now=bars.index[-1])
        reopened = BarStore("BTCUSDT", "1h", Path(tmp))
        assert reopened.count() == 30
        assert reopened.last_close_time() == bars.index[-1]
        loaded = reopened.load()
        assert loaded.index.tz is not None, "timezone lost on reload"
        assert "taker_buy_ratio" in loaded.columns, "derived columns missing"
    return True


def test_month_boundary_is_handled():
    """Bars spanning two months land in two files and reload as one frame."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-31 12:00", 30)      # crosses into September
        store.append(bars, now=bars.index[-1])
        files = sorted(p.name for p in Path(tmp).rglob("*.csv"))
        assert len(files) == 2, f"expected 2 monthly files, got {files}"
        assert store.count() == 30, "bars lost across the month boundary"
        assert store.load(derived=False).index.is_monotonic_increasing
    return True


# ------------------------------------------------------------ the gap
def test_gaps_are_detected():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-01", 48)
        holed = pd.concat([bars.iloc[:20], bars.iloc[30:]])   # 10 bars missing
        store.append(holed, now=bars.index[-1])

        gaps = store.find_gaps(HOUR)
        assert len(gaps) == 1, f"expected 1 gap, found {len(gaps)}"
        after, before = gaps[0]
        assert after == bars.index[19] and before == bars.index[30]
        assert "gaps" in store.status(HOUR)
    return True


def test_backfill_fills_the_hole():
    """After downtime, REST must recover everything that was missed."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-01", 48)
        now = bars.index[-1]

        # collector was up for 20 bars, then went away
        store.append(bars.iloc[:20], now=bars.index[19])
        assert store.count() == 20

        collector = FakeREST(bars, store)
        filled = collector.backfill(now=now)
        assert filled == 28, f"backfilled {filled}, expected 28"
        assert store.count() == 48
        assert store.find_gaps(HOUR) == [], "a hole survived backfill"
    return True


def test_repair_fills_interior_holes():
    """A hole in the middle of history is re-fetched, not just the tail."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-01", 48)
        store.append(pd.concat([bars.iloc[:20], bars.iloc[30:]]),
                     now=bars.index[-1])
        assert len(store.find_gaps(HOUR)) == 1

        filled = FakeREST(bars, store).repair_gaps(now=bars.index[-1])
        assert filled == 10, f"repaired {filled}, expected 10"
        assert store.find_gaps(HOUR) == []
    return True


def test_backfill_on_empty_store_seeds_it():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-01", 12)
        written = FakeREST(bars, store).backfill(now=bars.index[-1])
        assert written == 12 and store.count() == 12
    return True


def test_poll_stores_only_closed_bars():
    """The polling path must respect the guard too, not just direct appends."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-01", 10)
        collector = FakeREST(bars, store)
        # now sits inside the final bar
        collector.poll_once(now=bars.index[-1] - pd.Timedelta(minutes=1))
        assert store.count() == 9, f"stored {store.count()}, expected 9"
        assert store.last_close_time() == bars.index[-2]
    return True


# ----------------------------------------------------------- the clock
def test_news_ingested_at_is_stamped_now():
    """Agent 3 gates on ingested_at. It must record when WE saw the item."""
    class OneItem:
        name = "fake"

        def fetch(self):
            # published two hours ago, and the source gives us no arrival time
            return [NewsItem(headline="Exchange announces listing",
                             source="fake-exchange",
                             published_at=utc_now() - pd.Timedelta(hours=2))]

    with tempfile.TemporaryDirectory() as tmp:
        store = JSONLNewsStore(Path(tmp))
        collector = NewsCollector(sources=[OneItem()], store=store)
        assert collector.poll_once() == 1

        item = store.load_items()[0]
        assert item.ingested_at is not None, "arrival time was not stamped"
        # observable_at must follow ingestion, not publication
        gap = (item.ingested_at - item.published_at).total_seconds()
        assert gap > 3600, f"ingested_at only {gap:.0f}s after published_at"
        assert item.observable_at() == item.ingested_at, \
            "observable_at drifted toward the published time"
    return True


def test_news_polling_dedupes():
    """Re-seeing the same headline every poll must not inflate news volume."""
    class Repeater:
        name = "repeat"

        def fetch(self):
            return [NewsItem(headline="Same story every time", source="wire",
                             published_at="2026-08-01T12:00:00Z")]

    with tempfile.TemporaryDirectory() as tmp:
        collector = NewsCollector(sources=[Repeater()],
                                  store=JSONLNewsStore(Path(tmp)))
        assert collector.poll_once() == 1
        assert collector.poll_once() == 0, "the same item was stored twice"
        assert collector.poll_once() == 0
        assert collector.stats.items_seen == 3 and collector.stats.items_new == 1
    return True


def test_a_broken_source_does_not_stop_collection():
    """One dead source must not take the whole collector down."""
    class Broken:
        name = "broken"

        def fetch(self):
            raise ConnectionError("upstream is down")

    class Working:
        name = "working"

        def fetch(self):
            return [NewsItem(headline="Still reporting", source="wire",
                             published_at="2026-08-01T12:00:00Z")]

    with tempfile.TemporaryDirectory() as tmp:
        collector = NewsCollector(sources=[Broken(), Working()],
                                  store=JSONLNewsStore(Path(tmp)))
        assert collector.poll_once() == 1, "a broken source blocked a working one"
        assert collector.stats.errors == 1, "the failure was not recorded"
    return True


# ------------------------------------------------------- supervisor
def test_collector_starts_and_stops_cleanly():
    """Threads must come down on request, not hang the process."""
    import time

    from livefeed.collector import LiveCollector

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        bars = _bars("2026-08-01", 8)
        collector = LiveCollector(klines=[FakeREST(bars, store)], news=None)
        collector.start()
        time.sleep(0.5)
        collector.shutdown(timeout=5)
        assert collector.stopping()
        assert all(not t.is_alive() for t in collector._threads), \
            "a worker thread survived shutdown"
        assert "uptime" in collector.status()
    return True


def test_collected_bars_feed_the_agents():
    """The whole point: what is collected must run straight into the agents."""
    from agent2 import IndicatorAgent
    from core import check_bars

    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        rng = np.random.default_rng(0)
        bars = _bars("2026-08-01", 400)
        # give it real movement so the indicators have something to chew on
        walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, 400)))
        for c, off in (("open", 0), ("close", 0), ("high", 1), ("low", -1)):
            bars[c] = walk + off
        store.append(bars, now=bars.index[-1])

        loaded = store.load()
        check_bars(loaded, who="collected bars")      # same validator as live
        features = IndicatorAgent().compute(loaded)
        assert len(features) == 400
        assert features["rsi_14"].notna().sum() > 100, \
            "collected bars produced no usable features"
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} live collector tests passed.")


# ---------------------------------------------------------------------------
# A 404 that is asked for again on every request is a slow dashboard
# ---------------------------------------------------------------------------

def test_a_missing_archive_day_is_remembered_and_not_asked_for_twice():
    """These archives are one file per day, and a missing day stays missing.

    DOGE's bars start 2021-01 and its open-interest archive starts 2021-12,
    so 334 days of it can never exist; the liquidation feed has been
    restricted for years and 404s every day of every range. Each dashboard
    build asked for all of them again — around 350 doomed round trips before
    a single feature was computed.
    """
    import urllib.error

    from marketdata import binance

    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "DOGEUSDT-metrics-2021-05-01.zip"
        real = binance.urllib.request.urlopen
        binance.urllib.request.urlopen = fake_urlopen
        try:
            # a settled day: asked once, remembered for good
            assert binance._download("http://x/a.zip", dest, miss_ttl=None) is False
            assert binance._download("http://x/a.zip", dest, miss_ttl=None) is False
            assert len(calls) == 1, calls
            # the marker is trusted only as long as the caller says
            assert binance._download("http://x/a.zip", dest, miss_ttl=3600) is False
            assert len(calls) == 1, "a fresh marker was re-requested"
            assert binance._download("http://x/a.zip", dest, miss_ttl=0.0) is False
            assert len(calls) == 2, "miss_ttl=0 must behave as it always did"
            # and a day whose marker has aged past the TTL is asked again --
            # this is the recent day that gets published a few hours later
            m = binance._missing_marker(dest)
            os.utime(m, (time.time() - 7200, time.time() - 7200))
            body = io.BytesIO(b"hello")
            binance.urllib.request.urlopen = lambda url, timeout=None: _Resp(body)
            assert binance._download("http://x/a.zip", dest, miss_ttl=3600) is True
            assert dest.read_bytes() == b"hello"
        finally:
            binance.urllib.request.urlopen = real
    return True


class _Resp:
    """The minimum `urlopen` result `_download` streams from."""

    def __init__(self, body):
        self._b = body

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_day_that_may_still_be_published_is_retried_but_an_old_one_is_not():
    from marketdata.derivatives import RECENT_MISS_TTL, _miss_ttl

    now = pd.Timestamp("2026-09-22", tz="UTC")
    assert _miss_ttl(pd.Timestamp("2021-05-01", tz="UTC"), now) is None
    assert _miss_ttl(pd.Timestamp("2026-09-21", tz="UTC"), now) == RECENT_MISS_TTL
    assert _miss_ttl(pd.Timestamp("2026-09-22", tz="UTC"), now) == RECENT_MISS_TTL
    return True
