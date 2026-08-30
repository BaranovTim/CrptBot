"""Per-bar trade-tape aggregates, kept on disk.

WHY THIS EXISTS
    Agent 4 declares 22 features and, measured on 6,000 real bars, 20 of them
    were entirely NaN. Only `taker_buy_ratio` carried data, because that one
    is derivable from klines. Everything the agent was actually written for —
    order-flow imbalance, CVD slope, aggressor imbalance, large prints,
    average trade size — needs the TRADE TAPE, and nothing was ever supplying
    it. A quarter of the judge's 88 columns were empty.

    `marketdata.aggtrades.load_tape_bars` could already download and reduce
    the tape. It was never called.

WHY THE RAW ARCHIVES ARE NOT KEPT
    A single day of BTCUSDT futures aggTrades is 4-54 MB compressed. Four
    symbols over the 1h training window is on the order of 100 GB, which fits
    on neither the droplet's 25 GB disk nor comfortably on a laptop.

    The aggregate is what has value: one row per bar, ~60 columns. Two days of
    1h bars reduce to 48 rows. So each daily archive is downloaded, reduced,
    and DELETED, and only the reduction is kept. Re-running is cheap because
    the reduction is what gets cached, not the source.

WHY IT IS THE SAME STORE FOR BACKFILL AND LIVE
    Inference does not need the last bar's tape, it needs the whole live
    window — Agent 4's rolling baselines span hundreds of bars. If history
    came from archives and live came from somewhere else with a different
    shape, the model would see one distribution while training and another
    while serving. Both write here, in the same columns.

    The archives lag by roughly a day, so the live collector fills the end.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import pandas as pd

from core import utc_now

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("data_cache") / "tape"


class TapeStore:
    """Per-bar tape aggregates for one symbol and interval."""

    def __init__(self, symbol: str, interval: str,
                 directory: Path = DEFAULT_DIR):
        self.symbol = symbol.upper()
        self.interval = interval
        self.dir = Path(directory) / self.symbol / interval
        self.dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- read
    def load(self, since: Optional[pd.Timestamp] = None) -> pd.DataFrame:
        """Everything stored, oldest first, indexed by close_time (UTC)."""
        files = sorted(self.dir.glob("*.csv"))
        if since is not None:
            # month files are named YYYY-MM, so anything ending before the
            # month `since` falls in cannot contain a row we want
            cutoff = f"{since:%Y-%m}"
            files = [f for f in files if f.stem >= cutoff]
        frames: List[pd.DataFrame] = []
        for f in files:
            try:
                df = pd.read_csv(f, index_col=0, parse_dates=[0])
                frames.append(df)
            except (ValueError, OSError) as e:
                # one bad month must not lose the rest of the history
                log.warning("tape %s unreadable: %s", f.name, e)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames).sort_index()
        out = out[~out.index.duplicated(keep="last")]
        if out.index.tz is None:
            out.index = out.index.tz_localize("UTC")
        out.index.name = "close_time"
        if since is not None:
            out = out[out.index >= since]
        return out

    def covered_days(self) -> set:
        """Which UTC dates already have at least one aggregated bar.

        Backfill consults this so a re-run skips days it has already reduced
        rather than re-downloading tens of megabytes to produce rows that are
        already on disk.
        """
        have = self.load()
        if have.empty:
            return set()
        return set(have.index.tz_convert("UTC").date)

    # ------------------------------------------------------------- write
    def append(self, frame: pd.DataFrame) -> int:
        """Merge rows in, one file per month. Returns rows actually added."""
        if frame is None or frame.empty:
            return 0
        df = frame.copy()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df = df.sort_index()

        added = 0
        for month, chunk in df.groupby(df.index.strftime("%Y-%m")):
            path = self.dir / f"{month}.csv"
            if path.exists():
                try:
                    old = pd.read_csv(path, index_col=0, parse_dates=[0])
                    if old.index.tz is None:
                        old.index = old.index.tz_localize("UTC")
                except (ValueError, OSError):
                    old = pd.DataFrame()
            else:
                old = pd.DataFrame()

            before = len(old)
            merged = pd.concat([old, chunk]) if not old.empty else chunk
            # last wins: a re-reduced day is more trustworthy than a partial
            # one written live before the bar had finished
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            tmp = path.with_suffix(".tmp")
            merged.to_csv(tmp)
            tmp.replace(path)
            added += max(0, len(merged) - before)
        return added


def backfill_tape(symbol: str, interval: str, bars_index: pd.DatetimeIndex,
                  start: str, end: Optional[str] = None,
                  directory: Path = DEFAULT_DIR,
                  keep_archives: bool = False) -> int:
    """Reduce the aggTrades archives for a range into the store.

    Day by day, so memory stays flat and an interruption loses at most one
    day's work. Each archive is deleted once reduced unless `keep_archives`,
    because the reduction is 3-4 orders of magnitude smaller than the source.
    """
    from marketdata.aggtrades import (DEFAULT_CACHE, _aggregate_chunk,
                                      _download, _read_agg_zip)

    store = TapeStore(symbol, interval, directory)
    done = store.covered_days()
    sym = symbol.upper()
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else utc_now()

    total = 0
    day = start_ts.normalize()
    while day <= end_ts:
        if day.date() in done:
            day += pd.Timedelta(days=1)
            continue
        stem = f"{sym}-aggTrades-{day:%Y-%m-%d}"
        url = (f"https://data.binance.vision/data/futures/um/daily/aggTrades/"
               f"{sym}/{stem}.zip")
        dest = Path(DEFAULT_CACHE) / "futures/um" / "aggTrades" / sym / f"{stem}.zip"
        if _download(url, dest, timeout=600):
            try:
                chunk = _aggregate_chunk(_read_agg_zip(dest),
                                         pd.DatetimeIndex(bars_index))
                total += store.append(chunk)
            except Exception as e:
                log.warning("tape %s %s: %s", sym, day.date(), e)
            finally:
                if not keep_archives:
                    try:
                        dest.unlink()
                    except OSError:
                        pass
        day += pd.Timedelta(days=1)
    return total
