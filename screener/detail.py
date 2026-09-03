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


# How far back each interval reaches, and how many points to return.
#
# Not one number: 200 one-minute bars is three hours and 200 daily bars is a
# year. The window is chosen so every timeframe shows a comparable amount of
# STRUCTURE rather than a comparable number of bars.
_WINDOWS = {
    "1m": (3, 240), "5m": (10, 240), "15m": (30, 240),
    "1h": (120, 240), "4h": (365, 240), "1d": (400, 260),
}


def chart(symbol: str, interval: str = "1d") -> List[float]:
    """Closes for one interval, oldest first. Cached per (symbol, interval)."""
    from marketdata.equity_seed import INTERVALS

    sym = symbol.upper().strip()
    iv = interval if interval in _WINDOWS else "1d"
    key = f"{sym}:{iv}"
    now = time.time()
    with _lock:
        hit = _chart_cache.get(key)
        if hit and now - hit[0] < CHART_TTL:
            return hit[1]

    from marketdata.alpaca import Alpaca, AlpacaError

    days, points = _WINDOWS[iv]
    try:
        client = Alpaca()
        if not client.configured:
            return []
        tf, _ = INTERVALS[iv]
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.6))
        df = client.bars([sym], timeframe=tf, start=start).get(sym)
        if df is not None and not df.empty and iv == "4h":
            # Alpaca has no four-hour bar; built from hours, same as the
            # training seeder does, so the chart and the model agree on what
            # a 4h bar is.
            df = df.resample("4h", label="left", closed="left").agg(
                {"close": "last"}).dropna()
        series = [] if df is None or df.empty else \
            [float(v) for v in df["close"].tail(points).tolist()]
    except AlpacaError as e:
        log.warning("chart %s %s: %s", sym, iv, e)
        series = []

    with _lock:
        _chart_cache[key] = (now, series)
    return series


def detail(symbol: str, table: Dict[str, Any],
           interval: str = "1d") -> Dict[str, Any]:
    """Everything the stock page draws, in one payload.

    THE RECOMMENDATION IS THE SAME CODE PATH AS CRYPTO.
    ---------------------------------------------------
    When a model exists for this symbol and interval, the analysis comes from
    `TradingService.dashboard` — the identical function that produces BTC's
    call, with the identical labelling, calibration and expected-value
    arithmetic. Nothing here re-implements a recommendation.

    When no model exists it says so and gives the exact command to fit one,
    the way the crypto side already does for an untrained timeframe. An
    equity is not a special case; it is an untrained pair.
    """
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
        "interval": interval if interval in _WINDOWS else "1d",
        "intervals": list(_WINDOWS),
        "series": chart(sym, interval),
        "built_at": table.get("built_at"),
        **_analysis(sym, interval),
    }


# US regular session, in Eastern time. Pre-market runs from 04:00, after
# hours to 20:00.
_SESSION = ((9, 30), (16, 0))
_PRE = ((4, 0), (9, 30))
_POST = ((16, 0), (20, 0))


def session_state(now=None) -> Dict[str, Any]:
    """Which session US equities are in, right now.

    WHY THIS EXISTS
        A price with no session attached is misleading in a way a crypto price
        never is. "AAPL 325.06" at 3am is Friday's close being shown on a
        Tuesday, and nothing on screen said so. Crypto never closes, so this
        whole concept was absent from the app.

    Holidays are NOT handled — the exchange calendar is another dataset, and
    claiming "open" on Thanksgiving is a smaller error than pretending to know
    a calendar this does not have. Weekends are.
    """
    from datetime import datetime, time as _time, timezone as _tz

    try:
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
    except Exception:
        return {"state": "unknown", "label": ""}

    now = (now or datetime.now(_tz.utc)).astimezone(et)
    if now.weekday() >= 5:
        return {"state": "closed", "label": "Market closed · weekend",
                "extended": False}

    def at(hm):
        return _time(hm[0], hm[1])

    t = now.time()
    if at(_SESSION[0]) <= t < at(_SESSION[1]):
        return {"state": "open", "label": "Market open", "extended": False}
    if at(_PRE[0]) <= t < at(_PRE[1]):
        return {"state": "pre", "label": "Pre-market", "extended": True}
    if at(_POST[0]) <= t < at(_POST[1]):
        return {"state": "post", "label": "After hours", "extended": True}
    return {"state": "closed", "label": "Market closed", "extended": False}


def extended_quotes(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Latest trade outside the regular session, per symbol.

    The screener table holds the last CLOSE. In pre-market and after hours
    that is stale by hours while the stock is actively trading, so this asks
    Alpaca for the most recent minute bar and reports it separately — never
    merged into `price`, because "the close" and "where it is trading now"
    are different numbers and conflating them is how a stale figure gets
    acted on.
    """
    from datetime import datetime, timedelta, timezone as _tz

    from marketdata.alpaca import Alpaca, AlpacaError

    syms = [s.upper().strip() for s in symbols if s and s.strip()]
    if not syms:
        return {}
    try:
        client = Alpaca()
        if not client.configured:
            return {}
        # A wide window: pre-market can be thin, and a symbol with no trade in
        # the last ten minutes still has one from an hour ago.
        start = datetime.now(_tz.utc) - timedelta(hours=14)
        bars = client.bars(syms, timeframe="1Min", start=start)
    except AlpacaError as e:
        log.warning("extended quotes: %s", e)
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for sym, df in bars.items():
        if df is None or df.empty:
            continue
        out[sym] = {
            "price": float(df["close"].iloc[-1]),
            # The free plan is fifteen minutes delayed, so this is labelled
            # rather than presented as live.
            "at": df.index[-1].isoformat(),
            "delayed_minutes": 15,
        }
    return out


def quotes(symbols: List[str], table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Price and change for a watchlist, straight from the screener table.

    No network: the table already holds a price for every symbol in it, and a
    market list that fetched thirty symbols live would be thirty round trips
    to show what is already on disk.
    """
    rows = table.get("rows") or {}
    session = session_state()
    # Only outside the regular session, and only then: during market hours the
    # nightly close is not what anyone wants either, but a 15-minute-delayed
    # minute bar is a different kind of wrong and one request per page load.
    ext = extended_quotes(symbols) if session.get("extended") else {}
    out = []
    for s in symbols:
        sym = s.upper().strip()
        m = rows.get(sym)
        if m is None:
            # Kept, not dropped: a watchlist that silently loses a ticker is
            # worse than one that shows it with no price.
            out.append({"symbol": sym, "known": False})
            continue
        e = ext.get(sym)
        row = {
            # Which timeframes have a fitted model, so the market list can
            # show the same trained/untrained state the crypto list shows
            # rather than implying every stock has a call behind it.
            "trained": _trained_intervals(sym),
            "symbol": sym,
            "known": True,
            "price": m.get("price"),
            "change_pct": m.get("change_pct"),
            "rsi14": m.get("rsi14"),
            "market_cap": m.get("market_cap"),
            "rel_volume": m.get("rel_volume"),
            "name": m.get("name"),
            "session": session.get("state"),
            "session_label": session.get("label"),
        }
        if e:
            close = m.get("price")
            row["extended_price"] = e["price"]
            row["extended_at"] = e["at"]
            if close:
                row["extended_change_pct"] = (e["price"] / close - 1.0) * 100.0
        out.append(row)
    return out


def _trained_intervals(symbol: str) -> List[str]:
    from core.timeframes import is_trained

    out = []
    for iv in ("1m", "5m", "15m", "1h", "4h", "1d"):
        try:
            if is_trained(symbol, iv):
                out.append(iv)
        except Exception:
            pass
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
             "name": m.get("name"),
             "market_cap": m.get("market_cap")}
            for s, m in sorted(candidates, key=rank)[:limit]]


def _analysis(symbol: str, interval: str) -> Dict[str, Any]:
    """The fitted model's call, or an honest account of why there is none."""
    from core.timeframes import is_trained

    try:
        trained = is_trained(symbol, interval)
    except Exception:
        trained = False

    if not trained:
        return {
            "trained": False,
            "recommendation": None,
            "untrained_note": (
                f"No model fitted for {symbol} {interval} yet. Every "
                f"timeframe is its own fit — patterns mean different things "
                f"at 1h and 1d, so one model cannot serve both."),
            "train_command": (
                f"python3 train_stocks.py --symbol {symbol} "
                f"--intervals {interval}"),
        }

    try:
        from api.service import get_service

        d = get_service().dashboard(symbol=symbol, interval=interval)
    except Exception as e:
        log.warning("stock analysis %s %s: %s", symbol, interval, e)
        return {"trained": True, "recommendation": None,
                "untrained_note": f"Model exists but could not be read: {e}",
                "train_command": ""}

    return {
        "trained": True,
        # The whole dashboard payload, so the stock page can draw the same
        # recommendation card, the same levels and the same indicators the
        # crypto page draws — from the same numbers.
        "recommendation": d.get("recommendation"),
        "levels": d.get("levels"),
        "indicators": d.get("indicators"),
        "status": d.get("status"),
        "last_closed_bar": d.get("last_closed_bar"),
        "timeframes": d.get("timeframes"),
    }


# --------------------------------------------------------------- insiders
#
# The crypto side's "whales" are SEC Form 4 filings from a hardcoded list of
# crypto-adjacent companies, because insider trading at MicroStrategy is the
# closest thing to a whale that files with a regulator.
#
# For a stock the question is simpler and the answer is better: the insiders
# of THAT company. Same filings, same parser, no watchlist needed — the
# company is the watchlist.

_insider_cache: Dict[str, tuple] = {}
INSIDER_TTL = 1800.0


def insiders(symbol: str, since_days: int = 90,
             limit: int = 25) -> List[Dict[str, Any]]:
    """Form 4 insider transactions for one company."""
    sym = symbol.upper().strip()
    now = time.time()
    with _lock:
        hit = _insider_cache.get(sym)
        if hit and now - hit[0] < INSIDER_TTL:
            return hit[1]

    events: List[Dict[str, Any]] = []
    try:
        from marketdata.edgar import ticker_map, ticker_names
        from whalefeed import Entity, fetch_entity

        cik = ticker_map().get(sym)
        if cik is None:
            raise LookupError(f"{sym} has no CIK")

        entity = Entity(
            name=ticker_names().get(sym, sym), cik=int(cik), ticker=sym,
            category="issuer",
            # This is the company's OWN insiders, so its bearing on crypto is
            # nil — the field exists to damp a distant company's signal and
            # has nothing to damp here.
            crypto_proximity=0.0,
            note="insider filings for this issuer")
        for e in fetch_entity(entity, since_days=since_days,
                              max_filings=limit):
            d = e.to_json() if hasattr(e, "to_json") else dict(e.__dict__)
            events.append(d)
    except Exception as e:
        log.warning("insiders %s: %s", sym, e)

    with _lock:
        _insider_cache[sym] = (now, events)
    return events


# ------------------------------------------------------------ news per stock
_news_cache: Dict[str, tuple] = {}
NEWS_TTL = 900.0


def stock_news(symbol: str, limit: int = 40) -> List[Dict[str, Any]]:
    """Filings-based news for one company, fetched on demand and cached.

    ON DEMAND rather than collected, because the set of symbols anyone
    follows is a device preference the server does not know. Polling 13,000
    companies to pre-fill a page nobody has open would be absurd; polling one
    when it is opened costs a single request.
    """
    sym = symbol.upper().strip()
    now = time.time()
    with _lock:
        hit = _news_cache.get(sym)
        if hit and now - hit[0] < NEWS_TTL:
            return hit[1]

    items: List[Dict[str, Any]] = []
    try:
        from newsfeed.equities import TickerNews

        for n in TickerNews([sym], limit_per_symbol=limit).fetch():
            items.append({
                "headline": n.headline,
                "source": n.source,
                "summary": n.body,
                "url": n.url,
                "assets": list(n.assets),
                "macro": False,
                "published_at": n.published_at.isoformat()
                if n.published_at else None,
                # Filings carry no directional reading. Saying NO READING is
                # honest; inventing BULL from an 8-K item code would not be.
                "bias": "NO READING",
                "impact": "NO READING",
            })
    except Exception as e:
        log.warning("stock news %s: %s", sym, e)

    with _lock:
        _news_cache[sym] = (now, items)
    return items
