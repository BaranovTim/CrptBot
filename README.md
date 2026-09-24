# Vanth — crypto calls you can check

A crypto prediction app: a Flutter app (Android first, iOS from the same code)
reading a Python API on one small server. It tells you **when to enter, where
to place the order, where to take profit and where the stop goes** on 15 coins,
and it keeps an honest public record of how those calls actually did. It holds
no exchange key and places no orders: you trade on your exchange, the app says
what and when.

This page is the map. Every number on it comes from `research/QUANT.md`, which
records every experiment — including the many that failed — and how each was
measured. Deploying is in `DEPLOY.md`; the app has its own `mobile/README.md`.

## What it does today (September 2026)

| | What you get | How it is decided |
|---|---|---|
| **4h calls** | BUY / SELL with a **limit order** 0.5 ATR better than the close, good for 24 hours; target at the next swing level; stop 0.5 ATR beyond the last one; **a third off halfway**, then the stop moves to your entry | One model trained on all 15 coins together (long and short sides, five seeds averaged). A coin calls when its reading is in the top 3% (strong), 5% (medium) or 10% (small) **of its own readings over the last 90 days** — other coins cannot change it. The card leads with the model's own chance that the target comes before the stop |
| **1d calls** | Swing entries with **no time limit**: a trailing stop follows the confirmed swing lows; longs only above the 200-day average | Pooled daily models (long and short), structure levels |
| **Weekly rotation** | Every Monday 00:00 UTC: long 5 / short 5 of the 30 most-traded Binance perpetuals, held a week | 15-day momentum and a week of net taker buying (buyers more aggressive than the price shows), ranks averaged |
| **Smart money** | Whether followed top traders on Hyperliquid took the same side recently | Raises or lowers a call one level; not a model input |
| **Live record** | Every order since the model went live: filled or not, target / stop / back to entry, after fees, per sensitivity | `api/ledger.py`, written as it happens; nothing reconstructed |
| **Notifications** | New call, order moved, **filled**, **halfway — take a third off**, trade ended, order expired, Monday rotation | `api/alerts.py`, transitions only |

## How good it is — measured on data the models never saw

Walk-forward: refit every six months on the past only, scored on the next six,
**Sep 2023 → Sep 2026**. Rules were chosen on the first two years and only
*confirmed* on the last one. Net of 0.10% fees.

| | Win rate | Per trade | Risk-adjusted (Sharpe) | Notes |
|---|---|---|---|---|
| 4h, strong calls, taking every call | 72% (70% last year) | +0.21% (−0.02% last year) | — | a trade that reaches halfway can no longer lose |
| 4h, holding at most 3 trades at once | 75% (73%) | — | 2.19 / 1.35 | first come, first served |
| 1d longs, trailed (above the 200-day) | 24% | +4.8% to +5.1% | — | most end at the stop; winners run +15–30% |
| Weekly rotation | — | — | 0.91 / 1.09 | worst weeks −15% to −18% |

For scale: peer-reviewed machine-learning forecasts of bitcoin were right
51–56% of the time an hour or less ahead, and no bot or signal seller we could
find publishes an audited record (QUANT.md, round four). Backtests decay in
real life; the live record is the number that counts, and the app says so.

Things that were measured and **did not** help, so they are not in the app:
order flow and sweeps inside the 4h model, estimated liquidation maps,
weekly/monthly levels, the Coinbase premium, a model per coin (alone or
blended with the shared one), strength from a fixed probability line,
round-number targets, and ten extra coins (below).

## Adding coins

Each coin is ranked against **its own** history, so adding a coin never
changes another coin's calls (tested: the 15's trades were identical with ten
more coins served). What decides whether a new coin gets calls is **its own
out-of-sample record**:

```bash
python3 research/expand_coins.py fetch          # history for the candidates
python3 research/expand_coins.py lab-build
python3 research/expand_coins.py lab-run x25_bag5 x15_bag5
python3 research/expand_coins.py lab-report     # per-coin: does it pay?
```

AVAX, LINK, LTC, BCH, AAVE, FIL, DOT, WLD, TAO and ONDO were tested on
2026-09-24: the model wins 68% of the time on them but loses money per trade
(−0.15%, −0.52% in the last year), so they are not served. Training on them
did not improve the other 15 either.

## Running it

```bash
pip3 install -r requirements.txt
python3 run_tests.py                 # 592 tests, no network needed
cd mobile && flutter test            # 163 app tests

python3 serve.py                     # the API locally (read-only JSON)
python3 train_pooled_4h.py           # refit the 4h models (every ~6 months)
python3 train_pooled_4h.py --update-rules   # change entry/exit rules, no refit
python3 train_daily_pooled.py        # refit the daily models

python3 research/wf4h.py run <variant>      # the walk-forward lab
python3 research/win_rate.py                # win rate vs money, every lever
python3 research/momentum_pit.py            # the rotation on every perpetual
```

A retrain starts a new rank pool; see `DEPLOY.md` for filling it from current
bars before uploading, and for the rules that keep the server alive (one vCPU,
967 MB: never run model-loading scripts inside the containers).

## Where things live

```
api/          the server: service.py (dashboards, orders, record), alerts.py
              (notifications), ledger.py (live record), momentum.py (rotation),
              server.py (routes), push.py, accounts/billing/oauth
monitor.py    the call logic: ranking, resting orders, scale-out, the card text
agent1-5/     the detectors (1-4) and the model (5) -- documented below
smartmoney/   followed Hyperliquid traders: selection and tracking
research/     every study, and QUANT.md, the record of what was measured
mobile/       the Flutter app
train_pooled_4h.py, train_daily_pooled.py    the production fits
```

---

*Everything below documents the components as they were built: the four
detector agents, the judge, the first version of the app and the whale
tracking. The design reasoning still holds; the configuration that runs today
is the one described above.*

# The detector agents

**Agent 1** (patterns / smart money), **Agent 2** (indicators), **Agent 3**
(news), **Agent 4** (order flow). All four measure. None of them judges — that
is Agent 5's job, and it is the only block that learns.

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
python3 run_tests.py            # 592 tests, no network needed
python3 run_tests.py --real     # + leak checks on live Binance data
python3 main.py --offline       # synthetic bars
python3 main.py                 # real BTCUSDT 1h perps, both agents
python3 main.py --agent 2       # indicators only
python3 main.py --agent 3 --offline   # news, synthetic headlines
python3 main.py --agent 4 --tape      # order flow (downloads aggTrades)
python3 main.py --judge --ablation    # train Agent 5 and run the ablation
python3 collect.py --seed 2024-01-01  # ONCE: fill the live store from history
python3 monitor.py                    # <- the rolling two-bar screen
                                      #    (fetches its own bars; collect.py optional)
python3 predict.py --history          # <- the percentage, right now
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

---

# Agent 3 — news

```python
from agent3 import NewsAgent, ClaudeScorer
from newsfeed import JSONLNewsStore

store  = JSONLNewsStore()
agent  = NewsAgent(scorer=ClaudeScorer())      # LexiconScorer by default
scores = agent.score_items(items)              # slow, offline, once per item
feats  = agent.compute(bars, items, scores)    # fast, pure arithmetic
```

19 columns: `sentiment` (5), `salience` (5), `volume` (4), `category` (4),
`quality` (1). Same contract as the others — pure, untrained, blind to the
future, opinionless. "Untrained" still holds even though it calls an LLM: the
model is a fixed pretrained instrument, and nothing here is fitted to trading
outcomes. Change the prompt or the model and you have changed the instrument —
re-score the whole corpus rather than mixing old and new scores in one dataset.

Agents 1 and 2 read candles: closed, numeric, exchange-timestamped, and nobody
can write to the feed. Agent 3 reads text off the public internet. That
introduces two problems the others do not have.

## Problem 1: the timestamp is the weak link

Price data is microsecond-accurate and honest. A news timestamp is a *claim*,
sometimes revised after the fact. Every item carries three clocks:

```
event_time    when the thing actually happened   (often unknown)
published_at  when the source published it
ingested_at   when WE first saw it
```

`observable_at` is the **latest** of the ones we trust, plus a safety margin —
never `event_time`. An event at 14:00, published 14:20, ingested 14:40 becomes
usable at 14:40. Keying on 14:00 hands the backtest 40 minutes of free
foresight *on every item*, and news lands precisely on the moves you are trying
to predict, so that foresight is worth a lot and will never reproduce live.

This is the direct analogue of Agent 1's `confirmed_at`, and it is the quietest
of the three leaks: every row still has a plausible timestamp and nothing
raises. `tests/test_agent3_pit.py` catches it two ways — extending the bar index
must not change earlier rows, and adding one item must not move any bar that
closed before it was observable. Both have been verified to fire on planted
bugs (gate on `event_time`: 6-hour drift; gate on `published_at`: 3 contaminated
bars).

The `safety_lag_seconds` default of 60 costs a minute of signal and buys the
guarantee that a revised publication time cannot pull an item earlier than you
saw it.

## Problem 2: the input is adversarial

Anyone can publish a headline, and a headline written to steer a bot known to
be reading it is a cheap, repeatable attack that has already happened in real
markets. Four defences, in order of how much they matter:

1. **Structure, not persuasion.** The scorer's output schema is bounded numbers
   plus a closed category enum, with `additionalProperties: false`. There is no
   action field, no `should_buy`, nothing an injected instruction could express
   itself through. A fully successful attack moves a number inside its valid
   range — the same failure mode as an honest mis-score, which the calibration
   layer already assumes.
2. **Validation on the way out.** Every field clamped and type-checked. The
   model is never trusted to respect its own schema.
3. **Delimited, labelled input.** The tag is stripped from the content first so
   a payload cannot close the block early.
4. **Detection.** `looks_like_injection` flags known patterns so attack volume
   is a number, not a hunch. Monitoring, never a filter.

Asking the model nicely to ignore instructions is in the prompt because it
helps at the margin, but it is the weakest layer and nothing depends on it.

The strongest test assumes the model is **fully compromised** — an attacker
choosing every value it returns — and asserts the feature frame still respects
every bound. It does, because the clamp sits between the model and the
features.

## Why an LLM rather than FinBERT / CryptoBERT

Because sentiment is the part that doesn't matter. Those models answer "is this
bullish?", and nearly everything published about a coin is bullish — so
sentiment alone separates almost nothing. What's needed is **novelty**
(did the market already know?), magnitude, and which asset. That needs
something that can reason about whether a scheduled event happening on schedule
is news. It isn't.

Two features exist specifically because of this:
`news_sentiment_dispersion_24h` (near 0 = everyone agrees, which is the common
case and therefore weak evidence) and `news_novelty_max_6h`.

Fine-tuning later is cheaper than it sounds, because the labels are free: take
each historical headline, look up the forward return in your own price data,
and label by what actually happened. No manual annotation. That's a later
phase, not now.

## Latency: scoring is offline, always

LLM inference takes seconds. A trading decision has to be arithmetic. So every
model call happens at **ingest** time, cached by content hash, and the per-bar
feature computation reads frozen numbers off disk. No model call ever sits
between a bar closing and a decision being made.

For historical backfill, `ClaudeScorer.batch_requests()` builds Batch API
requests — same work at half price, and nothing about a backfill is
latency-sensitive. The system prompt is cached (`cache_control: ephemeral`);
keep it frozen, since interpolating an asset name or timestamp into it would
invalidate the cache on every request.

Thinking is left **on at low effort** rather than disabled. Disabling looks
cheaper but has a documented failure mode where internal `<thinking>` tags leak
into the visible response — which, for a JSON extraction task, is a parse
failure.

## The NaN rule is sharpest here

`news_sentiment_6h == 0` means news exists and reads neutral. `NaN` means there
was **no news at all**. Different market states; a 0-fill would teach the model
that silence is neutrality. Counts are the mirror — a 24h count of 0 is an
observed fact, so those are 0-filled.

A failed score returns magnitude 0, so a broken scorer degrades the features
toward "no news" rather than toward fabricated signal — and
`news_scored_fraction_24h` reports the degradation to Agent 5 instead of hiding
it.

## Model choice

Defaults to `claude-opus-5`. Model, effort, and `max_tokens` are all in
`ClaudeScorer.__init__`, so trading cost against scoring quality is your call,
not a default made silently on your behalf.

## Ablation position

Agent 3 is **fifth and last** in the plan's ablation order — regime → +agent 2
→ +agent 1 → +agent 4 → +agent 3 — and there is a real chance you never reach
it. That ordering is worth respecting: it is the most expensive block to run
and the most likely to add nothing.

---

---

# Agent 4 — order flow & positioning

```python
from agent4 import FlowAgent
from marketdata.aggtrades import load_tape_bars
from marketdata.derivatives import load_open_interest, resample_to_bars

tape = load_tape_bars(bars.index, "BTCUSDT", start="2026-05-01")
oi   = resample_to_bars(load_open_interest("BTCUSDT", "2026-05-01"), bars.index)

agent = FlowAgent()
features = agent.compute(bars, tape=tape, open_interest=oi)   # 22 columns
```

Agents 1 and 2 read price. Order flow is the **cause** of which price is the
effect, so this is the first block reading something upstream of the thing it
predicts. That's the reason to expect more from it than from chart geometry —
a hypothesis to measure, not a promise.

## The three tiers, and why netflow isn't bundled

The plan calls this *"a data-buying problem, not a modelling one"*, so the
inputs are split by what they cost:

| Tier | Inputs | Status |
|---|---|---|
| free, already downloaded | `taker_buy_ratio`, trade counts, avg fill size | ride inside every kline |
| free, needs downloading | aggTrades (the tape), open-interest metrics | `--tape` / `load_open_interest` |
| paid | exchange netflow (Glassnode / Nansen / Arkham) | **not bundled** |

Agents 1 and 2 were deliberately kept away from the kline order-flow columns
so this block could claim them in the ablation — if something upstream had
quietly consumed them, no ablation could attribute their contribution.

**No paid client ships here on purpose.** Buying a netflow feed before the
harness can measure whether Agent 4 contributes anything is spending money to
answer a question the ablation answers for free. Implement `NetflowProvider`
against whichever vendor you pick; nothing else changes.

There is also **no wallet-following path**, and that's deliberate. Named-whale
tracking is lagging by construction — you see the transfer after it lands —
and it's actively gamed by people who know they're watched. Large prints and
netflow are the two whale signals that hold up.

## `is_buyer_maker` — the flag everyone inverts

Every aggTrade carries it, and it is the single most commonly flipped field in
crypto quant work:

```
m == True    the BUYER was the maker  ->  the SELLER crossed  ->  aggressive SELL
m == False   the buyer was the taker  ->  the BUYER crossed   ->  aggressive BUY
```

Invert it and every flow feature in the project flips sign — silently. No
exception, plausible magnitudes, and the model dutifully learns that
aggressive selling precedes rallies. Pinned against hand-built cases in
`tests/test_agent4_tape.py`, and verified to fire when planted.

It also has an **independent cross-check on real data**, which is stronger
than any unit test: `taker_buy_ratio` comes from Binance's own
`taker_buy_base_volume` kline field, while `aggressor_imbalance` comes from
our own aggTrades parsing. Two unrelated paths, and on real BTCUSDT bars they
correlate **+1.000** with 100% sign agreement. If the flag were inverted the
correlation would be −1.

Two more sign traps, both tested: a liquidation with `side=SELL` means a
**long** was force-closed (bearish), and exchange **inflow** is supply
arriving to be sold (bearish), so the bullish-positive direction is the
negative of the flow.

## The histogram trick

Per-bar totals throw away the size distribution, which is exactly what
large-print detection needs — but a day of BTCUSDT aggTrades is hundreds of
megabytes, so keeping raw prints is impractical.

Each bar therefore stores a **log-spaced histogram of trade notionals and
counts**, split by aggressor side. Fixed width, and — the useful part — it
makes the expensive tape pass **independent of the large-print threshold**.
Re-tune what counts as "large" and you recompute from disk in milliseconds
instead of re-downloading. Real throughput: 2M prints (a realistic BTCUSDT
day) reduce to bars in ~0.9s.

## Defining "large" — measured, not guessed

A fixed dollar threshold drifts: $500k was a whale in 2020 and is ordinary
now, so it silently changes meaning across a training window — the same
non-stationarity trap as feeding a raw MACD to Agent 2.

The threshold is a percentile of the print-size distribution **by count**, from
a trailing window that excludes the bar being classified. Both halves of that
were bugs I hit while building:

- **By count, not by volume.** Defining "large" as the bucket holding the top
  5% of *dollar volume* collapses onto a single bucket on a heavy tail — 7
  detections in 375 bars. By count it fires every bar.
- **Excluding the current bar.** Otherwise an unusual burst raises its own
  threshold and can classify itself as ordinary, hiding exactly the events the
  feature exists to find.

The default (`0.005`, top 0.5% of prints) came from sweeping the value and
reading the feature distribution — at `0.05` large prints capture ~85% of bar
volume and discriminate almost nothing; at `0.005` they capture ~50% with the
highest variance. Tune it on distribution shape if you like, but **never on
trading outcomes** — a threshold chosen because it made money is a fitted
parameter, and fitted parameters belong to Agent 5.

## Missing feeds are the normal case

Every feed is independently optional, so "missing" is routine, not an edge
case. Each one degrades to NaN — never a confident zero, which would read as
"no whales were active" instead of "we cannot see whether they were" — and two
coverage columns say which case you're in:

```
tape coverage 0% over 24h — no aggTrades loaded; running on kline order flow only
liquidation feed present, but none in this bar
no exchange netflow (paid feed; not configured)
```

Without the tape, Agent 4 falls back to kline order flow rather than failing:
reduced resolution, not nothing.

## Boundary

Reads tape, open interest, liquidations, netflow, and the order-flow columns
inside klines. Not candle geometry (Agent 1), not classic indicators (Agent 2),
not news (Agent 3) — and **not** funding rate or realized-vol percentile, which
belong to the regime block even though they sit next to order flow
conceptually. `avg_trade_size_usd_z` and `trade_count_z` are here because they
describe the *character* of flow (few large fills vs many small), a different
question from "is volume high right now".

## Known ceiling

`aggTrades` collapses same-price, same-direction fills at the same instant into
one row — right for flow analysis, wrong if you're counting individual orders.
A "print" here is one aggregated fill. And there is no order-book depth: this
block sees what traded, not what was resting, so "was the book thin before the
move?" stays unanswered.

---

---

# Agent 5 — the judge

```python
from agent5 import JudgeAgent

judge = JudgeAgent()
ds = judge.build(bars, warmup=400, agent1=f1, agent2=f2, agent3=f3, agent4=f4)
print(judge.fit(ds, with_ablation=True))     # cross-validate, calibrate, report
print(judge.latest(bars, ds.X))              # probability -> EV -> position size
judge.save("output/judge.pkl")
```

Agents 1–4 are thermometers. This is the doctor — and there is exactly one of
it. Delete any detector and the system still works, slightly better or worse,
and you *measure* which. Delete Agent 5 and you have a pile of numbers and no
way to turn them into a decision.

## Two formulas, deliberately separate

Keeping these apart is what makes the whole design tractable. The original `S`
formula was trying to be both at once, which is why it felt slippery.

**Stage 1–3 — data → probability. FITTED.**
Features → model → `p_raw` → isotonic calibration → `p_calibrated`.
The model's coefficients *are* the `N / W / P / I` weights. You don't compute
them from anything; they're fitted from history. That was the direct answer to
"AI or code?" — it's `LogisticRegression().fit(X, y)`, one line, no AI.

**Stage 4–5 — probability → trade. WRITTEN BY HAND.**
```
EV   = p·TP − (1−p)·SL − costs
f*   = (p·b − (1−p)) / b        b = TP/SL
size = 0.25 · f* · equity       then clamped by hard risk limits
```
Nothing here is learned. The worked example from the plan, reproduced exactly
by `tests/test_agent5_pipeline.py`:

| p | barriers | EV after 0.1% costs | decision |
|---|---|---|---|
| 61% | +2% / −1% | **+0.73%** | long, quarter-Kelly 10.375% |
| 61% | +0.5% / −1% | **−0.185%** | flat |

Same confidence, opposite decision. This is why probability alone can never be
the entry rule.

## The label decides what the model learns

Triple barrier: from bar `t`'s close, does price travel `+2 ATR` before
`−1 ATR`, within 24 bars? The payoff is baked into the label, so the model
predicts *"would this trade have won"* rather than an abstract direction.

Three decisions worth knowing:

- **The ambiguous bar counts as a loss.** One bar can touch both barriers, and
  OHLC can't say which came first. Assuming the friendly answer is how
  backtests get confident and accounts get empty. Dropping those samples would
  be worse — it selectively deletes the most volatile moments.
- **Tail bars get no label at all**, never a partial one. A window that peeked
  at "the data so far" would systematically favour whatever the final bars did.
- **One side per model.** `k_up=2, k_dn=1` is the payoff geometry of a *long*.
  A low `p` does **not** imply a profitable short — for a short those barriers
  are the wrong way round. So shorts have their own model on mirrored labels
  (the pooled 4h and daily fits are a long model and a short model).

## The scoreboard is the product

`PurgedKFold` is the single most important file here, because every number the
project will ever produce flows through it.

- **Purge** — a sample taken 10 bars before the test period resolves *inside*
  it. Training on it means memorising part of the test outcome.
- **Embargo** — features are autocorrelated, so a sample just *after* the test
  block is a near-duplicate even when no label overlaps.

`tests/test_agent5_splits.py` proves this is load-bearing, not decoration. On
data built to contain **zero** real signal, shuffled K-fold (the sklearn
tutorial default) with kNN scores **AUC > 0.75** — it's copying temporal
neighbours out of the training fold. `PurgedKFold` on the same data collapses
to a coin flip. Same data, same model; the only difference is the scoreboard.

## Reading a training report

```
samples 1,760 (287 effective) win rate 32.5%
AUC 0.430  folds [0.515 0.456 0.453 0.505]  spread 0.028
overfit gap (train - oof) +0.304
Brier 0.2273 -> 0.2208 after calibration
calibration error 0.096 -> 0.045
shuffle test AUC 0.523 (0.50 = clean)
  WARNING: train AUC exceeds out-of-fold by 0.30 ...
  WARNING: only 287 independent observations ... too few for 88 features
  WARNING: AUC 0.430 is at chance - this feature set predicts nothing
```

That is a real run on three months of BTCUSDT, and it is the harness working
correctly. Read it in this order:

1. **Folds, not the mean.** `0.61 / 0.49 / 0.58 / 0.51 / 0.57` averages to a
   respectable 0.55 and is noise. Stable `0.54 / 0.53 / 0.55` is worth far more.
2. **Shuffle test.** Labels shuffled → anything above ~0.50 is leakage, not
   skill. Re-run it after adding any new data source; leakage arrives with new
   *data*, not new models.
3. **Effective sample size.** 1,760 samples became **287** independent
   observations after uniqueness weighting. Overlapping labels are not
   independent, and pretending otherwise inflates your data ~6×.
4. **Overfit gap.** Train AUC 0.30 above out-of-fold → raise
   `min_data_in_leaf`, lower `num_leaves`. On this problem a *training* AUC
   near 0.60 is healthier than one near 0.90.

## That report was a SMALL-SAMPLE verdict, not a feature-set verdict

The run above is real and worth keeping, because reading it correctly is the
skill. But it has since been repeated on the same timeframe, with the same
features and the same purged cross-validation, over the full history rather
than three months:

```
                     AUC   folds                          spread   eff-n
three months       0.430   [0.515 0.456 0.453 0.505]       0.028      287
full history       0.526   [0.540 0.541 0.517 0.524 0.536] 0.009   11,471
```

Every fold is now above 0.50, the spread is a third of what it was, and the
shuffle test is clean at 0.496. The same shape holds independently at 1m, 5m,
15m and 4h — six timeframes, all folds above 0.50, all shuffles between 0.494
and 0.502.

**What that does and does not mean.** Six independent timeframes landing at
0.51-0.53 with stable folds and clean shuffles is not the signature of
multiple testing; noise gives you folds straddling 0.50 and spreads the size
of the effect. So there is probably something small there, and the earlier
"this feature set predicts nothing" was mostly a statement about 287
observations.

It is still **not** a claim of profitability. An AUC of 0.526 is a thin edge,
it has not survived forward testing, and on the fast timeframes it cannot
survive costs at all — see the table below. The correct next step is forward
paper trading with a prediction journal, not more fitting.

## Costs decide the barrier geometry, not which timeframes are possible

This has nothing to do with how good a model is, and it is settled before
accuracy is discussed. With the **default +/-1 ATR barriers**, median 1-ATR
moves on BTCUSDT against a 0.100% round trip:

| timeframe | span at +/-1 ATR | fees as share |
|---|---|---|
| 1m | 0.092% | **109%** |
| 5m | 0.278% | 36% |
| 15m | 0.555% | 18% |
| 1h | 1.278% | 8% |

At 1m the whole distance from take-profit to stop-loss is smaller than the
cost of opening and closing. An AUC of 0.99 would still lose money.

**That is a statement about the barriers, not about the timeframe.** +/-1 ATR
over one or two 1m bars is a *scalping* target — predicting the next two
minutes to within 0.05%. Day trading does not mean that. It means using fast
bars for RESOLUTION while holding for tens of minutes to hours.

So `core.timeframes.BARRIERS` widens the barriers as the bars shrink:

| tf | k (each side) | hold | horizon | span | fees as share |
|---|---|---|---|---|---|
| 1m | 11 | 240 bars | 4h | 1.01% | 10% |
| 5m | 4 | 32 bars | 2h40 | 1.11% | 9% |
| 15m | 2 | 8 bars | 2h | 1.11% | 9% |
| 1h | 1 | 2 bars | 2h | 1.28% | 8% |
| 4h | 1 | 2 bars | 8h | 3.06% | 3% |
| 1d | 1 | 2 bars | 2d | 8.27% | 1% |

`k` is picked so the span clears ~1%, which puts a 0.1% round trip near 10% of
it. The hold is about `2k^2` bars, because a random walk covers `k` ATR in
roughly `k^2` bars — a hold much shorter than that means only the vertical
(time-out) barrier ever fires and the model learns to predict the clock. The
result is a 2-4 hour horizon on every fast timeframe, which is what day
trading is.

1h, 4h and 1d keep +/-1: their spans already clear costs, and 1h's frozen
models were fitted that way.

**The monitor reads the horizon off the fitted model** rather than assuming 1
and 2 bars. At 1m it draws a 120/240-bar window because that is the question
the model was asked; hardcoding the old constants would have drawn a
two-minute window over a four-hour prediction.

`tests/test_timeframes.py` asserts every profile keeps fees under 15% of its
span, and separately asserts that +/-1 ATR at 1m would still be untradeable —
so if that ever stops being true, the profiles get revisited rather than
quietly kept.

## Two limits on the fast timeframes

**Feature rebuilds do not scale down.** `Monitor.features()` recomputes the
whole history whenever the newest bar changes. At 1h that is a 1.7s job once
an hour. At 1m it is **18.2 seconds over 171,360 bars, once a minute** —
roughly a third of a core, permanently, for one symbol. The models are fitted
and usable, and reading a single screen is fine, but a live 1m loop needs
incremental features before it is practical. Nothing here pretends otherwise:
the first load of a 1m dashboard visibly takes half a minute.

**Collect every interval you intend to look at.** `--interval` is
appendable, so one process covers all six:

```bash
python3 collect.py --interval 1m --interval 5m --interval 15m \
                   --interval 1h --interval 4h --interval 1d
```

Open a 1m screen without a 1m collector and it reports `STALE` — correctly,
because the newest stored bar really is old. That is the guard working, not a
limitation of the timeframe.

## Reading the training table

`train.py` prints every timeframe together and **refuses to sort them**. Six
timeframes times two horizons is twelve fits, so the best of them is above
0.50 by construction even when nothing predicts anything — that is the
multiple-testing trap the plan named, and picking the top row is how you fall
into it.

A timeframe earns trust from **stable folds** and a **clean shuffle test**
(~0.50), never from its mean. A fold spread wider than the mean's distance
from 0.50 means the mean is noise.

---

---

# The phone app

```bash
python3 serve.py            # read-only JSON API, prints the address to use
cd mobile && flutter run    # iOS or Android, one codebase
```

A Flutter app for **iPhone and Android**, built to the Stitch design in
`app_idea/`, reading this stack through a small local API. Full notes in
[mobile/README.md](mobile/README.md).

## The API is deliberately boring

`api/` is seven GET endpoints on `http.server`. No framework: adding FastAPI
and uvicorn to serve read-only JSON to one phone would be the largest
dependency decision in the repo, made for its least important component.

It reuses `Monitor` rather than reimplementing anything. Two code paths
computing "the probability" is how a phone and a terminal end up disagreeing
about the same bar — `Monitor` already owns the horizon rule, the feature
cache, the staleness guard and the barrier arithmetic, so the app is a second
*view* of that one object.

| endpoint | what it returns |
|---|---|
| `/api/dashboard` | status, live forming bar, indicators, both analyses, the recommendation, TP/SL |
| `/api/coins` | the universe, with live prices and which pairs have a fitted model |
| `/api/chart` | closes for the sparkline |
| `/api/whales` `/api/news` | the same feeds `monitor.py` prints |
| `/api/training` | fitted horizons, or the command to fit one |
| `/api/alerts` | transitions worth a notification, since a cursor |
| `/api/calendar` | scheduled events, so the phone can book warnings ahead |
| `/api/symbols` | every USD-M perpetual Binance lists, by 24h volume |
| `/api/coins?symbols=` | rows for whatever list the phone asks about |

## The watchlist lives on the phone, not here

`/api/symbols` reads Binance's `exchangeInfo` and returns the 524 USDT
perpetuals currently `TRADING`, ordered by 24h quote volume — a search for
"b" should offer BTC before BAKE, and alphabetical order buries every pair
anyone wants behind three-letter tokens nobody has heard of. Cached six
hours; contracts are listed and delisted, not renamed hourly.

**Nothing is stored server-side.** The app keeps its own list and passes it as
`/api/coins?symbols=BTCUSDT,ETHUSDT,...`; the service answers and forgets. That
is deliberate: this API has no auth and no rate limiting, and its entire
security argument is that a compromise yields only what your terminal already
prints. A write endpoint — even one holding four strings — trades that away.
`tests/test_api.py::test_the_api_stays_read_only` asserts the handler still
implements nothing but GET and OPTIONS.

A pair the exchange does not list comes back with `listed: false` rather than
a row of dashes. That case is real: `PEPEUSDT` does not exist as a perpetual
because the listed contract is `1000PEPEUSDT`.

## Alerts are transitions, never states

`/api/alerts` exists to wake a phone, and the fastest way to make a
notification worthless is to send it too often. So "the recommendation is
FLAT" is a state and is never sent; "it just became BUY" is a transition and
is sent once. Filings dedupe on their filing id, calendar events on the lead
window that fired, an intra-bar spike on the bar it happened in.

Two details that are load-bearing:

**The cursor is epoch milliseconds, not an ISO timestamp.** A `+00:00` offset
in a query string has its `+` decoded as a SPACE, so the cursor arrived
unparseable, and a handler that falls back to "return everything" then
re-delivers the entire backlog on every poll — forever. An integer has no such
edge, and an unparseable cursor returns **nothing** rather than everything.

**A first call with no cursor returns nothing.** Otherwise opening the app
fires twenty notifications about filings from last week, which teaches you to
swipe the app's alerts away without reading them.

Every alert carries when the underlying thing *happened* alongside when it was
noticed. For an SEC filing those differ by days.

`newsfeed/schedule.py` reads the FOMC calendar from federalreserve.gov —
official, no key, published years ahead, with statement times resolved through
a real timezone because half the meetings are EST and half EDT. BLS blocks
scripted access, so CPI and payrolls go in `data_cache/calendar.json` by hand.

No writes, no orders, no keys, no auth. If the process were compromised the
worst outcome is that someone learns what your terminal already prints — which
is also why it must never grow a write endpoint without authentication first.

## Where the app disagrees with the mockup

The design is followed closely. Three places it is not, and in each the
backend wins:

- **RECOMMENDED ACTION.** The mockup shows a confident `BUY`. The card shows
  whatever `evaluate()` decided — usually `FLAT`, because EV after costs does
  not clear the threshold. `tests/test_api.py` asserts a waiting backend can
  never render as BUY.
- **BOT ACTIVE → WATCHING.** Nothing places an order.
- **Data by TradingView → Binance.** Where the bars are from.

The mockup's **NOT TRAINED** badge needed no change — models exist for BTCUSDT
and nothing else, so it was already true. Tapping an untrained pair opens
Training instead of a dashboard with no honest probability to show.

## What is absent, and stays absent until it is real

No auth: there is no account server, so the login fields are a local label and
nothing is transmitted. No billing: the Pro panel is the design, with no store
product and no entitlement server behind it. Both say so on screen rather than
looking functional.

---

---

# Whale & insider tracking

```bash
python3 monitor.py --watchlist      # who is tracked, and why
python3 monitor.py                  # alerts appear alongside news
```

Alerts use the same shape as the news block:

```
 *** WHALE ACTIVITY DETECTED ***
   Werner Ryan D. (SVP, CAO) SELL $778,751 of RIOT
   whale move is BEARISH
   SELL
   (traded 2026-08-05, disclosed 69h later  code S  conviction 0.50)
   NOTE: filings are lagging evidence, not a trigger
```

## Source: SEC EDGAR — free, official, exact

Form 4 gives the officer's name, exact share count, exact price, exact date,
filed under legal obligation. It is the best "important person traded" data
available at zero cost. No API key. 17 entities, every CIK resolved from
SEC's own ticker map rather than typed from memory — a wrong CIK doesn't
error, it silently returns another company's filings forever.

Ranked by `crypto_proximity`: how much an entity's own trading says about
**crypto** rather than about its own share price. MSTR 0.95, MARA 0.80,
COIN 0.55, BLK 0.30.

## Size is not conviction

The single most common way this data is misread. In a real week, the two
largest transactions on the watchlist were both **code F** — shares withheld
automatically to pay tax on vesting:

```
$2,995,245 RIOT SELL   code F  conviction 0.00  ->  MECHANICAL - NOT A VIEW
$1,412,051 XYZ  SELL   code F  conviction 0.00  ->  MECHANICAL - NOT A VIEW
$  778,751 RIOT SELL   code S  conviction 0.50  ->  BEARISH
```

A naive tracker headlines the first as *"RIOT EXECUTIVE DUMPS $3M"*. Nobody
decided anything. `conviction` encodes the transaction code:

| code | meaning | conviction |
|---|---|---|
| P | open-market purchase | 1.00 — they chose to buy |
| S | open-market sale | 0.50 — often a scheduled 10b5-1 plan |
| M | option exercise | 0.00 — mechanical |
| A / F / G | award, tax withholding, gift | 0.00 — no view |

## Two limits that cannot be engineered away

**It lags.** Form 4 is due within two business days; measured lag on real
filings was 24–120 hours. 13F is 45 days stale by law. You see the transfer
after it lands.

**It is gamed.** Anyone who knows their wallet is watched can move funds to
manufacture a signal. Named wallets are the most-watched objects in crypto.

Which is why alerts say *"lagging evidence, not a trigger"* and the analyses
below them are **unchanged** by a filing. It is reported, not traded on.

## What is deliberately absent

No X/Twitter (API pricing out of reach, scraping breaches ToS), no
TradingView ideas (no API, scraping breaches ToS), no anonymous wallet
labels (needs paid Arkham/Nansen, and the labels are guesses that get gamed).
Whale Alert returns 401 without a paid key — the adapter is there if you buy
one.

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
agent3/              news (19 cols)
  config.py          Agent3Config — windows, decay, safety lag
  schema.py          the 19-column contract
  prompt.py          extraction prompt + JSON schema + injection hardening
  scorers.py         ClaudeScorer / LexiconScorer / CachedScorer
  features.py        point-in-time decay-weighted aggregation
  agent.py           NewsAgent, NewsOutput
newsfeed/
  events.py          NewsItem, NewsScore, observable_at   <- read first
  store.py           append-only JSONL store + PIT queries
  sources.py         Binance announcements, JSONL replay
predict.py           read the current signal from a frozen model
whalefeed/           SEC insider & treasury tracking
  watchlist.py       the tracked entities, CIKs verified
  edgar.py           Form 4 -> who bought/sold and how much
  events.py          WhaleEvent + conviction + PIT clocks
livefeed/            live data collection (does not trade)
  store.py           append-only bar store; refuses forming bars
  klines.py          poll/stream + mandatory REST gap-fill
  news.py            scheduled news poll, stamps ingested_at
  collector.py       supervisor: threads, signals, restart-on-crash
agent5/              the judge - the only block that learns
  config.py          barriers, CV geometry, costs, risk limits
  labels.py          triple barrier + uniqueness weights   <- read first
  splits.py          PurgedKFold: purge + embargo
  dataset.py         assemble blocks -> X, y, weights
  model.py           logistic + LightGBM, out-of-fold predictions
  calibration.py     isotonic: score -> defensible probability
  decision.py        EV + fractional Kelly + hard risk caps
  metrics.py         evaluation, shuffle test, warnings
  ablation.py        does each block earn its place?
  agent.py           JudgeAgent
agent4/              order flow & positioning (22 cols)
  config.py          Agent4Config — thresholds, windows
  schema.py          the 22-column contract
  tape.py            histogram -> flow, large prints, size distribution
  positioning.py     open interest, liquidations
  netflow.py         provider protocol; no paid client bundled
  agent.py           FlowAgent, FlowOutput
marketdata/
  binance.py         klines
  aggtrades.py       trade tape -> per-bar size histograms   <- read first
  derivatives.py     open interest, liquidations
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

The harness and the judge described here exist and run in production. What is
still open — and what was tried and closed — is kept current in
`research/QUANT.md`: the quarter-hour opening order imbalance on 4h, an
open-interest flush reversal rule, and spot-led versus perp-led flow are next
in line.
