"""The read-only JSON API behind the mobile app.

What these guard, in order of how badly they would mislead you:

  A DEFAULT BUY          the mockup the app was built from shows a confident
                         BUY. If the payload ever falls back to that when the
                         backend actually said WAIT, the phone would be
                         advising a trade the EV rule rejected. The
                         recommendation must come from `evaluate()`, always.

  NaN REACHING THE WIRE  `json.dumps` writes bare NaN by default, which is
                         invalid JSON that many parsers accept anyway and turn
                         into garbage. The server dumps with allow_nan=False,
                         so an unconverted NaN raises here rather than
                         rendering as a price on a phone.

  ZERO FOR ABSENT        the Python side is careful that a missing level is
                         NaN and not 0, because 0 means "price is exactly
                         here". That distinction has to survive the JSON hop,
                         which is why absent values become null, never 0.

  A BADGE THAT LIES      nothing in this project places an order. The status
                         payload must never imply otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from api.service import _analysis, _indicators, _num, _recommendation
from monitor import Analysis

HOUR = pd.Timedelta("1h")
T0 = pd.Timestamp("2026-08-28T12:00:00Z")
MODELS = [Path("output/judge_h1.joblib"), Path("output/judge_h2.joblib")]


def _wait(name: str = "ANALYSIS B") -> Analysis:
    a = Analysis(name=name, opened_at=T0, ends_at=T0 + HOUR, bars_left=2)
    a.p_up, a.entry = 0.535, 79000.0
    a.upper, a.lower = 79500.0, 78500.0
    a.tp_price, a.sl_price = 79500.0, 78500.0
    a.ev_long, a.ev_short = -0.06, -0.14
    a.action = "WAIT"
    a.reason = "best EV -0.060% is below the +0.05% threshold"
    return a


def _enter(side: str = "LONG") -> Analysis:
    a = _wait("ANALYSIS A")
    a.action, a.side, a.size_pct = f"ENTER {side} NOW", side, 10.4
    a.ev_long, a.ev_short = (0.73, -0.9) if side == "LONG" else (-0.9, 0.73)
    a.reason = "EV clears the threshold after costs"
    return a


# ------------------------------------------------------------- numbers
def test_nan_and_inf_become_none_not_zero():
    """Zero means "the level is exactly here". Absent must not read as that."""
    for bad in (float("nan"), float("inf"), float("-inf"), None, "x"):
        assert _num(bad) is None, f"{bad!r} did not become None"
    assert _num(0.0) == 0.0, "a real zero was thrown away"
    assert _num(np.float64(1.5)) == 1.5
    return True


def test_analysis_payload_is_json_safe():
    """A NaN here would be written as bare NaN and parsed as garbage."""
    a = Analysis(name="A", opened_at=T0, ends_at=T0 + HOUR, bars_left=1)
    payload = _analysis(a)                    # every numeric field is NaN
    json.dumps(payload, allow_nan=False)      # raises if any NaN survived
    assert payload["p_up"] is None
    assert payload["take_profit"] is None
    return True


# ------------------------------------------------- the big card's verdict
def test_a_waiting_backend_never_renders_as_buy():
    """The single most dangerous substitution the app could make."""
    r = _recommendation(_wait(), _wait(), stale=False)
    assert r["action"] == "FLAT", r
    assert r["action"] != "BUY"
    assert r["tone"] == "flat"
    assert "threshold" in r["detail"]
    return True


def test_an_entering_backend_renders_its_own_side():
    long_r = _recommendation(_enter("LONG"), _wait(), stale=False)
    short_r = _recommendation(_enter("SHORT"), _wait(), stale=False)
    assert long_r["action"] == "BUY" and long_r["tone"] == "up", long_r
    assert short_r["action"] == "SELL" and short_r["tone"] == "down", short_r
    return True


def test_a_stale_backend_says_so_instead_of_advising():
    """An expired window is history. Advising off it is the worst failure."""
    r = _recommendation(_enter("LONG"), _enter("LONG"), stale=True)
    assert r["action"] == "STALE", r
    assert r["action"] not in ("BUY", "SELL")
    return True


def test_the_secondary_window_can_supply_the_signal():
    """A wins on the primary; if only the secondary entered, use it."""
    r = _recommendation(_wait(), _enter("LONG"), stale=False)
    assert r["action"] == "BUY", r
    return True


# ---------------------------------------------------------- indicators
def test_rsi_notes_track_the_value():
    for value, expect in ((75, "Overbought"), (65, "Approaching Overbought"),
                          (50, "Neutral Range"), (35, "Approaching Oversold"),
                          (25, "Oversold")):
        got = _indicators({"rsi_14": float(value)})
        assert got[0]["note"] == expect, (value, got[0]["note"])
    return True


def test_indicators_skip_columns_that_are_not_there():
    """A model trained without a block must not produce an empty card."""
    assert _indicators({}) == []
    assert _indicators({"rsi_14": float("nan")}) == []
    return True


# ----------------------------------------------------- the live payload
def test_dashboard_payload_survives_strict_json():
    """The end-to-end contract: everything the phone reads must serialise."""
    if not all(p.exists() for p in MODELS):
        return True
    from livefeed import BarStore
    if BarStore("BTCUSDT", "1h").load().empty:
        return True                            # nothing collected here

    from api.service import get_service
    d = get_service().dashboard()
    json.dumps(d, allow_nan=False)             # the actual guarantee

    assert d["status"]["trades"] is False, "the app claimed the bot trades"
    assert d["recommendation"]["action"] in ("BUY", "SELL", "FLAT", "STALE")
    assert d["calibration_note"], "the honesty note went missing"
    for a in d["analyses"]:
        assert a["p_up"] is None or 0.0 <= a["p_up"] <= 1.0
    return True


def test_untrained_pairs_are_reported_as_untrained():
    """The app routes on this. Getting it wrong shows odds with no model."""
    from api.service import get_service
    info = get_service().training("ETHUSDT")
    assert info["trained"] is False, info
    assert "command" in info and "main.py" in info["command"]
    return True
