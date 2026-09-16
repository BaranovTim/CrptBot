"""Funding-rate history for USD-M perpetuals.

The regime block has carried a `funding_z` column since the plan was written
and nothing has ever filled it: `compute_frames` never loaded a funding
series, so every model shipped with that column NaN. This is the loader.

WHY IT IS ITS OWN MODULE
    `derivatives.py` is order flow and positioning; funding is the cost of
    holding a position, and the ablation needs to tell those apart. Keeping
    the loaders separate keeps the blocks separate.

SOURCE
    `GET /fapi/v1/fundingRate` -- settled rates, one row per settlement
    (every 8 hours on Binance), up to 1,000 rows per call. Three years is
    ~3,300 rows, so four calls per symbol; the dumps on data.binance.vision
    carry the premium index, not the settled rate, and the settled rate is
    the number a position actually pays.

CACHE
    One CSV per symbol under `futures/um/funding/`, extended in place: a
    reload asks the API only for what came after the last settlement on
    disk. Rates are stored as fractions per settlement (0.0001 = 0.01%),
    exactly as the exchange reports them.

CAUSALITY
    A row's `funding_time` is when the payment settled. Aligning to bars
    with a backward `asof` on close_time means a bar sees only settlements
    at or before its close -- the rate is known ~8h ahead in fact, but this
    is the conservative reading and it costs nothing.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd

from core import utc_now

from .binance import DEFAULT_CACHE, FAPI_BASE

PAGE = 1000
SETTLEMENT = pd.Timedelta(hours=8)


def _cache_path(symbol: str, cache_dir: Path) -> Path:
    return Path(cache_dir) / "futures/um/funding" / f"{symbol.upper()}.csv"


def _fetch_page(symbol: str, start_ms: int, end_ms: Optional[int] = None,
                retries: int = 3) -> list:
    params = [f"symbol={symbol.upper()}", f"startTime={start_ms}", f"limit={PAGE}"]
    if end_ms is not None:
        params.append(f"endTime={end_ms}")
    url = f"{FAPI_BASE}/fapi/v1/fundingRate?" + "&".join(params)
    req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/funding"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                used = r.headers.get("X-MBX-USED-WEIGHT-1M")
                if used and int(used) > 2000:
                    time.sleep(10)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                time.sleep(30 * (attempt + 1))
                continue
            if e.code == 400:
                return []
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(2 * (attempt + 1))
    return []


def _read_cache(path: Path) -> pd.Series:
    if not path.exists():
        return pd.Series(dtype=float, name="funding_rate",
                         index=pd.DatetimeIndex([], tz="UTC", name="funding_time"))
    df = pd.read_csv(path)
    idx = pd.to_datetime(df["funding_time"], utc=True)
    return pd.Series(df["funding_rate"].astype(float).to_numpy(), index=idx,
                     name="funding_rate").sort_index()


def load_funding(symbol: str = "BTCUSDT", start: str = "2023-01-01",
                 end: Optional[str] = None, cache_dir: Path = DEFAULT_CACHE,
                 refresh: bool = True) -> pd.Series:
    """Settled funding rates, one per settlement, as a fraction of notional.

    Indexed by settlement time (UTC). Empty when the symbol has no perpetual
    or the exchange returns nothing. `refresh=False` reads the cache only,
    for tests and for hosts without network access.
    """
    path = _cache_path(symbol, cache_dir)
    have = _read_cache(path)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else utc_now()

    if refresh:
        # only what is missing: from the last settlement on disk (or `start`)
        # forward. the API returns rows with funding_time >= startTime
        cursor = (int((have.index[-1] + pd.Timedelta(seconds=1)).timestamp() * 1000)
                  if len(have) else int(start_ts.timestamp() * 1000))
        stop = int(end_ts.timestamp() * 1000)
        rows = []
        while cursor < stop:
            page = _fetch_page(symbol, cursor, stop)
            if not page:
                break
            rows.extend(page)
            last = int(page[-1]["fundingTime"])
            if len(page) < PAGE or last <= cursor:
                break
            cursor = last + 1
        if rows:
            new = pd.Series(
                [float(r["fundingRate"]) for r in rows],
                index=pd.to_datetime([int(r["fundingTime"]) for r in rows],
                                     unit="ms", utc=True),
                name="funding_rate")
            have = pd.concat([have, new])
            have = have[~have.index.duplicated(keep="last")].sort_index()
            path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"funding_time": have.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "funding_rate": have.to_numpy()}).to_csv(path, index=False)

    have.index.name = "funding_time"
    return have[(have.index >= start_ts) & (have.index <= end_ts)]


def align_to_bars(rates: pd.Series, bars_index: pd.DatetimeIndex) -> pd.Series:
    """The last settled rate at or before each bar's close. NaN before the
    first settlement, never a rate from the future."""
    if rates.empty:
        return pd.Series(float("nan"), index=bars_index, name="funding_rate")
    frame = pd.DataFrame({"funding_rate": rates.to_numpy()},
                         index=rates.index).sort_index()
    out = pd.merge_asof(pd.DataFrame(index=bars_index).reset_index(names="t"),
                        frame.reset_index(names="t"), on="t", direction="backward")
    return pd.Series(out["funding_rate"].to_numpy(), index=bars_index,
                     name="funding_rate")
