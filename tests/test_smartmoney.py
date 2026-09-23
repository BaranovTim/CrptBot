"""smartmoney/: selection, differencing, persistence and the alert it becomes.

No network anywhere here. The venue is a dict of canned answers; the
leaderboard is a small file in the shape of the real 37MB one.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.alerts import AlertEngine                                  # noqa: E402
from api.push import _priority, is_kind_muted                       # noqa: E402
from marketdata.hyperliquid import (iter_leaderboard, positions_from_state,  # noqa: E402
                                    symbol_for)
from smartmoney.episodes import reconstruct                       # noqa: E402
from smartmoney.select import (TraderStats, candidates, record_stats,  # noqa: E402
                               score_of, select_traders)
from smartmoney.tracker import Tracker, diff_books                  # noqa: E402

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ mapping
def test_hyperliquid_coins_map_to_binance_symbols():
    assert symbol_for("BTC") == "BTCUSDT"
    assert symbol_for("kPEPE") == "1000PEPEUSDT"
    assert symbol_for("HYPE") == "HYPEUSDT"
    assert symbol_for("@334") is None            # a spot index
    assert symbol_for("PURR/USDC") is None       # a spot pair
    assert symbol_for("") is None


def test_positions_are_read_with_side_and_notional():
    state = {"assetPositions": [
        {"position": {"coin": "BTC", "szi": "-2.5", "entryPx": "63120.0",
                      "leverage": {"type": "cross", "value": 10},
                      "positionValue": "157800.0", "unrealizedPnl": "-410.5"}},
        {"position": {"coin": "ETH", "szi": "0", "entryPx": "2400"}},
    ]}
    book = positions_from_state(state)
    assert list(book) == ["BTC"]                 # a zero position is no position
    assert book["BTC"]["side"] == "SHORT" and book["BTC"]["size"] == 2.5
    assert book["BTC"]["notional"] == 157800.0 and book["BTC"]["leverage"] == 10


# ------------------------------------------------------------ leaderboard
def _row(addr, acct, month_pnl, all_pnl, vlm=5e6):
    return {"ethAddress": addr, "accountValue": str(acct), "displayName": None,
            "windowPerformances": [
                ["day", {"pnl": "1", "roi": "0.01", "vlm": "1"}],
                ["month", {"pnl": str(month_pnl), "roi": "0.3", "vlm": str(vlm)}],
                ["allTime", {"pnl": str(all_pnl), "roi": "1.0", "vlm": "1"}]]}


def _board(rows) -> Path:
    p = Path(tempfile.mkdtemp()) / "leaderboard.json"
    p.write_text(json.dumps({"leaderboardRows": rows}, indent=2))
    return p


def test_leaderboard_is_read_one_row_at_a_time_and_filtered():
    rows = [_row("0xaaaa", 2e6, 5e5, 9e6),          # qualifies
            _row("0xbbbb", 5e4, 5e5, 9e6),          # account too small
            _row("0xcccc", 2e6, -1e4, 9e6),         # lost this month
            _row("0xdddd", 2e6, 5e5, -1e4),         # lifetime loser
            _row("0xeeee", 2e6, 8e5, 9e6, vlm=1e3)]  # no real volume
    path = _board(rows)
    assert len(list(iter_leaderboard(path))) == 5
    picked = candidates(path)
    assert [c.address for c in picked] == ["0xaaaa"]


# ------------------------------------------------------------------ the record
T0 = int(NOW.timestamp() * 1000)


def _fill(days_ago, coin, px, sz, side, start, pnl=0.0, fee=1.0, crossed=True, liq=False, tid=None):
    t = T0 - int(days_ago * 86_400_000)
    return {"coin": coin, "px": str(px), "sz": str(sz), "side": side, "time": t,
            "startPosition": str(start), "closedPnl": str(pnl), "fee": str(fee),
            "crossed": crossed, "liquidation": liq, "tid": tid or t}


def _round_trip(days_ago, coin, px_in, px_out, sz, pnl, hold_h=6.0, crossed=True, liq=False):
    """One long: open, close `hold_h` later."""
    return [_fill(days_ago, coin, px_in, sz, "B", 0, crossed=crossed, tid=f"{days_ago}a{coin}"),
            _fill(days_ago - hold_h / 24, coin, px_out, sz, "A", sz, pnl=pnl, liq=liq, tid=f"{days_ago}b{coin}")]


def test_episodes_are_rebuilt_from_the_stated_position():
    fills = [_fill(10, "BTC", 100, 1.0, "B", 0), _fill(9.9, "BTC", 99, 1.0, "B", 1.0, crossed=False),
             _fill(9.5, "BTC", 105, 1.0, "A", 2.0, pnl=5), _fill(9.4, "BTC", 106, 1.0, "A", 1.0, pnl=6),
             _fill(9.0, "BTC", 106, 2.0, "A", 0), _fill(8.8, "BTC", 104, 3.0, "B", -2.0, pnl=4),
             _fill(8.5, "BTC", 103, 1.0, "A", 1.0, pnl=-1), _fill(8.0, "ETH", 10, 5.0, "B", 0)]
    eps = reconstruct(fills)
    assert [(e.coin, e.side, e.n_entry, e.n_exit) for e in eps] == [
        ("BTC", "LONG", 2, 2), ("BTC", "SHORT", 1, 1), ("BTC", "LONG", 1, 1)]
    assert abs(eps[0].entry_vwap - 99.5) < 1e-9 and abs(eps[0].exit_vwap - 105.5) < 1e-9
    assert abs(eps[0].maker_entry - 0.5) < 1e-9 and eps[0].pnl < 11
    assert eps[1].ret_pct > 0 and eps[2].ret_pct < 0          # the flip split both ways
    return True


def test_the_record_counts_position_trades_and_their_weeks():
    fills = []
    for d in range(0, 70, 5):                        # a trade every 5 days, 14 of them
        fills += _round_trip(d + 1, "BTC", 100, 103 if d % 15 else 97, 100, 300 if d % 15 else -300)
    fills += _round_trip(3, "BTC", 100, 100.5, 100, 50, hold_h=0.2)     # too short: not a position
    fills += _round_trip(4, "BTC", 100, 102, 1, 2)                        # $100: too small
    fills += _round_trip(200, "BTC", 100, 150, 100, 5000)                 # outside the window
    s = record_stats(fills, now=NOW)
    assert s["position_trades"] == 14 and s["wins"] == 9
    assert abs(s["win_rate"] - 9 / 14) < 1e-9
    assert s["pnl_record"] > 0 and s["payoff"] > 0.5
    assert s["weeks_covered"] >= 9 and s["weeks_positive"] >= 5
    assert s["coins"] == ["BTCUSDT"] and s["liquidations"] == 0
    assert abs(s["median_hold_h"] - 6.0) < 1e-9
    return True


def test_a_record_needs_every_axis_at_once():
    base = dict(address="0x1", account_value=1e6, pnl_30d=1e6, roi_30d=0.5,
                volume_30d=1e7, pnl_all=2e6, position_trades=20, wins=13,
                win_rate=0.65, pnl_record=2e5, avg_win_pct=4.0, avg_loss_pct=-3.0,
                payoff=1.33, weeks_covered=10, weeks_positive=7, median_hold_h=20.0,
                maker_share=0.3, liquidations=1, coins=["BTCUSDT"])
    good = TraderStats(**base)
    assert good.qualifies and score_of(good) > 0
    assert not TraderStats(**{**base, "position_trades": 5}).qualifies
    assert not TraderStats(**{**base, "pnl_record": -1.0}).qualifies
    assert not TraderStats(**{**base, "win_rate": 0.45}).qualifies
    assert not TraderStats(**{**base, "win_rate": 0.99}).qualifies      # never loses: a bot
    assert not TraderStats(**{**base, "weeks_covered": 3}).qualifies
    assert not TraderStats(**{**base, "weeks_positive": 4}).qualifies   # 4 of 10
    assert not TraderStats(**{**base, "liquidations": 4}).qualifies
    assert not TraderStats(**{**base, "maker_share": 0.99}).qualifies   # a market maker
    assert not TraderStats(**{**base, "coins": []}).qualifies
    # a bigger record with the same shape scores higher; a great month
    # with no record scores nothing
    assert score_of(TraderStats(**{**base, "pnl_record": 2e6})) > score_of(good)
    assert score_of(TraderStats(**{**base, "position_trades": 3, "pnl_30d": 5e7})) == 0
    return True


def test_select_reads_a_record_for_each_candidate_and_ranks_by_it():
    path = _board([_row("0xaaaa", 2e6, 5e5, 9e6), _row("0xbbbb", 2e6, 3e6, 9e6),
                   _row("0xcccc", 2e6, 5e5, 9e6)])
    asked = []

    def fills(addr):
        asked.append(addr)
        out = []
        for d in range(0, 70, 5):
            if addr == "0xcccc":                          # loses more than it wins
                out += _round_trip(d + 1, "ETH", 100, 99, 100, -100)
            else:
                win = (d // 5) % 3 != 0
                size = 3000 if addr == "0xaaaa" else 300  # aaaa has the bigger record
                out += _round_trip(d + 1, "BTC", 100, 103 if win else 98, 100, size if win else -size / 2)
        return out

    picked = select_traders(path, fills_fn=fills, now=NOW)
    assert sorted(asked) == ["0xaaaa", "0xbbbb", "0xcccc"]
    assert [t.address for t in picked] == ["0xaaaa", "0xbbbb"], [t.address for t in picked]
    assert all(t.score > 0 and t.position_trades == 14 for t in picked)
    return True


# ------------------------------------------------------------- differencing
def _pos(side, size, notional=1e5):
    return {"side": side, "size": size, "entry": 100.0, "leverage": 5,
            "notional": notional, "upnl": 0.0}


def test_diff_books_names_every_kind_of_change():
    old = {"BTC": _pos("LONG", 10), "ETH": _pos("SHORT", 4), "SOL": _pos("LONG", 100),
           "XRP": _pos("LONG", 100), "DOGE": _pos("LONG", 100)}
    new = {"BTC": _pos("SHORT", 10), "SOL": _pos("LONG", 160), "XRP": _pos("LONG", 40),
           "DOGE": _pos("LONG", 120), "HYPE": _pos("LONG", 3)}
    kinds = {d["coin"]: d["kind"] for d in diff_books(old, new)}
    assert kinds == {"BTC": "flipped", "ETH": "closed", "SOL": "added",
                     "XRP": "reduced", "HYPE": "opened"}    # DOGE +20% is nothing


class Venue:
    """Canned books, one per address, steerable between polls."""

    def __init__(self):
        self.books = {}
        self.calls = 0

    def state(self, addr):
        self.calls += 1
        book = self.books.get(addr)
        if book is None:
            return None
        return {"assetPositions": [
            {"position": {"coin": c, "szi": str(p["size"] if p["side"] == "LONG" else -p["size"]),
                          "entryPx": str(p.get("entry", 100.0)),
                          "leverage": {"value": p.get("leverage", 5)},
                          "positionValue": str(p["notional"]), "unrealizedPnl": "0"}}
            for c, p in book.items()]}


def _trader(addr, **kw):
    base = dict(address=addr, account_value=1e6, pnl_30d=2e6, roi_30d=0.4,
                volume_30d=1e7, pnl_all=3e6, position_trades=40, wins=28,
                win_rate=0.7, pnl_record=8e5, payoff=2.2, weeks_covered=8,
                weeks_positive=7, coins=["BTCUSDT"], score=3.0)
    base.update(kw)
    return TraderStats(**base)


def _tracker(venue, traders, price=None):
    path = Path(tempfile.mkdtemp()) / "smart.json"
    return Tracker(state_path=path, state_fn=venue.state,
                   select_fn=lambda: traders, price_fn=price), path


def test_first_poll_primes_and_announces_nothing():
    v = Venue()
    v.books["0xaaaa"] = {"BTC": _pos("LONG", 2, 1.2e6)}
    t, _ = _tracker(v, [_trader("0xaaaa")])
    assert t.reselect() == 1
    assert t.poll(NOW) == []                              # prime
    v.books["0xaaaa"]["ETH"] = _pos("SHORT", 50, 9e4)
    evs = t.poll(NOW + timedelta(minutes=1))
    assert [(e.kind, e.coin, e.symbol, e.side) for e in evs] == [
        ("opened", "ETH", "ETHUSDT", "SHORT")]
    assert evs[0].trader["win_rate"] == 0.7 and evs[0].notional == 9e4


def test_events_carry_the_price_we_saw_and_survive_a_restart():
    v = Venue()
    v.books["0xaaaa"] = {}
    t, path = _tracker(v, [_trader("0xaaaa")], price=lambda s: 63000.5 if s == "BTCUSDT" else None)
    t.reselect(); t.poll(NOW)
    v.books["0xaaaa"] = {"BTC": _pos("LONG", 1, 6.3e4)}
    (e,) = t.poll(NOW + timedelta(minutes=1))
    assert e.price_at == 63000.5
    again = Tracker(state_path=path, state_fn=v.state, select_fn=lambda: [])
    assert [x.id for x in again.events] == [e.id]
    assert again.books["0xaaaa"]["BTC"]["side"] == "LONG"
    # and the restarted tracker does not re-prime: closing now is an event
    v.books["0xaaaa"] = {}
    (c,) = again.poll(NOW + timedelta(minutes=2))
    assert c.kind == "closed" and c.notional == 6.3e4


def test_an_unreadable_venue_changes_nothing():
    v = Venue()
    v.books["0xaaaa"] = {"BTC": _pos("LONG", 1)}
    t, _ = _tracker(v, [_trader("0xaaaa")])
    t.reselect(); t.poll(NOW)
    del v.books["0xaaaa"]                                  # venue answers None
    assert t.poll(NOW + timedelta(minutes=1)) == []
    assert t.books["0xaaaa"]["BTC"]["side"] == "LONG"      # not "closed"


def test_consensus_counts_sides_among_the_followed():
    v = Venue()
    v.books["0xaaaa"] = {"BTC": _pos("LONG", 1, 2e6)}
    v.books["0xbbbb"] = {"BTC": _pos("SHORT", 1, 5e5), "kPEPE": _pos("LONG", 9, 1e4)}
    v.books["0xcccc"] = {}
    t, _ = _tracker(v, [_trader("0xaaaa"), _trader("0xbbbb"), _trader("0xcccc")])
    t.reselect(); t.poll(NOW)
    c = t.consensus("BTCUSDT")
    assert (c["tracked"], c["long"], c["short"]) == (3, 1, 1)
    assert c["long_notional"] == 2e6 and c["holders"][0]["address"] == "0xaaaa"
    assert t.consensus("1000PEPEUSDT")["long"] == 1
    assert t.consensus("SOLUSDT")["holders"] == []


def test_reselection_keeps_the_books_of_those_who_stay():
    v = Venue()
    v.books["0xaaaa"] = {"BTC": _pos("LONG", 1)}
    v.books["0xbbbb"] = {"ETH": _pos("LONG", 1)}
    t, _ = _tracker(v, [_trader("0xaaaa"), _trader("0xbbbb")])
    t.reselect(); t.poll(NOW)
    t._select_fn = lambda: [_trader("0xaaaa"), _trader("0xdddd")]
    t.reselect()
    v.books["0xdddd"] = {"SOL": _pos("LONG", 1)}
    assert t.poll(NOW + timedelta(minutes=1)) == []        # 0xdddd primes, 0xaaaa unchanged
    assert "0xbbbb" not in t.books
    # an empty selection (venue down) keeps yesterday's list
    t._select_fn = lambda: []
    assert t.reselect() == 2


# -------------------------------------------------------------- the alert
class SmartSvc:
    RECORD_INTERVALS = ()

    def __init__(self, tracker):
        self._t = tracker
        self.symbol, self.interval = "BTCUSDT", "1h"

    def trained_symbols(self):
        return ["BTCUSDT", "ETHUSDT"]

    def smart_tracker(self):
        return self._t

    def dashboard(self, **_):
        raise AssertionError("no pairs are watched in this test")

    def whales(self, limit=25):
        return []

    def news(self, limit=25):
        return []


def test_an_event_becomes_a_labelled_alert_once():
    v = Venue()
    v.books["0xe867fbdad3291530e41530301ecb77693850c78e"] = {}
    t, _ = _tracker(v, [_trader("0xe867fbdad3291530e41530301ecb77693850c78e",
                               pnl_record=22.1e6, win_rate=0.69, position_trades=873)])
    t.reselect(); t.poll(NOW)
    engine = AlertEngine(SmartSvc(t), pairs=[],
                         state_path=Path(tempfile.mkdtemp()) / "a.json")
    assert engine.refresh() == []                          # primes the engine
    v.books["0xe867fbdad3291530e41530301ecb77693850c78e"] = {
        "BTC": {"side": "LONG", "size": 33.0, "notional": 2.1e6, "entry": 63120.0,
                "leverage": 10, "upnl": 0.0},
        "ZEC": {"side": "LONG", "size": 1.0, "notional": 1e3, "entry": 1.0,
                "leverage": 1, "upnl": 0.0}}                # ZEC is not served
    t.poll(NOW + timedelta(minutes=1))
    new = engine.refresh()
    assert [a.kind for a in new] == ["smart"]
    a = new[0]
    assert a.symbol == "BTCUSDT" and a.interval == "" and a.strength == ""
    assert a.title == "BTCUSDT: 0xe867…c78e opened LONG"
    assert a.body.splitlines() == ["10× · $2.1M @ 63,120.00",
                                   "69% win rate · +$22.1M / 6mo · 873 trades",
                                   "1 of 1 followed long, 0 short"]
    assert a.extra["event"] == "opened" and a.extra["side"] == "LONG"
    assert engine.refresh() == []                          # not twice
    # an add is panel context, not a buzz
    v.books["0xe867fbdad3291530e41530301ecb77693850c78e"]["BTC"]["size"] = 60.0
    t.poll(NOW + timedelta(minutes=2))
    assert engine.refresh() == []


def test_the_phone_can_silence_the_kind_and_the_relay_ranks_it_high():
    assert is_kind_muted(["kind:smart"], "smart")
    assert not is_kind_muted(["kind:news"], "smart")

    class A:
        kind, strength = "smart", ""
    assert _priority(A()) == 4


def test_polling_continues_while_a_selection_runs():
    """A selection is hours of paged reads. The loop must keep polling
    books while one is in flight, and take the new list when it lands."""
    import threading
    import time as _t

    v = Venue()
    v.books["0xaaaa"] = {"BTC": _pos("LONG", 1)}
    gate = threading.Event()

    def slow_select():
        gate.wait(5)
        return [_trader("0xaaaa"), _trader("0xbbbb")]

    t, _ = _tracker(v, [_trader("0xaaaa")])
    t.reselect()                                   # the initial, synchronous one
    t.poll(NOW)
    t._select_fn = slow_select
    t.selected_at = "2000-01-01T00:00:00+00:00"    # long overdue
    t.start(poll_seconds=0.05, reselect_seconds=3600)
    try:
        _t.sleep(0.3)
        v.books["0xaaaa"]["ETH"] = _pos("LONG", 2)   # a change WHILE selecting
        _t.sleep(0.3)
        assert any(e.kind == "opened" and e.coin == "ETH" for e in t.events), \
            "polling stopped during selection"
        assert len(t.traders) == 1                  # not swapped yet
        gate.set()
        deadline = _t.time() + 3
        while len(t.traders) != 2 and _t.time() < deadline:
            _t.sleep(0.05)
        assert len(t.traders) == 2, "the finished selection was not applied"
    finally:
        t.stop()
    return True


def test_net_entries_counts_the_last_day_by_side():
    from datetime import timedelta as _td

    v = Venue()
    v.books["0xaaaa"] = {}; v.books["0xbbbb"] = {}; v.books["0xcccc"] = {}
    t, _ = _tracker(v, [_trader("0xaaaa"), _trader("0xbbbb"), _trader("0xcccc")])
    t.reselect(); t.poll(NOW)
    v.books["0xaaaa"] = {"BTC": _pos("LONG", 1)}
    t.poll(NOW + _td(hours=1))
    v.books["0xbbbb"] = {"BTC": _pos("LONG", 2), "ETH": _pos("SHORT", 3)}
    t.poll(NOW + _td(hours=2))
    v.books["0xcccc"] = {"BTC": _pos("SHORT", 1)}
    t.poll(NOW + _td(hours=3))
    at = NOW + _td(hours=4)
    btc = t.net_entries("BTCUSDT", hours=24, now=at)
    assert (btc["net"], btc["longs"], btc["shorts"]) == (1, 2, 1), btc
    assert t.net_entries("ETHUSDT", hours=24, now=at)["net"] == -1
    # an entry older than the window no longer counts; a close never does
    assert t.net_entries("BTCUSDT", hours=24, now=NOW + _td(hours=30))["net"] == 0
    v.books["0xaaaa"] = {}
    t.poll(NOW + _td(hours=5))
    assert t.net_entries("BTCUSDT", hours=24, now=NOW + _td(hours=6))["net"] == 1
    assert t.net_entries("SOLUSDT", hours=24, now=at)["net"] == 0
    return True


# ------------------------------------------------ the selection, out of process
def test_the_selection_runs_in_a_child_and_a_failure_keeps_yesterdays_list():
    """As a thread in the API, the daily selection grew the process past a
    gigabyte and the kernel killed the API four times in one evening. It is
    a child process now: its answer is read from a file, and any failure
    leaves the followed set as it was."""
    import json
    import subprocess
    import tempfile
    from pathlib import Path

    from smartmoney import tracker as T
    from smartmoney.select import TraderStats

    with tempfile.TemporaryDirectory() as d:
        tr = T.Tracker(state_path=Path(d) / "state.json", cache_dir=Path(d),
                       state_fn=lambda a: None)
        tr.traders = [TraderStats(address="0xold", account_value=1, pnl_30d=1, roi_30d=0,
                                  volume_30d=1, pnl_all=1)]
        real = subprocess.run
        calls = []

        def ok(cmd, **kw):
            calls.append(cmd)
            Path(cmd[3]).write_text(json.dumps([TraderStats(
                address="0xnew", account_value=2, pnl_30d=2, roi_30d=0, volume_30d=2,
                pnl_all=2, score=1.0).to_json()]))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def dies(cmd, **kw):
            return subprocess.CompletedProcess(cmd, -9, "", "Killed")
        try:
            subprocess.run = dies
            assert tr.reselect() == 1 and tr.traders[0].address == "0xold"
            subprocess.run = ok
            assert tr.reselect() == 1 and tr.traders[0].address == "0xnew"
            assert calls[-1][1:3] == ["-m", "smartmoney.run_select"]
        finally:
            subprocess.run = real
    return True


def test_the_fills_the_selection_reads_are_slimmed_to_what_it_uses():
    from marketdata import hyperliquid as H
    from smartmoney.select import FILL_FIELDS

    page = [{"coin": "BTC", "px": "1", "sz": "2", "side": "B", "time": 1, "startPosition": "0",
             "closedPnl": "0", "fee": "0", "crossed": True, "tid": 7,
             "hash": "0x" + "a" * 64, "oid": 123, "dir": "Open Long", "feeToken": "USDC"}]
    real = H.info
    try:
        H.info = lambda body: page
        got = H.user_fills_since("0xabc", fields=FILL_FIELDS, pause=0)
    finally:
        H.info = real
    assert got == [{k: page[0][k] for k in FILL_FIELDS if k in page[0]}]
    assert "hash" not in got[0] and "oid" not in got[0]
    return True
