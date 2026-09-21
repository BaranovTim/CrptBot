"""Timeframes, and the one that silently ate a year of history.

What these guard, in order of how badly they would mislead you:

  MINUTES BECOMING MONTHS   pandas reads "15m" as fifteen MONTH-ENDS. A
                            minute-based higher timeframe therefore collapses
                            all of history into a single bucket, every HTF
                            column goes flat, nothing raises, the tests still
                            pass, and the model quietly learns nothing from a
                            block that looks present. `htf_rule="4h"` only ever
                            worked because "h" happens to mean hours in both
                            notations.

  A LADDER THAT DOES NOT    a fixed 4h context is four bars up from 1h and two
  SCALE                     hundred and forty bars up from 1m. At that
                            distance it barely changes between samples and the
                            feature is dead weight.

  MODEL PATHS COLLIDING     six timeframes are six different models. If two
                            resolved to the same file, training one would
                            silently overwrite another and the app would serve
                            a 1d model on a 5m chart.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from core import (TIMEFRAMES, barriers_for, htf_for, interval_seconds,
                  model_paths, pandas_rule)


def test_minutes_resample_as_minutes_not_months():
    """The bug this module exists for."""
    idx = pd.date_range("2026-01-01", periods=200, freq="1min", tz="UTC")
    df = pd.DataFrame({"c": 1.0}, index=idx)

    raw = len(df.resample("15m").agg({"c": "last"}).dropna())
    fixed = len(df.resample(pandas_rule("15m")).agg({"c": "last"}).dropna())

    assert raw == 1, f"pandas changed: '15m' gave {raw} groups, expected 1"
    assert fixed == 14, f"pandas_rule('15m') gave {fixed} groups, expected 14"
    return True


def test_every_offered_timeframe_translates():
    for tf in TIMEFRAMES:
        rule = pandas_rule(tf)
        assert pd.tseries.frequencies.to_offset(rule) is not None, tf
    return True


def test_an_unknown_interval_is_rejected_not_guessed():
    for bad in ("7q", "", "1 hour", "H1", None):
        try:
            pandas_rule(bad)
        except (ValueError, TypeError):
            continue
        raise AssertionError(f"{bad!r} was accepted")
    return True


def test_the_higher_timeframe_is_always_a_step_up():
    for tf in TIMEFRAMES:
        base, up = interval_seconds(tf), interval_seconds(htf_for(tf))
        ratio = up / base
        assert ratio > 1, f"{tf} -> {htf_for(tf)} is not higher"
        assert 3 <= ratio <= 20, (
            f"{tf} -> {htf_for(tf)} is {ratio:.0f}x; outside 3-20x it is "
            f"either indistinguishable from the base or effectively static")
    return True


def test_the_htf_is_a_whole_multiple_of_the_base():
    """A ragged ratio means HTF bars straddle base bars unevenly."""
    for tf in TIMEFRAMES:
        base, up = interval_seconds(tf), interval_seconds(htf_for(tf))
        assert up % base == 0, f"{tf} -> {htf_for(tf)} is not a whole multiple"
    return True


def test_one_h_keeps_its_original_context():
    """The frozen 1h models were fitted with a 4h context. Changing the
    ladder under them would make live features differ from training."""
    assert htf_for("1h") == "4h"
    return True


def test_model_paths_are_distinct_per_timeframe():
    seen = {}
    for tf in TIMEFRAMES:
        for p in model_paths("BTCUSDT", tf):
            assert p not in seen, (
                f"{tf} and {seen[p]} resolve to the same file {p.name} — "
                f"training one would overwrite the other")
            seen[p] = tf
    return True


def test_model_paths_are_distinct_per_symbol():
    a = model_paths("BTCUSDT", "15m")
    b = model_paths("ETHUSDT", "15m")
    assert set(a).isdisjoint(set(b)), (a, b)
    return True


def test_btcusdt_1h_keeps_the_legacy_filenames():
    """Those files exist and every command in the README names them."""
    h1, h2 = model_paths("BTCUSDT", "1h")
    assert h1.name == "judge_h1.joblib", h1.name
    assert h2.name == "judge_h2.joblib", h2.name
    return True


def test_timeframes_are_ordered_fastest_first():
    secs = [interval_seconds(tf) for tf in TIMEFRAMES]
    assert secs == sorted(secs), TIMEFRAMES
    return True


def test_agents_accept_a_minute_based_htf():
    """End to end: the config value the ladder produces for 1m must work."""
    from agent1.htf import resample_bars as r1
    from agent2.htf import resample_bars as r2

    idx = pd.date_range("2026-01-01", periods=600, freq="1min", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0,
                       "close": 1.0, "volume": 1.0}, index=idx)
    for fn in (r1, r2):
        got = len(fn(df, htf_for("1m")))
        assert got > 10, f"{fn.__module__} gave {got} HTF bars from 600 minutes"
    return True


# ------------------------------------------------------- barrier geometry
#
# Median 1-ATR move as a share of price, measured on BTCUSDT. These are what
# make the fast timeframes impossible with the default barriers, so the test
# carries them rather than re-deriving them from whatever happens to be in
# the store.
_ATR_PCT = {"1m": 0.046, "5m": 0.139, "15m": 0.278,
            "1h": 0.639, "4h": 1.528, "1d": 4.133}
_ROUND_TRIP_PCT = 0.10


def test_every_timeframe_targets_a_move_bigger_than_its_costs():
    """The check that disqualifies a timeframe before accuracy is discussed.

    With the default +/-1 ATR, 1m spans 0.092% against a 0.100% round trip —
    fees are 109% of the whole distance from take-profit to stop-loss, and no
    model can win that. The profiles exist to keep every timeframe on the
    right side of this.
    """
    for tf in TIMEFRAMES:
        k, _, _ = barriers_for(tf)
        span = 2 * k * _ATR_PCT[tf]
        share = _ROUND_TRIP_PCT / span
        assert share <= 0.15, (
            f"{tf}: fees are {share:.0%} of the {span:.2f}% span — "
            f"too thin to trade whatever the model says")
    return True


def test_the_default_barriers_would_be_untradeable_at_1m():
    """Guards the reasoning, not just the result: if this ever stops being
    true the profiles are solving a problem that no longer exists."""
    span = 2 * 1.0 * _ATR_PCT["1m"]          # the +/-1 ATR default
    assert _ROUND_TRIP_PCT / span > 1.0, (
        f"+/-1 ATR at 1m spans {span:.3f}%, which now clears a "
        f"{_ROUND_TRIP_PCT}% round trip — revisit BARRIERS")
    return True


def test_the_hold_is_long_enough_for_the_target_to_be_reachable():
    """A random walk covers k ATR in about k^2 bars. A hold much shorter than
    that means only the vertical barrier ever fires, and the model learns to
    predict the clock instead of the market."""
    for tf in TIMEFRAMES:
        k, _, h2 = barriers_for(tf)
        assert h2 >= k * k, (
            f"{tf}: holding {h2} bars for a {k} ATR target needs ~{k*k:.0f}")
    return True


def test_the_short_horizon_is_genuinely_shorter():
    """On an ATR timeframe the slots are two horizons. On a structure
    timeframe they are two SIDES on one horizon, so the holds are equal."""
    from core import geometry_for

    for tf in TIMEFRAMES:
        _, h1, h2 = barriers_for(tf)
        if geometry_for(tf) == "structure":
            assert h1 == h2 >= 8, (tf, h1, h2)
        else:
            assert 1 <= h1 < h2, (tf, h1, h2)
    return True


def test_day_trading_holds_are_hours_not_days_on_fast_timeframes():
    """15m and 1h should land in the hours — that is what day trading is.
    1m and 5m are gone as timeframes: at chance on every coin, and needing
    p > 0.6 to cover fees on a 1% span."""
    from core import TIMEFRAMES

    assert "1m" not in TIMEFRAMES and "5m" not in TIMEFRAMES
    for tf in ("15m", "1h"):
        _, _, h2 = barriers_for(tf)
        hours = interval_seconds(tf) * h2 / 3600
        assert 1 <= hours <= 8, f"{tf} holds {hours:.1f}h"
    return True


def test_daily_holds_ten_days_because_two_measured_at_chance():
    """The window curve: 0.498 at 2 days, 0.508 at 5, 0.519 at 10; a pooled
    ten-day model scored 0.530 on a held-out year against controls at 0.50.
    Shortening this would put a model at chance back on the daily card."""
    k, h1, h2 = barriers_for("1d")
    assert (k, h1, h2) == (1.0, 5, 10), (k, h1, h2)
    return True


def test_one_hour_keeps_the_geometry_its_frozen_models_were_fitted_with():
    """Changing k or the hold under the existing 1h models would make the
    monitor draw a window the model was never asked about."""
    k, h1, h2 = barriers_for("1h")
    assert (k, h1, h2) == (1.0, 1, 2), (k, h1, h2)
    return True


def test_the_monitor_reads_horizons_from_the_models_not_from_constants():
    """The window it draws must match the question the model was asked."""
    from pathlib import Path as _P

    from monitor import Monitor

    h1, h2 = model_paths("BTCUSDT", "1h")
    if not (h1.exists() and h2.exists()):
        return True
    mon = Monitor("BTCUSDT", "1h", h1, h2)
    assert mon.hold1 == mon.h1.cfg.max_hold_bars, mon.hold1
    assert mon.hold2 == mon.h2.cfg.max_hold_bars, mon.hold2
    return True
