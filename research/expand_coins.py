"""Ten more coins for the pooled models: measured in the walk-forward lab,
fitted into a STAGING directory, never installed.

THE QUESTION
    The owner asked to "train 10 more coins". The 4h model is ONE pooled
    fit across coins and its calls come from a rank across coins, so ten
    more coins can pay in two separate ways, and the lab keeps them apart:

      (a) today: the 15-coin pool, scored on the 15            (baseline)
      (b) a 25-coin pool, scored on the SAME 15 coins, ranked among them
          -- does training on more coins make the existing calls better?
      (c) a 25-coin pool, scored on all 25, ranked across all 25
          -- does the extra CHOICE make the served account better?
      (d) the 15-coin pool, scored on all 25, ranked across all 25
          -- the same choice without retraining (a control for (c))

    An earlier learning curve (research/QUANT.md) found that past ten
    coins "more coins buy choice, not accuracy" (AUC 0.602 -> 0.604 from
    10 -> 15). (b) re-asks the first half; (c) and (d) the second.

WHAT IS REUSED, UNCHANGED
    The lab (research/wf4h.py: six half-year refits, first four choose,
    last two confirm; stop 0.5 ATR beyond the level -- the "sb0.5" data;
    five-seed bag), the served-policy replay (research/improve_4h.served,
    i.e. monitor.resting_orders, 0.5 ATR / 6 bars), the production
    trainer's pooling and config (train_pooled_4h.pool_4h and its
    constants) and the daily one's (research/pooled_daily). This file only
    points them at other directories.

RESULTS (2026-09-23; research/results/wf4h_25coins/report*.json, detail.json)
    Two disjoint five-seed bags (A = 7..19, B = 23..41); served = 3-at-once
    account Sharpe, development / holdout, and the six-half-year total.

                          no scale-out (wait 6)        shipped scale-out
      (a) 15 on the 15    A 2.65/1.94 +269  B 2.32/1.84 +237   A 2.30/1.37 +184  B 1.75/1.31 +147
      (b) 25 on the 15    A 1.75/1.90 +195  B 2.00/2.27 +230   A 1.68/1.77 +151  B 1.77/1.67 +156
      (c) 25 on all 25    A 1.72/0.83 +177  B 1.88/0.48 +175   A 1.41/1.28 +137  B 1.58/0.69 +136
      (d) 15 on all 25    A 1.60/1.37 +200  B 1.65/1.38 +206   A 1.64/1.48 +167  B 1.29/1.53 +148

    (a) bag A reproduces the shipped `live_bag5` scores bit for bit.
    Training on 25 lifts AUC on the fifteen a little on development
    (0.6073 -> 0.6119) and not on holdout (0.6166 -> 0.6172); the account is
    worse on development in all four pairs. Serving 25 is worse on every
    period in every pair; the new ten's served trades earn about half of
    what the fifteen's do in the same runs, and are ~40% of the calls. At an
    equal call count (cut 0.982) holdout is still 1.13 / 0.85 vs 1.94 / 1.84.

WHERE THINGS GO (nothing here writes to output/, the score book, or
data_cache/live/ -- the ten new coins' bars live in their own store)
    data_cache/staging_25coins/
      bars/<SYM>/<4h|1d>/         the ten coins' klines (a BarStore)
      research_frames/            the lab's frames: links to the fifteen,
                                  plus the ten cut at the fifteen's end
        wf4h/                     the 25-coin lab cache and scores
      train_frames_4h/            production 4h frames: links + the ten
      frames_1d/                  1d frames for all 25, fresh
      models/                     the candidate models and verdicts
    research/results/wf4h_25coins/  summaries

    python3 research/expand_coins.py fetch          # bars, funding, frames
    python3 research/expand_coins.py lab-build      # 25-coin sb0.5 datasets
    python3 research/expand_coins.py lab-run x25_bag5 x15_bag5
    python3 research/expand_coins.py lab-run x25_bag5b x15_bag5b   # a second bag
    python3 research/expand_coins.py lab-report     # the (a)-(d) table
    python3 research/expand_coins.py lab-report scaled   # ... with the shipped scale-out
    python3 research/expand_coins.py lab-detail     # equal call count; dev-chosen subset
    python3 research/expand_coins.py stage-4h       # candidate 4h models
    python3 research/expand_coins.py stage-4h 15    # the fifteen refit, reference only
    python3 research/expand_coins.py stage-1d       # candidate 1d models
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

STAGING = Path("data_cache/staging_25coins")
BARS = STAGING / "bars"
LAB_FRAMES = STAGING / "research_frames"
TRAIN_4H = STAGING / "train_frames_4h"
FRAMES_1D = STAGING / "frames_1d"
MODELS = STAGING / "models"
LAB_OUT = Path("research/results/wf4h_25coins")

# the fifteen served today (train_pooled_4h.COINS), repeated so this file
# does not depend on that list staying what it was when these were measured
OLD = ["1000PEPEUSDT", "ADAUSDT", "ARBUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT",
       "ENAUSDT", "ETHUSDT", "HYPEUSDT", "NEARUSDT", "SOLUSDT", "SUIUSDT",
       "UNIUSDT", "XRPUSDT", "ZECUSDT"]
# the ten, with the day Binance listed each perpetual (fapi exchangeInfo
# `onboardDate`, read 2026-09-23). History is asked for from the later of
# the timeframe's usual start and the listing month -- the same bars the
# fifteen got, without a thousand 404s for months that never existed.
NEW = {"AVAXUSDT": "2020-09-23", "LINKUSDT": "2020-01-17", "LTCUSDT": "2020-01-09",
       "BCHUSDT": "2019-12-19", "AAVEUSDT": "2020-10-16", "FILUSDT": "2020-10-16",
       "DOTUSDT": "2020-08-22", "WLDUSDT": "2023-07-24", "TAOUSDT": "2024-04-11",
       "ONDOUSDT": "2024-01-20"}
ALL = sorted(OLD + list(NEW))
# The fifteen's research frames end 2026-09-06 23:59 (BTC 09-12, ETH/SOL/ADA
# 08-28). The ten are cut at the modal end so the last half-year never has a
# stretch where only new coins are scored -- which would hand them every
# call in it.
LAB_END = pd.Timestamp("2026-09-06 23:59:59.999", tz="UTC")
FUNDING_START = "2023-01-01"        # as the fifteen's funding files


def _start(sym: str, interval: str) -> str:
    from core.timeframes import history_start
    listed = pd.Timestamp(NEW[sym]).replace(day=1)
    return str(max(pd.Timestamp(history_start(interval)), listed).date())


# --------------------------------------------------------------------- fetch
def fetch_bars(sym: str, interval: str) -> pd.DataFrame:
    from livefeed.klines import seed_store
    from livefeed.store import BarStore

    store = BarStore(sym, interval, directory=BARS)
    marker = store.dir / ".seeded_from"
    start = _start(sym, interval)
    if not (marker.exists() and marker.read_text().strip() <= start):
        seed_store(sym, interval, start=start, store=store)
        marker.write_text(start)
    return store.load()


def _frames(bars: pd.DataFrame, sym: str, interval: str):
    from core import htf_for
    from train import compute_frames

    frames, warm, why = compute_frames(bars, htf_for(interval), sym, interval, no_tape=True)
    if frames is None:
        raise RuntimeError(f"{sym} {interval}: {why}")
    return bars, frames, warm


def fetch() -> None:
    """Bars (4h and 1d), funding, and feature frames for the ten; 1d frames
    for the fifteen too (read from the live store, which is not written)."""
    from marketdata.funding import load_funding

    for d in (BARS, LAB_FRAMES, TRAIN_4H, FRAMES_1D, MODELS):
        d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for sym in NEW:
        b4 = fetch_bars(sym, "4h")
        b1 = fetch_bars(sym, "1d")
        f = load_funding(sym, start=FUNDING_START)
        print(f"{sym}: 4h {len(b4):,} bars {b4.index[0]:%Y-%m-%d} -> {b4.index[-1]:%Y-%m-%d %H:%M}; "
              f"1d {len(b1):,} from {b1.index[0]:%Y-%m-%d}; funding {len(f):,} "
              f"[{time.time() - t0:.0f}s]", flush=True)
        p = TRAIN_4H / f"{sym}_4h_frames.pkl"
        if not p.exists():
            pickle.dump(_frames(b4, sym, "4h"), open(p, "wb"))
        p = LAB_FRAMES / f"{sym}_4h_frames.pkl"
        if not p.exists():
            # computed on the CUT bars, not sliced from the full ones: exactly
            # how the fifteen's were made (their bars simply ended there)
            pickle.dump(_frames(b4[b4.index <= LAB_END], sym, "4h"), open(p, "wb"))
        p = FRAMES_1D / f"{sym}_1d_frames.pkl"
        if not p.exists():
            pickle.dump(_frames(b1, sym, "1d"), open(p, "wb"))
        print(f"  {sym}: frames done [{time.time() - t0:.0f}s]", flush=True)
    link_old()
    for sym in OLD:
        p = FRAMES_1D / f"{sym}_1d_frames.pkl"
        if not p.exists():
            from livefeed.store import BarStore
            b1 = BarStore(sym, "1d").load()       # read only: the store exists
            pickle.dump(_frames(b1, sym, "1d"), open(p, "wb"))
            print(f"  {sym}: 1d frames {len(b1):,} bars to {b1.index[-1]:%Y-%m-%d} "
                  f"[{time.time() - t0:.0f}s]", flush=True)


def _link(src: Path, dst: Path) -> None:
    if dst.is_symlink() or dst.exists():
        return
    dst.symlink_to(src.resolve())


def link_old() -> None:
    """The fifteen's existing frames, LINKED (never copied or rewritten), so
    the 25-coin lab and trainer read the very files the baseline used."""
    for sym in OLD:
        _link(Path("data_cache/research_frames") / f"{sym}_4h_frames.pkl",
              LAB_FRAMES / f"{sym}_4h_frames.pkl")
        _link(Path("data_cache/train_frames_4h") / f"{sym}_4h_frames.pkl",
              TRAIN_4H / f"{sym}_4h_frames.pkl")
    cache = LAB_FRAMES / "wf4h"
    (cache / "scores").mkdir(parents=True, exist_ok=True)
    for sym in OLD:
        src = Path("data_cache/research_frames/wf4h") / f"metrics_{sym}.pkl"
        if src.exists():
            _link(src, cache / f"metrics_{sym}.pkl")
    # the shipped baseline's scores, as a reference row: COPIED, so nothing
    # run here can ever write through to the original
    src = Path("data_cache/research_frames/wf4h/scores/live_bag5_scores.pkl")
    dst = cache / "scores" / "live_bag5_scores.pkl"
    if src.exists() and not dst.exists():
        import shutil
        shutil.copyfile(src, dst)


# ----------------------------------------------------------------------- lab
BAG = [7, 11, 13, 17, 19]           # train_pooled_4h.BAG_SEEDS
BAG_B = [23, 29, 31, 37, 41]        # a second, disjoint bag: the bag's own noise
VARIANTS = {
    # trained on all 25, scored on all 25 -> (c); filtered to the 15 -> (b)
    "x25_bag5":  dict(pooled=True, blocks=["base"], data="sb0.5", seeds=BAG),
    # trained on the 15, scored on all 25 -> (d); filtered to the 15 -> (a)
    "x15_bag5":  dict(pooled=True, blocks=["base"], data="sb0.5", seeds=BAG,
                      train_coins="SERVED15"),
    "x25_bag5b": dict(pooled=True, blocks=["base"], data="sb0.5", seeds=BAG_B),
    "x15_bag5b": dict(pooled=True, blocks=["base"], data="sb0.5", seeds=BAG_B,
                      train_coins="SERVED15"),
}
ON15 = "_on15"                      # suffix of a score file cut to the fifteen


def lab():
    """research/wf4h.py, pointed at the 25-coin frames and its own cache.

    Only module attributes are repointed; every function is the lab's own.
    The fifteen-coin cache, scores and summaries are never opened for
    writing."""
    import research.wf4h as L
    L.FRAMES = LAB_FRAMES
    L.CACHE = LAB_FRAMES / "wf4h"
    L.SCORES = L.CACHE / "scores"
    L.OUT = LAB_OUT
    L.COIN_SETS["SERVED15"] = set(OLD)
    L.VARIANTS.update(VARIANTS)
    return L


def lab_build() -> None:
    """The 25-coin "sb0.5" datasets (stop 0.5 ATR beyond the level), by the
    lab's own `build_stop_buffer`, then a check that the fifteen's rows in
    them are the baseline's rows exactly -- so (a) and (b) differ only in
    what trained them."""
    L = lab()
    link_old()
    have = sorted(p.name.split("_4h_")[0] for p in LAB_FRAMES.glob("*_4h_frames.pkl"))
    if have != ALL:
        raise SystemExit(f"lab frames are not the 25: missing {sorted(set(ALL) - set(have))}")
    if not all((L.CACHE / f"{s}_sb0.5.pkl").exists() for s in ("long", "short")):
        L.build_stop_buffer(0.5)
    for side in ("long", "short"):
        base = pickle.load(open(Path("data_cache/research_frames/wf4h") / f"{side}_sb0.5.pkl", "rb"))
        D = L.load(side, "sb0.5")
        sub = D[D["coin"].isin(OLD)].reset_index(drop=True)
        base = base.reset_index(drop=True)
        extra = [c for c in D.columns if c not in base.columns]
        # every column the base-block variants read -- the labels, P&L,
        # weights, timing and the 87 features -- must match exactly
        used = list(L.META) + L.feature_sets(base)["base"]
        pd.testing.assert_frame_equal(sub[used], base[used], check_dtype=False)
        # the rest (geo_/pos_ blocks, unused here) is reported, not required:
        # the geo_ level-KIND one-hots changed with agent5/structure.py after
        # the baseline's cache was built (2026-09-22 21:54 vs 22:15)
        other = [c for c in base.columns if c not in used and not (
            sub[c].astype(str).to_numpy() == base[c].astype(str).to_numpy()).all()]
        print(f"{side}: {len(D):,} rows over {D['coin'].nunique()} coins; the fifteen's "
              f"{len(sub):,} rows identical to the baseline's on META + {len(used) - len(L.META)} "
              f"base features; unused columns that differ: {other or 'none'}; "
              f"columns only in the 25-coin set: {extra or 'none'}", flush=True)
        for s, g in D[~D["coin"].isin(OLD)].groupby("coin"):
            print(f"   {s:<9} {len(g):6,} rows  {g['t'].min():%Y-%m-%d} -> {g['t'].max():%Y-%m-%d}  "
                  f"base rate {np.average(g['y'], weights=g['w']):.3f}", flush=True)


def lab_run(names) -> None:
    L = lab()
    for name in names:
        if name not in VARIANTS:
            # never L.run() a name this file does not own: a score file of the
            # same name could be a link or copy of the baseline's
            raise SystemExit(f"{name}: not one of {sorted(VARIANTS)}")
        R = L.run(name)
        cut15(name, R)


def cut15(name: str, R: pd.DataFrame = None) -> str:
    """A 25-coin score file cut to the fifteen. Identical to having run the
    variant with `score_coins` = the fifteen: a row's score does not depend
    on which other rows are scored, and the rank is taken afterwards, over
    what is left."""
    L = lab()
    if R is None:
        R = pickle.load(open(L.SCORES / f"{name}_scores.pkl", "rb"))
    out = name + ON15
    pickle.dump(R[R["coin"].isin(OLD)].reset_index(drop=True),
                open(L.SCORES / f"{out}_scores.pkl", "wb"))
    return out


def _measure(D: pd.DataFrame) -> dict:
    """The served policy's figures, as research/win_rate.measure computes
    them: every call (win, net per trade) and the three-position account."""
    import research.improve_4h as I
    L = lab()
    net = D["pnl"] - L.COST - D["funding"]
    ho = (D["window"] >= L.DEV_WINDOWS).to_numpy()
    a_dev, a_ho = I.account(D[~ho]), I.account(D[ho])
    cells = I.per_window(D)
    return {"trades": int(len(D)), "trades_dev": int((~ho).sum()), "trades_hold": int(ho.sum()),
            "win_dev": float((net[~ho] > 0).mean()), "win_hold": float((net[ho] > 0).mean()),
            "net_dev": float(net[~ho].mean()), "net_hold": float(net[ho].mean()),
            "acct_trades_dev": a_dev.get("trades", 0), "acct_trades_hold": a_ho.get("trades", 0),
            "acct_win_dev": a_dev.get("win", 0), "acct_win_hold": a_ho.get("win", 0),
            "acct_net_dev": a_dev.get("net_per_trade", 0), "acct_net_hold": a_ho.get("net_per_trade", 0),
            "sh_dev": a_dev.get("sharpe", 0), "sh_hold": a_ho.get("sharpe", 0),
            "dd_dev": a_dev.get("max_drawdown_pct", 0), "dd_hold": a_ho.get("max_drawdown_pct", 0),
            "half_years": [float(c) for c in cells], "total": float(sum(cells)),
            "positive": int(sum(c > 0 for c in cells))}


def _auc(R: pd.DataFrame, by: str = "window") -> dict:
    from sklearn.metrics import roc_auc_score
    return {int(k) if by == "window" else k:
            float(roc_auc_score(g["y"], g["p"], sample_weight=g["w"]))
            for k, g in R.groupby(by) if g["y"].nunique() > 1}


ROWS = [  # (label, score file)
    ("(a)  15-coin pool, scored on the 15", "x15_bag5" + ON15),
    ("(b)  25-coin pool, scored on the 15", "x25_bag5" + ON15),
    ("(c)  25-coin pool, scored on all 25", "x25_bag5"),
    ("(d)  15-coin pool, scored on all 25", "x15_bag5"),
    ("(a') bag B: 15 on the 15", "x15_bag5b" + ON15),
    ("(b') bag B: 25 on the 15", "x25_bag5b" + ON15),
    ("(c') bag B: 25 on all 25", "x25_bag5b"),
    ("(d') bag B: 15 on all 25", "x15_bag5b"),
    ("ref  live_bag5 (the shipped lab run)", "live_bag5"),
]


def lab_report(wait: int = 6, scaled: bool = False) -> dict:
    """The (a)-(d) table. `scaled`: trade the calls with the scale-out the
    owner shipped on 2026-09-23 (train_pooled_4h.SCALE_OUT_PART/_AT: a third
    off halfway to the target, the rest's stop to the entry), through
    improve_4h.served -> monitor.resting_orders, the server's own code."""
    import research.improve_4h as I
    L = lab()
    kw = {}
    if scaled:
        import train_pooled_4h as T
        kw = dict(scale_part=T.SCALE_OUT_PART, scale_at=T.SCALE_OUT_AT)
        print(f"served WITH the scale-out: {T.SCALE_OUT_PART:.3f} off at {T.SCALE_OUT_AT:.0%} "
              f"of the way, the rest's stop to the entry", flush=True)
    out = {}
    for label, name in ROWS:
        if not (L.SCORES / f"{name}_scores.pkl").exists():
            continue
        R = L.with_ranks(pickle.load(open(L.SCORES / f"{name}_scores.pkl", "rb")))
        C = I.calls(name)
        D = I.served(C, wait=wait, **kw)
        m = _measure(D)
        m["auc_window"] = _auc(R)
        m["auc_side"] = {k: v for k, v in _auc(R, "side").items()}
        m["auc_dev"] = float(np.mean([v for k, v in m["auc_window"].items() if k < L.DEV_WINDOWS]))
        m["auc_hold"] = float(np.mean([v for k, v in m["auc_window"].items() if k >= L.DEV_WINDOWS]))
        m["calls"] = int(len(C))
        if R["coin"].isin(NEW).any():
            new = R["coin"].isin(NEW)
            m["auc_window_new10"] = _auc(R[new])
            m["auc_window_old15"] = _auc(R[~new])
            dn = D[D["coin"].isin(NEW)]; do = D[~D["coin"].isin(NEW)]
            for tag, x in (("new10", dn), ("old15", do)):
                net = x["pnl"] - L.COST - x["funding"]
                m[f"served_{tag}"] = {"trades": int(len(x)), "win": float((net > 0).mean()) if len(x) else None,
                                      "net": float(net.mean()) if len(x) else None}
            m["calls_new10_share"] = float(C["coin"].isin(NEW).mean())
            m["served_by_coin"] = {
                c: {"trades": int(len(g)),
                    "net": float((g["pnl"] - L.COST - g["funding"]).mean()),
                    "win": float(((g["pnl"] - L.COST - g["funding"]) > 0).mean())}
                for c, g in D.groupby("coin")}
        out[name] = dict(m, label=label)
        _show(label, name, m)
    LAB_OUT.mkdir(parents=True, exist_ok=True)
    f = LAB_OUT / ("report_scaled.json" if scaled else "report.json")
    f.write_text(json.dumps(out, indent=1, default=float))
    print(f"\nwritten {f}", flush=True)
    return out


def _show(label: str, name: str, m: dict) -> None:
    aw = " ".join(f"{m['auc_window'].get(w, float('nan')):.3f}" for w in range(6))
    hy = " ".join(f"{c:+.1f}" for c in m["half_years"])
    print(f"\n{label}   [{name}]\n"
          f"  AUC by window {aw}   (dev mean {m['auc_dev']:.4f}, hold mean {m['auc_hold']:.4f}; "
          f"long {m['auc_side'].get('long', float('nan')):.4f} short {m['auc_side'].get('short', float('nan')):.4f})\n"
          f"  served, every call: {m['trades']} trades ({m['trades_dev']}/{m['trades_hold']}), "
          f"win {m['win_dev']:.1%} / {m['win_hold']:.1%}, per trade {m['net_dev']:+.3f}% / {m['net_hold']:+.3f}%\n"
          f"  3 at once: {m['acct_trades_dev']}/{m['acct_trades_hold']} trades, win {m['acct_win_dev']:.1%} / "
          f"{m['acct_win_hold']:.1%}, per trade {m['acct_net_dev']:+.3f}% / {m['acct_net_hold']:+.3f}%, "
          f"Sharpe {m['sh_dev']:.2f} / {m['sh_hold']:.2f}, maxDD {m['dd_dev']:.1f}% / {m['dd_hold']:.1f}%\n"
          f"  half-years {hy}   total {m['total']:+.1f}%  positive {m['positive']}/6", flush=True)
    if "served_new10" in m:
        n, o = m["served_new10"], m["served_old15"]
        an = " ".join(f"{m['auc_window_new10'].get(w, float('nan')):.3f}" for w in range(6))
        print(f"  new ten: AUC by window {an}; {m['calls_new10_share']:.0%} of calls; served "
              f"{n['trades']} trades win {n['win'] or 0:.1%} net {n['net'] or 0:+.3f}%  | old fifteen "
              f"{o['trades']} trades win {o['win'] or 0:.1%} net {o['net'] or 0:+.3f}%", flush=True)


def lab_detail(wait: int = 6) -> dict:
    """Two diagnostics behind (c), both on the 25-coin pool's scores.

    EQUAL CALL COUNT. At the served cut (top 3% of the pooled rank) 25
    coins make ~65% more calls than 15, so (c) mixes "better choice" with
    "more trades". Cut 0.982 is the same 3% of the fifteen's readings spread
    over 25 coins (3% x 15/25 = 1.8%) -- arithmetic, not a search -- so the
    calls are the best ~as-many as today, from a bigger pool.

    A SUBSET CHOSEN ON DEVELOPMENT. The new coins whose served trades made
    money in the four development half-years (bag A), added to the fifteen,
    then read on the two holdout half-years it did not see. Per-coin P&L
    on ~100 trades is noisy, so this is the most a subset can be expected
    to show, not a recommendation of the subset.
    """
    import research.improve_4h as I
    L = lab()
    out = {}
    for name in ("x15_bag5" + ON15, "x25_bag5", "x25_bag5b", "x15_bag5"):
        for cut in (0.97, 0.982):
            if cut == 0.97 and name != "x15_bag5" + ON15:
                continue
            D = I.served(I.calls(name, cut=cut), wait=wait)
            m = _measure(D)
            out[f"{name}@{cut}"] = m
            print(f"{name:<16} cut {cut}: served {m['trades']} trades ({m['trades_dev']}/{m['trades_hold']}), "
                  f"win {m['win_dev']:.1%} / {m['win_hold']:.1%}, per trade {m['net_dev']:+.3f}% / "
                  f"{m['net_hold']:+.3f}% | 3 at once Sharpe {m['sh_dev']:.2f} / {m['sh_hold']:.2f}, "
                  f"total {m['total']:+.1f}%, positive {m['positive']}/6", flush=True)
    # per new coin, development vs holdout, both bags
    keep = None
    for name in ("x25_bag5", "x25_bag5b"):
        D = I.served(I.calls(name), wait=wait)
        D["net"] = D["pnl"] - L.COST - D["funding"]
        D["part"] = np.where(D["window"] < L.DEV_WINDOWS, "dev", "hold")
        t = D[D["coin"].isin(NEW)].groupby(["coin", "part"])["net"].agg(["count", "mean"]).unstack("part")
        print(f"\n{name}: the new ten, served trades (count, mean net %) dev | hold\n"
              + t.round(3).to_string(), flush=True)
        out[f"{name}_per_new_coin"] = {c: {k: float(v) for k, v in r.items()} for c, r in
                                       t.set_axis([f"{a}_{b}" for a, b in t.columns], axis=1).iterrows()}
        if keep is None:
            dev = t[("mean", "dev")]
            keep = sorted(dev[dev > 0].index)
    print(f"\nnew coins positive on development (bag A): {keep}", flush=True)
    for name in ("x25_bag5", "x25_bag5b"):
        R = pickle.load(open(L.SCORES / f"{name}_scores.pkl", "rb"))
        sel = f"{name}_devsel"
        pickle.dump(R[R["coin"].isin(OLD + keep)].reset_index(drop=True),
                    open(L.SCORES / f"{sel}_scores.pkl", "wb"))
        m = _measure(I.served(I.calls(sel), wait=wait))
        out[sel] = dict(m, coins=OLD + keep)
        print(f"{sel:<18} ({15 + len(keep)} coins): served {m['trades']} trades, win {m['win_dev']:.1%} / "
              f"{m['win_hold']:.1%}, per trade {m['net_dev']:+.3f}% / {m['net_hold']:+.3f}% | 3 at once "
              f"Sharpe {m['sh_dev']:.2f} / {m['sh_hold']:.2f}, total {m['total']:+.1f}%, "
              f"positive {m['positive']}/6", flush=True)
    LAB_OUT.mkdir(parents=True, exist_ok=True)
    (LAB_OUT / "detail.json").write_text(json.dumps(out, indent=1, default=float))
    return out


# ------------------------------------------------------------------- staging
def _load_frames(directory: Path, sym: str, interval: str):
    """A cached (bars, frames, warm) triple, or an error. Never falls back to
    research.pooled_daily.frames_for, which would open the live store."""
    p = directory / f"{sym}_{interval}_frames.pkl"
    if not p.exists():
        raise SystemExit(f"{p} missing: run `fetch` first")
    return pickle.load(open(p, "rb"))


def stage_4h(tag: str = "25") -> dict:
    """The production 4h fit (train_pooled_4h.main, step for step: same
    config, pooling, scale-bound drop, purged CV, shuffled control, gate,
    five-seed bag) over the 25 coins, saved to the staging directory.
    Nothing is installed and no score book is touched.

    `tag="15"` refits the fifteen from the same frames as a REFERENCE for
    the cross-validated numbers (it should reproduce the installed verdict);
    its verdict is written, its models are not."""
    import train_pooled_4h as T
    import research.pooled_daily as PD
    from agent5 import Agent5Config, JudgeAgent
    from core import barriers_for, beats_shuffle, slot_side

    coins = {"25": ALL, "15": OLD}[tag]
    interval = T.INTERVAL
    frames = {s: _load_frames(TRAIN_4H, s, interval) for s in coins}
    k, h1, h2 = barriers_for(interval)
    pool_id = f"crypto-4h-staging{tag}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    MODELS.mkdir(parents=True, exist_ok=True)
    verdict = {"symbol": "POOLED", "interval": interval, "source": "pooled", "staging": True,
               "coins": sorted(frames), "rank_pool": pool_id,
               "stop_buffer_atr": T.STOP_BUFFER_ATR, "bag_seeds": list(T.BAG_SEEDS),
               "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "frames_end": {s: str(f[0].index[-1]) for s, f in frames.items()},
               "horizons": {}}
    print(f"STAGING pooled 4h fit over {len(frames)} coins; pool {pool_id}", flush=True)
    for slot, hold in ((1, h1), (2, h2)):
        side = slot_side(interval, slot)
        cfg = Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k, geometry="structure",
                           side=side, stop_buffer_atr=T.STOP_BUFFER_ATR, rank_pool=pool_id,
                           entry_offset_atr=T.ENTRY_OFFSET_ATR, entry_valid_bars=T.ENTRY_VALID_BARS,
                           bag_seeds=T.BAG_SEEDS,
                           scale_out_part=getattr(T, "SCALE_OUT_PART", 0.0),
                           scale_out_at=getattr(T, "SCALE_OUT_AT", 0.0))
        parts = {}
        for sym, (bars, fr, warm) in frames.items():
            ds = JudgeAgent(cfg).build(bars, warmup=warm, **fr)
            if len(ds) > 500:
                parts[sym] = ds
        dropped = PD.scale_bound_columns(parts)
        dropped15 = PD.scale_bound_columns({s: d for s, d in parts.items() if s in OLD})
        pooled, coin = T.pool_4h(parts)
        cols = [c for c in pooled.X.columns if c not in dropped]
        judge = JudgeAgent(cfg)
        t0 = time.time()
        report = judge.fit(pooled, columns=cols, with_shuffle=True,
                           with_ablation=False, with_importance=False)
        ev = report.evaluation
        ok = beats_shuffle(ev.auc, ev.shuffle_auc, ev.auc_spread)
        by_coin = PD.per_coin_auc(report.fit, pooled, coin)
        print(f"  h{slot} ({side}): AUC {ev.auc:.4f}  spread {ev.auc_spread:.4f}  "
              f"shuffle {ev.shuffle_auc:.4f}  {'CLEARS' if ok else 'fails'}  "
              f"({len(pooled):,} samples, {len(parts)} coins, {len(cols)} columns; "
              f"scale-bound dropped {dropped} vs {dropped15} on the fifteen) "
              f"[{time.time() - t0:.0f}s]", flush=True)
        on15 = float(np.mean([a for c, a in by_coin.items() if c in OLD]))
        print(f"     mean out-of-fold AUC on the fifteen {on15:.4f}", flush=True)
        print("     out-of-fold AUC per coin: " + "  ".join(
            f"{s}{'*' if s in NEW else ''} {a:.3f}" for s, a in sorted(by_coin.items(), key=lambda kv: -kv[1])),
            flush=True)
        verdict["horizons"][f"h{slot}"] = {
            "auc": round(float(ev.auc), 4), "shuffle": round(float(ev.shuffle_auc), 4),
            "spread": round(float(ev.auc_spread), 4), "effective_n": int(len(pooled)),
            "beats_shuffle": bool(ok), "hold": hold, "geometry": "structure", "side": side,
            "columns": len(cols), "dropped_scale_bound": dropped,
            "dropped_scale_bound_on_the_15": dropped15, "per_coin_oof_auc": by_coin,
            "mean_oof_auc_on_the_15": round(on15, 4),
        }
        if tag == "25":
            path = MODELS / f"judge_POOLED25_4h_h{slot}.joblib"
            judge.save(path)
            print(f"     saved {path}", flush=True)
    (MODELS / f"eval_POOLED{tag}_4h.json").write_text(json.dumps(verdict, indent=1))
    return verdict


def stage_1d() -> dict:
    """The daily pooled fit (train_daily_pooled.main, step for step) over the
    fifteen AND over the 25, from the same fresh frames, so the two are
    comparable; only the 25-coin models are saved (to staging)."""
    import research.pooled_daily as PD
    from agent5 import Agent5Config, JudgeAgent
    from core import barriers_for, beats_shuffle, geometry_for, slot_side

    interval = "1d"
    PD.INTERVAL = interval
    PD.CACHE = FRAMES_1D
    for s in ALL:                  # PD.build_one must find every frame cached
        _load_frames(FRAMES_1D, s, interval)
    k, h1, h2 = barriers_for(interval)
    structure = geometry_for(interval) == "structure"
    MODELS.mkdir(parents=True, exist_ok=True)
    result = {}
    for tag, coins in (("15", OLD), ("25", ALL)):
        verdict = {"symbol": "POOLED", "interval": interval, "source": "pooled", "staging": True,
                   "coins": list(coins),
                   "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "horizons": {}}
        for slot, hold in ((1, h1), (2, h2)):
            side = slot_side(interval, slot) if structure else None
            parts = {}
            for sym in coins:
                ds = PD.build_one(sym, hold, k, side=side)
                if ds is not None and len(ds) > 200:
                    parts[sym] = ds
            dropped = PD.scale_bound_columns(parts)
            pooled, coin = PD.pool(parts)
            cols = [c for c in pooled.X.columns if c not in dropped]
            cfg = (Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k, geometry="structure", side=side)
                   if structure else Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k))
            judge = JudgeAgent(cfg)
            report = judge.fit(pooled, columns=cols, with_shuffle=True,
                               with_ablation=False, with_importance=False)
            ev = report.evaluation
            ok = beats_shuffle(ev.auc, ev.shuffle_auc, ev.auc_spread)
            by_coin = PD.per_coin_auc(report.fit, pooled, coin)
            old = [a for s, a in by_coin.items() if s in OLD]
            print(f"  1d {tag} coins h{slot} ({side}): AUC {ev.auc:.4f}  spread {ev.auc_spread:.4f}  "
                  f"shuffle {ev.shuffle_auc:.4f}  {'CLEARS' if ok else 'fails'}  "
                  f"({len(pooled):,} samples, {len(parts)} coins, {len(cols)} columns); "
                  f"mean OOF AUC on the fifteen {np.mean(old):.4f}", flush=True)
            verdict["horizons"][f"h{slot}"] = {
                "auc": round(float(ev.auc), 4), "shuffle": round(float(ev.shuffle_auc), 4),
                "spread": round(float(ev.auc_spread), 4), "effective_n": int(len(pooled)),
                "beats_shuffle": bool(ok), "hold": hold,
                **({"geometry": "structure", "side": side} if side else {}),
                "columns": len(cols), "dropped_scale_bound": dropped,
                "per_coin_oof_auc": by_coin,
                "mean_oof_auc_on_the_15": round(float(np.mean(old)), 4),
            }
            if tag == "25":
                path = MODELS / f"judge_POOLED25_1d_h{slot}.joblib"
                judge.save(path)
                print(f"     saved {path}", flush=True)
        (MODELS / f"eval_POOLED{tag}_1d.json").write_text(json.dumps(verdict, indent=1))
        result[tag] = verdict
    return result


def main() -> int:
    os.chdir(ROOT)                  # every path here, and the lab's, is repo-relative
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        fetch()
    elif cmd == "lab-build":
        lab_build()
    elif cmd == "lab-run":
        lab_run(sys.argv[2:])
    elif cmd == "lab-report":
        lab_report(scaled=sys.argv[2:3] == ["scaled"])
    elif cmd == "lab-detail":
        lab_detail()
    elif cmd == "stage-4h":
        stage_4h(sys.argv[2] if len(sys.argv) > 2 else "25")
    elif cmd == "stage-1d":
        stage_1d()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
