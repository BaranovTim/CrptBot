"""The weekly momentum rotation (api/momentum.py) and its weekly alert."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from api.momentum import MIN_COINS, PICKS, ranking, rotation, week_start


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


def test_the_picks_are_the_best_and_worst_thirty_day_returns_at_monday():
    closes = _closes()
    m = rotation(closes, pd.Timestamp("2026-09-24 10:00", tz="UTC"))
    assert m["available"] and m["universe"] == 10
    assert [r["symbol"] for r in m["longs"]] == ["C9USDT", "C8USDT", "C7USDT"]
    assert [r["symbol"] for r in m["shorts"]] == ["C0USDT", "C1USDT", "C2USDT"]
    # 30 days of 0.4% a day for the best, measured to Sunday's close
    assert abs(m["longs"][0]["ret_30d"] - (np.exp(0.004 * 30) - 1) * 100) < 1e-6
    assert len(m["longs"]) == len(m["shorts"]) == PICKS
    assert m["next_rebalance"].startswith("2026-09-28")
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
