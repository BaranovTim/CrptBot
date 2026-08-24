"""Trade tape (aggTrades) — the only feed that shows the mechanism.

A candle is an aggregate, and the same OHLC can describe a smooth grind up or
a violent spike and collapse. Most crypto spikes are liquidation cascades into
a thin book; the candle shows the result, the tape shows how it happened. That
is the whole argument for Agent 4 reading trades directly instead of settling
for the five numbers a bar gives you.

THE FLAG EVERYONE INVERTS
-------------------------
Every aggTrade carries ``is_buyer_maker`` (``m`` in the raw file), and it is
the single most commonly inverted field in crypto quant work:

    m == True   the BUYER was the maker (resting bid got hit)
                -> the SELLER crossed the spread
                -> this is an AGGRESSIVE SELL

    m == False  the buyer was the taker
                -> the BUYER crossed the spread
                -> this is an AGGRESSIVE BUY

Get it backwards and every flow feature in the project has its sign flipped —
silently, because the magnitudes all still look plausible.
``tests/test_agent4_tape.py`` pins it against a hand-built case.

THE HISTOGRAM TRICK
-------------------
Reducing the tape to per-bar totals throws away the size distribution, which
is exactly what large-print detection needs. But storing raw prints is
impractical: one day of BTCUSDT aggTrades is hundreds of megabytes.

So each bar stores a log-spaced histogram of trade notionals, split by
aggressor side. That keeps the distribution at fixed width, and — the useful
part — makes the expensive tape pass **independent of the large-print
threshold**. Re-tune what counts as "large" later and you recompute from the
histograms in milliseconds instead of re-downloading a hundred gigabytes.

``aggTrades`` collapses same-price, same-direction fills at the same instant
into one row. Right for flow analysis, wrong if you are counting individual
orders — a "print" here is one aggregated fill, not one order.

BINNING
-------
Prints are assigned to bars by searching the caller's own bar index, never by
rounding to an interval. Binance labels a kline by ``close_time``, which is
``open_time + interval - 1ms``; rounding a print up to the hour produces a
label one millisecond past the bar it belongs to, and the subsequent join
silently matches nothing and yields an all-zero tape. Binning against the
real index makes the convention irrelevant.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from .binance import DEFAULT_CACHE, VISION_BASE, _days, _download, _to_utc

AGG_COLUMNS = ["agg_trade_id", "price", "quantity", "first_trade_id",
               "last_trade_id", "transact_time", "is_buyer_maker", "is_best_match"]

# Log-spaced notional buckets, $10 to $100M, two per decade. Wide enough that
# a whale print and a dust fill never share a bucket, narrow enough that the
# per-bar row stays small.
BUCKET_EDGES = np.logspace(1, 8, 15)          # 14 buckets
N_BUCKETS = len(BUCKET_EDGES) - 1
BUY_COLS = [f"buy_bucket_{i}" for i in range(N_BUCKETS)]
SELL_COLS = [f"sell_bucket_{i}" for i in range(N_BUCKETS)]
# Counts as well as notional. "Large print" is a percentile of the print-SIZE
# distribution, which is a question about how many prints are that big — not
# about which bucket happens to hold a given share of dollar volume. On a
# heavy tail those two answers differ by orders of magnitude.
BUY_CNT_COLS = [f"buy_count_{i}" for i in range(N_BUCKETS)]
SELL_CNT_COLS = [f"sell_count_{i}" for i in range(N_BUCKETS)]
HIST_COLS = BUY_COLS + SELL_COLS + BUY_CNT_COLS + SELL_CNT_COLS
BASE_COLS = ["buy_notional", "sell_notional", "buy_prints", "sell_prints",
             "max_print_notional"]


def _read_agg_zip(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    first = raw.split(b"\n", 1)[0].decode("utf-8", "ignore").lower()
    header = 0 if "agg_trade_id" in first or "transact_time" in first else None
    df = pd.read_csv(
        io.BytesIO(raw), header=header,
        names=None if header == 0 else AGG_COLUMNS[: first.count(",") + 1],
    )
    if header == 0:
        df.columns = [c.strip().lower() for c in df.columns]
    else:
        df.columns = AGG_COLUMNS[: len(df.columns)]
    return df


def _aggregate_chunk(df: pd.DataFrame, bars_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Collapse raw prints into one row per bar, keeping the size histogram.

    Each print is attributed to the first bar whose close_time is at or after
    the print's timestamp — correct for any labelling convention.
    """
    price = pd.to_numeric(df["price"], errors="coerce")
    qty = pd.to_numeric(df["quantity"], errors="coerce")
    notional = price * qty
    ts = _to_utc(df["transact_time"])

    maker_buy = df["is_buyer_maker"].astype(str).str.lower().isin(("true", "1"))
    # THE FLIP. if the buyer was the maker, their bid was just sitting there
    # and the SELLER crossed the spread to hit it - so this is a market sell.
    # the ~ is what makes the whole project's flow direction correct
    is_aggressive_buy = ~maker_buy

    ok = notional.notna() & ts.notna()
    # find the first bar whose close_time is at or after each print.
    # searching the real bar index instead of rounding to the hour avoids the
    # off-by-one-millisecond trap in Binance's close_time convention
    slot = np.searchsorted(bars_index.values, ts[ok].values, side="left")
    inside = slot < len(bars_index)      # prints past the last bar are not ours yet
    frame = pd.DataFrame({
        "bar": pd.DatetimeIndex(bars_index[np.clip(slot, 0, len(bars_index) - 1)]),
        "notional": notional[ok],
        "buy": is_aggressive_buy[ok],
        "bucket": np.clip(
            np.digitize(notional[ok].to_numpy(), BUCKET_EDGES) - 1, 0, N_BUCKETS - 1
        ),
    })
    frame = frame[inside]           # prints after the last bar are not ours yet
    if frame.empty:
        return empty_tape_bars(bars_index[:0])

    frame["buy_notional"] = frame["notional"].where(frame["buy"], 0.0)
    frame["sell_notional"] = frame["notional"].where(~frame["buy"], 0.0)

    grouped = frame.groupby("bar", sort=True)
    out = grouped.agg(
        buy_notional=("buy_notional", "sum"),
        sell_notional=("sell_notional", "sum"),
        buy_prints=("buy", "sum"),
        n_prints=("notional", "size"),
        max_print_notional=("notional", "max"),
    )
    out["sell_prints"] = out["n_prints"] - out["buy_prints"]
    out = out.drop(columns=["n_prints"])

    # One histogram column per (side, bucket), built with a pivot rather than
    # a per-group loop — this runs over millions of prints per day.
    # give each (side, size-bucket) pair its own column number: buys land in
    # 0..13, sells in 14..27. one pivot then fills the whole histogram
    frame["hcol"] = frame["bucket"] + np.where(frame["buy"], 0, N_BUCKETS)
    notional_hist = (
        frame.pivot_table(index="bar", columns="hcol", values="notional",
                          aggfunc="sum", fill_value=0.0)
        .reindex(index=out.index, columns=range(2 * N_BUCKETS), fill_value=0.0)
    )
    count_hist = (
        frame.pivot_table(index="bar", columns="hcol", values="notional",
                          aggfunc="size", fill_value=0)
        .reindex(index=out.index, columns=range(2 * N_BUCKETS), fill_value=0)
    )
    out[BUY_COLS + SELL_COLS] = notional_hist.to_numpy()
    out[BUY_CNT_COLS + SELL_CNT_COLS] = count_hist.to_numpy().astype(float)
    return out[BASE_COLS + HIST_COLS]


def aggregate_tape(df: pd.DataFrame, bars_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Public entry point for an in-memory tape frame."""
    return _aggregate_chunk(df, pd.DatetimeIndex(bars_index))


def load_tape_bars(
    bars_index: pd.DatetimeIndex,
    symbol: str = "BTCUSDT",
    start: str = "2026-05-01",
    end: Optional[str] = None,
    market: str = "futures/um",
    cache_dir: Path = DEFAULT_CACHE,
) -> pd.DataFrame:
    """Download aggTrades day by day and reduce each day to per-bar rows.

    Daily files are aggregated and discarded one at a time — the whole tape is
    never held in memory at once, which is what makes multi-year ranges
    tractable on a laptop.
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.utcnow().tz_localize("UTC")
    sym = symbol.upper()
    frames: List[pd.DataFrame] = []

    for day in _days(start_ts, end_ts):
        stem = f"{sym}-aggTrades-{day:%Y-%m-%d}"
        url = f"{VISION_BASE}/{market}/daily/aggTrades/{sym}/{stem}.zip"
        dest = Path(cache_dir) / market / "aggTrades" / sym / f"{stem}.zip"
        # A daily tape archive is orders of magnitude bigger than a kline
        # file, so it gets a correspondingly larger timeout.
        if not _download(url, dest, timeout=600):
            continue
        try:
            frames.append(_aggregate_chunk(_read_agg_zip(dest),
                                           pd.DatetimeIndex(bars_index)))
        except (zipfile.BadZipFile, KeyError, ValueError):
            continue

    if not frames:
        return empty_tape_bars()
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.index.name = "close_time"
    return out


def empty_tape_bars(index: Optional[pd.Index] = None) -> pd.DataFrame:
    """A correctly-shaped, all-zero tape frame.

    Returned when no aggTrades are available. Zero is right here rather than
    NaN — "no trades observed" is a real state — and Agent 4's coverage
    column is what tells the model the tape is missing rather than quiet.
    """
    cols = BASE_COLS + HIST_COLS
    idx = index if index is not None else pd.DatetimeIndex([], tz="UTC", name="close_time")
    return pd.DataFrame(0.0, index=idx, columns=cols)


def align_tape(tape: pd.DataFrame, bars_index: pd.Index) -> pd.DataFrame:
    """Reindex a tape frame onto the bar index. Missing bars become zeros."""
    if tape is None or tape.empty:
        return empty_tape_bars(bars_index)
    return tape.reindex(bars_index).fillna(0.0)
