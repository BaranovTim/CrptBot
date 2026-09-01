"""US equity bars from Alpaca.

WHY ALPACA AND NOT THE OBVIOUS FREE ALTERNATIVES
    A screener has to refresh the whole tradable universe — roughly five
    thousand tickers — every day. That is a BULK problem, and it is where the
    other free tiers die: Tiingo allows 500 unique symbols per MONTH, Alpha
    Vantage 25 requests per day, Polygon five calls per minute. Alpaca takes a
    comma-separated symbol list with no documented cap, so the universe is
    about fifty requests rather than five thousand.

    yfinance would also work technically. It is excluded on licensing: Yahoo's
    terms forbid redistributing their data through a product you charge for,
    which is the same reason this project does not scrape TradingView.

THE TWO DEFAULTS THAT WOULD HAVE BEEN SILENTLY WRONG
    Both produce numbers that render perfectly and mean nothing, which is the
    failure this codebase is least able to notice. They are set here, once, so
    that no call site can forget them.

    adjustment  Defaults to `raw` — no split or dividend adjustment. A 4:1
                split then reads as a 75% single-day crash. SMA200, RSI(14)
                and the 50-day high are all corrupted for months afterwards,
                and a model trained on it learns a price collapse that never
                happened. Always `all`.

    feed        Alpaca's own docs disagree with themselves: the API reference
                says the default is `sip` (all US exchanges), the FAQ says
                historical endpoints default to "the best available feed based
                on the user's subscription" — which on a free account means
                IEX. IEX is about 2.5% of US volume: in Alpaca's own example
                one Apple session had 12,630 trades on IEX against 535,136
                consolidated. Every volume filter in the screener would come
                out roughly fortyfold too small. Never inherited, always sent.

THE FIFTEEN MINUTE RULE
    The free plan gets the full SIP consolidated tape for historical queries,
    on one condition, quoted from the FAQ: "the `end` parameter must be at
    least 15 minutes old to query SIP data without a subscription."

    So `end` is clamped, here, for every caller. A screener never wants the
    last fifteen minutes anyway — but a request that accidentally does gets
    rejected or silently downgraded to IEX, and the downgrade is the bad one.

RATE LIMIT
    200 requests/minute on the free plan — NOT the 10,000/min figure that
    describes the paid tier. With multi-symbol requests that is ample for a
    daily refresh; it is not ample for per-symbol loops, so this module does
    not offer one.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import pandas as pd

log = logging.getLogger(__name__)

DATA_HOST = "https://data.alpaca.markets"

# TWO trading hosts, tried in order.
#
# Market data does not care which kind of key you hold — `data.alpaca.markets`
# accepts paper and live alike. The TRADING API does care, and `/v2/assets`
# lives there: a paper key sent to the live host comes back
# `401 request is not authorized`, which reads exactly like a bad key and sent
# me looking at the credentials instead of the URL.
#
# Paper is tried FIRST because a free account is the expected case here and a
# paper key is what it issues. The asset list is identical either way — it is
# the exchange's universe, not the account's.
TRADING_HOSTS = ("https://paper-api.alpaca.markets",
                 "https://api.alpaca.markets")

# Quoted from the Alpaca FAQ; see the module docstring. Thirty seconds of slack
# so a slow clock or a slow request cannot land inside the window.
SIP_DELAY = timedelta(minutes=15, seconds=30)

# Symbols per request. No documented cap, but a URL has practical limits and a
# failed 500-symbol request wastes far more than a failed 100-symbol one.
CHUNK = 100

# 200/min on the free plan. This paces well inside it rather than discovering
# the limit by being throttled.
MIN_INTERVAL = 0.35


class AlpacaError(RuntimeError):
    pass


def credentials() -> Optional[tuple]:
    """(key id, secret) from the environment, or None.

    Read from the environment and never from a file this repo controls, so the
    keys live where the Binance token already does and are never committed.
    """
    kid = os.environ.get("ALPACA_KEY_ID", "").strip()
    sec = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    return (kid, sec) if kid and sec else None


class Alpaca:
    """Thin, deliberately boring client. Bars and the tradable universe."""

    def __init__(self, key_id: Optional[str] = None,
                 secret: Optional[str] = None, timeout: int = 30):
        creds = credentials()
        self.key_id = key_id or (creds[0] if creds else "")
        self.secret = secret or (creds[1] if creds else "")
        self.timeout = timeout
        self._last_call = 0.0
        # whichever trading host answered, so the 401 probe happens once
        self._trading_host: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret)

    # ------------------------------------------------------------ plumbing
    def _get(self, host: str, path: str, params: Dict[str, str]) -> dict:
        if not self.configured:
            raise AlpacaError(
                "no Alpaca credentials. Set ALPACA_KEY_ID and "
                "ALPACA_SECRET_KEY in the environment — a free account at "
                "alpaca.markets is enough for everything this uses.")

        # pace, rather than find the rate limit by hitting it
        wait = MIN_INTERVAL - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

        url = f"{host}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret,
            "User-Agent": "ThusIldy/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            if e.code == 429:
                raise AlpacaError(f"rate limited (200/min on the free plan): {body}")
            raise AlpacaError(f"HTTP {e.code} on {path}: {body}") from e
        except (urllib.error.URLError, OSError) as e:
            raise AlpacaError(f"cannot reach Alpaca: {e}") from e

    @staticmethod
    def _safe_end(end: Optional[datetime]) -> datetime:
        """The latest instant a free plan may ask SIP for. See the docstring."""
        latest = datetime.now(timezone.utc) - SIP_DELAY
        if end is None:
            return latest
        end = end.astimezone(timezone.utc) if end.tzinfo else end.replace(
            tzinfo=timezone.utc)
        return min(end, latest)

    # ---------------------------------------------------------------- bars
    def bars(self, symbols: Iterable[str], timeframe: str = "1Day",
             start: Optional[datetime] = None,
             end: Optional[datetime] = None,
             limit: int = 10000) -> Dict[str, pd.DataFrame]:
        """Adjusted OHLCV per symbol, indexed by bar timestamp (UTC).

        Returns a dict rather than one frame: the callers all want per-symbol
        series, and a MultiIndex here would be unpacked at every one of them.
        """
        syms = [s.strip().upper() for s in symbols if s and s.strip()]
        if not syms:
            return {}

        start = start or (datetime.now(timezone.utc) - timedelta(days=400))
        params_base = {
            "timeframe": timeframe,
            "start": start.astimezone(timezone.utc).isoformat()
            if start.tzinfo else start.replace(tzinfo=timezone.utc).isoformat(),
            "end": self._safe_end(end).isoformat(),
            # see the module docstring; neither of these may be inherited
            "adjustment": "all",
            "feed": "sip",
            "limit": str(limit),
            "sort": "asc",
        }

        raw: Dict[str, List[dict]] = {}
        for i in range(0, len(syms), CHUNK):
            chunk = syms[i:i + CHUNK]
            page = None
            while True:
                params = dict(params_base, symbols=",".join(chunk))
                if page:
                    params["page_token"] = page
                payload = self._get(DATA_HOST, "/v2/stocks/bars", params)
                for sym, rows in (payload.get("bars") or {}).items():
                    raw.setdefault(sym, []).extend(rows or [])
                page = payload.get("next_page_token")
                if not page:
                    break

        out: Dict[str, pd.DataFrame] = {}
        for sym, rows in raw.items():
            if not rows:
                continue
            out[sym] = _frame(rows)
        return out

    # ------------------------------------------------------------ universe
    def universe(self, tradable_only: bool = True) -> List[Dict[str, str]]:
        """Every US equity Alpaca will quote.

        `status=active` alone still returns names that cannot be traded or
        quoted, so the tradable flag is checked too — a screener that offers a
        row you cannot act on is worse than one that does not list it.
        """
        rows = None
        errors = []
        hosts = ([self._trading_host] if self._trading_host
                 else list(TRADING_HOSTS))
        for host in hosts:
            try:
                rows = self._get(host, "/v2/assets",
                                 {"status": "active",
                                  "asset_class": "us_equity"})
                self._trading_host = host      # remember, do not re-probe
                break
            except AlpacaError as e:
                if "401" not in str(e):
                    raise                       # a real failure, not the host
                errors.append(f"{host}: {e}")
        if rows is None:
            raise AlpacaError(
                "neither trading host accepted the key — this is the key "
                "itself, not the host:\n  " + "\n  ".join(errors))
        out = []
        for a in rows if isinstance(rows, list) else []:
            if tradable_only and not a.get("tradable"):
                continue
            out.append({
                "symbol": a.get("symbol", ""),
                "name": a.get("name", ""),
                "exchange": a.get("exchange", ""),
                # carried because the screener's own filters cannot infer it,
                # and a fractionable, easily-shorted name is a different
                # proposition from one that is neither
                "shortable": "1" if a.get("shortable") else "0",
                "easy_to_borrow": "1" if a.get("easy_to_borrow") else "0",
            })
        return out


def _frame(rows: List[dict]) -> pd.DataFrame:
    """Alpaca's short keys into the column names the rest of this repo uses."""
    df = pd.DataFrame(rows)
    df = df.rename(columns={"t": "time", "o": "open", "h": "high",
                            "l": "low", "c": "close", "v": "volume",
                            "n": "trades", "vw": "vwap"})
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    df = df.set_index("time").sort_index()
    # A duplicate timestamp means the same bar arrived twice across a page
    # boundary. Keeping both would double a day's volume, which is exactly the
    # kind of quiet corruption the relative-volume filter would then report as
    # a signal.
    df = df[~df.index.duplicated(keep="last")]
    for c in ("open", "high", "low", "close", "volume", "trades", "vwap"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df
