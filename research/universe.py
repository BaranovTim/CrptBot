"""Every USDT perpetual Binance has ever listed, as daily bars -- delisted
ones included -- for studies that must not be run on today's winners.

WHY
    The momentum rotation was measured on the fifteen coins this server
    serves: today's list. A coin is on today's list partly BECAUSE it went
    up, which is exactly what a momentum study then "finds". The 2021 subset
    was a partial check. The real one is a universe chosen the way it would
    have been chosen at the time: every perpetual trading then, ranked by
    what it was trading then (30-day dollar volume), dead coins and all.
    data.binance.vision keeps the monthly archives of delisted symbols
    (LUNA, FTT, SRM ...), so that universe can be rebuilt.

    python research/universe.py download        # ~20k small files, cached
"""
from __future__ import annotations

import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marketdata.binance import _read_zip, _to_utc          # noqa: E402

LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix={p}"
FILE = "https://data.binance.vision/{k}"
PREFIX = "data/futures/um/monthly/klines/"
DIR = Path("data_cache/research_frames/universe_1d")
# tokenised stocks, indices and fiat pairs are not coins
NOT_COINS = re.compile(r"^(AAPL|TSLA|NVDA|MSTR|COIN|HOOD|AMZN|GOOGL|META|MSFT|QQQ|SPY|BTCDOM|DEFI|FOOTBALL|"
                       r"BLUEBIRD|USDC|EUR|GBP|XAU|XAG|TRY|BRL)")


def _get(url: str, tries: int = 4) -> bytes:
    for a in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "vanth-research"}),
                                          timeout=30).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            time.sleep(1 + 2 * a)
        except Exception:
            time.sleep(1 + 2 * a)
    return b""


def _list(prefix: str, pattern: str) -> list:
    out, marker = [], ""
    while True:
        x = _get(LIST.format(p=prefix) + (f"&marker={marker}" if marker else "")).decode()
        got = re.findall(pattern, x)
        out += got
        if "<IsTruncated>true" not in x or not got:
            return out
        keys = re.findall(r"<(?:Key|Prefix)>([^<]+)</(?:Key|Prefix)>", x)
        marker = keys[-1]


def symbols() -> list:
    syms = _list(PREFIX, r"<Prefix>" + PREFIX + r"([^/<]+)/</Prefix>")
    return sorted(s for s in set(syms) if s.endswith("USDT") and not NOT_COINS.match(s))


def download_symbol(sym: str) -> int:
    dest = DIR / f"{sym}.pkl"
    if dest.exists():
        old = pd.read_pickle(dest)
        if old.empty or "taker_buy_quote_volume" in old:
            return -1                        # complete, or nothing to fetch
    keys = _list(f"{PREFIX}{sym}/1d/", r"<Key>(" + PREFIX + sym + r"/1d/[^<]+\.zip)</Key>")
    frames = []
    for k in keys:
        raw = _get(FILE.format(k=k))
        if not raw:
            continue
        tmp = DIR / "_tmp" / f"{sym}_{Path(k).name}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(raw)
        try:
            df = _read_zip(tmp)
            df.index = _to_utc(df["open_time"]).dt.normalize()      # the day the bar covers
            frames.append(df)
        except Exception:
            pass
        tmp.unlink(missing_ok=True)
    if not frames:
        pd.DataFrame().to_pickle(dest)
        return 0
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # the taker split too: net taker flow across coins is the best-measured
    # big-player footprint at a weekly horizon (research/QUANT.md, round four)
    df = df[["open", "high", "low", "close", "volume", "quote_volume",
             "taker_buy_quote_volume"]].apply(pd.to_numeric, errors="coerce")
    part = dest.with_suffix(".part")
    df.to_pickle(part)
    part.replace(dest)                       # whole or not at all: readers never see half a file
    return len(df)


def download(workers: int = 32) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    syms = symbols()
    print(f"{len(syms)} USDT perpetuals in the archive", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for i, (s, n) in enumerate(zip(syms, ex.map(download_symbol, syms))):
            if i % 50 == 0:
                print(f"  {i}/{len(syms)} {s}: {n} days [{time.time() - t0:.0f}s]", flush=True)


def panel(field: str = "close") -> pd.DataFrame:
    """Every symbol's daily `field`, one column per symbol, by close date."""
    cols = {}
    for p in sorted(DIR.glob("*.pkl")):
        df = pd.read_pickle(p)
        if len(df) and field in df:
            cols[p.stem] = df[field].astype(float)
    return pd.DataFrame(cols).sort_index()


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "download":
    download()
