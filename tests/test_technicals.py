"""Price-derived screener fields.

WHAT THESE GUARD

  THE ZERO THAT SHOULD BE BLANK   A stock with 40 bars has no SMA200.
                                  Reporting that as 0.0 puts it above every
                                  price in the market, so it passes "price
                                  above SMA200" and leads every breakout
                                  screen with a company that just listed.

  TODAY IN ITS OWN AVERAGE        If relative volume divides today by an
                                  average that includes today, a genuine 5x
                                  day reports about 4.2x — and the error grows
                                  with the spike, so it is worst exactly when
                                  the number matters.

  BETA ON MISALIGNED DATES        A stock halted for a day, compared position
                                  by position against the index, has every
                                  later return matched to the wrong session.
                                  The result is a beta near zero for a stock
                                  that simply missed a day.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.technicals import technicals


def _bars(closes, volumes=None):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes if volumes is not None else [1e6] * len(closes),
    }, index=idx)


def test_a_short_history_leaves_the_long_averages_blank_not_zero():
    m = technicals(_bars(list(range(1, 41))))       # 40 bars
    assert m["sma20"] is not None
    assert m["sma50"] is None, m["sma50"]
    assert m["sma200"] is None, m["sma200"]
    # and crucially the comparison is blank too, not a default True
    assert m["above_sma50"] is None
    assert m["above_sma200"] is None
    return True


def test_price_above_and_below_its_average_read_correctly():
    rising = _bars([10.0] * 30 + [20.0] * 5)
    assert technicals(rising)["above_sma20"] == 1.0
    falling = _bars([20.0] * 30 + [10.0] * 5)
    assert technicals(falling)["above_sma20"] == 0.0
    return True


def test_todays_volume_is_excluded_from_its_own_average():
    # 60 quiet sessions then one five-times day
    vols = [1e6] * 60 + [5e6]
    m = technicals(_bars([10.0] * 61, vols), avg_volume_days=50)
    assert m["avg_volume"] == 1e6, m["avg_volume"]
    assert m["rel_volume"] == 5.0, m["rel_volume"]
    assert m["current_volume"] == 5e6
    return True


def test_a_new_high_means_the_latest_bar_set_it():
    at_high = _bars(list(range(1, 61)))                 # monotonically up
    assert technicals(at_high)["at_50d_high"] == 1.0
    # near the high but not at it is not a new high
    off_high = _bars(list(range(1, 61)) + [30.0])
    assert technicals(off_high)["at_50d_high"] == 0.0
    return True


def test_rsi_matches_the_definition_the_models_use():
    """One definition, imported — not a second one written for the screener.
    They disagree by several points near 30, which is where the oversold
    preset makes its decision."""
    from agent2.indicators import rsi

    closes = list(np.cumsum(np.random.default_rng(7).normal(0, 1, 200)) + 100)
    bars = _bars(closes)
    theirs = float(rsi(bars["close"], 14).iloc[-1])
    assert abs(technicals(bars)["rsi14"] - theirs) < 1e-9
    return True


def test_an_unbroken_rally_is_rsi_100_not_a_blank():
    m = technicals(_bars([float(i) for i in range(1, 40)]))
    assert m["rsi14"] == 100.0, m["rsi14"]
    return True


def test_beta_is_aligned_on_dates_and_not_on_position():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=300, freq="D", tz="UTC")
    bench = pd.Series(np.cumsum(rng.normal(0, 1, 300)) + 500, index=idx)

    # A stock whose RETURNS are twice the index's has beta 2.
    #
    # Built by compounding doubled returns, not by doubling absolute point
    # moves: beta is a ratio of returns, so a stock at $100 moving the same
    # number of dollars as an index at 500 already has a beta of 5. Getting
    # that wrong in a fixture produces a confident, meaningless number.
    bret = bench.pct_change().fillna(0.0)
    stock = 100.0 * (1.0 + 2.0 * bret).cumprod()
    bars = pd.DataFrame({"close": stock, "open": stock, "high": stock,
                         "low": stock, "volume": [1e6] * 300}, index=idx)
    b = technicals(bars, benchmark=bench)["beta"]
    assert b is not None and 1.6 < b < 2.4, b

    # now drop a session from the stock. Position-wise comparison would
    # scramble every later return; date alignment keeps the answer.
    holed = bars.drop(bars.index[100])
    b2 = technicals(holed, benchmark=bench)["beta"]
    assert b2 is not None and 1.4 < b2 < 2.6, b2
    return True


def test_empty_or_broken_input_returns_blanks_rather_than_throwing():
    """A screener over 13,000 names meets bad rows. One must not stop the run."""
    for bad in (None, pd.DataFrame(), pd.DataFrame({"close": []})):
        m = technicals(bad)
        assert m["price"] is None
        assert m["rsi14"] is None
    return True
