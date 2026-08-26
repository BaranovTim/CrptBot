"""Tracking what important actors actually did, with amounts and timestamps.

    from whalefeed import EdgarSource, WhaleStore
    events = EdgarSource().fetch()
    WhaleStore().append(events)

Evidence, not a trigger. See events.py for why named-whale following is
the weakest of the whale signals, and why it is reported rather than acted on.
"""
from .edgar import EdgarSource, fetch_entity, parse_form4
from .events import CONVICTION, WhaleEvent
from .store import WhaleStore
from .watchlist import BY_CIK, BY_TICKER, WATCHLIST, Entity, describe, entities

__all__ = ["WhaleEvent", "CONVICTION", "WhaleStore", "EdgarSource",
           "fetch_entity", "parse_form4", "WATCHLIST", "Entity",
           "BY_TICKER", "BY_CIK", "entities", "describe"]
