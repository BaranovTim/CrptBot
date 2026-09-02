"""One stock: chart, metrics, and what we do not know about it.

WHY THIS IS NOT THE CRYPTO DASHBOARD
    The crypto dashboard's centre of gravity is a fitted model — a
    recommendation, an expected value, a barrier geometry. None of that exists
    for equities, and this page does not pretend otherwise. What it has is a
    price series, the screener's own metrics, and an honest gap where the
    signal would be.

    That is still worth a page. "What is this company's P/E, ROE, debt, short
    interest, and where is it against its averages" is most of what a screener
    result needs to be actionable, and none of it needs a model.

WHERE THE NUMBERS COME FROM
    The metrics are read from the screener table — the SAME row the screen
    matched on. Recomputing them here would let the detail page and the
    result list disagree about the same stock, which is the kind of
    inconsistency that destroys trust in both.

    Only the chart is fetched live, because the table stores no series.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Bars change once a day. Ten minutes is short enough that a late correction
# appears, long enough that opening five stocks costs one round trip each.
CHART_TTL = 600.0

_chart_cache: Dict[str, tuple] = {}
_lock = threading.Lock()


def chart(symbol: str, days: int = 260) -> List[float]:
    """Daily closes, oldest first. Cached; never raises for one bad symbol."""
    sym = symbol.upper().strip()
    now = time.time()
    with _lock:
        hit = _chart_cache.get(sym)
        if hit and now - hit[0] < CHART_TTL:
            return hit[1]

    from marketdata.alpaca import Alpaca, AlpacaError

    try:
        client = Alpaca()
        if not client.configured:
            return []
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.5))
        df = client.bars([sym], start=start).get(sym)
        series = [] if df is None or df.empty else \
            [float(v) for v in df["close"].tail(days).tolist()]
    except AlpacaError as e:
        log.warning("chart %s: %s", sym, e)
        series = []

    with _lock:
        _chart_cache[sym] = (now, series)
    return series


def detail(symbol: str, table: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the stock page draws, in one payload."""
    sym = symbol.upper().strip()
    rows = table.get("rows") or {}
    metrics = rows.get(sym)

    return {
        "symbol": sym,
        "known": metrics is not None,
        # None when the symbol is not in the table at all, rather than an
        # empty dict — "we have never seen this ticker" and "we have it and
        # every field is blank" are different answers.
        "metrics": metrics or {},
        "series": chart(sym),
        "built_at": table.get("built_at"),
        # Stated on the page. The crypto side has a recommendation here and a
        # blank space would read as a missing feature rather than a deliberate
        # absence.
        "signal": None,
        "signal_note": (
            "No fitted model for equities. The crypto signals are trained on "
            "a market that never closes; stocks gap overnight, halt and "
            "split, so the same labelling would produce a confident number "
            "that means nothing."),
    }


def quotes(symbols: List[str], table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Price and change for a watchlist, straight from the screener table.

    No network: the table already holds a price for every symbol in it, and a
    market list that fetched thirty symbols live would be thirty round trips
    to show what is already on disk.
    """
    rows = table.get("rows") or {}
    out = []
    for s in symbols:
        sym = s.upper().strip()
        m = rows.get(sym)
        if m is None:
            # Kept, not dropped: a watchlist that silently loses a ticker is
            # worse than one that shows it with no price.
            out.append({"symbol": sym, "known": False})
            continue
        out.append({
            "symbol": sym,
            "known": True,
            "price": m.get("price"),
            "change_pct": m.get("change_pct"),
            "rsi14": m.get("rsi14"),
            "market_cap": m.get("market_cap"),
            "rel_volume": m.get("rel_volume"),
        })
    return out


def search(q: str, table: Dict[str, Any], limit: int = 40) -> List[Dict[str, Any]]:
    """Symbol search over the table.

    Ranked by prefix first, then by market cap: typing "A" should offer AAPL
    before AACG, and alphabetical order buries every ticker anyone wants.
    """
    needle = (q or "").upper().strip()
    rows = table.get("rows") or {}
    if not needle:
        candidates = list(rows.items())
    else:
        candidates = [(s, m) for s, m in rows.items() if needle in s]

    def rank(item):
        sym, m = item
        cap = m.get("market_cap") or 0
        return (0 if sym.startswith(needle) else 1, -cap, sym)

    return [{"symbol": s, "price": m.get("price"),
             "change_pct": m.get("change_pct"),
             "market_cap": m.get("market_cap")}
            for s, m in sorted(candidates, key=rank)[:limit]]
