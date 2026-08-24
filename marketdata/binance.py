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
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd

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


def _download(url: str, dest: Path, retries: int = 3) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                payload = r.read()
            dest.write_bytes(payload)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False          # month not published (yet) — expected
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return False


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
    end_ts = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.utcnow().tz_localize("UTC")
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
    now = now or pd.Timestamp.utcnow().tz_localize("UTC")
    return df[df.index <= now]
