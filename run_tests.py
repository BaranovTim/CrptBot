#!/usr/bin/env python3
"""Run every detector test (Agents 1-5). No pytest required.

    python3 run_tests.py
    python3 run_tests.py --real     # also check for lookahead on live Binance data
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MODULES = [
    "tests.test_pivots",
    "tests.test_schema",
    "tests.test_no_lookahead",
    "tests.test_agent2_indicators",
    "tests.test_agent2_schema",
    "tests.test_agent2_no_lookahead",
    "tests.test_agent3_pit",
    "tests.test_agent3_injection",
    "tests.test_agent3_schema",
    "tests.test_agent4_tape",
    "tests.test_agent4_no_lookahead",
    "tests.test_agent4_schema",
    "tests.test_robustness",
    "tests.test_agent5_labels",
    "tests.test_agent5_splits",
    "tests.test_agent5_pipeline",
    "tests.test_livefeed",
    "tests.test_monitor",
    "tests.test_whalefeed",
]


def run_module(name: str) -> tuple:
    mod = __import__(name, fromlist=["*"])
    tests = [v for k, v in sorted(vars(mod).items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
            failed += 1
    return passed, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true",
                    help="additionally verify no lookahead on downloaded Binance bars")
    args = ap.parse_args()

    total_p = total_f = 0
    for name in MODULES:
        print(f"\n{name}")
        p, f = run_module(name)
        total_p += p
        total_f += f

    if args.real:
        print("\nreal Binance data")
        try:
            import config
            from marketdata import load_klines
            from tests.test_no_lookahead import test_no_lookahead as a1_leak
            from tests.test_agent2_no_lookahead import test_no_lookahead as a2_leak

            bars = load_klines("BTCUSDT", "1h", start="2026-05-01", end="2026-07-31",
                               cache_dir=config.DATA_CACHE)
            for label, fn in (("Agent 1", a1_leak), ("Agent 2", a2_leak)):
                fn(bars=bars, t_range=range(1300, 1500), horizon=200)
                print(f"  PASS  {label}: no lookahead over {len(bars):,} real bars")
                total_p += 1
        except Exception:
            traceback.print_exc()
            total_f += 1

    print(f"\n{'=' * 40}\n{total_p} passed, {total_f} failed")
    return 1 if total_f else 0


if __name__ == "__main__":
    raise SystemExit(main())
