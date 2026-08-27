# Using the agents — what to remember, and what can go wrong quietly

Every agent passes its tests. That is not the same as "nothing can go wrong",
because the dangerous failures here do not raise exceptions — they return
plausible numbers that are wrong. This file is the list of those.

## The rules that apply to all four

**Feed them UTC bars indexed by `close_time`.** All four now reject a naive
index and convert a non-UTC one. Before that fix, a `America/New_York` index
passed every check and silently shifted Agent 1's Asian session on 451/500
bars and Agent 2's 4h buckets on 812 values. Nothing errored.

**Check the health report before training on anything.**

```python
from core import feature_report
print(feature_report(features, agent.warmup_bars, "Agent 4"))
```

Dead columns are not harmless. They burn a feature slot, dilute importance
rankings, and make an ablation result meaningless.

**Drop the warmup rows.** `agent.warmup_bars` tells you how many. The early
rows are not wrong, they are unformed — rolling windows have not filled yet.

**NaN after warmup is usually correct, not broken.** `dist_to_bull_ob_atr` is
NaN 57% of the time because there is not always a live bullish order block.
That is the honest answer. Do not fill it with zero — zero means "price is
standing exactly on the zone", which is the opposite.

**`bars_since_*` counts BARS, not TIME.** With a 300-bar gap in the data,
`bars_since_bos` read 24 while 306 hours had actually passed. If your data has
holes, these features lie about elapsed time. `describe_bars_problem(bars)`
warns you about gaps.

**Never tune a config value on trading outcomes.** Every constant in every
`Agent*Config` is a definition, not a parameter. The moment you pick one
because it made money, it is fitted — and fitted things belong to Agent 5,
where they can be cross-validated. Tune on distribution shape if you must.

---

## Agent 1 — patterns

**Remember:** it is the slow one. ~370 microseconds per bar, versus 2-15 for
the others, because the structure/zone/figure logic is genuinely per-bar
Python. 50,000 bars takes ~19 seconds. It scales linearly, so it is fine
offline — just do not put it in a live loop and expect millisecond latency.

**Hidden problem — `position_in_range` is not bounded to [0, 1].** Above 1
means price broke out above the dealing range. That is deliberate and
informative, but it will surprise you if you assume it is a percentage.

**Hidden problem — `confirm_bars` must equal `pivot_right`.** If you raise
`confirm_bars` higher, there is a window where a level can be broken before
the agent hears about the pivot. The code handles it (such levels are recorded
but never armed), but you lose signal for no benefit.

**Hidden problem — sessions and daily levels are cut on UTC.** If you trade a
market whose "day" is not UTC, `dist_to_pdh_atr` and the Asia session columns
are measuring something you may not mean.

---

## Agent 2 — indicators

**Remember:** it needs `volume`. MFI, CMF and OBV read it. Without it those
three columns would be silently all-NaN, so the validator now rejects the
frame instead.

**Hidden problem — `ema_spread_atr` IS the MACD line.** At the default 12/26
periods they are the same number, not an approximation. Do not add a separate
`macd_line_atr`; a perfect duplicate makes a logistic regression answer with
huge cancelling coefficients that are meaningless individually.

**Hidden problem — `macd_hist_atr` is not a direction feature.** It measures
whether momentum is speeding up or slowing down. On a strong synthetic
downtrend the MACD *line* reads -3.90 while the *histogram* reads +0.06. It is
perfectly normal for it to be positive while price falls. `schema.py` sorts
columns into `DIRECTIONAL_`, `ACCELERATION_` and `SELF_CENTRING_` groups —
read that before assuming any column's sign means "bullish".

**Hidden problem — a coarse HTF needs far more bars than `warmup_bars` says.**
`warmup_bars` covers the base timeframe (~210). With `htf_rule="1d"` on hourly
candles you need ~960 bars before the 4h/1d columns populate. Use
`required_bars(bars)`, which accounts for it. Feed a shorter window live and
those columns come back NaN while your backtest had them filled — a
backtest/live divergence caused purely by window sizing.

---

## Agent 3 — news

**Remember: scoring is offline, always.** Call `score_items()` once at ingest,
cache it, and pass the scores into `compute()`. An LLM call takes seconds; a
decision has to be arithmetic. Never let scoring happen between a bar closing
and a trade.

**Remember: the default scorer is the keyword one.** `NewsAgent()` uses
`LexiconScorer` so it runs offline with no API key. It cannot judge novelty —
the field that actually matters — and returns a constant 0.5 for it. For real
scoring pass `ClaudeScorer()`.

**Hidden problem — `news_sentiment_* == 0` and `NaN` mean different things.**
Zero means news exists and reads neutral. NaN means there was no news at all.
Filling NaN with zero teaches the model that silence is neutrality.

**Hidden problem — changing the prompt or the model changes the instrument.**
Scores from two different prompts are not comparable. If you edit
`agent3/prompt.py`, re-score the whole corpus rather than mixing old and new
scores in one dataset.

**Hidden problem — the timestamp is the weak link, not the price.** Items are
gated on `observable_at` = max(published, ingested) + safety lag, never on
`event_time`. If you write your own news source, set `ingested_at` honestly.
If you set it to the event time, you have re-introduced the exact lookahead
the whole design prevents.

**Hidden problem — news text is an attack surface.** Anyone can publish a
headline aimed at a bot. The defence is structural: the scorer's output schema
holds bounded numbers and a closed enum, so an injected instruction has
nowhere to go. Do not add a free-text or "action" field to that schema.

---

## Agent 4 — order flow

**Remember: without the optional feeds, 16 of its 22 columns are NaN.** Run it
with no aggTrades, no liquidations and no netflow and you get 4 usable columns
out of 22. Nothing errors. `main.py` now prints the dead list, but if you call
the agent directly, run `feature_report` yourself.

**Remember: `--tape` downloads a lot.** A day of BTCUSDT aggTrades is tens to
hundreds of megabytes. The downloader streams to a `.part` file so an
interrupted download leaves no corrupt zip, and uses a 600s timeout — the
original 60s timeout failed on real data.

**Hidden problem — `is_buyer_maker` is inverted by almost everyone.**
`m == True` means the buyer was the *maker*, so the **seller** crossed the
spread: it is an aggressive SELL. Invert it and every flow feature flips sign
with no error. This is cross-checked on real data against Binance's own
`taker_buy_base_volume` kline field: correlation +1.000, 100% sign agreement.
If you ever touch that line, re-run that check.

**Hidden problem — two more sign traps.** A liquidation with `side=SELL` means
a **long** was force-closed (bearish). Exchange **inflow** is supply arriving
to be sold (bearish), so the bullish-positive direction is the negative of the
flow.

**Hidden problem — "large print" is a percentile, and the definition matters
enormously.** Defined as the top 5% of dollar *volume* it collapses onto one
bucket on a heavy tail: 7 detections in 375 bars. Defined by print *count* it
fires every bar. The default (top 0.5% by count) was chosen by sweeping and
reading the feature distribution, not by intuition.

**Hidden problem — exchange netflow is a paid feed and is not included.** The
columns are NaN by default. Do not buy Glassnode/Nansen/Arkham before the
harness can tell you whether Agent 4 contributes anything — that is what the
ablation is for.

---

---

## Agent 5 — the judge

**Remember: it is the only agent that learns, and the only one allowed to.**
Every constant in Agents 1-4 is a definition. Agent 5's coefficients are
fitted from history, which is exactly why every number it produces goes
through purged cross-validation first.

**Remember: `fit()` and `predict_proba()` are separate on purpose.**
`predict_proba()` raises if nothing has been fitted. Predicting is real-time;
training is batch, offline, on a schedule. A model that updates on every tick
is chasing noise.

**Remember: read the folds, not the mean.** `0.61 / 0.49 / 0.58 / 0.51 / 0.57`
averages to 0.55 and is noise. The `spread` column is the one that tells you
whether you found anything.

**Remember: your sample is far smaller than it looks.** 1,760 samples became
287 independent observations on a real run. Overlapping labels are not
independent. The report warns when the effective count is too small for the
feature count - believe it, and start with 10-15 features rather than 100.

**Hidden problem - it is long only, and inverting `p` will not give you
shorts.** `k_up=2, k_dn=1` asks about a long. A low `p` does not mean a
profitable short, because for a short those barriers sit the wrong way round.
Shorting needs a second model fitted on mirrored labels.

**Hidden problem - the ambiguous bar.** When one bar touches both barriers,
OHLC cannot say which came first, and it is counted as a **loss**. Your win
rate is therefore slightly pessimistic by construction. That is the intended
direction of the error.

**Hidden problem - `simulate()` is not a backtest.** It reuses the label
outcome as the trade result, assuming perfect fills at the barrier. It ignores
overlapping positions, funding, partial fills and exchange downtime. Treat any
number from it as an upper bound, never as expected performance.

**Hidden problem - the model is frozen with a column ORDER.** `predict_proba`
rejects a frame missing any fitted column, but you must also keep the order
stable. Feed the agents in a different order one day and values land on the
wrong features with nothing raising.

**Hidden problem - LightGBM is optional and silently substituted.** With
`model="auto"` the agent falls back to logistic if LightGBM will not import
(on macOS this is usually `brew install libomp`). The report prints which one
ran - check it, because the two have very different overfitting behaviour.

**Re-run the shuffle test after adding any new data source.** Leakage arrives
with new *data*, not with new models. Anything meaningfully above 0.50 there
means information is reaching the model through a path other than the
features.

---

## The live collector

**Seed the store before anything else.** A fresh collector holds ~100 bars;
the detectors need about 356. Collecting that live takes days.

```bash
python3 collect.py --seed 2024-01-01     # once, ~30s for 3 years
python3 collect.py                       # then keep it current
```

Seeding uses the monthly archives, not REST pagination - 23,000 bars in about
thirty seconds. Safe to re-run: the store dedupes.

**`monitor.py` no longer needs `collect.py`.** It fetches each closed bar
itself, once per bar. Run the collector alongside it only if you also want
news collection or a second symbol recorded - the store dedupes, so both
writing the same bar is harmless.

**Network drops are survivable, and normal.** A laptop sleeping or wifi
dropping produces `[Errno 8] nodename nor servname provided` - a DNS failure
that reads like a crash but isn't. The monitor keeps running, shows
`OFFLINE - no network (N failed polls)` in the status line, and backfills
every missed bar the moment the connection returns. Verified: 10 bars missed
during an outage, all recovered, zero gaps.

**Remember: it records, it does not trade.** Run it as its own process. It has
to be running *before* forward paper trading, because you cannot forward-test
on data you never captured.

**Remember: `poll` is the default and is almost certainly what you want.**
For 1h bars it costs about 24 requests a day and measured weight 2 per cycle
against a 2400/min budget. Use `stream` only for 1s/1m bars or many symbols.

**Remember: run `--status` occasionally.** It reports gaps. A collector that
is silently failing is worse than one that is loudly down.

**Hidden problem - the forming bar is the whole reason this exists.** Any feed
hands you the candle currently being built. Storing it makes live features
differ from backtest features with nothing raising. The store refuses it, but
if you ever write your own persistence path, `drop_unclosed()` is not optional.

**Hidden problem - `ingested_at` cannot be recovered later.** If you backfill
news instead of collecting it live, every item was "ingested" on the day you
downloaded it, and Agent 3 loses the only clock that cannot be revised out from
under it.

**Hidden problem - a dropped websocket is normal, not an error.** Roughly once
a day. What matters is that the reconnect triggers a backfill. If you swap the
transport, keep that behaviour.

---

## The one thing that still cannot be checked

None of this tells you whether any feature *predicts* anything. Every test in
this repo checks that the numbers are computed correctly, honestly, and
without seeing the future. Whether they carry signal is a question only the
evaluation harness and Agent 5 can answer, and neither exists yet.

100 columns that are all correctly computed and all useless is a completely
possible outcome, and the tests here would still pass.

Agent 5 narrows this considerably - it is the harness that can finally
*answer* the question - but it does not remove it. On the real BTCUSDT run
above the answer came back "this feature set predicts nothing", and the
correct response to that is not to tune until the number improves. It is to
change the features, lengthen the history, or accept the answer.
