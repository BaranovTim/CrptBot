"""Project-level settings.

No API key is needed for anything in this repo. data.binance.vision is public,
and the research phase never touches an account — which usefully decouples
"can I get an exchange account" from "can I start working". Keys only matter
at the execution phase, which is a long way off.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_CACHE = ROOT / "data_cache"
OUTPUT_DIR = ROOT / "output"

# --- market ---------------------------------------------------------------
SYMBOL = "BTCUSDT"
INTERVAL = "1h"
# USD-M perpetuals, not spot: spot has no funding, no open interest and no
# liquidations, which removes half of what Agent 4 and the regime block need.
MARKET = "futures/um"
HISTORY_START = "2023-01-01"

# --- execution (unused until the live phase) ------------------------------
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
USE_TESTNET = True
