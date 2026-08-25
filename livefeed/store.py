"""Append-only bar store for live collection.

The one rule this file exists to enforce: A BAR IS ONLY WRITTEN ONCE IT HAS
CLOSED.

In live mode the last row coming off any feed is the bar currently forming.
Its `close` is just the current price and it will keep changing until the
period ends. A backtest never sees such a bar, so if a live collector stores
it, every feature computed from that row differs between backtest and live -
and it looks like nothing is wrong. That divergence is the number the plan
calls the most important one in the project.

`marketdata/drop_unclosed()` existed for months and nothing called it. This
store calls it, and refuses the write rather than trusting the caller.

Layout: one CSV per month per symbol/interval, matching how the historical
archives are organised, so a month is easy to inspect, delete or re-fetch.

    data_cache/live/BTCUSDT/1h/2026-08.csv

Append-only, because a store you can overwrite is a store where a later
correction silently rewrites history - and then your backtest trains on a
version of the past nobody actually had.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from core import utc_now

from marketdata.binance import KLINE_COLUMNS, add_derived_columns

DEFAULT_LIVE_DIR = Path(__file__).resolve().parent.parent / "data_cache" / "live"

# the columns we persist. `ignore` is dropped; everything else is kept,
# because re-downloading three years of history the day you want
# number_of_trades is a wasted day and the disk is free
STORE_COLUMNS = [c for c in KLINE_COLUMNS if c != "ignore"]


class BarStore:
    def __init__(self, symbol: str, interval: str,
                 directory: Path = DEFAULT_LIVE_DIR):
        self.symbol = symbol.upper()
        self.interval = interval
        self.dir = Path(directory) / self.symbol / interval
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- write
    def append(self, bars: pd.DataFrame,
               now: Optional[pd.Timestamp] = None) -> int:
        """Persist closed bars only. Returns how many were actually new.

        `bars` must be indexed by close_time (UTC). Anything whose close_time
        is still in the future is a forming bar and is silently skipped -
        not an error, just not ours yet.
        """
        if bars is None or bars.empty:
            return 0
        now = now or utc_now()

        df = bars.copy()
        if df.index.tz is None:
            raise ValueError("bar index must be timezone-aware UTC")

        # THE GUARD. a bar labelled 12:00-12:59:59.999 is only real once the
        # clock passes 12:59:59.999
        df = df[df.index <= now]
        if df.empty:
            return 0

        written = 0
        # group by calendar month so each file stays a manageable size.
        # strftime rather than to_period: converting a tz-aware index to a
        # period drops the timezone and warns about it every single write
        month = df.index.strftime("%Y-%m")
        for period, chunk in df.groupby(month):
            path = self.dir / f"{period}.csv"
            existing = self._read_file(path)
            known = set(existing.index) if existing is not None else set()

            fresh = chunk[~chunk.index.isin(known)]
            if fresh.empty:
                continue

            cols = [c for c in STORE_COLUMNS if c in fresh.columns]
            out = fresh[cols].copy()
            out.index.name = "close_time"
            header = existing is None
            out.to_csv(path, mode="a", header=header)
            written += len(out)
        return written

    # -------------------------------------------------------------- read
    def _read_file(self, path: Path) -> Optional[pd.DataFrame]:
        if not path.exists() or path.stat().st_size == 0:
            return None
        df = pd.read_csv(path, parse_dates=["close_time"])
        df["close_time"] = pd.to_datetime(df["close_time"], utc=True)
        # append-only files can pick up a duplicate if a write was retried;
        # last write wins on read rather than corrupting the frame
        return df.drop_duplicates("close_time", keep="last").set_index("close_time")

    def load(self, start: Optional[str] = None,
             end: Optional[str] = None, derived: bool = True) -> pd.DataFrame:
        """Everything stored, sorted, deduped, ready for the agents."""
        files = sorted(self.dir.glob("*.csv"))
        frames = [f for f in (self._read_file(p) for p in files) if f is not None]
        if not frames:
            return pd.DataFrame(columns=STORE_COLUMNS[1:],
                                index=pd.DatetimeIndex([], tz="UTC",
                                                       name="close_time"))
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        if start:
            df = df[df.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return add_derived_columns(df) if derived else df

    def last_close_time(self) -> Optional[pd.Timestamp]:
        """Newest stored bar, or None. The anchor for gap detection."""
        files = sorted(self.dir.glob("*.csv"))
        if not files:
            return None
        last = self._read_file(files[-1])
        return None if last is None or last.empty else last.index.max()

    def count(self) -> int:
        return len(self.load(derived=False))

    # -------------------------------------------------------------- gaps
    def find_gaps(self, expected_delta: pd.Timedelta) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
        """Holes in the stored history, as (after, before) pairs.

        A websocket drops roughly once a day by design. Without gap detection
        the holes are invisible for months, and every bar-count feature
        (`bars_since_*`) then quietly misreports elapsed time.
        """
        df = self.load(derived=False)
        if len(df) < 2:
            return []
        idx = df.index
        deltas = idx[1:] - idx[:-1]
        # tolerate a small amount of clock jitter around the exact interval
        bad = deltas > expected_delta * 1.5
        return [(idx[i], idx[i + 1]) for i in np.flatnonzero(bad)]

    def status(self, expected_delta: Optional[pd.Timedelta] = None) -> str:
        df = self.load(derived=False)
        if df.empty:
            return f"{self.symbol} {self.interval}: empty"
        lines = [f"{self.symbol} {self.interval}: {len(df):,} bars",
                 f"  {df.index[0]} -> {df.index[-1]}"]
        if expected_delta is not None:
            gaps = self.find_gaps(expected_delta)
            if gaps:
                missing = sum(int((b - a) / expected_delta) - 1 for a, b in gaps)
                lines.append(f"  {len(gaps)} gaps, ~{missing} bars missing")
                for a, b in gaps[:3]:
                    lines.append(f"    {a} -> {b}")
            else:
                lines.append("  no gaps")
        return "\n".join(lines)
