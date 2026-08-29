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
        assert got[0]["note"].startswith(expect), (value, got[0]["note"])
    return True


def test_the_rsi_note_says_which_bars_it_spans():
    """RSI(14) is 14 minutes at 1m and 14 hours at 1h. Same number, wildly
    different claim — the card has to say which."""
    for interval in ("1m", "1h", "1d"):
        note = _indicators({"rsi_14": 50.0}, interval=interval)[0]["note"]
        assert interval in note, note
    return True


def test_the_htf_card_names_the_timeframe_it_is_actually_showing():
    """The bug this guards: the label was hardcoded "4H STRUCTURE" while the
    value came from whatever `htf_rule` was set to. On the 1d screen that
    displayed the WEEKLY trend under a 4H heading, which then appeared to
    contradict the 1h screen for a reason no user could see. Agent 1's column
    is called `trend_direction_4h` on every timeframe, so the column name
    cannot be trusted as the label."""
    for interval, htf in (("1m", "15m"), ("15m", "4h"), ("1h", "4h"),
                          ("4h", "1d"), ("1d", "1w")):
        card = [c for c in _indicators({"trend_direction_4h": 1.0},
                                       interval=interval, htf=htf)
                if c["key"] == "htf"][0]
        assert card["label"] == f"{htf.upper()} STRUCTURE", card["label"]
        assert htf in card["note"], card["note"]
    return True


def test_levels_describe_the_same_window_the_card_recommends():
    """A BUY from the 1-bar model shown above a probability from the 2-bar
    model reads as incoherent: "BUY" next to "51.7%". Both must come from
    whichever window actually won."""
    from api.service import _choose

    entering, waiting = _enter("LONG"), _wait()
    assert _choose(waiting, entering) is entering, "an entering window lost"
    assert _choose(entering, waiting) is entering
    # nothing entering -> the primary window speaks
    assert _choose(waiting, waiting) is waiting

    rec = _recommendation(waiting, entering, stale=False)
    assert rec["p_up"] == entering.p_up
    assert rec["window_bars"] == entering.bars_left
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
    assert "train.py" in info.get("command", ""), info.get("command")
    return True


def test_training_is_reported_per_timeframe_not_per_symbol():
    """A symbol is not trained or untrained. Each timeframe is its own model,
    and BTCUSDT having a 1h model says nothing about its 5m."""
    from api.service import get_service
    svc = get_service()
    rows = {t["interval"]: t["trained"] for t in svc.timeframes("ETHUSDT")}
    assert rows and not any(rows.values()), rows

    btc = svc.training("BTCUSDT", interval="1h")
    assert btc["interval"] == "1h" and btc["htf"] == "4h", btc
    return True


def test_the_cost_drag_is_reported_and_flags_the_untradeable():
    """At 1m the barriers sit inside the round trip. That has to reach the
    surface — it is not a model problem and no accuracy fixes it."""
    from api.service import get_service
    svc = get_service()
    c = svc.cost_drag("BTCUSDT", "1m")
    if c["span_pct"] is None:
        return True                       # no 1m bars collected here
    assert c["cost_share"] > c["cost_share"] * 0, c
    assert c["verdict"] in ("untradeable", "marginal", "workable")
    slow = svc.cost_drag("BTCUSDT", "1d")
    if slow["span_pct"] is not None:
        assert c["cost_share"] > slow["cost_share"], (
            "fees must eat a larger share of a smaller barrier span")
    return True


# ------------------------------------------------------------ the universe
def test_the_api_stays_read_only():
    """The watchlist lives on the DEVICE, and this is why.

    The service has no auth and no rate limiting, and its whole security
    argument is that the worst a compromise yields is what the terminal
    already prints. A write endpoint — even one that only stores a few
    strings — trades that away. If a handler for anything but GET ever
    appears here, that argument needs rewriting first.
    """
    from api.server import Handler

    verbs = [m for m in dir(Handler) if m.startswith("do_")]
    assert sorted(verbs) == ["do_GET", "do_OPTIONS"], verbs
    return True


def test_an_unlisted_symbol_is_flagged_not_silently_blank():
    """PEPEUSDT is not a perpetual — 1000PEPEUSDT is. Without the flag the
    row renders as dashes with nothing to explain them."""
    from api.service import get_service

    svc = get_service()
    if not svc.symbols(limit=5):
        return True                              # offline
    rows = {c["symbol"]: c for c in
            svc.coins(["BTCUSDT", "DEFINITELYNOTAPAIRUSDT"])}
    assert rows["BTCUSDT"]["listed"] is True
    assert rows["DEFINITELYNOTAPAIRUSDT"]["listed"] is False
    assert rows["DEFINITELYNOTAPAIRUSDT"]["price"] is None
    return True


def test_the_universe_is_tradable_usdt_perpetuals_only():
    """A SETTLING contract or a quarterly future would look identical in the
    picker and then have no live price behind it."""
    from api.service import get_service

    rows = get_service().symbols(limit=500)
    if not rows:
        return True
    assert len(rows) > 100, f"only {len(rows)} symbols; the filter is too tight"
    for r in rows[:50]:
        assert r["symbol"].endswith("USDT"), r["symbol"]
        assert r["quote"] == "USDT", r
    return True


def test_symbols_are_ordered_by_volume_not_alphabetically():
    """Searching "b" must offer BTC before BAKE."""
    from api.service import get_service

    rows = get_service().symbols(limit=20)
    if len(rows) < 5:
        return True
    vols = [r["volume_24h"] or 0.0 for r in rows]
    assert vols == sorted(vols, reverse=True), vols[:5]
    assert rows[0]["symbol"] == "BTCUSDT", rows[0]["symbol"]
    return True


def test_search_matches_both_the_pair_and_the_base_asset():
    from api.service import get_service

    svc = get_service()
    if not svc.symbols(limit=5):
        return True
    got = {r["symbol"] for r in svc.symbols(q="sol", limit=30)}
    assert "SOLUSDT" in got, got
    for sym in got:
        assert "SOL" in sym, sym
    return True


def test_coins_honours_the_list_it_is_given():
    """The device decides what is followed; the server just answers."""
    from api.service import get_service

    svc = get_service()
    if not svc.symbols(limit=5):
        return True
    picked = ["ETHUSDT", "BTCUSDT"]
    got = [c["symbol"] for c in svc.coins(picked)]
    assert got == picked, got                     # order preserved too
    return True


def test_a_slow_build_of_one_timeframe_does_not_block_another():
    """The bug that broke the phone: one 1m build stalling every other screen.

    `dashboard()` used to call `_build_dashboard()` while holding the single
    global lock. On a laptop the builds are ~3s and nobody notices; on the
    one-core droplet a 1m build is 46s, so switching to 1m made the 4h screen
    time out too, and the 5s TTL meant it rebuilt forever and never recovered.

    Two pairs, one of them deliberately slow. The fast one must return while
    the slow one is still building.
    """
    import threading
    import time

    from api.service import TradingService

    svc = TradingService()
    started = threading.Event()
    release = threading.Event()

    def fake_build(symbol, interval):
        if interval == "1m":
            started.set()
            release.wait(10)                      # hold it open
        return {"symbol": symbol, "interval": interval}

    svc._build_dashboard = fake_build

    slow = threading.Thread(target=svc.dashboard, args=("BTCUSDT", "1m"))
    slow.start()
    assert started.wait(5), "slow build never started"

    t0 = time.time()
    got = svc.dashboard("BTCUSDT", "4h")          # must NOT queue behind 1m
    elapsed = time.time() - t0

    release.set()
    slow.join(10)

    assert got["interval"] == "4h", got
    assert elapsed < 2.0, f"4h waited {elapsed:.1f}s on the 1m build"
    return True


def test_an_expired_dashboard_is_served_immediately_not_rebuilt_inline():
    """Stale-while-revalidate: expiry must never make a phone wait.

    A cached payload past its TTL is returned as-is and refreshed behind the
    request. Only a pair that has never been built is allowed to block.
    """
    import threading
    import time

    from api.service import TradingService, _Cached

    svc = TradingService()
    calls = []
    gate = threading.Event()

    def fake_build(symbol, interval):
        calls.append(interval)
        gate.wait(10)                             # any inline call would hang
        return {"symbol": symbol, "interval": interval, "fresh": True}

    svc._build_dashboard = fake_build
    key = ("BTCUSDT", "1m")
    svc._dash[key] = _Cached(time.time() - 10_000, {"stale": True})

    t0 = time.time()
    got = svc.dashboard("BTCUSDT", "1m")
    elapsed = time.time() - t0
    gate.set()

    assert got == {"stale": True}, got            # the old copy, right away
    assert elapsed < 1.0, f"took {elapsed:.1f}s to serve a cached payload"
    return True


def test_the_dashboard_ttl_tracks_the_bar_period():
    """A 1h payload rebuilt every 5s is pure waste; it changes once an hour.

    The old fixed 5s TTL against a 10-46s build meant the cache was expired
    every time it was read.
    """
    from api.service import TradingService

    svc = TradingService()
    t = lambda iv: svc._dash_ttl(("BTCUSDT", iv))
    assert t("1m") < t("15m") < t("1h")
    assert t("1m") >= 30.0                        # never hammer a slow box
    assert t("1d") <= 900.0                       # never serve a stale day

    # and it backs off from what the build ACTUALLY costs. 27s is the measured
    # 1m build on the one-core droplet; a 30s TTL there means never stopping.
    svc._build_cost[("BTCUSDT", "1m")] = 27.0
    assert t("1m") >= 108.0, t("1m")
    return True
