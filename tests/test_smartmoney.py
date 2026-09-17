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
from smartmoney.select import (TraderStats, candidates, fill_stats,  # noqa: E402
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


# ------------------------------------------------------------------ fills
def _fill(days_ago, coin, pnl, fee=1.0, close=True):
    t = NOW - timedelta(days=days_ago)
    return {"coin": coin, "time": int(t.timestamp() * 1000),
            "dir": ("Close Long" if close else "Open Long"),
            "closedPnl": str(pnl), "fee": str(fee)}


def test_fill_stats_count_reducing_fills_net_of_fees_by_week():
    fills = [_fill(1, "BTC", 100), _fill(2, "BTC", 50), _fill(3, "ETH", -30),
             _fill(9, "kPEPE", 80), _fill(10, "BTC", 1.5, fee=2.0),  # a loss net of fee
             _fill(16, "SOL", 200), _fill(1, "BTC", 0, close=False),  # an opening fill
             _fill(120, "BTC", 9999)]                                   # outside 90 days
    s = fill_stats(fills, now=NOW)
    assert s["closed_trades"] == 6 and s["wins"] == 4
    assert abs(s["win_rate"] - 4 / 6) < 1e-9
    assert s["weeks_covered"] == 3 and s["weeks_positive"] == 3
    assert s["coins"] == ["1000PEPEUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert 0 < s["profit_factor"] < 99
    assert s["active_days"] == 6 and 0 < s["fills_per_day"] < 1


def test_a_record_needs_every_axis_at_once():
    base = dict(address="0x1", account_value=1e6, pnl_30d=1e6, roi_30d=0.5,
                volume_30d=1e7, pnl_all=2e6, closed_trades=300, wins=200,
                win_rate=0.66, profit_factor=2.0, weeks_covered=4, weeks_positive=4,
                active_days=20, fills_per_day=8.0, coins=["BTCUSDT"])
    good = TraderStats(**base)
    assert good.qualifies and score_of(good) > 0
    assert not TraderStats(**{**base, "closed_trades": 50}).qualifies
    assert not TraderStats(**{**base, "win_rate": 0.5}).qualifies
    assert not TraderStats(**{**base, "profit_factor": 1.0}).qualifies
    assert not TraderStats(**{**base, "weeks_positive": 2}).qualifies   # 2 of 4
    assert TraderStats(**{**base, "weeks_positive": 3}).qualifies       # 3 of 4
    assert not TraderStats(**{**base, "weeks_covered": 1, "weeks_positive": 1}).qualifies
    # the bot filter: a book that never loses, a hundred closes a day, or
    # nothing but spot indices is not a trader with a view
    assert not TraderStats(**{**base, "win_rate": 1.0, "profit_factor": 99}).qualifies
    assert not TraderStats(**{**base, "profit_factor": 99}).qualifies   # never a realised loss
    assert not TraderStats(**{**base, "fills_per_day": 140.0}).qualifies
    assert not TraderStats(**{**base, "active_days": 4}).qualifies
    assert not TraderStats(**{**base, "coins": []}).qualifies
    # a bigger month with the same record scores higher, and a perfect
    # tiny month does not beat a good big one
    small = TraderStats(**{**base, "pnl_30d": 5e4, "win_rate": 0.9, "profit_factor": 5})
    assert score_of(good) > score_of(small)


def test_select_reads_fills_only_for_candidates_and_ranks_by_score():
    path = _board([_row("0xaaaa", 2e6, 5e5, 9e6), _row("0xbbbb", 2e6, 3e6, 9e6),
                   _row("0xcccc", 2e6, 5e5, 9e6)])
    asked = []

    def fills(addr):
        asked.append(addr)
        if addr == "0xcccc":                  # a scalper that bleeds
            return [_fill(d % 20, "BTC", 1 if d % 3 else -50) for d in range(150)]
        return [_fill(d % 25, "BTC", 30 if d % 4 else -20) for d in range(150)]

    picked = select_traders(path, fills_fn=fills, now=NOW)
    assert sorted(asked) == ["0xaaaa", "0xbbbb", "0xcccc"]
    assert [t.address for t in picked] == ["0xbbbb", "0xaaaa"]   # bigger month first
    assert all(t.score > 0 for t in picked)


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
                volume_30d=1e7, pnl_all=3e6, closed_trades=400, wins=280,
                win_rate=0.7, profit_factor=2.2, weeks_covered=4, weeks_positive=4,
                score=3.0)
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
                               pnl_30d=22.1e6, win_rate=0.69, closed_trades=873)])
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
                                   "69% win rate · +$22.1M / 30d · 873 trades",
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
