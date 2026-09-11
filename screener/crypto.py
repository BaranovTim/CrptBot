"""The crypto side of the screener.

WHY THIS IS NOT THE EQUITY BUILDER WITH A DIFFERENT SOURCE
    Half the equity fields do not exist here and never will. A perpetual
    future has no earnings, no equity, no dividend and no float, so P/E,
    ROE and payout ratio are not "missing data" — they are category errors.
    Offering them would produce a screen that matches nothing and blames the
    data for it.

    What crypto has instead is what equities do not: funding, open interest,
    and a 24/7 tape. Those are the fields worth screening on here.

WHERE THE UNIVERSE COMES FROM
    Binance's 24h ticker gives price, change and volume for every perpetual in
    one request. Indicators need bars, and bars are one request per symbol —
    so the universe is capped at the most liquid names. Screening the tail of
    a perpetuals exchange returns contracts nobody can get out of.

WHY 'TRAINED' IS A FIRST-CLASS FILTER HERE
    On the equity side a model is a rarity. Here it is the point of the app:
    "show me coins the bot has a fitted model for, that are also oversold" is
    the question this screener exists to answer, and it cannot be asked with
    price fields alone.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

log = logging.getLogger(__name__)

TICKER_24H = "https://fapi.binance.com/fapi/v1/ticker/24hr"
KLINES = ("https://fapi.binance.com/fapi/v1/klines"
          "?symbol={symbol}&interval=1d&limit={limit}")

STORE = Path("data_cache") / "screener"
TABLE = STORE / "crypto_universe.json"

# The most liquid contracts. Beyond this the spread is the trade.
MAX_SYMBOLS = 180

# Enough for SMA200 plus a little.
BARS = 260


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": "Vanth/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def tickers() -> List[Dict[str, Any]]:
    """Every perpetual, with 24h price, change and quote volume. One call."""
    rows = _get(TICKER_24H, timeout=30)
    out = []
    for r in rows:
        sym = r.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            out.append({
                "symbol": sym,
                "price": float(r["lastPrice"]),
                "change_pct": float(r["priceChangePercent"]),
                # QUOTE volume, i.e. dollars. Base volume would rank a
                # billion-token memecoin above Bitcoin.
                "quote_volume": float(r["quoteVolume"]),
                "trades": float(r.get("count") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: -x["quote_volume"])
    return out


def daily_bars(symbol: str, limit: int = BARS) -> Optional[pd.DataFrame]:
    try:
        raw = _get(KLINES.format(symbol=symbol, limit=limit))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError) as e:
        log.debug("klines %s: %s", symbol, e)
        return None
    if not raw:
        return None
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "tb_base", "tb_quote", "ignore"])
    # INDEXED BY CLOSE TIME, like everything else in this repo. A bar labelled
    # by its open is an hour of free foresight — see `binance.py`.
    df["close_time"] = pd.to_datetime(df["close_time"].astype("int64"),
                                      unit="ms", utc=True)
    df = df.set_index("close_time").sort_index()
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # The final bar is still forming, and its high, low and volume are all
    # partial. Dropping it is the same guard the collector applies.
    now = datetime.now(timezone.utc)
    return df[df.index <= now]


def build(limit: int = MAX_SYMBOLS,
          progress_every: int = 40) -> Dict[str, dict]:
    """Compute every crypto screener field for the liquid universe."""
    from core.timeframes import is_trained
    from screener.technicals import technicals

    universe = tickers()[:limit]
    log.info("crypto screener: %d symbols", len(universe))

    # Beta against BTC, not SPY: "how much does this move when the market
    # moves" means something different in a market where BTC IS the market.
    btc = daily_bars("BTCUSDT")
    bench = None if btc is None or btc.empty else btc["close"]

    rows: Dict[str, dict] = {}
    t0 = time.time()
    for i, t in enumerate(universe):
        sym = t["symbol"]
        bars = daily_bars(sym)
        if bars is None or bars.empty:
            continue
        m = technicals(bars, benchmark=bench)
        m.update({
            # From the ticker rather than the bars: it is the exchange's own
            # rolling 24h window, which is what every other crypto tool shows.
            "price": t["price"],
            "change_pct": t["change_pct"],
            "quote_volume": t["quote_volume"],
            "name": sym.replace("USDT", ""),
            "trained_intervals": _trained(sym, is_trained),
        })
        m["trained"] = 1.0 if m["trained_intervals"] else 0.0
        rows[sym] = m
        if progress_every and i and i % progress_every == 0:
            log.info("crypto screener: %d/%d, %.0fs",
                     i, len(universe), time.time() - t0)
    log.info("crypto screener: %d rows in %.0fs", len(rows), time.time() - t0)
    return rows


def _trained(symbol: str, is_trained) -> List[str]:
    out = []
    for iv in ("1m", "5m", "15m", "1h", "4h", "1d"):
        try:
            if is_trained(symbol, iv):
                out.append(iv)
        except Exception:
            pass
    return out


def save(rows: Dict[str, dict], path: Optional[Path] = None) -> Path:
    from screener.universe import _clean

    path = Path(path or TABLE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_clean({
        "built_at": datetime.now(timezone.utc).isoformat(),
        "symbols": len(rows), "rows": rows}), allow_nan=False))
    tmp.replace(path)
    return path


def load(path: Optional[Path] = None) -> dict:
    path = Path(path or TABLE)
    if not path.exists():
        return {"built_at": None, "symbols": 0, "rows": {}}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as e:
        log.warning("crypto screener table unreadable: %s", e)
        return {"built_at": None, "symbols": 0, "rows": {}}
