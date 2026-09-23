"""Can the 4h calls win 70% of the time, and what does it cost?

The owner asked for a win rate of at least 70%. The served rule (bagged
model, strong calls, a limit 0.5 ATR better than the close, good 6 bars,
target on the next level, stop 0.5 ATR beyond the last one) wins ~61%.
Win rate is cheap to buy -- a nearer target is hit more often -- so every
lever here is reported with what it does to the money: per trade, and the
three-position account's Sharpe. Chosen on the four development
half-years, confirmed on the two holdout ones, every call traded exactly as
the server trades it (improve_4h.served, monitor.resting_orders).

Levers:
    target    the target at a fraction of the way from the close to the level
    offset    the limit's distance from the close, in ATR (0 = at the close)
    stop      extra room for the stop, in ATR, beyond the 0.5 already there
    cut       the pooled rank a call needs (0.97 strong, 0.98, 0.99)

    python research/win_rate.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import research.improve_4h as I          # noqa: E402
import research.wf4h as L                # noqa: E402

VARIANT = "live_bag5"
WAIT = 6


def shaped(C: pd.DataFrame, target: float = 1.0, stop: float = 0.0) -> pd.DataFrame:
    """The calls with the target pulled to `target` of its distance from the
    close, and the stop given `stop` more ATRs of room."""
    C = C.copy()
    C["tp"] = C["tp"] * target
    if stop:
        extra = []
        for r in C.itertuples(index=False):
            d = I.coin_data(r.coin)
            extra.append(stop * d["atr"][int(r.i)] / d["c"][int(r.i)] * 100.0)
        C["sl"] = C["sl"] + np.asarray(extra)
    return C


def measure(D: pd.DataFrame) -> dict:
    net = D["pnl"] - L.COST - D["funding"]
    ho = (D["window"] >= L.DEV_WINDOWS).to_numpy()
    a_dev, a_ho = I.account(D[~ho]), I.account(D[ho])
    return {"trades": len(D),
            "win_dev": float((net[~ho] > 0).mean()), "win_hold": float((net[ho] > 0).mean()),
            "net_dev": float(net[~ho].mean()), "net_hold": float(net[ho].mean()),
            "acct_win_dev": a_dev.get("win", 0), "acct_win_hold": a_ho.get("win", 0),
            "sh_dev": a_dev.get("sharpe", 0), "sh_hold": a_ho.get("sharpe", 0),
            "total": float(sum(I.per_window(D)))}


def row(name: str, D: pd.DataFrame) -> dict:
    m = measure(D)
    print(f"  {name:<34} win {m['win_dev']:.0%} / {m['win_hold']:.0%}  "
          f"per trade {m['net_dev']:+.2f}% / {m['net_hold']:+.2f}%  "
          f"| 3 at once: win {m['acct_win_dev']:.0%} / {m['acct_win_hold']:.0%}, "
          f"Sharpe {m['sh_dev']:.2f} / {m['sh_hold']:.2f}, 3 yr {m['total']:+.0f}%  (n {m['trades']})",
          flush=True)
    return dict(m, name=name)


def main() -> int:
    print("every figure: development years / latest year (holdout)")
    base = {c: I.calls(VARIANT, cut=c) for c in (0.97, 0.98, 0.99)}
    out = [row("served now", I.served(base[0.97], offset=0.5, wait=WAIT))]
    print("ONE LEVER AT A TIME")
    for f in (0.8, 0.7, 0.6, 0.5):
        out.append(row(f"target at {f:.0%} of the way", I.served(shaped(base[0.97], target=f), wait=WAIT)))
    for k in (0.25, 0.0):
        out.append(row(f"limit {k} ATR from the close", I.served(base[0.97], offset=k, wait=WAIT)))
    for x in (0.5, 1.0):
        out.append(row(f"stop +{x} ATR more room", I.served(shaped(base[0.97], stop=x), wait=WAIT)))
    for c in (0.98, 0.99):
        out.append(row(f"rank cut {c}", I.served(base[c], wait=WAIT)))
    print("COMBINATIONS")
    for f in (0.8, 0.7, 0.6):
        for x in (0.0, 0.5):
            for k in (0.5, 0.25):
                out.append(row(f"target {f:.0%}, stop +{x}, limit {k}",
                               I.served(shaped(base[0.97], target=f, stop=x), offset=k, wait=WAIT)))
    R = pd.DataFrame(out)
    R.to_csv(Path(__file__).resolve().parent / "results" / "win_rate.csv", index=False)
    return 0


if __name__ == "__main__" and len(sys.argv) == 1:
    raise SystemExit(main())


# ------------------------------------------------------------ scale-out
def served_scaled(C: pd.DataFrame, part: float = 0.5, at: float = 0.5, offset: float = 0.5,
                  wait: int = WAIT, hold: int = 16) -> pd.DataFrame:
    """The served policy, but once filled: take `part` of the position off
    when price has gone `at` of the way from the fill to the target, and
    move the stop on the rest to the fill (breakeven). The rest runs to the
    target, the breakeven stop, or the clock. One return per trade, the
    parts weighted; a trade is a WIN when that return beats the fee.

    Same policy as monitor.resting_orders otherwise: one order resting at
    the newest call's price, one position at a time per coin and side."""
    from monitor import RestingOrder

    class Scaled(RestingOrder):
        booked = 0.0            # % booked by the part taken off, already weighted
        taken = False

        def step(self, j, o, h, lo, c, closed=True):
            if self.state != "filled" or self.taken:
                was = self.state
                super().step(j, o, h, lo, c, closed)
                if was == "open" and self.state == "filled":
                    self._mid = self.fill + at * (self.target - self.fill)
                if self.taken and self.state == "stop" and self.exit == self.stop:
                    pass
                return
            s = 1.0 if self.long else -1.0
            # the bar can hit the stop, the midpoint, or both (both = stop)
            hit_stop = (lo <= self.stop) if self.long else (h >= self.stop)
            hit_mid = (h >= self._mid) if self.long else (lo <= self._mid)
            if hit_stop:
                super().step(j, o, h, lo, c, closed)
                return
            if hit_mid:
                self.booked = part * s * (self._mid / self.fill - 1.0) * 100.0
                self.taken = True
                self.stop = self.fill                    # breakeven on the rest
                if (h >= self.target) if self.long else (lo <= self.target):
                    self.state, self.exit, self.closed_at = "target", self.target, j
                return
            super().step(j, o, h, lo, c, closed)

        def ret_pct(self):
            s = 1.0 if self.long else -1.0
            rest = s * (self.exit / self.fill - 1.0) * 100.0
            return self.booked + (1.0 - part) * rest if self.taken else rest

    rows = []
    for (coin, side), g in C.groupby(["coin", "side"]):
        d = I.coin_data(coin); n = len(d["c"]); long = side == "long"; s = 1.0 if long else -1.0
        tp = np.full(n, np.nan); sl = np.full(n, np.nan); lev = np.zeros(n, int)
        ii = g["i"].to_numpy(int); lev[ii] = 3; tp[ii] = g["tp"].to_numpy(); sl[ii] = g["sl"].to_numpy()
        meta = g.set_index("i"); cur = None; orders = []
        for j in range(n):
            if cur is not None and cur.active and j > cur.placed:
                cur.step(j, d["o"][j], d["h"][j], d["l"][j], d["c"][j])
            if lev[j] >= 3 and np.isfinite(tp[j]) and np.isfinite(sl[j]) and d["atr"][j] > 0:
                if cur is not None and cur.state == "filled":
                    continue
                if cur is not None and cur.state == "open":
                    cur.state = "replaced"; cur.closed_at = j
                shift = s * offset * d["atr"][j]; c0 = d["c"][j]
                cur = Scaled(placed=j, long=long, limit=c0 - shift,
                             stop=c0 * (1 - s * sl[j] / 100.0) - shift,
                             target=c0 * (1 + s * tp[j] / 100.0),
                             valid_to=j + wait, hold_to=j + hold, level=3)
                orders.append(cur)
        for od in orders:
            if od.state in ("target", "stop", "timeout"):
                m = meta.loc[od.placed]
                rows.append(dict(coin=coin, side=side, t=m["t"], pos=m["pos"], window=m["window"],
                                 rank_pool=m["rank_pool"], exit_off=od.closed_at - od.placed,
                                 pnl=od.ret_pct(), state=od.state))
    P = pd.DataFrame(rows)
    P["funding"] = L.funding_paid(P)
    return P


def scale_out() -> None:
    print("every figure: development years / latest year (holdout)")
    C97, C98, C99 = (I.calls(VARIANT, cut=c) for c in (0.97, 0.98, 0.99))
    # the replay with part=0 must be the served policy exactly
    same = measure(served_scaled(C97, part=0.0))["total"]
    print(f"  check: scale-out replay with nothing taken off = served total {same:+.0f}% (served: +269%)")
    for part, at in ((0.5, 0.5), (0.5, 0.33), (0.5, 0.6), (0.33, 0.5), (0.67, 0.5)):
        row(f"take {part:.0%} off at {at:.0%} of the way", served_scaled(C97, part=part, at=at))
    print("WITH A STRICTER CUT")
    for cut, C in ((0.98, C98), (0.99, C99)):
        row(f"cut {cut}, half off at 50%", served_scaled(C, part=0.5, at=0.5))
        for f in (0.7, 0.6):
            row(f"cut {cut}, target at {f:.0%}", I.served(shaped(C, target=f), wait=WAIT))
            row(f"cut {cut}, target {f:.0%}, stop +0.5", I.served(shaped(C, target=f, stop=0.5), wait=WAIT))


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "scale":
    scale_out()
