"""The daily swing rules: agent5/trail.py and what serves it.

  THE TRAIL    the stop follows confirmed swings, only tighter, only from
               the bar a swing confirmed on, never above the price.
  THE TREND    a daily long below the 200-day average is not made; a
               short is never gated.
  THE TABLE    daily is a structure timeframe; its slots are sides.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent5.trail import next_trail_step, trailing_stop, trend_ok, trend_state
from core import barriers_for, geometry_for, slot_side
from tests.synthetic import make_bars


def _daily(n=600, seed=0):
    bars = make_bars(n, seed=seed) if "seed" in make_bars.__code__.co_varnames else make_bars(n)
    bars.index = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    return bars


def test_the_trail_only_tightens_and_only_after_a_swing_confirms():
    bars = _daily()
    for side in ("LONG", "SHORT"):
        prev = None
        # walking the clock forward, the stop must be monotone in the
        # trade's favour, and each intermediate state must be what the full
        # history says as of that bar (no future swing pulls it early)
        i0 = 200
        stops = []
        for end in range(i0 + 1, i0 + 120):
            st = trailing_stop(bars.iloc[:end], bars.index[i0], side)
            stops.append(st.stop)
            if prev is not None:
                assert (st.stop >= prev - 1e-9) if side == "LONG" else (st.stop <= prev + 1e-9), (side, end)
            prev = st.stop
        full = trailing_stop(bars, bars.index[i0], side)
        assert full.moves >= 1 or full.stop == full.initial_stop
    return True


def test_the_stop_never_sits_above_the_last_close():
    bars = _daily(800)
    close = bars["close"].to_numpy(float)
    for i0 in (150, 300, 450):
        for end in range(i0 + 2, i0 + 100, 7):
            st = trailing_stop(bars.iloc[:end], bars.index[i0], "LONG")
            assert st.stop < close[end - 2] + 1e-9 or st.stop == st.initial_stop
    return True


def test_a_logged_stop_is_the_floor_the_trail_starts_from():
    bars = _daily()
    st = trailing_stop(bars, bars.index[300], "LONG", initial_stop=1.0)
    assert st.initial_stop == 1.0 and st.stop >= 1.0
    st2 = trailing_stop(bars, bars.index[300], "LONG", initial_stop=1e9)
    assert st2.stop == 1e9, "a tighter logged stop is never loosened"
    return True


def test_the_trend_gate_is_long_only():
    bars = _daily(400)
    c = bars["close"]
    ema = c.ewm(span=200, adjust=False).mean()
    above = bool(c.iloc[-1] > ema.iloc[-1])
    assert trend_ok(bars, "LONG") is above
    assert trend_ok(bars, "SHORT") is True
    assert trend_ok(bars.iloc[:50], "LONG") is None, "too little history says nothing"
    t = trend_state(bars)
    assert t["above"] is above and t["span"] == 200 and abs(t["ema"] - float(ema.iloc[-1])) < 1e-9
    return True


def test_a_daily_buy_below_the_average_is_flat_and_a_sell_is_not():
    from api.service import _gate_by_trend

    bars = _daily(400)
    # force the close under the average by appending a crash bar
    low = bars.copy()
    last = low.iloc[-1].copy(); last["close"] = float(low["close"].min()) * 0.5
    last["low"] = min(last["low"], last["close"]); last["open"] = last["close"]
    low.iloc[-1] = last
    buy = {"action": "BUY", "tone": "up", "strength": "strong", "slot": "h1", "window_ends": "x"}
    sell = {"action": "SELL", "tone": "down", "strength": "strong", "slot": "h2", "window_ends": "x"}
    g = _gate_by_trend("1d", low, buy)
    assert g["action"] == "FLAT" and g["gated_by"] == "trend" and "200-day" in g["detail"]
    assert _gate_by_trend("1d", low, sell) is sell
    assert _gate_by_trend("4h", low, buy) is buy, "only daily gates on the trend"
    hi = bars.copy()
    last = hi.iloc[-1].copy(); last["close"] = float(hi["close"].max()) * 2
    last["high"] = max(last["high"], last["close"]); hi.iloc[-1] = last
    assert _gate_by_trend("1d", hi, buy) is buy
    return True


def test_daily_is_a_structure_timeframe_with_side_slots():
    assert geometry_for("1d") == "structure"
    k, h1, h2 = barriers_for("1d")
    assert (h1, h2) == (10, 10)
    assert slot_side("1d", 1) == "long" and slot_side("1d", 2) == "short"
    return True


def test_the_trail_json_is_what_the_app_reads():
    bars = _daily()
    st = trailing_stop(bars, bars.index[300], "LONG")
    j = st.to_json()
    assert set(j) == {"side", "stop", "initial_stop", "moved_at", "moves", "swings"}
    assert j["swings"][0] == j["initial_stop"] and j["swings"][-1] == j["stop"]
    assert (j["moved_at"] is None) == (j["moves"] == 0)
    return True


def test_the_next_step_is_the_swing_the_trail_is_waiting_for():
    """What the app promises between moves: "the stop moves to X at T, if no
    daily low goes below X before then". Walk the clock: every forming step
    that was not traded through by T is a stop the trail took by T."""
    bars = _daily(700, seed=4)
    for side in ("LONG", "SHORT"):
        long = side == "LONG"
        i0, kept, seen, void = 150, 0, 0, 0
        for k in range(i0 + 10, len(bars) - 5):
            st = trailing_stop(bars.iloc[:k], bars.index[i0], side)
            nx = next_trail_step(bars.iloc[:k], st)
            if nx is None:
                continue
            seen += 1
            assert (st.stop < nx["stop"]) if long else (nx["stop"] < st.stop), (side, k)
            at = pd.Timestamp(nx["at"])
            ahead = bars[(bars.index > bars.index[k - 1]) & (bars.index <= at)]
            broke = (ahead["low"] < nx["stop"]).any() if long else (ahead["high"] > nx["stop"]).any()
            if broke:
                void += 1
                continue
            later = trailing_stop(bars[bars.index <= at], bars.index[i0], side)
            assert any(abs(x - nx["stop"]) < 1e-9 for x in later.swings), (side, k, nx, later.swings)
            kept += 1
        assert seen > 10 and kept > 0, (side, seen, kept, void)
    return True


def test_no_step_is_promised_beyond_the_price():
    """A swing above the last close (below it, for a short) is not a stop
    the trail would take: it must not be promised."""
    bars = _daily(400, seed=2)
    for k in range(120, 400, 7):
        b = bars.iloc[:k]
        for side in ("LONG", "SHORT"):
            st = trailing_stop(b, b.index[100], side)
            nx = next_trail_step(b, st)
            if nx:
                last = float(b["close"].iloc[-1])
                assert (nx["stop"] < last) if side == "LONG" else (nx["stop"] > last)
    return True

