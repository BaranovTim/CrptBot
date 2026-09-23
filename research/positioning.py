"""What Binance's big traders are positioned for, and whether it helps.

THE DATA
    Binance's daily metrics archive, every five minutes per coin since each
    perpetual listed: top traders' long/short ratio by ACCOUNTS and by
    POSITION (the top 20% of accounts by margin), every account's long/short
    ratio (the crowd), and the taker buy/sell volume ratio. The project
    downloaded these for years and kept only open interest. Features are
    built in research/wf4h.py (`pos_*`): log ratios, their 30-day z-scores,
    their one-day change, and `smart_vs_crowd` = top traders' position
    ratio minus the crowd's.

THE QUESTIONS
    1. On its own, does positioning say where price goes? Forward returns by
       quintile, both raw and DEMEANED across coins at the same close (so a
       market-wide rally does not masquerade as a signal), for each
       half-year separately -- a pattern that flips between years is not one.
    2. On the live 4h calls, does agreement with the big traders separate
       the winners? The same way the Hyperliquid overlay was tested.
    3. As a strategy of its own: fade a crowded crowd when the big traders
       are on the other side. Fixed thresholds, nothing fitted.

    python research/positioning.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research.wf4h as L                       # noqa: E402

HORIZONS = {"24h": 6, "3d": 18}
FEATS = ["pos_crowd_z", "pos_top_pos_z", "pos_smart_vs_crowd_z", "pos_top_pos_chg1d",
         "pos_crowd_chg1d", "pos_taker_1d_z"]


def panel() -> pd.DataFrame:
    """Every coin's 4h bars with positioning and forward returns."""
    D = L.load("long")[["coin", "t"] + [c for c in L.load("long").columns if c.startswith("pos_")]]
    fwd = []
    for coin in D["coin"].unique():
        bars = pickle.load(open(L.FRAMES / f"{coin}_4h_frames.pkl", "rb"))[0]
        c = bars["close"]
        f = pd.DataFrame({"coin": coin, "t": bars.index})
        for name, k in HORIZONS.items():
            f[f"fwd_{name}"] = (100 * (c.shift(-k) / c - 1)).to_numpy()
        fwd.append(f)
    P = D.merge(pd.concat(fwd, ignore_index=True), on=["coin", "t"], how="left")
    for name in HORIZONS:
        med = P.groupby("t")[f"fwd_{name}"].transform("median")
        P[f"xs_{name}"] = P[f"fwd_{name}"] - med          # coin minus the market
    P["half"] = P["t"].dt.year.astype(str) + np.where(P["t"].dt.month <= 6, "H1", "H2")
    return P.dropna(subset=["pos_crowd_z"])


def q1(P: pd.DataFrame) -> None:
    print("1. ON ITS OWN -- forward return by quintile of each feature (all coins, 4h closes)")
    print("   demeaned = the coin's return minus the median coin's at the same close\n")
    for f in FEATS:
        x = P.dropna(subset=[f, "xs_3d"])
        q = pd.qcut(x[f], 5, labels=False, duplicates="drop")
        tab = x.groupby(q)[["fwd_24h", "fwd_3d", "xs_24h", "xs_3d"]].mean()
        spread = tab["xs_3d"].iloc[-1] - tab["xs_3d"].iloc[0]
        # does the top-minus-bottom spread keep its sign half-year to half-year?
        halves = []
        for h, g in x.groupby("half"):
            if len(g) < 2000:
                continue
            qq = pd.qcut(g[f], 5, labels=False, duplicates="drop")
            t = g.groupby(qq)["xs_3d"].mean()
            halves.append(t.iloc[-1] - t.iloc[0])
        same = sum(1 for s in halves if np.sign(s) == np.sign(spread))
        print(f"   {f:<22} demeaned 3d, lowest -> highest quintile: "
              + "  ".join(f"{v:+.2f}" for v in tab["xs_3d"])
              + f"   | top-bottom {spread:+.2f}%, same sign in {same}/{len(halves)} half-years")


def q2(P: pd.DataFrame) -> None:
    print("\n2. ON THE LIVE 4h CALLS -- does agreeing with the big traders separate the winners?")
    R = L.with_ranks(pickle.load(open(L.SCORES / "stop_beyond_0.5_scores.pkl", "rb")))
    C = R[R["rank_pool"] >= 0.97].copy()
    C = C.merge(P[["coin", "t"] + FEATS], on=["coin", "t"], how="left")
    sgn = np.where(C["side"] == "long", 1.0, -1.0)
    C["net"] = C["pnl"] - L.COST - C["funding"]
    tests = {
        "top traders ADDED to this side in 1d": sgn * C["pos_top_pos_chg1d"] > 0,
        "top traders net on this side (z>0)": sgn * C["pos_top_pos_z"] > 0,
        "crowd leans the OTHER way (z<0)": sgn * C["pos_crowd_z"] < 0,
        "big traders more this way than crowd": sgn * C["pos_smart_vs_crowd_z"] > 0,
    }
    for name, m in tests.items():
        m = m.fillna(False)
        cells = []
        for part, w in (("dev", C["window"] < L.DEV_WINDOWS), ("hold", C["window"] >= L.DEV_WINDOWS)):
            a, b = C[w & m]["net"], C[w & ~m & C[FEATS[0]].notna()]["net"]
            cells.append(f"{part}: agree {a.mean():+.3f}% (n {len(a)}) vs not {b.mean():+.3f}% (n {len(b)})")
        print(f"   {name:<40} " + " | ".join(cells))


def q3(P: pd.DataFrame) -> None:
    print("\n3. AS A STRATEGY OF ITS OWN -- fade the crowd when the big traders disagree with it")
    print("   (enter at the close, hold 3 days, 0.10% fees; no model; fixed thresholds)")
    for name, sig in (
        ("long: crowd short z<-1.5, big traders longer", (P["pos_crowd_z"] < -1.5) & (P["pos_smart_vs_crowd_z"] > 1.0)),
        ("short: crowd long z>1.5, big traders shorter", (P["pos_crowd_z"] > 1.5) & (P["pos_smart_vs_crowd_z"] < -1.0)),
        ("long: big traders' 1d add in top 5%", P["pos_top_pos_chg1d"] > P["pos_top_pos_chg1d"].quantile(0.95)),
        ("short: big traders' 1d cut in bottom 5%", P["pos_top_pos_chg1d"] < P["pos_top_pos_chg1d"].quantile(0.05)),
    ):
        s = 1.0 if name.startswith("long") else -1.0
        x = P[sig.fillna(False)].dropna(subset=["fwd_3d"])
        # one position per coin per 3 days: keep an entry only if the last kept is >= 18 bars back
        x = x.sort_values(["coin", "t"])
        keep, last = [], {}
        for r in x.itertuples():
            if r.coin not in last or (r.t - last[r.coin]) >= pd.Timedelta(hours=72):
                keep.append(r.Index); last[r.coin] = r.t
        x = x.loc[keep]
        net = s * x["fwd_3d"] - L.COST
        mkt = s * x["xs_3d"]
        by = x.assign(net=net).groupby("half")["net"].mean()
        print(f"   {name:<46} n {len(x):4d}  net {net.mean():+.2f}%/trade  hit {(net > 0).mean():.0%}  "
              f"vs market {mkt.mean():+.2f}%   positive half-years {int((by > 0).sum())}/{len(by)}")


def main() -> int:
    P = panel()
    print(f"{len(P):,} coin-bars with positioning, {P['coin'].nunique()} coins, "
          f"{P['t'].min():%Y-%m} -> {P['t'].max():%Y-%m}\n")
    q1(P); q2(P); q3(P)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
