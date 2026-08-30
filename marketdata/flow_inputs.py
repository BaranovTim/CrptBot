"""Everything Agent 4 needs, assembled once, for training and for serving.

WHY ONE FUNCTION AND NOT TWO
    This is the whole point of the module. Agent 4's rolling baselines span
    hundreds of bars, so inference does not need the last bar's tape — it
    needs the tape across the entire live window. If training assembled its
    inputs one way and the live path another, the model would learn one
    distribution and be asked to predict on a different one, and nothing in
    the tests would notice: every column would still be present and every
    number still finite.

    Train/serve skew of that kind does not throw. It just quietly makes the
    model worse than the backtest said, which is the failure this project is
    least able to detect. So both callers go through here.

WHAT IS AVAILABLE, MEASURED 2026-08-30
    tape (aggTrades)      data.binance.vision daily archives, years of it.
                          4-54 MB per day, so it is reduced to per-bar rows
                          and the archive deleted — see `tape_store`.
    open interest         daily `metrics` archives, verified back to at least
                          2023-02. NOT the REST endpoint, which only serves
                          about 21 days and is useless for training.
    liquidations          same archive family; Binance's coverage is patchy,
                          so this is allowed to come back empty.
    exchange netflow      on-chain, and not free. Stays empty, and Agent 4's
                          `netflow_coverage_24h` column is what tells the
                          model so rather than leaving it to guess.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

log = logging.getLogger(__name__)


def flow_inputs(symbol: str, interval: str, bars: pd.DataFrame,
                backfill: bool = False,
                start: Optional[str] = None) -> Dict[str, Optional[pd.DataFrame]]:
    """The `tape`, `open_interest` and `liquidations` frames for these bars.

    `backfill=True` will download anything missing, which is a training-time
    operation measured in minutes to hours. The live path passes False and
    reads only what is already on disk, so a serving request never blocks on
    a network fetch it did not ask for.
    """
    from marketdata.tape_store import TapeStore, backfill_tape

    if bars is None or bars.empty:
        return {"tape": None, "open_interest": None, "liquidations": None}

    first = bars.index[0]
    since = str(first.date())

    store = TapeStore(symbol, interval)
    if backfill:
        try:
            added = backfill_tape(symbol, interval, bars.index,
                                  start=start or since)
            if added:
                log.info("tape %s %s: %d new bars", symbol, interval, added)
        except Exception as e:
            # A failed backfill must not stop training. Agent 4 degrades to
            # the kline fallback and the coverage column records it.
            log.warning("tape backfill %s %s failed: %s", symbol, interval, e)

    tape = store.load(since=first)
    if tape.empty:
        tape = None

    oi = liq = None
    try:
        from marketdata.derivatives import load_open_interest, resample_to_bars

        raw_oi = load_open_interest(symbol, start=start or since)
        if raw_oi is not None and not raw_oi.empty:
            oi = resample_to_bars(raw_oi, bars.index, how="last")
    except Exception as e:
        log.warning("open interest %s: %s", symbol, e)

    try:
        from marketdata.derivatives import load_liquidations, resample_to_bars

        raw_liq = load_liquidations(symbol, start=start or since)
        if raw_liq is not None and not raw_liq.empty:
            liq = resample_to_bars(raw_liq, bars.index, how="sum")
    except Exception as e:
        # Binance's liquidation archives are patchy; empty is a normal answer
        log.info("liquidations %s unavailable: %s", symbol, e)

    return {"tape": tape, "open_interest": oi, "liquidations": liq}


def describe(inputs: Dict[str, Optional[pd.DataFrame]]) -> str:
    """One line saying what actually arrived, for the training log.

    Printed rather than inferred, because "Agent 4 ran" and "Agent 4 had
    anything to work with" looked identical for the entire life of this
    project until somebody measured the columns.
    """
    bits = []
    for name in ("tape", "open_interest", "liquidations"):
        f = inputs.get(name)
        bits.append(f"{name}={len(f):,}" if f is not None and not f.empty
                    else f"{name}=none")
    return "  ".join(bits)
