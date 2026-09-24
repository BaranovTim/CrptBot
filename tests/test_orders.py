"""The resting entry: the order a pooled 4h call now enters with.

research/improve_4h.py found that entering with a resting order 0.5 ATR
better than the close -- the stop moved with it, the target left on its
level -- doubled the profit per trade on the walk-forward, and that the
realistic way to trade it is one order resting at the newest call's price,
one position at a time (`monitor.resting_orders`). These tests pin that
state machine, the card built on it, the forming-bar update, and the
notifications that tell a person to move or cancel the order.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from agent5.config import Agent5Config
from agent5.labels import _atr
from monitor import RestingOrder, evaluate, resting_orders
from tests.test_structure import HOUR, RankedJudge, _X, _bars, _book, _fill


def _order(long=True, **kw):
    base = dict(placed=0, long=long, limit=99.0, stop=95.0, target=104.0,
                valid_to=4, hold_to=16, level=3)
    base.update(kw)
    return RestingOrder(**base)


# ------------------------------------------------------------ the order
def test_an_order_fills_at_its_price_or_at_a_gap_open():
    od = _order()
    od.step(1, 100.0, 101.0, 99.5, 100.5)          # did not reach 99
    assert od.state == "open"
    od.step(2, 100.0, 100.2, 98.5, 99.8)           # traded through 99
    assert od.state == "filled" and od.fill == 99.0 and od.filled_at == 2
    gap = _order()
    gap.step(1, 98.0, 98.5, 97.5, 98.2)            # opened below the order
    assert gap.state == "filled" and gap.fill == 98.0
    return True


def test_the_fill_bar_counts_the_stop_and_not_the_target():
    """OHLC cannot say whether the high came before the dip, so the kinder
    reading -- target reached after the fill -- is refused in the fill bar."""
    od = _order()
    od.step(1, 100.0, 105.0, 98.9, 104.5)          # dipped to fill, high past the target
    assert od.state == "filled"
    od.step(2, 104.0, 104.5, 103.0, 104.2)         # the next bar reaches it
    assert od.state == "target" and od.exit == 104.0
    stopped = _order()
    stopped.step(1, 100.0, 100.5, 94.0, 95.5)      # filled and stopped in one bar
    assert stopped.state == "stop" and stopped.exit == 95.0 and stopped.fill == 99.0
    return True


def test_a_bar_touching_both_is_a_stop_and_the_window_closes_the_trade():
    od = _order(); od.step(1, 100, 100, 98.9, 99.5)
    od.step(2, 99, 104.5, 94.5, 100)                # both levels in one bar
    assert od.state == "stop"
    tout = _order(hold_to=3); tout.step(1, 100, 100, 98.9, 99.5)
    tout.step(2, 100, 101, 99.5, 100.5)
    tout.step(3, 100, 101, 99.5, 100.7)
    assert tout.state == "timeout" and tout.exit == 100.7
    assert abs(tout.ret_pct() - (100.7 / 99.0 - 1) * 100) < 1e-9
    return True


def test_an_unfilled_order_expires_but_the_forming_bar_never_expires_it():
    od = _order(valid_to=2)
    od.step(1, 100, 101, 99.5, 100.5)
    od.step(2, 100, 101, 99.5, 100.5, closed=False)   # forming: cannot expire it
    assert od.state == "open"
    od.step(2, 100, 101, 99.5, 100.5)                  # its close: it can
    assert od.state == "expired"
    return True


def test_a_short_order_is_the_mirror():
    od = _order(long=False, limit=101.0, stop=105.0, target=96.0)
    od.step(1, 100.0, 101.5, 99.5, 101.2)
    assert od.state == "filled" and od.fill == 101.0
    od.step(2, 100.0, 100.5, 95.5, 96.5)
    assert od.state == "target" and od.ret_pct() > 0
    return True


# ----------------------------------------------------------- the policy
def _arrays(n=12, price=100.0):
    c = np.full(n, price); o = c.copy(); h = c + 0.5; lo = c - 0.5
    atr = np.full(n, 2.0); tp = np.full(n, 4.0); sl = np.full(n, 5.0)
    return o, h, lo, c, atr, tp, sl


def test_a_newer_call_replaces_an_unfilled_order_and_a_trade_blocks_new_calls():
    o, h, lo, c, atr, tp, sl = _arrays()
    level = np.zeros(12, int); level[[1, 2]] = 3
    orders = resting_orders(o, h, lo, c, atr, tp, sl, level, True, 0.5, 4, 16, min_level=3)
    assert [x.placed for x in orders] == [1, 2]
    assert orders[0].state == "replaced" and orders[1].state in ("open", "expired")
    # the limit sits 0.5 ATR under the close, the stop moved by the same
    assert orders[1].limit == 99.0 and abs(orders[1].stop - (95.0 - 1.0)) < 1e-9
    assert orders[1].target == 104.0
    # a dip fills bar 3's order; the call at bar 4 is ignored while it runs
    lo2 = lo.copy(); lo2[3] = 98.5
    level2 = np.zeros(12, int); level2[[2, 4]] = 3
    orders = resting_orders(o, h, lo2, c, atr, tp, sl, level2, True, 0.5, 4, 16, min_level=3)
    assert len(orders) == 1 and orders[0].state == "filled"
    return True


def test_each_sensitivity_sees_only_its_own_calls():
    """A person on "strong" never saw the small call, so its order must not
    block the strong one that came after it."""
    o, h, lo, c, atr, tp, sl = _arrays()
    lo = lo.copy(); lo[2] = 98.5                     # the small order fills here
    level = np.zeros(12, int); level[1] = 1; level[3] = 3
    strong = resting_orders(o, h, lo, c, atr, tp, sl, level, True, 0.5, 4, 16, min_level=3)
    small = resting_orders(o, h, lo, c, atr, tp, sl, level, True, 0.5, 4, 16, min_level=1)
    assert [x.placed for x in strong] == [3]
    assert [x.placed for x in small] == [1] and small[0].state == "filled"
    return True


# ------------------------------------------------------------- the card
def _pooled_cfg(side="long"):
    return Agent5Config(max_hold_bars=16, k_up=1.0, k_dn=1.0, geometry="structure",
                        side=side, stop_buffer_atr=0.5, rank_pool="P",
                        entry_offset_atr=0.5, entry_valid_bars=4)


def _card(scores, bars=None, side="long"):
    import monitor

    bars = _bars() if bars is None else bars
    X = _X(bars)
    last = bars.index[-1]
    real = monitor.SCOREBOOK
    monitor.SCOREBOOK = _book()
    try:
        _fill(monitor.SCOREBOOK, f"P|{side}", [f"C{i}" for i in range(9)], last + HOUR)
        a = evaluate(RankedJudge(side, scores, cfg=_pooled_cfg(side)), bars, X, "A",
                     last, last + 16 * HOUR, 16, symbol="MINE")
    finally:
        monitor.SCOREBOOK = real
    return a, bars


def _calm(bars, k):
    """The last k bars drift gently UP, never dipping more than 0.1 ATR
    below the previous close -- so a long order 0.5 ATR under stays open."""
    b = bars.copy()
    atr = _atr(b, 14).to_numpy(float)
    c = float(b["close"].iloc[-k - 1])
    for j in range(len(b) - k, len(b)):
        step = 0.02 * atr[j]
        b.iloc[j, b.columns.get_loc("open")] = c
        b.iloc[j, b.columns.get_loc("low")] = c - 0.1 * atr[j]
        c = c + step
        b.iloc[j, b.columns.get_loc("close")] = c
        b.iloc[j, b.columns.get_loc("high")] = c + 0.1 * atr[j]
    return b


def test_a_fresh_call_enters_with_a_resting_order():
    bars = _bars()
    sc = np.full(len(bars), 0.5); sc[-1] = 0.99
    a, bars = _card(sc, bars)
    info = a.order_info
    atr = _atr(bars, 14).to_numpy(float)[-1]
    close = float(bars["close"].iloc[-1])
    assert a.action == "ENTER LONG NOW" and a.strength == "strong", a.reason
    assert info["state"] == "open" and abs(info["limit"] - (close - 0.5 * atr)) < 1e-9
    assert a.entry == info["limit"] and a.tp_price == info["target"] and a.sl_price == info["stop"]
    assert info["stop"] < info["limit"] < close < info["target"]
    assert "limit order" in a.reason and "good until" in a.reason
    assert a.ends_at == info["valid_until"] == info["placed_at"] + 4 * HOUR
    assert a.size_pct == 5.0
    return True


def test_an_order_stays_on_the_card_after_its_call_has_gone():
    """The call was two closes ago; the order is good for four."""
    bars = _calm(_bars(), 2)
    sc = np.full(len(bars), 0.5); sc[-3] = 0.99
    a, _ = _card(sc, bars)
    assert a.action == "ENTER LONG NOW", a.reason
    assert a.order_info["placed_at"] == bars.index[-3]
    assert f"{bars.index[-3]:%H:%M} UTC" in a.reason
    return True


def test_a_filled_order_is_a_trade_not_a_new_call():
    bars = _calm(_bars(), 2)
    sc = np.full(len(bars), 0.5); sc[-3] = 0.99
    atr = _atr(bars, 14).to_numpy(float)
    limit = float(bars["close"].iloc[-3]) - 0.5 * atr[-3]
    b = bars.copy()
    b.iloc[-2, b.columns.get_loc("low")] = limit - 0.01 * atr[-2]    # the dip that fills it
    a, _ = _card(sc, b)
    assert a.action == "WAIT" and a.order_info["state"] == "filled", a.reason
    assert "In the trade" in a.reason and a.entry == a.order_info["fill"]
    return True


def test_an_order_that_expired_at_this_close_says_so():
    bars = _calm(_bars(), 4)
    sc = np.full(len(bars), 0.5); sc[-5] = 0.99
    a, _ = _card(sc, bars)
    assert a.action == "WAIT" and a.order_info.get("state") == "expired", a.reason
    assert a.reason.startswith("The buy order from the") and "cancel the limit" in a.reason
    return True


def test_a_model_without_the_entry_rule_is_untouched():
    import monitor

    bars = _bars()
    sc = np.full(len(bars), 0.5); sc[-1] = 0.99
    cfg = Agent5Config(max_hold_bars=16, geometry="structure", side="long",
                       stop_buffer_atr=0.5, rank_pool="P")
    real = monitor.SCOREBOOK
    monitor.SCOREBOOK = _book()
    try:
        _fill(monitor.SCOREBOOK, "P|long", [f"C{i}" for i in range(9)], bars.index[-1] + HOUR)
        a = evaluate(RankedJudge("long", sc, cfg=cfg), bars, _X(bars), "A",
                     bars.index[-1], bars.index[-1] + 16 * HOUR, 16, symbol="MINE")
    finally:
        monitor.SCOREBOOK = real
    assert a.action == "ENTER LONG NOW" and not a.order_info and a.entry == float(bars["close"].iloc[-1])
    return True


# ---------------------------------------------------------- the service
def test_the_recommendation_carries_the_order_and_a_trade_is_wait():
    from api.service import _recommendation

    bars = _bars()
    sc = np.full(len(bars), 0.5); sc[-1] = 0.99
    a, _ = _card(sc, bars)
    rec = _recommendation(a, a, stale=False)
    assert rec["action"] == "BUY" and rec["order"]["state"] == "open"
    assert rec["order"]["limit"] == a.order_info["limit"] and rec["ev"] is None
    a.order_info["state"] = "filled"; a.order_info["fill"] = a.order_info["limit"]
    rec = _recommendation(a, a, stale=False)
    assert rec["action"] == "WAIT" and rec["order"]["state"] == "filled"
    return True


def test_a_trade_outranks_a_fresh_call_on_the_other_side():
    from api.service import _choose
    from tests.test_structure import _call

    trade = _call("LONG", "strong"); trade.action = "WAIT"; trade.order_info = {"state": "filled"}
    fresh = _call("SHORT", "strong")
    assert _choose(fresh, trade) is trade and _choose(trade, fresh) is trade
    return True


def test_the_forming_bar_fills_an_order_or_leaves_it_alone():
    from api.service import _recommendation, _settle_call

    bars = _bars()
    sc = np.full(len(bars), 0.5); sc[-1] = 0.99
    a, _ = _card(sc, bars)
    rec = _recommendation(a, a, stale=False)
    lim, tgt = a.order_info["limit"], a.order_info["target"]
    # the target touched while the order is unfilled changes nothing
    same, what = _settle_call(rec, a, {"price": tgt, "high": tgt * 1.001, "low": lim * 1.01})
    assert same is rec and what == ""
    # the price trading through the limit fills it
    got, what = _settle_call(rec, a, {"price": lim * 0.999, "high": lim * 1.01, "low": lim * 0.998})
    assert what == "" and got["action"] == "WAIT" and got["order"]["state"] == "filled"
    assert "In the trade" in got["detail"]
    return True


# ------------------------------------------------------- the notifications
def test_a_moved_order_and_an_expired_one_are_announced():
    from tests.test_alerts import StubService, _engine

    svc = StubService(action="BUY", strength="strong")
    order = {"state": "open", "limit": 99.0, "stop": 94.0, "target": 104.0,
             "placed_at": "2026-09-22T12:00:00+00:00", "valid_until": "2026-09-23T04:00:00+00:00"}
    base = svc.dashboard

    def with_order(o):
        def dash(symbol=None, interval=None):
            d = base(symbol, interval)
            d["recommendation"]["action"] = svc.action
            if o is not None:
                d["recommendation"]["order"] = dict(o)
            return d
        return dash

    svc.dashboard = with_order(order)
    e = _engine(svc)
    assert e.refresh() == []                                   # the same order: silence
    svc._bar = "2026-08-28T16:59:59.999000+00:00"
    svc.dashboard = with_order(dict(order, placed_at="2026-09-22T16:00:00+00:00", limit=100.5))
    moved = e.refresh()
    assert len(moved) == 1 and "order moved" in moved[0].title, [a.title for a in moved]
    assert "Move the buy limit to 100.50" in moved[0].body
    svc.action = "FLAT"; svc._bar = "2026-08-28T20:59:59.999000+00:00"
    svc.dashboard = with_order(dict(order, state="expired", limit=100.5))
    gone = e.refresh()
    assert len(gone) == 1 and "order expired" in gone[0].title and "Cancel it" in gone[0].body
    return True


def test_the_entry_alert_names_the_order():
    from tests.test_alerts import StubService, _engine

    svc = StubService(action="FLAT")
    e = _engine(svc)
    base = svc.dashboard

    def dash(symbol=None, interval=None):
        d = base(symbol, interval)
        d["recommendation"]["order"] = {"state": "open", "limit": 99.0,
                                        "valid_until": "2026-09-23T04:00:00+00:00"}
        return d

    svc.dashboard = dash
    svc.action = "BUY"; svc._bar = "2026-08-28T16:59:59.999000+00:00"
    got = e.refresh()
    assert len(got) == 1 and "Buy limit 99.00, good until Wed 04:00 UTC" in got[0].body, got[0].body
    return True


def test_calls_from_before_the_model_was_installed_are_not_replayed():
    """After an install, the window reaches back over bars the model was
    fitted on; their back-filled readings must not become trades."""
    import monitor

    bars = _calm(_bars(), 2)
    sc = np.full(len(bars), 0.5); sc[-3] = 0.99            # a call two closes ago
    X = _X(bars); last = bars.index[-1]
    call_at = bars.index[-3]

    def card(pool):
        cfg = Agent5Config(max_hold_bars=16, k_up=1.0, k_dn=1.0, geometry="structure",
                           side="long", stop_buffer_atr=0.5, rank_pool=pool,
                           entry_offset_atr=0.5, entry_valid_bars=4)
        real = monitor.SCOREBOOK
        monitor.SCOREBOOK = _book()
        try:
            _fill(monitor.SCOREBOOK, f"{pool}|long", [f"C{i}" for i in range(9)], last + HOUR)
            return evaluate(RankedJudge("long", sc, cfg=cfg), bars, X, "A", last,
                            last + 16 * HOUR, 16, symbol="MINE")
        finally:
            monitor.SCOREBOOK = real

    before = (call_at - HOUR).strftime("crypto-4h-%Y%m%dT%H%M%SZ")    # installed before the call
    after = (call_at + HOUR).strftime("crypto-4h-%Y%m%dT%H%M%SZ")     # installed after it
    a = card(before)
    assert a.action == "ENTER LONG NOW" and a.order_info["state"] == "open", a.reason
    b = card(after)
    assert b.action != "ENTER LONG NOW" and not b.order_info, b.reason
    assert all(not rows for rows in b.order_book.values())
    assert monitor.pool_installed_at("P") is None
    return True


# ------------------------------------------------------------ the scale-out
def _scaled(long=True, **kw):
    base = dict(placed=0, long=long, limit=99.0, stop=95.0, target=107.0,
                valid_to=4, hold_to=16, level=3, scale_part=1 / 3, scale_at=0.5)
    base.update(kw)
    return RestingOrder(**base)


def test_halfway_takes_a_third_off_and_moves_the_stop_to_the_entry():
    od = _scaled()
    od.step(1, 100.0, 100.5, 98.5, 99.5)                 # fills at 99
    assert od.state == "filled" and od.fill == 99.0 and od.scale_price == 103.0
    od.step(2, 99.5, 103.2, 99.2, 103.0)                  # halfway (99 -> 107 is 103)
    assert od.taken and od.stop == 99.0 and od.state == "filled"
    od.step(3, 103.0, 103.1, 98.9, 99.0)                  # back to the entry
    assert od.state == "stop" and od.exit == 99.0
    # a third at +4.04%, the rest flat: a win
    assert abs(od.ret_pct() - (1 / 3) * (103 / 99 - 1) * 100) < 1e-9 and od.ret_pct() > 0.1
    return True


def test_the_rest_can_still_reach_the_target_and_a_stop_first_is_a_full_loss():
    od = _scaled()
    od.step(1, 100.0, 100.5, 98.5, 99.5)
    od.step(2, 99.5, 103.5, 99.5, 103.0)                  # halfway
    od.step(3, 103.0, 107.5, 102.0, 107.0)                # target
    want = (1 / 3) * (103 / 99 - 1) * 100 + (2 / 3) * (107 / 99 - 1) * 100
    assert od.state == "target" and abs(od.ret_pct() - want) < 1e-9
    lose = _scaled()
    lose.step(1, 100.0, 100.5, 98.5, 99.5)
    lose.step(2, 99.0, 102.0, 94.5, 95.0)                 # the stop before halfway
    assert lose.state == "stop" and not lose.taken
    assert abs(lose.ret_pct() - (95 / 99 - 1) * 100) < 1e-9
    # halfway and the target in one bar: the trade ends at the target, scaled
    one = _scaled()
    one.step(1, 100.0, 100.5, 98.5, 99.5)
    one.step(2, 99.5, 108.0, 99.5, 107.5)
    assert one.state == "target" and one.taken
    # a short is the mirror
    sh = _scaled(long=False, limit=101.0, stop=105.0, target=93.0)
    sh.step(1, 100.0, 101.5, 99.5, 100.5)                 # fills at 101
    sh.step(2, 100.5, 101.0, 96.8, 97.0)                  # halfway 101 -> 93 is 97
    assert sh.taken and sh.stop == 101.0
    return True


def test_the_card_names_the_halfway_price_and_the_notice_fires_once():
    import monitor
    from tests.test_alerts import StubService, _engine

    bars = _bars()
    sc = np.full(len(bars), 0.5); sc[-1] = 0.99
    X = _X(bars); last = bars.index[-1]
    cfg = Agent5Config(max_hold_bars=16, k_up=1.0, k_dn=1.0, geometry="structure",
                       side="long", stop_buffer_atr=0.5, rank_pool="P",
                       entry_offset_atr=0.5, entry_valid_bars=6,
                       scale_out_part=1 / 3, scale_out_at=0.5)
    real = monitor.SCOREBOOK
    monitor.SCOREBOOK = _book()
    try:
        _fill(monitor.SCOREBOOK, "P|long", [f"C{i}" for i in range(9)], last + HOUR)
        a = evaluate(RankedJudge("long", sc, cfg=cfg), bars, X, "A", last, last + 16 * HOUR, 16,
                     symbol="MINE")
    finally:
        monitor.SCOREBOOK = real
    info = a.order_info
    want = info["limit"] + 0.5 * (info["target"] - info["limit"])
    assert abs(info["scale_price"] - want) < 1e-9 and info["scale_part"] == 1 / 3
    assert "take a third off" in a.reason, a.reason
    assert "chance of reaching the target before the stop" in a.reason, a.reason

    svc = StubService(action="WAIT", strength="strong")
    base = svc.dashboard
    order = {"state": "filled", "limit": 99.0, "fill": 99.0, "stop": 95.0, "target": 107.0,
             "placed_at": "2026-09-23T12:00:00+00:00", "scale_part": 1 / 3, "scale_price": 103.0,
             "taken": False, "level": "strong"}
    state = {"o": dict(order)}

    def dash(symbol=None, interval=None):
        d = base(symbol, interval)
        d["recommendation"]["action"] = svc.action
        d["recommendation"]["order"] = dict(state["o"])
        return d
    svc.dashboard = dash
    e = _engine(svc)
    state["o"].update(taken=True, stop=99.0)
    got = e.refresh()
    assert len(got) == 1 and "halfway, take a third off" in got[0].title, [x.title for x in got]
    assert "Move the stop on the rest to your entry 99.00" in got[0].body
    assert e.refresh() == []
    svc.action = "FLAT"
    state["o"].update(state="stop", exit=99.0, ret_pct=1.35)
    end = e.refresh()
    assert len(end) == 1 and "+1.35% overall" in end[0].title and "came back to the entry" in end[0].body
    return True


def test_a_trade_back_at_the_entry_after_the_third_is_not_a_stop_in_the_record():
    from api.ledger import OrderLedger
    L = OrderLedger(Path(tempfile.mkdtemp()) / "o.json")
    row = {"placed_at": pd.Timestamp("2026-09-23 20:00", tz="UTC"), "state": "stop", "long": True,
           "limit": 99.0, "stop": 99.0, "target": 107.0, "fill": 99.0, "exit": 99.0,
           "closed_at": pd.Timestamp("2026-09-24 08:00", tz="UTC"), "ret_pct": 1.35, "taken": True}
    L.record("BTCUSDT", "4h", {"strong": [row]})
    st = L.summary()["levels"]["strong"]
    assert st["stops"] == 0 and st["back_to_entry"] == 1 and st["wins"] == 1
    try:
        Agent5Config(scale_out_part=0.33)
    except ValueError:
        return True
    raise AssertionError("a scale-out with no point was accepted")


def test_the_forming_bar_can_reach_halfway_between_closes():
    from types import SimpleNamespace
    from api.service import _settle_order

    od = _scaled()
    od.step(1, 100.0, 100.5, 98.5, 99.5)                  # filled at 99, halfway 103
    a = SimpleNamespace(order=od, order_info={"next": 2, "last_close": 100.0,
                                              "placed_at": pd.Timestamp("2026-09-23 12:00", tz="UTC")})
    rec = {"action": "WAIT", "order": {"state": "filled", "fill": 99.0, "taken": False}}
    out, reached = _settle_order(rec, a, {"high": 103.4, "low": 99.6, "price": 103.1})
    assert reached == "" and out["order"]["taken"] and out["order"]["stop"] == 99.0
    assert "take a third off at 103.00" in out["detail"], out["detail"]
    assert od.taken is False                                # the card's own order untouched
    return True


# ------------------------------------------------- ranked against its own
def test_the_own_rank_is_the_researchs_rank_coin():
    import research.wf4h as L
    from monitor import _own_ranks

    rng = np.random.default_rng(5)
    raw = rng.random(700)
    raw[[100, 350]] = raw[99]                       # ties count half in both
    pos = np.arange(700)
    want = L._trailing_rank(pos, raw, pos, raw)
    got = _own_ranks(raw)
    both = np.isfinite(want)
    assert np.array_equal(both, np.isfinite(got))
    assert np.allclose(got[both], want[both])
    return True


def test_a_coin_scoped_call_ignores_every_other_coin():
    """Ranked against its own readings, a coin's call is the same whatever
    the other coins read -- which is what lets the app add coins."""
    import monitor

    bars = _bars()
    sc = np.full(len(bars), 0.5); sc[-1] = 0.99
    X = _X(bars); last = bars.index[-1]
    cfg = Agent5Config(max_hold_bars=16, k_up=1.0, k_dn=1.0, geometry="structure",
                       side="long", stop_buffer_atr=0.5, rank_pool="P",
                       entry_offset_atr=0.5, entry_valid_bars=6, rank_scope="coin")

    def card(other_level):
        real = monitor.SCOREBOOK
        monitor.SCOREBOOK = _book()
        try:
            # nine other coins, reading everything from nothing to far above ours
            _fill(monitor.SCOREBOOK, "P|long", [f"C{i}" for i in range(9)], last + HOUR)
            for i in range(9):
                monitor.SCOREBOOK._pools["P|long"][f"C{i}"] = (
                    monitor.SCOREBOOK._pools["P|long"][f"C{i}"][0],
                    np.full(len(monitor.SCOREBOOK._pools["P|long"][f"C{i}"][0]), other_level))
            return evaluate(RankedJudge("long", sc, cfg=cfg), bars, X, "A", last,
                            last + 16 * HOUR, 16, symbol="MINE")
        finally:
            monitor.SCOREBOOK = real

    low, high = card(0.0), card(5.0)
    assert low.rank == high.rank and low.action == high.action == "ENTER LONG NOW"
    assert "this coin's own readings" in low.reason
    return True
