"""The in-app training queue.

WHAT THESE GUARD

  TWO FITS AT ONCE   The droplet has one core. Two forty-minute fits running
        together take ninety minutes each and make the app time out
        throughout. Exactly one runs; the rest wait.

  A DUPLICATE JOB    Queueing the same pair twice spends eighty minutes to
        produce one model.

  EXIT 0 READ AS SUCCESS   Training exits CLEANLY when the higher-timeframe
        store is empty — it prints the bar count and stops. That silently
        burned two fifty-minute runs before anyone looked. A run that
        reported no rows did not train anything, whatever its exit code says.

  A LOST VERDICT     "At chance" is the most useful thing a fit can report and
        the easiest to drop on the floor. It is parsed out and kept.

  THE WRONG FITTER   Binance lists perpetuals on things that are also
        equities — AVGOUSDT, XAUUSDT — and they are in the crypto screener's
        default 178. Opening one used to land on the STOCK page, whose Train
        button hardcodes `market: "stocks"`, so the request asked Alpaca to
        fit a ticker called AVGOUSDT. Every attempt came back "finished
        without fitting anything", with nothing on screen to suggest the
        symbol had been sent to the wrong market entirely.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import trainer as T


def _fresh():
    """A trainer with private state, no disk, and no worker.

    `_ensure_worker` is stubbed deliberately: without it `submit` starts a
    real thread that immediately pops the job and runs `train.py` as a
    subprocess — so the queue under test empties itself, and the test suite
    starts fitting models. What is being tested here is the queue's
    bookkeeping, not the worker.
    """
    t = T.Trainer.__new__(T.Trainer)
    import threading
    t._lock = threading.Lock()
    t._queue, t._finished, t._current, t._worker = [], [], None, None
    t._recent = {}
    t._save = lambda: None                    # no disk in tests
    t._ensure_worker = lambda: None           # no subprocesses in tests
    return t


def test_an_unknown_timeframe_is_refused_before_the_work_starts():
    t = _fresh()
    r = t.submit("BTCUSDT", "3h")
    assert not r["ok"]
    assert "3h" in r["error"] and "4h" in r["error"], r
    # ...and nothing was queued
    assert t.status()["queued"] == []
    return True


def test_the_same_pair_is_never_queued_twice():
    t = _fresh()
    assert t.submit("BTCUSDT", "4h")["ok"]
    again = t.submit("BTCUSDT", "4h")
    assert not again["ok"]
    assert "already" in again["error"]
    assert len(t.status()["queued"]) == 1
    return True


def test_the_queue_has_a_ceiling():
    t = _fresh()
    for i in range(T.MAX_QUEUE):
        assert t.submit(f"SYM{i}", "4h")["ok"], i
    over = t.submit("ONEMORE", "4h")
    assert not over["ok"]
    assert "full" in over["error"]
    return True


def test_a_run_that_fitted_nothing_is_a_failure_whatever_its_exit_code():
    """THE BUG THIS PINS. `train.py` exits 0 with no model when the
    higher-timeframe bars are missing — it prints the bar count and stops.
    Two fifty-minute runs were lost to that before anyone read the output."""
    t = _fresh()
    job = T.Job(id="x", symbol="AAPL", interval="1d", market="stocks")

    class Proc:
        returncode = 0
        stdout = ("======\nAAPL 1d   htf 1w\n======\n"
                  "  2,511 bars  2016-09-07 -> 2026-09-02\n")
        stderr = ""

    import subprocess as sp
    real = sp.run
    sp.run = lambda *a, **k: Proc()
    try:
        t._fit(job)
    finally:
        sp.run = real

    assert job.state == T.FAILED, job.state
    assert "higher-timeframe" in job.note, job.note
    return True


def test_the_at_chance_verdict_survives_into_the_job():
    """The most useful thing a fit can report, and the easiest to lose."""
    job = T.Job(id="x", symbol="AAPL", interval="1d", market="stocks")
    T.Trainer._parse(job, """
  tf  h      bars   eff-n    AUC  spread  shuffle  overfit  folds
  1d h1     2,511   1,077  0.511   0.017    0.499   +0.040  [0.509]
  1d h2     2,511     797  0.498   0.025    0.500   +0.035  [0.546]

  at chance once fold spread is accounted for: 1d
""")
    assert len(job.results) == 2, job.results
    assert job.results[0]["auc"] == 0.511
    assert job.results[1]["shuffle"] == 0.500
    assert job.at_chance is True
    assert "noise" in job.note
    return True


def test_a_verdict_about_another_timeframe_is_not_claimed():
    """The summary lists every timeframe trained in that run. A job for 1d
    must not inherit 4h's verdict."""
    job = T.Job(id="x", symbol="AAPL", interval="1d", market="stocks")
    T.Trainer._parse(job, "  at chance once fold spread is accounted for: 4h\n")
    assert job.at_chance is False
    return True


def test_a_running_job_is_not_cancellable_but_a_queued_one_is():
    t = _fresh()
    r = t.submit("BTCUSDT", "4h")
    jid = r["job"]["id"]
    assert t.cancel(jid)["ok"]
    assert t.status()["queued"] == []

    r2 = t.submit("ETHUSDT", "4h")
    t._current = t._queue.pop(0)
    t._current.state = T.RUNNING
    out = t.cancel(r2["job"]["id"])
    assert not out["ok"]
    assert "waste" in out["error"]
    return True


def test_a_restart_does_not_leave_a_job_claiming_to_be_running():
    """A queue with a permanently 'running' job looks hung forever."""
    d = Path(tempfile.mkdtemp())
    old = T.STATE
    T.STATE = d / "jobs.json"
    try:
        import json
        from dataclasses import asdict

        T.STATE.write_text(json.dumps({
            "queue": [], "finished": [],
            "current": asdict(T.Job(id="a", symbol="BTCUSDT", interval="4h",
                                    market="crypto", state=T.RUNNING)),
        }))
        t = T.Trainer()
        st = t.status()
        assert st["current"] is None, st["current"]
        assert st["finished"][0]["state"] == T.FAILED
        assert "restart" in st["finished"][0]["note"]
    finally:
        T.STATE = old
    return True


def test_one_account_cannot_fill_the_day():
    """A POST that spends forty minutes of a one-core server is a resource
    the read-only argument never had to defend. The queue stops fits running
    at once; this stops one account from booking the whole day."""
    t = _fresh()
    for i in range(T.MAX_PER_DAY):
        # The queue is drained BEFORE each submit, not after a rejection:
        # `submit` checks MAX_QUEUE before the daily counter, so a rejected
        # call never counts — and a loop that let the queue fill would submit
        # fewer than MAX_PER_DAY while appearing to submit them all.
        t._queue.clear()
        r = t.submit(f"SYM{i}", "4h", account="alice")
        assert r["ok"], (i, r)

    t._queue.clear()
    over = t.submit("LASTONE", "4h", account="alice")
    assert not over["ok"] and "day" in over["error"], over

    # ...and a different account is unaffected
    t._queue.clear()
    assert t.submit("OTHER", "4h", account="bob")["ok"]
    return True


def test_a_usdt_pair_is_fitted_as_crypto_whatever_the_caller_claims():
    """The symbol is not ambiguous — no US equity ticker ends in USDT — so an
    incorrect `market` is corrected rather than obeyed. Obeying it is what
    produced three failed jobs and no explanation."""
    assert T.market_for("AVGOUSDT", "stocks") == "crypto"
    assert T.market_for("XAUUSDT", "stocks") == "crypto"
    assert T.market_for("avgousdt", "stocks") == "crypto"
    # and a real equity is left alone
    assert T.market_for("AVGO", "stocks") == "stocks"
    assert T.market_for("BTCUSDT", "crypto") == "crypto"

    t = _fresh()
    r = t.submit("AVGOUSDT", "4h", market="stocks", account="tim")
    assert r["ok"] is True
    assert r["job"]["market"] == "crypto", r["job"]
    return True


def test_a_seed_that_found_nothing_says_so_by_name():
    """"Usually the higher-timeframe bars are missing" was the note for every
    clean exit, including the ones where the provider had never heard of the
    symbol. Same sentence, different causes, different fixes."""
    job = T.Job(id="x", symbol="AVGOUSDT", interval="4h", market="stocks")
    note = T._no_model_note(job, "equity seed AVGOUSDT 1h: no bars")
    assert "AVGOUSDT" in note and "provider" in note, note

    other = T.Job(id="y", symbol="BTCUSDT", interval="1w", market="crypto")
    n2 = T._no_model_note(other, "some other failure entirely")
    assert "higher-timeframe" in n2, n2
    return True


def test_a_fit_from_the_phone_skips_the_tape_backfill():
    """The flag that is the difference between 30 minutes and 6.7 hours.

    Backfilling the trade tape is ~99% of a training run, and an ablation
    over 8 paired fits put those features at -0.002 AUC -- inside the spread
    between folds. This shipped without the flag once already, so the
    command itself is asserted rather than the intent.

    Equities are excluded on purpose: `train_stocks.py` has no such flag and
    passing it would fail the fit outright.
    """
    import subprocess as sp

    import api.trainer as T

    seen = {}

    class Proc:
        returncode = 0
        stdout = ("======\nBTCUSDT 1h   htf 4h\n======\n"
                  "  32,376 bars  2023-01-01 -> 2026-09-10\n"
                  "  AUC 0.531\n")
        stderr = ""

    real = sp.run

    def capture(cmd, *a, **k):
        seen["cmd"] = list(cmd)
        return Proc()

    sp.run = capture
    try:
        t = _fresh()
        crypto = T.Job(id="c1", symbol="BTCUSDT", interval="1h",
                       market="crypto")
        t._fit(crypto)
        assert "--no-tape" in seen["cmd"], seen["cmd"]

        equity = T.Job(id="e1", symbol="AAPL", interval="1d",
                       market="stocks")
        t._fit(equity)
        assert "--no-tape" not in seen["cmd"], seen["cmd"]
        assert "train_stocks.py" in " ".join(seen["cmd"]), seen["cmd"]
    finally:
        sp.run = real
    return True
