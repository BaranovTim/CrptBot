"""Can the LIVE 4h system be improved where it has never been tested:
the exit, the entry, and when to trade at all?

THE BIG TRADERS SAY WHERE TO LOOK
    A year of fills for 54 top Hyperliquid accounts (research/results/
    trader_patterns.md): profitable and losing traders have the SAME win
    rate (57-59%) and the SAME average loss (~5.7%). What differs is the
    winners -- +6.2% against +2.9% -- and the hold (28h against 15h). They
    also enter with resting orders far more (36% against 21%), and those
    entries were +1.36% an hour later where their market entries were
    +0.03%. So: exits that let winners run, and entries that wait for the
    price instead of paying it. Every 4h label here exits at the first
    level or after 16 bars, and enters at the close. Neither was ever tested.

WHAT THIS DOES
    Takes the calls the live configuration makes in the walk-forward
    (research/wf4h.py variant `stop_beyond_0.5`: pooled model, stop 0.5 ATR
    past the level, pooled rank >= 0.97) and replays each one bar by bar
    against the real 4h bars under different exit and entry rules. The
    account simulation is the lab's own (three positions, fees, funding).
    Rules are chosen on the first four half-years and confirmed on the
    last two.

    First it replays the CURRENT rule and checks it reproduces the labels
    to the cent. A simulator that does not reproduce the thing it is
    extending is not evidence of anything.

    python research/improve_4h.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research.wf4h as L                                   # noqa: E402
from agent1.pivots import HIGH, find_pivots                 # noqa: E402
from agent5.labels import _atr                              # noqa: E402
from agent5.structure import structural_levels              # noqa: E402

VARIANT = "stop_beyond_0.5"
MAX_HOLD = 90                  # 15 days: the cap for rules with no time exit
PIVOT = 3                      # the swing a trail follows: 3 bars each side, as on daily


# ----------------------------------------------------------------- inputs
_BARS = {}


def coin_data(coin: str):
    """bars, ATR, the level behind the target, and confirmed swings, cached."""
    if coin not in _BARS:
        bars = pickle.load(open(L.FRAMES / f"{coin}_4h_frames.pkl", "rb"))[0]
        atr = _atr(bars, 14).to_numpy(float)
        lv = structural_levels(bars, atr)
        piv = sorted(find_pivots(bars["high"], bars["low"], PIVOT, PIVOT, PIVOT),
                     key=lambda p: (p.confirmed_at, p.index))
        lows = [(p.confirmed_at, p.price) for p in piv if p.kind != HIGH]
        highs = [(p.confirmed_at, p.price) for p in piv if p.kind == HIGH]
        _BARS[coin] = dict(bars=bars, o=bars["open"].to_numpy(float), h=bars["high"].to_numpy(float),
                           l=bars["low"].to_numpy(float), c=bars["close"].to_numpy(float), atr=atr,
                           res2=lv["res2"], sup2=lv["sup2"],
                           lows=(np.array([a for a, _ in lows], int), np.array([b for _, b in lows], float)),
                           highs=(np.array([a for a, _ in highs], int), np.array([b for _, b in highs], float)))
    return _BARS[coin]


def calls(variant: str = VARIANT, cut: float = 0.97) -> pd.DataFrame:
    """Every bar the live rule would call, with its levels as prices."""
    R = L.with_ranks(pickle.load(open(L.SCORES / f"{variant}_scores.pkl", "rb")))
    R = R[R["rank_pool"] >= cut].copy()
    idx = []
    for coin, g in R.groupby("coin"):
        d = coin_data(coin)
        idx.append(pd.Series(d["bars"].index.get_indexer(pd.DatetimeIndex(g["t"])), index=g.index))
    R["i"] = pd.concat(idx)
    return R[R["i"] >= 0].reset_index(drop=True)


# ------------------------------------------------------------------ replay
def replay(d, i: int, long: bool, tp: float, sl: float, rule: str, entry_px=None,
           start=None) -> tuple:
    """(exit bar offset from the SIGNAL bar, % return net of nothing) for one
    trade under `rule`. Stop and target are PRICES fixed at the signal; a
    bar that touches both is a loss (the labeller's convention); the trail
    moves at a bar's close using swings confirmed by then, never earlier.

    rules
      fixed        the label: target or stop, else out at the 16th close
      no_clock     the same, with no 16-bar exit (cap MAX_HOLD)
      breakeven    stop to entry once price is halfway to the target
      half_trail   half off at the target; the rest's stop to entry, then
                   it trails confirmed swings (the daily rule) until stopped
      run_trail    no exit at the target: stop to entry there, then trail
      trail_only   no target at all; trail from the first bar (daily style)
      second_level target at the level BEHIND the first one when there is one
      clock_8      the fixed rule with an 8-bar exit
    """
    c0 = d["c"][i]
    px = c0 if entry_px is None else entry_px
    s = 1.0 if long else -1.0
    T = c0 * (1 + s * tp / 100.0)
    S = c0 * (1 - s * sl / 100.0)
    if rule == "second_level":
        lvl = d["res2"][i] if long else d["sup2"][i]
        if np.isfinite(lvl) and (lvl - T) * s > 0:
            T = lvl
    hold = {"fixed": 16, "clock_8": 8, "second_level": 16}.get(rule, MAX_HOLD)
    j0 = (i + 1) if start is None else start
    n = len(d["c"])
    stop = S
    half_done = False
    booked = 0.0
    trailing = rule == "trail_only"
    piv_t, piv_p = d["lows"] if long else d["highs"]
    use_target = rule not in ("trail_only",)
    for j in range(j0, min(i + hold, n - 1) + 1):
        hi, lo = d["h"][j], d["l"][j]
        hit_stop = (lo <= stop) if long else (hi >= stop)
        hit_tgt = use_target and not (rule == "run_trail" and trailing) and not half_done and (
            (hi >= T) if long else (lo <= T))
        if hit_stop:
            r = s * (stop / px - 1) * 100
            return j - i, (booked + r * (0.5 if half_done else 1.0))
        if hit_tgt:
            r = s * (T / px - 1) * 100
            if rule in ("fixed", "no_clock", "breakeven", "second_level", "clock_8"):
                return j - i, r
            if rule == "half_trail":
                booked += 0.5 * r; half_done = True
                stop = px; trailing = True
            elif rule == "run_trail":
                stop = max(stop, px) if long else min(stop, px); trailing = True
        if rule == "breakeven" and not trailing:
            if (hi - px) * s >= 0.5 * abs(T - px) or (lo - px) * s >= 0.5 * abs(T - px):
                stop = max(stop, px) if long else min(stop, px); trailing = False
        if trailing:
            # swings confirmed at or before this close, between the stop and the close
            k = np.searchsorted(piv_t, j, side="right")
            if k:
                recent = piv_p[max(0, k - 5):k]
                cl = d["c"][j]
                better = recent[(recent > stop) & (recent < cl)] if long else recent[(recent < stop) & (recent > cl)]
                if len(better):
                    stop = better.max() if long else better.min()
    j = min(i + hold, n - 1)
    r = s * (d["c"][j] / px - 1) * 100
    return j - i, (booked + r * (0.5 if half_done else 1.0))


def with_rule(C: pd.DataFrame, rule: str, **kw) -> pd.DataFrame:
    out = C.copy()
    offs, rets = [], []
    for r in C.itertuples(index=False):
        off, ret = replay(coin_data(r.coin), int(r.i), r.side == "long", r.tp, r.sl, rule, **kw)
        offs.append(off); rets.append(ret)
    out["exit_off"] = np.asarray(offs, float)
    out["pnl"] = np.asarray(rets, float)
    out["funding"] = L.funding_paid(out)
    return out


# ----------------------------------------------------------------- entries
def with_limit(C: pd.DataFrame, k_atr: float, wait: int) -> pd.DataFrame:
    """Enter with a resting order k ATR better than the signal's close, good
    for `wait` bars. Filled when a bar trades through it (at the open if it
    gapped through). Same target and stop PRICES -- a better entry is a
    nearer stop and a farther target. Unfilled calls are not traded.

    In the fill bar the stop counts and the target does not: OHLC cannot say
    whether the high came before the dip, so the kinder reading is refused.
    """
    out = C.copy()
    rets, offs, filled = [], [], []
    for r in C.itertuples(index=False):
        d = coin_data(r.coin); i = int(r.i); long = r.side == "long"; s = 1.0 if long else -1.0
        lim = d["c"][i] - s * k_atr * d["atr"][i]
        fill_j, px = None, None
        for j in range(i + 1, min(i + wait, len(d["c"]) - 1) + 1):
            if (d["l"][j] <= lim) if long else (d["h"][j] >= lim):
                fill_j = j
                px = min(lim, d["o"][j]) if long else max(lim, d["o"][j])
                break
        if fill_j is None:
            rets.append(np.nan); offs.append(np.nan); filled.append(False); continue
        S = d["c"][i] * (1 - s * r.sl / 100.0)
        if (d["l"][fill_j] <= S) if long else (d["h"][fill_j] >= S):
            rets.append(s * (S / px - 1) * 100); offs.append(fill_j - i); filled.append(True); continue
        off, ret = replay(d, i, long, r.tp, r.sl, "fixed", entry_px=px, start=fill_j + 1)
        rets.append(ret); offs.append(off); filled.append(True)
    out["pnl"] = rets; out["exit_off"] = offs; out["filled"] = filled
    out = out[out["filled"]].copy()
    out["funding"] = L.funding_paid(out)
    return out


# ------------------------------------------------------------------ report
def book(d: pd.DataFrame) -> str:
    if d.empty:
        return "none"
    net = d["pnl"] - L.COST - d["funding"]
    return f"n {len(d):5d}  net {net.mean():+.3f}%  hit {(net > 0).mean():.0%}"


def account(d: pd.DataFrame) -> dict:
    return L.portfolio(d.reset_index(drop=True), pd.Series(True, index=range(len(d))))


def line(name: str, D: pd.DataFrame) -> None:
    cells = []
    for part, m in (("dev", D["window"] < L.DEV_WINDOWS), ("hold", D["window"] >= L.DEV_WINDOWS)):
        a = account(D[m])
        cells.append(f"{part} {a.get('trades', 0):4d}tr win {a.get('win', 0):.0%} "
                     f"{a.get('net_per_trade', 0):+.2f}%/tr {a.get('return_pct_per_year', 0):+6.1f}%/yr "
                     f"Sh {a.get('sharpe', 0):+.2f} DD {a.get('max_drawdown_pct', 0):4.1f}%")
    print(f"  {name:<30} " + " | ".join(cells), flush=True)


def main() -> int:
    C = calls()
    print(f"{len(C):,} calls from the live rule ({VARIANT}, pooled rank >= 0.97), "
          f"{C['coin'].nunique()} coins, {C['t'].min():%Y-%m} -> {C['t'].max():%Y-%m}\n")

    # 0. the replay must reproduce the labels before it is allowed to say anything
    F = with_rule(C, "fixed")
    ok = np.isclose(F["pnl"].to_numpy(), C["pnl"].to_numpy(), atol=1e-6)
    print(f"replay of the current rule reproduces the label P&L on {ok.mean():.2%} of calls "
          f"(max diff {np.nanmax(np.abs(F['pnl'] - C['pnl'])):.2e}%)\n")

    print("EXITS  (account: 3 positions, fees and funding; dev = 4 half-years that choose, hold = 2 that confirm)")
    for rule in ("fixed", "clock_8", "no_clock", "breakeven", "second_level",
                 "half_trail", "run_trail", "trail_only"):
        line(rule, F if rule == "fixed" else with_rule(C, rule))

    print("\nENTRIES  (resting order k ATR better than the close, current exit)")
    for k, w in ((0.25, 1), (0.25, 2), (0.5, 2), (0.5, 3), (1.0, 3)):
        E = with_limit(C, k, w)
        print(f"  k {k:.2f} ATR, {w} bar{'s' if w > 1 else ' '}  fill rate {len(E) / len(C):.0%}", flush=True)
        line(f"   limit {k:g} ATR / {w} bars", E)
    return 0


if __name__ == "__main__" and len(sys.argv) == 1:
    raise SystemExit(main())


# ------------------------------------------------------------- round two
def outcomes(D: pd.DataFrame) -> str:
    """How the trades ended: full target, scratch (within the fee of flat),
    full stop, other (timeouts and partial trails)."""
    net = D["pnl"]
    tgt = np.isclose(net, D["tp"], atol=0.05) | (net >= D["tp"] - 0.05)
    stop = net <= -D["sl"] + 0.05
    scratch = net.abs() < 0.05
    return (f"target {tgt.mean():.0%}  scratch {scratch.mean():.0%}  "
            f"stop {stop.mean():.0%}  other {(~(tgt | stop | scratch)).mean():.0%}")


def with_limit_rule(C: pd.DataFrame, k_atr: float, wait: int, rule: str) -> pd.DataFrame:
    """The resting entry, then an exit rule other than the fixed one."""
    out = C.copy()
    rets, offs, keep = [], [], []
    for r in C.itertuples(index=False):
        d = coin_data(r.coin); i = int(r.i); long = r.side == "long"; s = 1.0 if long else -1.0
        lim = d["c"][i] - s * k_atr * d["atr"][i]
        fill_j = px = None
        for j in range(i + 1, min(i + wait, len(d["c"]) - 1) + 1):
            if (d["l"][j] <= lim) if long else (d["h"][j] >= lim):
                fill_j, px = j, (min(lim, d["o"][j]) if long else max(lim, d["o"][j]))
                break
        if fill_j is None:
            keep.append(False); rets.append(np.nan); offs.append(np.nan); continue
        S = d["c"][i] * (1 - s * r.sl / 100.0)
        keep.append(True)
        if (d["l"][fill_j] <= S) if long else (d["h"][fill_j] >= S):
            rets.append(s * (S / px - 1) * 100); offs.append(fill_j - i); continue
        off, ret = replay(d, i, long, r.tp, r.sl, rule, entry_px=px, start=fill_j + 1)
        rets.append(ret); offs.append(off)
    out["pnl"] = rets; out["exit_off"] = offs
    out = out[np.asarray(keep)].copy()
    out["funding"] = L.funding_paid(out)
    return out


def round_two() -> None:
    C = calls()
    F = with_rule(C, "fixed")
    B = with_rule(C, "breakeven")
    print("HOW THE TRADES END")
    print(f"  fixed      {outcomes(F)}")
    print(f"  breakeven  {outcomes(B)}")
    E = with_limit(C, 0.5, 3)
    EB = with_limit_rule(C, 0.5, 3, "breakeven")
    print(f"  limit      {outcomes(E)}")
    print(f"  limit+be   {outcomes(EB)}\n")
    print("COMBINED  (account, dev chooses / hold confirms)")
    line("fixed (live today)", F)
    line("breakeven", B)
    line("limit 0.5 ATR / 3 bars", E)
    line("limit 0.5 ATR / 3 bars + breakeven", EB)
    return C, EB


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "two":
    round_two()


# ------------------------------------------------------------ the pick
def with_resting_entry(C: pd.DataFrame, k_atr: float = 0.5, wait: int = 4,
                       through_atr: float = 0.0) -> pd.DataFrame:
    """THE RULE THIS STUDY RECOMMENDS: a resting order `k_atr` better than the
    signal's close, good for `wait` bars; once filled, the STOP keeps the
    distance it had from the close (so it moves with the entry) and the
    TARGET stays on its level. `through_atr` > 0 demands the price trade
    that far past the order before it counts as filled -- the realism check,
    since an order resting exactly at a bar's low does not always fill.

    Chosen over the same-stop version (win 55%, and it collapsed under a
    strict fill) and the same-geometry version (win 73%, half the gain):
    best on the development windows, confirmed on the holdout, and still
    Sharpe 2.2+ when the price must trade 0.05 ATR through the order.
    """
    out = C.copy(); rets, offs, keep = [], [], []
    for r in C.itertuples(index=False):
        d = coin_data(r.coin); i = int(r.i); long = r.side == "long"; s = 1.0 if long else -1.0
        c0 = d["c"][i]; lim = c0 - s * k_atr * d["atr"][i]; need = lim - s * through_atr * d["atr"][i]
        fj = px = None
        for j in range(i + 1, min(i + wait, len(d["c"]) - 1) + 1):
            if (d["l"][j] <= need) if long else (d["h"][j] >= need):
                fj, px = j, (min(lim, d["o"][j]) if long else max(lim, d["o"][j])); break
        if fj is None:
            keep.append(False); rets.append(np.nan); offs.append(np.nan); continue
        keep.append(True)
        S = c0 * (1 - s * r.sl / 100.0) - (c0 - lim)
        sl_pct = abs(c0 - S) / c0 * 100
        if (d["l"][fj] <= S) if long else (d["h"][fj] >= S):
            rets.append(s * (S / px - 1) * 100); offs.append(fj - i); continue
        off, ret = replay(d, i, long, r.tp, sl_pct, "fixed", entry_px=px, start=fj + 1)
        rets.append(ret); offs.append(off)
    out["pnl"] = rets; out["exit_off"] = offs
    out = out[np.asarray(keep)].copy(); out["funding"] = L.funding_paid(out)
    return out


def the_pick() -> None:
    C = calls()
    live = with_rule(C, "fixed")
    print("THE RECOMMENDED ENTRY against the live one (account; dev chooses, hold confirms)")
    line("market at the close (live)", live)
    line("resting 0.5 ATR / 4 bars, stop moves", with_resting_entry(C))
    for pen in (0.01, 0.03, 0.05):
        line(f"   ... must trade {pen} ATR through", with_resting_entry(C, through_atr=pen))
    print("\neach half-year (account return in the half-year)")
    for name, D in (("live", live), ("pick", with_resting_entry(C)),
                    ("pick, 0.03 ATR through", with_resting_entry(C, through_atr=0.03))):
        cells = [account(D[D["window"] == w]).get("return_pct_per_year", 0) * 0.5 for w in range(6)]
        print(f"  {name:<24} " + " ".join(f"{c:+6.1f}%" for c in cells)
              + f"   positive {sum(c > 0 for c in cells)}/6")


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "pick":
    the_pick()


# ------------------------------------------------- as the server trades it
def served(C: pd.DataFrame, offset: float = 0.5, wait: int = 4, through: float = 0.0,
           hold: int = 16) -> pd.DataFrame:
    """The calls traded exactly as the app tells a person to trade them --
    `monitor.resting_orders`, the code the server runs: one order resting
    at the newest call's price, one position at a time per coin and side.
    (research/QUANT.md, "Correction: the resting-entry figures above are
    optimistic": the per-call replay above lets an account pick among
    orders with knowledge of which fill.) Every trade that ended, with the
    call that placed it."""
    from monitor import resting_orders
    rows = []
    for (coin, side), g in C.groupby(["coin", "side"]):
        d = coin_data(coin); n = len(d["c"])
        level = np.zeros(n, int); tp = np.full(n, np.nan); sl = np.full(n, np.nan)
        ii = g["i"].to_numpy(int); level[ii] = 3
        tp[ii] = g["tp"].to_numpy(); sl[ii] = g["sl"].to_numpy()
        meta = g.set_index("i")
        for od in resting_orders(d["o"], d["h"], d["l"], d["c"], d["atr"], tp, sl, level,
                                 side == "long", offset, wait, hold, min_level=3, through_atr=through):
            if od.state in ("target", "stop", "timeout"):
                m = meta.loc[od.placed]
                rows.append(dict(coin=coin, side=side, t=m["t"], pos=m["pos"], window=m["window"],
                                 rank_pool=m["rank_pool"], exit_off=od.closed_at - od.placed,
                                 pnl=od.ret_pct(), state=od.state))
    P = pd.DataFrame(rows)
    P["funding"] = L.funding_paid(P)
    return P


def per_window(D: pd.DataFrame) -> list:
    """The account's return in each half-year (half the annual rate)."""
    return [account(D[D["window"] == w]).get("return_pct_per_year", 0) * 0.5 for w in range(6)]


def compare(variants, through: float = 0.0) -> None:
    """Variants of the MODEL, each traded as the server trades it."""
    for v in variants:
        D = served(calls(v), through=through)
        line(v, D)
        cells = per_window(D)
        print(f"  {'':<30} half-years " + " ".join(f"{c:+6.1f}%" for c in cells)
              + f"   total {sum(cells):+.0f}%  positive {sum(c > 0 for c in cells)}/6", flush=True)


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "served":
    compare(sys.argv[2:])
