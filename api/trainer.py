"""Fitting models from the app, instead of from a laptop.

WHY A QUEUE AND NOT "JUST RUN IT"
    A fit takes forty minutes and the droplet has ONE core, which the API,
    the collector and the alert engine already share. Two fits at once do not
    take forty minutes each — they take ninety, and the app times out
    throughout. So exactly one runs at a time and the rest wait in line.

WHY THE REQUEST RETURNS IMMEDIATELY
    Forty minutes is not a request. The button queues a job and the app polls
    for its state, the same shape as the screener build. A handler that
    blocked would hold a socket open past every timeout in the stack and the
    phone would report failure while the work continued invisibly.

WHAT IS REFUSED, AND WHY
    A duplicate           the same pair and timeframe already queued or
                          running. Queueing it twice costs eighty minutes to
                          produce one model.
    A full queue          past MAX_QUEUE, because a queue longer than a day
                          is not a queue, it is a way to never notice that
                          nothing is progressing.
    An unknown timeframe  before the work starts rather than forty minutes in.

THE RESULT IS KEPT, INCLUDING THE BAD ONES
    A fit that comes back at chance is the most useful thing this can tell
    anyone, and it is exactly what gets lost when only successes are recorded.
    The AUC, the fold spread and the shuffle score are parsed out of the run
    and kept against the job, so "I trained it and nothing happened" has an
    answer.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

STATE = Path("data_cache") / "train_jobs.json"

MAX_QUEUE = 8
KEEP_FINISHED = 20

# Fits one account may start in a rolling day.
#
# WHY THERE IS A CAP AT ALL. Before training could be triggered over HTTP,
# the security argument for this API was "a compromise yields what your
# terminal already prints" — every route was a read. A POST that spends forty
# minutes of a single-core server is not that: eight of them is five hours of
# CPU, and the app is unusable throughout. The queue stops them running at
# once; this stops one account from filling the day.
#
# Twelve is generous for the intended use — a person fitting a few timeframes
# for a few symbols — and useless for exhausting the box.
MAX_PER_DAY = 12
DAY = 86400.0

VALID_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")


def market_for(symbol: str, requested: str) -> str:
    """Which fitter this symbol belongs to, decided HERE and not by the caller.

    THE BUG THIS EXISTS TO KILL
        Binance lists perpetuals on things that are also equities — AVGOUSDT,
        XAUUSDT — and the crypto screener shows them. Tapping one opened the
        STOCK page, which hardcoded `market: 'stocks'`, so the request asked
        `train_stocks.py` to fit "AVGOUSDT". That went to Alpaca, which has
        never heard of a ticker called AVGOUSDT, seeded nothing, and exited 0.
        Three minutes of the user's attention to be told "finished without
        fitting anything", with no hint that the symbol had been routed to the
        wrong market entirely.

    WHY INFER RATHER THAN REFUSE
        The symbol is not ambiguous. No US equity ticker ends in USDT, so a
        symbol that does is a Binance perpetual whatever the caller believes,
        and the useful thing to do with an unambiguous request is honour it.
        Refusing would be technically defensible and practically useless — the
        user would read an error about a distinction they never made.
    """
    if (symbol or "").upper().endswith("USDT"):
        return "crypto"
    return requested or "crypto"

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"

# Parsed from the training summary table, e.g.
#   1d h1     2,511   1,077  0.511   0.017    0.499   +0.040  [...]
_ROW = re.compile(
    r"^\s*(\S+)\s+(h[12])\s+[\d,]+\s+[\d,]+\s+"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)")
_VERDICT = re.compile(r"at chance once fold spread is accounted for:\s*(.+)")


@dataclass
class Job:
    id: str
    symbol: str
    interval: str
    market: str
    state: str = QUEUED
    queued_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    note: str = ""
    # AUC / spread / shuffle per horizon slot, and the pipeline's own verdict.
    results: List[Dict[str, Any]] = field(default_factory=list)
    at_chance: bool = False

    def public(self) -> dict:
        d = asdict(self)
        if self.started_at and not self.finished_at:
            d["elapsed"] = int(time.time() - self.started_at)
        return d


class Trainer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: List[Job] = []
        self._finished: List[Job] = []
        self._current: Optional[Job] = None
        self._worker: Optional[threading.Thread] = None
        # account -> submission timestamps, for the daily cap
        self._recent: Dict[str, List[float]] = {}
        self._load()

    # ------------------------------------------------------------- public
    def submit(self, symbol: str, interval: str,
               market: str = "crypto",
               account: str = "") -> Dict[str, Any]:
        symbol = (symbol or "").upper().strip()
        interval = (interval or "").strip()
        if not symbol:
            return {"ok": False, "error": "symbol is required"}
        if interval not in VALID_INTERVALS:
            # Rejected up front, not forty minutes in.
            return {"ok": False,
                    "error": f"unknown timeframe {interval!r}; expected one "
                             f"of {', '.join(VALID_INTERVALS)}"}

        with self._lock:
            for j in self._queue + ([self._current] if self._current else []):
                if j.symbol == symbol and j.interval == interval:
                    return {"ok": False, "queued": True, "job": j.public(),
                            "error": f"{symbol} {interval} is already "
                                     f"{j.state}"}
            if len(self._queue) >= MAX_QUEUE:
                return {"ok": False,
                        "error": f"the queue is full ({MAX_QUEUE}). Each fit "
                                 f"takes about forty minutes on this server."}

            cutoff = time.time() - DAY
            today = [t for t in self._recent.get(account, []) if t > cutoff]
            if len(today) >= MAX_PER_DAY:
                return {"ok": False,
                        "error": f"that is {MAX_PER_DAY} fits started in a "
                                 f"day, which is all this server has room "
                                 f"for. Try again tomorrow."}
            today.append(time.time())
            self._recent[account] = today

            resolved = market_for(symbol, market)
            if resolved != market:
                log.info("trainer: %s requested as %s, fitting as %s",
                         symbol, market, resolved)
            job = Job(id=uuid.uuid4().hex[:10], symbol=symbol,
                      interval=interval, market=resolved)
            self._queue.append(job)
            self._save()
        self._ensure_worker()
        return {"ok": True, "job": job.public(),
                "position": self.position(job.id)}

    def position(self, job_id: str) -> int:
        with self._lock:
            for i, j in enumerate(self._queue):
                if j.id == job_id:
                    return i + 1
        return 0

    def status(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            cur = self._current.public() if self._current else None
            queued = [j.public() for j in self._queue]
            finished = [j.public() for j in reversed(self._finished)]
        if symbol:
            sym = symbol.upper()
            queued = [j for j in queued if j["symbol"] == sym]
            finished = [j for j in finished if j["symbol"] == sym]
            if cur and cur["symbol"] != sym:
                cur = None
        return {
            "current": cur,
            "queued": queued,
            "finished": finished[:KEEP_FINISHED],
            # So the app can say "about 80 minutes" rather than "soon".
            "minutes_per_fit": 40,
        }

    def cancel(self, job_id: str) -> Dict[str, Any]:
        """Queued jobs only. A running fit is left alone deliberately —
        killing it halfway wastes the CPU already spent and can leave a
        half-written model file behind."""
        with self._lock:
            for i, j in enumerate(self._queue):
                if j.id == job_id:
                    self._queue.pop(i)
                    j.state = FAILED
                    j.note = "cancelled"
                    j.finished_at = time.time()
                    self._finished.append(j)
                    self._save()
                    return {"ok": True}
            if self._current and self._current.id == job_id:
                return {"ok": False,
                        "error": "already running; cancelling now would "
                                 "waste the work already done"}
        return {"ok": False, "error": "no such job"}

    # ------------------------------------------------------------- worker
    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, daemon=True,
                                            name="trainer")
            self._worker.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._current = None
                    self._save()
                    return
                job = self._queue.pop(0)
                job.state = RUNNING
                job.started_at = time.time()
                self._current = job
                self._save()

            log.info("trainer: fitting %s %s", job.symbol, job.interval)
            try:
                self._fit(job)
            except Exception as e:                # never kill the worker
                job.state = FAILED
                job.note = str(e)[:200]
                log.warning("trainer: %s %s failed: %s",
                            job.symbol, job.interval, e)

            job.finished_at = time.time()
            with self._lock:
                self._current = None
                self._finished.append(job)
                self._finished = self._finished[-KEEP_FINISHED:]
                self._save()

    def _fit(self, job: Job) -> None:
        script = ("train_stocks.py" if job.market == "stocks"
                  else "train.py")
        cmd = [sys.executable, script, "--symbol", job.symbol,
               "--intervals", job.interval]
        if job.market != "stocks":
            # --no-tape, ALWAYS, for a fit started from the phone.
            #
            # Backfilling the trade tape is ~99% of a training run. An
            # ablation over 8 paired fits put the tape features at -0.002
            # AUC -- smaller than the spread between folds, i.e. no
            # measurable contribution. Paying 6.7 hours instead of 30
            # minutes for that is not a trade-off, it is a mistake, and on a
            # single-vCPU box it is one that also starves the API serving
            # the dashboard.
            #
            # The flag stays opt-IN in train.py so the ablation can still be
            # re-run from the command line and argue the other way.
            cmd.append("--no-tape")
        if job.market != "stocks":
            # crypto seeds from Binance inside train.py; equities are seeded
            # by the wrapper, which also pulls the higher timeframe
            cmd.append("--no-seed")

        # A hard ceiling. A fit that has not finished in three hours is stuck,
        # and leaving it there blocks every queued job behind it forever.
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=3 * 3600)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        self._parse(job, out)

        if proc.returncode != 0:
            job.state = FAILED
            job.note = job.note or _tail(out)
            return

        # EXIT 0 IS NOT SUCCESS. Training exits cleanly when the higher
        # timeframe store is empty, printing the bar count and nothing else —
        # which is how two fifty-minute runs produced no model and no error.
        # A run that reported no rows did not train anything.
        if not job.results:
            job.state = FAILED
            job.note = _no_model_note(job, out)
            return

        job.state = DONE

    @staticmethod
    def _parse(job: Job, out: str) -> None:
        for line in out.splitlines():
            m = _ROW.match(line)
            if m:
                tf, slot, auc, spread, shuffle = m.groups()
                job.results.append({
                    "interval": tf, "slot": slot,
                    "auc": float(auc), "spread": float(spread),
                    "shuffle": float(shuffle),
                })
                continue
            v = _VERDICT.search(line)
            if v and job.interval in v.group(1):
                # The pipeline's own guard. Carried through to the app rather
                # than left in a log nobody reads.
                job.at_chance = True
                job.note = ("at chance once fold spread is accounted for — "
                            "the fold-to-fold variation is wider than the "
                            "distance from 0.50, so the mean is noise")

    # -------------------------------------------------------- persistence
    def _save(self) -> None:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "queue": [asdict(j) for j in self._queue],
                "finished": [asdict(j) for j in self._finished],
                "current": asdict(self._current) if self._current else None,
            }))
            os.replace(tmp, STATE)
        except (OSError, TypeError, ValueError) as e:
            log.warning("trainer: could not persist state: %s", e)

    def _load(self) -> None:
        try:
            raw = json.loads(STATE.read_text())
        except (OSError, ValueError):
            return
        try:
            self._queue = [Job(**j) for j in raw.get("queue", [])]
            self._finished = [Job(**j) for j in raw.get("finished", [])]
            # A job that was RUNNING when the process died did not finish.
            # Recorded as failed rather than left claiming to be in progress
            # forever, which is the state that makes a queue look hung.
            cur = raw.get("current")
            if cur:
                j = Job(**cur)
                j.state = FAILED
                j.note = "interrupted by a server restart"
                j.finished_at = time.time()
                self._finished.append(j)
        except (TypeError, ValueError) as e:
            log.warning("trainer: state ignored: %s", e)
            self._queue, self._finished = [], []


def _tail(out: str, n: int = 240) -> str:
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return " / ".join(lines[-3:])[:n]


def _no_model_note(job: "Job", out: str) -> str:
    """Why a clean exit produced no model, in words that name a cause.

    The old text said "usually the higher-timeframe bars are missing" for
    every case, which is true often enough to be useless: it is the same
    sentence whether the data provider has never heard of the symbol or the
    4h store simply has not been filled yet. Those need different actions, so
    they get different sentences.
    """
    seeded_nothing = "no bars" in out
    if seeded_nothing and job.market == "stocks":
        return (f"no daily bars came back for {job.symbol}. Either the data "
                f"provider does not carry that ticker, or the market has "
                f"been closed long enough that nothing new arrived. "
                + _tail(out))
    if seeded_nothing:
        return (f"no bars came back for {job.symbol} {job.interval}. Check "
                f"the pair is still listed. " + _tail(out))
    return ("finished without fitting anything. Usually the higher-timeframe "
            "bars are missing. " + _tail(out))


_TRAINER: Optional[Trainer] = None
_TRAINER_LOCK = threading.Lock()


def get_trainer() -> Trainer:
    global _TRAINER
    with _TRAINER_LOCK:
        if _TRAINER is None:
            _TRAINER = Trainer()
            # Resume anything left queued by a restart.
            if _TRAINER._queue:
                _TRAINER._ensure_worker()
    return _TRAINER
