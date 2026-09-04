"""Long-horizon base rates — counts, and how little they support.

WHAT THESE GUARD

  OVERLAPPING WINDOWS COUNTED AS EVIDENCE   Stepping one bar at a time gives
        thousands of windows that share all but one day. Treating those as
        independent is how five years of history becomes a confident-looking
        "2,074 observations" and a model nobody should believe.

  A CONFIDENCE INTERVAL THAT HIDES THE PROBLEM   The normal approximation at
        n=6 produces bounds outside [0,1] and a width that understates the
        uncertainty — in exactly the regime this module exists to be honest
        about. Wilson does not.

  DRAWDOWN COMPUTED FROM CLOSES   The low along the way is what decides
        whether a position survives to see the outcome. Using closes would
        report a gentler path than the one that actually happened.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent5.longhorizon import outcomes, summarise, wilson


def _bars(closes, lows=None):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({
        "open": closes, "high": closes,
        "low": lows if lows is not None else closes,
        "close": closes, "volume": [1.0] * len(closes),
    }, index=idx)


def test_windows_do_not_overlap():
    """365 days of history is ONE yearly observation, not 365."""
    bars = _bars(list(range(1, 731)))            # two years of daily bars
    got = outcomes(bars, 365)
    assert len(got) == 1, len(got)

    # and a month steps a month at a time
    monthly = outcomes(bars, 30)
    assert len(monthly) == 730 // 30, len(monthly)
    # consecutive windows start a full horizon apart
    starts = [g["at"] for g in monthly]
    assert len(set(starts)) == len(starts)
    return True


def test_the_interval_is_wilson_and_survives_a_tiny_sample():
    """At n=6 the normal approximation gives bounds outside [0,1]."""
    lo, hi = wilson(4, 6)
    assert 0.0 <= lo <= hi <= 1.0, (lo, hi)
    # and it is genuinely wide — this is the point
    assert hi - lo > 0.4, (lo, hi)

    # a large sample tightens it
    lo2, hi2 = wilson(400, 600)
    assert hi2 - lo2 < 0.1, (lo2, hi2)

    # the degenerate cases do not throw or escape the unit interval
    assert wilson(0, 3)[0] == 0.0
    assert wilson(3, 3)[1] == 1.0
    assert wilson(0, 0) is None
    return True


def test_a_thin_sample_is_marked_as_such():
    """Six observations and eighty-one must not present identically."""
    # Long enough that the monthly horizon clears MIN_MEANINGFUL while the
    # yearly one cannot — which is the real shape of the problem: the same
    # history is ample at one horizon and nearly empty at another.
    bars = _bars(list(range(1, 1500)))
    rows = {r["horizon"]: r for r in summarise(bars)}
    year = rows["1 year"]
    month = rows["1 month"]
    assert year["n"] < 30 and not year["meaningful"], year
    assert month["n"] >= 30 and month["meaningful"], month
    # and the note says the count rather than hedging vaguely
    assert str(year["n"]) in year["note"]
    return True


def test_drawdown_uses_the_low_not_the_close():
    """A position is stopped out by the low it touched, not by where the day
    happened to close."""
    closes = [100.0] * 31
    lows = [100.0] * 31
    lows[15] = 60.0                       # a 40% intramonth plunge, recovered
    got = outcomes(_bars(closes, lows), 30)
    assert len(got) == 1
    assert abs(got[0]["return_pct"]) < 1e-9, got
    assert abs(got[0]["drawdown_pct"] - (-40.0)) < 1e-9, got
    return True


def test_no_history_says_so_rather_than_reporting_zero():
    bars = _bars([100.0] * 10)
    rows = {r["horizon"]: r for r in summarise(bars)}
    assert rows["1 year"]["n"] == 0
    assert "no history" in rows["1 year"]["note"]
    # a zero up-rate would read as "never went up", which is a claim
    assert "up_rate" not in rows["1 year"]
    return True


def test_a_flat_series_is_not_reported_as_rising():
    """Strictly greater than zero: unchanged is not up."""
    bars = _bars([100.0] * 200)
    rows = {r["horizon"]: r for r in summarise(bars)}
    assert rows["1 month"]["up_rate"] == 0.0
    return True
