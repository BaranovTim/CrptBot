# TradingBot — detector agents

**Agent 1** (patterns / smart money) and **Agent 2** (indicators). Both measure.
Neither judges — that is Agent 5's job, and it is the only block that learns.

## Agent 1 — patterns & smart money

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
python3 run_tests.py            # 46 tests, no network needed
python3 run_tests.py --real     # + leak checks on live Binance data
python3 main.py --offline       # synthetic bars
python3 main.py                 # real BTCUSDT 1h perps, both agents
python3 main.py --agent 2       # indicators only
```

```python
from agent1 import PatternAgent
from agent2 import IndicatorAgent
from marketdata import load_klines

bars = load_klines("BTCUSDT", "1h", start="2024-01-01")

a1, a2 = PatternAgent(), IndicatorAgent()
features = pd.concat([a1.compute(bars), a2.compute(bars)], axis=1)  # -> Agent 5
print(a1.latest(bars))                # trace -> humans only
print(a2.latest(bars))
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

---

# Agent 2 — indicators

```python
from agent2 import IndicatorAgent
agent = IndicatorAgent()
features = agent.compute(bars)        # 26 columns
print(agent.latest(bars))
```

Same four properties as Agent 1: pure, untrained, blind to the future,
opinionless. RSI, MACD, Bollinger and ATR are closed-form formulas — there is
nothing here to train, and the defaults (14, 12/26/9, 20/2) are the
conventional ones on purpose. Not because they are optimal, but because tuning
them against outcomes would make this block a fitted model, and fitting is
Agent 5's job.

## What to expect from it

Indicators are lagged transforms of price. RSI is a smoothed ratio of recent
up moves to down moves; MACD is the difference of two smoothed prices. They
carry no information that is not already in the candles Agent 1 reads — they
are a re-encoding, not a new source. So the honest expectation is that this
block adds little once order flow is present, because order flow is the cause
and price is the effect.

That is the argument for building it cheaply and *measuring* it, not for
skipping it. Agent 2 is iteration 2 of the ablation — the first block tested
against the regime-only floor — and "indicators did not beat the floor" is a
genuinely useful result that costs one afternoon.

| Block | Cols | Examples |
|---|---|---|
| trend | 6 | `ema_spread_atr`, `price_vs_ema200_atr`, `adx_14`, `macd_hist_atr` |
| momentum | 6 | `rsi_14`, `rsi_slope_3`, `stoch_k_14`, `cci_20`, `roc_10_atr` |
| volatility | 5 | `atr_pct`, `bb_position`, `bb_width_atr`, `bb_kc_ratio` |
| volume | 3 | `mfi_14`, `cmf_20`, `obv_slope_z` |
| meanrev | 4 | `zscore_close_20`, `rsi_price_corr_20`, `bars_since_macd_cross` |
| htf | 2 | `rsi_14_4h`, `macd_hist_atr_4h` |

## Its characteristic bug is not Agent 1's

Agent 1's leak was structural — a pivot published before the bars confirming
it had closed. Agent 2 has no pivots. Its leak hides in the **normalisation
layer**, and it is much quieter:

```python
z = (x - x.mean()) / x.std()     # the entire future, in every row
```

That line looks like ordinary preprocessing. It raises nothing, produces
plausible numbers, and improves the backtest. Every standardisation in
`agent2/` is a rolling window, and two things enforce it: the same
compute-twice comparison used for Agent 1, plus a static check that greps the
package for full-column `.mean()` / `.std()`. Both have been verified to fire
on a planted bug — the behavioural test caught it in 100/100 bars.

## Three things worth knowing before you edit it

**Nothing may be emitted in raw price units.** A MACD histogram of 50 is
enormous for BTC at 20k and noise at 100k. Everything in price units is
divided by ATR (`*_atr`) or close (`*_pct`), and
`test_nothing_is_in_raw_price_units` multiplies every price by 10 and asserts
the features barely move.

**`ema_spread_atr` IS the MACD line.** With 12/26 periods it is the same
number, not an approximation. Do not add a `macd_line_atr` — a perfect
duplicate makes a logistic regression answer with huge cancelling
coefficients (+8.3 on one, −8.1 on the other) that are meaningless
individually and unstable across folds.

**Not every signed column is a direction indicator.** `macd_hist_atr` is the
MACD line minus its own signal line, i.e. whether momentum is *accelerating*.
It sits near zero in a steady trend either way, and it is perfectly normal for
it to be positive while price falls — a decline that is decelerating. Measured
on a strong synthetic downtrend, the MACD *line* reads −3.90 while the
*histogram* reads +0.06. `schema.py` sorts the columns into `DIRECTIONAL_`,
`ACCELERATION_` and `SELF_CENTRING_` groups, and the sign test only checks the
first.

## Boundaries

Reads OHLCV. Volume enters only through classic indicators (MFI, CMF, OBV) —
never through `taker_buy_volume`, `number_of_trades` or `quote_volume`, which
are Agent 4's order-flow territory. Realized-vol percentile and
volume-vs-baseline are the regime block's, not Agent 2's, so `atr_pct` ships
raw and un-ranked.

Agent 2 also does not import from `agent1`, including the dozen lines of
resampling logic it duplicates. That is deliberate: the ablation deletes one
block at a time, and an agent that stops importing cleanly when its neighbour
is removed cannot be ablated.

## Window sizing

`warmup_bars` covers the base timeframe (~210, dominated by the 200 EMA).
`required_bars(bars)` also accounts for the higher timeframe — a 1d HTF over
hourly candles needs ~960 base bars, not 210. Feed a live window shorter than
that and the HTF columns come back NaN while the backtest, run over full
history, had them populated: a backtest/live divergence produced entirely by
window sizing. Short windows fail to NaN, never to a plausible wrong number.

---

## Layout

```
main.py              CLI runner
config.py            symbol, interval, paths
agent1/              patterns & smart money (33 cols)
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
agent2/              indicators (26 cols)
  config.py          Agent2Config — periods, all conventional defaults
  schema.py          the 26-column contract + directional/acceleration groups
  indicators.py      RSI, MACD, ADX, Bollinger, Keltner, MFI, CMF, OBV
  htf.py             4h context (own copy, so the agents stay separable)
  agent.py           IndicatorAgent, IndicatorOutput
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
   uniqueness weights. Until it exists you cannot tell whether any of these 59
   columns is worth keeping.
2. Agent 5, block by block: regime only → +agent 2 → +agent 1 → +agent 4 →
   +agent 3. Each step is one full run through the harness. That sequence *is*
   the ablation, and it is how you get `N, W, P, I`.

Start with 10–15 features, not all 59. After uniqueness weighting your
effective sample size is a few thousand independent observations, and 59
features on 3,000 effective samples will confidently find patterns that are
not there.
