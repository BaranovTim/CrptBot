"""What big players leave behind in public data, as feature blocks for the
4h lab (research/wf4h.py).

WHY THESE, AND WHY NOW
    The pooled 4h model is a location-in-structure model: where the close
    sits in its range, how far from the 200-EMA, from yesterday's high and
    low. It has never been told WHO is trading. Agent 4's tape columns --
    net aggression, trade count, trade size -- are empty in every training
    row, because the tape store only covers the weeks the collector has
    run. But three of them do not need a tape at all: Binance's klines carry
    the taker-buy volume and the trade count for every bar since 2020, and
    they have been downloaded all along.

    flw_  ORDER FLOW from the klines. Net aggression (taker buys minus
          taker sells, as a share of volume) over 1-18 bars, its z-score,
          its DIVERGENCE from the price (heavy selling that does not move
          the price is someone absorbing it with resting orders -- the
          habit the profitable Hyperliquid traders showed), trade count and
          average trade size against the coin's own recent norm (a bigger
          average print is bigger participants).
    swp_  SWEEPS of the trade's own levels. A bar that trades through the
          support the stop would sit behind and closes back above it has
          taken the stops resting there -- the liquidity a large buyer
          needs to fill. Depth in ATR, age, and the net aggression on the
          sweep bar. Agent 1 already flags sweeps of its own swing points;
          this is keyed to the levels the trade uses, with depth and flow.
    liq_  ESTIMATED LIQUIDATION CLUSTERS from open interest (the model
          behind the public "liquidation heatmaps"): positions opened when
          open interest rose at a price, liquidated at that price x (1 -+
          1/leverage). Where those clusters sit relative to the target and
          the stop.

Every value is computed from bars up to and including the close it is
attached to, and is cached per block as one frame keyed by (coin, t).

    python research/footprints.py build flw swp liq
"""
from __future__ import annotations

import glob
import io
import pickle
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research.wf4h as L                                   # noqa: E402
from agent5.labels import _atr                              # noqa: E402
from agent5.structure import structural_levels              # noqa: E402


def _z(x: pd.Series, n: int, minp: int = None) -> pd.Series:
    mu = x.rolling(n, min_periods=minp or n // 3).mean()
    sd = x.rolling(n, min_periods=minp or n // 3).std()
    return (x - mu) / sd.replace(0, np.nan)


# ------------------------------------------------------------------- flow
def flow(bars: pd.DataFrame) -> pd.DataFrame:
    v = bars["volume"].astype(float).where(lambda s: s > 0)
    tb = bars["taker_buy_base_volume"].astype(float)
    c = np.log(bars["close"].astype(float))
    out = pd.DataFrame(index=bars.index)
    for k in (1, 3, 6, 18):
        out[f"flw_net_{k}"] = (2 * tb.rolling(k).sum() - v.rolling(k).sum()) / v.rolling(k).sum()
    for k in (6, 18):
        out[f"flw_net_{k}_z"] = _z(out[f"flw_net_{k}"], 180)
        # more (or less) aggression than the move shows: positive = buyers
        # hitting offers that did not give way, negative = sellers absorbed
        out[f"flw_div_{k}"] = out[f"flw_net_{k}_z"] - _z(c.diff(k), 180)
    n = bars["number_of_trades"].astype(float).where(lambda s: s > 0)
    out["flw_trades_z"] = _z(np.log(n), 90)
    size = np.log(bars["quote_volume"].astype(float).where(lambda s: s > 0) / n)
    out["flw_size_z"] = _z(size, 90)
    out["flw_size_6_z"] = _z(size.rolling(6).mean(), 180)
    # who moved the last day's range: aggression on the bars that closed
    # down, against the bars that closed up (the absorption read at a low)
    down = (bars["close"] < bars["open"]).astype(float)
    net1 = (2 * tb - v) / v
    out["flw_net_down_6"] = (net1 * down).rolling(6).sum() / down.rolling(6).sum().replace(0, np.nan)
    out["flw_net_up_6"] = (net1 * (1 - down)).rolling(6).sum() / (1 - down).rolling(6).sum().replace(0, np.nan)
    return out


# ----------------------------------------------------------------- sweeps
def sweeps(bars: pd.DataFrame, atr: np.ndarray, lv: dict) -> pd.DataFrame:
    h = bars["high"].to_numpy(float); lo = bars["low"].to_numpy(float); c = bars["close"].to_numpy(float)
    v = bars["volume"].to_numpy(float); tb = bars["taker_buy_base_volume"].to_numpy(float)
    net1 = np.where(v > 0, (2 * tb - v) / np.where(v > 0, v, 1), np.nan)
    sup = np.r_[np.nan, lv["sup"][:-1]]            # the level as it stood before the bar
    res = np.r_[np.nan, lv["res"][:-1]]
    a = np.where(atr > 0, atr, np.nan)
    lo_sw = (lo < sup) & (c > sup)
    hi_sw = (h > res) & (c < res)
    d_lo = np.where(lo_sw, (sup - lo) / a, 0.0)
    d_hi = np.where(hi_sw, (h - res) / a, 0.0)
    out = pd.DataFrame(index=bars.index)
    for k in (1, 3, 6):
        out[f"swp_lo_{k}"] = pd.Series(d_lo, index=bars.index).rolling(k, min_periods=1).max()
        out[f"swp_hi_{k}"] = pd.Series(d_hi, index=bars.index).rolling(k, min_periods=1).max()
    for name, ev in (("lo", lo_sw), ("hi", hi_sw)):
        idx = np.where(ev, np.arange(len(c)), -1)
        last = np.maximum.accumulate(idx)
        age = np.where(last >= 0, np.arange(len(c)) - last, 60).astype(float)
        out[f"swp_{name}_age"] = np.minimum(age, 60)
        f = np.where(last >= 0, net1[np.maximum(last, 0)], np.nan)
        out[f"swp_{name}_flow"] = np.where(age <= 6, f, np.nan)
    # a real break -- a close through the level -- in the last three bars
    out["swp_broke_lo_3"] = pd.Series((c < sup).astype(float), index=bars.index).rolling(3, min_periods=1).max()
    out["swp_broke_hi_3"] = pd.Series((c > res).astype(float), index=bars.index).rolling(3, min_periods=1).max()
    return out


# ------------------------------------------------------------ liquidations
LEVERAGE = (10.0, 25.0, 50.0)           # the tiers a heatmap spreads new OI over
LIQ_HALF_LIFE_BARS = 18                 # positions close on their own too (3 days)


def load_oi(sym: str) -> pd.Series:
    """Open interest (coins) from the metrics archive, five-minute, cached."""
    p = L.CACHE / f"oi_{sym}.pkl"
    if p.exists():
        return pickle.load(open(p, "rb"))
    frames = []
    for f in sorted(glob.glob(str(L.METRICS / sym / "*.zip"))):
        try:
            with zipfile.ZipFile(f) as z:
                df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), usecols=["create_time", "sum_open_interest"])
        except (zipfile.BadZipFile, IndexError, ValueError, KeyError, pd.errors.ParserError):
            continue
        frames.append(df)
    if not frames:
        s = pd.Series(dtype=float)
    else:
        m = pd.concat(frames)
        m["create_time"] = pd.to_datetime(m["create_time"], utc=True, errors="coerce")
        s = (m.dropna().drop_duplicates("create_time").set_index("create_time")["sum_open_interest"]
             .astype(float).sort_index())
    pickle.dump(s, open(p, "wb"))
    return s


def liquidations(sym: str, bars: pd.DataFrame, atr: np.ndarray, lv: dict) -> pd.DataFrame:
    """A liquidation map rebuilt bar by bar from open interest.

    When open interest RISES by dOI during a bar, dOI new contracts were
    opened around the bar's typical price p -- half long, half short (the
    split is unknowable from OI alone), spread evenly over the leverage
    tiers. A long opened at p is liquidated near p(1 - 1/lev), a short near
    p(1 + 1/lev). Each estimated cluster decays (positions close on their
    own, half-life three days) and is removed when the price trades through
    it (it has been liquidated). Features, as a share of open interest:
    the short-liquidation mass between the close and the resistance above
    (fuel for a squeeze up into it), the long-liquidation mass between the
    close and the support below, and the mass just beyond each level.
    """
    oi = load_oi(sym)
    out = pd.DataFrame(index=bars.index)
    names = ["liq_short_to_res", "liq_long_to_sup", "liq_short_beyond_res", "liq_long_beyond_sup",
             "liq_short_2atr", "liq_long_2atr", "liq_skew_2atr"]
    if oi.empty:
        for n in names:
            out[n] = np.nan
        return out
    # OI at each bar close, only if a sample exists within the last hour
    left = pd.DataFrame({"_t": bars.index})
    al = pd.merge_asof(left, oi.rename("oi").reset_index().rename(columns={"create_time": "_s"}),
                       left_on="_t", right_on="_s", direction="backward", tolerance=pd.Timedelta(hours=1))
    o = al["oi"].to_numpy(float)
    h = bars["high"].to_numpy(float); lo = bars["low"].to_numpy(float); c = bars["close"].to_numpy(float)
    typ = (h + lo + c) / 3.0
    decay = 0.5 ** (1.0 / LIQ_HALF_LIFE_BARS)
    longs_p = np.empty(0); longs_m = np.empty(0)          # liquidation price, mass
    shorts_p = np.empty(0); shorts_m = np.empty(0)
    res = np.full((len(c), len(names)), np.nan)
    for j in range(len(c)):
        longs_m *= decay; shorts_m *= decay
        # the bar's range liquidates what it touched
        if len(longs_p):
            keep = longs_p < lo[j]
            longs_p, longs_m = longs_p[keep], longs_m[keep]
        if len(shorts_p):
            keep = shorts_p > h[j]
            shorts_p, shorts_m = shorts_p[keep], shorts_m[keep]
        if j > 0 and np.isfinite(o[j]) and np.isfinite(o[j - 1]) and o[j] > o[j - 1]:
            d = (o[j] - o[j - 1]) / len(LEVERAGE) / 2.0
            longs_p = np.r_[longs_p, [typ[j] * (1 - 1 / L_) for L_ in LEVERAGE]]
            longs_m = np.r_[longs_m, [d] * len(LEVERAGE)]
            shorts_p = np.r_[shorts_p, [typ[j] * (1 + 1 / L_) for L_ in LEVERAGE]]
            shorts_m = np.r_[shorts_m, [d] * len(LEVERAGE)]
            if len(longs_p) > 400:                     # drop the dust
                k = np.argsort(longs_m)[-300:]; longs_p, longs_m = longs_p[k], longs_m[k]
            if len(shorts_p) > 400:
                k = np.argsort(shorts_m)[-300:]; shorts_p, shorts_m = shorts_p[k], shorts_m[k]
        if not np.isfinite(o[j]) or o[j] <= 0 or not np.isfinite(atr[j]) or atr[j] <= 0:
            continue
        tot = o[j]
        r_, s_ = lv["res"][j], lv["sup"][j]
        up2, dn2 = c[j] + 2 * atr[j], c[j] - 2 * atr[j]
        row = [np.nan] * len(names)
        if np.isfinite(r_):
            row[0] = shorts_m[(shorts_p > c[j]) & (shorts_p <= r_)].sum() / tot
            row[2] = shorts_m[(shorts_p > r_) & (shorts_p <= r_ + atr[j])].sum() / tot
        if np.isfinite(s_):
            row[1] = longs_m[(longs_p < c[j]) & (longs_p >= s_)].sum() / tot
            row[3] = longs_m[(longs_p < s_) & (longs_p >= s_ - atr[j])].sum() / tot
        su = shorts_m[(shorts_p > c[j]) & (shorts_p <= up2)].sum() / tot
        ld = longs_m[(longs_p < c[j]) & (longs_p >= dn2)].sum() / tot
        row[4], row[5], row[6] = su, ld, su - ld
        res[j] = row
    for i, n in enumerate(names):
        out[n] = res[:, i]
    return out


# ------------------------------------------------------- Coinbase premium
COINBASE = "https://api.exchange.coinbase.com/products/{p}/candles?granularity=3600&start={a}&end={b}"
CB_DIR = L.FRAMES / "coinbase"


def cb_product(sym: str) -> str:
    base = sym.replace("USDT", "")
    return ("PEPE" if base == "1000PEPE" else base) + "-USD"


def coinbase_hourly(product: str, start="2021-01-01", end=None) -> pd.Series:
    """Hourly Coinbase closes, indexed by the hour's END (when the close is
    known), paged 300 at a time, cached; reruns fetch only what is new."""
    import json
    import urllib.request
    CB_DIR.mkdir(parents=True, exist_ok=True)
    path = CB_DIR / f"{product}_1h.pkl"
    have = pickle.load(open(path, "rb")) if path.exists() else pd.Series(dtype=float)
    t = pd.Timestamp(start, tz="UTC") if have.empty else have.index.max()
    stop = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC").floor("h")
    rows = []
    while t < stop:
        b = min(t + pd.Timedelta(hours=300), stop)
        url = COINBASE.format(p=product, a=t.strftime("%Y-%m-%dT%H:%M:%SZ"), b=b.strftime("%Y-%m-%dT%H:%M:%SZ"))
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "vanth-research"})
                data = json.loads(urllib.request.urlopen(req, timeout=30).read())
                break
            except Exception:
                time.sleep(2 + 3 * attempt)
        else:
            data = []
        rows += [(r[0], r[4]) for r in data if isinstance(r, list)]
        t = b
        time.sleep(0.15)
    if rows:
        new = pd.Series({pd.Timestamp(a, unit="s", tz="UTC") + pd.Timedelta(hours=1): float(c) for a, c in rows})
        have = pd.concat([have, new]).sort_index()
        have = have[~have.index.duplicated(keep="last")]
        pickle.dump(have, open(path, "wb"))
    return have


def premium(bars: pd.DataFrame, sym: str, btc: pd.Series) -> pd.DataFrame:
    """Coinbase over Binance at each 4h close, in basis points.

    The Binance close is the USDT perpetual's; the Coinbase close the USD
    spot's, so the raw premium carries the USDT/USD basis and the perp's
    own premium too. BTC's is the classic "Coinbase premium" (US demand,
    ETF-era institutions); a coin's premium RELATIVE to BTC's removes
    everything common to all coins."""
    out = pd.DataFrame(index=bars.index)
    key = bars.index.ceil("h")                     # 07:59:59.999 -> 08:00
    b = bars["close"].to_numpy(float)
    def at(series):
        return series.reindex(key).to_numpy(float) if not series.empty else np.full(len(bars), np.nan)
    own_path = CB_DIR / f"{cb_product(sym)}_1h.pkl"
    own = pickle.load(open(own_path, "rb")) if own_path.exists() else pd.Series(dtype=float)
    if sym.startswith("1000"):
        own = own * 1000.0                         # Binance quotes these per thousand
    p_own = pd.Series((at(own) / b - 1) * 1e4, index=bars.index)
    p_own = p_own.where(p_own.abs() < 500)        # a stale or broken print, not a premium
    out["cbp_own"] = p_own
    out["cbp_own_1d"] = p_own.rolling(6, min_periods=3).mean()
    out["cbp_own_z"] = _z(out["cbp_own_1d"], 180)
    return out


# --------------------------------------------------- higher-timeframe levels
def htf(bars: pd.DataFrame, atr: np.ndarray) -> pd.DataFrame:
    """Where the close sits against the levels desks quote and work orders
    around: last week's and last month's high and low, this week's and
    this month's open, and the nearest round number. The model already has
    yesterday's high and low; nothing longer. Week = Monday 00:00 UTC."""
    t = bars.index.tz_convert("UTC") - pd.Timedelta(milliseconds=1)   # the bar's own period
    h = bars["high"].astype(float); lo = bars["low"].astype(float)
    c = bars["close"].astype(float); o = bars["open"].astype(float)
    a = pd.Series(np.where(atr > 0, atr, np.nan), index=bars.index)
    out = pd.DataFrame(index=bars.index)
    for name, key in (("w", t.to_period("W-SUN")), ("m", t.to_period("M"))):
        k = pd.Series(key, index=bars.index)
        per = pd.DataFrame({"h": h.values, "l": lo.values, "o": o.values}, index=key).groupby(level=0) \
            .agg({"h": "max", "l": "min", "o": "first"})
        prev = per.shift(1)
        ph = k.map(prev["h"]).astype(float); pl = k.map(prev["l"]).astype(float)
        op = k.map(per["o"]).astype(float)
        out[f"htf_p{name}h"] = (c - ph) / a
        out[f"htf_p{name}l"] = (c - pl) / a
        out[f"htf_{name}open"] = (c - op) / a
        out[f"htf_p{name}_pos"] = (c - pl) / (ph - pl).replace(0, np.nan)
    # round numbers: the nearest multiple of a tenth and of half the price's
    # order of magnitude (BTC at 86,000: 1,000 and 5,000; DOGE at 0.24: 0.01
    # and 0.05)
    mag = 10.0 ** np.floor(np.log10(c.clip(lower=1e-12)).to_numpy())
    cv = c.to_numpy()
    for name, m in (("htf_round_10th", 0.1), ("htf_round_half", 0.5)):
        step = mag * m
        out[name] = np.abs(cv - np.round(cv / step) * step) / a.to_numpy()
    return out


# ------------------------------------------------------------------ blocks
def _coin(sym: str):
    bars = pickle.load(open(L.FRAMES / f"{sym}_4h_frames.pkl", "rb"))[0]
    atr = _atr(bars, 14).to_numpy(float)
    return bars, atr, structural_levels(bars, atr)


def build_block(name: str) -> pd.DataFrame:
    parts = []
    t0 = time.time()
    for s in L.coins():
        bars, atr, lv = _coin(s)
        if name == "flw":
            B = flow(bars)
        elif name == "swp":
            B = sweeps(bars, atr, lv)
        elif name == "liq":
            B = liquidations(s, bars, atr, lv)
        elif name == "htf":
            B = htf(bars, atr)
        elif name == "cbp":
            B = premium(bars, s, None)
            if s == "BTCUSDT":
                btc_p = B["cbp_own_1d"].rename("cbp_btc_1d")
        else:
            raise KeyError(name)
        B = B.replace([np.inf, -np.inf], np.nan)
        B["coin"] = s; B["t"] = B.index
        parts.append(B.reset_index(drop=True))
        print(f"  {name} {s}: {int(B.drop(columns=['coin', 't']).notna().all(axis=1).sum()):,} complete rows "
              f"[{time.time() - t0:.0f}s]", flush=True)
    F = pd.concat(parts, ignore_index=True)
    if name == "cbp":
        # the market-wide premium (BTC's) on every coin, and each coin's
        # premium over BTC's, which strips the USDT basis they all share
        btc = F[F["coin"] == "BTCUSDT"].set_index("t")["cbp_own_1d"]
        F["cbp_btc_1d"] = btc.reindex(F["t"]).to_numpy()
        F["cbp_btc_z"] = F["t"].map(_z(btc, 180))
        F["cbp_btc_chg1d"] = F["t"].map(btc - btc.shift(6))
        F["cbp_rel_1d"] = F["cbp_own_1d"] - F["cbp_btc_1d"]
        F.loc[F["coin"] == "BTCUSDT", "cbp_rel_1d"] = np.nan
    L.CACHE.mkdir(parents=True, exist_ok=True)
    pickle.dump(F, open(L.CACHE / f"block_{name}.pkl", "wb"))
    return F


def block(name: str) -> pd.DataFrame:
    p = L.CACHE / f"block_{name}.pkl"
    if p.exists():
        return pickle.load(open(p, "rb"))
    return build_block(name)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "coinbase":
        for s in L.coins():
            t0 = time.time()
            x = coinbase_hourly(cb_product(s))
            print(f"  {cb_product(s)}: {len(x):,} hours, from {x.index.min() if len(x) else '-'} "
                  f"[{time.time() - t0:.0f}s]", flush=True)
    if len(sys.argv) > 2 and sys.argv[1] == "build":
        for n in sys.argv[2:]:
            build_block(n)
