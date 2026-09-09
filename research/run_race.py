"""Run every exit rule over every trained coin and rank them honestly."""
from __future__ import annotations

import sys, warnings
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from livefeed import BarStore
from research.exit_rules import catalogue
from research.race import run

COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","ADAUSDT","XRPUSDT","ZECUSDT",
         "HYPEUSDT","DOGEUSDT","BNBUSDT","ENAUSDT","UNIUSDT","NEARUSDT",
         "SUIUSDT","1000PEPEUSDT","ARBUSDT"]
INTERVALS = sys.argv[1:] or ["1h", "4h"]
MAX_HOLD = {"1h": 24, "4h": 12, "1d": 10, "15m": 32}
COST = 0.10

rules = catalogue()
agg = defaultdict(lambda: {"pct": [], "edge": [], "hit": [], "chance": [],
                           "n": 0, "span": [], "r": []})

for iv in INTERVALS:
    hold = MAX_HOLD.get(iv, 24)
    for sym in COINS:
        try:
            bars = BarStore(sym, iv).load()
        except Exception:
            continue
        if bars is None or len(bars) < 500:
            continue
        for name, rule in rules.items():
            try:
                tp, sl = rule(bars)
            except Exception:
                continue
            for side in ("LONG", "SHORT"):
                out = run(bars, tp, sl, max_hold=hold, cost_pct=COST,
                          step=hold, side=side)
                if out is None:
                    continue
                a = agg[name]
                a["pct"].append(out["ev_pct"]); a["r"].append(out["ev_r"])
                a["hit"].append(out["hit_rate"]); a["chance"].append(out["chance_rate"])
                a["edge"].append(out["hit_rate"] - out["chance_rate"])
                a["span"].append(out["median_span_pct"]); a["n"] += out["trades"]

print(f"\n  {len(COINS)} coins x {INTERVALS} x both directions, "
      f"non-overlapping entries, {COST}% round trip")
print(f"  {'rule':<24}{'trades':>8}{'span%':>7}{'hit':>7}{'chance':>8}"
      f"{'edge':>8}{'EV %/trade':>12}{'EV in R':>9}")
print("  " + "-" * 84)
rows = []
for name, a in agg.items():
    if not a["pct"]:
        continue
    rows.append((name, a["n"], float(np.mean(a["span"])),
                 float(np.mean(a["hit"])), float(np.mean(a["chance"])),
                 float(np.mean(a["edge"])), float(np.mean(a["pct"])),
                 float(np.mean(a["r"]))))
for r in sorted(rows, key=lambda x: -x[6]):
    print(f"  {r[0]:<24}{r[1]:>8,}{r[2]:>7.2f}{r[3]:>7.1%}{r[4]:>8.1%}"
          f"{r[5]:>+8.2%}{r[6]:>+12.4f}{r[7]:>+9.3f}")
