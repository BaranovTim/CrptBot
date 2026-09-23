"""Order handling on the 4h calls, where the gains have been.

Round four (research/QUANT.md) found every new information source null --
order flow, sweeps, liquidation maps, higher-timeframe levels, the Coinbase
premium -- while the rules for HOW a call is traded (the stop beyond the
level, the resting entry) paid twice. So this looks at the order's life
once more, with rules the served policy (monitor.resting_orders) lacks:

    cancel_on_target   an unfilled order is cancelled once the price trades
                       at the target: the move it was waiting to join has
                       happened without it, and a fill after that is a buy
                       of the retracement from a spent move
    cooldown N         after a stop-out, the same coin and side ignores
                       calls for N bars (the level failed; the structure the
                       call was read from is broken)
    exit_on_opposite   a filled trade is closed at the close where the
                       OTHER side's model calls (rank >= 0.97)
    cancel_on_drop Q   an unfilled order is cancelled at a close where its
                       side's pooled rank falls below Q

The replay is resting_orders' own, re-implemented with hooks and checked
to reproduce it exactly with every hook off. Chosen on the four
development half-years, confirmed on the two holdout ones; the bagged
model's calls, orders good for 6 bars.

    python research/decision_4h.py [variant]
"""
from __future__ import annotations

import pickle
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import research.improve_4h as I                            # noqa: E402
import research.wf4h as L                                  # noqa: E402
from monitor import RestingOrder, resting_orders           # noqa: E402

CUT = 0.97


def ranks(variant: str) -> pd.DataFrame:
    R = L.with_ranks(pickle.load(open(L.SCORES / f"{variant}_scores.pkl", "rb")))
    return R[R["in_window"]] if "in_window" in R else R


def replay(d, lev, opp, rk, tp, sl, long, offset, valid, hold, cancel_on_target=False,
           cooldown=0, exit_on_opposite=False, cancel_on_drop=None):
    """resting_orders(policy="replace") with hooks. `lev` = this side's call
    level per bar, `opp` = the other side's, `rk` = this side's rank."""
    o, h, lo, c, atr = d["o"], d["h"], d["l"], d["c"], d["atr"]
    n = len(c); s = 1.0 if long else -1.0
    out = []; cur = None; quiet_until = -1
    for j in range(n):
        if cur is not None and cur.active and j > cur.placed:
            was = cur.state
            if was == "open" and cancel_on_target and (
                    (h[j] >= cur.target) if long else (lo[j] <= cur.target)):
                # the fill test runs first: a bar that reached both the limit
                # and the target is taken as filled, as the served rule does
                need = cur.limit
                if not ((lo[j] <= need) if long else (h[j] >= need)):
                    cur.state = "expired"; cur.closed_at = j
            if cur.state == "open" and cancel_on_drop is not None and np.isfinite(rk[j]) \
                    and rk[j] < cancel_on_drop:
                cur.step(j, o[j], h[j], lo[j], c[j])
                if cur.state == "open":
                    cur.state = "expired"; cur.closed_at = j
            elif cur.active:
                cur.step(j, o[j], h[j], lo[j], c[j])
            if cur.state == "filled" and exit_on_opposite and opp[j] >= 3 and j > cur.filled_at:
                cur.state, cur.exit, cur.closed_at = "timeout", c[j], j
            if cur.state == "stop" and cooldown:
                quiet_until = j + cooldown
        if lev[j] >= 3 and np.isfinite(tp[j]) and np.isfinite(sl[j]) and np.isfinite(atr[j]) and atr[j] > 0:
            if j <= quiet_until:
                continue
            if cur is not None and cur.state == "filled":
                continue
            if cur is not None and cur.state == "open":
                cur.state = "replaced"; cur.closed_at = j
            shift = s * offset * atr[j]
            cur = RestingOrder(placed=j, long=long, limit=c[j] - shift,
                               stop=c[j] * (1 - s * sl[j] / 100.0) - shift,
                               target=c[j] * (1 + s * tp[j] / 100.0),
                               valid_to=j + valid, hold_to=j + hold, level=3)
            out.append(cur)
    return out


def run(variant: str = "live_bag5", valid: int = 6, **hooks) -> pd.DataFrame:
    R = ranks(variant)
    rows = []
    for coin, g in R.groupby("coin"):
        d = I.coin_data(coin); n = len(d["c"])
        ix = d["bars"].index.get_indexer(pd.DatetimeIndex(g["t"]))
        g = g.assign(i=ix)[ix >= 0]
        per = {}
        for side, gs in g.groupby("side"):
            lev = np.zeros(n, int); tp = np.full(n, np.nan); sl = np.full(n, np.nan); rk = np.full(n, np.nan)
            ii = gs["i"].to_numpy(int)
            rk[ii] = gs["rank_pool"].to_numpy(float)
            call = gs["rank_pool"].to_numpy(float) >= CUT
            lev[ii[call]] = 3; tp[ii] = gs["tp"].to_numpy(); sl[ii] = gs["sl"].to_numpy()
            per[side] = (lev, tp, sl, rk, gs.set_index("i"))
        for side, (lev, tp, sl, rk, meta) in per.items():
            opp = per.get("short" if side == "long" else "long", (np.zeros(n, int),))[0]
            for od in replay(d, lev, opp, rk, tp, sl, side == "long", 0.5, valid, 16, **hooks):
                if od.state in ("target", "stop", "timeout"):
                    m = meta.loc[od.placed]
                    rows.append(dict(coin=coin, side=side, t=m["t"], pos=m["pos"], window=m["window"],
                                     rank_pool=m["rank_pool"], exit_off=od.closed_at - od.placed,
                                     pnl=od.ret_pct(), state=od.state))
    P = pd.DataFrame(rows)
    P["funding"] = L.funding_paid(P)
    return P


def check(variant: str = "live_bag5") -> None:
    """With every hook off this must BE the served policy."""
    a = run(variant).sort_values(["coin", "side", "t"]).reset_index(drop=True)
    b = I.served(I.calls(variant), wait=6).sort_values(["coin", "side", "t"]).reset_index(drop=True)
    same = len(a) == len(b) and np.allclose(a["pnl"].to_numpy(), b["pnl"].to_numpy())
    print(f"replay reproduces the served policy: {same} ({len(a)} vs {len(b)} trades)")


def main() -> int:
    v = sys.argv[1] if len(sys.argv) > 1 else "live_bag5"
    check(v)
    rows = [("served (bag5, valid 6)", {}),
            ("cancel on target", dict(cancel_on_target=True)),
            ("cooldown 6 after a stop", dict(cooldown=6)),
            ("cooldown 12 after a stop", dict(cooldown=12)),
            ("exit on the opposite call", dict(exit_on_opposite=True)),
            ("cancel if rank < 0.50", dict(cancel_on_drop=0.50)),
            ("cancel if rank < 0.80", dict(cancel_on_drop=0.80))]
    for name, hooks in rows:
        D = run(v, **hooks); pw = I.per_window(D)
        I.line(name, D)
        print(f"  {'':<30} half-years " + " ".join(f"{x:+6.1f}%" for x in pw)
              + f"   total {sum(pw):+.0f}%", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
