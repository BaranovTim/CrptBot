"""The forming bar, and the intra-bar watch built on it.

What these guard, in order of how badly they would mislead you:

  THE STORED FORMING BAR   the whole project's live/backtest agreement rests
                           on never persisting a partial candle. This module
                           handles one on purpose, so the guard is tested
                           from the new direction rather than assumed.

  HISTORY REWRITTEN        appending the forming bar must change exactly one
                           row of features. If an earlier row moves, some
                           detector is reading forward, and the provisional
                           read would be contaminated in a way no threshold
                           would reveal.

  THE POISONED CACHE       `features()` caches the CLOSED-bar frame. A
                           provisional call that overwrote it would leave
                           every later screen quoting a partial bar with
                           nothing raising.

  THE ALERT STORM          a genuine 3-ATR move must produce a few alerts as
                           it develops, not one every poll. Without the
                           re-arm the screen is unreadable exactly when it
                           matters most.

  THE AMBIGUOUS BAR        one bar touching both barriers is counted a LOSS
                           by Agent 5's labeller. The live screen must agree,
                           or every statistic gathered here flatters the
                           model against its own training data.

  THE DEAD THRESHOLD       thresholds are in ATR so the same config means the
                           same thing at 40k and at 100k. A percentage
                           threshold would silently stop firing in a calm
                           regime and never stop firing in a violent one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from livefeed import (
    BarStore,
    FormingBar,
    FormingBarFeed,
    SpikeConfig,
    SpikeWatcher,
)
from monitor import Analysis, Monitor, atr_price_estimate
from tests.synthetic import make_bars

HOUR = pd.Timedelta("1h")
MODELS = [Path("output/judge_h1.joblib"), Path("output/judge_h2.joblib")]


def _forming(anchor_time, price, *, open_=None, high=None, low=None,
             volume=100.0, taker_frac=0.5) -> FormingBar:
    open_ = open_ if open_ is not None else price
    return FormingBar(
        open_time=anchor_time,
        close_time=anchor_time + HOUR - pd.Timedelta(milliseconds=1),
        open=open_, high=high if high is not None else max(open_, price),
        low=low if low is not None else min(open_, price), close=price,
        volume=volume, quote_volume=volume * price,
        number_of_trades=500.0, taker_buy_base_volume=volume * taker_frac)


# ---------------------------------------------------------------- storage
def test_the_forming_bar_is_never_stored():
    """The guard that keeps live features identical to backtest features."""
    import tempfile

    bars = make_bars(50)
    anchor = bars.index[-1]
    f = _forming(anchor, 42000.0)
    with tempfile.TemporaryDirectory() as d:
        store = BarStore("BTCUSDT", "1h", directory=Path(d))
        written = store.append(f.as_row(), now=anchor + pd.Timedelta(minutes=20))
    assert written == 0, (
        f"a forming bar was persisted ({written} rows). live features will now "
        f"differ from backtest features with nothing raising")
    return True


def test_as_row_aligns_with_the_stored_schema():
    """A row that silently misaligns lands values on the wrong columns."""
    bars = make_bars(50)
    row = _forming(bars.index[-1], 42000.0).as_row().reindex(columns=bars.columns)
    assert list(row.columns) == list(bars.columns), "column order diverged"
    for c in ("open", "high", "low", "close", "volume"):
        assert np.isfinite(row[c].iloc[0]), f"{c} did not survive the reindex"
    return True


# ------------------------------------------------------------------ feed
def test_fetch_picks_the_unclosed_bar_not_the_last_row():
    """At the boundary the API returns a just-closed bar as the last row.

    Taking `.iloc[-1]` blindly would compare that bar against itself and
    report a 0.00% move - the watch would go quiet at exactly the moment a
    bar closes, which is when moves cluster.
    """
    import livefeed.forming as fm

    now = pd.Timestamp("2026-08-27T08:30:00Z")
    closed_open = int(pd.Timestamp("2026-08-27T07:00:00Z").value // 10**6)
    closed_close = int(pd.Timestamp("2026-08-27T07:59:59.999Z").value // 10**6)
    live_open = int(pd.Timestamp("2026-08-27T08:00:00Z").value // 10**6)
    live_close = int(pd.Timestamp("2026-08-27T08:59:59.999Z").value // 10**6)

    def row(ot, ct, close):
        return [ot, "100.0", "101.0", "99.0", close, "10.0", ct,
                "1000.0", 50, "5.0", "500.0", "0"]

    payload = [row(closed_open, closed_close, "100.0"),
               row(live_open, live_close, "123.0")]

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()

    real = fm.urllib.request.urlopen
    fm.urllib.request.urlopen = lambda *a, **k: FakeResp()
    try:
        got = FormingBarFeed("BTCUSDT", "1h").fetch(now=now)
    finally:
        fm.urllib.request.urlopen = real

    assert got is not None, "the forming bar was not returned at all"
    assert got.close == 123.0, (
        f"picked the closed bar (close {got.close}) instead of the forming one")
    return True


def test_fetch_survives_a_dead_network():
    """A dropped wifi must dim the readout, not kill the monitor."""
    import livefeed.forming as fm

    real = fm.urllib.request.urlopen

    def boom(*a, **k):
        raise OSError("nodename nor servname provided, or not known")

    fm.urllib.request.urlopen = boom
    fm.log.disabled = True          # the warning is the point; don't print it
    try:
        feed = FormingBarFeed("BTCUSDT", "1h")
        assert feed.fetch() is None, "a network failure did not return None"
        assert feed.errors == 1, "the failure was not counted"
    finally:
        fm.urllib.request.urlopen = real
        fm.log.disabled = False
    return True


# --------------------------------------------------------------- watcher
def test_rearm_stops_one_move_alerting_every_poll():
    """A 3-ATR dislocation must not print an alert block every 20 seconds."""
    t0 = pd.Timestamp("2026-08-27T08:00:00Z")
    w = SpikeWatcher(SpikeConfig())
    w.reset(t0, 40000.0)
    atr, vb = 400.0, 100.0

    fired = 0
    for k in range(12):                      # four minutes of polling, price flat
        _, sp = w.observe(_forming(t0, 38800.0), atr, vb,
                          now=t0 + pd.Timedelta(seconds=20 * (k + 1)))
        fired += sp is not None
    assert fired == 1, f"one stationary move alerted {fired} times, not once"
    return True


def test_rearm_releases_when_the_move_extends():
    """Silence must not become permanent - a deepening move is new news."""
    t0 = pd.Timestamp("2026-08-27T08:00:00Z")
    w = SpikeWatcher(SpikeConfig())
    w.reset(t0, 40000.0)
    atr, vb = 400.0, 100.0

    _, first = w.observe(_forming(t0, 39600.0), atr, vb,
                         now=t0 + pd.Timedelta(minutes=5))
    _, second = w.observe(_forming(t0, 39580.0), atr, vb,
                          now=t0 + pd.Timedelta(minutes=6))
    _, third = w.observe(_forming(t0, 39100.0), atr, vb,
                         now=t0 + pd.Timedelta(minutes=7))
    assert first is not None, "the initial move did not alert"
    assert second is None, "a stationary price re-alerted"
    assert third is not None, "an extension of the move stayed silent"
    return True


def test_reset_reanchors_on_the_new_close():
    """After a bar closes, the move is measured from the NEW close.

    Keeping the old anchor would report a move that already happened, and
    would keep reporting it forever.
    """
    t0 = pd.Timestamp("2026-08-27T08:00:00Z")
    w = SpikeWatcher(SpikeConfig())
    w.reset(t0, 40000.0)
    r1, _ = w.observe(_forming(t0, 39600.0), 400.0, 100.0,
                      now=t0 + pd.Timedelta(minutes=30))
    assert abs(r1.move_atr - (-1.0)) < 1e-9

    w.reset(t0 + HOUR, 39600.0)              # that bar closed at 39,600
    r2, _ = w.observe(_forming(t0 + HOUR, 39600.0), 400.0, 100.0,
                      now=t0 + HOUR + pd.Timedelta(minutes=5))
    assert abs(r2.move_atr) < 1e-9, (
        f"move still reads {r2.move_atr:+.2f} ATR against the previous bar")
    return True


def test_thresholds_are_scale_invariant():
    """The same move must read the same at 40k and at 100k.

    This is the entire reason thresholds are in ATR rather than percent.
    """
    t0 = pd.Timestamp("2026-08-27T08:00:00Z")
    readings = []
    for price, atr in ((40000.0, 400.0), (100000.0, 1000.0)):
        w = SpikeWatcher(SpikeConfig())
        w.reset(t0, price)
        r, _ = w.observe(_forming(t0, price * 0.98), atr, 100.0,
                         now=t0 + pd.Timedelta(minutes=30))
        readings.append(r.move_atr)
    assert abs(readings[0] - readings[1]) < 1e-9, (
        f"same move read {readings[0]:.3f} vs {readings[1]:.3f} ATR")
    return True


def test_no_spike_without_a_usable_atr():
    """No ATR means no normalisation. Stay silent rather than guess."""
    t0 = pd.Timestamp("2026-08-27T08:00:00Z")
    w = SpikeWatcher(SpikeConfig())
    w.reset(t0, 40000.0)
    for bad in (float("nan"), 0.0, -5.0):
        r, sp = w.observe(_forming(t0, 20000.0), bad, 100.0,
                          now=t0 + pd.Timedelta(minutes=30))
        assert r is None and sp is None, f"atr={bad} produced a reading"
    return True


def test_volume_pace_is_silent_too_early_in_the_bar():
    """Projecting full-bar volume from 30 seconds divides by ~0.008."""
    t0 = pd.Timestamp("2026-08-27T08:00:00Z")
    w = SpikeWatcher(SpikeConfig())
    w.reset(t0, 40000.0)
    r, _ = w.observe(_forming(t0, 40000.0, volume=5.0), 400.0, 100.0,
                     now=t0 + pd.Timedelta(seconds=30))
    assert not np.isfinite(r.volume_pace), (
        f"pace reported {r.volume_pace} thirty seconds into the bar")

    r2, _ = w.observe(_forming(t0, 40000.0, volume=60.0), 400.0, 100.0,
                      now=t0 + pd.Timedelta(minutes=30))
    assert np.isfinite(r2.volume_pace), "pace stayed dead mid-bar"
    assert abs(r2.volume_pace - 1.2) < 0.05, r2.volume_pace
    return True


def test_volume_baseline_uses_the_median():
    """A mean would let one flush hide the next one."""
    bars = make_bars(300)
    bars = bars.copy()
    bars.loc[bars.index[-1], "volume"] = bars["volume"].median() * 500
    base = SpikeWatcher.volume_baseline(bars, SpikeConfig())
    assert base < bars["volume"].mean(), (
        "the baseline tracked the outlier - a mean would do this")
    return True


# ------------------------------------------------------------ resolution
def _analysis(side="LONG", entry=40000.0, up=40400.0, dn=39600.0) -> Analysis:
    t0 = pd.Timestamp("2026-08-27T08:00:00Z")
    return Analysis(name="A", opened_at=t0, ends_at=t0 + HOUR, bars_left=1,
                    p_up=0.55, entry=entry, upper=up, lower=dn, side=side)


def test_both_barriers_touched_counts_as_a_loss():
    """The labeller's convention. The screen must not be kinder than it."""
    state, detail = _analysis().resolution(high=40500.0, low=39500.0)
    assert state == "BOTH", state
    assert "LOSS" in detail, detail
    return True


def test_resolution_maps_side_to_win_and_loss():
    """Upper is a win for a long and a loss for a short. Flipping this is
    the same class of bug as inverting `is_buyer_maker`: no error, and every
    outcome recorded backwards."""
    _, long_up = _analysis("LONG").resolution(high=40500.0, low=39900.0)
    _, short_up = _analysis("SHORT").resolution(high=40500.0, low=39900.0)
    _, long_dn = _analysis("LONG").resolution(high=40100.0, low=39500.0)
    _, short_dn = _analysis("SHORT").resolution(high=40100.0, low=39500.0)
    assert "WIN" in long_up and "LOSS" in short_up, (long_up, short_up)
    assert "LOSS" in long_dn and "WIN" in short_dn, (long_dn, short_dn)
    return True


def test_resolution_is_open_between_the_barriers():
    state, _ = _analysis().resolution(high=40300.0, low=39700.0)
    assert state == "OPEN", state
    return True


def test_resolution_reports_no_side_when_none_was_proposed():
    """A WAIT window still has barriers, but nobody was in the trade."""
    _, detail = _analysis(side="").resolution(high=40500.0, low=39900.0)
    assert "no position was proposed" in detail, detail
    return True


# -------------------------------------------------------------- features
def test_appending_a_forming_bar_does_not_rewrite_history():
    """The leak check for the new path.

    Adding one bar at the end must change exactly one row of features. If an
    earlier row moves, a detector is reading forward - and the provisional
    read would be contaminated in a way no threshold could reveal.

    Same idea as `test_no_lookahead`, aimed at the forming-bar merge.
    """
    if not all(p.exists() for p in MODELS):
        return True                          # models not trained here
    bars = make_bars(700)
    mon = Monitor("BTCUSDT", "1h", MODELS[0], MODELS[1])
    closed = mon.features(bars)
    prov = mon.provisional_features(
        bars, _forming(bars.index[-1], float(bars["close"].iloc[-1]) * 1.01))

    common = closed.index.intersection(prov.index)
    assert len(common) > 100, "the two frames barely overlap"
    a, b = closed.loc[common], prov.loc[common]
    same = ((a == b) | (a.isna() & b.isna())).all()
    bad = [c for c in same.index if not same[c]]
    assert not bad, (
        f"appending a forming bar rewrote history in {len(bad)} columns: "
        f"{bad[:6]} - a detector is reading forward")
    return True


def test_provisional_features_do_not_poison_the_closed_bar_cache():
    """`features()` must keep returning the CLOSED frame afterwards."""
    if not all(p.exists() for p in MODELS):
        return True
    bars = make_bars(700)
    mon = Monitor("BTCUSDT", "1h", MODELS[0], MODELS[1])
    before = mon.features(bars)
    mon.provisional_features(
        bars, _forming(bars.index[-1], float(bars["close"].iloc[-1]) * 1.01))
    after = mon.features(bars)
    assert after is before, "the provisional call replaced the cached frame"
    assert after.index[-1] == before.index[-1] <= bars.index[-1], (
        "the cache now ends on a bar that never closed")
    return True


def test_atr_estimate_falls_back_without_agent2():
    """A model trained without Agent 2 carries no `atr_pct`. The watch must
    still work - a feature that quietly does nothing is worse than one that
    is switched off."""
    bars = make_bars(300)
    empty = pd.DataFrame(index=bars.index)          # no atr_pct anywhere
    v = atr_price_estimate(bars, empty)
    assert np.isfinite(v) and v > 0, f"fallback ATR came back {v}"
    span = float((bars["high"] - bars["low"]).tail(50).mean())
    assert 0.2 * span < v < 5 * span, f"fallback ATR {v} implausible vs {span}"
    return True


def test_spike_threshold_sits_below_the_barrier():
    """A spike alert at or above the barrier distance is a post-mortem.

    By the time it fires the barrier has been touched and the window is
    already decided, so there is nothing left to warn about.
    """
    cfg = SpikeConfig()
    assert cfg.spike_atr < 1.0, (
        f"default spike_atr {cfg.spike_atr} is not below the trained barrier "
        f"distance of 1.0 ATR - alerts would only arrive after the fact")
    return True


def test_the_live_window_reproduces_the_full_history_prediction():
    """Trimming history for a live read must not change the answer.

    The live path computes features on a tail rather than on all 172,000
    stored bars, because the cache is keyed on the newest bar and a 1m
    collector invalidates it every sixty seconds — recomputing everything
    each time burned 75% of a core. The trim is only safe because the
    detectors are causal, and that is exactly what this asserts: same newest
    row, same probability.

    If a non-causal feature is ever added, this fails and `live_window` has
    to grow (or the feature has to go).
    """
    from livefeed import BarStore

    if not all(p.exists() for p in MODELS):
        return True
    bars = BarStore("BTCUSDT", "1h").load()
    if len(bars) < 5000:
        return True

    mon = Monitor("BTCUSDT", "1h", MODELS[0], MODELS[1])
    window = mon.live_window(bars)
    assert len(window) >= mon.required_bars(bars), "the window is too short"

    full = mon._compute_features(bars)
    trimmed = mon._compute_features(window)
    p_full = float(mon.h2.predict_proba(full.iloc[[-1]])[0])
    p_trim = float(mon.h2.predict_proba(trimmed.iloc[[-1]])[0])
    assert abs(p_full - p_trim) < 1e-6, (
        f"trimming history moved the prediction {p_full:.6f} -> {p_trim:.6f}")
    return True


def test_the_live_window_never_trims_below_what_detectors_need():
    """A window shorter than warmup produces NaN columns and the frozen
    model refuses the frame - loudly, but only at request time."""
    from livefeed import BarStore

    if not all(p.exists() for p in MODELS):
        return True
    for interval in ("1h", "1m"):
        bars = BarStore("BTCUSDT", interval).load()
        if bars.empty:
            continue
        h1, h2 = __import__("core").model_paths("BTCUSDT", interval)
        if not (h1.exists() and h2.exists()):
            continue
        mon = Monitor("BTCUSDT", interval, h1, h2)
        assert len(mon.live_window(bars)) >= mon.required_bars(bars), interval
    return True


def test_above_an_hour_the_live_window_is_bounded_by_what_it_is_for():
    """A 4h dashboard read the whole 3.4-year store to produce one row.

    The 20,000-bar floor was measured on 1m, where it is two weeks; on 4h
    it is nine years, so it never bound anything and every rebuild walked
    the entire history — and with it one open-interest archive PER DAY of
    that history, 1,756 files parsed and ~350 requested that cannot exist.
    Above an hour the window is the measured multiple with two floors that
    name their purpose: detectors warm, and the rank's trailing window
    full several times over.
    """
    from monitor import TRAIL_BARS
    from livefeed import BarStore

    if not all(p.exists() for p in MODELS):
        return True
    bars = BarStore("BTCUSDT", "4h").load()
    h1, h2 = __import__("core").model_paths("BTCUSDT", "4h")
    if bars.empty or not (h1.exists() and h2.exists()):
        return True
    mon = Monitor("BTCUSDT", "4h", h1, h2)
    need = mon.required_bars(bars)
    w = mon.live_window(bars)
    assert len(w) >= need * 4, "shorter than the multiple the drift was measured at"
    assert len(w) >= need + 3 * TRAIL_BARS, "the rank window would run short"
    if len(bars) > need * 4 + 3 * TRAIL_BARS:
        assert len(w) < len(bars), "a long 4h store must still be trimmed"
    return True


def test_below_an_hour_nothing_about_the_window_changed():
    """1m is where the drift table was measured; it keeps its floor."""
    import pandas as pd

    if not all(p.exists() for p in MODELS):
        return True
    mon = Monitor("BTCUSDT", "1m", MODELS[0], MODELS[1])
    idx = pd.date_range("2026-01-01", periods=60_000, freq="1min", tz="UTC")
    bars = pd.DataFrame({c: 1.0 for c in ("open", "high", "low", "close", "volume")},
                        index=idx)
    want = max(mon.required_bars(bars) * 4, 20_000)
    assert len(mon.live_window(bars)) == min(want, len(bars))
    return True
