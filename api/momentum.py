"""The weekly rotation: among the thirty most-traded perpetuals, long the
five that rank best on 15-day momentum AND a week of net taker buying, short
the five that rank worst, rebalanced every Monday 00:00 UTC.

WHAT IT IS, MEASURED HONESTLY (research/momentum_pit.py, research/QUANT.md)
    The first version ranked the fifteen coins this server serves by their
    30-day return, and measured Sharpe ~1.0 doing so. That list is today's:
    a coin is on it partly BECAUSE it went up, which is exactly what a
    momentum test then "finds". Rerun on every USDT perpetual Binance has
    listed (research/universe.py, delisted coins such as LUNA included),
    with the universe chosen each Monday by what was trading then, the old
    rule was Sharpe 0.39 on Jan 2021 - Jun 2024 and 0.32 on Jul 2024 -
    Aug 2026. Most of the 1.0 was survivorship. An academic study of the
    same design (Grobys et al. 2025) found it positive before mid-2020 and
    nothing since.

    Of the momentum variants tried on the honest universe, one was good on
    the period that chose it AND the one that only confirmed it: the thirty
    coins with the most dollar volume over the past 30 days (60 days of
    history required), five long / five short, on the 15-day return --
    Sharpe 0.45 and 0.61.

    THE SECOND SIGNAL: NET TAKER FLOW. A week of taker buys minus taker
    sells as a share of volume, less what the coin's 7- and 30-day returns
    already explain (Anastasopoulos et al., J. Financial Markets 2026: order
    flow predicts the weekly cross-section; the big-player footprint the
    survey rated best-measured). Alone, on the same universe: Sharpe 0.58
    and 0.60, positive at every weekday phase in BOTH periods. Its weekly
    returns correlate +0.05 with momentum's, so the two ranks averaged --
    the rule served here -- had Sharpe 0.91 and 1.09, worst weeks -15% to
    -18%. Ties in the averaged rank go to the stronger 15-day return. The
    short side needs a futures account.

WHY MONDAY 00:00 UTC
    The natural time for a person. The median over the seven weekday
    phases is the number quoted, not the Monday phase.

PURE: takes daily closes (and dollar volumes), returns the picks. The
service feeds it Binance's daily bars for the most-traded perpetuals and
caches the result.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

LOOKBACK_DAYS = 15
FLOW_DAYS = 7
PICKS = 5
UNIVERSE = 30            # the most-traded perpetuals, by 30-day median dollar volume
VOLUME_DAYS = 30
MIN_HISTORY_DAYS = 60    # a coin listed last month has no 15-day return worth ranking
MIN_COINS = 8            # below this there is no cross-section to rank
# not coins: stablecoins trade as perpetuals too, and would sit in the
# middle of every ranking doing nothing
STABLE = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "DAIUSDT",
          "USDEUSDT", "USD1USDT", "RLUSDUSDT", "BFUSDUSDT", "XUSDUSDT"}

MEASURED = ("Tested on every Binance perpetual as it stood at each date, delisted coins "
            "included, Jan 2021 - Aug 2026, net of fees: momentum alone (15-day) had Sharpe "
            "0.45 / 0.61 (the years that chose it / the two after), a week of net taker "
            "buying alone 0.58 / 0.60, and the two ranks averaged -- this rule -- 0.91 / 1.09. "
            "Its worst weeks lost 15-18%. The first rule, 30-day momentum on today's fifteen "
            "coins, looked like 1.0 but was 0.3-0.4 on an honest universe.")


def week_start(now: pd.Timestamp) -> pd.Timestamp:
    """The Monday 00:00 UTC at or before `now`."""
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    day = now.normalize()
    return day - pd.Timedelta(days=day.dayofweek)


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _asof(s: pd.Series, t: pd.Timestamp) -> Optional[float]:
    """The last close at or before `t` (a daily bar closes 23:59:59.999)."""
    s = s[s.index <= t]
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if np.isfinite(v) and v > 0 else None


def universe(closes: Dict[str, pd.Series], volumes: Optional[Dict[str, pd.Series]],
             at: pd.Timestamp, n: int = UNIVERSE) -> List[str]:
    """The coins ranked at `at`: with `volumes`, the `n` with the most median
    dollar volume over the 30 days before it, among those with 60 days of
    history; without, every coin given (the served-coins fallback)."""
    at = _utc(at)
    keep = []
    for sym, s in closes.items():
        if sym in STABLE or s is None or s.empty:
            continue
        s = s[s.index <= at]
        if volumes is not None and len(s.dropna()) < MIN_HISTORY_DAYS:
            continue
        keep.append(sym)
    if volumes is None:
        return keep
    med = {}
    for sym in keep:
        v = volumes.get(sym)
        if v is None or v.empty:
            continue
        w = v[(v.index <= at) & (v.index > at - pd.Timedelta(days=VOLUME_DAYS))].dropna()
        if len(w):
            med[sym] = float(w.median())
    return [s for s, _ in sorted(med.items(), key=lambda kv: -kv[1])[:n]]


def _ret(s: pd.Series, at: pd.Timestamp, days: int) -> Optional[float]:
    now, then = _asof(s, at), _asof(s, at - pd.Timedelta(days=days))
    return (now / then - 1.0) if now and then else None


def flow_residuals(closes: Dict[str, pd.Series], volumes: Dict[str, pd.Series],
                   takers: Dict[str, pd.Series], at: pd.Timestamp,
                   coins: List[str]) -> Dict[str, float]:
    """Each coin's week of net taker buying -- (taker buys - taker sells) /
    volume over the 7 daily bars to `at` -- less what its 7- and 30-day
    returns explain across the coins (a least-squares fit at `at`). The
    part of the flow the price has not already shown."""
    at = _utc(at)
    nf, r7, r30 = {}, {}, {}
    for sym in coins:
        v, tq, c = volumes.get(sym), takers.get(sym), closes.get(sym)
        if v is None or tq is None or c is None:
            continue
        m = (v.index <= at) & (v.index > at - pd.Timedelta(days=FLOW_DAYS))
        vs, ts = float(v[m].sum()), float(tq.reindex(v.index[m]).sum())
        a, b = _ret(c, at, 7), _ret(c, at, 30)
        if vs > 0 and np.isfinite(ts) and a is not None and b is not None:
            nf[sym], r7[sym], r30[sym] = (2 * ts - vs) / vs, a, b
    syms = list(nf)
    if len(syms) < MIN_COINS:
        return {}
    A = np.c_[np.ones(len(syms)), [r7[x] for x in syms], [r30[x] for x in syms]]
    y = np.array([nf[x] for x in syms])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    return dict(zip(syms, (y - A @ beta).tolist()))


def ranking(closes: Dict[str, pd.Series], at: pd.Timestamp,
            coins: Optional[List[str]] = None,
            volumes: Optional[Dict[str, pd.Series]] = None,
            takers: Optional[Dict[str, pd.Series]] = None) -> List[dict]:
    """Every coin as of `at`, best first. A coin with no close in the week
    before `at`, or none 15 days earlier, is left out rather than ranked on
    a stale price. With taker volumes, the order is the mean of two
    percentile ranks -- the 15-day return and the net taker flow -- and a
    coin missing either is left out; without, the 15-day return alone."""
    at = _utc(at)
    out = []
    for sym in (coins if coins is not None else list(closes)):
        s = closes.get(sym)
        if s is None or s.empty:
            continue
        s = s.sort_index()
        recent = s[(s.index <= at) & (s.index > at - pd.Timedelta(days=7))]
        now = _asof(s, at)
        r = _ret(s, at, LOOKBACK_DAYS)
        if recent.empty or now is None or r is None:
            continue
        # `ret_30d` is the old name, kept so installed apps still read it
        out.append({"symbol": sym, "ret_pct": r * 100.0, "ret_30d": r * 100.0, "close": now})
    flows = (flow_residuals(closes, volumes, takers, at, [r["symbol"] for r in out])
             if volumes is not None and takers is not None else {})
    if flows:
        # each signal ranked over every coin that has it, as measured; then
        # a coin missing either is left out
        mom = pd.Series({r["symbol"]: r["ret_pct"] for r in out}).rank(pct=True)
        flw = pd.Series(flows).rank(pct=True)
        out = [r for r in out if r["symbol"] in flows]
        for r in out:
            r["flow"] = float(flows[r["symbol"]])
            r["score"] = float((mom[r["symbol"]] + flw[r["symbol"]]) / 2.0)
        # averaged percentile ranks tie often; the stronger 15-day return
        # goes first, so the basket's edge is decided, not arbitrary
        out.sort(key=lambda r: (r["score"], r["ret_pct"]), reverse=True)
    else:
        out.sort(key=lambda r: r["ret_pct"], reverse=True)
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return out


def rotation(closes: Dict[str, pd.Series], now: pd.Timestamp,
             prices: Optional[Dict[str, float]] = None,
             volumes: Optional[Dict[str, pd.Series]] = None,
             takers: Optional[Dict[str, pd.Series]] = None) -> dict:
    """This week's picks, how they have done since Monday, and the live
    ranking ("if the week ended now").

    `prices` are live prices for the week-so-far figures; without them the
    latest close is used. `volumes` (daily dollar volume) select the
    universe; without them every coin in `closes` is ranked.
    """
    now = _utc(now)
    start = week_start(now)
    members = universe(closes, volumes, start)
    ranked = ranking(closes, start, members, volumes, takers)
    live = ranking(closes, now, universe(closes, volumes, now), volumes, takers)
    with_flow = bool(ranked) and "score" in ranked[0]
    ok = len(ranked) >= MIN_COINS
    k = min(PICKS, len(ranked) // 2) if ok else 0

    def since_monday(r: dict) -> Optional[float]:
        px = (prices or {}).get(r["symbol"])
        if px is None:
            px = _asof(closes[r["symbol"]], now)
        return (px / r["close"] - 1.0) * 100.0 if px else None

    longs = [dict(r, week_pct=since_monday(r)) for r in ranked[:k]] if ok else []
    shorts = [dict(r, week_pct=since_monday(r)) for r in ranked[-k:][::-1]] if ok else []
    week = None
    lw = [r["week_pct"] for r in longs if r["week_pct"] is not None]
    sw = [r["week_pct"] for r in shorts if r["week_pct"] is not None]
    if lw and sw:
        # half the capital a side, as it was measured
        week = 0.5 * float(np.mean(lw)) - 0.5 * float(np.mean(sw))
    return {
        "week_start": start.isoformat(),
        "next_rebalance": (start + pd.Timedelta(days=7)).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "picks": k,
        "universe": len(ranked),
        "universe_rule": (f"the {UNIVERSE} most-traded Binance perpetuals" if volumes is not None
                          else "the coins this app follows"),
        "signal": ("15-day momentum and a week of net taker buying, ranks averaged" if with_flow
                   else "15-day momentum"),
        "flow_days": FLOW_DAYS if with_flow else None,
        "available": ok,
        "longs": longs,
        "shorts": shorts,
        "week_pct": week,
        "ranking": ranked,
        "live_ranking": live,
        "measured": MEASURED,
        "sharpe": ({"development": 0.91, "holdout": 1.09, "old_rule_honest": 0.36} if with_flow
                   else {"development": 0.45, "holdout": 0.61, "old_rule_honest": 0.36}),
    }
