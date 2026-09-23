"""Strategy families this project has never run, and timing filters on the one it does.

    1. TIMING on the live 4h calls: the bar-close hour, the weekday, and
       Bitcoin's 200-day trend as a gate (the daily rule's cousin).
    2. CROWD CONTRARIAN, market-neutral: every three days, long the three
       coins whose crowd (all accounts' long/short ratio, 30-day z) leans
       most SHORT, short the three leaning most LONG. research/positioning.py
       found that ordering robust (9 of 11 half-years) -- as a spread between
       coins, which is exactly what a long/short pair collects and a
       one-direction call cannot.
    3. MOMENTUM ROTATION: every week, long the three coins with the best
       7-day (or 30-day) return, short the three worst. The documented
       crypto factor (Liu, Tsyvinski & Wu 2022).
    4. FUNDING EXTREMES: the settled funding rate's 30-day z, per coin. A
       crowded, paying long side (z > 2) is faded for three days, and the
       mirror. And the cross-sectional version: long the lowest funding,
       short the highest.

Every figure is net of 0.10% per position per rebalance, reported per
half-year so one good year cannot carry a verdict.

    python research/new_strategies.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research.wf4h as L                       # noqa: E402

COST = 0.10


def closes() -> pd.DataFrame:
    return pd.DataFrame({c: pickle.load(open(L.FRAMES / f"{c}_4h_frames.pkl", "rb"))[0]["close"]
                         for c in L.coins()}).sort_index()


def half(ix) -> pd.Series:
    ix = pd.DatetimeIndex(ix)
    return pd.Series(ix.year.astype(str) + np.where(ix.month <= 6, "H1", "H2"), index=ix)


def report(name: str, per: pd.Series, periods_per_year: float) -> None:
    """`per` = % return on capital per rebalance, indexed by rebalance time."""
    per = per.dropna()
    if per.empty:
        print(f"   {name:<52} none"); return
    by = per.groupby(half(per.index).to_numpy()).sum()
    sh = per.mean() / per.std() * np.sqrt(periods_per_year) if per.std() > 0 else 0.0
    recent = per[per.index >= "2023-09-20"]
    sh_r = recent.mean() / recent.std() * np.sqrt(periods_per_year) if len(recent) > 5 and recent.std() > 0 else 0.0
    print(f"   {name:<52} {per.mean() * periods_per_year:+6.1f}%/yr  Sharpe {sh:+.2f} "
          f"(since Sep 2023 {sh_r:+.2f})  positive half-years {int((by > 0).sum())}/{len(by)}")


def cross_section(score: pd.DataFrame, fwd: pd.DataFrame, every: int, k: int = 3,
                  low_is_long: bool = True, min_coins: int = 8) -> pd.Series:
    """Long the k lowest scores, short the k highest (or the reverse), held
    `every` bars, rebalanced every `every` bars. Half the capital a side."""
    out = {}
    for t in score.index[::every]:
        s = score.loc[t].dropna(); f = fwd.loc[t]
        s = s[f.reindex(s.index).notna()]
        if len(s) < min_coins:
            continue
        o = s.sort_values()
        lo, hi = o.index[:k], o.index[-k:]
        longs, shorts = (lo, hi) if low_is_long else (hi, lo)
        out[t] = 0.5 * f[longs].mean() - 0.5 * f[shorts].mean() - COST
    return pd.Series(out)


def timing() -> None:
    import research.improve_4h as I

    print("1. TIMING on the live 4h calls (resting entry 0.5 ATR / 4 bars), account per slice")
    E = I.with_limit(I.calls(), 0.5, 4)
    C = closes()
    btc = C["BTCUSDT"]; btc_up = (btc > btc.ewm(span=1200, adjust=False).mean())
    t = pd.DatetimeIndex(E["t"])
    E["hour"] = t.hour; E["wd"] = t.dayofweek
    E["btc_up"] = btc_up.reindex(t).to_numpy()
    I.line("all", E)
    with_trend = ((E["side"] == "long") & E["btc_up"]) | ((E["side"] == "short") & ~E["btc_up"])
    I.line("only WITH Bitcoin's 200-day trend", E[with_trend])
    I.line("only AGAINST it", E[~with_trend])
    net = E["pnl"] - L.COST - E["funding"]
    for col, name in (("hour", "bar closing (UTC)"), ("wd", "weekday (0 = Mon)")):
        rows = []
        for v, g in E.assign(net=net).groupby(col):
            dev = g[g["window"] < L.DEV_WINDOWS]["net"].mean(); hold = g[g["window"] >= L.DEV_WINDOWS]["net"].mean()
            rows.append(f"{v}: {dev:+.2f}/{hold:+.2f}")
        print(f"   per trade by {name}, dev/hold: " + "  ".join(rows))


def main() -> int:
    timing()
    C = closes()
    fwd18 = 100 * (C.shift(-18) / C - 1)
    fwd42 = 100 * (C.shift(-42) / C - 1)

    print("\n2. CROWD CONTRARIAN, market-neutral (3 long / 3 short, rebalanced every 3 days)")
    P = L.load("long")[["coin", "t", "pos_crowd_z", "pos_smart_vs_crowd_z", "pos_top_pos_z"]]
    for col, low_long, name in (("pos_crowd_z", True, "long crowd-short, short crowd-long"),
                                ("pos_smart_vs_crowd_z", False, "long where big traders out-long the crowd"),
                                ("pos_top_pos_z", False, "long where big traders are most long")):
        S = P.pivot_table(index="t", columns="coin", values=col).reindex(C.index)
        report(name, cross_section(S, fwd18, 18, low_is_long=low_long), 365 / 3)

    print("\n3. MOMENTUM ROTATION (3 long / 3 short, rebalanced weekly)")
    for lb, name in ((42, "7-day"), (180, "30-day")):
        mom = 100 * (C / C.shift(lb) - 1)
        report(f"long the best {name} returns, short the worst", cross_section(mom, fwd42, 42, low_is_long=False), 52)
        report(f"the REVERSE (reversal): long the worst {name}", cross_section(mom, fwd42, 42, low_is_long=True), 52)

    print("\n4. FUNDING EXTREMES")
    F = {}
    for c in C.columns:
        f = L._funding(c)
        if f.empty:
            continue
        z = (f - f.rolling(90).mean()) / f.rolling(90).std()          # 90 settlements = 30 days
        F[c] = z.reindex(C.index, method="ffill")
    Z = pd.DataFrame(F).reindex(columns=C.columns)
    report("long the lowest funding z, short the highest (3-day)", cross_section(Z, fwd18, 18, low_is_long=True), 365 / 3)
    # the directional fade, per coin, one position per coin per 3 days
    rets = []
    for c in Z.columns:
        z = Z[c]; f = fwd18[c]
        t = z.index[::18]
        for ti in t:
            zi = z.get(ti)
            if zi is None or not np.isfinite(zi) or not np.isfinite(f.get(ti, np.nan)):
                continue
            if zi > 2:
                rets.append((ti, -f[ti] - COST))
            elif zi < -2:
                rets.append((ti, f[ti] - COST))
    # a list, not a dict: several coins fire at the same close
    R = (pd.Series([v for _, v in rets], index=pd.DatetimeIndex([t for t, _ in rets])).sort_index()
         if rets else pd.Series(dtype=float))
    if len(R):
        by = R.groupby(half(R.index).to_numpy()).mean()
        print(f"   fade funding |z|>2 for 3 days, per trade: n {len(R)}  net {R.mean():+.2f}%  "
              f"hit {(R > 0).mean():.0%}  positive half-years {int((by > 0).sum())}/{len(by)}")
    return 0


if __name__ == "__main__" and len(sys.argv) == 1:
    raise SystemExit(main())


def momentum_checks() -> None:
    """The two ways the momentum result could be fooling us.

    SURVIVORSHIP: the fifteen coins are today's list, and a coin that rallied
    is more likely to be on it -- which momentum would then 'find'. So the
    rotation is rerun on the ten coins already established in 2021.
    AS A SPOT STRATEGY: long-only, the best three against holding all ten.
    """
    C = closes()
    fwd42 = 100 * (C.shift(-42) / C - 1)
    old = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
           "BNBUSDT", "UNIUSDT", "NEARUSDT", "ZECUSDT"]
    mom = 100 * (C / C.shift(180) - 1)
    for k in (2, 3):
        report(f"10 coins established in 2021, {k} long / {k} short",
               cross_section(mom[old], fwd42[old], 42, k=k, low_is_long=False), 52)
    lo, ew = {}, {}
    for t in mom.index[::42]:
        s = mom[old].loc[t].dropna(); f = fwd42[old].loc[t].reindex(s.index).dropna(); s = s[f.index]
        if len(s) < 8:
            continue
        lo[t] = f[s.sort_values().index[-3:]].mean() - COST
        ew[t] = f.mean()
    lo, ew = pd.Series(lo), pd.Series(ew)
    report("long-only: best 3 of the ten by 30-day return", lo, 52)
    report("hold all ten, equal weight", ew, 52)
    report("the difference", lo - ew, 52)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "momentum":
    momentum_checks()
