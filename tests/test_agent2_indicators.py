"""Indicator math correctness.

A leak-free indicator that computes the wrong number is still wrong, and the
lookahead test cannot see that — it only checks that a value does not change
when future bars appear. These check the values themselves, against known
closed-form results and against slow reference implementations.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent2 import indicators as ind
from tests.synthetic import make_bars

BARS = make_bars(600, seed=21)


def test_safe_div_never_returns_inf():
    """An unguarded division gives inf on a flat window, and inf wrecks a split."""
    out = ind.safe_div(pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 0.0, 1e-18]))
    assert not np.isinf(out.to_numpy()).any()
    assert pd.isna(out.iloc[1]) and pd.isna(out.iloc[2])
    return True


def test_smoothers_preserve_constants():
    c = pd.Series([50.0] * 100)
    for fn, n in ((ind.sma, 20), (ind.ema, 20), (ind.rma, 14)):
        v = fn(c, n).dropna()
        assert np.allclose(v, 50.0), f"{fn.__name__} distorted a constant series"
    return True


def test_rma_recursion():
    """Wilder smoothing must satisfy y[i] = (1-1/n)*y[i-1] + (1/n)*x[i]."""
    n = 14
    x = pd.Series(np.random.default_rng(0).normal(10, 2, 300))
    y = ind.rma(x, n)
    lhs = y.iloc[n + 5:].to_numpy()
    rhs = ((1 - 1 / n) * y.shift(1) + (1 / n) * x).iloc[n + 5:].to_numpy()
    assert np.allclose(lhs, rhs), "rma is not Wilder's recursion"
    return True


def test_atr_of_constant_range():
    """Bars with a fixed true range of 10 must give ATR exactly 10."""
    n = 40
    close = pd.Series([100.0] * n)
    high = close + 5.0
    low = close - 5.0
    a = ind.atr(high, low, close, 14).dropna()
    assert np.allclose(a, 10.0), f"expected ATR 10, got {a.iloc[-1]}"
    return True


def test_rsi_saturates():
    """Unbroken up moves give RSI 100; unbroken down moves give 0."""
    up = pd.Series(np.arange(100, 200, dtype=float))
    down = pd.Series(np.arange(200, 100, -1, dtype=float))
    assert np.isclose(ind.rsi(up, 14).iloc[-1], 100.0)
    assert np.isclose(ind.rsi(down, 14).iloc[-1], 0.0)
    return True


def test_rsi_bounded():
    v = ind.rsi(BARS["close"], 14).dropna()
    assert v.between(0, 100).all(), f"RSI escaped [0,100]: {v.min()}..{v.max()}"
    return True


def test_rsi_matches_slow_reference():
    """Vectorised RSI must equal an explicit-loop implementation."""
    c = BARS["close"].to_numpy(float)
    n = 14
    delta = np.diff(c, prepend=np.nan)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain[0] = loss[0] = np.nan

    ag = np.full(len(c), np.nan)
    al = np.full(len(c), np.nan)
    ag[1], al[1] = gain[1], loss[1]
    for i in range(2, len(c)):
        ag[i] = (1 - 1 / n) * ag[i - 1] + (1 / n) * gain[i]
        al[i] = (1 - 1 / n) * al[i - 1] + (1 / n) * loss[i]
    with np.errstate(divide="ignore", invalid="ignore"):
        ref = 100 - 100 / (1 + ag / al)

    got = ind.rsi(BARS["close"], n).to_numpy()
    both = ~np.isnan(ref) & ~np.isnan(got)
    both[:n + 2] = False                       # skip min_periods warmup
    assert both.sum() > 400
    assert np.allclose(got[both], ref[both], atol=1e-9), "RSI differs from reference"
    return True


def test_macd_of_constant_is_zero():
    c = pd.Series([42.0] * 200)
    line, sig, hist = ind.macd(c)
    assert np.allclose(line.dropna(), 0.0)
    assert np.allclose(hist.dropna(), 0.0)
    return True


def test_stochastic_endpoints():
    """%K is 100 at the window high and 0 at the window low."""
    close = pd.Series(np.arange(1, 61, dtype=float))     # always the new high
    high = close + 0.0
    low = close - 0.0
    k, _ = ind.stochastic(high, low, close, 14, 3)
    assert np.isclose(k.dropna().iloc[-1], 100.0)

    close_dn = pd.Series(np.arange(60, 0, -1, dtype=float))
    k2, _ = ind.stochastic(close_dn, close_dn, close_dn, 14, 3)
    assert np.isclose(k2.dropna().iloc[-1], 0.0)
    return True


def test_cmf_endpoints():
    """Closing at the high every bar is maximum accumulation: CMF = +1."""
    n = 60
    low = pd.Series([100.0] * n)
    high = pd.Series([110.0] * n)
    close = high.copy()
    vol = pd.Series([5.0] * n)
    assert np.allclose(ind.cmf(high, low, close, vol, 20).dropna(), 1.0)
    assert np.allclose(ind.cmf(high, low, low, vol, 20).dropna(), -1.0)
    return True


def test_mfi_bounded():
    v = ind.mfi(BARS["high"], BARS["low"], BARS["close"], BARS["volume"], 14).dropna()
    assert v.between(0, 100).all(), f"MFI escaped [0,100]: {v.min()}..{v.max()}"
    return True


def test_adx_bounded():
    a, p, m = ind.adx(BARS["high"], BARS["low"], BARS["close"], 14)
    assert a.dropna().between(0, 100).all()
    assert p.dropna().ge(0).all() and m.dropna().ge(0).all()
    return True


def test_rolling_z_guards_zero_variance():
    """A flat window has no standard deviation: NaN, not inf."""
    z = ind.rolling_z(pd.Series([7.0] * 60), 20)
    assert not np.isinf(z.to_numpy()).any()
    assert z.dropna().empty or np.allclose(z.dropna(), 0.0)
    return True


def test_bars_since_sign_change():
    s = pd.Series([1.0, 2.0, 3.0, -1.0, -2.0, -3.0, -4.0, 5.0])
    since, direction = ind.bars_since_sign_change(s)
    assert since.iloc[3] == 0 and direction.iloc[3] == -1.0   # crossed down here
    assert since.iloc[6] == 3 and direction.iloc[6] == -1.0   # 3 bars later
    assert since.iloc[7] == 0 and direction.iloc[7] == 1.0    # crossed up
    return True


def test_obv_direction():
    close = pd.Series([1.0, 2.0, 3.0, 2.0])
    vol = pd.Series([10.0, 10.0, 10.0, 10.0])
    o = ind.obv(close, vol)
    assert list(o) == [0.0, 10.0, 20.0, 10.0]
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} indicator tests passed.")
