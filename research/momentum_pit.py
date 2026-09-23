"""The momentum rotation on a universe chosen the way it would have been
chosen at the time -- and the refinements the literature says matter.

THE QUESTION THE FIRST STUDY COULD NOT ANSWER
    research/new_strategies.py ran the rotation on the fifteen coins the
    server serves today. Today's list is survivors: a coin is on it partly
    because it went up. Here the universe at each Monday is the N perpetuals
    with the most dollar volume over the previous 30 days, among every USDT
    perpetual Binance listed (research/universe.py, delisted ones included).
    A coin that died mid-week is exited at its last close -- LUNA's -99.99%
    is in here.

VARIANTS (each a known result somewhere; none tested here before)
    size of the universe and of the legs   N 15/30/50, k 3/5/quintile
    risk-adjusted momentum                 30d return / 30d volatility
    residual momentum                      30d return net of beta x BTC's
                                           (Blitz, Huij & Martens 2011)
    skipping the last days                 30d return to t-2 (short-term
                                           reversal lives in the last days)
    inverse-volatility weights             each leg's coins sized 1/vol
    volatility-managed exposure            the book scaled to a target vol
                                           from its own trailing weeks
                                           (Barroso & Santa-Clara 2015)

PROTOCOL
    Weekly, from Monday's open (Sunday's close) to the next. Net of 0.10%
    per position per rebalance, like the first study. Run at all seven
    weekday phases; the median phase is the number. CHOSEN on Jan 2021 ->
    Jun 2024, CONFIRMED on Jul 2024 -> Aug 2026.

    python research/momentum_pit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.universe import panel     # noqa: E402

COST = 0.10
DEV_END = pd.Timestamp("2024-07-01", tz="UTC")
START = pd.Timestamp("2021-01-01", tz="UTC")
STABLE = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "DAIUSDT", "USDEUSDT",
          "USD1USDT", "RLUSDUSDT", "BFUSDUSDT", "XUSDUSDT"}

_P = {}


def data():
    if not _P:
        C = panel("close"); V = panel("quote_volume")
        keep = [c for c in C.columns if c not in STABLE]
        C, V = C[keep], V[keep]
        # Binance's archive is missing a few days (Feb and Apr 2022) for many
        # coins -- not BTC or ETH. Unfilled, the 60-day history rule dropped
        # SOL, XRP, NEAR and LUNA from the universe for two months. Interior
        # gaps up to 5 days are carried; never past a coin's last real close,
        # so a delisted coin does not linger at a frozen price.
        alive = C.bfill().notna()
        C = C.ffill(limit=5).where(alive)
        _P["C"], _P["V"] = C, V
        _P["R"] = np.log(C).diff()
    return _P["C"], _P["V"], _P["R"]


def universe(t: pd.Timestamp, n: int) -> list:
    """The n coins with the most dollar volume over the 30 days before t,
    among those with 60 days of history and a close on the day before t."""
    C, V, _ = data()
    prev = t - pd.Timedelta(days=1)
    if prev not in C.index:
        return []
    hist = C.loc[:prev].tail(60)
    ok = hist.notna().sum() >= 60
    ok &= C.loc[prev].notna()
    vol = V.loc[prev - pd.Timedelta(days=29):prev].median()
    vol = vol[ok[ok].index].dropna().sort_values(ascending=False)
    return list(vol.index[:n])


def signal(t: pd.Timestamp, coins: list, kind: str) -> pd.Series:
    C, _, R = data()
    prev = t - pd.Timedelta(days=1)
    c = C[coins]
    if kind.startswith("mom"):                          # mom30, mom30s2 (skip 2), mom15 ...
        lb = int(kind[3:].split("s")[0]); skip = int(kind.split("s")[1]) if "s" in kind[3:] else 0
        end = prev - pd.Timedelta(days=skip)
        return c.loc[end] / c.loc[end - pd.Timedelta(days=lb)] - 1
    if kind.startswith("high"):                         # high20, high55: distance to the N-day high
        n = int(kind[4:])
        return c.loc[prev] / c.loc[prev - pd.Timedelta(days=n - 1):prev].max() - 1
    if kind.startswith("flow"):                         # flow7: net taker share, net of past returns
        n = int(kind[4:])
        tq = _P.get("TQ")
        if tq is None:
            tq = _P["TQ"] = panel("taker_buy_quote_volume")
        _, V, _ = data()
        win = slice(prev - pd.Timedelta(days=n - 1), prev)
        nf = (2 * tq[coins].loc[win].sum() - V[coins].loc[win].sum()) / V[coins].loc[win].sum()
        r7 = c.loc[prev] / c.loc[prev - pd.Timedelta(days=7)] - 1
        r30 = c.loc[prev] / c.loc[prev - pd.Timedelta(days=30)] - 1
        ok = nf.notna() & r7.notna() & r30.notna() & np.isfinite(nf)
        if ok.sum() < 8:
            return nf * np.nan
        A = np.c_[np.ones(ok.sum()), r7[ok], r30[ok]]
        beta, *_ = np.linalg.lstsq(A, nf[ok].to_numpy(float), rcond=None)
        out = nf * np.nan
        out[ok] = nf[ok] - A @ beta                    # the flow the returns do not explain
        return out
    if kind.startswith("combo"):                        # combo: mean cross-sectional rank of mom15 and flow7
        a = signal(t, coins, "mom15").rank(pct=True)
        b = signal(t, coins, "flow7").rank(pct=True)
        return (a + b) / 2
    if kind == "radj30":
        r30 = c.loc[prev] / c.loc[prev - pd.Timedelta(days=30)] - 1
        return r30 / R[coins].loc[prev - pd.Timedelta(days=29):prev].std()
    if kind == "resid30":
        r = R[coins].loc[prev - pd.Timedelta(days=89):prev]
        b = R["BTCUSDT"].loc[r.index]
        beta = r.apply(lambda x: np.cov(x.dropna(), b[x.dropna().index])[0, 1] / b[x.dropna().index].var()
                       if x.notna().sum() > 30 else np.nan)
        last = r.loc[prev - pd.Timedelta(days=29):]
        return last.sum() - beta * b.loc[last.index].sum()
    raise KeyError(kind)


def week_return(t: pd.Timestamp, coins: list) -> pd.Series:
    """Each coin's return from the close before t to the close 7 days on;
    a coin with no close there is exited at its last close in the week."""
    C, _, _ = data()
    a = C.loc[t - pd.Timedelta(days=1), coins]
    wk = C.loc[t:t + pd.Timedelta(days=6), coins]
    b = wk.ffill().iloc[-1] if len(wk) else a * np.nan
    return b / a - 1


def run(n=15, k=3, kind="mom30", weights="equal", phase=0, vol_target=None, long_only=False) -> pd.Series:
    """% return on capital per week, indexed by the rebalance time."""
    C, _, R = data()
    first = START + pd.Timedelta(days=(phase - START.dayofweek) % 7)
    out = {}
    for t in pd.date_range(first, C.index.max() - pd.Timedelta(days=7), freq="7D"):
        coins = universe(t, n)
        if len(coins) < max(8, 2 * (k if isinstance(k, int) else 2)):
            continue
        s = signal(t, coins, kind).dropna()
        f = week_return(t, list(s.index)).dropna(); s = s[f.index]
        kk = max(1, int(round(len(s) * k))) if isinstance(k, float) else k
        o = s.sort_values()
        lo, hi = list(o.index[:kk]), list(o.index[-kk:])
        if weights == "invvol":
            vol = R[list(s.index)].loc[t - pd.Timedelta(days=30):t - pd.Timedelta(days=1)].std()
            wl = (1 / vol[hi]); wl /= wl.sum(); ws = (1 / vol[lo]); ws /= ws.sum()
        else:
            wl = pd.Series(1 / kk, index=hi); ws = pd.Series(1 / kk, index=lo)
        if long_only:
            out[t] = 100 * ((f[hi] * wl).sum() - f.mean()) - COST     # over holding the whole universe
        else:
            out[t] = 100 * (0.5 * (f[hi] * wl).sum() - 0.5 * (f[lo] * ws).sum()) - COST
    r = pd.Series(out)
    if vol_target:
        # scale each week by target / trailing 26-week vol of the book itself
        tv = r.rolling(26, min_periods=12).std().shift(1)
        r = r * (vol_target / tv).clip(upper=2.0)
        r = r.dropna()
    return r


def sharpe(r: pd.Series) -> float:
    return float(r.mean() / r.std() * np.sqrt(52)) if len(r) > 10 and r.std() > 0 else float("nan")


def phases(**kw) -> dict:
    """Median Sharpe over the seven weekday phases, dev and holdout."""
    dev, ho, pos, yr = [], [], [], []
    for ph in range(7):
        r = run(phase=ph, **kw)
        dev.append(sharpe(r[r.index < DEV_END])); ho.append(sharpe(r[r.index >= DEV_END]))
        hy = r.groupby([r.index.year, r.index.month <= 6]).sum()
        pos.append(float((hy > 0).mean())); yr.append(float(r.mean() * 52))
    return {"dev": float(np.median(dev)), "dev_min": float(np.min(dev)), "hold": float(np.median(ho)),
            "hold_min": float(np.min(ho)), "pos_half": float(np.median(pos)), "pct_yr": float(np.median(yr))}


def show(name: str, **kw) -> None:
    x = phases(**kw)
    print(f"  {name:<44} dev {x['dev']:+.2f} (min {x['dev_min']:+.2f})  hold {x['hold']:+.2f} "
          f"(min {x['hold_min']:+.2f})  {x['pct_yr']:+6.1f}%/yr  positive half-years {x['pos_half']:.0%}",
          flush=True)


def main() -> int:
    print("weekly rotation, median of 7 weekday phases; dev Jan 2021-Jun 2024 chooses, hold Jul 2024-Aug 2026 confirms")
    print("UNIVERSE (point in time, by 30-day dollar volume, delisted coins included)")
    show("top 15, 3/3, 30d (the served rule, honest universe)", n=15, k=3)
    show("top 30, 3/3, 30d", n=30, k=3)
    show("top 30, 5/5, 30d", n=30, k=5)
    show("top 50, 5/5, 30d", n=50, k=5)
    show("top 50, quintiles, 30d", n=50, k=0.2)
    print("SIGNAL (top 30, 5/5)")
    for kind in ("mom15", "mom30s2", "mom60", "radj30", "resid30", "high20", "high55"):
        show(kind, n=30, k=5, kind=kind)
    print("WEIGHTS AND EXPOSURE (top 30, 5/5, 30d)")
    show("inverse-vol weights", n=30, k=5, weights="invvol")
    show("vol-managed book (target 3%/week)", n=30, k=5, vol_target=3.0)
    print("LONG-ONLY over holding the universe (spot users)")
    show("top 15, best 3", n=15, k=3, long_only=True)
    show("top 30, best 5", n=30, k=5, long_only=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
