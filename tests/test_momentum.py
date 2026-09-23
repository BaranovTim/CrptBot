"""The weekly momentum rotation (api/momentum.py) and its weekly alert."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from api.momentum import (LOOKBACK_DAYS, MIN_COINS, PICKS, ranking, rotation,
                          universe, week_start)


def _closes(n_coins=10, days=60, end="2026-09-27", growth=None):
    """Daily closes (bar close 23:59:59.999) for coins that grow at known rates."""
    idx = pd.date_range(end=pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=59, seconds=59.999),
                        periods=days, freq="1D", tz="UTC")
    growth = growth or {f"C{i}USDT": 0.001 * (i - n_coins // 2) for i in range(n_coins)}
    return {s: pd.Series(100.0 * np.exp(g * np.arange(days)), index=idx) for s, g in growth.items()}


def test_the_week_starts_on_monday_at_midnight_utc():
    assert week_start(pd.Timestamp("2026-09-23 15:00", tz="UTC")) == pd.Timestamp("2026-09-21", tz="UTC")
    assert week_start(pd.Timestamp("2026-09-21 00:00", tz="UTC")) == pd.Timestamp("2026-09-21", tz="UTC")
    assert week_start(pd.Timestamp("2026-09-20 23:59", tz="UTC")) == pd.Timestamp("2026-09-14", tz="UTC")
    return True


def test_the_picks_are_the_best_and_worst_returns_at_monday():
    closes = _closes(n_coins=12, days=90)
    m = rotation(closes, pd.Timestamp("2026-09-24 10:00", tz="UTC"))
    assert m["available"] and m["universe"] == 12 and m["picks"] == PICKS == 5
    assert [r["symbol"] for r in m["longs"]] == ["C11USDT", "C10USDT", "C9USDT", "C8USDT", "C7USDT"]
    assert [r["symbol"] for r in m["shorts"]] == ["C0USDT", "C1USDT", "C2USDT", "C3USDT", "C4USDT"]
    # LOOKBACK_DAYS of 0.5% a day for the best, measured to Sunday's close
    want = (np.exp(0.005 * LOOKBACK_DAYS) - 1) * 100
    assert abs(m["longs"][0]["ret_pct"] - want) < 1e-6
    assert m["longs"][0]["ret_30d"] == m["longs"][0]["ret_pct"]      # the old name, for old apps
    assert m["next_rebalance"].startswith("2026-09-28")
    assert "honest" in m["measured"] and m["sharpe"]["holdout"] < 1.0
    return True


def test_the_universe_is_the_most_traded_with_enough_history():
    closes = _closes(n_coins=12, days=90)
    at = pd.Timestamp("2026-09-21", tz="UTC")
    vol = {s: pd.Series(float(i + 1), index=c.index) for i, (s, c) in enumerate(closes.items())}
    top = universe(closes, vol, at, n=10)
    assert top == [f"C{i}USDT" for i in range(11, 1, -1)]       # most volume first, C0/C1 out
    # a coin listed three weeks ago is not ranked, however much it trades
    young = closes["C5USDT"][closes["C5USDT"].index > at - pd.Timedelta(days=21)]
    closes["NEWUSDT"] = young; vol["NEWUSDT"] = pd.Series(1e9, index=young.index)
    assert "NEWUSDT" not in universe(closes, vol, at, n=10)
    # stablecoins are never ranked
    closes["USDCUSDT"] = closes["C3USDT"] * 0 + 1.0; vol["USDCUSDT"] = vol["C11USDT"] * 100
    assert "USDCUSDT" not in universe(closes, vol, at, n=10)
    m = rotation(closes, pd.Timestamp("2026-09-24", tz="UTC"), volumes=vol)
    assert m["universe"] == 12 and "most-traded" in m["universe_rule"]      # all 12 eligible, up to 30
    return True


def test_this_week_is_measured_from_mondays_close():
    closes = _closes()
    now = pd.Timestamp("2026-09-24 10:00", tz="UTC")
    prices = {s: float(c[c.index <= pd.Timestamp("2026-09-20 23:59:59.999", tz="UTC")].iloc[-1]) * 1.10
              for s, c in closes.items()}
    m = rotation(closes, now, prices)
    assert all(abs(r["week_pct"] - 10.0) < 1e-6 for r in m["longs"] + m["shorts"])
    assert abs(m["week_pct"]) < 1e-6          # long and short both up 10%: flat, as a pair should be
    return True


def test_a_stale_coin_is_left_out_and_too_few_coins_make_no_picks():
    closes = _closes()
    stale = closes["C9USDT"]
    closes["C9USDT"] = stale[stale.index < pd.Timestamp("2026-09-10", tz="UTC")]
    r = ranking(closes, pd.Timestamp("2026-09-21", tz="UTC"))
    assert "C9USDT" not in [x["symbol"] for x in r]
    few = _closes(n_coins=MIN_COINS - 1)
    m = rotation(few, pd.Timestamp("2026-09-24", tz="UTC"))
    assert not m["available"] and m["longs"] == [] and m["shorts"] == []
    return True


def test_the_weekly_alert_is_sent_once_per_week():
    from tests.test_alerts import StubService, _engine

    svc = StubService()
    closes = _closes()
    now = {"t": pd.Timestamp("2026-09-24 10:00", tz="UTC")}
    svc.momentum = lambda: rotation(closes, now["t"])
    e = _engine(svc)                         # the priming refresh sends nothing
    assert [a for a in e.refresh() if a.kind == "momentum"] == []
    now["t"] = pd.Timestamp("2026-09-29 01:00", tz="UTC")
    closes.update(_closes(end="2026-09-28"))
    got = [a for a in e.refresh() if a.kind == "momentum"]
    assert len(got) == 1 and "week of 2026-09-28" in got[0].title, [a.title for a in got]
    assert got[0].body.startswith("Long: C9 ") and "Short: C0 " in got[0].body
    assert [a for a in e.refresh() if a.kind == "momentum"] == []
    return True


def test_net_taker_buying_moves_a_coin_up_the_combined_rank():
    """Same prices for every coin, so momentum ties; the week's net taker
    buying then decides. Without taker data the rank is momentum alone."""
    closes = _closes(n_coins=12, days=90, growth={f"C{i}USDT": 0.002 for i in range(12)})
    vol = {s: pd.Series(1e6, index=c.index) for s, c in closes.items()}
    # C0 had the most aggressive buying all month, C11 the most selling
    tk = {s: pd.Series(1e6 * (0.35 + 0.3 * (11 - i) / 11), index=c.index)
          for i, (s, c) in enumerate(closes.items())}
    m = rotation(closes, pd.Timestamp("2026-09-24", tz="UTC"), volumes=vol, takers=tk)
    assert m["longs"][0]["symbol"] == "C0USDT" and m["shorts"][0]["symbol"] == "C11USDT"
    assert "net taker buying" in m["signal"] and m["flow_days"] == 7
    assert m["sharpe"]["holdout"] > m["sharpe"]["old_rule_honest"]
    plain = rotation(closes, pd.Timestamp("2026-09-24", tz="UTC"), volumes=vol)
    assert plain["signal"] == "15-day momentum" and plain["flow_days"] is None
    return True
