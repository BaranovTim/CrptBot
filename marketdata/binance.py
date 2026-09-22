"""Binance USD-M perpetual klines.

Three jobs, three tools, deliberately not unified:

  * bulk history  -> data.binance.vision   (free, no key, no rate limit,
                                            a month per file)
  * live stream   -> websocket             (not in this file; Agent 1 does
                                            not need it for research)
  * gap repair    -> REST /fapi/v1/klines  (only for holes left by a dropped
                                            collector)

Perpetuals rather than spot, because spot has no funding rate, no open
interest and no liquidations — half the features planned for Agent 4 and the
regime block.  Agent 1 itself only needs OHLC, but the storage layer should
not have to be rebuilt later.

All eleven useful columns are stored even though Agent 1 reads five of them.
Re-downloading three years of history the day you want ``number_of_trades``
is a wasted day; the disk is free.

Two traps this module handles for you:

  * Spot files from 2025-01-01 onward carry MICROSECOND timestamps while
    everything before is milliseconds.  Parsing naively with unit="ms" gives
    dates in the year 57000 without raising.  Unit is detected per file.
  * Bars are indexed by ``close_time``.  A bar labelled 12:00 covers
    12:00-12:59:59, and its close is unknowable until 13:00.  Index by
    open_time and you have handed yourself an hour of free foresight.
"""
from __future__ import annotations

import io
import json
import shutil
import socket
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd

from core import utc_now

VISION_BASE = "https://data.binance.vision/data"
FAPI_BASE = "https://fapi.binance.com"

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "number_of_trades", "taker_buy_base_volume",
    "taker_buy_quote_volume", "ignore",
]
NUMERIC = [
    "open", "high", "low", "close", "volume", "quote_volume",
    "number_of_trades", "taker_buy_base_volume", "taker_buy_quote_volume",
]

DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "data_cache"


def _to_utc(series: pd.Series) -> pd.Series:
    """Parse epoch timestamps, detecting ms vs µs per file rather than assuming."""
    v = pd.to_numeric(series, errors="coerce")
    unit = "us" if v.dropna().max() > 1e14 else "ms"
    return pd.to_datetime(v, unit=unit, utc=True)


def _missing_marker(dest: Path) -> Path:
    """Where a remembered 404 for `dest` is recorded."""
    return dest.with_suffix(dest.suffix + ".missing")


def _known_missing(dest: Path, miss_ttl: Optional[float]) -> bool:
    """Has this file already been found absent, recently enough to trust?

    `miss_ttl` is None for "a day that is closed: if the archive is not
    published now it never will be", and a number of seconds for a day
    still being written.
    """
    m = _missing_marker(dest)
    try:
        age = time.time() - m.stat().st_mtime
    except OSError:
        return False
    return miss_ttl is None or age < miss_ttl


def _download(url: str, dest: Path, retries: int = 3, timeout: int = 60,
              miss_ttl: Optional[float] = 0.0) -> bool:
    """Fetch to ``dest``, streaming through a .part file.

    Streaming rather than ``r.read()`` matters once aggTrades enter the
    picture: a daily tape archive is hundreds of megabytes, and reading it
    whole both wastes memory and makes the socket timeout apply to the entire
    transfer instead of to each chunk.

    The .part rename is what keeps the cache trustworthy — an interrupted
    download leaves no file, so a later run retries cleanly instead of
    parsing a truncated zip.

    A 404 IS REMEMBERED, and that is the difference between a fast serve and
    a slow one. These archives are one file per day and a missing day stays
    missing: DOGE's bars start 2021-01 while its open-interest archive starts
    2021-12, so 334 days of it can never exist. Without a marker every
    dashboard build asked for all 334 again, plus fourteen liquidation days
    Binance stopped publishing years ago — around 350 doomed round trips
    before a single feature was computed, on every rebuild of every pair.

    `miss_ttl` says how long to trust the marker: None for a day that has
    closed (the archive is published or it never will be), seconds for a
    recent day that may still appear. The default 0 means "do not trust it
    at all", so every existing caller behaves exactly as it did.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return True
    if _known_missing(dest, miss_ttl):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                with tmp.open("wb") as fh:
                    shutil.copyfileobj(r, fh, length=1 << 20)
            tmp.replace(dest)
            return True
        except urllib.error.HTTPError as e:
            tmp.unlink(missing_ok=True)
            if e.code == 404:
                # not published (yet) — expected, and worth remembering
                try:
                    _missing_marker(dest).touch()
                except OSError:
                    pass
                return False
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            tmp.unlink(missing_ok=True)
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return False


def prefetch(jobs, workers: int = 8, miss_ttl: Optional[float] = 0.0) -> int:
    """Warm the on-disk cache for many files at once. Returns how many landed.

    WHY THIS EXISTS
        `load_open_interest` needs one zip PER DAY, so a three-year range is
        ~1,300 files fetched one after another. Measured against
        data.binance.vision: 0.83s each, essentially all of it waiting on the
        socket — 4 minutes of CPU inside 40 minutes of wall clock for a single
        training run. Seeding ten new pairs that way is most of a day spent
        idle.

    WHY IT IS A SEPARATE PASS AND NOT A REWRITE OF THE LOOPS
        Every caller keeps its existing sequential loop untouched. This runs
        first and fills the cache; the loop then finds each file already on
        disk and returns immediately from `_download`'s existence check. So
        the parsing, the ordering of the frames, the 404 handling and the
        early-exit rules are all exactly as they were — the only thing that
        changed is that the bytes arrived earlier. A failure here costs
        nothing either: the file is simply fetched by the loop, as before.

    SAFE TO THREAD because each job writes its OWN destination through a
    `.part` file and a rename. No two jobs share a path, and nothing here
    mutates module state.

    `jobs` is an iterable of (url, dest). Eight workers against a static file
    CDN — this is data.binance.vision, not the trading API, so there is no
    request-weight budget to blow.
    """
    from concurrent.futures import ThreadPoolExecutor

    jobs = [(u, d) for u, d in jobs
            if not (d.exists() and d.stat().st_size > 0)
            and not _known_missing(d, miss_ttl)]
    if not jobs:
        return 0

    def one(job) -> bool:
        url, dest = job
        try:
            return _download(url, dest, miss_ttl=miss_ttl)
        except Exception:
            # Swallowed on purpose. This is a cache warm-up: anything it fails
            # to get, the caller's own loop will try again and handle in the
            # way it already handles it.
            return False

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return sum(1 for ok in pool.map(one, jobs) if ok)


def _read_zip(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        raw = z.read(name)
    # Newer archives ship a header row; older ones do not.
    first = raw.split(b"\n", 1)[0].decode("utf-8", "ignore")
    header = 0 if "open_time" in first or "open" in first.lower() else None
    df = pd.read_csv(io.BytesIO(raw), header=header, names=None if header == 0 else KLINE_COLUMNS)
    if header == 0:
        df.columns = KLINE_COLUMNS[: len(df.columns)]
    return df


def _months(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[pd.Timestamp]:
    cur = start.tz_convert("UTC").normalize().replace(day=1)
    last = end.tz_convert("UTC").normalize().replace(day=1)
    while cur <= last:
        yield cur
        cur = (cur + pd.Timedelta(days=32)).replace(day=1)


def _days(start: pd.Timestamp, end: pd.Timestamp) -> Iterable[pd.Timestamp]:
    cur = start.tz_convert("UTC").normalize()
    while cur <= end:
        yield cur
        cur += pd.Timedelta(days=1)


def load_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    start: str = "2023-01-01",
    end: Optional[str] = None,
    market: str = "futures/um",
    cache_dir: Path = DEFAULT_CACHE,
) -> pd.DataFrame:
    """Bulk history from data.binance.vision, cached on disk.

    Monthly archives where available, daily for the current partial month.
    Returns a frame indexed by ``close_time`` (UTC).
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else utc_now()
    cache_dir = Path(cache_dir)
    sym = symbol.upper()
    frames: List[pd.DataFrame] = []

    for month in _months(start_ts, end_ts):
        stem = f"{sym}-{interval}-{month:%Y-%m}"
        url = f"{VISION_BASE}/{market}/monthly/klines/{sym}/{interval}/{stem}.zip"
        dest = cache_dir / market / "monthly" / sym / interval / f"{stem}.zip"
        if _download(url, dest):
            frames.append(_read_zip(dest))
            continue
        # No monthly archive: fall back to per-day files for that month.
        m_end = min(end_ts, (month + pd.Timedelta(days=32)).replace(day=1) - pd.Timedelta(days=1))
        for day in _days(max(month, start_ts.normalize()), m_end):
            d_stem = f"{sym}-{interval}-{day:%Y-%m-%d}"
            d_url = f"{VISION_BASE}/{market}/daily/klines/{sym}/{interval}/{d_stem}.zip"
            d_dest = cache_dir / market / "daily" / sym / interval / f"{d_stem}.zip"
            if _download(d_url, d_dest):
                frames.append(_read_zip(d_dest))

    if not frames:
        raise RuntimeError(
            f"No data found for {sym} {interval} between {start_ts.date()} and "
            f"{end_ts.date()}. Check the symbol, or that {market!r} is the right "
            f"market path (spot vs futures/um)."
        )

    df = pd.concat(frames, ignore_index=True)
    return _finalise(df, start_ts, end_ts)


def fetch_klines_rest(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 1500,
) -> pd.DataFrame:
    """Patch a hole via REST. Not for bulk history.

    Three years of minutes is ~1.5M bars: over a thousand paginated requests
    against a weight budget, versus a few zip files. Use this only to fill
    the gap left by a collector that fell over.
    """
    params = [f"symbol={symbol.upper()}", f"interval={interval}", f"limit={min(limit, 1500)}"]
    if start:
        params.append(f"startTime={int(pd.Timestamp(start, tz='UTC').timestamp() * 1000)}")
    if end:
        params.append(f"endTime={int(pd.Timestamp(end, tz='UTC').timestamp() * 1000)}")
    url = f"{FAPI_BASE}/fapi/v1/klines?" + "&".join(params)

    req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/agent1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        # Limits are weight-based, not request-count-based. Read the header and
        # throttle yourself; a 429 escalates to a 418 IP ban of growing length.
        used = r.headers.get("X-MBX-USED-WEIGHT-1M")
        if used and int(used) > 2000:
            time.sleep(10)
        payload = json.loads(r.read())

    df = pd.DataFrame(payload, columns=KLINE_COLUMNS)
    return _finalise(df, None, None)


def _finalise(df: pd.DataFrame, start_ts, end_ts) -> pd.DataFrame:
    df = df.copy()
    df["open_time"] = _to_utc(df["open_time"])
    df["close_time"] = _to_utc(df["close_time"])
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop(columns=["ignore"], errors="ignore")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.sort_values("close_time").drop_duplicates("close_time", keep="last")
    df = df.set_index("close_time")
    if start_ts is not None:
        df = df[df.index >= start_ts]
    if end_ts is not None:
        df = df[df.index <= end_ts]
    return add_derived_columns(df)


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Order flow that klines give away for free.

    ``quote_volume / volume`` is the sum of price*qty over the sum of qty —
    the bar's VWAP. Binance already computed it and just didn't name it.

    ``vwap_position`` then says WHERE inside the bar the volume actually
    traded. A bar closing on its low but with vwap_position 0.8 means the
    business happened up top and price was pushed down on thin volume — very
    different from a steady slide, and invisible in OHLC alone.
    """
    out = df.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        vol = out["volume"].replace(0, np.nan)
        rng = (out["high"] - out["low"]).replace(0, np.nan)
        out["vwap"] = out["quote_volume"] / vol
        out["vwap_position"] = (out["vwap"] - out["low"]) / rng
        out["taker_buy_ratio"] = out["taker_buy_base_volume"] / vol
        out["avg_trade_size_usd"] = out["quote_volume"] / out["number_of_trades"].replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def drop_unclosed(df: pd.DataFrame, now: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """Remove a bar that is still forming.

    In live mode the last row is the in-progress candle: its ``close`` is the
    current price and will keep changing. A backtest never sees such a bar,
    so reading it live is a direct source of backtest/live divergence.
    """
    now = now or utc_now()
    return df[df.index <= now]
