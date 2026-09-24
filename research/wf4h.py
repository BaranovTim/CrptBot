"""Walk-forward laboratory for the 4h structure models.

WHY A LAB AND NOT ANOTHER ONE-OFF SCRIPT
    Every study in this directory so far fits once, scores one held-out
    year, and reports an average per selected bar. That was enough to find
    that pooling helps. It is not enough to decide what to BUILD, for three
    reasons this harness exists to fix:

      1. One or two test years is one or two draws. Here the model is refit
         every six months on its own past only and scored on the next six:
         six disjoint out-of-time windows, Sep 2023 -> Sep 2026.
      2. Many variants tried on the same years is how a 2-sigma result gets
         shipped. Variants are CHOSEN on the first four windows
         ("development", Sep 2023 -> Sep 2025) and the last two ("holdout",
         Sep 2025 -> Sep 2026) are only ever read as confirmation.
      3. "Mean net per selected bar" is not what an account earns: selected
         bars overlap (16-bar holds, consecutive bars), several coins fire
         at once, and capital is finite. `portfolio` simulates it: at most
         K positions, never two on one coin, enter at the close, exit when
         the label resolves, fees on every trade.

THE RANK, AS PRODUCTION COMPUTES IT
    Production ranks the newest score against the trailing 540 scores the
    SAME model gives, and right after a refit most of those bars are in its
    training set. Each window's model therefore also scores the 540 bars
    before the window, and ranks are taken over that, exactly as the server
    would have. (It is a real effect: in-sample scores are more extreme, so
    fewer calls fire straight after a refit.)

FEATURE BLOCKS BEYOND THE SHIPPED 88
    geo_*   the trade's own geometry: target and stop distance in ATR, their
            ratio, the level behind the target, what KIND of level each is.
            Known at the close. The shipped model is asked whether it
            reaches a target without being told how far away it is, and
            reward:risk alone ranks the label better than the model does.
    pos_*   positioning from Binance's daily metrics archive, which the
            project has downloaded for years and discarded all but open
            interest from: top traders' long/short by accounts and by
            position, every account's long/short, the taker volume ratio.
            Public, five-minute, and served live by the REST endpoints
            `topLongShortPositionRatio`, `globalLongShortAccountRatio`,
            `takerlongshortRatio`.
    xs_*    the cross-section: where this coin's 1d / 7d / 30d return and
            volatility rank among the fifteen at this close, the median
            coin's return, breadth above the 50/200 EMA. A single-asset
            pipeline cannot express these; a pooled model can.

    python research/wf4h.py build                 # caches, once
    python research/wf4h.py run pooled_base       # one variant
    python research/wf4h.py report pooled_base [per_coin ...]
"""
from __future__ import annotations

import glob
import io
import json
import pickle
import sys
import time
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config                              # noqa: E402
from agent5.dataset import Dataset, build_dataset           # noqa: E402
from agent5.labels import _atr                               # noqa: E402
from agent5.model import fit_final                           # noqa: E402
from agent5.structure import structural_levels, structure_barrier  # noqa: E402

FRAMES = Path("data_cache/research_frames")
CACHE = FRAMES / "wf4h"
OUT = Path("research/results/wf4h")          # summaries (small, worth keeping)
SCORES = CACHE / "scores"                     # per-bar scores (~20 MB each, git-ignored)
METRICS = Path("data_cache/futures/um/metrics")
EPOCH = pd.Timestamp("2015-01-01", tz="UTC")
BAR = pd.Timedelta(hours=4)
HOLD = 16
COST = 0.10
TRAIL = 540
CUTS = ["2023-09-20", "2024-03-20", "2024-09-20", "2025-03-20",
        "2025-09-20", "2026-03-20", "2026-09-20"]
DEV_WINDOWS = 4          # the first four are for choosing; the rest confirm
KINDS = ("swing", "pdh", "pdl", "equal", "fib1", "fib1.618")
COIN_SETS = {"EVAL5": {"BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"},
             "EVAL10": {"BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
                        "ADAUSDT", "BNBUSDT", "UNIUSDT", "NEARUSDT", "ARBUSDT"}}
META = ["coin", "t", "pos", "t1", "exit_off", "y", "w", "pnl", "tp", "sl", "touch"]


def _pos(t) -> np.ndarray:
    ix = pd.DatetimeIndex(t)
    ix = ix.tz_localize("UTC") if ix.tz is None else ix
    return np.asarray((ix - EPOCH) / BAR, dtype=np.int64)


def coins() -> list:
    return sorted(p.name.split("_4h_")[0] for p in FRAMES.glob("*_4h_frames.pkl"))


# --------------------------------------------------------------------- build
def _geometry(bars, levels, lab, side) -> pd.DataFrame:
    """The trade's own geometry at the close. Causal: the levels are, and the
    label's tp/sl are the barriers set at entry."""
    c = bars["close"].to_numpy(float)
    atr = _atr(bars, 14).to_numpy(float)
    long = side == "long"
    tgt2 = levels["res2"] if long else levels["sup2"]
    tk = levels["res_kind"] if long else levels["sup_kind"]
    sk = levels["sup_kind"] if long else levels["res_kind"]
    tp = lab.tp_pct.to_numpy(float); sl = lab.sl_pct.to_numpy(float)
    g = pd.DataFrame(index=bars.index)
    g["geo_tp_atr"] = tp * c / 100 / atr
    g["geo_sl_atr"] = sl * c / 100 / atr
    g["geo_rr"] = tp / sl
    g["geo_log_rr"] = np.log(tp / sl)
    g["geo_tgt2_atr"] = np.abs(tgt2 - c) / atr
    g["geo_tgt_fallback"] = (tk == "").astype(float)
    g["geo_stp_fallback"] = (sk == "").astype(float)
    for k in KINDS:
        g[f"geo_tgt_{k}"] = (tk == k).astype(float)
        g[f"geo_stp_{k}"] = (sk == k).astype(float)
    return g


def _realised(lab, bars, side) -> np.ndarray:
    """% per bar from the label: the barrier touched, or the mark at the
    time exit. The arithmetic every structure study here uses."""
    close = bars["close"].to_numpy(float)
    t1 = lab.t1.to_numpy(float)
    ok = np.isfinite(t1)
    ex = np.full(len(close), np.nan)
    sgn = 1.0 if side == "long" else -1.0
    ex[ok] = sgn * 100 * (close[t1[ok].astype(int)] / close[ok] - 1)
    touch = lab.touch.to_numpy(object)
    win, lose = ("upper", "lower") if side == "long" else ("lower", "upper")
    return np.where(touch == win, lab.tp_pct.to_numpy(float),
                    np.where((touch == lose) | (touch == "ambiguous"),
                             -lab.sl_pct.to_numpy(float), ex))


def build_side(side: str, stop_buffer: float = 0.0) -> pd.DataFrame:
    """`stop_buffer` moves the STOP past its level by that many ATRs (the
    target never moves). The placement cell the structure study left out:
    it tested target-at/stop-at and target-in-front/stop-beyond, never the
    target at the level with the stop beyond it, which is the liquidity-
    sweep story -- a stop exactly on a swing low sits where everyone's does."""
    cfg = Agent5Config(max_hold_bars=HOLD, geometry="structure", side=side)
    parts = []
    for s in coins():
        bars, fr, warm = pickle.load(open(FRAMES / f"{s}_4h_frames.pkl", "rb"))
        atr = _atr(bars, 14).to_numpy(float)
        levels = structural_levels(bars, atr)
        if stop_buffer:
            levels = dict(levels)
            if side == "long":
                levels["sup"] = levels["sup"] - stop_buffer * atr
            else:
                levels["res"] = levels["res"] + stop_buffer * atr
        lab = structure_barrier(bars, cfg, levels)
        ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **fr)
        if len(ds) < 500:
            continue
        loc = np.asarray(ds.positions, int)
        geo = _geometry(bars, levels, lab, side).iloc[loc].reset_index(drop=True)
        pnl = _realised(lab, bars, side)[loc]
        pos = _pos(ds.index)
        off = np.asarray(ds.t1, float) - loc
        m = pd.DataFrame({"coin": s, "t": pd.DatetimeIndex(ds.index), "pos": pos,
                          "t1": pos + off, "exit_off": off,
                          "y": ds.y.to_numpy(float), "w": ds.weight.to_numpy(float),
                          "pnl": pnl, "tp": lab.tp_pct.to_numpy(float)[loc],
                          "sl": lab.sl_pct.to_numpy(float)[loc],
                          "touch": lab.touch.to_numpy(object)[loc]})
        parts.append(pd.concat([m, ds.X.reset_index(drop=True), geo], axis=1))
        print(f"  {side} {s}: {len(ds):,} rows", flush=True)
    D = pd.concat(parts, ignore_index=True)
    return D.sort_values(["pos", "coin"], kind="stable").reset_index(drop=True)


def load_metrics(sym: str) -> pd.DataFrame:
    """Every five-minute positioning sample in the archive for `sym`, cached."""
    p = CACHE / f"metrics_{sym}.pkl"
    if p.exists():
        return pickle.load(open(p, "rb"))
    cols = ["count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
            "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
    frames = []
    for f in sorted(glob.glob(str(METRICS / sym / "*.zip"))):
        try:
            with zipfile.ZipFile(f) as z:
                df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        except (zipfile.BadZipFile, IndexError, ValueError, pd.errors.ParserError):
            continue
        if "create_time" not in df or not set(cols) <= set(df.columns):
            continue
        frames.append(df[["create_time"] + cols])
    if not frames:
        return pd.DataFrame()
    m = pd.concat(frames)
    m["create_time"] = pd.to_datetime(m["create_time"], utc=True, errors="coerce")
    m = m.dropna(subset=["create_time"]).drop_duplicates("create_time").set_index("create_time").sort_index()
    m = m.apply(pd.to_numeric, errors="coerce")
    pickle.dump(m, open(p, "wb"))
    return m


def positioning(sym: str, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Per-bar positioning features. A sample older than an hour at the close
    is not used: a gap in the archive must read as missing, not as the last
    value carried for days."""
    m = load_metrics(sym)
    out = pd.DataFrame(index=index)
    if m.empty:
        return out
    lg = np.log(m.where(m > 0))
    raw = pd.DataFrame({
        "top_acct": lg["count_toptrader_long_short_ratio"],
        "top_pos": lg["sum_toptrader_long_short_ratio"],
        "crowd": lg["count_long_short_ratio"],
        # the taker ratio is a per-interval flow, so it is AVERAGED over the
        # bar and the day, not sampled at the close
        "taker": lg["sum_taker_long_short_vol_ratio"].rolling("4h").mean(),
        "taker_1d": lg["sum_taker_long_short_vol_ratio"].rolling("24h").mean(),
    })
    raw["smart_vs_crowd"] = raw["top_pos"] - raw["crowd"]
    left = pd.DataFrame({"_t": index})
    al = pd.merge_asof(left, raw.reset_index().rename(columns={"create_time": "_s"}),
                       left_on="_t", right_on="_s", direction="backward",
                       tolerance=pd.Timedelta(hours=1))
    al.index = index
    for c in ("top_acct", "top_pos", "crowd", "smart_vs_crowd", "taker", "taker_1d"):
        x = al[c]
        out[f"pos_{c}"] = x
        mu = x.rolling(180, min_periods=60).mean(); sd = x.rolling(180, min_periods=60).std()
        out[f"pos_{c}_z"] = (x - mu) / sd.replace(0, np.nan)
        if c not in ("taker", "taker_1d"):
            out[f"pos_{c}_chg1d"] = x - x.shift(6)
    return out


def cross_section() -> pd.DataFrame:
    """Per (bar, coin): where this coin stands among the fifteen at this close."""
    closes, atrp = {}, {}
    for s in coins():
        bars = pickle.load(open(FRAMES / f"{s}_4h_frames.pkl", "rb"))[0]
        closes[s] = bars["close"]
        atrp[s] = _atr(bars, 14) / bars["close"]
    C = pd.DataFrame(closes).sort_index(); A = pd.DataFrame(atrp).reindex(C.index)
    n = C.notna().sum(axis=1)
    feats = {}
    for name, k in (("1d", 6), ("7d", 42), ("30d", 180)):
        r = C.pct_change(k, fill_method=None)
        feats[f"xs_ret{name}_rank"] = r.rank(axis=1, pct=True)
        feats[f"xs_ret{name}_dev"] = r.sub(r.median(axis=1), axis=0) * 100
        feats[f"xs_mkt_ret{name}"] = pd.DataFrame({c: r.median(axis=1) * 100 for c in C.columns})
    feats["xs_vol_rank"] = A.rank(axis=1, pct=True)
    for span in (50, 200):
        above = (C > C.ewm(span=span, adjust=False).mean()).where(C.notna())
        b = above.mean(axis=1)
        feats[f"xs_breadth_ema{span}"] = pd.DataFrame({c: b for c in C.columns})
    btc7 = C["BTCUSDT"].pct_change(42, fill_method=None) if "BTCUSDT" in C else None
    if btc7 is not None:
        feats["xs_ret7d_vs_btc"] = C.pct_change(42, fill_method=None).sub(btc7, axis=0) * 100
    thin = n < 5
    rows = []
    for name, F in feats.items():
        F = F.where(~thin, np.nan, axis=0) if hasattr(F, "where") else F
        rows.append(F.stack(dropna=False).rename(name))
    X = pd.concat(rows, axis=1)
    X.index.names = ["t", "coin"]
    return X.reset_index()


def build() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    xs = cross_section()
    print(f"cross-section: {len(xs):,} rows [{time.time()-t0:.0f}s]", flush=True)
    pos = []
    for s in coins():
        bars = pickle.load(open(FRAMES / f"{s}_4h_frames.pkl", "rb"))[0]
        P = positioning(s, bars.index)
        P["coin"] = s; P["t"] = P.index
        pos.append(P.reset_index(drop=True))
        print(f"  positioning {s}: {int(P.filter(like='pos_top_pos').notna().any(axis=1).sum()):,} bars "
              f"[{time.time()-t0:.0f}s]", flush=True)
    pos = pd.concat(pos, ignore_index=True)
    for side in ("long", "short"):
        D = build_side(side)
        D = D.merge(xs, on=["t", "coin"], how="left").merge(pos, on=["t", "coin"], how="left")
        pickle.dump(D, open(CACHE / f"{side}.pkl", "wb"))
        print(f"{side}: {len(D):,} rows, {D.shape[1]} columns [{time.time()-t0:.0f}s]", flush=True)


def load(side: str, data: str = "") -> pd.DataFrame:
    return pickle.load(open(CACHE / f"{side}{'_' + data if data else ''}.pkl", "rb"))


def _positioning_frame() -> pd.DataFrame:
    pos = []
    for s in coins():
        bars = pickle.load(open(FRAMES / f"{s}_4h_frames.pkl", "rb"))[0]
        P = positioning(s, bars.index)
        P["coin"] = s; P["t"] = P.index
        pos.append(P.reset_index(drop=True))
    return pd.concat(pos, ignore_index=True)


def build_stop_buffer(b: float) -> None:
    pos = _positioning_frame()
    for side in ("long", "short"):
        D = build_side(side, stop_buffer=b).merge(pos, on=["t", "coin"], how="left")
        pickle.dump(D, open(CACHE / f"{side}_sb{b:g}.pkl", "wb"))
        print(f"{side} stop +{b:g} ATR: {len(D):,} rows", flush=True)


BLOCKS = ("geo", "pos", "xs", "flw", "swp", "liq", "cbp", "htf")


def feature_sets(D: pd.DataFrame) -> dict:
    extra = tuple(f"{b}_" for b in BLOCKS)
    base = [c for c in D.columns if c not in META and not c.startswith(extra)]
    out = {"base": base}
    for b in BLOCKS:
        out[b] = [c for c in D.columns if c.startswith(f"{b}_")]
    return out


# ------------------------------------------------------------------ variants
VARIANTS = {
    # the shipped approach, inside the same walk-forward: a model per coin
    "per_coin":        dict(pooled=False, blocks=["base"]),
    "pooled_base":     dict(pooled=True, blocks=["base"]),
    "pooled_geo":      dict(pooled=True, blocks=["base", "geo"]),
    "pooled_pos":      dict(pooled=True, blocks=["base", "pos"]),
    "pooled_xs":       dict(pooled=True, blocks=["base", "xs"]),
    "pooled_all":      dict(pooled=True, blocks=["base", "geo", "pos", "xs"]),
    # the label weighted by what was at stake, not only by how unique it was
    "pooled_all_money": dict(pooled=True, blocks=["base", "geo", "pos", "xs"], weight="money"),
    "pooled_base_money": dict(pooled=True, blocks=["base"], weight="money"),
    # more capacity for eleven times the rows the defaults were set for
    "pooled_all_big":  dict(pooled=True, blocks=["base", "geo", "pos", "xs"],
                            params=dict(min_data_in_leaf=200, num_leaves=63, max_depth=8)),
    # ONE model for both sides, the side as a feature: every bar is asked
    # both questions, so this doubles the rows again the way pooling coins did
    # the label made of MONEY: regress the realised net return instead of
    # classifying target-before-stop. Huber, because a handful of 20% moves
    # would otherwise own the fit.
    "pooled_reg_base": dict(pooled=True, blocks=["base"], objective="money"),
    "pooled_reg_geo":  dict(pooled=True, blocks=["base", "geo"], objective="money"),
    "pooled_reg_all":  dict(pooled=True, blocks=["base", "geo", "pos", "xs"], objective="money"),
    # LEARNING CURVES. Does adding OTHER coins help predict the same five?
    # Scored only on the five the shipped models covered, so the three rows
    # answer about identical trades and differ only in what trained them.
    "lc_coins5":  dict(pooled=True, blocks=["base"], score_coins="EVAL5", train_coins="EVAL5"),
    "lc_coins10": dict(pooled=True, blocks=["base"], score_coins="EVAL5", train_coins="EVAL10"),
    "lc_coins15": dict(pooled=True, blocks=["base"], score_coins="EVAL5"),
    # and does old history help, or only the recent past?
    "lc_recent12m": dict(pooled=True, blocks=["base"], lookback_days=365),
    "lc_recent24m": dict(pooled=True, blocks=["base"], lookback_days=730),
    # MODEL SIZE. Early stopping picks 76-107 trees at lr 0.02, judged on one
    # recent 15% slice -- so the size of the model is decided by whichever
    # regime that slice happened to be. Fixed sizes instead:
    "trees_300": dict(pooled=True, blocks=["base"], cfg=dict(inner_val_frac=0.0),
                      params=dict(n_estimators=300)),
    "trees_800": dict(pooled=True, blocks=["base"], cfg=dict(inner_val_frac=0.0),
                      params=dict(n_estimators=800)),
    # the combination, judged as one system
    "combo_sb0.25_pos": dict(pooled=True, blocks=["base", "pos"], data="sb0.25"),
    "combo_sb0.5_pos":  dict(pooled=True, blocks=["base", "pos"], data="sb0.5"),
    "stop_beyond_0.25": dict(pooled=True, blocks=["base"], data="sb0.25"),
    "stop_beyond_0.5":  dict(pooled=True, blocks=["base"], data="sb0.5"),
    "stop_beyond_1":    dict(pooled=True, blocks=["base"], data="sb1"),
    "pooled_sides_base": dict(pooled=True, blocks=["base"], sides=True),
    "pooled_sides_all":  dict(pooled=True, blocks=["base", "geo", "pos", "xs"], sides=True),
    # ROUND FOUR (research/footprints.py): what big players leave in public
    # data, on top of the live configuration (stop 0.5 ATR beyond). The
    # reseeded live configuration is the noise floor every row is read against.
    "live_seed11":   dict(pooled=True, blocks=["base"], data="sb0.5", params=dict(seed=11)),
    "live_flw":      dict(pooled=True, blocks=["base", "flw"], data="sb0.5", extra=["flw"]),
    "live_swp":      dict(pooled=True, blocks=["base", "swp"], data="sb0.5", extra=["swp"]),
    "live_liq":      dict(pooled=True, blocks=["base", "liq"], data="sb0.5", extra=["liq"]),
    "live_flw_swp":  dict(pooled=True, blocks=["base", "flw", "swp"], data="sb0.5", extra=["flw", "swp"]),
    # the map without its geometry: mass near the price, not between the
    # price and the trade's own levels (which grows with the distance)
    "live_liq2atr":  dict(pooled=True, blocks=["base", "liq"], data="sb0.5", extra=["liq"],
                          drop=["liq_short_to_res", "liq_long_to_sup", "liq_short_beyond_res",
                                "liq_long_beyond_sup"]),
    "live_recency":  dict(pooled=True, blocks=["base"], data="sb0.5", weight="recency"),
    "live_htf":      dict(pooled=True, blocks=["base", "htf"], data="sb0.5", extra=["htf"]),
    "live_htf_bag5": dict(pooled=True, blocks=["base", "htf"], data="sb0.5", extra=["htf"],
                          seeds=[7, 11, 13, 17, 19]),
    "live_cbp_bag5": dict(pooled=True, blocks=["base", "cbp"], data="sb0.5", extra=["cbp"],
                          seeds=[7, 11, 13, 17, 19]),
    # PARENT AND CHILD (the owner's question): a model per coin on the live
    # data (the child), to blend with the pooled parent; and the pooled model
    # told which coin it is looking at
    "child_per_coin": dict(pooled=False, blocks=["base"], data="sb0.5"),
    "live_coinid_bag5": dict(pooled=True, blocks=["base"], data="sb0.5", coin_dummies=True,
                             seeds=[7, 11, 13, 17, 19]),
    "live_cbp":      dict(pooled=True, blocks=["base", "cbp"], data="sb0.5", extra=["cbp"]),
    # the noise floor, measured more than once
    "live_seed13":   dict(pooled=True, blocks=["base"], data="sb0.5", params=dict(seed=13)),
    "live_seed17":   dict(pooled=True, blocks=["base"], data="sb0.5", params=dict(seed=17)),
    "live_seed19":   dict(pooled=True, blocks=["base"], data="sb0.5", params=dict(seed=19)),
    # variance reduction: five seeds averaged; the previous refit blended in
    "live_bag5":     dict(pooled=True, blocks=["base"], data="sb0.5", seeds=[7, 11, 13, 17, 19]),
    "live_prev":     dict(pooled=True, blocks=["base"], data="sb0.5", prev_blend=True),
}


def _ds(frame: pd.DataFrame, cols, weight: str) -> Dataset:
    f = frame.sort_values("pos", kind="stable")
    w = f["w"].to_numpy(float)
    if weight == "recency":
        # old data is not stale (the learning curve), but it may be less
        # like tomorrow: halve a row's weight every two years back
        age = (f["pos"].max() - f["pos"].to_numpy()) / (6 * 365.0)
        w = w * 0.5 ** (age / 2.0)
        w = w / np.nanmean(w)
    if weight == "money":
        stake = np.abs(f["pnl"].to_numpy(float))
        stake = np.clip(stake, 0, np.nanpercentile(stake, 99))
        w = w * stake
        w = w / np.nanmean(w)
    return Dataset(X=f[cols].reset_index(drop=True), y=f["y"].reset_index(drop=True),
                   weight=pd.Series(w), t1=f["t1"].to_numpy(float),
                   positions=f["pos"].to_numpy(), blocks={}, index=pd.DatetimeIndex(f["t"]))


def _fit_money(tr: pd.DataFrame, sc: pd.DataFrame, cols) -> np.ndarray:
    """Expected net return per trade, regressed directly.

    Same guard rails as the classifier: big leaves, early stopping on a
    purged tail of the training period, uniqueness weights so overlapping
    labels do not count many times over. The target is the realised % net
    of the fee, clipped at its 1st/99th percentiles.
    """
    import lightgbm as lgb

    tr = tr.sort_values("pos", kind="stable")
    y = (tr["pnl"] - COST).to_numpy(float)
    lo, hi = np.nanpercentile(y, [1, 99]); y = np.clip(y, lo, hi)
    w = tr["w"].to_numpy(float)
    n = len(tr); cut = int(n * 0.85)
    val_start = tr["pos"].to_numpy()[cut]
    inner = np.flatnonzero(tr["t1"].to_numpy()[:cut] < val_start); val = np.arange(cut, n)
    X = tr[cols].astype(float)
    m = lgb.LGBMRegressor(objective="huber", alpha=2.0, min_data_in_leaf=500, num_leaves=31,
                          max_depth=6, learning_rate=0.02, n_estimators=2000,
                          feature_fraction=0.7, lambda_l2=5.0, verbosity=-1, seed=7,
                          deterministic=True)
    m.fit(X.iloc[inner], y[inner], sample_weight=w[inner],
          eval_set=[(X.iloc[val], y[val])], eval_sample_weight=[w[val]],
          callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(0)])
    return m.predict(sc[cols].astype(float))


def _both_sides() -> pd.DataFrame:
    parts = []
    for side in ("long", "short"):
        D = load(side)
        D["is_long"] = 1.0 if side == "long" else 0.0
        D["_side"] = side
        parts.append(D)
    return pd.concat(parts, ignore_index=True).sort_values(["pos", "coin", "_side"], kind="stable")


def run(name: str) -> pd.DataFrame:
    v = VARIANTS[name]
    if v.get("sides"):
        return _run_sides(name, v)
    out = []
    prev = {}
    t0 = time.time()
    for side in ("long", "short"):
        D = load(side, v.get("data", ""))
        for b in v.get("extra", ()):
            from research.footprints import block
            D = D.merge(block(b), on=["coin", "t"], how="left")
        fs = feature_sets(D)
        cols = [c for b in v["blocks"] for c in fs[b] if c not in v.get("drop", ())]
        if v.get("coin_dummies"):
            # THE CHILD INSIDE THE PARENT: the coin itself as an input, one
            # 0/1 column per coin, so the shared model can learn a coin's own
            # departures from the common patterns where its data supports them
            for c in sorted(D["coin"].unique()):
                D[f"is_{c}"] = (D["coin"] == c).astype(float)
                cols.append(f"is_{c}")
        cfg = Agent5Config(max_hold_bars=HOLD, geometry="structure", side=side)
        if v.get("params") or v.get("cfg"):
            params = dict(cfg.lgbm_params); params.update(v.get("params") or {})
            cfg = Agent5Config(max_hold_bars=HOLD, geometry="structure", side=side,
                               lgbm_params=params, **(v.get("cfg") or {}))
        for i in range(len(CUTS) - 1):
            cut, nxt = _pos([CUTS[i]])[0], _pos([CUTS[i + 1]])[0]
            train = D[D["t1"] < cut]
            score = D[(D["pos"] >= cut - TRAIL) & (D["pos"] < nxt)]
            if v.get("train_coins"):
                train = train[train["coin"].isin(COIN_SETS[v["train_coins"]])]
            if v.get("score_coins"):
                score = score[score["coin"].isin(COIN_SETS[v["score_coins"]])]
            if v.get("lookback_days"):
                train = train[train["pos"] >= cut - int(v["lookback_days"] * 6)]
            groups = [(None, train, score)] if v["pooled"] else [
                (s, train[train["coin"] == s], score[score["coin"] == s]) for s in score["coin"].unique()]
            for s, tr, sc in groups:
                if len(tr) < 500 or tr["y"].nunique() < 2 or sc.empty:
                    continue
                if v.get("objective") == "money":
                    p = _fit_money(tr, sc, cols)
                elif v.get("seeds"):
                    # BAGGED: the same fit under several seeds, averaged. The
                    # top 3% of a rank is where fit noise matters most -- one
                    # reseed of the live configuration moved the served
                    # account from Sharpe 2.22/2.22 to 1.82/1.40.
                    ps = []
                    for sd in v["seeds"]:
                        prm = dict(cfg.lgbm_params); prm.update(v.get("params") or {}); prm["seed"] = sd
                        c_sd = Agent5Config(max_hold_bars=HOLD, geometry="structure", side=side,
                                            lgbm_params=prm, **(v.get("cfg") or {}))
                        model, c2 = fit_final(_ds(tr, cols, v.get("weight", "unique")), c_sd, cols)
                        ps.append(model.predict_proba(sc[c2].astype(float))[:, 1])
                    p = np.mean(ps, axis=0)
                else:
                    model, c2 = fit_final(_ds(tr, cols, v.get("weight", "unique")), cfg, cols)
                    p = model.predict_proba(sc[c2].astype(float))[:, 1]
                    if v.get("prev_blend"):
                        # the previous refit's model, still scoring: half its
                        # vote. A refit changes which bars rank top on the day
                        # it lands; the blend smooths that step.
                        if prev.get(side) is not None:
                            pm, pc = prev[side]
                            p = 0.5 * p + 0.5 * pm.predict_proba(sc[pc].astype(float))[:, 1]
                        prev[side] = (model, c2)
                r = sc[["coin", "t", "pos", "exit_off", "y", "w", "pnl", "tp", "sl"]].copy()
                r["p"] = p; r["side"] = side; r["window"] = i
                r["in_window"] = r["pos"] >= cut
                out.append(r)
            print(f"  {name} {side} window {i} ({CUTS[i]}): {len(train):,} train rows "
                  f"[{time.time()-t0:.0f}s]", flush=True)
    R = pd.concat(out, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    SCORES.mkdir(parents=True, exist_ok=True)
    pickle.dump(R, open(SCORES / f"{name}_scores.pkl", "wb"))
    return R


# ------------------------------------------------------------------ funding
_FUND = {}


def _funding(coin: str) -> pd.Series:
    """Settled funding per settlement, as a PERCENT, indexed by settle time."""
    if coin not in _FUND:
        f = Path(f"data_cache/futures/um/funding/{coin}.csv")
        if f.exists():
            df = pd.read_csv(f)
            s = pd.Series(pd.to_numeric(df["funding_rate"], errors="coerce").to_numpy() * 100,
                          index=pd.to_datetime(df["funding_time"], utc=True)).sort_index()
        else:
            s = pd.Series(dtype=float)
        _FUND[coin] = s
    return _FUND[coin]


def funding_paid(d: pd.DataFrame) -> np.ndarray:
    """% each trade pays in funding between its entry close and its exit close.

    NEVER COUNTED BEFORE. Every P&L in this directory treats a perpetual
    like spot. A long pays the rate at every settlement it is open across
    (positive rates, which is most of the time) and a short receives it;
    over a multi-day hold that is the same order as the fee, and it leans
    against longs in exactly the hot markets that produce long signals.
    """
    out = np.zeros(len(d))
    entry = pd.DatetimeIndex(d["t"])
    exit_ = entry + pd.to_timedelta(d["exit_off"].to_numpy() * 4, unit="h")
    sgn = np.where(d["side"].to_numpy() == "long", 1.0, -1.0)
    for coin in d["coin"].unique():
        m = (d["coin"] == coin).to_numpy()
        f = _funding(coin)
        if f.empty:
            continue
        cum = np.concatenate([[0.0], np.cumsum(f.to_numpy())])
        a = f.index.searchsorted(entry[m], "right"); b = f.index.searchsorted(exit_[m], "right")
        out[m] = sgn[m] * (cum[b] - cum[a])
    return out


def _run_sides(name: str, v: dict) -> pd.DataFrame:
    D = _both_sides()
    fs = feature_sets(D.drop(columns=["is_long", "_side"]))
    cols = [c for b in v["blocks"] for c in fs[b]] + ["is_long"]
    cfg = Agent5Config(max_hold_bars=HOLD, geometry="structure", side="long")
    out = []; t0 = time.time()
    for i in range(len(CUTS) - 1):
        cut, nxt = _pos([CUTS[i]])[0], _pos([CUTS[i + 1]])[0]
        train = D[D["t1"] < cut]
        score = D[(D["pos"] >= cut - TRAIL) & (D["pos"] < nxt)]
        model, c2 = fit_final(_ds(train, cols, v.get("weight", "unique")), cfg, cols)
        p = model.predict_proba(score[c2].astype(float))[:, 1]
        r = score[["coin", "t", "pos", "exit_off", "y", "w", "pnl", "tp", "sl"]].copy()
        r["p"] = p; r["side"] = score["_side"].to_numpy(); r["window"] = i
        r["in_window"] = r["pos"] >= cut
        out.append(r)
        print(f"  {name} window {i} ({CUTS[i]}): {len(train):,} train rows [{time.time()-t0:.0f}s]", flush=True)
    R = pd.concat(out, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    SCORES.mkdir(parents=True, exist_ok=True)
    pickle.dump(R, open(SCORES / f"{name}_scores.pkl", "wb"))
    return R


# --------------------------------------------------------------------- rules
def _trailing_rank(pos: np.ndarray, p: np.ndarray, qpos: np.ndarray, qp: np.ndarray) -> np.ndarray:
    """Rank of each query score against the reference scores whose bar lies in
    the TRAIL bars strictly before the query's. Ties count half."""
    o = np.argsort(pos, kind="stable"); pos, p = pos[o], p[o]
    out = np.full(len(qp), np.nan)
    for b in np.unique(qpos):
        lo, hi = np.searchsorted(pos, b - TRAIL), np.searchsorted(pos, b)
        if hi - lo < 50:
            continue
        ref = np.sort(p[lo:hi])
        m = qpos == b
        less = np.searchsorted(ref, qp[m], "left"); le = np.searchsorted(ref, qp[m], "right")
        out[m] = (less + 0.5 * (le - less)) / len(ref)
    return out


def with_ranks(R: pd.DataFrame) -> pd.DataFrame:
    R = R.copy()
    R["rank_coin"] = np.nan; R["rank_pool"] = np.nan
    for (win, side), g in R.groupby(["window", "side"]):
        # pooled: this side's scores from every coin
        R.loc[g.index, "rank_pool"] = _trailing_rank(g["pos"].to_numpy(), g["p"].to_numpy(),
                                                     g["pos"].to_numpy(), g["p"].to_numpy())
        for coin, h in g.groupby("coin"):
            R.loc[h.index, "rank_coin"] = _trailing_rank(h["pos"].to_numpy(), h["p"].to_numpy(),
                                                         h["pos"].to_numpy(), h["p"].to_numpy())
    R = R[R["in_window"]].reset_index(drop=True)
    R["funding"] = funding_paid(R)
    # CONFIRMATION, causally: how many consecutive closes (this one included)
    # this coin and side have ranked at or above the cut. Known at the close.
    R = R.sort_values(["side", "coin", "pos"], kind="stable")
    for q in (0.90, 0.97):
        hit = (R["rank_pool"] >= q).to_numpy()
        same = ((R["pos"].diff() == 1) & (R["coin"] == R["coin"].shift())
                & (R["side"] == R["side"].shift())).to_numpy()
        run = np.zeros(len(R), int)
        for i in range(len(R)):
            run[i] = (run[i - 1] + 1 if (i and same[i] and hit[i - 1]) else 1) if hit[i] else 0
        R[f"run{int(q * 100)}"] = run
    return R.sort_values(["pos", "coin", "side"], kind="stable").reset_index(drop=True)


RULES = {
    "rank_coin>=0.90 (shipped rule)": lambda d: d["rank_coin"] >= 0.90,
    "rank_pool>=0.90": lambda d: d["rank_pool"] >= 0.90,
    "rank_pool>=0.97": lambda d: d["rank_pool"] >= 0.97,
}


def best_per_bar(d: pd.DataFrame, cut=0.90) -> pd.Series:
    """The single highest-ranked of every coin and side at each close."""
    top = d.sort_values("rank_pool", ascending=False).groupby("pos").head(1).index
    m = pd.Series(False, index=d.index); m.loc[top] = True
    return m & (d["rank_pool"] >= cut)


# ------------------------------------------------------------------ scoring
def per_trade(d: pd.DataFrame, m) -> dict:
    x = d[m]
    if x.empty:
        return {"n": 0}
    net = x["pnl"].to_numpy() - COST - x["funding"].to_numpy(); w = x["w"].to_numpy()
    # THE PLAIN MEAN. Every book in this directory before this one averaged
    # P&L with the label's uniqueness weight. That weight belongs in
    # TRAINING -- it stops overlapping labels counting as independent -- but
    # it depends on how fast the label resolved, which depends on how it
    # resolved, so as a P&L weight it leans toward the winners. Measured on
    # pooled 4h: weighted +0.564%, plain +0.273%. A trade costs the same fee
    # and risk whatever its weight, so the plain mean is the honest one;
    # the weighted one is kept only for comparison with older numbers.
    mean = float(net.mean())
    w_mean = float(np.average(net, weights=w))
    fund = float(x["funding"].mean())
    w = np.ones(len(net))
    # honest error: resample whole calendar WEEKS, which keeps the overlap
    # between consecutive bars' trades inside a block instead of pretending
    # every selected bar is an independent draw
    wk = x["t"].dt.to_period("W").astype(str).to_numpy()
    uw = np.unique(wk); rng = np.random.default_rng(0)
    idx = {k: np.flatnonzero(wk == k) for k in uw}
    boots = []
    for _ in range(300):
        pick = np.concatenate([idx[k] for k in rng.choice(uw, len(uw))])
        boots.append(np.average(net[pick], weights=w[pick]))
    return {"n": int(len(x)), "mean": mean, "w_mean": w_mean, "se_week": float(np.std(boots)),
            "funding": fund, "hit": float((net > 0).mean())}


def portfolio(d: pd.DataFrame, m, k: int = 3, sizing: str = "equal") -> dict:
    """What an account would have done: at most k positions, never two on one
    coin, 1/k of equity each, enter at the close, exit when the label
    resolves, 0.10% round trip on every trade. P&L booked at the exit."""
    cand = d[m].sort_values(["pos", "rank_pool"], ascending=[True, False])
    open_ = []                       # (exit_pos, coin)
    trades = []
    for pos, g in cand.groupby("pos", sort=True):
        open_ = [(e, c) for e, c in open_ if e > pos]
        held = {c for _, c in open_}
        for _, r in g.iterrows():
            if len(open_) >= k:
                break
            if r["coin"] in held:
                continue
            ex = pos + int(r["exit_off"])
            open_.append((ex, r["coin"])); held.add(r["coin"])
            net = r["pnl"] - COST - r["funding"]
            if sizing == "risk":
                # SIZED BY THE STOP. Equal notional lets a trade with a 7%
                # stop carry seven times the risk of one with a 1% stop, so
                # the account's swings are set by whichever trades happen to
                # have the widest stops. Here every trade risks 1% of the
                # account: notional = 1% / stop distance. The result is in
                # "% of account at 1% risk per trade", which is R-multiples.
                net = net / max(r["sl"], 0.25) * k      # k cancels the /k below
            trades.append((ex, net, r["coin"], r["side"]))
    if not trades:
        return {"trades": 0}
    T = pd.DataFrame(trades, columns=["exit", "net", "coin", "side"])
    T["day"] = (EPOCH + T["exit"] * BAR).dt.floor("D")
    daily = T.groupby("day")["net"].sum() / k
    span = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(span, fill_value=0.0)
    eq = daily.cumsum()
    years = max(len(span) / 365.0, 1e-9)
    return {"trades": int(len(T)), "win": float((T["net"] > 0).mean()),
            "net_per_trade": float(T["net"].mean()),
            "return_pct_per_year": float(daily.sum() / years),
            "sharpe": float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0,
            "max_drawdown_pct": float((eq.cummax() - eq).max()),
            "longs": int((T["side"] == "long").sum()), "shorts": int((T["side"] == "short").sum())}


def summarise(name: str) -> dict:
    from sklearn.metrics import roc_auc_score
    R = with_ranks(pickle.load(open(SCORES / f"{name}_scores.pkl", "rb")))
    res = {"variant": name}
    for part, d in (("dev", R[R["window"] < DEV_WINDOWS]), ("holdout", R[R["window"] >= DEV_WINDOWS])):
        d = d.reset_index(drop=True)
        r = {"auc": {s: float(roc_auc_score(g["y"], g["p"], sample_weight=g["w"]))
                     for s, g in d.groupby("side")},
             "auc_by_window": {int(w): float(roc_auc_score(g["y"], g["p"], sample_weight=g["w"]))
                               for w, g in d.groupby("window")}}
        for rn, fn in RULES.items():
            r[rn] = per_trade(d, fn(d))
        r["best 1 of 30 per bar"] = per_trade(d, best_per_bar(d))
        r["portfolio k=3, shipped rule"] = portfolio(d, d["rank_coin"] >= 0.90)
        r["portfolio k=3, rank_pool>=0.90"] = portfolio(d, d["rank_pool"] >= 0.90)
        r["portfolio k=3, rank_pool>=0.97"] = portfolio(d, d["rank_pool"] >= 0.97)
        for n in (2, 3, 5):
            r[f"portfolio k=3, 0.97 held {n} closes"] = portfolio(d, d["run97"] >= n)
        r["portfolio k=3, 0.90 held 5 closes"] = portfolio(d, d["run90"] >= 5)
        r["portfolio k=3, rank_pool>=0.97, 1% risk"] = portfolio(d, d["rank_pool"] >= 0.97, sizing="risk")
        r["portfolio k=3, shipped rule, 1% risk"] = portfolio(d, d["rank_coin"] >= 0.90, sizing="risk")
        r["portfolio k=5, rank_pool>=0.97, 1% risk"] = portfolio(d, d["rank_pool"] >= 0.97, k=5, sizing="risk")
        res[part] = r
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}_summary.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def show(res: dict) -> None:
    print(f"\n### {res['variant']}")
    for part in ("dev", "holdout"):
        r = res[part]
        print(f"  {part:<8} AUC long {r['auc'].get('long', float('nan')):.3f} short "
              f"{r['auc'].get('short', float('nan')):.3f}   by window "
              + " ".join(f"{v:.3f}" for v in r["auc_by_window"].values()))
        for k, v in r.items():
            if k.startswith("auc"):
                continue
            if "portfolio" in k:
                if v.get("trades"):
                    print(f"    {k:<34} {v['trades']:4d} trades  win {v['win']:.0%}  "
                          f"{v['net_per_trade']:+.2f}%/trade  {v['return_pct_per_year']:+6.1f}%/yr  "
                          f"Sharpe {v['sharpe']:+.2f}  maxDD {v['max_drawdown_pct']:.1f}%")
            elif v.get("n"):
                print(f"    {k:<34} n {v['n']:5d}  net {v['mean']:+.3f}%  +-{v['se_week']:.3f} (weekly blocks)"
                      f"  hit {v['hit']:.0%}  [weighted {v['w_mean']:+.3f}%]")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "build":
        build()
    elif cmd == "build_sb":
        for b in sys.argv[2:]:
            build_stop_buffer(float(b))
    elif cmd == "run":
        for name in sys.argv[2:]:
            run(name)
            show(summarise(name))
    elif cmd == "report":
        for name in sys.argv[2:]:
            show(summarise(name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
