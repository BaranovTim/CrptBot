"""Would the followed traders' entries improve the model's score?

THE QUESTION
    The smart-money feed is deliberately outside Agent 5: it is shown, and
    it notifies, but it does not touch the percentage. Tim's instinct is
    that it should move the number a lot. This measures it, causally, on
    the year the 4h models never saw.

THE FEATURE, BUILT AS IT COULD HAVE BEEN BUILT AT THE TIME
    A trader counts as "followed" on day D if, in the 180 days before D,
    they closed at least 8 position trades with a positive total and a win
    rate between 50% and 92% -- the selection the tracker runs. That
    membership is recomputed daily from fills before D only. Then per
    (coin, 4h bar):

      smart_net_24h   entries by followed traders on that coin in the 24h
                      before the bar's close: +1 per long opened, -1 per
                      short opened
      smart_net_72h   the same over 72h
      smart_exposure  followed traders long minus short on the coin at the
                      bar's close

    Signed by the model's side, so "agrees" means the feature points the
    way the side model would trade.

THE TESTS
    1. Coverage: how many test-year bars have any smart-money activity at
       all. If the answer is "almost none", the feature cannot change the
       number broadly whatever else is true.
    2. On the model's own calls (top 15% of the trailing rank, as served):
       the book split by the feature agreeing / disagreeing / silent.
    3. On all bars: does the feature alone rank the label? And added to
       the model's score in a cross-validated logistic fit, does the AUC
       move?
    4. As an overlay: forward 24h return from the bar's close when a
       followed trader entered in the last 24h, both sides pooled --
       the number a "smart money agrees" badge would be worth.

    python research/smart_feature.py
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.structure_rules import rolling_rank              # noqa: E402
from research.trader_patterns import load_trades, position_trades  # noqa: E402

COST = 0.10
FILLS = Path("data_cache/research_frames/deep_fills")
FRAMES = Path("data_cache/research_frames")
SCORES = Path("data_cache/research_frames/holdout_scores.pkl")
LOOKBACK = pd.Timedelta(days=180)
MIN_TRADES, MIN_WIN, MAX_WIN = 8, 0.50, 0.92


def cohort_by_day(trades: pd.DataFrame) -> dict:
    """{address: DatetimeIndex of days on which the trader was followed}."""
    out = {}
    days = pd.date_range(trades["t_close"].min().normalize(), trades["t_open"].max().normalize(), freq="1D")
    for addr, g in trades.groupby("address"):
        closed = g.sort_values("t_close")
        member = []
        for d in days:
            w = closed[(closed["t_close"] < d) & (closed["t_close"] >= d - LOOKBACK)]
            if len(w) >= MIN_TRADES and w["pnl"].sum() > 0 and MIN_WIN <= (w["pnl"] > 0).mean() <= MAX_WIN:
                member.append(d)
        if member:
            out[addr] = pd.DatetimeIndex(member)
    return out


def features_for(sym: str, bar_index: pd.DatetimeIndex, trades: pd.DataFrame, cohort: dict) -> pd.DataFrame:
    """The three features per bar close, from cohort members' trades on `sym`."""
    g = trades[trades["symbol"] == sym]
    ev_t, ev_s, ex = [], [], []
    for _, t in g.iterrows():
        days = cohort.get(t["address"])
        if days is None or t["t_open"].normalize() not in days:
            continue
        s = 1.0 if t["side"] == "LONG" else -1.0
        ev_t.append(t["t_open"]); ev_s.append(s)
        ex.append((t["t_open"], t["t_close"], s))
    ev_t = pd.DatetimeIndex(ev_t); ev_s = np.asarray(ev_s)
    n = len(bar_index)
    f24 = np.zeros(n); f72 = np.zeros(n); expo = np.zeros(n)
    for i, tc in enumerate(bar_index):
        if len(ev_t):
            m24 = (ev_t > tc - pd.Timedelta(hours=24)) & (ev_t <= tc)
            m72 = (ev_t > tc - pd.Timedelta(hours=72)) & (ev_t <= tc)
            f24[i] = ev_s[m24].sum(); f72[i] = ev_s[m72].sum()
        expo[i] = sum(s for a, b, s in ex if a <= tc < b)
    return pd.DataFrame({"smart_net_24h": f24, "smart_net_72h": f72, "smart_exposure": expo}, index=bar_index)


def book(x):
    x = np.asarray(x, float) - COST
    if len(x) == 0:
        return "none"
    return f"n {len(x):4d}  net {x.mean():+.3f}% ±{x.std(ddof=1)/np.sqrt(len(x)) if len(x) > 1 else float('nan'):.3f}  win {(x > 0).mean():.0%}"


def main() -> int:
    df = load_trades(FILLS)
    pos = position_trades(df)
    pos = pos[pos["symbol"] != ""]
    print(f"{len(pos):,} position trades from {pos['address'].nunique()} traders, "
          f"{pos['t_open'].min().date()} → {pos['t_open'].max().date()}")
    cohort = cohort_by_day(pos)
    first = min(d.min() for d in cohort.values())
    print(f"cohort definable from {first.date()}: {len(cohort)} traders ever qualify; "
          f"median days in cohort {np.median([len(d) for d in cohort.values()]):.0f}")

    scores = pickle.load(open(SCORES, "rb"))
    rows = []
    for r in scores:
        sym, side = r["symbol"], r["side"]
        t = pd.DatetimeIndex(r["t"])
        F = features_for(sym, t, pos, cohort)
        sgn = 1.0 if side == "long" else -1.0
        rank = np.full(len(t), np.nan)
        p = r["p"]
        for i in range(len(p)):
            past = p[max(0, i - 540):i]
            if len(past) >= 50:
                rank[i] = (past < p[i]).mean()
        win = "upper" if side == "long" else "lower"
        for i in range(len(t)):
            rows.append({"symbol": sym, "side": side, "t": t[i], "p": p[i], "rank": rank[i],
                         "y": float(r["touch"][i] == win), "pnl": r["pnl"][i], "w": r["w"][i],
                         "f24": sgn * F["smart_net_24h"].iloc[i], "f72": sgn * F["smart_net_72h"].iloc[i],
                         "expo": sgn * F["smart_exposure"].iloc[i], "defined": t[i] >= first})
    D = pd.DataFrame(rows)
    D = D[D["defined"]]
    print(f"\ntest-year bars with a definable cohort: {len(D):,} ({D['t'].min().date()} → {D['t'].max().date()})")

    # 1. coverage
    print("\n1. COVERAGE -- how often is there anything to add?")
    for c in ("f24", "f72", "expo"):
        nz = (D[c] != 0).mean()
        print(f"   {c:<5} non-zero on {100*nz:5.1f}% of bars; agrees {100*(D[c] > 0).mean():4.1f}%, disagrees {100*(D[c] < 0).mean():4.1f}%")
    per = D.groupby("symbol")["f24"].apply(lambda s: (s != 0).mean())
    print("   by coin (f24 non-zero):", ", ".join(f"{k} {100*v:.1f}%" for k, v in per.items()))

    # 2. on the served calls
    calls = D[D["rank"] >= 0.85]
    print(f"\n2. ON THE MODEL'S OWN CALLS ({len(calls)} bars at rank ≥ 0.85), by the feature:")
    for c in ("f24", "f72", "expo"):
        print(f"   {c}: agrees   {book(calls[calls[c] > 0]['pnl'])}")
        print(f"   {c}: silent   {book(calls[calls[c] == 0]['pnl'])}")
        print(f"   {c}: disagrees{book(calls[calls[c] < 0]['pnl'])}")

    # 3. ranking power, alone and added
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import KFold
    print("\n3. RANKING THE LABEL (AUC), on the same bars:")
    y = D["y"].to_numpy(); w = D["w"].to_numpy()
    for c in ("f24", "f72", "expo"):
        x = D[c].to_numpy()
        if len(np.unique(x)) > 1:
            print(f"   {c} alone:            {roc_auc_score(y, x, sample_weight=w):.3f}")
    print(f"   model score alone:    {roc_auc_score(y, D['p'], sample_weight=w):.3f}")
    # time-ordered folds, so a fold never trains on its own future
    Dt = D.sort_values("t").reset_index(drop=True)
    yt = Dt["y"].to_numpy(); wt = Dt["w"].to_numpy()
    for cols, name in (([["p"]], "score"), ([["p", "f24"]], "score + f24"), ([["p", "f24", "f72", "expo"]], "score + all three")):
        X = Dt[cols[0]].to_numpy(float)
        oof = np.full(len(Dt), np.nan)
        kf = KFold(5, shuffle=False)
        for tr, te in kf.split(X):
            m = LogisticRegression(max_iter=1000).fit(X[tr], yt[tr], sample_weight=wt[tr])
            oof[te] = m.predict_proba(X[te])[:, 1]
        print(f"   logistic, 5 time folds, {name:<18}: {roc_auc_score(yt, oof, sample_weight=wt):.3f}")

    # 4. the overlay's worth: forward 24h from the bar's close
    print("\n4. AS AN OVERLAY -- 24h forward return from the close, both sides pooled:")
    fwd = {}
    for sym in D["symbol"].unique():
        bars = pickle.load(open(FRAMES / f"{sym}_4h_frames.pkl", "rb"))[0]["close"]
        fwd[sym] = 100 * (bars.shift(-6) / bars - 1)
    D["fwd24"] = [fwd[s].get(t, np.nan) * (1 if sd == "long" else -1) for s, t, sd in zip(D["symbol"], D["t"], D["side"])]
    dd = D.dropna(subset=["fwd24"])
    for c in ("f24", "expo"):
        for name, m in (("agrees", dd[c] > 0), ("silent", dd[c] == 0), ("disagrees", dd[c] < 0)):
            x = dd[m]["fwd24"]
            print(f"   {c} {name:<9} n {len(x):5d}  fwd24 {x.mean():+.3f}% ±{x.std()/np.sqrt(max(1, len(x))):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
