#!/usr/bin/env python3
"""Fit the judge for one or more timeframes.

    python3 train.py                          # every timeframe in core.TIMEFRAMES
    python3 train.py --intervals 15m,4h
    python3 train.py --symbol ETHUSDT --intervals 1h
    python3 train.py --intervals 1d --no-seed # use whatever is already stored

Each timeframe gets TWO models, h1 and h2, because the monitor asks a
different question with one bar left than with two. Both use symmetric
barriers so "chance down" is honestly 1 - "chance up".

WHY THE SUMMARY AT THE END MATTERS MORE THAN ANY SINGLE ROW
-----------------------------------------------------------
Training six timeframes and keeping the best one is the multiple-testing
trap the plan warned about in as many words: run enough variants and the
best is noise. Six timeframes x two horizons is twelve fits, so the expected
best out-of-fold AUC is meaningfully above 0.50 even when nothing predicts
anything.

So this prints every timeframe together and refuses to rank them. A timeframe
is worth trusting when its FOLDS are stable and its shuffle test is clean —
not when its mean AUC happens to be the highest of twelve.

WHAT IT DOES NOT TRAIN
----------------------
Agent 3. News is one story per hours or days; against 175,000 one-minute bars
those columns are ~100% NaN and would burn three feature slots to say nothing.
`Monitor` already adapts to whichever columns a model carries, so a model
without news simply never asks for it.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import config as project_config
from core import (TIMEFRAMES, barriers_for, history_start, htf_for,
                  model_paths)


@dataclass
class Outcome:
    symbol: str
    interval: str
    horizon: int
    ok: bool
    bars: int = 0
    samples: int = 0
    effective_n: float = 0.0
    auc: float = float("nan")
    folds: str = ""
    spread: float = float("nan")
    shuffle: float = float("nan")
    overfit: float = float("nan")
    seconds: float = 0.0
    note: str = ""


def ensure_bars(symbol: str, interval: str, seed: bool) -> "object":
    """Make sure the store covers `history_start(interval)`.

    Skips the download when it already does. `seed_store` dedupes on write,
    but it still fetches every monthly archive first — eighty files for daily
    bars back to 2019 — so re-seeding an already-complete store costs minutes
    and achieves nothing.
    """
    from livefeed import BarStore, seed_store

    store = BarStore(symbol, interval)
    if not seed:
        return store

    start = history_start(interval)

    # A MARKER, not a coverage check.
    #
    # The obvious test — "does the store already reach `start`?" — never
    # passes for early dates, because USD-M futures did not exist yet. Asking
    # for 2019-01-01 on BTCUSDT 1d returns bars from 2019-12-31, so a coverage
    # check sees a gap that can never be filled and re-downloads eighty
    # monthly archives on every run, forever.
    #
    # Recording what was actually REQUESTED sidesteps that: the question is
    # "have we already asked for this history?", not "did the exchange have
    # it?".
    marker = store.dir / ".seeded_from"
    if marker.exists() and marker.read_text().strip() <= start:
        have = store.count()
        print(f"  already seeded from {marker.read_text().strip()} "
              f"({have:,} bars); skipping download", flush=True)
        return store

    print(f"  seeding {symbol} {interval} from {start} "
          f"(store holds {store.count():,})...", flush=True)
    try:
        written = seed_store(symbol, interval, start=start, store=store)
        marker.write_text(start)
        print(f"  seeded {written:,} new bars", flush=True)
    except Exception as e:
        print(f"  seed failed ({e}); using what is stored", flush=True)
    return store


def compute_frames(bars, htf: str, symbol: str, interval: str):
    """Detector features for these bars. Computed ONCE per timeframe.

    Agent 1 is ~370 microseconds a bar, so 175,000 one-minute bars is a
    minute of pure Python. Both horizons share the same features — only the
    label differs — so computing them per horizon would double that for
    nothing.
    """
    from agent1 import Agent1Config, PatternAgent
    from agent2 import Agent2Config, IndicatorAgent
    from agent4 import FlowAgent

    # the higher timeframe scales with the base — a fixed 4h is four bars up
    # from 1h and two hundred and forty bars up from 1m
    agents = (("agent1", PatternAgent(Agent1Config(htf_rule=htf))),
              ("agent2", IndicatorAgent(Agent2Config(htf_rule=htf))),
              ("agent4", FlowAgent()))

    # Agent 4 was measured producing 20 all-NaN columns out of 22 because
    # nothing ever passed it the tape or the derivatives feeds. Its
    # `compute()` has always accepted them; they were simply never supplied,
    # so a quarter of the judge's feature space was empty.
    #
    # Assembled through `flow_inputs` so training and serving read the same
    # sources in the same shape — see that module on why that is not
    # optional.
    from marketdata.flow_inputs import describe, flow_inputs

    extra = flow_inputs(symbol, interval, bars, backfill=True)
    print(f"  flow inputs: {describe(extra)}")

    frames, warm = {}, []
    for key, agent in agents:
        need = getattr(agent, "required_bars", lambda _b: agent.warmup_bars)(bars)
        if len(bars) <= need:
            return None, 0, f"only {len(bars):,} bars; {key} needs {need:,}"
        if key == "agent4":
            frames[key] = agent.compute(bars, **extra)
        else:
            frames[key] = agent.compute(bars)
        warm.append(agent.warmup_bars)
    return frames, max(warm), ""


def _cost_for(symbol: str) -> dict:
    """Round-trip cost, which is not the same in both markets.

    The default 0.10% is a crypto perpetual: taker fees both sides plus
    slippage. US equities at a commission-free broker are cheaper — the cost
    is the spread, which on a liquid large cap is a couple of basis points
    each way.

    This is not a detail. `p_needed = 0.5 + (threshold + cost) / (2 * span)`,
    so the cost sets how confident the model has to be before it says anything
    at all. Training a stock at crypto costs would silently demand an accuracy
    no equity model reaches, and every timeframe would read FLAT forever —
    which looks exactly like a model that learned nothing.

    Deliberately NOT optimistic: 0.05% assumes a liquid name. A thinly traded
    small cap costs far more than this, and its model will therefore promise
    more than it can deliver. That is a limitation of one number standing in
    for a whole market, and it is on the model card.
    """
    return {} if symbol.upper().endswith(("USDT", "USD", "BUSD")) else {
        "round_trip_cost_pct": 0.05,
    }


def train_one(symbol: str, interval: str, slot: int, hold: int, k: float,
              bars, frames, warm: int, save: bool = True) -> Outcome:
    from agent5 import Agent5Config, JudgeAgent

    t0 = time.time()
    out = Outcome(symbol, interval, slot, ok=False, bars=len(bars))
    # symmetric, so "chance down" is honestly 1 - "chance up"
    cfg = Agent5Config(max_hold_bars=hold, k_up=k, k_dn=k,
                       **_cost_for(symbol))
    judge = JudgeAgent(cfg)
    ds = judge.build(bars, warmup=warm, **frames)
    report = judge.fit(ds, with_shuffle=True, with_ablation=False,
                       with_importance=False)

    ev = report.evaluation
    out.ok = True
    out.samples, out.effective_n = ev.n_samples, ev.effective_n
    out.auc, out.spread = ev.auc, ev.auc_spread
    out.folds = " ".join(f"{f:.3f}" for f in ev.auc_folds)
    out.shuffle = ev.shuffle_auc if ev.shuffle_auc is not None else float("nan")
    out.overfit = ev.overfit_gap
    out.seconds = time.time() - t0

    if save:
        h1, h2 = model_paths(symbol, interval)
        judge.save(h1 if slot == 1 else h2)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fit the judge per timeframe.")
    p.add_argument("--symbol", default=project_config.SYMBOL)
    p.add_argument("--intervals", default=",".join(TIMEFRAMES),
                   help="comma separated, e.g. 15m,4h")
    p.add_argument("--no-seed", action="store_true",
                   help="do not download history; use the live store as-is")
    p.add_argument("--dry-run", action="store_true",
                   help="fit and report, but write no model files")
    a = p.parse_args(argv)

    intervals = [s.strip() for s in a.intervals.split(",") if s.strip()]
    results: List[Outcome] = []

    for interval in intervals:
        htf = htf_for(interval)
        print(f"\n{'=' * 70}\n{a.symbol} {interval}   htf {htf}\n{'=' * 70}",
              flush=True)
        store = ensure_bars(a.symbol, interval, seed=not a.no_seed)
        bars = store.load()
        if bars.empty:
            print("  no bars; skipping")
            for h in (1, 2):
                results.append(Outcome(a.symbol, interval, h, ok=False,
                                       note="no bars"))
            continue
        print(f"  {len(bars):,} bars  {bars.index[0]:%Y-%m-%d} -> "
              f"{bars.index[-1]:%Y-%m-%d}", flush=True)

        t0 = time.time()
        frames, warm, why = compute_frames(bars, htf, a.symbol, interval)
        if frames is None:
            print(f"  SKIPPED - {why}", flush=True)
            for h in (1, 2):
                results.append(Outcome(a.symbol, interval, h, ok=False, note=why))
            continue
        print(f"  features computed in {time.time() - t0:.0f}s "
              f"({sum(f.shape[1] for f in frames.values())} detector columns)",
              flush=True)

        k, hold1, hold2 = barriers_for(interval)
        print(f"  barriers +/-{k:g} ATR   holds {hold1}/{hold2} bars", flush=True)
        for slot, hold in ((1, hold1), (2, hold2)):
            try:
                r = train_one(a.symbol, interval, slot, hold, k, bars, frames,
                              warm, save=not a.dry_run)
            except Exception as e:
                traceback.print_exc()
                r = Outcome(a.symbol, interval, slot, ok=False, note=str(e)[:60])
            results.append(r)
            if r.ok:
                print(f"  h{slot} ({hold} bars): AUC {r.auc:.3f}  folds [{r.folds}]  "
                      f"spread {r.spread:.3f}  shuffle {r.shuffle:.3f}  "
                      f"eff-n {r.effective_n:,.0f}  ({r.seconds:.0f}s)",
                      flush=True)
            else:
                print(f"  h{slot}: SKIPPED - {r.note}", flush=True)

    _summary(results)
    return 0


def _summary(results: List[Outcome]) -> None:
    print("\n" + "=" * 78)
    print("ALL TIMEFRAMES")
    print("=" * 78)
    print(f"{'tf':>4} {'h':>2} {'bars':>9} {'eff-n':>7} {'AUC':>6} "
          f"{'spread':>7} {'shuffle':>8} {'overfit':>8}  folds")
    for r in results:
        if not r.ok:
            print(f"{r.interval:>4} h{r.horizon}  {'-':>8} {'-':>7} "
                  f"{'-':>6} {'-':>7} {'-':>8} {'-':>8}  {r.note}")
            continue
        print(f"{r.interval:>4} h{r.horizon} {r.bars:>9,} {r.effective_n:>7,.0f} "
              f"{r.auc:>6.3f} {r.spread:>7.3f} {r.shuffle:>8.3f} "
              f"{r.overfit:>+8.3f}  [{r.folds}]")

    good = [r for r in results if r.ok]
    if not good:
        return
    print("\nHOW TO READ THIS, AND HOW NOT TO")
    print("  Do NOT pick the highest AUC. Twelve fits means the best one is")
    print("  above 0.50 by construction even when nothing predicts anything —")
    print("  that is the multiple-testing trap, and it is why this table is")
    print("  printed whole rather than sorted.")
    print("  A timeframe earns trust from STABLE FOLDS and a CLEAN SHUFFLE")
    print("  (~0.50), not from its mean. A spread wider than the distance")
    print("  from 0.50 means the mean is noise.")
    near = [r for r in good if abs(r.auc - 0.5) <= r.spread]
    if near:
        tfs = sorted({r.interval for r in near})
        print(f"\n  at chance once fold spread is accounted for: {', '.join(tfs)}")
    dirty = [r for r in good if r.shuffle == r.shuffle and abs(r.shuffle - 0.5) > 0.05]
    if dirty:
        print(f"\n  SHUFFLE TEST DIRTY (possible leakage): "
              f"{', '.join(f'{r.interval} h{r.horizon}' for r in dirty)}")


if __name__ == "__main__":
    raise SystemExit(main())
