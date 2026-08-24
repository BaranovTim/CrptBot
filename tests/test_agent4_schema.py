"""Agent 4's output contract, and how it degrades when feeds are missing.

The degradation tests carry most of the weight here. Agent 4's inputs are
genuinely optional — the tape may not be downloaded, open interest may not be
published, netflow is a paid feed most people will never buy — so "missing"
is the normal case, not an edge case. Every one of those must produce NaN
plus an honest coverage number, never a confident zero.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent4 import Agent4Config, FlowAgent
from agent4.schema import (
    BOUNDED_COLUMNS,
    COVERAGE_COLUMNS,
    DIRECTIONAL_COLUMNS,
    FEATURE_COLUMNS,
    validate_features,
)
from tests.synthetic import make_bars
from tests.synthetic_tape import (
    make_liquidations,
    make_netflow,
    make_open_interest,
    make_tape,
)

BARS = make_bars(1000)
TAPE = make_tape(BARS, per_bar=150, seed=23)
OI = make_open_interest(BARS)
LIQ = make_liquidations(BARS)
NET = make_netflow(BARS)
AGENT = FlowAgent()
FULL = AGENT.compute(BARS, tape=TAPE, open_interest=OI, liquidations=LIQ, netflow=NET)


def test_shape_and_order():
    assert tuple(FULL.columns) == FEATURE_COLUMNS
    assert FULL.index.equals(BARS.index)
    validate_features(FULL)
    return True


def test_no_infinities():
    assert not np.isinf(FULL.to_numpy(dtype=float)).any()
    return True


def test_bounded_columns():
    for col, (lo, hi) in BOUNDED_COLUMNS.items():
        v = FULL[col].dropna()
        if v.empty:
            continue
        assert v.between(lo, hi).all(), (
            f"{col} escaped [{lo}, {hi}]: {v.min():.4f}..{v.max():.4f}"
        )
    return True


def test_no_sentinel_values():
    for c in FULL.columns:
        assert not (FULL[c].dropna() == -999).any(), f"{c} uses a -999 sentinel"
    return True


def test_coverage_columns_never_nan():
    """Coverage is an observed fact — we always know what we looked at."""
    for c in COVERAGE_COLUMNS:
        assert FULL[c].notna().all(), f"{c} must never be NaN"
    return True


def test_missing_tape_is_nan_not_zero():
    """No tape must read as 'cannot see', not as 'no whales were active'."""
    out = AGENT.compute(BARS, open_interest=OI)
    assert (out["tape_coverage_24h"] == 0).all(), "coverage should report zero"
    for col in ("aggressor_imbalance", "ofi_z", "large_print_imbalance_1h",
                "large_print_volume_share", "top_print_share", "trade_count_z"):
        assert out[col].isna().all(), (
            f"{col} produced a value with no tape — a zero here would mean "
            f"'flow was balanced', which is not what we observed"
        )
    # The kline fallback still works — that is the point of the fallback.
    assert out["taker_buy_ratio"].notna().any(), "kline order flow was lost too"
    return True


def test_missing_netflow_is_nan_with_zero_coverage():
    out = AGENT.compute(BARS, tape=TAPE)
    assert out["exchange_netflow_z"].isna().all()
    assert out["exchange_netflow_direction"].isna().all()
    assert (out["netflow_coverage_24h"] == 0).all()
    return True


def test_missing_open_interest_is_nan():
    out = AGENT.compute(BARS, tape=TAPE)
    for c in ("oi_change_pct", "oi_change_z", "oi_price_divergence"):
        assert out[c].isna().all(), f"{c} produced a value with no OI feed"
    return True


def test_missing_liquidations_is_nan():
    """Binance restricts the history, so this is the common case."""
    out = AGENT.compute(BARS, tape=TAPE, open_interest=OI)
    assert out["liq_imbalance_1h"].isna().all()
    assert out["bars_since_liq_spike"].isna().all()
    return True


def test_everything_missing_is_survivable():
    out = AGENT.compute(BARS)
    assert tuple(out.columns) == FEATURE_COLUMNS
    assert len(out) == len(BARS)
    for c in COVERAGE_COLUMNS:
        assert (out[c] == 0).all()
    return True


def test_sign_convention_positive_is_buy_pressure():
    """Buy-dominated tape and short liquidations must read positive."""
    bars = make_bars(600)
    agent = FlowAgent()
    up = agent.compute(bars, tape=make_tape(bars, buy_bias=0.85, seed=31))
    dn = agent.compute(bars, tape=make_tape(bars, buy_bias=0.15, seed=31))

    for col in ("aggressor_imbalance", "large_print_imbalance_1h"):
        mu_up, mu_dn = up[col].mean(), dn[col].mean()
        assert mu_up > 0, f"{col} is {mu_up:+.3f} on a buy-dominated tape"
        assert mu_dn < 0, f"{col} is {mu_dn:+.3f} on a sell-dominated tape"

    # Shorts being force-closed is forced buying -> positive.
    liq = pd.DataFrame({"long_liquidated": np.zeros(len(bars)),
                        "short_liquidated": np.full(len(bars), 1e6)}, index=bars.index)
    out = agent.compute(bars, liquidations=liq)
    assert (out["liq_imbalance_1h"].dropna() > 0).all(), \
        "short liquidations produced a negative imbalance"

    # Inflow to exchanges is supply arriving to be sold -> bearish.
    inflow = pd.DataFrame({"netflow_usd": np.full(len(bars), 5e6)}, index=bars.index)
    out = agent.compute(bars, netflow=inflow)
    assert (out["exchange_netflow_direction"].dropna() < 0).all(), \
        "exchange inflow read as bullish — the netflow sign is inverted"
    return True


def test_directional_set_is_covered():
    assert len(DIRECTIONAL_COLUMNS) >= 3
    assert "ofi_z" not in DIRECTIONAL_COLUMNS, \
        "a self-centring z-score should not be in the directional sign test"
    return True


def test_latest_matches_compute():
    out = AGENT.latest(BARS, tape=TAPE, open_interest=OI, liquidations=LIQ, netflow=NET)
    row = FULL.iloc[-1]
    assert out.timestamp == FULL.index[-1]
    for c in FEATURE_COLUMNS:
        a, b = out.features[c], row[c]
        assert (pd.isna(a) and pd.isna(b)) or np.isclose(a, b), f"{c} differs"
    return True


def test_features_are_populated():
    tail = FULL.iloc[AGENT.warmup_bars:]
    empty = [c for c in tail.columns if tail[c].notna().sum() == 0]
    assert not empty, f"columns never produced a value on 1000 bars: {empty}"
    return True


def test_short_windows_do_not_crash():
    """Small config windows must work, not raise.

    pandas rejects min_periods > window, and the derived min_periods here is
    a max() of a constant and a fraction of the window — so any window below
    that constant crashed. Hidden by the defaults, hit immediately by anyone
    experimenting with shorter ones.
    """
    bars = make_bars(200)
    tape = make_tape(bars, per_bar=60, seed=5)
    for n in (4, 8, 12, 19, 20, 40):
        cfg = Agent4Config(flow_z_window=n, print_threshold_bars=n,
                           large_print_z_window=n, oi_z_window=n,
                           divergence_window=min(n, 10))
        out = FlowAgent(cfg).compute(bars, tape=tape, open_interest=make_open_interest(bars))
        assert tuple(out.columns) == FEATURE_COLUMNS, f"window {n} broke the schema"
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} schema tests passed.")
