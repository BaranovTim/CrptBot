# TradingBot — Agent 1 (pattern & smart-money detector)

Agent 1 measures. It does not judge.

It reads a window of candles and returns 33 numbers describing that window's
geometry: where the order blocks are, how long since the last break of
structure, how far price sits from the 0.618, how complete a double bottom is.
Whether any of that predicts anything is a question for Agent 5.

## Four properties this code must keep

| | |
|---|---|
| **Pure** | Same window in, same numbers out. No state survives between calls. |
| **Untrained** | Not one number is fitted to outcomes. Every constant is in `Agent1Config`. |
| **Blind to the future** | Enforced by `tests/test_no_lookahead.py`, not by good intentions. |
| **Opinionless** | `dist_to_bull_ob_atr = -0.7` means a zone built by the bullish-order-block rule sits 0.7 ATR below price. It does **not** mean buy. |

The last one is the counter-intuitive one. If Agent 1 decided a bullish order
block was a buy signal, it would have imposed a hypothesis nobody tested. It
may well turn out that in high volatility bullish OBs precede *down* moves —
that is a question about data, and only Agent 5 can answer it.

Agent 1 also does not compute historical weights. Doing so would double-count
against Agent 5's fitted coefficients, and computing "how often did this
pattern work" needs point-in-time discipline that is easy to get wrong. Those
weights *are* Agent 5.

## Quick start

```bash
pip3 install -r requirements.txt
python3 run_tests.py            # 16 tests, no network needed
python3 main.py --offline       # synthetic bars
python3 main.py                 # real BTCUSDT 1h perps from Binance
```

```python
from agent1 import PatternAgent
from marketdata import load_klines

bars = load_klines("BTCUSDT", "1h", start="2024-01-01")

agent = PatternAgent()
features = agent.compute(bars)        # DataFrame -> Agent 5
print(agent.latest(bars))             # trace -> humans only
```

## The two output channels

`PatternOutput` deliberately keeps them apart:

- **`features`** — 33 floats, fixed order, goes to the model.
- **`trace`** — human-readable strings, goes to logs and the dashboard, and
  must **never** reach the model. If an explanation could influence a
  prediction, you could no longer validate the explanation independently of it.

```
[2026-07-30 23:59:59.999+00:00] Agent 1
  trend bullish, 4h bullish
  CHOCH bullish 14 bars ago, took out 64393.00
  liquidity sweep of highs 29 bars ago at 64688.00 (wicked through, closed back inside)
  price at 0.30 of dealing range (discount)
  nearest order blocks: bullish OB 2.29 ATR below, bearish OB 0.11 ATR above
  unfilled bullish FVG, size 0.67 ATR, 0.70 ATR below
```

## Output contract

Two rules, and breaking either is a bug:

**Sign.** Every `*_direction` / `trend_*` / `inside_*` column is `+1` bullish,
`-1` bearish, `0` none. Every `dist_to_*` column is `(level - close) / atr`, so
positive means the level is **above** price. One rule, no exceptions — that is
what makes a sign flip visible instead of silently turning bad news into a buy.

**Missing.** Absent things are `NaN`, never `0`, never `-999`. Zero means
"price is standing exactly on it", the opposite of "it isn't there". LightGBM
handles NaN natively and learns which branch to send it down.

| Block | Cols | Examples |
|---|---|---|
| structure | 8 | `trend_direction`, `bars_since_bos`, `position_in_range` |
| zones | 8 | `dist_to_bull_ob_atr`, `inside_ob`, `dist_to_fvg_atr` |
| liquidity | 6 | `dist_to_equal_highs_atr`, `dist_to_pdh_atr`, `dist_to_asia_high_atr` |
| fib | 3 | `fib_position`, `dist_to_fib_618_atr` |
| figures | 4 | `w_completion`, `m_completion`, `hs_completion` |
| candles | 2 | `cdl_bull_count_3`, `cdl_bear_count_3` |
| htf | 2 | `trend_direction_4h`, `position_in_range_4h` |

The blocks in `FEATURE_GROUPS` are what you sum permutation importance over
later. That grouping is the operational form of your `N, W, P, I`: you don't
compute those weights, you measure what breaks when a block is removed.

## The lookahead problem

The single most destructive bug available here is a pivot visible before the
bars proving it exists have closed. `scipy.signal.argrelextrema` over a full
series marks a top at the bar it happened — but you could only *know* it three
bars later. Three bars of free foresight per pivot, and the whole SMC layer is
built on top.

The defence is structural, not careful. A `Pivot` carries `index` (where the
extremum was) and `confirmed_at` (when it may be used); consumers walk bar by
bar and may only ingest pivots with `confirmed_at <= t`.

`tests/test_no_lookahead.py` computes features on `bars[:t]`, then on
`bars[:t+100]`, and compares the row for bar `t`. Honest detectors agree. It
runs over 100 bars of synthetic data plus, with `--real`, live Binance data.
It has been verified to fail when a lookahead bug is planted.

Beware third-party SMC libraries here. `smartmoneyconcepts` and friends were
written for *drawing charts*, where knowing the future is harmless. Test before
trusting.

## Data

| Job | Tool | Why |
|---|---|---|
| bulk history | `data.binance.vision` | free, no key, no rate limit, a month per file |
| gap repair | REST `/fapi/v1/klines` | only for holes left by a dropped collector |

USD-M perpetuals, not spot: spot has no funding rate, no open interest, no
liquidations — half of what Agent 4 and the regime block will need.

Handled for you: spot files from 2025-01-01 use **microsecond** timestamps
while earlier ones use milliseconds (naive `unit="ms"` parsing yields the year
57000 without raising), and bars are indexed by **`close_time`** — a bar
labelled 12:00 covers 12:00–12:59:59 and its close is unknowable until 13:00.

All 11 useful kline columns are stored even though Agent 1 reads five.
`add_derived_columns` turns the rest into free order flow:

```python
vwap               = quote_volume / volume          # Binance already computed it
vwap_position      = (vwap - low) / (high - low)    # where inside the bar volume traded
taker_buy_ratio    = taker_buy_base_volume / volume # aggressive-buy share
avg_trade_size_usd = quote_volume / number_of_trades
```

These belong to Agent 4 and the regime block, not Agent 1 — Agent 1 reads OHLC
only, so the ablation can tell geometry apart from flow.

## Layout

```
main.py              CLI runner
config.py            symbol, interval, paths
agent1/
  config.py          Agent1Config — every tunable constant
  schema.py          the 33-column contract + validate_features
  indicators.py      ATR, causal helpers
  pivots.py          swing detection + the confirmation stamp   <- read first
  structure.py       BOS / CHoCH state machine, dealing range, sweeps
  zones.py           order blocks, FVG, and the variable->fixed reduction
  liquidity.py       equal highs/lows, PDH/PDL, session extremes
  fib.py             retracements off the last confirmed leg
  figures.py         W / M / head & shoulders as graded completion
  candles.py         native patterns -> two counts
  htf.py             4h context, joined with backward merge_asof
  agent.py           PatternAgent, PatternOutput
marketdata/binance.py
tests/
```

`main_oanda_sketch.py.bak` is your original OANDA sketch, kept for reference.

## What Agent 1 does not read

Order book and tape (Agent 4), funding / OI / liquidations (Agent 4 + regime),
news (Agent 3), other coins (regime). Not tidiness: if funding leaked in here,
the ablation could never tell you whether a lift came from geometry or funding.

## Known ceiling

A candle is an aggregate. Identical OHLC can describe a smooth grind up with
one pullback, or a violent spike up then a collapse and recovery — completely
different events, one candle. That is Agent 1's accuracy ceiling, and it is
why Agent 4 reads `aggTrades` directly.

## Next

1. The evaluation harness — triple-barrier labels, purged CV with embargo,
   uniqueness weights. Until it exists you cannot tell whether any of these 33
   columns is worth keeping.
2. Agent 2 (indicators) — a library import, one afternoon.
3. Agent 5, block by block: regime only → +agent 2 → +agent 1 → +agent 4 →
   +agent 3. Each step is one full run through the harness. That sequence *is*
   the ablation, and it is how you get `N, W, P, I`.

Start with 10–15 features, not all 33. After uniqueness weighting your
effective sample size is a few thousand independent observations, and 33
features on 3,000 effective samples will confidently find patterns that are
not there.
