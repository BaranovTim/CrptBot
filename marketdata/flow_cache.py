"""The flow inputs a live read needs, kept ready instead of rebuilt.

WHY THIS EXISTS
    Agent 4's open-interest and liquidation inputs come from ONE ARCHIVE
    FILE PER DAY. A serving read asks for the whole live window, so a 4h
    dashboard parsed ~1,700 daily zips into half a million rows and threw
    the result away, every rebuild -- and there are sixty pairs rebuilding
    on a timer. The API was killed by the out-of-memory reaper nineteen
    times in a week and spent its life warming up again, which is what
    "the dashboard is slow" actually was.

    What the build needs is one number per BAR. That is small, it only
    changes when a bar closes, and it survives a restart if it is written
    down. So it is: `data_cache/flow_cache/SYMBOL_interval_kind.csv`,
    indexed by bar close.

WHY IT IS EXACT, NOT APPROXIMATE
    `resample_to_bars` is a backward `merge_asof`: a bar takes the most
    recent observation at or before its close and never one from inside
    its future. So a bar's value depends only on data that already
    existed at that bar -- computing the newest bars from a short slice
    of archives gives the same numbers as computing all of them from the
    whole history. The slice reaches back `LOOKBACK` days so that a bar
    with no observation of its own still carries the right earlier one.

TRAINING DOES NOT USE THIS. `flow_inputs(backfill=True)` reads the
    archives directly, exactly as it always has. A cache that could ever
    disagree with the training path is a train/serve skew waiting to
    happen, and the cost it saves is a serving cost.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data_cache" / "flow_cache"
# how far back to re-read the archives when topping the cache up
LOOKBACK = pd.Timedelta(days=30)
# how many bars to keep on disk. 6,000 covers every live window this
# project uses, and bounds the file a restart has to read.
MAX_ROWS = 6_000


def path_for(symbol: str, interval: str, kind: str,
             cache_dir: Path = DEFAULT_DIR) -> Path:
    return Path(cache_dir) / f"{symbol.upper()}_{interval}_{kind}.csv"


def load(symbol: str, interval: str, kind: str,
         cache_dir: Path = DEFAULT_DIR) -> Optional[pd.DataFrame]:
    """What is on disk, or None. A broken file is not an error: it is a
    cache, and the caller recomputes."""
    p = path_for(symbol, interval, kind, cache_dir)
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, index_col=0, parse_dates=[0])
        if df.empty:
            return None
        # the index parse is inside the guard too: a truncated or
        # half-written file reads as a frame and fails here, and a cache
        # that can raise into a dashboard build is not a cache
        idx = pd.DatetimeIndex(df.index)
        df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        return df[~df.index.duplicated(keep="last")].sort_index()
    except Exception as e:      # noqa: BLE001 - see above
        log.info("flow cache %s unreadable (%s); recomputing", p.name, e)
        return None


def save(symbol: str, interval: str, kind: str, frame: pd.DataFrame,
         cache_dir: Path = DEFAULT_DIR) -> None:
    """Best effort. A cache that can take the server down is worse than no
    cache, so every failure here is logged and swallowed."""
    if frame is None or frame.empty:
        return
    p = path_for(symbol, interval, kind, cache_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".csv.part")
        frame.tail(MAX_ROWS).to_csv(tmp)
        tmp.replace(p)
    except OSError as e:
        log.info("flow cache %s not written: %s", p.name, e)


def aligned(symbol: str, interval: str, kind: str, index: pd.DatetimeIndex,
            fetch, how: str = "last",
            cache_dir: Path = DEFAULT_DIR) -> Optional[pd.DataFrame]:
    """The per-bar frame for `index`, reading only what the cache lacks.

    `fetch(since)` returns the raw irregular observations from `since`
    onward; it is called at most once, and not at all when every bar in
    `index` is already known.
    """
    from .derivatives import resample_to_bars

    cached = load(symbol, interval, kind, cache_dir)
    if cached is not None:
        missing = index.difference(cached.index)
        if len(missing) == 0:
            return cached.reindex(index)
        # from the EARLIEST bar the cache lacks, not from the newest it has:
        # a window can grow backwards (a longer warmup, more history in the
        # store), and reading forward from the cache's end would leave those
        # older bars carrying a value from the wrong side of the gap.
        since = max(index[0] - LOOKBACK, missing.min() - LOOKBACK)
    else:
        missing = index
        since = index[0]

    raw = fetch(str(pd.Timestamp(since).date()))
    if raw is None or raw.empty:
        # nothing published for this range. the cache still answers for the
        # bars it knows; the rest stay unknown, which is Agent 4's "missing"
        return None if cached is None else cached.reindex(index)

    fresh = resample_to_bars(raw, index, how=how)
    if cached is None:
        out = fresh
    else:
        out = cached.reindex(index)
        out.loc[missing, fresh.columns] = fresh.loc[missing]
    save(symbol, interval, kind,
         out.combine_first(cached) if cached is not None else out, cache_dir)
    return out
