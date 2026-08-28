# TradingBot — detector agents

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
python3 run_tests.py            # 184 tests, no network needed
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
- **Long only.** `k_up=2, k_dn=1` is the payoff geometry of a *long*. A low `p`
  does **not** imply a profitable short — for a short those barriers are the
  wrong way round. Shorting needs its own model on mirrored labels.

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

## The ablation answers the N/W/P/I question

You don't compute those weights — you measure what breaks when a block is
removed:

```
added     feats     AUC  spread   delta
regime        7  0.4235  0.0478
agent2       33  0.4194  0.0290 -0.0041
agent1       66  0.4666  0.0418 +0.0473
agent4       88  0.4304  0.0279 -0.0362
```

A block that doesn't move out-of-fold AUC hasn't earned its complexity, its
runtime, or — for Agents 3 and 4 — its data bill.

## Calibration is what makes a percentage publishable

Raw model output is a score, not a probability. Isotonic regression on
out-of-fold predictions learns `0.72 → 0.61`, and 0.61 is what gets shown. The
property purchased: **every time it says 61%, roughly 61% of those trades
should win.** That's the entire justification for putting a number on a screen
— and given the plan's legal notes about advertising accuracy, the only
defensible way to publish one.

Measured on the real run: calibration error **0.096 → 0.045**, Brier
**0.2273 → 0.2208**.

## Training is batch. Predicting is real-time.

`fit()` and `predict_proba()` are separate, and `predict_proba()` raises if no
model has been frozen. A model that updates its weights on every incoming tick
is a model chasing noise — that's the failure mode behind "learn from errors"
in the original notes. Inference is arithmetic on frozen coefficients:
microseconds.

---

---

# The live collector

```bash
python3 collect.py                     # BTCUSDT 1h + news, poll transport
python3 collect.py --once              # one cycle, then exit (good for cron)
python3 collect.py --status            # what is stored, and any gaps
python3 collect.py --repair            # re-fetch missing bars
python3 collect.py --interval 1m --transport stream
```

Records closed bars into `data_cache/live/` and news into `data_cache/news/`.
**It does not trade.** Collection is kept separate from decision-making so you
can restart it without touching a strategy — and it has to run *before* forward
paper trading, because you cannot forward-test on data you never captured.

## Three silent failures it exists to prevent

**The forming bar.** In live mode the last row off any feed is the candle
currently being built — its `close` is just the current price and keeps
changing. A backtest never sees such a row, so storing it makes every derived
feature differ live, with nothing raising. `drop_unclosed()` sat in the
codebase unused for weeks; `BarStore.append` now refuses the write rather than
trusting the caller. Verified against live Binance: REST returned the 14:59 bar
at 14:30 and the store rejected it.

**The invisible gap.** A Binance websocket drops roughly once every 24 hours —
documented behaviour, not a failure. Reconnecting without backfilling leaves
one hole per day, and nothing surfaces it: the frame still loads, the agents
still run, and every `bars_since_*` feature quietly understates elapsed time.
So every reconnect triggers a REST backfill, and `find_gaps()` reports holes
that slipped through.

**The lost clock.** Agent 3's entire point-in-time defence rests on
`ingested_at` — when *we* saw an item, not when it claims to have happened. A
backfilled corpus can never recover it: download three years of headlines today
and every one was "ingested" today. Running this collector from now on is what
makes future news research honest, even though it does nothing for the past.

## Two transports

| | |
|---|---|
| **poll** (default) | REST on a timer. Zero extra dependencies. For 1h bars: ~24 requests a day against a 2400/min weight budget — measured weight of **2** per cycle. Cannot silently half-work. |
| **stream** | Websocket. Push, sub-second latency, right answer for 1s/1m bars or many symbols. Needs `pip install websockets`. |

Both reconcile against REST after every cycle, so the guarantee is identical.
Rate limiting is **weight-based, not request-count** — the collector reads
`X-MBX-USED-WEIGHT-1M` from every response and throttles itself before Binance
does it with a 429 and then an escalating IP ban.

## Feeding it back into the agents

```python
from livefeed import BarStore
bars = BarStore("BTCUSDT", "1h").load()     # same shape the agents expect
```

Store is append-only and monthly-partitioned CSV, so a month is easy to
inspect, delete or re-fetch. Restarting the process loses nothing.

---

---

# Getting the percentage

Three commands, three different jobs. `collect.py` never prints a signal —
that is not what it is for.

| command | what it does | how long |
|---|---|---|
| `collect.py` | records closed bars, forever | runs until stopped |
| `main.py --judge --save-model ...` | trains and freezes a model | minutes, once |
| `predict.py` | reads the probability | milliseconds |

```bash
# 1. train once on downloaded history (the live store will not have
#    enough bars for weeks)
python3 main.py --judge --save-model output/judge.joblib --start 2024-01-01

# 2. read the signal
python3 predict.py --history
python3 predict.py --watch          # re-read as each bar closes
```

```
[2026-08-25 23:59:59.999+00:00] Agent 5
  calibrated probability 30.2%
  barriers  TP +1.69%  SL -0.84%
  EV after costs -0.178%  (threshold +0.05%)
  full Kelly 0.000 -> 0.25 Kelly = 0.00% of equity
  DECISION: FLAT - EV below threshold after costs
```

**Training and prediction are separate commands on purpose.** Training is
batch and offline; prediction is arithmetic on a frozen function. A model that
refits on every incoming bar is a model chasing noise — the plan is explicit
that "training and predicting in real time" is the wrong shape.

## What the percentage actually means

Not "chance BTC goes up". It is: **the probability that a long entered at this
bar's close reaches +k_up ATR before −k_dn ATR, within max_hold bars.** Tied to
specific barriers and a specific holding period, which is why they are printed
next to it. The same 30% against different barriers is a different statement.

And the probability is *not* the decision. A 61% chance with a good payoff is a
trade; the same 61% with a bad one is not. EV after costs decides — which is
why the output shows the arithmetic rather than just a number.

---

---

# Watching inside the bar

```bash
python3 monitor.py                       # intra-bar watch is on by default
python3 monitor.py --no-spikes           # off: only re-read on bar closes
python3 monitor.py --spike-atr 0.5       # more sensitive
python3 monitor.py --no-provisional      # resolved barriers only, never re-read
```

Between closes the monitor used to be blind. On 1h bars that is up to 59
minutes in which a 4% move is invisible and the analysis on screen keeps
quoting an entry price that stopped existing forty minutes ago.

The status line now carries the forming bar, refreshed every `--poll` seconds:

```
  08:06 to close   79,825.00   +0.97%   +1.78atr   vol 3.6x   tkr 0.59   rsi 57   4h up   watching news...
```

Left of `rsi` is the **forming** bar and moves second to second. `rsi` and
`4h` describe the last **closed** bar and are frozen until the next close.
They are ordered that way on purpose — an RSI printed beside a live price
invites you to read it as current, and it is not.

## Two kinds of statement, and one is much stronger

A spike prints both, kept visually apart because they are not equally load-bearing:

| | |
|---|---|
| **RESOLVED** | price reached a barrier. An observation. No model, no features, no forming bar fed to anything — and it does not care what the odds said. |
| **provisional** | the models re-read with the forming bar treated as closed. **Uncalibrated**, never recorded. |

The second one needs the warning it carries. Both models were fitted on
closed bars, and every closed bar spans a full interval. A forming bar does
not: at minute 10 of an hour its high, low and volume describe ten minutes,
and the ATR, RSI and structure computed over it are all shifted to match. The
isotonic map that makes "61%" mean *61 times in 100* was fitted on out-of-fold
predictions over whole bars only, so it does not apply here.

So the provisional number is a **staleness warning with a number attached**,
not a better forecast. It says the anchored read has drifted; it does not
replace it. It is never stored, never journalled, and never counted in any
accuracy statistic. The distortion shrinks as the bar fills — at minute 55 of
60 it is nearly the closed bar — which is why the elapsed fraction is printed
next to it every time.

## The threshold has to sit below the barrier

`--spike-atr` defaults to **0.75** and the trained barriers are at **1.0 ATR**.
That gap is the whole point. At 1.0 the alert would fire exactly when the
barrier is touched — by then the window is decided and there is nothing left
to warn about. A warning has to arrive first.

Retrain with different barriers and this needs revisiting. `monitor.py` reads
`k_up` / `k_dn` off the loaded models at startup and says so rather than
letting the mismatch pass quietly.

## Three triggers, because one misses too much

| trigger | default | catches |
|---|---|---|
| `MOVE` | 0.75 ATR from the last close | the move that got there however slowly |
| `JOLT` | 0.60 ATR in 5 minutes | a fast move that then retraces, which `MOVE` never sees |
| `VOLUME` | 3.0x the usual pace | size arriving before price moves — often absorption |

Thresholds are in **ATR, never percent**, so the same config means the same
thing at 40k and at 100k. A percentage threshold silently stops firing in a
calm regime and never stops firing in a violent one.

They are **definitions, not parameters** — the same rule as every
`Agent*Config`. Tune them on how often you want to be interrupted. The moment
one is picked because it made money it is fitted, and fitted things belong to
Agent 5 where they can be cross-validated.

## Cheap measurement gates the expensive re-read

One REST call per poll costs weight 1 — about 3 a minute against a 2400/min
budget. A 107-feature recompute over 23,000 bars costs ~1.7s. So the cheap
thing runs on the timer and only a crossed threshold pays for the recompute —
and the re-read is skipped entirely when both windows already resolved, which
makes the most violent case also the cheapest.

The **re-arm** is what keeps this readable. Without it a genuine 3-ATR move
prints an alert block every 20 seconds. Price must travel another 0.5 ATR, or
back, before the same bar can alert again. Verified live: one alert, then four
quiet heartbeats while the move persisted.

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

1. The evaluation harness — triple-barrier labels, purged CV with embargo,
   uniqueness weights. Until it exists you cannot tell whether any of these 100
   columns is worth keeping.
2. Agent 5, block by block: regime only → +agent 2 → +agent 1 → +agent 4 →
   +agent 3. Each step is one full run through the harness. That sequence *is*
   the ablation, and it is how you get `N, W, P, I`.

Start with 10–15 features, not all 100. After uniqueness weighting your
effective sample size is a few thousand independent observations, and 100
features on 3,000 effective samples will confidently find patterns that are
not there.
