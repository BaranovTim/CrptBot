"""Alpaca equity bars — the two defaults that would have been silently wrong.

WHAT THESE GUARD, IN ORDER OF HOW BADLY THEY WOULD MISLEAD

  THE UNADJUSTED SERIES   `adjustment` defaults to `raw`. A 4:1 split then
                          reads as a 75% single-day crash, which corrupts
                          SMA200, RSI(14) and the 50-day high for months and
                          teaches a model about a collapse that never
                          happened. Nothing throws. The chart looks fine.

  THE WRONG FEED          `feed` on a free account can resolve to IEX, which
                          is about 2.5% of US volume — Alpaca's own example
                          has one Apple session at 12,630 IEX trades against
                          535,136 consolidated. Every volume filter in the
                          screener comes out roughly fortyfold too small, and
                          every one of those numbers looks plausible.

  THE FIFTEEN MINUTES     Free SIP access requires `end` to be at least 15
                          minutes old. A request that reaches into the window
                          is rejected, or worse, quietly downgraded to IEX.

  THE DOUBLE-COUNTED BAR  The same bar arriving twice across a page boundary
                          doubles that day's volume, which relative volume
                          then reports as a surge.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketdata.alpaca import (SIP_DELAY, TRADING_HOSTS, Alpaca,
                               AlpacaError, _frame)


def _client():
    return Alpaca(key_id="test-key", secret="test-secret")


def _bar(t, close=100.0, volume=1000.0):
    return {"t": t, "o": close, "h": close, "l": close, "c": close,
            "v": volume, "n": 10, "vw": close}


def test_split_adjustment_is_never_left_to_the_default():
    """`raw` is the API default and is the wrong answer for every caller."""
    sent = {}
    c = _client()
    c._get = lambda host, path, params: (sent.update(params), {"bars": {}})[1]
    c.bars(["AAPL"])
    assert sent["adjustment"] == "all", sent.get("adjustment")
    return True


def test_the_feed_is_always_stated_and_always_sip():
    """Alpaca's own docs disagree on the default; inheriting it risks IEX."""
    sent = {}
    c = _client()
    c._get = lambda host, path, params: (sent.update(params), {"bars": {}})[1]
    c.bars(["AAPL"])
    assert sent["feed"] == "sip", sent.get("feed")
    return True


def test_the_request_never_reaches_into_the_last_fifteen_minutes():
    c = _client()
    now = datetime.now(timezone.utc)

    # Asserted as "how far back is it", not "is it before a clock I read a
    # moment ago" — the latter fails by microseconds because `_safe_end` reads
    # its own clock after this one, and a test that fails on timing teaches
    # you to rerun it rather than to read it.
    def behind(v):
        return datetime.now(timezone.utc) - c._safe_end(v)

    assert behind(None) >= timedelta(minutes=15)
    assert behind(now) >= timedelta(minutes=15)

    # a naive datetime is treated as UTC rather than silently as local time,
    # which near midnight would move the clamp by hours
    assert behind((now + timedelta(hours=2)).replace(tzinfo=None)) \
        >= timedelta(minutes=15)

    # but an end already well in the past is left exactly alone: clamping it
    # would silently shorten a historical backfill
    old = now - timedelta(days=3)
    assert c._safe_end(old) == old
    return True


def test_a_bar_repeated_across_a_page_boundary_is_not_counted_twice():
    rows = [_bar("2026-08-31T20:00:00Z", volume=1000),
            _bar("2026-09-01T20:00:00Z", volume=2000),
            _bar("2026-09-01T20:00:00Z", volume=2000)]   # the page overlap
    df = _frame(rows)
    assert len(df) == 2, df
    assert float(df["volume"].sum()) == 3000.0, df["volume"].tolist()
    return True


def test_bars_come_back_in_time_order_with_the_repo_s_column_names():
    """Out of order bars would make every rolling window meaningless."""
    rows = [_bar("2026-09-02T20:00:00Z", close=3),
            _bar("2026-08-31T20:00:00Z", close=1),
            _bar("2026-09-01T20:00:00Z", close=2)]
    df = _frame(rows)
    assert list(df.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [1.0, 2.0, 3.0]
    assert df.index.is_monotonic_increasing
    assert str(df.index.tz) == "UTC"
    return True


def test_missing_credentials_say_what_to_do_rather_than_throwing_a_urlerror():
    c = Alpaca(key_id="", secret="")
    assert not c.configured
    try:
        c.bars(["AAPL"])
    except Exception as e:
        assert "ALPACA_KEY_ID" in str(e), e
        return True
    raise AssertionError("an unconfigured client silently did nothing")


def test_the_universe_drops_names_that_cannot_actually_be_traded():
    """A screener row you cannot act on is worse than no row."""
    c = _client()
    c._get = lambda host, path, params: [
        {"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ",
         "tradable": True, "shortable": True, "easy_to_borrow": True},
        {"symbol": "DEAD", "name": "Delisted Co", "exchange": "OTC",
         "tradable": False, "shortable": False, "easy_to_borrow": False},
    ]
    rows = c.universe()
    assert [r["symbol"] for r in rows] == ["AAPL"], rows
    assert rows[0]["shortable"] == "1"
    return True


def test_a_paper_key_is_not_mistaken_for_a_bad_key():
    """THE BUG THIS PINS.

    `data.alpaca.markets` accepts paper and live keys alike, but the TRADING
    API does not — and `/v2/assets` lives there. A paper key sent to the live
    host returns `401 request is not authorized`, which is indistinguishable
    from a wrong key and sends you to check your credentials rather than the
    URL. A free Alpaca account issues paper keys, so this is the DEFAULT case,
    not an edge one.
    """
    tried = []

    def only_paper_works(host, path, params):
        tried.append(host)
        if "paper" not in host:
            raise AlpacaError("HTTP 401 on /v2/assets: request is not authorized")
        return [{"symbol": "AAPL", "name": "Apple", "tradable": True}]

    c = _client()
    c._get = only_paper_works
    assert [r["symbol"] for r in c.universe()] == ["AAPL"]
    assert "paper" in tried[0], "paper must be tried first, it is the free default"

    # and the working host is remembered rather than re-probed every call
    before = len(tried)
    c.universe()
    assert len(tried) == before + 1, tried
    return True


def test_a_genuinely_bad_key_says_so_instead_of_blaming_the_host():
    """The fallback must not turn a real auth failure into a confusing one."""
    c = _client()
    c._get = lambda h, p, q: (_ for _ in ()).throw(
        AlpacaError("HTTP 401 on /v2/assets: request is not authorized"))
    try:
        c.universe()
    except AlpacaError as e:
        assert "the key itself" in str(e), e
        # both hosts named, so the message can be acted on
        assert all(h in str(e) for h in TRADING_HOSTS), e
        return True
    raise AssertionError("a rejected key did not raise")


def test_a_non_auth_failure_is_not_retried_against_the_other_host():
    """A 500 or a rate limit is not a host problem; retrying doubles the load
    on a service that has just told you it is struggling."""
    calls = []

    def boom(host, path, params):
        calls.append(host)
        raise AlpacaError("rate limited (200/min on the free plan)")

    c = _client()
    c._get = boom
    try:
        c.universe()
    except AlpacaError as e:
        assert "rate limited" in str(e)
        assert len(calls) == 1, calls
        return True
    raise AssertionError("a rate limit did not propagate")
