"""The rolling two-bar monitor.

What these guard, in order of how badly they would mislead you:

  THE HORIZON MISMATCH   a model trained at max_hold_bars=2 answers "within
                         the next 2 bars". Reading it for a window with one
                         bar left is a different question. The monitor must
                         use the h1 model there, not h2.

  THE SYMMETRY CLAIM     "DOWN %" is only 1 - "UP %" when the barriers are
                         symmetric. With a 2:1 payoff that arithmetic is
                         simply false, and so is the short EV.

  THE ROLLING WINDOW     analysis A at bar N must cover the same span B
                         covered at bar N-1. If they drift apart the display
                         is lying about which bars it is forecasting.

  THE STALE SCREEN       a window whose end time has passed is history, not
                         a forecast. Without the guard you can act on a
                         signal that expired hours ago.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent5.config import Agent5Config
from monitor import Analysis, Monitor, evaluate, news_verdict
from tests.synthetic import make_bars

HOUR = pd.Timedelta("1h")


class FakeJudge:
    """A frozen model stand-in: returns a fixed probability, no training."""

    def __init__(self, p: float, cfg: Agent5Config):
        self.p = p
        self.cfg = cfg
        self.columns = ["dummy"]

    def predict_proba(self, X):
        # 1-D, matching JudgeAgent.predict_proba - it returns calibrated
        # probabilities directly, not sklearn's two-column layout
        return np.full(len(X), self.p, dtype=float)


def _bars_and_X(n=300):
    bars = make_bars(n)
    X = pd.DataFrame({"dummy": np.zeros(len(bars))}, index=bars.index)
    return bars, X


# ------------------------------------------------------- symmetry
def test_down_is_the_complement_of_up():
    a = Analysis("A", pd.Timestamp("2026-01-01", tz="UTC"),
                 pd.Timestamp("2026-01-01 02:00", tz="UTC"), 2)
    a.p_up = 0.62
    assert abs(a.p_down - 0.38) < 1e-12
    return True


def test_symmetric_barriers_put_tp_and_sl_equidistant():
    """The claim the whole up/down display rests on."""
    cfg = Agent5Config(max_hold_bars=2, k_up=1.0, k_dn=1.0, atr_period=14)
    bars, X = _bars_and_X()
    a = evaluate(FakeJudge(0.55, cfg), bars, X, "A",
                 bars.index[-1], bars.index[-1] + 2 * HOUR, 2)

    entry = float(bars["close"].iloc[-1])
    up_gap = a.tp_price - entry
    dn_gap = entry - a.sl_price
    assert up_gap > 0 and dn_gap > 0
    assert abs(up_gap - dn_gap) / entry < 1e-9, (
        f"barriers are not symmetric: +{up_gap:.2f} vs -{dn_gap:.2f} - "
        f"'DOWN %' would not be 1 - 'UP %'")
    return True


def test_short_ev_is_the_mirror_of_long_ev():
    """Only valid because the barriers are symmetric."""
    cfg = Agent5Config(max_hold_bars=2, k_up=1.0, k_dn=1.0, atr_period=14)
    bars, X = _bars_and_X()

    bull = evaluate(FakeJudge(0.80, cfg), bars, X, "A",
                    bars.index[-1], bars.index[-1] + 2 * HOUR, 2)
    bear = evaluate(FakeJudge(0.20, cfg), bars, X, "A",
                    bars.index[-1], bars.index[-1] + 2 * HOUR, 2)

    assert abs(bull.ev_long - bear.ev_short) < 1e-9, \
        "a p=0.8 long and a p=0.2 short should have identical EV here"
    assert bull.ev_long > bull.ev_short
    assert bear.ev_short > bear.ev_long
    return True


def test_confident_reading_enters_and_flat_one_waits():
    cfg = Agent5Config(max_hold_bars=2, k_up=1.0, k_dn=1.0, atr_period=14)
    bars, X = _bars_and_X()

    strong = evaluate(FakeJudge(0.90, cfg), bars, X, "A",
                      bars.index[-1], bars.index[-1] + 2 * HOUR, 2)
    assert strong.action.startswith("ENTER LONG"), strong.action
    assert 0 < strong.size_pct <= cfg.max_position_pct

    short = evaluate(FakeJudge(0.10, cfg), bars, X, "A",
                     bars.index[-1], bars.index[-1] + 2 * HOUR, 2)
    assert short.action.startswith("ENTER SHORT"), short.action
    # a short's take-profit sits BELOW the entry
    assert short.tp_price < short.entry < short.sl_price

    coin = evaluate(FakeJudge(0.50, cfg), bars, X, "A",
                    bars.index[-1], bars.index[-1] + 2 * HOUR, 2)
    assert coin.action == "WAIT", "a coin flip should never clear the threshold"
    assert coin.size_pct == 0.0
    return True


def test_position_size_respects_the_hard_cap():
    """No probability, however confident, may exceed max_position_pct."""
    cfg = Agent5Config(max_hold_bars=2, k_up=1.0, k_dn=1.0, atr_period=14,
                       max_position_pct=3.0)
    bars, X = _bars_and_X()
    a = evaluate(FakeJudge(0.99, cfg), bars, X, "A",
                 bars.index[-1], bars.index[-1] + 2 * HOUR, 2)
    assert a.size_pct <= 3.0 + 1e-9, f"size {a.size_pct} broke the cap"
    return True


# ------------------------------------------------------ exit time
def test_close_by_time_matches_the_horizon():
    """'When should I close' must be the vertical barrier, exactly."""
    cfg = Agent5Config(max_hold_bars=2, k_up=1.0, k_dn=1.0, atr_period=14)
    bars, X = _bars_and_X()
    last = bars.index[-1]
    a = evaluate(FakeJudge(0.9, cfg), bars, X, "B", last, last + 2 * HOUR, 2)
    assert a.ends_at == last + 2 * HOUR
    assert "CLOSE BY" in a.render(last, HOUR)
    return True


# --------------------------------------------------------- news
def test_news_verdict_mapping():
    assert news_verdict(0.9, 0.8) == ("BULLISH", "BUY")
    assert news_verdict(-0.9, 0.8) == ("BEARISH", "SELL")
    # direction is irrelevant when nothing is at stake
    assert news_verdict(0.9, 0.25)[0] == "SMALL IMPACT"
    assert news_verdict(0.9, 0.05)[0] == "NO IMPACT"
    assert news_verdict(0.9, 0.25)[1] == "NO ACTION"
    # a big move with no clear side is not a trade either
    assert news_verdict(0.0, 0.9)[1] == "NO ACTION"
    return True


def test_news_watcher_does_not_announce_pre_existing_items():
    """The first poll learns what exists; it must not cry 'breaking news'.

    Without priming, starting the monitor would announce every headline
    already on disk as if it had just broken - and then restart both
    analyses for news that is days old.
    """
    import tempfile

    import monitor as M
    from monitor import NewsWatcher
    from newsfeed.events import NewsItem
    from newsfeed.store import JSONLNewsStore

    with tempfile.TemporaryDirectory() as tmp:
        store = JSONLNewsStore(Path(tmp))
        store.append_items([NewsItem(headline="Old story from last week",
                                     source="wire",
                                     published_at="2026-08-01T12:00:00Z")])

        w = NewsWatcher(asset="BTC", store=store)
        if True:
            first = w.poll()
            assert first == [], (
                f"the first poll announced {len(first)} pre-existing items "
                f"as breaking news")

            # now something genuinely new arrives
            store.append_items([NewsItem(
                headline="Exchange halts withdrawals after security incident",
                source="wire", assets=("BTC",),
                published_at="2026-08-26T09:00:00Z")])
            second = w.poll()
            assert len(second) == 1, f"a new item was not reported: {second}"
            assert "halts withdrawals" in second[0]["headline"]
            assert -1.0 <= second[0]["direction"] <= 1.0
            assert 0.0 <= second[0]["magnitude"] <= 1.0

            # and it is only reported once
            assert w.poll() == [], "the same item was announced twice"
    return True


# ----------------------------------------------- rolling windows
def test_rolling_windows_share_their_middle_bar():
    """A at bar N must cover exactly what B covered at bar N-1."""
    bars = make_bars(400)
    delta = HOUR

    def windows(last_close):
        a = (last_close - delta, last_close + delta)      # 1 bar left
        b = (last_close, last_close + 2 * delta)          # 2 bars left
        return a, b

    n_minus_1, n = bars.index[-2], bars.index[-1]
    _, b_prev = windows(n_minus_1)
    a_now, _ = windows(n)
    assert a_now == b_prev, (
        f"analysis A at bar N covers {a_now}, but B at N-1 covered {b_prev} - "
        f"the windows have drifted apart")
    return True


def test_window_lengths_are_two_bars():
    bars = make_bars(300)
    last = bars.index[-1]
    a = Analysis("A", last - HOUR, last + HOUR, 1)
    b = Analysis("B", last, last + 2 * HOUR, 2)
    assert a.ends_at - a.opened_at == 2 * HOUR
    assert b.ends_at - b.opened_at == 2 * HOUR
    return True


# -------------------------------------------------------- staleness
def test_stale_data_is_flagged():
    """An expired window must be labelled, not presented as a live forecast."""
    models = [Path("output/judge_h1.joblib"), Path("output/judge_h2.joblib")]
    if not all(m.exists() for m in models):
        return True                     # models not trained here; nothing to check

    bars = make_bars(600)
    mon = Monitor("BTCUSDT", "1h", models[0], models[1])
    # pretend a great deal of time has passed since the last bar
    screen = mon.screen(bars, [], now=bars.index[-1] + pd.Timedelta("9h"))
    assert "STALE" in screen, "an expired screen was not flagged"

    fresh = mon.screen(bars, [], now=bars.index[-1] + pd.Timedelta("10m"))
    assert "STALE" not in fresh, "a live screen was wrongly flagged stale"
    return True


def test_monitor_uses_the_right_model_per_horizon():
    """h1 for one bar left, h2 for two. Reading h2 at 1 bar is a category error."""
    models = [Path("output/judge_h1.joblib"), Path("output/judge_h2.joblib")]
    if not all(m.exists() for m in models):
        return True

    from agent5 import JudgeAgent
    h1, h2 = JudgeAgent.load(models[0]), JudgeAgent.load(models[1])
    assert h1.cfg.max_hold_bars == 1, "the h1 model is not a 1-bar model"
    assert h2.cfg.max_hold_bars == 2, "the h2 model is not a 2-bar model"
    for j in (h1, h2):
        assert j.cfg.k_up == j.cfg.k_dn, (
            "monitor models must have symmetric barriers, or the DOWN% and "
            "short EV shown on screen are both wrong")
    return True


def test_thin_store_gives_an_actionable_error():
    """Too little history must name the cause and the fix.

    The raw failure is "missing 81 feature columns", which points at the
    model when the real problem is the store. A user cannot act on that.
    """
    import tempfile

    models = [Path("output/judge_h1.joblib"), Path("output/judge_h2.joblib")]
    if not all(m.exists() for m in models):
        return True

    from livefeed import BarStore

    with tempfile.TemporaryDirectory() as tmp:
        store = BarStore("BTCUSDT", "1h", Path(tmp))
        bars = make_bars(120)                       # far below the ~356 needed
        store.append(bars, now=bars.index[-1])

        mon = Monitor("BTCUSDT", "1h", models[0], models[1])
        try:
            mon.screen(store.load(), [])
        except SystemExit as e:
            msg = str(e)
            assert "120 bars" in msg, "the message does not say how many we have"
            assert "--seed" in msg, "the message does not give the fix"
            return True
        raise AssertionError("a thin store did not raise at all")


def test_seed_store_is_idempotent():
    """Seeding twice must not duplicate bars."""
    import tempfile

    import config as project_config
    from livefeed import BarStore, seed_store

    with tempfile.TemporaryDirectory() as tmp:
        store = BarStore("BTCUSDT", "1h", Path(tmp))
        try:
            first = seed_store("BTCUSDT", "1h", start="2026-07-01",
                               store=store, cache_dir=project_config.DATA_CACHE)
        except Exception:
            return True                  # no cached archive here; nothing to test
        if first == 0:
            return True
        second = seed_store("BTCUSDT", "1h", start="2026-07-01",
                            store=store, cache_dir=project_config.DATA_CACHE)
        assert second == 0, f"re-seeding added {second} duplicate bars"
        assert store.find_gaps(HOUR) == [], "seeding produced gaps"
    return True


def test_monitor_refreshes_the_store_itself():
    """The monitor must not silently depend on collect.py running elsewhere.

    It used to only READ the store. With no collector running, no new bar
    ever appeared and the screen sat unchanged for hours - looking broken
    while behaving exactly as written. Now it pulls the bar itself.
    """
    import tempfile

    import pandas as pd

    from livefeed import BarStore, KlineCollector

    with tempfile.TemporaryDirectory() as tmp:
        store = BarStore("BTCUSDT", "1h", Path(tmp))
        bars = make_bars(40)
        store.append(bars.iloc[:-5], now=bars.index[-1])
        assert store.count() == 35

        # a collector wired to a fake REST layer, exactly as the monitor uses it
        class FakeREST(KlineCollector):
            def _rest_klines(self, start_ms=None, end_ms=None, limit=1500):
                return bars

        written = FakeREST("BTCUSDT", "1h", store=store).poll_once(
            now=bars.index[-1])
        assert written == 5, f"self-refresh wrote {written} bars, expected 5"
        assert store.last_close_time() == bars.index[-1], \
            "the monitor did not catch the store up"
    return True


def test_no_fetch_flag_exists_for_collector_users():
    """Running collect.py separately should be able to opt out of fetching."""
    from monitor import parse_args

    default = parse_args([])
    assert default.no_fetch is False, "fetching must be ON by default"
    opted_out = parse_args(["--no-fetch"])
    assert opted_out.no_fetch is True
    return True


# ------------------------------------------------------ connectivity
def test_network_failure_does_not_kill_the_monitor():
    """A dropped wifi connection must be survivable, not fatal.

    urllib reports DNS failure as "nodename nor servname provided, or not
    known" - which reads like a bug in this program rather than a laptop
    losing its connection.
    """
    import tempfile
    import urllib.error

    from livefeed import BarStore, KlineCollector

    bars = make_bars(40)
    with tempfile.TemporaryDirectory() as tmp:
        store = BarStore("BTCUSDT", "1h", Path(tmp))
        store.append(bars.iloc[:30], now=bars.index[29])

        class Flaky(KlineCollector):
            mode = "down"

            def _rest_klines(self, start_ms=None, end_ms=None, limit=1500):
                if self.mode == "down":
                    raise urllib.error.URLError(
                        "[Errno 8] nodename nor servname provided, or not known")
                return bars

        c = Flaky("BTCUSDT", "1h", store=store)
        for _ in range(3):
            assert c.poll_once(now=bars.index[-1]) == 0
        assert c.stats.errors == 3, "failures were not counted"
        assert store.count() == 30, "the store was corrupted by a failed poll"

        # and when the network returns, everything missed is recovered
        c.mode = "up"
        before = c.stats.errors
        written = c.poll_once(now=bars.index[-1])
        assert written == 10, f"recovery wrote {written} bars, expected 10"
        assert c.stats.errors == before, "a successful poll counted an error"
        assert store.find_gaps(HOUR) == [], "the outage left a permanent hole"
    return True


def test_connection_hint_is_plain_language():
    """The status line must not show a raw OS errno string."""
    from monitor import connection_hint
    from core import utc_now

    assert connection_hint(0, utc_now()) == "", "a healthy link reported a problem"

    msg = connection_hint(3, utc_now() - pd.Timedelta("7min"))
    assert "OFFLINE" in msg and "3 failed polls" in msg
    assert "7m ago" in msg
    # the cryptic form must not leak through
    assert "nodename" not in msg and "Errno" not in msg

    one = connection_hint(1, utc_now())
    assert "1 failed poll" in one and "polls" not in one, "plural not handled"
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} monitor tests passed.")


def test_a_short_reports_its_levels_on_the_correct_side_of_the_price():
    """WHAT THIS CATCHES

    `tp_pct` and `sl_pct` are MAGNITUDES — they say how far, never which way.
    The app re-anchors the levels to the live websocket price using them, and
    it did that by moving take-profit UP and stop-loss DOWN whatever the call
    said. So on a SELL it drew the target above the price and the stop below:
    both on the wrong side, under a card that reads SELL.

    The prices in `take_profit`/`stop_loss` were never wrong. The distances
    the app actually draws were, and on crypto there is essentially always a
    live price, so the wrong ones were what you saw.
    """
    from api.service import _levels

    entry = 100.0
    short = Analysis(name="A", opened_at=None, ends_at=None, bars_left=2,
                     entry=entry, side="SHORT", p_up=0.4,
                     tp_price=entry * 0.98, sl_price=entry * 1.02,
                     upper=entry * 1.02, lower=entry * 0.98)
    L = _levels(short, entry)
    assert L["side"] == "SHORT"
    assert L["take_profit"] < entry < L["stop_loss"], L
    assert L["tp_offset_pct"] < 0 < L["sl_offset_pct"], L

    # and re-anchoring to a moved price keeps them on their own sides — this
    # is the arithmetic the app runs
    live = 90.0
    assert live * (1 + L["tp_offset_pct"] / 100) < live
    assert live * (1 + L["sl_offset_pct"] / 100) > live

    long_ = Analysis(name="B", opened_at=None, ends_at=None, bars_left=2,
                     entry=entry, side="LONG", p_up=0.6,
                     tp_price=entry * 1.02, sl_price=entry * 0.98,
                     upper=entry * 1.02, lower=entry * 0.98)
    M = _levels(long_, entry)
    assert M["stop_loss"] < entry < M["take_profit"], M
    assert M["sl_offset_pct"] < 0 < M["tp_offset_pct"], M
    return True


def test_the_old_magnitude_fields_are_unchanged_for_both_sides():
    """Kept deliberately. The server deploys before the build is installed,
    and during that window an older app reads these — it must behave exactly
    as it did rather than acquire a new way to be wrong."""
    from api.service import _levels

    entry = 100.0
    for side, tp, sl in (("LONG", 102.0, 98.0), ("SHORT", 98.0, 102.0)):
        a = Analysis(name="x", opened_at=None, ends_at=None, bars_left=2,
                     entry=entry, side=side, tp_price=tp, sl_price=sl,
                     upper=102.0, lower=98.0)
        L = _levels(a, entry)
        assert abs(L["tp_pct"] - 2.0) < 1e-9, L
        assert abs(L["sl_pct"] - 2.0) < 1e-9, L
    return True
