"""Does following the followed pay? Score the smart-money feed after the fact.

Every event the tracker logged carries `price_at`: our own 1m close at the
minute we saw the trader act. That is a follower's fill, not the trader's,
and it is the only honest basis for the question. For each opened / flipped
event this reads what price did 1h, 4h and 24h later from the 1m store and
reports the mean return IN THE TRADER'S DIRECTION, with an error bar, per
horizon -- and the same for a shuffled-direction control, so drift over the
sample does not pass for skill.

Needs a copy of the server's `data_cache/smartmoney.v1.json` and 1m bars
covering the events (rsync both).

    python research/smart_forward.py [data_cache/smartmoney.v1.json]
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from livefeed.store import BarStore                          # noqa: E402

HORIZONS = {"1h": 60, "4h": 240, "24h": 1440}
COST = 0.10


def forward(bars: pd.DataFrame, at: pd.Timestamp, minutes: int):
    after = bars[bars.index > at + timedelta(minutes=minutes)]
    return float(after["close"].iloc[0]) if len(after) else None


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data_cache/smartmoney.v1.json")
    raw = json.loads(path.read_text())
    events = [e for e in raw.get("events", []) if e.get("kind") in ("opened", "flipped")
              and e.get("symbol") and e.get("price_at")]
    print(f"{len(events)} scorable events (opened/flipped with a price)")
    if not events:
        return 0
    stores = {}
    rows = []
    for e in events:
        sym = e["symbol"]
        if sym not in stores:
            stores[sym] = BarStore(sym, "1m").load(derived=False)
        bars = stores[sym]
        at = pd.Timestamp(e["at"])
        sign = 1.0 if e["side"] == "LONG" else -1.0
        row = {"symbol": sym, "side": e["side"], "at": at, "who": e["address"][:10]}
        for name, mins in HORIZONS.items():
            px = forward(bars, at, mins)
            row[name] = None if px is None else sign * 100.0 * (px / e["price_at"] - 1.0)
        rows.append(row)
    df = pd.DataFrame(rows)
    rng = np.random.default_rng(0)
    print(f"{'horizon':<8} {'n':>4} {'mean %':>8} {'se':>6} {'net of fees':>12} | {'shuffled':>9}")
    for name in HORIZONS:
        x = df[name].dropna().to_numpy(float)
        if len(x) < 2:
            print(f"{name:<8} {len(x):>4}   (too few)")
            continue
        flips = rng.choice([-1.0, 1.0], size=(200, len(x)))
        ctrl = float(np.mean(np.abs(x) * flips))
        se = float(np.std(x, ddof=1) / np.sqrt(len(x)))
        print(f"{name:<8} {len(x):>4} {x.mean():>+8.3f} {se:>6.3f} {x.mean() - COST:>+12.3f} | {ctrl:>+9.3f}")
    print("\nper trader (24h):")
    for who, g in df.groupby("who"):
        x = g["24h"].dropna()
        if len(x):
            print(f"  {who}  n {len(x):>3}  mean {x.mean():+.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
