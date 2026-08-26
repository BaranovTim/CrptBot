"""Positioning data: open interest and liquidations.

The reason the plan chose USD-M perpetuals over spot. Spot has no funding
rate, no open interest and no liquidations — which removes the answer to
"was positioning crowded before this move?", and that question is most of
what separates a cascade from real flow.

Open interest comes from the free daily ``metrics`` dumps. Liquidations are
best-effort: Binance restricted the historical liquidation feed, so the
loader returns empty rather than pretending. Coinglass is the usual
workaround, but its numbers are estimated rather than exhaustive — worth
knowing before you build a feature on them.

Funding rate is deliberately NOT here. It belongs to the regime block, and
mixing it in would make the ablation unable to tell whether a lift came from
order flow or from positioning cost.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from core import utc_now

from .binance import DEFAULT_CACHE, VISION_BASE, _days, _download, _to_utc

METRIC_COLUMNS = [
    "create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
    "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
    "count_long_short_ratio", "sum_taker_long_short_vol_ratio",
]


def _read_csv_zip(path: Path, names: List[str]) -> Optional[pd.DataFrame]:
    try:
        with zipfile.ZipFile(path) as z:
            raw = z.read(z.namelist()[0])
    except (zipfile.BadZipFile, IndexError):
        return None
    first = raw.split(b"\n", 1)[0].decode("utf-8", "ignore").lower()
    header = 0 if any(n.split("_")[0] in first for n in names[:2]) else None
    df = pd.read_csv(io.BytesIO(raw), header=header)
    if header is None:
        df.columns = names[: len(df.columns)]
    else:
        df.columns = [c.strip().lower() for c in df.columns]
    return df


def load_open_interest(
    symbol: str = "BTCUSDT",
    start: str = "2026-05-01",
    end: Optional[str] = None,
    cache_dir: Path = DEFAULT_CACHE,
) -> pd.DataFrame:
    """Open interest history from the daily metrics dumps (5-minute samples).

    Returns a frame indexed UTC with ``open_interest`` (contracts) and
    ``open_interest_usd`` (notional). Empty when nothing is published.
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else utc_now()
    sym = symbol.upper()
    frames = []

    for day in _days(start_ts, end_ts):
        stem = f"{sym}-metrics-{day:%Y-%m-%d}"
        url = f"{VISION_BASE}/futures/um/daily/metrics/{sym}/{stem}.zip"
        dest = Path(cache_dir) / "futures/um" / "metrics" / sym / f"{stem}.zip"
        if not _download(url, dest):
            continue
        df = _read_csv_zip(dest, METRIC_COLUMNS)
        if df is None or "sum_open_interest" not in df.columns:
            continue
        frames.append(pd.DataFrame({
            "time": _to_utc(df["create_time"]) if pd.api.types.is_numeric_dtype(
                df["create_time"]) else pd.to_datetime(df["create_time"], utc=True),
            "open_interest": pd.to_numeric(df["sum_open_interest"], errors="coerce"),
            "open_interest_usd": pd.to_numeric(df["sum_open_interest_value"],
                                               errors="coerce"),
        }))

    if not frames:
        return pd.DataFrame(columns=["open_interest", "open_interest_usd"],
                            index=pd.DatetimeIndex([], tz="UTC", name="time"))
    out = pd.concat(frames).dropna(subset=["time"]).sort_values("time")
    return out.drop_duplicates("time", keep="last").set_index("time")


def load_liquidations(
    symbol: str = "BTCUSDT",
    start: str = "2026-05-01",
    end: Optional[str] = None,
    cache_dir: Path = DEFAULT_CACHE,
    give_up_after: int = 14,
) -> pd.DataFrame:
    """Forced liquidations. Best-effort — the historical feed is restricted.

    An empty frame is the expected outcome on most ranges, not a failure. The
    honest response is NaN liquidation features and a coverage column that
    says so, rather than a zero that reads as "no liquidations happened".
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else utc_now()
    sym = symbol.upper()
    names = ["time", "symbol", "side", "order_type", "time_in_force",
             "original_quantity", "price", "average_price", "order_status",
             "last_fill_quantity", "accumulated_fill_quantity"]
    frames = []

    # Binance restricted this feed, so on most ranges EVERY day 404s. Without
    # an early exit that is ~1300 pointless HTTP round trips for a three-year
    # range - measured at over ten minutes of a training run spent waiting for
    # a feed that returns nothing. If the first `give_up_after` consecutive
    # days are all missing, the feed is not published for this range; stop.
    misses = 0
    for day in _days(start_ts, end_ts):
        stem = f"{sym}-liquidationSnapshot-{day:%Y-%m-%d}"
        url = f"{VISION_BASE}/futures/um/daily/liquidationSnapshot/{sym}/{stem}.zip"
        dest = Path(cache_dir) / "futures/um" / "liquidations" / sym / f"{stem}.zip"
        if not _download(url, dest):
            misses += 1
            if misses >= give_up_after and not frames:
                # nothing has ever downloaded and the streak is long: the feed
                # is not available here. an empty frame is the honest answer,
                # and Agent 4 reports it as missing rather than as "no
                # liquidations happened"
                return pd.DataFrame(
                    columns=["long_liquidated", "short_liquidated"],
                    index=pd.DatetimeIndex([], tz="UTC", name="time"))
            continue
        misses = 0
        df = _read_csv_zip(dest, names)
        if df is None or "side" not in df.columns:
            continue
        ts = _to_utc(df["time"]) if pd.api.types.is_numeric_dtype(df["time"]) \
            else pd.to_datetime(df["time"], utc=True)
        price = pd.to_numeric(df.get("average_price", df.get("price")), errors="coerce")
        qty = pd.to_numeric(df.get("accumulated_fill_quantity",
                                   df.get("original_quantity")), errors="coerce")
        frames.append(pd.DataFrame({
            "time": ts,
            # A liquidation order with side=SELL is a forced sell: a LONG was
            # liquidated. The naming trips people up in the same way
            # is_buyer_maker does.
            "long_liquidated": np.where(
                df["side"].astype(str).str.upper() == "SELL", price * qty, 0.0),
            "short_liquidated": np.where(
                df["side"].astype(str).str.upper() == "BUY", price * qty, 0.0),
        }))

    if not frames:
        return pd.DataFrame(columns=["long_liquidated", "short_liquidated"],
                            index=pd.DatetimeIndex([], tz="UTC", name="time"))
    return pd.concat(frames).dropna(subset=["time"]).sort_values("time").set_index("time")


def resample_to_bars(df: pd.DataFrame, bars_index: pd.Index, how: str = "last") -> pd.DataFrame:
    """Align an irregular series onto the bar index, causally.

    ``merge_asof`` backward: each bar sees the most recent observation at or
    before its close, never one from inside the bar's future. Same guard as
    the higher-timeframe joins in Agents 1 and 2.
    """
    if df is None or df.empty:
        return pd.DataFrame(index=bars_index, columns=df.columns if df is not None else [])
    if how == "sum":
        binned = df.groupby(pd.Index(df.index).ceil(
            pd.infer_freq(bars_index) or (bars_index[1] - bars_index[0]))).sum()
        return binned.reindex(bars_index).fillna(0.0)
    left = pd.DataFrame({"_t": bars_index})
    right = df.reset_index().rename(columns={df.index.name or "index": "_src"})
    merged = pd.merge_asof(left, right, left_on="_t", right_on="_src",
                           direction="backward")
    out = merged[list(df.columns)]
    out.index = bars_index
    return out
