"""Following the traders whose books are public and whose records are good.

    from smartmoney import get_tracker
    t = get_tracker()          # selects, then polls, on its own thread
    t.consensus("BTCUSDT")     # who among the tracked is long / short now
    t.events(since_ms)         # what they opened, closed or flipped

Two halves. `select` reads the venue's leaderboard and every candidate's
fill history and keeps the few whose record is long, consistent and
profitable. `tracker` polls those addresses' open positions every minute
and turns the differences into events, which the alert engine relays to
phones as the "smart" kind.

THIS IS INFORMATION, NOT A SIGNAL, UNTIL IT IS MEASURED
    A leaderboard selects on PAST profit. Whether the people on it keep
    winning, and whether a follower who sees the entry a minute late and at
    a worse price keeps any of it, are empirical questions this package
    deliberately does not answer by assertion: every event is logged with
    the price at detection so `research/` can score what happened after.
    The app labels the feed accordingly.
"""
from .episodes import Episode, reconstruct
from .select import TraderStats, record_stats, select_traders
from .tracker import Event, Tracker, get_tracker

__all__ = ["Episode", "Event", "Tracker", "TraderStats", "get_tracker",
           "reconstruct", "record_stats", "select_traders"]
