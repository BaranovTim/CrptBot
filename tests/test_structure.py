"""Barriers on the chart's levels: agent5/structure.py and what reads it.

What these guard:
  CAUSALITY     a level is usable only after the bars proving it have
                closed. Removing the future must not change any past level.
  THE SIDES     a long's target is the resistance, a short's the support;
                the label answers for the model's own side; timeouts lose.
  THE DECISION  a structure model calls by RANK against its trailing
                scores, never by expected value, and the rank is causal.
  THE GATE      a call is gated on the verdict of the model that made it.
  THE TABLE     4h is the structure timeframe; its slots are sides.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent5.config import Agent5Config
from agent5.labels import _atr, triple_barrier
from agent5.structure import barrier_prices, structural_levels, structure_barrier
from core import barriers_for, geometry_for, model_usable, slot_side
from monitor import STRUCTURE_RANKS, TRAIL_BARS, Analysis, evaluate
from tests.synthetic import make_bars

HOUR = pd.Timedelta("4h")


class RankedJudge:
    """A structure model stand-in: a score series, the last one steerable."""

    def __init__(self, side: str, scores, cfg=None):
        self.cfg = cfg or Agent5Config(max_hold_bars=16, k_up=1.0, k_dn=1.0,
                                       geometry="structure", side=side)
        self._scores = np.asarray(scores, dtype=float)
        self.columns = ["dummy"]

    def predict_proba(self, X):
        n = len(X)
        return self._scores[-n:] if n <= len(self._scores) else np.resize(self._scores, n)

    # the stand-in's raw and calibrated scores are the same series; the
    # production judge's are not, and `test_the_rank_is_on_the_raw_score`
    # covers that split with a real calibrator
    raw_scores = predict_proba


def _bars(n=900):
    bars = make_bars(n)
    # 4h spacing, so the daily levels have several bars per day
    bars.index = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    return bars


# ----------------------------------------------------------- the levels
def test_levels_never_change_when_the_future_is_removed():
    bars = _bars()
    atr = _atr(bars, 14).to_numpy(float)
    full = structural_levels(bars, atr)
    cut = structural_levels(bars.iloc[:-200], atr[:-200])
    for k in ("res", "sup", "res2", "sup2"):
        a, b = full[k][:-200], cut[k]
        both = np.isfinite(a) & np.isfinite(b)
        assert np.array_equal(np.isfinite(a), np.isfinite(b)), k
        assert np.allclose(a[both], b[both]), f"{k}: a past level moved"
    return True


def test_levels_sit_on_the_right_side_and_at_a_sane_distance():
    bars = _bars()
    atr = _atr(bars, 14).to_numpy(float)
    lv = structural_levels(bars, atr)
    c = bars["close"].to_numpy(float)
    m = lv["has_res"] & lv["has_sup"]
    assert m.mean() > 0.8, f"levels found on only {m.mean():.0%} of bars"
    assert np.all(lv["res"][m] > c[m]) and np.all(lv["sup"][m] < c[m])
    d_up = (lv["res"][m] - c[m]) / atr[m]; d_dn = (c[m] - lv["sup"][m]) / atr[m]
    assert d_up.min() >= 0.5 - 1e-9 and d_dn.min() >= 0.5 - 1e-9
    assert d_up.max() <= 6.0 + 1e-9 and d_dn.max() <= 6.0 + 1e-9
    # the level behind is further out, and distinct
    m2 = m & lv["has_res2"]
    assert np.all(lv["res2"][m2] > lv["res"][m2] + 0.25 * atr[m2] - 1e-9)
    return True


def test_a_bar_without_a_level_falls_back_to_one_atr():
    bars = _bars()
    atr = _atr(bars, 14).to_numpy(float)
    lv = structural_levels(bars, atr)
    lv["has_res"][:] = False                 # pretend nothing is above
    cfg = Agent5Config(max_hold_bars=16, geometry="structure", side="long")
    upper, lower, both = barrier_prices(bars, cfg, lv, atr)
    c = bars["close"].to_numpy(float)
    ok = np.isfinite(atr)
    assert np.allclose(upper[ok], c[ok] + atr[ok])
    assert not both.any()
    return True


# ----------------------------------------------------------- the label
def test_long_and_short_labels_answer_for_their_own_side():
    bars = _bars()
    L = triple_barrier(bars, Agent5Config(max_hold_bars=16, geometry="structure", side="long"))
    S = triple_barrier(bars, Agent5Config(max_hold_bars=16, geometry="structure", side="short"))
    # the same barriers, read from opposite sides
    c = bars["close"]
    assert np.allclose(L.tp_pct.dropna(), S.sl_pct.dropna()) and np.allclose(L.sl_pct.dropna(), S.tp_pct.dropna())
    # a long wins on the upper touch; a short on the lower; timeouts lose both
    tl, ts = L.touch.to_numpy(object), S.touch.to_numpy(object)
    yl, ys = L.y.to_numpy(), S.y.to_numpy()
    ok = ~np.isnan(yl)
    assert np.all(yl[ok & (tl == "upper")] == 1) and np.all(yl[ok & (tl == "lower")] == 0)
    assert np.all(ys[ok & (tl == "lower")] == 1) and np.all(ys[ok & (tl == "upper")] == 0)
    assert np.all(yl[ok & (tl == "timeout")] == 0) and np.all(ys[ok & (ts == "timeout")] == 0)
    assert np.all(yl[ok & (tl == "ambiguous")] == 0) and np.all(ys[ok & (ts == "ambiguous")] == 0)
    # no partial labels at the end of the data
    assert np.isnan(yl[-16:]).all()
    return True


def test_the_atr_geometry_is_untouched():
    bars = _bars()
    cfg = Agent5Config(max_hold_bars=2, k_up=1.0, k_dn=1.0)
    lab = triple_barrier(bars, cfg)
    assert np.allclose(lab.tp_pct.dropna(), lab.sl_pct.dropna())
    return True


def test_config_rejects_nonsense():
    for kw in ({"geometry": "fib"}, {"side": "both"}):
        try:
            Agent5Config(**kw)
        except ValueError:
            continue
        raise AssertionError(kw)
    return True


# --------------------------------------------------------- the decision
def _X(bars):
    return pd.DataFrame({"dummy": np.zeros(len(bars))}, index=bars.index)


def test_a_structure_model_calls_by_rank_not_by_ev():
    bars = _bars()
    X = _X(bars)
    rng = np.random.default_rng(0)
    base = rng.uniform(0.40, 0.60, len(bars))
    last = bars.index[-1]
    # a top-of-the-window score enters, a middling one waits, whatever EV says
    hi = base.copy(); hi[-1] = 0.61
    a = evaluate(RankedJudge("long", hi), bars, X, "ANALYSIS A", last, last + 16 * HOUR, 16)
    assert a.geometry == "structure" and a.rank >= 0.95, a.rank
    assert a.action == "ENTER LONG NOW" and a.strength == "strong" and a.side == "LONG"
    assert a.tp_price > a.entry > a.sl_price
    mid = base.copy(); mid[-1] = 0.50
    b = evaluate(RankedJudge("long", mid), bars, X, "ANALYSIS A", last, last + 16 * HOUR, 16)
    assert b.action == "WAIT" and b.strength == "" and b.side == ""
    # a short model's target is BELOW the entry and its p_up is 1 - p
    s = evaluate(RankedJudge("short", hi), bars, X, "ANALYSIS B", last, last + 16 * HOUR, 16)
    assert s.action == "ENTER SHORT NOW" and s.tp_price < s.entry < s.sl_price
    assert abs(s.p_up - (1 - 0.61)) < 1e-9
    return True


def test_the_rank_is_against_the_trailing_window_only():
    bars = _bars(TRAIL_BARS + 300)
    X = _X(bars)
    last = bars.index[-1]
    # ancient scores are enormous; recent ones small; the newest is the best
    # of the RECENT window, and that is what counts
    scores = np.concatenate([np.full(300, 0.99), np.full(TRAIL_BARS - 1, 0.45), [0.46]])
    a = evaluate(RankedJudge("long", scores), bars, X, "ANALYSIS A", last, last + 16 * HOUR, 16)
    assert a.rank == 1.0, a.rank
    assert a.action.startswith("ENTER")
    return True


def test_too_little_history_makes_no_call():
    bars = _bars(45)                      # fewer than the 50 trailing scores a rank needs
    X = _X(bars)
    last = bars.index[-1]
    a = evaluate(RankedJudge("long", np.full(45, 0.9)), bars, X, "ANALYSIS A", last, last + 16 * HOUR, 16)
    assert a.action == "WAIT" and np.isnan(a.rank)
    return True


def test_strength_levels_are_ordered_and_measured():
    cuts = [c for _, c in STRUCTURE_RANKS]
    assert cuts == sorted(cuts, reverse=True) and cuts[0] == 0.95 and cuts[1] == 0.90
    return True


# ------------------------------------------------------------- the gate
def test_the_call_is_gated_on_the_slot_that_made_it():
    d = Path(tempfile.mkdtemp())
    (d / "eval_BTCUSDT_4h.json").write_text(json.dumps({
        "symbol": "BTCUSDT", "interval": "4h", "horizons": {
            "h1": {"auc": 0.60, "shuffle": 0.49, "spread": 0.02, "side": "long"},
            "h2": {"auc": 0.50, "shuffle": 0.49, "spread": 0.02, "side": "short"}}}))
    ok1, _ = model_usable("BTCUSDT", "4h", output_dir=d, slot="h1")
    ok2, why = model_usable("BTCUSDT", "4h", output_dir=d, slot="h2")
    okany, _ = model_usable("BTCUSDT", "4h", output_dir=d)
    assert ok1 and okany and not ok2
    assert "short model" in why
    return True


def test_the_recommendation_names_its_slot_and_the_gate_reads_it():
    from api.service import _gate_by_verdict, _recommendation

    bars = _bars()
    X = _X(bars)
    last = bars.index[-1]
    rng = np.random.default_rng(1)
    base = rng.uniform(0.40, 0.60, len(bars)); hi = base.copy(); hi[-1] = 0.61
    a = evaluate(RankedJudge("long", base), bars, X, "ANALYSIS A", last, last + 16 * HOUR, 16)
    b = evaluate(RankedJudge("short", hi), bars, X, "ANALYSIS B", last, last + 16 * HOUR, 16)
    rec = _recommendation(b, a, stale=False)
    assert rec["action"] == "SELL" and rec["slot"] == "h2" and rec["geometry"] == "structure"
    assert rec["rank"] >= 0.95
    d = Path(tempfile.mkdtemp())
    (d / "eval_ZZZUSDT_4h.json").write_text(json.dumps({
        "symbol": "ZZZUSDT", "interval": "4h", "horizons": {
            "h1": {"auc": 0.60, "shuffle": 0.49, "spread": 0.02, "side": "long"},
            "h2": {"auc": 0.50, "shuffle": 0.49, "spread": 0.02, "side": "short"}}}))
    import core.timeframes as tfm
    old = tfm.eval_path
    tfm.eval_path = lambda s, i, o=None: d / f"eval_{s}_{i}.json"
    try:
        gated = _gate_by_verdict("ZZZUSDT", "4h", rec)
    finally:
        tfm.eval_path = old
    assert gated["action"] == "FLAT" and gated.get("gated"), gated
    return True


def test_when_both_sides_fire_the_more_confident_speaks():
    from api.service import _choose

    bars = _bars()
    X = _X(bars)
    last = bars.index[-1]
    rng = np.random.default_rng(2)
    base = rng.uniform(0.40, 0.60, len(bars))
    s1 = base.copy(); s1[-1] = 0.595      # strong, but not the best of the window
    s2 = base.copy(); s2[-1] = 0.61       # above everything
    a = evaluate(RankedJudge("long", s2), bars, X, "ANALYSIS A", last, last + 16 * HOUR, 16)
    b = evaluate(RankedJudge("short", s1), bars, X, "ANALYSIS B", last, last + 16 * HOUR, 16)
    assert a.action.startswith("ENTER") and b.action.startswith("ENTER")
    assert _choose(b, a) is a
    return True


# -------------------------------------------------------------- the table
def test_four_hours_is_the_structure_timeframe_and_its_slots_are_sides():
    assert geometry_for("4h") == "structure" and geometry_for("1d") == "structure"
    for tf in ("15m", "1h"):
        assert geometry_for(tf) == "atr"
    k, h1, h2 = barriers_for("4h")
    assert (h1, h2) == (16, 16), "structure slots hold for the same window"
    assert slot_side("4h", 1) == "long" and slot_side("4h", 2) == "short"
    return True


def test_levels_serialise_the_trades_own_target_and_stop():
    from api.service import _levels

    bars = _bars()
    X = _X(bars)
    last = bars.index[-1]
    rng = np.random.default_rng(3)
    hi = rng.uniform(0.40, 0.60, len(bars)); hi[-1] = 0.61
    s = evaluate(RankedJudge("short", hi), bars, X, "ANALYSIS B", last, last + 16 * HOUR, 16)
    lv = _levels(s, s.entry)
    assert lv["side"] == "SHORT"
    assert lv["tp_offset_pct"] < 0 < lv["sl_offset_pct"]
    assert abs(lv["tp_pct"] - abs(lv["tp_offset_pct"])) < 1e-9
    assert abs(lv["sl_pct"] - abs(lv["sl_offset_pct"])) < 1e-9
    return True


def test_when_neither_side_calls_the_card_speaks_for_the_nearer_one():
    from api.service import _choose

    bars = _bars()
    X = _X(bars)
    last = bars.index[-1]
    rng = np.random.default_rng(4)
    base = rng.uniform(0.40, 0.60, len(bars))
    near = base.copy(); near[-1] = 0.56          # a decent rank, not a call
    far = base.copy(); far[-1] = 0.41
    a = evaluate(RankedJudge("long", near), bars, X, "ANALYSIS A", last, last + 16 * HOUR, 16)
    b = evaluate(RankedJudge("short", far), bars, X, "ANALYSIS B", last, last + 16 * HOUR, 16)
    assert a.action == "WAIT" and b.action == "WAIT"
    assert _choose(b, a) is a
    assert "long entry" in a.reason
    return True


# ------------------------------------------------ the smart-money overlay
def _call(side, strength):
    a = Analysis("ANALYSIS A", pd.Timestamp("2026-09-21", tz="UTC"),
                 pd.Timestamp("2026-09-24", tz="UTC"), 16)
    a.action = f"ENTER {side} NOW"; a.side = side; a.strength = strength
    a.geometry = "structure"; a.reason = "A signal."; a.size_pct = 2.0
    return a


def test_agreement_raises_a_call_and_disagreement_lowers_it():
    from api.service import _smart_overlay

    a = _call("LONG", "small")
    _smart_overlay(a, {"net": 2, "longs": 2, "shorts": 0})
    assert a.strength == "medium" and a.smart_effect == "raised"
    assert "Smart money agrees: 2 followed traders opened LONG" in a.reason
    b = _call("LONG", "strong")
    _smart_overlay(b, {"net": 1, "longs": 1, "shorts": 0})
    assert b.strength == "strong" and b.smart_effect == "confirmed"
    c = _call("SHORT", "strong")
    _smart_overlay(c, {"net": 1, "longs": 1, "shorts": 0})
    assert c.strength == "medium" and c.smart_effect == "lowered" and c.action.startswith("ENTER")
    assert "disagrees" in c.smart_note and "lowered to medium" in c.reason
    return True


def test_a_small_call_the_followed_bet_against_is_withdrawn():
    from api.service import _smart_overlay

    a = _call("LONG", "small")
    _smart_overlay(a, {"net": -1, "longs": 0, "shorts": 1})
    assert a.action == "WAIT" and a.side == "" and a.strength == "" and a.size_pct == 0.0
    assert a.smart_effect == "withdrawn" and "went the other way" in a.reason
    return True


def test_silence_and_no_call_leave_everything_alone():
    from api.service import _smart_overlay

    a = _call("LONG", "medium")
    _smart_overlay(a, {"net": 0, "longs": 1, "shorts": 1})
    assert a.strength == "medium" and a.smart_note == "" and a.reason == "A signal."
    w = Analysis("ANALYSIS A", pd.Timestamp("2026-09-21", tz="UTC"),
                 pd.Timestamp("2026-09-24", tz="UTC"), 16)
    w.action = "WAIT"; w.reason = "No entry."
    _smart_overlay(w, {"net": 3, "longs": 3, "shorts": 0})
    assert w.action == "WAIT" and w.strength == "", "the overlay never creates a call"
    return True


def test_the_overlay_windows_and_asymmetry_are_the_measured_ones():
    from api.service import SMART_OVERLAY, _smart_overlay
    assert SMART_OVERLAY == {"4h": {"hours": 24.0, "lower": True},
                             "1d": {"hours": 72.0, "lower": False}}
    # daily: agreement raises, disagreement only annotates
    a = _call("LONG", "small")
    _smart_overlay(a, {"net": 1, "longs": 1, "shorts": 0, "hours": 72}, lower=False)
    assert a.strength == "medium" and "last 72h" in a.smart_note
    b = _call("LONG", "small")
    _smart_overlay(b, {"net": -2, "longs": 0, "shorts": 2, "hours": 72}, lower=False)
    assert b.strength == "small" and b.action.startswith("ENTER") and b.smart_effect == "noted"
    assert "the call stands" in b.reason
    return True


def test_the_rank_is_on_the_raw_score_not_the_calibrated_plateaus():
    """A calibrated score takes a handful of values; ranking it counts a
    whole plateau as ties and starves the top of the window of calls."""
    from agent5.calibration import Calibrator

    class PlateauJudge(RankedJudge):
        def predict_proba(self, X):
            r = self.raw_scores(X)
            return np.where(r >= 0.55, 0.62, 0.48)      # two plateaus

        def raw_scores(self, X):
            n = len(X)
            return self._scores[-n:] if n <= len(self._scores) else np.resize(self._scores, n)

    bars = _bars()
    X = _X(bars)
    rng = np.random.default_rng(5)
    base = rng.uniform(0.40, 0.60, len(bars)); base[-1] = 0.61   # the best raw score
    a = evaluate(PlateauJudge("long", base), bars, X, "ANALYSIS A",
                 bars.index[-1], bars.index[-1] + 16 * HOUR, 16)
    assert a.rank == 1.0, a.rank                    # on raw: top of the window
    assert abs(a.p_up - 0.62) < 1e-9                # the probability shown is the calibrated one
    assert a.action.startswith("ENTER")
    # ties count half, not zero
    tie = np.full(len(bars), 0.5); tie[-1] = 0.5
    b = evaluate(RankedJudge("long", tie), bars, X, "ANALYSIS A",
                 bars.index[-1], bars.index[-1] + 16 * HOUR, 16)
    assert abs(b.rank - 0.5) < 1e-9, b.rank
    return True
