"""What the best public traders actually do, read off their fills.

THE QUESTION
    Tim: "analyse the logs of the best traders -- their TP and SL, news,
    entry times per coin, every feature -- and find the pattern they use to
    pick entries." This is the machinery. It reads a year of fills per
    address (Hyperliquid, public), rebuilds their trades, and asks four
    things of each trade against OUR bars and OUR features:

      1. SHAPE      how long held, how big, how it was entered (resting
                    order at a price, or a market order chasing), scaled in
                    or out, and what it made -- so a "trade" is a thing with
                    a beginning, an end and a P&L, not a fill.
      2. WHERE      the entry relative to the chart: distance to the nearest
                    confirmed swing, position in yesterday's range, RSI,
                    trend, what price did in the hours before, whether a
                    level was just swept. Each compared with the same
                    feature over ALL bars of that coin, so "they buy after
                    a dip" means "more than the market does", not just
                    "sometimes".
      3. WHEN       hour of day, weekday, and headlines around the entry.
      4. EXIT       the furthest price went for and against them while they
                    were in (MFE / MAE), and where they got out relative to
                    that -- which is the only honest way to read a stop and
                    a target off someone else's book, since their orders
                    are not public, only their fills.

    And then the test that turns description into a strategy: can OUR
    features predict WHEN they enter? A model fitted to "a followed trader
    opens a long on this coin in the next hour" that beats its shuffle is a
    rule that can be written down; one that does not means their entries
    are not a function of anything the chart shows.

WHAT A TRADE IS
    An EPISODE: the position leaving zero and coming back to it (or
    flipping). Every fill between belongs to it. Hyperliquid's fills carry
    `startPosition`, so this is exact, not inferred.

MARKET MAKERS ARE NOT TRADERS
    Half the "best traders" by monthly profit are making markets: both
    sides quoted, seconds-long round trips, fee rebates. Their fills teach
    nothing about entries. A trade is kept for the pattern analysis only if
    it was held at least MIN_HOLD and reached MIN_NOTIONAL, and each
    address is flagged by its share of maker fills so a book that is 99%
    resting quotes is read as what it is.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marketdata.hyperliquid import symbol_for            # noqa: E402

MIN_HOLD = pd.Timedelta(minutes=30)
MIN_NOTIONAL = 5_000.0


@dataclass
class Trade:
    address: str
    coin: str
    symbol: str
    side: str                        # LONG | SHORT
    t_open: pd.Timestamp
    t_close: pd.Timestamp
    entry_vwap: float
    exit_vwap: float
    max_size: float
    max_notional: float
    pnl: float                       # realised, net of fees
    fees: float
    n_entry: int
    n_exit: int
    maker_entry: float               # share of entry size on resting orders
    maker_exit: float
    liquidated: bool
    flipped_in: bool                 # opened by a flip from the other side
    flipped_out: bool
    scale_in_hours: float            # first to last entry fill
    scale_out_hours: float

    @property
    def hold(self) -> pd.Timedelta:
        return self.t_close - self.t_open

    @property
    def ret_pct(self) -> float:
        s = 1.0 if self.side == "LONG" else -1.0
        return 100.0 * s * (self.exit_vwap / self.entry_vwap - 1.0)

    def to_json(self) -> dict:
        d = asdict(self)
        d["t_open"] = self.t_open.isoformat(); d["t_close"] = self.t_close.isoformat()
        d["hold_hours"] = self.hold.total_seconds() / 3600.0
        d["ret_pct"] = self.ret_pct
        return d


# ------------------------------------------------------------- reconstruct
def reconstruct(address: str, fills: List[dict]) -> List[Trade]:
    """Fills -> episodes. One pass per coin in time order."""
    by_coin: Dict[str, List[dict]] = defaultdict(list)
    for f in fills:
        by_coin[f["coin"]].append(f)
    out: List[Trade] = []
    for coin, fs in by_coin.items():
        sym = symbol_for(coin) or ""
        fs.sort(key=lambda x: (x["time"], x["tid"]))
        cur: Optional[dict] = None       # the open episode

        def close_episode(ep: dict, flipped_out: bool) -> None:
            if not ep["entry"]:
                return
            e_sz = sum(s for _, s, _ in ep["entry"]); x_sz = sum(s for _, s, _ in ep["exit"])
            if e_sz <= 0 or x_sz <= 0:
                return
            e_vwap = sum(p * s for p, s, _ in ep["entry"]) / e_sz
            x_vwap = sum(p * s for p, s, _ in ep["exit"]) / x_sz
            out.append(Trade(
                address=address, coin=coin, symbol=sym, side=ep["side"],
                t_open=pd.Timestamp(ep["t_open"], unit="ms", tz="UTC"),
                t_close=pd.Timestamp(ep["t_close"], unit="ms", tz="UTC"),
                entry_vwap=e_vwap, exit_vwap=x_vwap,
                max_size=ep["max_size"], max_notional=ep["max_notional"],
                pnl=ep["pnl"] - ep["fees"], fees=ep["fees"],
                n_entry=len(ep["entry"]), n_exit=len(ep["exit"]),
                maker_entry=sum(s for _, s, m in ep["entry"] if m) / e_sz,
                maker_exit=sum(s for _, s, m in ep["exit"] if m) / x_sz,
                liquidated=ep["liq"], flipped_in=ep["flipped_in"], flipped_out=flipped_out,
                scale_in_hours=(ep["t_last_entry"] - ep["t_open"]) / 3.6e6,
                scale_out_hours=(ep["t_close"] - ep["t_first_exit"]) / 3.6e6,
            ))

        for f in fs:
            try:
                px, sz = float(f["px"]), float(f["sz"])
                start = float(f.get("startPosition", 0) or 0)
                pnl = float(f.get("closedPnl", 0) or 0)
                fee = float(f.get("fee", 0) or 0)
            except (TypeError, ValueError):
                continue
            buy = f.get("side") == "B"
            maker = not bool(f.get("crossed", True))
            liq = bool(f.get("liquidation"))
            t = int(f["time"])
            end = start + (sz if buy else -sz)
            if abs(end) < 1e-12:
                end = 0.0
            # the fill's role, from the position it moved. exact: the
            # exchange states the position before every fill
            if start == 0:
                role = "open"
            elif end == 0:
                role = "close"
            elif np.sign(end) == np.sign(start):
                role = "open" if abs(end) > abs(start) else "close"
            else:
                role = "flip"          # crossed zero: closes one, opens the other
            if role == "open":
                if cur is None:
                    cur = {"side": "LONG" if buy else "SHORT", "t_open": t, "t_last_entry": t,
                           "t_first_exit": None, "t_close": None, "entry": [], "exit": [],
                           "max_size": 0.0, "max_notional": 0.0, "pnl": 0.0, "fees": 0.0,
                           "liq": False, "flipped_in": False}
                cur["entry"].append((px, sz, maker)); cur["t_last_entry"] = t
                cur["fees"] += fee
                cur["max_size"] = max(cur["max_size"], abs(end))
                cur["max_notional"] = max(cur["max_notional"], abs(end) * px)
            elif role == "close":
                if cur is None:
                    continue           # history starts mid-position: skip
                cur["exit"].append((px, sz, maker)); cur["pnl"] += pnl; cur["fees"] += fee
                cur["liq"] |= liq
                if cur["t_first_exit"] is None:
                    cur["t_first_exit"] = t
                cur["t_close"] = t
                if end == 0:
                    close_episode(cur, False); cur = None
            else:  # flip
                closing = abs(start)
                if cur is not None:
                    cur["exit"].append((px, closing, maker)); cur["pnl"] += pnl; cur["fees"] += fee * closing / sz
                    cur["liq"] |= liq
                    if cur["t_first_exit"] is None:
                        cur["t_first_exit"] = t
                    cur["t_close"] = t
                    close_episode(cur, True)
                opening = abs(end)
                cur = {"side": "LONG" if end > 0 else "SHORT", "t_open": t, "t_last_entry": t,
                       "t_first_exit": None, "t_close": None, "entry": [(px, opening, maker)],
                       "exit": [], "max_size": opening, "max_notional": opening * px,
                       "pnl": 0.0, "fees": fee * opening / sz, "liq": False, "flipped_in": True}
    return out


def load_trades(fills_dir: Path, addresses: Optional[List[str]] = None) -> pd.DataFrame:
    rows = []
    for p in sorted(Path(fills_dir).glob("0x*.json")):
        addr = p.stem
        if addresses and addr not in addresses:
            continue
        for t in reconstruct(addr, json.loads(p.read_text())):
            rows.append(t.to_json())
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["t_open"] = pd.to_datetime(df["t_open"], utc=True, format="ISO8601")
    df["t_close"] = pd.to_datetime(df["t_close"], utc=True, format="ISO8601")
    return df


def position_trades(df: pd.DataFrame) -> pd.DataFrame:
    """The episodes that are trades in the human sense."""
    m = (df["hold_hours"] >= MIN_HOLD.total_seconds() / 3600) & (df["max_notional"] >= MIN_NOTIONAL)
    return df[m].copy()


# ---------------------------------------------------------------- profile
def profile(df: pd.DataFrame) -> pd.DataFrame:
    """One row per address: what kind of trader this is."""
    out = []
    for addr, g in df.groupby("address"):
        pos = position_trades(g)
        wins = pos[pos["pnl"] > 0]; losses = pos[pos["pnl"] <= 0]
        out.append({
            "address": addr,
            "episodes": len(g),
            "position_trades": len(pos),
            "maker_share": float(np.average(g["maker_entry"], weights=g["max_notional"].clip(lower=1))) if len(g) else np.nan,
            "median_hold_h": float(pos["hold_hours"].median()) if len(pos) else np.nan,
            "p25_hold_h": float(pos["hold_hours"].quantile(0.25)) if len(pos) else np.nan,
            "p75_hold_h": float(pos["hold_hours"].quantile(0.75)) if len(pos) else np.nan,
            "long_share": float((pos["side"] == "LONG").mean()) if len(pos) else np.nan,
            "win_rate": float((pos["pnl"] > 0).mean()) if len(pos) else np.nan,
            "avg_win_pct": float(wins["ret_pct"].mean()) if len(wins) else np.nan,
            "avg_loss_pct": float(losses["ret_pct"].mean()) if len(losses) else np.nan,
            "payoff_ratio": (float(wins["ret_pct"].mean() / -losses["ret_pct"].mean())
                             if len(wins) and len(losses) and losses["ret_pct"].mean() < 0 else np.nan),
            "pnl_total": float(pos["pnl"].sum()),
            "pnl_top5_share": (float(pos["pnl"].nlargest(5).sum() / pos["pnl"].clip(lower=0).sum())
                               if len(pos) and pos["pnl"].clip(lower=0).sum() > 0 else np.nan),
            "median_notional": float(pos["max_notional"].median()) if len(pos) else np.nan,
            "entry_clips": float(pos["n_entry"].median()) if len(pos) else np.nan,
            "exit_clips": float(pos["n_exit"].median()) if len(pos) else np.nan,
            "liquidations": int(pos["liquidated"].sum()),
            "coins": ",".join(pos["symbol"].replace("", "other").value_counts().head(4).index) if len(pos) else "",
        })
    return pd.DataFrame(out)


# ------------------------------------------------------------- our bars
def _atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = bars["high"], bars["low"], bars["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def hourly_context(bars_1h: pd.DataFrame, levels: dict) -> pd.DataFrame:
    """Per closed 1h bar: the features an entry is compared on. Every
    column is known at that bar's close; an entry in hour t+1 is compared
    with bar t."""
    c = bars_1h["close"]; h = bars_1h["high"]; l = bars_1h["low"]
    atr = _atr(bars_1h)
    ema20, ema50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    day = bars_1h.index.tz_convert("UTC").normalize()
    daily = pd.DataFrame({"h": h.to_numpy(), "l": l.to_numpy()}, index=day).groupby(level=0).agg({"h": "max", "l": "min"}).shift(1)
    pdh = pd.Series(daily["h"].reindex(day).to_numpy(), index=bars_1h.index)
    pdl = pd.Series(daily["l"].reindex(day).to_numpy(), index=bars_1h.index)
    # the rolling 24h range, for "where in the range"
    hi24, lo24 = h.rolling(24).max(), l.rolling(24).min()
    vol = bars_1h["volume"] if "volume" in bars_1h else pd.Series(np.nan, index=bars_1h.index)
    ret = np.log(c).diff()
    rv = ret.rolling(24).std()
    rv_pct = rv.rolling(24 * 30, min_periods=24 * 5).rank(pct=True)
    # a sweep: this bar's low took out the lowest low of the prior 24 bars
    # and the close came back above it (long sweep); mirror for shorts
    prior_lo = l.shift(1).rolling(24).min(); prior_hi = h.shift(1).rolling(24).max()
    swept_low = (l < prior_lo) & (c > prior_lo)
    swept_high = (h > prior_hi) & (c < prior_hi)
    res, sup = pd.Series(levels["res"], index=bars_1h.index), pd.Series(levels["sup"], index=bars_1h.index)
    return pd.DataFrame({
        "atr_pct": 100 * atr / c,
        "rsi14": _rsi(c),
        "ema20_dist_atr": (c - ema20) / atr,
        "ema50_dist_atr": (c - ema50) / atr,
        "trend_20_50": np.sign(ema20 - ema50),
        "ret_1h_pct": 100 * c.pct_change(1),
        "ret_4h_pct": 100 * c.pct_change(4),
        "ret_24h_pct": 100 * c.pct_change(24),
        "ret_7d_pct": 100 * c.pct_change(24 * 7),
        "range24_pos": (c - lo24) / (hi24 - lo24).replace(0, np.nan),
        "pdh_dist_atr": (pdh - c) / atr,
        "pdl_dist_atr": (c - pdl) / atr,
        "res_dist_atr": (res - c) / atr,
        "sup_dist_atr": (c - sup) / atr,
        "rv_pctile": rv_pct,
        "vol_z": (vol - vol.rolling(24 * 7).mean()) / vol.rolling(24 * 7).std(),
        "swept_low_24": swept_low.astype(float),
        "swept_high_24": swept_high.astype(float),
        "bar_pos": (c - l) / (h - l).replace(0, np.nan),
    }, index=bars_1h.index)


def excursions(bars_1m: pd.DataFrame, t: pd.Series) -> dict:
    """MFE / MAE inside the hold, in % of entry, and where the exit sat
    between them. `t` is one trade row."""
    w = bars_1m[(bars_1m.index > t["t_open"]) & (bars_1m.index <= t["t_close"])]
    if w.empty:
        return {}
    e = t["entry_vwap"]; s = 1.0 if t["side"] == "LONG" else -1.0
    hi = float(w["high"].max()); lo = float(w["low"].min())
    mfe = 100 * s * ((hi if s > 0 else lo) / e - 1.0)
    mae = 100 * s * ((lo if s > 0 else hi) / e - 1.0)
    # how much of the best price they kept, and how deep they let it go
    return {"mfe_pct": mfe, "mae_pct": mae,
            "exit_of_mfe": (t["ret_pct"] / mfe) if mfe > 0 else np.nan,
            # minutes from entry to the best price of the hold
            "t_mfe_min": (((w["high"].idxmax() if s > 0 else w["low"].idxmin()) - t["t_open"]).total_seconds() / 60
                          if mfe > 0 else np.nan)}


def entry_in_bar(bars_1h: pd.DataFrame, t: pd.Series) -> dict:
    """Where inside the hour's range the entry printed, and versus the close
    of the bar before -- did they get a better price than a market order at
    the previous close would have?"""
    i = bars_1h.index.searchsorted(t["t_open"])
    if i <= 0 or i >= len(bars_1h):
        return {}
    bar = bars_1h.iloc[i] if bars_1h.index[i] > t["t_open"] else bars_1h.iloc[min(i + 1, len(bars_1h) - 1)]
    prev = bars_1h.iloc[i - 1]
    rng = bar["high"] - bar["low"]
    s = 1.0 if t["side"] == "LONG" else -1.0
    return {"entry_bar_pos": (t["entry_vwap"] - bar["low"]) / rng if rng > 0 else np.nan,
            "entry_vs_prev_close_pct": 100 * s * (prev["close"] / t["entry_vwap"] - 1.0)}


def forward_returns(bars: pd.DataFrame, t: pd.Series, hours=(1, 4, 24)) -> dict:
    """From the trader's own VWAP, and from the FOLLOWER's price: the first
    bar close after the entry printed. A resting order fills at the dip by
    construction, so a return measured from its fill flatters it; the
    follower sees the fill after the bounce and pays the close."""
    out = {}
    s = 1.0 if t["side"] == "LONG" else -1.0
    nxt = bars[bars.index > t["t_open"]]
    follower_px = float(nxt["close"].iloc[0]) if len(nxt) else np.nan
    out["follower_slip_pct"] = 100 * s * (t["entry_vwap"] / follower_px - 1.0) if np.isfinite(follower_px) else np.nan
    for hrs in hours:
        after = bars[bars.index > t["t_open"] + pd.Timedelta(hours=hrs)]
        px = float(after["close"].iloc[0]) if len(after) else np.nan
        out[f"fwd_{hrs}h_pct"] = 100 * s * (px / t["entry_vwap"] - 1.0) if np.isfinite(px) else np.nan
        out[f"fol_{hrs}h_pct"] = (100 * s * (px / follower_px - 1.0)
                                  if np.isfinite(px) and np.isfinite(follower_px) else np.nan)
    return out


def news_around(items: List[dict], sym: str, t_open: pd.Timestamp, hours: float = 6.0) -> int:
    base = sym[:-4] if sym.endswith("USDT") else sym
    lo, hi = t_open - pd.Timedelta(hours=hours), t_open + pd.Timedelta(hours=1)
    n = 0
    for it in items:
        ts = it.get("_ts")
        if ts is None or not (lo <= ts <= hi):
            continue
        assets = it.get("assets") or []
        if base in assets or (not assets):
            n += 1
    return n


def compare(entries: pd.DataFrame, baseline: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    """Entry-time feature vs all-bars feature: medians, the share of entries
    in the baseline's top/bottom quartile, and a rank-sum z. |z| > 3 is a
    habit; |z| < 2 is nothing."""
    from scipy.stats import mannwhitneyu
    rows = []
    for c in cols:
        a = entries[c].dropna().to_numpy(float); b = baseline[c].dropna().to_numpy(float)
        if len(a) < 20 or len(b) < 100:
            continue
        q25, q75 = np.nanquantile(b, [0.25, 0.75])
        try:
            u, p = mannwhitneyu(a, b, alternative="two-sided")
            n1, n2 = len(a), len(b)
            z = (u - n1 * n2 / 2) / np.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
        except ValueError:
            z = np.nan
        rows.append({"feature": c, "n": len(a), "entry_median": float(np.median(a)),
                     "market_median": float(np.median(b)),
                     "share_top_q": float(np.mean(a >= q75)), "share_bottom_q": float(np.mean(a <= q25)),
                     "z": float(z)})
    return pd.DataFrame(rows).sort_values("z", key=lambda s: -s.abs())
