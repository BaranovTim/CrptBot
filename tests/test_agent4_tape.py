"""Tape mechanics — starting with the flag everyone inverts.

``is_buyer_maker`` is the single most commonly flipped field in crypto quant
work, and flipping it inverts the sign of every order-flow feature in the
project. Nothing else breaks: magnitudes stay plausible, no exception is
raised, and the model dutifully learns that aggressive selling precedes
rallies. These tests pin the convention against hand-built cases where the
right answer is known by construction.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent4 import Agent4Config, FlowAgent
from agent4.tape import large_print_threshold_bucket
from marketdata.aggtrades import (BUY_CNT_COLS, BUY_COLS, N_BUCKETS,
                                  SELL_COLS, aggregate_tape)
from tests.synthetic import make_bars
from tests.synthetic_tape import make_raw_prints, make_tape


ONE_BAR = pd.DatetimeIndex(["2024-05-01 12:59:59.999"], tz="UTC")


def _prints(flags, qtys, price=100.0, ts=1714567000000):
    return pd.DataFrame({"price": [price] * len(flags), "quantity": qtys,
                         "transact_time": [ts] * len(flags),
                         "is_buyer_maker": flags})


def test_is_buyer_maker_convention():
    """m=True is an aggressive SELL. m=False is an aggressive BUY."""
    t = aggregate_tape(_prints([False, True, False], [1.0, 2.0, 5.0]), ONE_BAR).iloc[0]
    assert t.buy_notional == 600.0, (
        f"aggressive buy volume is {t.buy_notional}, expected 600 — "
        f"is_buyer_maker is inverted"
    )
    assert t.sell_notional == 200.0, f"sell volume {t.sell_notional}, expected 200"
    assert t.buy_prints == 2 and t.sell_prints == 1, (
        f"print counts {t.buy_prints}/{t.sell_prints}, expected 2 buys / 1 sell")
    assert t.max_print_notional == 500.0
    return True


def test_all_maker_buys_are_all_sells():
    """Every print with m=True must land entirely on the sell side."""
    t = aggregate_tape(_prints([True] * 5, [1.0] * 5), ONE_BAR).iloc[0]
    assert t.buy_notional == 0.0 and t.sell_notional == 500.0, (
        f"all-maker-buy tape gave buy={t.buy_notional} sell={t.sell_notional}, "
        f"expected buy=0 sell=500 — is_buyer_maker is inverted")
    assert t.buy_prints == 0 and t.sell_prints == 5, (
        f"print counts {t.buy_prints}/{t.sell_prints}, expected 0 buys / 5 sells")
    return True


def test_histogram_sums_match_side_totals():
    """The size histogram must account for every dollar, on both sides."""
    bars = make_bars(30)
    t = aggregate_tape(make_raw_prints(bars, per_bar=300, seed=2), bars.index)
    assert np.allclose(t[BUY_COLS].sum(axis=1), t["buy_notional"], rtol=1e-9), \
        "buy histogram does not sum to buy notional"
    assert np.allclose(t[SELL_COLS].sum(axis=1), t["sell_notional"], rtol=1e-9), \
        "sell histogram does not sum to sell notional"
    return True


def test_buy_bias_flows_through_to_features():
    """A tape that is 80% aggressive buying must read as positive imbalance."""
    bars = make_bars(400)
    cfg = Agent4Config()
    agent = FlowAgent(cfg)

    bullish = agent.compute(bars, tape=make_tape(bars, buy_bias=0.8, seed=21))
    bearish = agent.compute(bars, tape=make_tape(bars, buy_bias=0.2, seed=21))

    b_up = bullish["aggressor_imbalance"].mean()
    b_dn = bearish["aggressor_imbalance"].mean()
    assert b_up > 0.2, f"buy-dominated tape gave imbalance {b_up:+.3f}"
    assert b_dn < -0.2, f"sell-dominated tape gave imbalance {b_dn:+.3f}"
    return True


def test_large_print_threshold_is_causal():
    """The threshold for bar t must use only bars strictly before t."""
    bars = make_bars(500)
    tape = make_tape(bars, seed=7)
    cfg = Agent4Config()

    full = large_print_threshold_bucket(tape, cfg)
    for t in (300, 380, 450):
        truncated = large_print_threshold_bucket(tape.iloc[:t], cfg)
        a, b = truncated.iloc[-1], full.iloc[t - 1]
        assert (pd.isna(a) and pd.isna(b)) or a == b, (
            f"threshold at bar {t} changed when later bars were added: {a} vs {b}"
        )
    return True


def test_large_print_threshold_excludes_own_bar():
    """A burst of large prints must not raise the bar it is measured against.

    If the threshold window included the current bar, an unusual burst would
    lift its own threshold and could classify itself as ordinary — quietly
    hiding exactly the events the feature exists to find.

    The spike goes into the COUNT histogram, because the threshold is a
    percentile of the print-size distribution by count. Perturbing notional
    alone would leave the threshold untouched and the test would pass no
    matter what the code did.
    """
    bars = make_bars(400)
    tape = make_tape(bars, seed=9).copy()
    cfg = Agent4Config()
    before = large_print_threshold_bucket(tape, cfg)

    hit = 250
    big_bucket = BUY_CNT_COLS[-4]
    tape.iloc[hit, tape.columns.get_loc(big_bucket)] += 20_000
    after = large_print_threshold_bucket(tape, cfg)

    assert before.iloc[hit] == after.iloc[hit], (
        f"the burst changed its own bar's threshold "
        f"({before.iloc[hit]} -> {after.iloc[hit]}) — the window is not shifted"
    )
    assert (before.iloc[:hit].fillna(-1) == after.iloc[:hit].fillna(-1)).all(), \
        "the burst changed thresholds for earlier bars"
    # And it must be visible afterwards, or the test proves nothing.
    later = slice(hit + 1, hit + 60)
    assert not (before.iloc[later].fillna(-1) == after.iloc[later].fillna(-1)).all(), (
        "the burst never moved any later threshold — the spike was too small "
        "for this test to demonstrate anything"
    )
    return True


def test_whale_prints_are_detected():
    """A deliberately planted whale must show up in the large-print columns."""
    bars = make_bars(500)
    tape = make_tape(bars, whale_rate=0.0, seed=13).copy()
    agent = FlowAgent()

    quiet = agent.compute(bars, tape=tape)
    hit = 450
    tape.iloc[hit, tape.columns.get_loc(BUY_COLS[-2])] += 2e8
    tape.iloc[hit, tape.columns.get_loc(BUY_CNT_COLS[-2])] += 1
    tape.iloc[hit, tape.columns.get_loc("buy_notional")] += 2e8
    tape.iloc[hit, tape.columns.get_loc("max_print_notional")] = 2e8
    loud = agent.compute(bars, tape=tape)

    q = quiet["large_print_volume_share"].iloc[hit]
    l = loud["large_print_volume_share"].iloc[hit]
    assert pd.isna(q) or l > q, f"whale print did not raise the share ({q} -> {l})"
    assert loud["large_print_imbalance_1h"].iloc[hit] > 0, \
        "a large BUY print produced a non-positive imbalance"
    return True


def test_threshold_defined_on_heavy_tails():
    """A fat tail must not make the threshold undefined.

    Discrete buckets mean the top bucket can hold more than the target share
    by itself. The selection rounds toward capturing slightly more, so a
    bucket is always found once trailing volume exists.
    """
    bars = make_bars(400)
    tape = make_tape(bars, whale_rate=0.15, seed=41)   # deliberately extreme
    cfg = Agent4Config()
    idx = large_print_threshold_bucket(tape, cfg)
    # min_periods on the rolling window, plus one for the shift(1).
    warm = max(24, cfg.print_threshold_bars // 8) + 1
    settled = idx.iloc[warm:]
    assert (settled < N_BUCKETS).all(), (
        f"threshold undefined on {(settled >= N_BUCKETS).sum()} bars with a heavy tail"
    )

    agent = FlowAgent(cfg)
    f = agent.compute(bars, tape=tape)
    assert f["large_print_imbalance_1h"].notna().sum() > 100, \
        "large-print features vanished on a heavy-tailed tape"
    # And they must actually discriminate: a threshold that sweeps in almost
    # all volume fires constantly and separates nothing.
    share = f["large_print_volume_share"].iloc[warm:].dropna()
    assert share.median() < 0.75, (
        f"large prints capture {share.median():.0%} of bar volume — the "
        f"threshold is too loose to discriminate"
    )
    assert share.std() > 0.05, "large_print_volume_share has almost no variance"
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} tape tests passed.")
