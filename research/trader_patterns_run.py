"""Run the trader-pattern analysis and write the report.

    python research/trader_patterns_run.py [--fills DIR] [--frames DIR]
        [--news FILE] [--out research/results/trader_patterns.md]

See research/trader_patterns.py for what each section measures.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agent5 import Agent5Config                                   # noqa: E402
from agent5.dataset import build_dataset                          # noqa: E402
from agent5.labels import LabelResult, _atr                       # noqa: E402
from agent5.metrics import shuffle_test                           # noqa: E402
from agent5.model import fit_cv, grouped_importance               # noqa: E402
from core import beats_shuffle                                    # noqa: E402
from livefeed.store import BarStore                               # noqa: E402
from research.structure_levels import structural_levels           # noqa: E402
from research.trader_patterns import (compare, entry_in_bar, excursions,  # noqa: E402
                                      forward_returns, hourly_context,
                                      load_trades, news_around,
                                      position_trades, profile)

CONTEXT_COLS = ["rsi14", "ema20_dist_atr", "ema50_dist_atr", "trend_20_50",
                "ret_1h_pct", "ret_4h_pct", "ret_24h_pct", "ret_7d_pct",
                "range24_pos", "pdh_dist_atr", "pdl_dist_atr", "res_dist_atr",
                "sup_dist_atr", "rv_pctile", "vol_z", "swept_low_24",
                "swept_high_24", "bar_pos", "atr_pct"]


def md_table(df: pd.DataFrame, floatfmt: str = "{:.2f}") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                cells.append("" if np.isnan(v) else floatfmt.format(v))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fills", default="data_cache/research_frames/deep_fills")
    ap.add_argument("--frames", default="data_cache/research_frames")
    ap.add_argument("--news", default="data_cache/research_frames/news/items.jsonl")
    ap.add_argument("--leaderboard", default="data_cache/hyperliquid/leaderboard.json")
    ap.add_argument("--out", default="research/results/trader_patterns.md")
    a = ap.parse_args()
    R: list = []                                    # report lines
    J: dict = {}                                    # machine-readable

    # ------------------------------------------------------------ 1. shape
    df = load_trades(Path(a.fills))
    pos = position_trades(df)
    pr = profile(df)
    # the leaderboard's 30-day number beside the year's
    from marketdata.hyperliquid import iter_leaderboard, performance
    lb = {}
    for row in iter_leaderboard(Path(a.leaderboard)):
        addr = str(row.get("ethAddress", "")).lower()
        if addr in set(pr["address"]):
            lb[addr] = performance(row, "month")["pnl"]
    pr["pnl_30d_board"] = pr["address"].map(lb)
    pr["profitable_year"] = pr["pnl_total"] > 0
    pr = pr.sort_values("pnl_total", ascending=False)
    n_neg = int((~pr["profitable_year"]).sum())
    R += ["# What the best public traders actually do",
          "",
          f"Source: a year of fills for {df['address'].nunique()} Hyperliquid addresses that were in the "
          f"top 400 by 30-day profit on 2026-09-16 and whose fills looked discretionary (not a market "
          f"maker's). {len(df):,} round-trip episodes; **{len(pos):,} position trades** (held ≥30 min, "
          f"≥$5k). Bars, levels and features are ours (Binance USDT perps).",
          "",
          "## 1. The leaderboard is a 30-day window on a year",
          "",
          f"Of {len(pr)} traders selected for a great *month*, **{n_neg} are net losers on their "
          f"position trades over the *year*** (worst: ${pr['pnl_total'].min()/1e6:+.1f}M). For the median "
          f"trader, the top 5 trades are **{100*pr['pnl_top5_share'].median():.0f}%** of gross profit. "
          f"Median {pr['liquidations'].median():.0f} liquidations per trader. A leaderboard ranks variance.",
          "",
          md_table(pr.assign(addr=pr["address"].str[:10])[["addr", "position_trades", "maker_share", "median_hold_h",
                    "long_share", "win_rate", "avg_win_pct", "avg_loss_pct", "payoff_ratio", "pnl_total",
                    "pnl_30d_board", "pnl_top5_share", "liquidations", "coins"]].round(2)),
          ""]
    J["profiles"] = pr.to_dict(orient="records")

    good = set(pr.loc[pr["profitable_year"] & (pr["position_trades"] >= 10), "address"])
    bad = set(pr.loc[~pr["profitable_year"] & (pr["position_trades"] >= 10), "address"])
    pos["cls"] = np.where(pos["address"].isin(good), "profitable", np.where(pos["address"].isin(bad), "losing", "thin"))
    shape = pos[pos["cls"] != "thin"].groupby("cls").agg(
        trades=("pnl", "size"), traders=("address", "nunique"),
        median_hold_h=("hold_hours", "median"), p75_hold_h=("hold_hours", lambda s: s.quantile(0.75)),
        long_share=("side", lambda s: (s == "LONG").mean()), win_rate=("pnl", lambda s: (s > 0).mean()),
        avg_win=("ret_pct", lambda s: s[s > 0].mean()), avg_loss=("ret_pct", lambda s: s[s <= 0].mean()),
        maker_entry=("maker_entry", "mean"), maker_exit=("maker_exit", "mean"),
        entry_clips=("n_entry", "median"), exit_clips=("n_exit", "median"),
        scale_in_h=("scale_in_hours", "median"), flipped_in=("flipped_in", "mean"),
    ).reset_index()
    R += ["## 2. Shape of a trade: profitable traders vs losing ones",
          "",
          "Same pool, split by whether the year's position trades made money (≥10 trades each).",
          "",
          md_table(shape.round(2)), ""]
    J["shape"] = shape.to_dict(orient="records")

    # --------------------------------------------- bars, levels, context
    syms = sorted({s for s in pos["symbol"].unique() if s})
    frames_dir = Path(a.frames)
    ctx: dict = {}; bars15: dict = {}; bars1h: dict = {}
    for sym in syms:
        fp = frames_dir / f"{sym}_1h_frames.pkl"
        if not fp.exists():
            continue
        b1h = pickle.load(open(fp, "rb"))[0]
        b1h = b1h[b1h.index >= pd.Timestamp("2025-06-01", tz="UTC")]
        atr = _atr(b1h, 14).to_numpy(float)
        lv = structural_levels(b1h, atr)
        ctx[sym] = hourly_context(b1h, lv)
        bars1h[sym] = b1h
        b15 = BarStore(sym, "15m").load(derived=False)
        bars15[sym] = b15[b15.index >= pd.Timestamp("2025-06-01", tz="UTC")]
    covered = pos[pos["symbol"].isin(ctx)].copy()
    R += [f"Coins with our bars and features: {', '.join(sorted(ctx))} — "
          f"**{len(covered):,}** of the {len(pos):,} position trades "
          f"({len(covered[covered['cls']=='profitable']):,} by profitable traders).", ""]

    # per-trade enrichment
    rows = []
    news = []
    try:
        for line in open(a.news):
            it = json.loads(line)
            ts = it.get("published_at")
            if ts:
                it["_ts"] = pd.Timestamp(ts)
                if it["_ts"].tzinfo is None:
                    it["_ts"] = it["_ts"].tz_localize("UTC")
                news.append(it)
    except OSError:
        pass
    news_span = (min(n["_ts"] for n in news), max(n["_ts"] for n in news)) if news else None
    for _, t in covered.iterrows():
        sym = t["symbol"]
        c = ctx[sym]
        i = c.index.searchsorted(t["t_open"]) - 1           # the bar closed BEFORE entry
        if i < 0:
            continue
        row = {"address": t["address"], "symbol": sym, "side": t["side"], "cls": t["cls"],
               "t_open": t["t_open"], "hold_hours": t["hold_hours"], "ret_pct": t["ret_pct"],
               "pnl": t["pnl"], "maker_entry": t["maker_entry"], "n_entry": t["n_entry"],
               "hour": t["t_open"].hour, "weekday": t["t_open"].dayofweek}
        row.update(c.iloc[i].to_dict())
        row.update(excursions(bars15[sym], t))
        row.update(entry_in_bar(bars1h[sym], t))
        row.update(forward_returns(bars15[sym], t))
        if news_span and news_span[0] <= t["t_open"] <= news_span[1]:
            row["news_6h"] = news_around(news, sym, t["t_open"])
        rows.append(row)
    E = pd.DataFrame(rows)
    J["n_enriched"] = int(len(E))

    # ---------------------------------------------------- 3. where they enter
    R += ["## 3. Where they enter, versus where the market is",
          "",
          "Each feature at the 1h bar closed before the entry, against the same feature over every 1h bar "
          "of that coin in the period. `z` is a rank-sum statistic: |z|>3 is a habit, |z|<2 is noise. "
          "`share_top_q` / `share_bottom_q`: fraction of entries in the market's top / bottom quartile "
          "(0.25 = no preference). Signs are from the trade's point of view for the directional "
          "features (a short's `ret_24h` is negated), so 'buying dips' and 'shorting rips' read alike.",
          ""]
    dirn = ["ret_1h_pct", "ret_4h_pct", "ret_24h_pct", "ret_7d_pct", "ema20_dist_atr", "ema50_dist_atr", "trend_20_50"]
    for cls in ("profitable", "losing"):
        for side in ("LONG", "SHORT"):
            e = E[(E["cls"] == cls) & (E["side"] == side)].copy()
            if len(e) < 30:
                continue
            base = pd.concat([ctx[s] for s in e["symbol"].unique()])
            if side == "SHORT":
                for cc in dirn:
                    e[cc] = -e[cc]; base = base.assign(**{cc: -base[cc]})
                e["range24_pos"] = 1 - e["range24_pos"]; base = base.assign(range24_pos=1 - base["range24_pos"])
                e["pdh_dist_atr"], e["pdl_dist_atr"] = e["pdl_dist_atr"], e["pdh_dist_atr"]
                base = base.assign(pdh_dist_atr=base["pdl_dist_atr"], pdl_dist_atr=base["pdh_dist_atr"])
                e["res_dist_atr"], e["sup_dist_atr"] = e["sup_dist_atr"], e["res_dist_atr"]
                base = base.assign(res_dist_atr=base["sup_dist_atr"], sup_dist_atr=base["res_dist_atr"])
                e["swept_low_24"], e["swept_high_24"] = e["swept_high_24"], e["swept_low_24"]
                base = base.assign(swept_low_24=base["swept_high_24"], swept_high_24=base["swept_low_24"])
                e["bar_pos"] = 1 - e["bar_pos"]; base = base.assign(bar_pos=1 - base["bar_pos"])
            cmp = compare(e, base, CONTEXT_COLS)
            R += [f"### {cls} traders, {side} entries (n={len(e)}, {e['address'].nunique()} traders)", "",
                  "(directional features oriented with the trade: `ret_*` and `ema_*` positive = price had "
                  "moved in the trade's favour before entry; `res_dist` = distance to the level ahead, "
                  "`sup_dist` = to the level behind)", "",
                  md_table(cmp.head(12).round(3)), ""]
            J[f"context_{cls}_{side}"] = cmp.to_dict(orient="records")

    # ------------------------------------------------------- 4. when
    R += ["## 4. When they enter", ""]
    for cls in ("profitable", "losing"):
        e = E[E["cls"] == cls]
        if len(e) < 30:
            continue
        hrs = e["hour"].value_counts(normalize=True).sort_index()
        wd = e["weekday"].value_counts(normalize=True).sort_index()
        # a uniform clock is the baseline; the excess over 1/24 in points
        top_h = hrs.sort_values(ascending=False).head(4)
        R += [f"**{cls}** (n={len(e)}): busiest hours UTC " +
              ", ".join(f"{h:02d}:00 ({100*v:.0f}%, vs 4.2% uniform)" for h, v in top_h.items()) +
              "; quietest " + ", ".join(f"{h:02d}:00 ({100*v:.1f}%)" for h, v in hrs.sort_values().head(3).items()) + ".",
              "Weekdays Mon..Sun: " + " / ".join(f"{100*wd.get(d, 0):.0f}%" for d in range(7)) + ".", ""]
        J[f"hours_{cls}"] = {int(k): float(v) for k, v in hrs.items()}
        J[f"weekday_{cls}"] = {int(k): float(v) for k, v in wd.items()}

    # ------------------------------------------------------- 5. exits
    R += ["## 5. Where they get out: the stop and the target, read off the book",
          "",
          "MFE = the best price reached during the hold, MAE = the worst, both in % of entry from the "
          "trade's side. A trader's *effective stop* is where the losers' MAE clusters; the *effective "
          "target* is the winners' realised return; `exit_of_mfe` is how much of the best price they kept.",
          ""]
    ex = []
    for cls in ("profitable", "losing"):
        e = E[(E["cls"] == cls) & E["mfe_pct"].notna()]
        if len(e) < 30:
            continue
        w = e[e["ret_pct"] > 0]; l = e[e["ret_pct"] <= 0]
        ex.append({"cls": cls, "n": len(e),
                   "winners_ret_med": w["ret_pct"].median(), "winners_ret_p75": w["ret_pct"].quantile(0.75),
                   "winners_mfe_med": w["mfe_pct"].median(), "winners_kept_of_mfe": w["exit_of_mfe"].median(),
                   "winners_mae_med": w["mae_pct"].median(),
                   "losers_ret_med": l["ret_pct"].median(), "losers_mae_med": l["mae_pct"].median(),
                   "losers_mae_p25": l["mae_pct"].quantile(0.25), "losers_mfe_med": l["mfe_pct"].median(),
                   "hold_med_h": e["hold_hours"].median(),
                   "entry_bar_pos_med": e["entry_bar_pos"].median(),
                   "entry_vs_prev_close_med": e["entry_vs_prev_close_pct"].median()})
    exd = pd.DataFrame(ex)
    R += [md_table(exd.round(2)), "",
          "Reading `entry_bar_pos`: 0 = the entry printed at the hour's low (a long) / high (a short), 1 = the "
          "worst price of the hour. `entry_vs_prev_close`: how much better than a market order at the "
          "previous bar's close (positive = better).", ""]
    J["exits"] = exd.to_dict(orient="records")
    # stop/target in ATR terms, per class
    for cls in ("profitable", "losing"):
        e = E[(E["cls"] == cls) & E["mfe_pct"].notna() & (E["atr_pct"] > 0)]
        if len(e) < 30:
            continue
        l = e[e["ret_pct"] <= 0]; w = e[e["ret_pct"] > 0]
        R += [f"**{cls}** in ATR of the 1h bar: losers' MAE median {-(l['mae_pct']/l['atr_pct']).median():.1f} ATR "
              f"(p25 {-(l['mae_pct']/l['atr_pct']).quantile(0.25):.1f}), realised loss median "
              f"{-(l['ret_pct']/l['atr_pct']).median():.1f} ATR; winners' realised gain median "
              f"{(w['ret_pct']/w['atr_pct']).median():.1f} ATR, MFE median {(w['mfe_pct']/w['atr_pct']).median():.1f} ATR.", ""]

    # ------------------------------------------------------- 6. news
    if "news_6h" in E:
        en = E[E["news_6h"].notna()]
        if len(en) >= 30 and news:
            # baseline: headlines per 7h window for the same coins, over the news span
            nd = pd.Series([n["_ts"] for n in news])
            span_h = (news_span[1] - news_span[0]).total_seconds() / 3600
            base_rate = 7.0 * len(nd) / span_h
            R += ["## 6. News around entries", "",
                  f"Headlines in the 6h before + 1h after an entry (our feed, {len(news)} items, "
                  f"{news_span[0].date()} → {news_span[1].date()}, matched by asset tag or untagged): "
                  f"entries by profitable traders {en[en['cls']=='profitable']['news_6h'].mean():.2f} per window, "
                  f"losing {en[en['cls']=='losing']['news_6h'].mean():.2f}, against **{base_rate:.2f}** for a "
                  f"random 7h window of the whole feed. Share of entries with any headline in the window: "
                  f"profitable {100*(en[en['cls']=='profitable']['news_6h']>0).mean():.0f}%, losing "
                  f"{100*(en[en['cls']=='losing']['news_6h']>0).mean():.0f}%.", ""]
            J["news"] = {"base_rate": base_rate, "profitable": float(en[en['cls']=='profitable']['news_6h'].mean()),
                         "losing": float(en[en['cls']=='losing']['news_6h'].mean())}

    # ------------------------------------------------------ 7. following
    R += ["## 7. What happens after they enter (a follower's view)",
          "",
          "Return in the trade's direction from the entry VWAP at 1h / 4h / 24h, before fees. Split by "
          "class and by how the entry was made (resting order vs taker).", ""]
    fw = []
    for cls in ("profitable", "losing"):
        for kind, m in (("resting ≥50%", E["maker_entry"] >= 0.5), ("taker", E["maker_entry"] < 0.5)):
            e = E[(E["cls"] == cls) & m]
            if len(e) < 30:
                continue
            r = {"cls": cls, "entry": kind, "n": len(e)}
            for h in (1, 4, 24):
                x = e[f"fwd_{h}h_pct"].dropna()
                r[f"fwd_{h}h"] = x.mean(); r[f"se_{h}h"] = x.std() / np.sqrt(len(x))
            fw.append(r)
    fwd = pd.DataFrame(fw)
    R += [md_table(fwd.round(3)), ""]
    J["forward"] = fwd.to_dict(orient="records")

    # 7b. the follower's price, not the trader's fill
    R += ["### 7b. From the price a follower would pay",
          "",
          "The same trades, measured from the first 15m close AFTER the entry printed -- what a copier "
          "gets, having seen the fill. `slip` = how much better the trader's own fill was than that close.", ""]
    fw2 = []
    for cls in ("profitable", "losing"):
        for kind, m in (("resting ≥50%", E["maker_entry"] >= 0.5), ("taker", E["maker_entry"] < 0.5)):
            e = E[(E["cls"] == cls) & m]
            if len(e) < 30:
                continue
            r = {"cls": cls, "entry": kind, "n": len(e), "slip": e["follower_slip_pct"].mean()}
            for h in (1, 4, 24):
                x = e[f"fol_{h}h_pct"].dropna()
                r[f"fol_{h}h"] = x.mean(); r[f"se_{h}h"] = x.std() / np.sqrt(len(x))
            fw2.append(r)
    fwd2 = pd.DataFrame(fw2)
    R += [md_table(fwd2.round(3)), ""]
    J["forward_follower"] = fwd2.to_dict(orient="records")

    # 7c. out of sample: who is "profitable" decided on the first half only
    mid = E["t_open"].min() + (E["t_open"].max() - E["t_open"].min()) / 2
    first = pos[pos["t_close"] < mid].groupby("address")["pnl"].agg(["sum", "size"])
    good_oos = set(first[(first["sum"] > 0) & (first["size"] >= 5)].index)
    bad_oos = set(first[(first["sum"] <= 0) & (first["size"] >= 5)].index)
    E2 = E[E["t_open"] >= mid].copy()
    E2["cls2"] = np.where(E2["address"].isin(good_oos), "profitable(H1)",
                          np.where(E2["address"].isin(bad_oos), "losing(H1)", "thin"))
    R += ["### 7c. Out of sample: class decided on the first half-year, trades from the second",
          "",
          f"Traders with ≥5 position trades before {mid.date()} are labelled by that half's P&L; only entries "
          f"after it are scored. This removes the trade's own outcome from its trader's label.", ""]
    fw3 = []
    for cls in ("profitable(H1)", "losing(H1)"):
        for kind, m in (("resting ≥50%", E2["maker_entry"] >= 0.5), ("taker", E2["maker_entry"] < 0.5)):
            e = E2[(E2["cls2"] == cls) & m]
            if len(e) < 20:
                continue
            r = {"cls": cls, "entry": kind, "n": len(e), "traders": e["address"].nunique(),
                 "win_rate": float((e["ret_pct"] > 0).mean()), "ret_med": e["ret_pct"].median()}
            for h in (1, 4, 24):
                x = e[f"fwd_{h}h_pct"].dropna(); xf = e[f"fol_{h}h_pct"].dropna()
                r[f"fwd_{h}h"] = x.mean(); r[f"fol_{h}h"] = xf.mean(); r[f"fol_se_{h}h"] = xf.std() / np.sqrt(max(1, len(xf)))
            fw3.append(r)
    fwd3 = pd.DataFrame(fw3)
    R += [md_table(fwd3.round(3)) if len(fwd3) else "(too few)", ""]
    J["forward_oos"] = fwd3.to_dict(orient="records")

    # --------------------------------------------- 8. can we predict them?
    R += ["## 8. Can our features predict WHEN they enter?",
          "",
          "For every 1h bar of every covered coin: label 1 if a *profitable* trader opened a position trade "
          "in the next hour (long model / short model separately). Fitted with the project's own purged CV "
          "on agent1/agent2/agent4/regime features, shuffled-label control. If this clears, their entry "
          "rule is a function of the chart and can be written down.", ""]
    pred = {}
    for side in ("LONG", "SHORT"):
        ev = E[(E["cls"] == "profitable") & (E["side"] == side)]
        parts = []
        for sym in sorted(ctx):
            fp = frames_dir / f"{sym}_1h_frames.pkl"
            bars, frames, warm = pickle.load(open(fp, "rb"))
            bars = bars[bars.index >= pd.Timestamp("2025-06-01", tz="UTC")]
            frames = {k: (v[v.index >= pd.Timestamp("2025-06-01", tz="UTC")] if v is not None else None) for k, v in frames.items()}
            n = len(bars)
            y = np.zeros(n); times = ev[ev["symbol"] == sym]["t_open"]
            for t in times:
                i = bars.index.searchsorted(t) - 1
                if 0 <= i < n:
                    y[i] = 1.0
            if y.sum() < 15:
                continue
            idx = np.arange(n, dtype=float)
            lab = LabelResult(y=pd.Series(y, index=bars.index), t1=pd.Series(idx + 1, index=bars.index),
                              weight=pd.Series(1.0, index=bars.index), touch=pd.Series("timeout", index=bars.index),
                              tp_pct=pd.Series(1.0, index=bars.index), sl_pct=pd.Series(1.0, index=bars.index))
            cfg = Agent5Config(max_hold_bars=1, k_up=1.0, k_dn=1.0)
            ds = build_dataset(bars, cfg, warmup=warm, labels=lab, **frames)
            parts.append((sym, ds, int(y.sum())))
        if not parts:
            continue
        # per coin, then the pooled read
        lines = []
        for sym, ds, k in parts:
            cfg = Agent5Config(max_hold_bars=1, k_up=1.0, k_dn=1.0)
            cols = list(ds.X.columns)
            fit = fit_cv(ds, cfg, cols); sh = shuffle_test(ds, cfg, cols)
            ok = beats_shuffle(fit.mean_auc, sh, fit.auc_spread)
            imp = grouped_importance(fit, ds, cfg)
            top = ", ".join(f"{k2} {v:.2f}" for k2, v in imp.sort_values(ascending=False).head(3).items()) if len(imp) else ""
            lines.append({"side": side, "coin": sym, "entries": k, "bars": len(ds), "auc": fit.mean_auc,
                          "shuffle": sh, "spread": fit.auc_spread, "clears": ok, "top_blocks": top})
        pl = pd.DataFrame(lines)
        pred[side] = pl.to_dict(orient="records")
        R += [f"### {side} entries", "", md_table(pl.round(3)), ""]
        # 8b. the learned pattern as a rule: top-decile OOF score -> forward return
        # from the bar's close (a follower's price), on the coins that cleared
        sgn = 1.0 if side == "LONG" else -1.0
        rr = []; bb = []
        for (sym, ds, k), row in zip(parts, lines):
            if not row["clears"]:
                continue
            cfg = Agent5Config(max_hold_bars=1, k_up=1.0, k_dn=1.0)
            fit = fit_cv(ds, cfg, list(ds.X.columns))
            b = bars1h[sym]["close"].reindex(ds.index)
            fwd24 = 100 * sgn * (bars1h[sym]["close"].shift(-24).reindex(ds.index) / b - 1.0)
            ok = ~np.isnan(fit.oof)
            cut = np.nanquantile(fit.oof, 0.90)
            rr.append(fwd24[ok & (fit.oof >= cut)].dropna()); bb.append(fwd24[ok].dropna())
        if rr:
            r_ = pd.concat(rr); b_ = pd.concat(bb)
            R += [f"**As a rule ({side}):** enter when the model's out-of-fold score is in its top decile "
                  f"(the bars that look most like a profitable trader's entry), exit 24h later at the close. "
                  f"Mean {r_.mean():+.3f}% (se {r_.std()/np.sqrt(len(r_)):.3f}, n={len(r_)}) vs every bar "
                  f"{b_.mean():+.3f}% (se {b_.std()/np.sqrt(len(b_)):.3f}). Fees 0.10%.", ""]
            J[f"rule_model_{side}"] = {"mean": float(r_.mean()), "se": float(r_.std()/np.sqrt(len(r_))),
                                      "n": int(len(r_)), "base": float(b_.mean())}
    J["predictability"] = pred

    # ------------------------------------------------ 9. the pattern, tested
    R += ["## 9. The rule their entries suggest, tested as a strategy",
          "",
          "Take the three strongest habits from §3 for profitable longs, turn them into an entry rule on "
          "our 1h bars, hold for their median hold, exit at the close. Baseline: every bar. "
          "This is the honest version of 'copy their pattern' — it uses only what the chart shows.", ""]
    # habits: read from the comparison table for profitable LONG
    cmpL = pd.DataFrame(J.get("context_profitable_LONG", []))
    rule_txt = []
    if len(cmpL):
        top3 = cmpL[cmpL["z"].abs() >= 3].head(3)
        conds = []
        for _, r in top3.iterrows():
            f = r["feature"]; hi = r["entry_median"] > r["market_median"]
            # threshold at the market's median: "above the market's median" is the habit
            conds.append((f, hi))
            rule_txt.append(f"`{f}` {'above' if hi else 'below'} its market median")
        hold_h = int(max(1, round(E[(E["cls"] == "profitable") & (E["side"] == "LONG")]["hold_hours"].median())))
        rets = []; base_rets = []
        for sym, c in ctx.items():
            b = bars1h[sym]["close"]
            fwd = 100 * (b.shift(-hold_h) / b - 1.0)
            m = pd.Series(True, index=c.index)
            for f, hi in conds:
                med = c[f].median()
                m &= (c[f] > med) if hi else (c[f] < med)
            rets.append(fwd[m].dropna()); base_rets.append(fwd.dropna())
        rr = pd.concat(rets); bb = pd.concat(base_rets)
        R += [f"Rule (long): " + " AND ".join(rule_txt) + f"; hold {hold_h}h.", "",
              f"Fires on {100*len(rr)/len(bb):.1f}% of bars. Mean forward return over {hold_h}h: "
              f"**{rr.mean():+.3f}%** (se {rr.std()/np.sqrt(len(rr)):.3f}) vs every bar {bb.mean():+.3f}% "
              f"(se {bb.std()/np.sqrt(len(bb)):.3f}). Round trip cost 0.10%.", ""]
        J["rule"] = {"conds": rule_txt, "hold_h": hold_h, "fires": float(len(rr)/len(bb)),
                     "mean": float(rr.mean()), "se": float(rr.std()/np.sqrt(len(rr))),
                     "base_mean": float(bb.mean())}

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(R))
    E.drop(columns=["t_open"]).to_json(Path(a.out).with_suffix(".trades.json"), orient="records")
    Path(a.out).with_suffix(".json").write_text(json.dumps(J, indent=1, default=str))
    print("\n".join(R))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
