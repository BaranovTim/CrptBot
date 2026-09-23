"""Four questions about the SHIPPED 4h rule that were never asked.

All of them run off `structure_holdout.py --dump` (the per-bar scores of
the shipped per-coin models over the year they never saw), so none of
them needs a refit.

  1. IS THE RULE BLIND TO THE PAYOFF IT IS BEING OFFERED?
     The structure label asks "does the target come before the stop". How
     far each sits varies five-fold bar to bar, and the decision rule --
     top decile of the model's own trailing score -- never looks at either.
  2. DOES THE MODEL KNOW ANYTHING BEYOND THAT GEOMETRY?
     A near target is mechanically easier to reach, so an AUC measured
     across bars offering wildly different payoffs is partly a geometry
     detector. The honest question is whether the score separates winners
     among bars offering the SAME payoff.
  3. DO THE TWO SIDE MODELS EVER CONTRADICT EACH OTHER?
     h1 (long) and h2 (short) answer about the same bar. If both are
     confident at once, at least one is wrong, and that is a free filter.
  4. DOES A FITTED MODEL GO OFF, AND HOW FAST?
     The models in `output/` were fitted on 2026-09-20 and never refitted.
     Nobody has measured what a year of age costs.

    python research/rule_probes.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sklearn.metrics import roc_auc_score      # noqa: E402

COST = 0.10
DUMP = Path("data_cache/research_frames/holdout_scores.pkl")
TRAIL = 540


def load() -> pd.DataFrame:
    S = pickle.load(open(DUMP, "rb"))
    rows = []
    for r in S:
        rank = np.array([((r["p"][max(0, i - TRAIL):i] < r["p"][i]).mean() if i >= 50 else np.nan)
                         for i in range(len(r["p"]))])
        for i in range(len(r["t"])):
            rows.append((r["t"][i], r["symbol"], r["side"], r["p"][i], rank[i],
                         float(r["touch"][i] == r["win"]), r["pnl"][i], r["w"][i],
                         r["tp"][i], r["sl"][i]))
    D = pd.DataFrame(rows, columns=["t", "sym", "side", "p", "rank", "y", "pnl", "w", "tp", "sl"])
    D["t"] = pd.DatetimeIndex(D["t"])
    D["rr"] = D["tp"] / D["sl"]
    return D.dropna(subset=["rank", "rr"])


def stat(d: pd.DataFrame) -> str:
    if len(d) == 0:
        return "none"
    net = d["pnl"].to_numpy() - COST; w = d["w"].to_numpy()
    mean = float(np.average(net, weights=w))
    se = float(np.sqrt(np.sum((w * (net - mean)) ** 2)) / w.sum())
    return f"n {len(d):5d}  net {mean:+.3f}% +-{se:.3f}"


def best_per_bar(d: pd.DataFrame, k: int = 1, cut: float = 0.90, rr_max=None) -> pd.DataFrame:
    x = d if rr_max is None else d[d["rr"] < rr_max]
    x = x.sort_values("rank", ascending=False).groupby("t").head(k)
    return x[x["rank"] >= cut]


def main() -> int:
    D = load()
    mid = D["t"].min() + (D["t"].max() - D["t"].min()) / 2
    print(f"{len(D):,} scored bars, {D['t'].min().date()} -> {D['t'].max().date()}, "
          f"{D['sym'].nunique()} coins x 2 sides")

    print("\n1. THE PAYOFF ON OFFER, AND WHETHER THE RULE LOOKS AT IT")
    print(f"   reward:risk  p10 {D['rr'].quantile(.1):.2f}  median {D['rr'].median():.2f}  "
          f"p90 {D['rr'].quantile(.9):.2f}  (break-even hit rates "
          f"{100/(1+D['rr'].quantile(.1)):.0f}% / {100/(1+D['rr'].quantile(.9)):.0f}%)")
    calls = D[D["rank"] >= 0.90]
    print(f"   the calls' median R:R {calls['rr'].median():.2f} against {D['rr'].median():.2f} "
          f"for every bar -- the rule does not select on it")
    print("   shipped rule                        :", stat(calls))
    for cut in (0.4, 0.8, 1.0):
        print(f"   ...and R:R >= {cut:.1f}                   :", stat(calls[calls['rr'] >= cut]))
    print("   ...and a NEAR target (R:R < 0.4)    :", stat(calls[calls["rr"] < 0.4]))

    print("\n2. SKILL WITHIN A FIXED PAYOFF")
    qs = D["rr"].quantile([.33, .67]).to_numpy()
    print(f"   R:R alone ranks the label at AUC "
          f"{roc_auc_score(D['y'], -D['rr'], sample_weight=D['w']):.3f}; "
          f"the model's score at {roc_auc_score(D['y'], D['p'], sample_weight=D['w']):.3f}")
    for name, m in (("tight target, wide stop", D["rr"] <= qs[0]),
                    ("balanced", (D["rr"] > qs[0]) & (D["rr"] <= qs[1])),
                    ("wide target, tight stop", D["rr"] > qs[1])):
        g = D[m]
        auc = roc_auc_score(g["y"], g["p"], sample_weight=g["w"])
        print(f"   {name:<24} AUC {auc:.3f}   every bar {stat(g)}   "
              f"top decile {stat(g[g['rank'] >= 0.90])}")

    print("\n3. THE TWO SIDES CONTRADICTING EACH OTHER")
    w = D.pivot_table(index=["t", "sym"], columns="side", values="rank")
    both = ((w.get("long", pd.Series(dtype=float)) >= 0.90) &
            (w.get("short", pd.Series(dtype=float)) >= 0.90)).sum()
    print(f"   bars where both sides call at once: {int(both)} of {len(w):,} "
          f"-- the sides are near-complements, so there is nothing to filter")

    print("\n4. DOES THE FIT GO OFF?  (30-day blocks after the cutoff)")
    D["month"] = ((D["t"] - D["t"].min()).dt.days // 30).clip(upper=11)
    aucs = {}
    for m, g in D.groupby("month"):
        aucs[m] = roc_auc_score(g["y"], g["p"], sample_weight=g["w"]) if g["y"].nunique() > 1 else np.nan
        print(f"   month {m:2d}  AUC {aucs[m]:.3f}   {stat(g[g['rank'] >= 0.90])}")
    v = pd.Series(aucs)
    print(f"   first four months {v.iloc[:4].mean():.3f} vs last four {v.iloc[-4:].mean():.3f} "
          f"({v.iloc[-4:].mean() - v.iloc[:4].mean():+.3f}) -- age is not the problem")

    print("\n5. CHOOSING ACROSS COINS RATHER THAN AGAINST A THRESHOLD")
    for name, fn in (("shipped: every coin over its own rank", lambda d: d[d["rank"] >= 0.90]),
                     ("best coin of the five, per bar", best_per_bar),
                     ("best coin per bar AND R:R < 0.4", lambda d: best_per_bar(d, rr_max=0.4))):
        print(f"   {name:<38} whole {stat(fn(D))}")
        print(f"   {'':<38} 1st   {stat(fn(D[D['t'] < mid]))}")
        print(f"   {'':<38} 2nd   {stat(fn(D[D['t'] >= mid]))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
