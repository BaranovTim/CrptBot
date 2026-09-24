"""The live record (api/ledger.py), the notifications for a fill and for a
trade's end, and the seed-bagged model (agent5.model.SeedBag)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from api.ledger import COST_PCT, OrderLedger, pool_installed_at


def _ledger():
    return OrderLedger(Path(tempfile.mkdtemp()) / "orders.json")


def _row(placed, state, long=True, fill=None, ret=None, closed=None, **kw):
    r = {"placed_at": pd.Timestamp(placed, tz="UTC"), "state": state, "long": long,
         "limit": 99.0, "stop": 94.0, "target": 104.0, "rank": 0.98, "fill": fill,
         "filled_at": None, "exit": None,
         "closed_at": pd.Timestamp(closed, tz="UTC") if closed else None, "ret_pct": ret}
    r.update(kw)
    return r


# ------------------------------------------------------------- the ledger
def test_the_ledger_counts_trades_not_orders():
    L = _ledger()
    book = {"strong": [_row("2026-09-23 00:00", "target", fill=99.0, ret=5.05, closed="2026-09-23 12:00"),
                       _row("2026-09-23 04:00", "stop", fill=99.0, ret=-5.05, closed="2026-09-23 16:00"),
                       _row("2026-09-23 08:00", "expired"),
                       _row("2026-09-23 12:00", "replaced"),
                       _row("2026-09-23 16:00", "open")]}
    assert L.record("BTCUSDT", "4h", book, pool="crypto-4h-20260922T191908Z") == 5
    s = L.summary()["levels"]["strong"]
    assert s["closed"] == 2 and s["targets"] == 1 and s["stops"] == 1
    assert s["expired"] == 1 and s["open_orders"] == 1
    assert s["orders"] == 4                    # the replaced one is the same order, moved
    assert s["win_rate"] == 0.5
    assert abs(s["avg_net_pct"] - (-COST_PCT)) < 1e-9     # +5.05 and -5.05, less the fee each
    assert L.summary()["levels"]["medium"]["closed"] == 0
    return True


def test_a_finished_order_is_never_rewritten_and_the_file_survives_a_restart():
    L = _ledger()
    L.record("ETHUSDT", "4h", {"strong": [_row("2026-09-23 00:00", "filled", fill=99.0)]})
    L.record("ETHUSDT", "4h", {"strong": [_row("2026-09-23 00:00", "target", fill=99.0, ret=5.0,
                                               closed="2026-09-23 12:00")]})
    # a later replay whose window has lost the order's start sees it differently
    assert L.record("ETHUSDT", "4h", {"strong": [_row("2026-09-23 00:00", "open")]}) == 0
    again = OrderLedger(L.path)
    rows = again.rows()
    assert len(rows) == 1 and rows[0]["state"] == "target" and rows[0]["ret_pct"] == 5.0
    assert again.since is not None
    return True


def test_orders_placed_before_the_model_was_installed_are_left_out():
    L = _ledger()
    pool = "crypto-4h-20260922T191908Z"
    book = {"strong": [_row("2026-09-22 16:00", "target", fill=99.0, ret=5.0, closed="2026-09-22 20:00"),
                       _row("2026-09-22 20:00", "open")]}
    L.record("SOLUSDT", "4h", book, pool=pool, not_before=pool_installed_at(pool))
    assert [r["placed_at"][:16] for r in L.rows()] == ["2026-09-22T20:00"]
    assert pool_installed_at("not-a-pool") is None
    return True


def test_the_recent_trades_are_kept_per_level():
    L = _ledger()
    t = _row("2026-09-23 00:00", "target", fill=99.0, ret=5.0, closed="2026-09-23 12:00")
    L.record("BTCUSDT", "4h", {"strong": [t], "medium": [t], "small": [t]})
    rec = L.summary()["recent"]
    assert [len(rec[k]) for k in ("strong", "medium", "small")] == [1, 1, 1]
    assert abs(rec["strong"][0]["net_pct"] - (5.0 - COST_PCT)) < 1e-9
    return True


# ------------------------------------- the monitor hands over every level
def test_the_card_carries_every_levels_orders():
    from tests.test_orders import _card
    from tests.test_structure import _bars

    bars = _bars()
    sc = np.full(len(bars), 0.5); sc[-1] = 0.99
    a, bars = _card(sc, bars)
    assert set(a.order_book) == {"strong", "medium", "small"}
    live = a.order_book["strong"][-1]
    assert live["state"] == "open" and live["long"] and live["ret_pct"] is None
    assert live["placed_at"] == a.order_info["placed_at"]
    assert abs(live["limit"] - a.order_info["limit"]) < 1e-12
    return True


# ------------------------------------------------- fill and end alerts
def _order_engine():
    from tests.test_alerts import StubService, _engine

    svc = StubService(action="BUY", strength="strong")
    base = svc.dashboard
    state = {"order": {"state": "open", "limit": 99.0, "stop": 94.0, "target": 104.0,
                       "placed_at": "2026-09-22T12:00:00+00:00",
                       "valid_until": "2026-09-23T12:00:00+00:00",
                       "hold_until": "2026-09-25T04:00:00+00:00", "level": "strong"}}

    def dash(symbol=None, interval=None):
        d = base(symbol, interval)
        d["recommendation"]["action"] = svc.action
        d["recommendation"]["order"] = dict(state["order"])
        return d

    svc.dashboard = dash
    return svc, state, _engine(svc)


def test_a_fill_and_the_trades_end_are_announced_once_each():
    svc, state, e = _order_engine()
    assert e.refresh() == []
    svc.action = "WAIT"
    state["order"].update(state="filled", fill=99.0)
    got = e.refresh()
    assert len(got) == 1 and "order filled" in got[0].title, [a.title for a in got]
    assert "Filled at 99.00" in got[0].body and "Take profit 104.00" in got[0].body
    assert got[0].strength == "strong"
    assert e.refresh() == []                                   # still filled: silence
    svc.action = "FLAT"
    state["order"].update(state="target", exit=104.0)
    end = e.refresh()
    assert len(end) == 1 and "target reached" in end[0].title, [a.title for a in end]
    assert "+5.05% from the fill" in end[0].body
    assert e.refresh() == []
    return True


def test_a_fill_and_stop_in_one_bar_is_one_notice():
    svc, state, e = _order_engine()
    e.refresh()
    svc.action = "FLAT"
    state["order"].update(state="stop", fill=99.0, exit=94.0)
    got = e.refresh()
    assert len(got) == 1 and "stopped out" in got[0].title
    assert "-5.05% from the fill" in got[0].body
    return True


def test_the_order_state_survives_a_restart():
    from api.alerts import AlertEngine

    svc, state, e = _order_engine()
    e.refresh()
    fresh = AlertEngine(svc, state_path=e._state_path)
    fresh._calendar = lambda: []
    svc.action = "WAIT"
    state["order"].update(state="filled", fill=99.0)
    got = fresh.refresh()
    assert any("order filled" in a.title for a in got), [a.title for a in got]
    return True


# ------------------------------------------------------------ seed bagging
def test_a_bagged_model_averages_its_members_and_round_trips():
    from agent5 import Agent5Config, JudgeAgent
    from agent5.model import SeedBag, fit_final
    from agent5.dataset import Dataset

    rng = np.random.default_rng(0)
    n = 1500
    X = pd.DataFrame(rng.normal(size=(n, 5)), columns=list("abcde"))
    y = pd.Series((X["a"] + 0.5 * rng.normal(size=n) > 0).astype(float))
    ds = Dataset(X=X, y=y, weight=pd.Series(np.ones(n)), t1=np.arange(n) + 2.0,
                 positions=np.arange(n), blocks={}, index=pd.date_range("2024", periods=n, freq="4h"))
    cfg = Agent5Config(bag_seeds=(7, 11, 13))
    model, cols = fit_final(ds, cfg, list("abcde"))
    assert isinstance(model, SeedBag) and len(model) == 3
    p = model.predict_proba(X)[:, 1]
    each = np.mean([m.predict_proba(X)[:, 1] for m in model.members], axis=0)
    assert np.allclose(p, each)
    single, _ = fit_final(ds, Agent5Config(), list("abcde"))
    assert not isinstance(single, SeedBag)
    j = JudgeAgent(cfg); j.model, j.columns, j._fitted = model, cols, True
    from agent5.calibration import fit_calibrator
    j.calibrator = fit_calibrator(p, y.to_numpy(), np.ones(n))
    path = Path(tempfile.mkdtemp()) / "bag.joblib"
    j.save(path)
    back = JudgeAgent.load(path)
    assert np.allclose(back.raw_scores(X), j.raw_scores(X))
    assert back.cfg.bag_seeds == (7, 11, 13)
    return True


def test_an_old_config_has_no_bag():
    from agent5.config import Agent5Config
    cfg = Agent5Config()
    assert tuple(cfg.bag_seeds) == ()
    try:
        Agent5Config(bag_seeds=(7, 7))
    except ValueError:
        return True
    raise AssertionError("duplicate seeds were accepted")


def test_identical_model_files_are_loaded_once():
    """A pooled model sits under every coin's name; each Monitor used to hold
    its own copy. One object per distinct content, and a changed file is a
    new object."""
    import shutil
    from monitor import load_shared_judge
    from agent5 import Agent5Config, JudgeAgent
    from agent5.calibration import fit_calibrator
    from agent5.model import fit_final
    from agent5.dataset import Dataset

    rng = np.random.default_rng(1)
    n = 800
    X = pd.DataFrame(rng.normal(size=(n, 3)), columns=list("abc"))
    y = pd.Series((X["a"] > 0).astype(float))
    ds = Dataset(X=X, y=y, weight=pd.Series(np.ones(n)), t1=np.arange(n) + 2.0,
                 positions=np.arange(n), blocks={}, index=pd.date_range("2024", periods=n, freq="4h"))
    d = Path(tempfile.mkdtemp())
    j = JudgeAgent(Agent5Config()); j.model, j.columns = fit_final(ds, j.cfg, list("abc")); j._fitted = True
    j.calibrator = fit_calibrator(j.model.predict_proba(X)[:, 1], y.to_numpy(), np.ones(n))
    j.save(d / "judge_AAAUSDT_4h_h1.joblib")
    shutil.copyfile(d / "judge_AAAUSDT_4h_h1.joblib", d / "judge_BBBUSDT_4h_h1.joblib")
    a = load_shared_judge(d / "judge_AAAUSDT_4h_h1.joblib")
    b = load_shared_judge(d / "judge_BBBUSDT_4h_h1.joblib")
    assert a is b
    j2 = JudgeAgent(Agent5Config(bag_seeds=(3, 5))); j2.model, j2.columns = fit_final(ds, j2.cfg, list("abc"))
    j2._fitted = True; j2.calibrator = j.calibrator
    j2.save(d / "judge_BBBUSDT_4h_h1.joblib")                   # retrained: new content
    c = load_shared_judge(d / "judge_BBBUSDT_4h_h1.joblib")
    assert c is not a and c.cfg.bag_seeds == (3, 5)
    return True


def test_the_range_since_an_entry_reads_only_its_months():
    """The OOM loop of 2026-09-23: the app's "high and low since my entry"
    parsed the coin's whole 1m history, concurrently for every open trade.
    Same answer, from only the months since the entry."""
    import livefeed
    from livefeed import BarStore
    from api.service import TradingService

    root = Path(tempfile.mkdtemp())
    real = livefeed.BarStore

    class TmpStore(BarStore):
        def __init__(self, symbol, interval, directory=root):
            super().__init__(symbol, interval, directory)

    idx = pd.date_range("2026-07-01", "2026-09-10", freq="1h", tz="UTC") + pd.Timedelta(minutes=59, seconds=59.999)
    rng = np.random.default_rng(3)
    c = 100 + rng.normal(size=len(idx)).cumsum()
    bars = pd.DataFrame({"open_time": idx - pd.Timedelta(minutes=59, seconds=59.999), "open": c, "high": c + 1,
                         "low": c - 1, "close": c, "volume": 1.0, "quote_volume": 1.0,
                         "number_of_trades": 1, "taker_buy_base_volume": 0.5,
                         "taker_buy_quote_volume": 0.5}, index=pd.DatetimeIndex(idx, name="close_time"))
    d = root / "TESTUSDT" / "1m"; d.mkdir(parents=True)
    for per, g in bars.groupby(bars.index.strftime("%Y-%m")):
        g.to_csv(d / f"{per}.csv")
    livefeed.BarStore = TmpStore
    try:
        svc = object.__new__(TradingService)
        svc._bars_cache, svc._lock = {}, __import__("threading").Lock()
        svc._pair = lambda s, i: (s, i)
        svc._bars = lambda s, i: TmpStore(s, i).load()
        since = "2026-08-20T10:30:00Z"
        got = svc.price_range("TESTUSDT", "1m", since)
        part = svc._range_bars("TESTUSDT", "1m", since)
        full = TmpStore("TESTUSDT", "1m").load()
        want = full[full.index > pd.Timestamp(since)]
        assert got["high"] == float(want["high"].max()) and got["low"] == float(want["low"].min())
        assert got["last"] == float(want["close"].iloc[-1]) and got["bars"] == len(want)
        assert part.index.min() >= pd.Timestamp("2026-08-01", tz="UTC")      # July never read
        assert list(part.columns) == ["high", "low", "close"]
    finally:
        livefeed.BarStore = real
    return True


def test_the_score_book_stores_nanoseconds_whatever_the_index_unit():
    """pandas 3 (the server) parses times at microsecond resolution and
    `asi8` returns that unit: every live reading was stored 1000x too small,
    trimmed, and invisible to the rank. Record from a microsecond index,
    load a file holding both units, and rank must see every reading."""
    import json
    from monitor import ScoreBook, POOL_MIN_COINS

    path = Path(tempfile.mkdtemp()) / "sb.json"
    b = ScoreBook(path)
    idx = pd.date_range("2026-06-01", periods=200, freq="4h", tz="UTC") + pd.Timedelta(hours=4) - pd.Timedelta(milliseconds=1)
    for i in range(POOL_MIN_COINS):
        b.record("P|long", f"C{i}", idx.as_unit("us"), np.linspace(0, 1, len(idx)))
    t = b._pools["P|long"]["C0"][0]
    assert (t > 10**17).all() and t.max() == idx[-1].as_unit("ns").value
    r, n, coins = b.rank("P|long", idx[-1] + pd.Timedelta(hours=4), 0.5, pd.Timedelta(days=90))
    assert coins == POOL_MIN_COINS and n > 50 and 0.4 < r < 0.6
    # a file already holding microsecond entries is repaired on load
    raw = json.loads(path.read_text())
    raw["pools"]["P|long"]["C0"]["t"] = [x // 1000 for x in raw["pools"]["P|long"]["C0"]["t"]]
    path.write_text(json.dumps(raw))
    again = ScoreBook(path); again._load()
    assert (again._pools["P|long"]["C0"][0] > 10**17).all()
    return True


def test_screened_coins_are_served_without_touching_the_fifteen():
    """--add-coins installs the pooled models under a new coin's name, with a
    verdict saying it is served, not trained on; a training coin is refused."""
    import json, shutil
    import train_pooled_4h as T
    from core import model_paths, eval_path

    out = Path(tempfile.mkdtemp())
    src1, src2 = model_paths("BTCUSDT", "4h", out)
    src1.parent.mkdir(parents=True, exist_ok=True)
    src1.write_bytes(b"h1"); src2.write_bytes(b"h2")
    eval_path("BTCUSDT", "4h", out).write_text(json.dumps({"symbol": "BTCUSDT", "rank_pool": "P"}))
    real_mp, real_ep = T.model_paths, T.eval_path
    T.model_paths = lambda s, i: model_paths(s, i, out)
    T.eval_path = lambda s, i: eval_path(s, i, out)
    try:
        assert T.add_coins(["CRVUSDT", "ETHUSDT"]) == 1          # ETH trains; left alone
    finally:
        T.model_paths, T.eval_path = real_mp, real_ep
    n1, n2 = model_paths("CRVUSDT", "4h", out)
    assert n1.read_bytes() == b"h1" and n2.read_bytes() == b"h2"
    v = json.loads(eval_path("CRVUSDT", "4h", out).read_text())
    assert v["symbol"] == "CRVUSDT" and v["served_not_trained"] and v["rank_pool"] == "P"
    assert not model_paths("ETHUSDT", "4h", out)[0].exists()
    assert len(T.SERVED_EXTRA) == 10 and not set(T.SERVED_EXTRA) & set(T.COINS)
    return True


def test_the_pair_list_flags_and_leads_with_the_coins_that_get_calls():
    from api.service import TradingService, _Cached
    import time as _t
    svc = object.__new__(TradingService)
    rows = [{"symbol": s, "base": s[:-4], "quote": "USDT", "volume_24h": v}
            for s, v in (("BTCUSDT", 9e9), ("AAAUSDT", 5e9), ("CRVUSDT", 1e8), ("ZZZUSDT", 5e7))]
    svc._tickers = {"__universe__": _Cached(_t.time(), rows)}
    svc.record_symbols = lambda: ["BTCUSDT", "CRVUSDT"]
    plain = svc.symbols()
    assert [r["symbol"] for r in plain] == ["BTCUSDT", "AAAUSDT", "CRVUSDT", "ZZZUSDT"]   # volume order kept
    assert [r["served"] for r in plain] == [True, False, True, False]
    first = svc.symbols(served_first=True)
    assert [r["symbol"] for r in first] == ["BTCUSDT", "CRVUSDT", "AAAUSDT", "ZZZUSDT"]
    return True
