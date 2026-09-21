"""Hyperliquid: the one venue where every trader's book is public.

WHY THIS VENUE
    Copy-trading feeds on the centralised exchanges are partial (a leader
    can hide the open book), unofficial (Binance's leaderboard is scraped
    against its terms) or both. Hyperliquid is an on-chain perpetual
    exchange: positions, fills and a leaderboard are published for every
    address by design, keyless, with generous limits. It also lists every
    coin this app covers.

THREE ENDPOINTS, ALL PUBLIC
    stats-data.hyperliquid.xyz/Mainnet/leaderboard
        ~45,000 accounts with PnL, ROI and volume over day / week / month /
        all-time. Around 37MB of JSON; fetched once a day and parsed one row
        at a time so the process never holds 45,000 dicts at once (measured:
        a naive json.load of it would cost ~400MB on a 1GB box).
    api.hyperliquid.xyz/info  {"type": "clearinghouseState", "user": ...}
        the address's open positions right now: coin, signed size, entry,
        leverage, unrealised PnL.
    api.hyperliquid.xyz/info  {"type": "userFills", "user": ...}
        the newest 2,000 fills with direction ("Open Long", "Close Short"...)
        and realised PnL per reducing fill -- enough to count a trader's
        closed trades and how many of them made money.

RATE LIMITS
    The info endpoint is weight-based per IP (1,200/min); a position read
    weighs 2. Reading 25 addresses a minute is 50. Every call retries with
    backoff on 429 and never raises past `info()` -- a venue being down for
    a while must degrade the tracker, not the API.

COIN NAMES
    Perpetuals are named by coin ("BTC"); thousand-lots carry a "k" prefix
    ("kPEPE" is what Binance calls 1000PEPE); spot markets are "@123" or
    "PURR/USDC" and are not tracked. `symbol_for` maps to Binance symbols
    and answers None for anything that is not a perpetual.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .binance import DEFAULT_CACHE

log = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
WINDOWS = ("day", "week", "month", "allTime")
USER_AGENT = "Vanth/smartmoney"


def symbol_for(coin: str) -> Optional[str]:
    """Hyperliquid coin -> Binance USDT symbol, or None for spot/unknown."""
    c = (coin or "").strip()
    if not c or c.startswith("@") or "/" in c:
        return None
    if len(c) > 1 and c[0] == "k" and c[1:].isupper():
        return f"1000{c[1:]}USDT"
    if not c.isupper():
        return None
    return f"{c}USDT"


def short_address(address: str) -> str:
    a = (address or "").lower()
    return f"{a[:6]}…{a[-4:]}" if len(a) >= 12 else a


def _post(body: dict, timeout: int = 30, retries: int = 3) -> Any:
    data = json.dumps(body).encode()
    req = urllib.request.Request(INFO_URL, data=data, headers={
        "Content-Type": "application/json", "User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if 400 <= e.code < 500:
                return None
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(2 * (attempt + 1))
    return None


def info(body: dict) -> Any:
    """One info call. None when the venue would not answer."""
    return _post(body)


def clearinghouse_state(address: str) -> Optional[Dict[str, Any]]:
    return info({"type": "clearinghouseState", "user": address})


def user_fills(address: str) -> List[Dict[str, Any]]:
    out = info({"type": "userFills", "user": address})
    return out if isinstance(out, list) else []


def user_fills_since(address: str, days: int = 180, pause: float = 0.3,
                     max_pages: int = 20) -> List[Dict[str, Any]]:
    """Every fill in the last `days`, paged through `userFillsByTime`.

    `userFills` stops at the newest 2,000, which for a busy account is a
    week and says nothing about a record. This walks forward from
    now-days in pages of 2,000 (aggregated by order), and is what the
    selection reads. Deduplicated on the trade id.
    """
    end = int(time.time() * 1000)
    cur = end - days * 86_400_000
    out: List[Dict[str, Any]] = []
    for _ in range(max_pages):
        page = info({"type": "userFillsByTime", "user": address,
                     "startTime": cur, "endTime": end, "aggregateByTime": True})
        if not isinstance(page, list) or not page:
            break
        out.extend(page)
        last = max(int(x.get("time", 0)) for x in page)
        if len(page) < 2000 or last <= cur:
            break
        cur = last + 1
        time.sleep(pause)
    seen: set = set()
    uniq = []
    for x in out:
        tid = x.get("tid")
        if tid in seen:
            continue
        seen.add(tid)
        uniq.append(x)
    return uniq


def positions_from_state(state: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{coin: {size, side, entry, leverage, notional, upnl}} from a state.

    `szi` is signed: positive long, negative short. Notional is size x entry,
    the exchange's own `positionValue` when present.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not state:
        return out
    for ap in state.get("assetPositions", []) or []:
        p = ap.get("position") or {}
        try:
            size = float(p.get("szi", 0) or 0)
        except (TypeError, ValueError):
            continue
        if size == 0:
            continue
        entry = _f(p.get("entryPx"))
        lev = p.get("leverage") or {}
        out[str(p.get("coin", ""))] = {
            "size": abs(size),
            "side": "LONG" if size > 0 else "SHORT",
            "entry": entry,
            "leverage": _f(lev.get("value")) if isinstance(lev, dict) else _f(lev),
            "notional": _f(p.get("positionValue")) or (abs(size) * (entry or 0.0)),
            "upnl": _f(p.get("unrealizedPnl")),
        }
    return out


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- leaderboard
def _leaderboard_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "hyperliquid" / "leaderboard.json"


def fetch_leaderboard(cache_dir: Path = DEFAULT_CACHE, max_age_s: float = 6 * 3600,
                      timeout: int = 300) -> Optional[Path]:
    """Download the leaderboard to disk unless a fresh copy is there.

    Streamed to the file in chunks, never held whole in memory. Returns the
    path, or None when nothing usable is available (a stale file still counts
    as usable: yesterday's board beats no board).
    """
    path = _leaderboard_path(cache_dir)
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    req = urllib.request.Request(LEADERBOARD_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        # a truncated download is the failure mode seen in testing: the
        # closing bracket is the cheapest completeness check there is
        with open(tmp, "rb") as f:
            f.seek(max(0, tmp.stat().st_size - 64))
            tail = f.read()
        if b"]" not in tail:
            raise ValueError("leaderboard download truncated")
        tmp.replace(path)
        return path
    except Exception as e:
        log.warning("hyperliquid leaderboard fetch failed: %s", e)
        tmp.unlink(missing_ok=True)
        return path if path.exists() else None


def iter_leaderboard(path: Path) -> Iterator[Dict[str, Any]]:
    """Yield one row at a time from the leaderboard file.

    The file is `{"leaderboardRows": [ {...}, {...} ]}`. The text is read
    once (37MB) and decoded object by object with `raw_decode`, so memory is
    the text plus one row -- not 45,000 rows.
    """
    text = Path(path).read_text()
    dec = json.JSONDecoder()
    start = text.find("[")
    if start < 0:
        return
    i = start + 1
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == "]":
            return
        try:
            row, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            return
        yield row
        i = end


def performance(row: Dict[str, Any], window: str) -> Dict[str, float]:
    """{pnl, roi, vlm} for one window of a leaderboard row, zeros if absent."""
    for w, v in row.get("windowPerformances", []) or []:
        if w == window and isinstance(v, dict):
            return {"pnl": _f(v.get("pnl")) or 0.0,
                    "roi": _f(v.get("roi")) or 0.0,
                    "vlm": _f(v.get("vlm")) or 0.0}
    return {"pnl": 0.0, "roi": 0.0, "vlm": 0.0}
