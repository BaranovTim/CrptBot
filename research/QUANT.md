# Quantitative trading, and what of it belongs in Vanth's recommended action

Written 2026-09-16. The question asked was "analyse quantitative trading fully
and apply it to how the recommended action is decided". This is the analysis;
the experiments it led to are in this directory and their status is at the end.

## 1. What the field is

"Quant" is not one strategy. It is the discipline of stating a trading rule
precisely enough to test it on history without fooling yourself, then sizing
it by risk rather than conviction. The strategies themselves fall into a small
number of families, and every fund runs a mix:

| family | the bet | horizon | crypto evidence |
|---|---|---|---|
| **Time-series momentum / trend** | what went up keeps going, for a while | days–months | strong at 1–4 weeks ([Liu & Tsyvinski 2021](https://www.nber.org/system/files/working_papers/w25882/w25882.pdf); [Liu, Tsyvinski & Wu 2022, J. Finance](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13119)) |
| **Cross-sectional momentum** | rank coins, long the top, short the bottom | weeks | 4.2%/week in large coins, nothing in small ([LTW 2022](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3379131)) |
| **Short-term reversal** | yesterday's move partly undoes itself | 1–3 days | reversal in illiquid coins, *continuation* in the largest ([Dobrynskaya-type evidence, 2021](https://www.sciencedirect.com/science/article/pii/S1057521921002349)) |
| **Carry** | be paid to hold; fade extremes | days | funding-rate extremes mean-revert; delta-neutral carry is a real business ([Coinbase primer](https://www.coinbase.com/learn/perpetual-futures/understanding-funding-rates-in-perpetual-futures)) |
| **Lead–lag / market factor** | the index moves first, the rest follow | minutes–hours | BTC Granger-causes alts; lag grows with illiquidity ([high-frequency evidence](https://link.springer.com/article/10.1007/s10690-026-09589-z)) |
| **Volatility management** | size positions by 1/variance | any | raises Sharpe in most equity factors ([Moreira & Muir 2017](https://www.nber.org/system/files/working_papers/w22208/w22208.pdf)); *mixed* for crypto momentum ([FMPM 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)) |
| **Statistical arbitrage** | pairs, cointegration, mean reversion of spreads | hours–days | needs a portfolio and both legs executed; not a single-coin phone call |
| **Market making / HFT** | earn the spread | milliseconds | needs colocation; out of scope |
| **ML on features** | let a model find the mix | any | this project |

Two results underpin all of it and are worth stating plainly, because both
were tested here this week:

* **A payoff ratio creates no edge.** Under a random walk every take-profit /
  stop-loss geometry has the same expectation (zero before costs). The edge a
  geometry *demands* is `(cost + threshold) / span`; the split of the span does
  not appear in it. `agent5/barriers.py` derives this; `research/four_r.py`
  and `two_r.json` measured it: 188 refits at 4R and 2R, none profitable.
* **A stop-loss only helps under momentum.** Kaminski & Lo (2014): under a
  random walk a stop reduces expected return; it adds value only when returns
  have momentum, and hurts under mean reversion. A stop is a claim about the
  process, which is why the model's time window matters as much as its level.

## 2. The process discipline (López de Prado, *Advances in Financial ML*)

The part of quant that is not about strategies is about not lying to yourself.
What Vanth already does, and what it does not:

| technique | purpose | in Vanth |
|---|---|---|
| Triple-barrier labels | label = the trade's outcome, not a direction | ✓ `agent5/labels.py` |
| Uniqueness weights | overlapping labels are not independent samples | ✓ |
| Purged K-fold + embargo | training never sees a label that overlaps the test window | ✓ `agent5/splits.py` |
| Shuffled-label control | catches leaks the CV missed | ✓ every fit; the gate rule |
| Isotonic calibration on OOF | a probability that means what it says | ✓ |
| EV after costs, Kelly fraction, hard cap | probability → position, written by hand | ✓ `agent5/decision.py` |
| Pooled fits on a shared time axis | small-sample timeframes borrow strength | ✓ daily only (this week) |
| **Meta-labelling** | a second model that decides *whether to act* on the first's signal; the [Hudson & Thames study](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/) reports precision 0.48 → 0.54 | ✗ |
| **CUSUM event sampling** | train on bars where something happened; fewer near-duplicate rows | ✗ (`sample_every` only; the config notes it as "a later upgrade") |
| **Fractional differentiation** | stationary features that keep memory | ✗ (tree models need it less) |
| **Deflated Sharpe / PBO** | correct a chosen result for how many were tried ([Bailey & LdP 2014](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)) | ✗ — and this week's "1 row in 188 at 2.2σ" is exactly the case it exists for |
| **Bet sizing by averaging active signals** | overlapping calls sized together, not each at full Kelly | ✗ (each timeframe sizes alone) |
| **Forecast combination** (Carver) | weighted, capped average of several rules; vol-target the result | ✗ (h1/h2 are picked, not combined; timeframes never talk) |

## 3. What the judge actually sees today

Measured on the cached BTC 1h training frame:

* `agent1` 33 chart-structure columns, `agent2` 26 indicator columns — all
  looking back **≤ 200 bars, most ≤ 20**.
* `agent4` 22 flow columns, of which **4 are populated** in the shipped
  `--no-tape` fit (taker ratio, open-interest change ×2, OI/price divergence).
* `regime` 7 columns, of which **`funding_z` has been NaN in every model ever
  trained** — `compute_frames` never loaded a funding series.
* Nothing from any other coin. The pipeline is single-asset by design, so BTC
  — the factor that explains most of an altcoin's hour — is invisible to an
  ETH or DOGE model.

Against the table in §1: the judge has no multi-week momentum, no reversal
term measured against volatility, no carry, no market factor, no lead-lag,
no breadth, and its volatility state is one percentile column. Those are the
documented effects; the model cannot learn a signal it is not shown.

## 4. What is being applied, in order

Each is a controlled experiment on the same purged CV and the same shipped
geometry; the only change is the one named. "Worked" means the AUC clears the
shuffle by more than the fold spread **and** the book under the shipped rule
improves by more than its error bar. Results are appended below as they land.

| # | change | where | why first |
|---|---|---|---|
| E1 | **Quant feature block** — TSMOM 1d–4w as t-stats, 1-bar/1-day reversal, vol ratio, Donchian / drawdown, BTC lead-lag + relative strength + beta, funding carry (rate, 3-day mean, 30-day z), breadth | `agent5/quant.py`, `marketdata/funding.py`, `research/quant_block.py` | the largest documented effects, absent from the features; cheap; changes the action through the model rather than around it |
| E2 | **Meta-labelling** — secondary model on "was the primary's side right", filter calls by its probability | `research/meta_label.py` | the literature's most direct answer to "fewer, better calls" |
| E3 | **Volatility-state gating** — split the book by `vol_pctile`; if the top tercile loses, WAIT there | from E1's output | zero cost; Moreira–Muir's mechanism as a filter, not a scaler |
| E4 | **Cross-timeframe agreement** — 1h call only when 4h's calibrated side agrees | `research/agreement.py` | Carver's combination in its simplest form; the app already shows all four |
| E5 | **CUSUM sampling** | `agent5` config | fewer duplicate rows; may sharpen calibration |
| E6 | **Seed bagging** — average 5 LightGBM seeds | `agent5/model.py` | stabler p, cheaper "strong" threshold |
| E7 | **Deflated verdicts** — the gate counts trials | `core/timeframes.py` | honesty, not action; stops a 1-in-188 row from shipping |

Not being applied, and why: cross-sectional long/short (needs a portfolio and
both legs; the app recommends one coin at a time), pairs/stat-arb (same),
market making (latency), HMM regimes (a fitted state is a look-ahead unless
refit inside every fold — E3 does the honest version with a causal percentile).

## 5. Results

All numbers are out-of-fold on purged CV, five coins (BTC, ETH, SOL, DOGE,
XRP) per coin on 1h/4h, fifteen coins pooled on 1d, calibration cross-fitted
in time, P&L on realised outcomes (target, stop, or the mark at the time exit)
less a 0.10% round trip. Raw output: `results/quant_block.json`,
`results/hold_curve.json`, `results/four_r.json`, `results/two_r.json`.

### The measurement that matters more than any experiment

The shipped models' own book, scored this way for the first time:

| timeframe | window | AUC vs shuffle | hit rate, "strong" calls | gross P&L / trade | **net of fees** |
|---|---|---|---|---|---|
| 1h | h1 (1 bar) | 0.53 / 0.50 | 54.0% | +0.012% | **−0.088%** |
| 1h | h2 (2 bars) | 0.52 / 0.50 | 52.2% | +0.010% | **−0.090%** |
| 4h | h1 | 0.52 / 0.50 | 52.1% | −0.002% | **−0.102%** |
| 4h | h2 | 0.51 / 0.50 | 50.4% | +0.012% | **−0.088%** |
| 1d pooled | h2 (10 days), top decile | 0.52 / 0.49 | 53.5% | +0.40% | **+0.30% ± 0.11** |

The AUC gate is not wrong — the shuffle control sits at 0.50 and the models
sit above it — but what the 1h/4h models know is the *sign of the next bar's
drift*, and 56% of 1h trades end at the time exit with a move of ~0.29%. The
barrier touches carry three times that, and on those the models are at
chance. A probability that is right about small moves and blind to large
ones has an AUC above 0.5 and a book of zero. Fees then make it negative.

Sanity checks on the bookkeeping (`ETH 1h`, same code): perfect foresight
+0.46%/trade; a random p −0.10%/trade (the fee, exactly); a p that is 55%
right on barrier touches and knows nothing else −0.01%. At a ±0.8% barrier
and 0.10% fees the touches must be called at ~57–58% to make money.

### E1 — quant feature block: **no lift**

| timeframe | AUC base → +quant | verdict |
|---|---|---|
| 1h h1 / h2 | 0.527 → 0.528 / 0.521 → 0.524 | inside the fold spread on every coin |
| 4h h1 / h2 | 0.522 → 0.523 / 0.511 → 0.514 | same |
| 1d pooled h1 / h2 | 0.508 → 0.496 / 0.519 → 0.511 | slightly worse, spread doubled |

Permuting the block costs the fitted model 0.01–0.02 AUC, as much as agent2,
so the model *uses* it — and the out-of-fold score does not move. The
information is redundant with what agent2 already carries at these horizons.
The documented 1–4-week momentum effect is a statement about weekly returns;
a 1–2 bar barrier label does not resolve it. The block and the funding loader
stay in the tree, tested and unwired.

### E3 — volatility-state gating: **nothing to gate**

| | calm third | middle third | wild third |
|---|---|---|---|
| 1h h1, net/trade | −0.092% | −0.092% | −0.096% |
| 4h h1, net/trade | −0.093% | −0.120% | −0.098% |
| 1d h2, net/trade | −0.088% | −0.206% | +0.119% |

No regime at 1h/4h is profitable; the daily pattern is within noise.

### Hold-window curve at the shipped ±1 ATR: **the edge is one to two bars deep**

| 1h window | 2 (shipped) | 4 | 8 | 16 | 32 |
|---|---|---|---|---|---|
| AUC | 0.521 | 0.499 | 0.496 | 0.496 | 0.499 |
| "strong" net/trade | −0.090% | −0.150% | −0.268% | −0.203% | −0.081% |

4h: AUC 0.500–0.504 at 4/8/16 bars, all books negative. Whatever the
features know about direction is gone after two bars. Keeping an entry open
past the window does not collect more of it.

### 4R / 2R geometries (research/four_r.py): **no**

188 refits. At 4R, 21 of 30 1h books and 22 of 30 15m books lose by more than
their error bar; 4h nets +0.13%/trade on the top 5% but only on the long side
of XRP and DOGE in their 2024 rallies (+0.006% without them). 2R: identical
picture, 0 of 30 positive 1h books. Written up in the chat of 2026-09-15.

### Not run, with the arithmetic for why

* **E2 meta-labelling, E4 cross-timeframe agreement** — both are *filters*.
  A filter on a signal whose gross edge is +0.01%/trade can at best keep the
  +0.01%. The daily model's +0.30% over ten days is 0.0025% per two-hour
  slice, so "1h agrees with 1d" inherits nothing worth a fee either.
* **E5 CUSUM, E6 bagging** — both make p *steadier*; neither adds gross edge.
  Worth doing when there is an edge to steady.

### Targets and stops on the structure (research/structure_levels.py, 2026-09-20)

The objection: every TP/SL the app shows is `entry ± k·ATR` — the same
percentage for every trade on a coin that day, blind to the previous swing,
the equal highs, yesterday's range. 2R/4R stretched the same blind barriers.
This puts them on agent1's own causal levels (confirmed swings, PDH/PDL,
equal-high clusters, the last leg's 1.0/1.618 extensions) and relabels.

| 4h, 16-bar hold, 5 coins × 2 sides | tp/sl (ATR) | AUC / shuffle | top-10% net | books + / sig+ / sig− |
|---|---|---|---|---|
| ±1 ATR (reference, same labeller) | 1.00/1.00 | 0.512 / 0.489 | +0.01% | 5/10, 4, 2 |
| target in front of level, stop beyond | 0.73/1.07 | 0.581 / 0.494 | +0.08% | 7/10, 3, 0 |
| **target at level, stop at level** | 0.83/0.83 | 0.577 / 0.494 | **+0.12% (top-5% +0.22%)** | 8/10 (10/10), 3 (5), 0 |
| target at the second level | 1.48/1.07 | 0.585 / 0.493 | +0.21% | 9/10, 4, 0 |

**What holds out of time** (fit < 2025-09-20, score the year after, threshold
from the training half's OOF, scrambled control on the identical recipe):
the *ranking* does — test AUC 0.55–0.64 against controls at 0.46–0.56 on
every row, mean 0.584 vs 0.511. The *book* mostly does not: a training-
quantile threshold selects zero calls on half the rows (score drift), and
where it fires the "at" placement nets +0.04% mean (BTC short +0.35% ± 0.16
on 72 calls, DOGE long −0.35%), with the whole test year hostile to longs
(all-rows long books −0.17 to −0.45%).

Reading: the levels are right and the model knows it — "reaches the next
swing before breaking the last one" is far more predictable than "moves
1 ATR", and stays so in an unseen year. Turning that into money needs (a)
a decision rule that is a rank cut with a drift-robust threshold, not the
EV-on-calibrated-p rule, which loses on every structure row; (b) confirmation
on the other ten coins; (c) the honest expectation that the edge is small
(+0.04% to +0.2% per 4h trade after fees), not the +0.2% the in-sample cell
suggests.

### Shipping structure on 4h: the served rule, replayed out of time (2026-09-20)

Before shipping, the exact rule `monitor.py` serves — rank the score against the trailing 540 scores, call from the top 15/10/5% — was replayed on the held-out year (`research/structure_rules.py` over `structure_holdout.py --dump`), beside the current ±1 ATR 2-bar model under the identical protocol:

| 4h, 5 coins × 2 sides, Sep 2025 → Sep 2026 | AUC / control | hit rate | timeouts | top-decile net/trade | books sig− |
|---|---|---|---|---|---|
| ±1 ATR, 2 bars (current) | 0.620 / 0.504 | 36% | 34% | −0.07% | 0 |
| structure, 16 bars, rolling rank | 0.584 / 0.511 | 58% | 4% | −0.04% | 0 |
| structure, EV-on-p rule (the ATR models' rule) | — | 32% | 4% | −0.15% | 4 |
| every bar (the year itself) | — | 47% | 3% | −0.13% | 6 |

The year was hostile to the label (every bar −0.13%, longs −0.24%). Neither model made money in it; the structure model turned a losing year into break-even with a 58% hit rate and the target/stop on the chart's levels, and it is the better predictor. **That, not profitability, is the claim it ships on.** Longer trailing windows (1500, 4000 bars), absolute-p cuts and rank×p combinations were all replayed: none beat the 540-bar rank; the top 3% (+0.07%) is a tail not tuned to. Funding rate as a feature: no effect (0.582 vs 0.584).

What ships: `agent5/structure.py` (levels + labeller, pinned to the research code), `Agent5Config.geometry/side`, `core.GEOMETRY = {"4h": "structure"}` with h1 = long model and h2 = short model on a 16-bar hold, `monitor._evaluate_structure` (rank decision, strong/medium/small = top 5/10/15%), per-slot verdict gating (`model_usable(slot=)`, which also closes the daily h1 leak), side-aware levels, the app's 4h horizon at 64h.

### What the best public traders do (research/trader_patterns*.py, 2026-09-20)

A year of fills for 54 Hyperliquid addresses (top-400 by 30-day profit, discretionary-looking): 11,083 episodes, 3,983 position trades. Report: `research/results/trader_patterns.md`.

- **The leaderboard ranks variance.** 21 of 54 are net losers over the year; median 4 liquidations; top-5 trades = 73% of gross profit for the median trader.
- **Profitable vs losing traders differ in winner size, not win rate** (59% vs 57%; avg win +6.2% vs +2.9%; hold 28h vs 15h; resting-order entries 36% vs 21%).
- **Entry setup is identifiable**: profitable longs buy high-volume, high-volatility dips (below EMA20, lower third of the 24h range, near PDL, RSI 45), 14:00–17:00 UTC, *fewer* headlines than baseline. A classifier on our features predicts "a profitable trader enters in the next hour" at AUC 0.64–0.68 on BTC/ETH/HYPE (clears shuffle).
- **Copying the setup loses**: the classifier's top decile as an entry → −0.34%/24h vs +0.08% every bar. The +1.4%/1h after their resting fills is the limit-order effect (fill 1.2% below the next 15m close); from a follower's price it is 0.
- **What persists**: traders profitable on position trades in H1 (≥5 trades) → their H2 entries, from a follower's price, **+1.52%/24h (se 0.41), both sides, 7/9 traders, 8/10 coins**, vs +0.34% drift. 166 trades, 9 traders — a real but thin result. It says: select on a half-year record of position trades, not the 30-day board; the smartmoney tracker's selection should change to that.

### Smart money as a model input vs as confluence (research/smart_feature.py, 2026-09-21)

Causal cohort (followed = ≥8 position trades, positive, 50–92% win in the trailing 180 days, from fills before each day), three features per 4h bar (net entries 24h / 72h, exposure), joined to the 4h held-out scores.

- **As a feature: nothing.** Non-zero on 9.6% of bars; alone AUC 0.501; logistic on score+feature 0.580 vs score 0.581.
- **On the model's own calls (rank ≥ 0.85): a lot.** Agrees +0.59%/trade at 72% (n=121 bar-rows); silent −0.18% at 56%; disagrees −0.41% at 50% (n=181). Same ordering on all three definitions.
- Daily (research/smart_daily.py, trailed entries, one year): a 24h window lands on 5% of calls (unusable); 72h on 15%. Agree (42 calls): trail +10.7%, half-out +7.1%; silent (~800): −0.2%/−0.3%; disagree (50): −1.5%/−0.35% — not worse than silence, and the sign flips between longs and shorts. A followed trader's entry ALONE, trailed on daily: longs +5.8% ±2.6 (n=122), shorts +2.7% ±1.5 (n=94), median negative, ~2σ — not acted on.
- Shipped as an overlay (`api/service._smart_overlay`, SMART_OVERLAY): 4h 24h window, agree raises / disagree lowers; 1d 72h window, agree raises / disagree only annotates: agreement raises the strength one level, disagreement lowers one, a small call it contradicts is withdrawn; never creates a call. One held-out year, ~300 rows with a signal — measure live.

### Bitcoin as context for the altcoins (research/btc_context.py, 2026-09-21)

No model reads any coin but its own. Tested: a BTC block (momentum 4h/24h/7d, EMA20/50/200 distance, RSI, vol percentile, above-200, 7d range position, structure state vs its last confirmed swings) plus relative strength (coin − BTC 24h/7d, rolling beta/corr) added to each alt's 4h structure models, fit < 2025-09-20, scored after. **26 models: test AUC +0.0001 ± 0.002, better on 11/26; calls net −0.02% → −0.09%.** Null. At 4h the alt's own bars already contain the BTC move (lead–lag is minutes), and the label is the alt's own level race.

As a RULE: the daily long gate on BTC's 200-day instead of the coin's own — same edge sliced differently. 2025→26 trailed: own +4.8% (n=55), BTC +5.5% (51), both +8.4% ±4.8 (32); 2024→25: own +5.1% (433), BTC +3.8% (519), both +6.0% ±2.5 (386). "Both" is ~+1–3%/trade better in each year at ~0.5–1σ with 15–40% fewer trades. Not changed; a candidate if a third year agrees.

## 6. What this says about the recommended action

Applying the field's discipline to this system yields one change and one
confirmation:

1. **The gate should read the book, not just the AUC.** A timeframe whose
   realised P&L after fees is negative out-of-fold should not emit calls,
   whatever its shuffle test says. Today that is 1h and 4h. This is E7, and it
   is a product decision — it would turn most of the "calls right now" list
   into WAIT and leave the daily model as the caller — so it is proposed, not
   shipped.
2. **The daily model is where the money is**, and only in its most confident
   decile: ~one call per coin every ten days, +0.30% ± 0.11 net per trade at
   ±8% barriers. A "strong-only" daily setting is the honest product.

What would change the 1h/4h picture: not features (tested), not windows
(tested), not geometry (tested), not regime filters (tested). Lower fees
would not either — the gross edge is +0.01%. Those timeframes need
information the pipeline does not have (tape and depth, which the ablation
switched off for cost) or they are information-only screens.

## Sources

* Liu, Tsyvinski — *Risks and Returns of Cryptocurrency* (2021), [NBER w25882 PDF](https://www.nber.org/system/files/working_papers/w25882/w25882.pdf)
* Liu, Tsyvinski, Wu — *Common Risk Factors in Cryptocurrency*, J. Finance 77 (2022): [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13119), [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3379131)
* *Up or down? Short-term reversal, momentum, and liquidity effects in cryptocurrency markets* (2021): [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1057521921002349)
* *Price Transmission from Bitcoin to Altcoins: High-Frequency Evidence* (2026): [Springer](https://link.springer.com/article/10.1007/s10690-026-09589-z)
* Moreira, Muir — *Volatility-Managed Portfolios* (2017): [NBER w22208 PDF](https://www.nber.org/system/files/working_papers/w22208/w22208.pdf); crypto caveat: [FMPM 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)
* Bailey, López de Prado — *The Deflated Sharpe Ratio* (2014): [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
* Singh, Joubert — *Does Meta-Labeling Add to Signal Efficacy?*: [Hudson & Thames](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/)
* CUSUM event sampling and overlapping labels: [Hudson & Thames notes](https://hudsonthames.org/machine-learning-trading-essentials-part-2-fractionally-differentiated-features-filtering-and-labelling/)
* Carver — *Systematic Trading* (forecast combination, capping, vol targeting): [CXO summary](https://www.cxoadvisory.com/big-ideas/a-few-notes-on-systematic-trading/)
* Funding rates on perpetuals: [Coinbase](https://www.coinbase.com/learn/perpetual-futures/understanding-funding-rates-in-perpetual-futures)

## Looking for what was never asked (2026-09-22)

Everything above tests a change to the *features* or to the *geometry*. This
round asked a different question — what has the project never tried at all —
and two of the answers are larger than anything in the table above.
Reproduce with `research/pooled_4h.py` and `research/rule_probes.py`.

### The one that matters: pool the 4h models across coins

Pooling is what turned the daily model from "0 of 15 beat their shuffled
control" into the shipped swing strategy. **On 4h it was simply never tried.**
Same features, same structure barriers, same 16-bar hold, same purged CV,
same shuffled control, same rank rule — the only change is that fifteen
coins are fitted together on one shared 4h-bar axis.

| 4h, 15 coins × 2 sides | fit < 2024-09-20, scored 2024-25 | fit < 2025-09-20, scored 2025-26 |
|---|---|---|
| CV AUC / shuffled control | 0.589 / — | 0.594–0.599 / **0.500** |
| out-of-time AUC | 0.597 / 0.603 | 0.604 / 0.590 |
| served rank rule, net per trade | **+0.255% ± 0.043** | **+0.095% ± 0.037** |
| pooled score ≥ 0.60, net per trade | **+0.476% ± 0.074** | **+0.550% ± 0.075** |
| pooled score ≥ 0.65 | +0.881% ± 0.153 | +0.707% ± 0.155 |
| best 1 of the 30 rows per bar | +0.238% ± 0.082 | +0.441% ± 0.083 |
| coins positive (score ≥ 0.60) | 9/15 | 9/15 |
| months positive | 6/13 | 7/13 |

The shipped per-coin models on the same 2025-26 year: AUC 0.584, rank rule
**−0.046% ± 0.051**. Pooling moves that to +0.095% over fifteen coins
(+0.008% restricted to the same five, so most of the gain is the ten coins
that were never measured — and the short side carries it: +0.169% ± 0.059
pooled against a losing per-coin book).

**Two independent years, both positive on every rule, control at 0.500.**
That is a stronger replication than anything else in this file. Three
cautions before it becomes a claim: the σ figures share this project's
overlapping-label optimism (16-bar holds, consecutive bars selected — the
effective sample is far smaller than `n`); only about half the months are
positive, so the mean is carried by a minority of them; and the fifteen
coins are today's universe, which is survivorship of a kind.

**Why an absolute threshold suddenly works.** Per-coin, a fixed cut selected
nothing on half the rows as the score distribution drifted — which is why
the served rule ranks each coin against its own trailing window. A pooled
model has ONE score distribution, so `p ≥ 0.60` means the same thing on
every coin, and the coins become comparable to each other. That is what
makes "which of the thirty is the best trade right now" expressible at all:
`best 1 per bar` is positive in both years, and it is one position at a
time rather than a threshold that can fire on nine coins at once.

### The served rule is blind to the payoff it is being offered

The structure label asks "does the target come before the stop". How far
each sits varies five-fold bar to bar — reward:risk runs 0.46 at the 10th
percentile to 2.16 at the 90th, break-even hit rates of 68% and 32% — and
the decision rule never looks at either. Measured on the shipped models'
held-out year:

* **R:R alone ranks the label at AUC 0.667**, against the model's own 0.584.
  The single most predictive thing about the label is not a feature, and
  part of what the AUC gate has been crediting to the model is it
  re-deriving the geometry from correlated columns. Within a fixed R:R
  tercile the score still separates (AUC 0.536–0.565), so there is real
  skill underneath — less than the headline.
* Filtering for *good* R:R makes the book worse (−0.145% at R:R ≥ 1.0). The
  only positive cell is the opposite: a **near target with a structurally
  distant stop** (R:R < 0.4) hits 81% and nets +0.114%, and combined with
  cross-sectional selection it is positive in both halves of the year
  (+0.113% / +0.672%, whole +0.361% ± 0.145). Counter-intuitive, fragile,
  and worth a proper test rather than a shipping decision.
* It also explains a live oddity: DOGE on 2026-09-21 printed "BUY, STRONG"
  with a diagnostic EV of **−2.97%** — a +1.2% target against a −6.7% stop.
  The rank said yes; nothing in the rule looks at the payoff.

### Measured and closed

* **Fitted models do not go off within a year.** First four months of the
  held-out year AUC 0.600, last four 0.610. The month-to-month P&L swings
  (−0.58% to +0.82%) are regime, not decay. Retraining monthly is housekeeping,
  not a fix — this had been assumed to be a process gap and it is not.
* **The two side models never contradict each other**: 0 bars of 10,148
  where both call. They are near-complements; there is no free filter there.
* The shipped rule's whole-year "break-even" hides −0.257% in the first half
  and +0.198% in the second.

### Still untested, ranked by what the evidence above suggests

1. **Barrier geometry as a feature** (`tp_pct`, `sl_pct`, their ratio, the
   level's kind and age). The model is asked whether it reaches a target
   without being told how far away it is. Evaluate on money, not AUC: the
   obvious failure mode is learning "near targets get hit".
2. **A label made of money.** Train on the realised R multiple, or weight
   samples by |outcome|. §5's own diagnosis was "right about small moves,
   blind to large ones", and sample weighting is the direct answer to it;
   nothing here has ever optimised anything but a coin flip.
3. **Meta-labelling**, dismissed above because a filter on a +0.01% gross
   edge keeps +0.01%. Against a pooled book of +0.5%/trade that arithmetic
   no longer holds.
4. **The missing placement cell.** "Target at the level" was tested with
   "stop at the level", and "target in front" with "stop beyond". *Target at
   the level, stop beyond it* — the liquidity-sweep story, where the stop
   sits exactly where every other stop is — was never run.
5. **Entry placement.** Everything here enters at the bar close, at taker
   cost. `trader_patterns` measured resting fills at +1.4%/1h, and maker
   fees are half of taker — the cost floor is what killed 1h and 15m.
6. **The tape is empty.** 22 of Agent 4's columns exist, 4 are populated,
   and every shipped fit is `--no-tape`. Order-flow imbalance and CVD are
   among the few microstructure effects with a real literature; they have
   never met the structure label.
7. **Cross-sectional features inside the pooled model** — a coin's strength
   relative to the pooled universe, which is the one crypto factor §1 rates
   as strong and which a single-asset pipeline cannot express. BTC context
   was null *per coin*; this is a different object.
8. **Bet sizing across simultaneous calls** (Carver / LdP). When nine coins
   call at once that is one market-wide move, and the app sizes each at full
   quarter-Kelly independently.
9. **Deflated Sharpe.** This file now contains a great many trials, today's
   included. The 7.3σ above is a *pre-deflation* number on a rule chosen
   after seeing the year; the second-year replication is what it rests on.

## The walk-forward laboratory: what to build next (2026-09-22)

`research/wf4h.py`. Everything before this section fits once and scores one
held-out year. That found pooling; it is not enough to decide what to build.
The lab refits every six months on its own past only and scores the next
six — six disjoint out-of-time half-years, Sep 2023 → Sep 2026 — with the
rank computed exactly as the server computes it (against the same model's
trailing 540 scores, in-sample ones included). Variants were **chosen on the
first four windows and only confirmed on the last two**. And results are
reported as an **account**: at most three positions, never two on one coin,
enter at the close, exit when the label resolves, 0.10% fees and — for the
first time here — the funding a perpetual pays or receives while held.

About thirty variants went through it. The protocol earned its keep once:
the stop placement that won development (1 ATR beyond the level, Sharpe
1.91) lost on the holdout (−0.10).

### First, a flaw in how this ledger has been scoring itself

**Every per-trade P&L above this line is uniqueness-weighted.** The weight
belongs in *training* — it stops overlapping labels counting as independent
samples — but a label's uniqueness depends on how fast it resolved, which
depends on how it resolved, so as a P&L weight it leans toward winners. On
pooled 4h calls: weighted mean +0.564%, **plain mean +0.273%**. Every trade
costs the same fee whatever its weight. The lab reports plain means; older
numbers in this file should be read as roughly doubled.

A second, smaller gap: a selected *bar* is not a *trade*. Consecutive
selected bars are one position; a signal that fires once and disappears
loses −0.56% while the fifth consecutive top-ranked close nets +0.61%. The
portfolio simulation is the number to trust.

### The account, half-year by half-year

Full exposure (three positions, each a third of the account, 1×), fees and
funding paid, non-compounded:

| system | Sep 23 | Mar 24 | Sep 24 | Mar 25 | Sep 25 | Mar 26 | total | positive |
|---|---|---|---|---|---|---|---|---|
| **shipped**: a model per coin, per-coin rank | −2.8% | −63.8% | +3.6% | −70.5% | +16.9% | −15.8% | **−132.5%** | 2/6 |
| pooled model, same rank rule | +16.9% | +45.5% | +35.9% | −41.2% | +18.3% | +14.8% | +90.2% | 5/6 |
| pooled, **pooled rank ≥ 0.97** | +35.5% | +28.1% | +29.3% | −29.3% | +25.8% | +48.1% | +137.6% | 5/6 |
| … + positioning block | +65.0% | +18.3% | +77.4% | −33.4% | +28.8% | +30.1% | +186.1% | 5/6 |
| … + **stop 0.5 ATR beyond the level** | +18.6% | +31.5% | +73.9% | +0.8% | +21.8% | +24.6% | +171.2% | **6/6** |
| … + both | +66.5% | +20.4% | +62.4% | −4.8% | +26.7% | +28.0% | **+199.2%** | 5/6 |

The last two columns chose nothing. At a third of that exposure the best
rows are roughly +20% a year with a ~12% drawdown; the holdout drawdown at
full exposure for the combined system was 15%.

What a user sees as accuracy moves with it: calls on the shipped system hit
their target 55–57% of the time; the pooled system with the stop beyond
the level hits **69–73%**. AUC: 0.554 / 0.571 → 0.607 / 0.611.

### Why each piece, from the lab

* **Pooling** beats a model per coin in *every one* of the six windows
  (AUC 0.578–0.624 against 0.526–0.603). The learning curves say why: the
  model is data-hungry. Training on the last 12 months gives 0.568, the
  last 24 gives 0.580, all history 0.599 — old data is not stale. Coins
  saturate faster: scored on the same five, training on 5 → 0.585, 10 →
  0.602, 15 → 0.604. Past ten, more coins buy *choice*, not accuracy.
* **The pooled rank.** A pooled score means the same thing on every coin,
  so each close can be ranked against every coin's recent scores instead
  of its own. At 0.97 (top 3%) it beats the per-coin rank on the same
  scores: Sharpe 0.65 / 1.63 against 0.50 / 0.57.
* **The stop beyond the level.** Every angle on the data says tight
  structural stops get swept. Reward:risk ≥ 1 trades lose; sizing every
  trade to the same risk (which gives tight stops the biggest positions)
  turns Sharpe 0.65 / 1.63 into 0.03 / 0.48; and moving the stop 0.25–0.5
  ATR past the swing is positive in both halves with lower drawdowns. At
  1 ATR it overfits.
* **Positioning** — top traders' and all accounts' long/short ratios and the
  taker ratio, from the metrics archive this project has downloaded for
  years and discarded all but open interest from. It does not raise AUC,
  but the fitted model gives it **9–10% of its total gain on day one**
  (`pos_crowd`, the retail long/short ratio, is a top-10 feature), and it
  improved the account in 8 of 10 rule comparisons. Mild, consistent, free.

### Closed, with the reason

| tried | result | why |
|---|---|---|
| the trade's geometry as features | AUC 0.600 → **0.673**, money **worse** | the model learns "near targets get hit" — they hit often and pay little |
| EV = p·target − (1−p)·stop, with a geometry-aware p | Sharpe −1.09 (dev) | EV hunts the largest payoffs, where calibration is worst |
| regress the realised return instead of classifying | dev +0.56%/trade, holdout **−0.53%** | money targets are heavy-tailed and regime-bound; the binary label denoises |
| label weighted by the money at stake | dev strong, holdout collapses | same |
| one model for both sides | no change | |
| cross-sectional features (return / vol ranks, breadth) | no change | redundant with pooling itself |
| bigger trees; fixed 300 / 800 trees | no gain | early stopping at 76–107 trees is right |
| training on recent data only | worse | see the learning curve |
| sizing to equal risk per trade | much worse | it sizes up the losing tight-stop trades |
| funding | ~0.01% per trade | real, and negligible at these holds |

**The app's own sizing is inverted.** `monitor._evaluate_structure` sizes a
call with quarter-Kelly from p and the payoff. Kelly grows with
reward:risk, so the largest size goes to the tight-stop trades and the
near-target, wide-stop ones — where the edge is — sit at the 1.25% floor.
Equal size per call is better on every row above.

### What the model is

A location-in-structure model: `position_in_range` alone is ~20% of the
gain, then distance to the 200-EMA and to yesterday's high and low.
Chart structure and indicators are 81–86% of everything. **26 features are
never used** — every tape column (the shipped fits are `--no-tape`, so they
are empty), `funding_z`, the candle counts, the head-and-shoulders
direction.

### Caveats that stay attached to the numbers

The fifteen coins are today's universe (survivorship). Shorts need a
futures account. Stops are assumed to fill at the level; a gap through one
fills worse. Two holdout half-years give an annualised Sharpe an error of
about ±1, so the ranking *among* the pooled rows is soft; what is not soft
is the gap between every pooled row and the shipped system, in every window.

### Shipped (2026-09-22)

Items 1, 2, 3 and 5 of the plan above; positioning (item 4) is not in yet —
it needs a live feed from Binance's futures REST endpoints.

* `train_pooled_4h.py` — one fit per side over the fifteen coins, stop
  `STOP_BUFFER_ATR = 0.5` past its level, installed under every coin's 4h
  slots with a `source: "pooled"` verdict. First fit: AUC 0.609 / 0.610
  against shuffled controls at 0.497, 150,560 samples.
* `Agent5Config.stop_buffer_atr / rank_pool / equal_size_pct`, read by
  `agent5.structure.stop_beyond` and `monitor._evaluate_structure`. A model
  pickled before these fields reads as the old model (no buffer, no pool).
* `monitor.SCOREBOOK` (`data_cache/scorebook.v1.json`) — every closed-bar
  read of a pooled model records its coin's trailing 540 scores, per side;
  every read ranks against all coins' (`POOLED_RANKS`: strong 0.97,
  medium 0.95, small 0.90; no rank until 8 coins have reported).
  `train_pooled_4h.py` fills it, so the first ranking after an install is
  against a full pool.
* Pooled calls are sized equally and carry no EV (it misread near targets).
* `train.py` now refuses to overwrite a pooled 4h slot (`--allow-per-coin-4h`
  to override). **Retrain with `train_pooled_4h.py`**, every six months.

## Round three: exits, entries, the big traders, new families (2026-09-22)

`research/improve_4h.py` (exits and entries, replayed bar by bar on the live
configuration's walk-forward calls — it first reproduces the label P&L on
99.97% of 6,002 calls), `research/positioning.py`, `research/new_strategies.py`.
Same protocol: six half-years, chosen on the first four, confirmed on the
last two, scored as the lab's account (three positions, fees, funding).

### What the big traders do that the system did not

From a year of Hyperliquid fills (`results/trader_patterns.md`): profitable
and losing traders have the same win rate (57–59%) and the same average
loss (~5.7%); the profitable ones' winners are twice the size and held twice
as long, and they enter with resting orders far more (36% vs 21%) — entries
that were +1.36% an hour later where their market orders were +0.03%.
So the two untested places were the exit and the entry.

**The entry is the improvement.** A resting order 0.5 ATR better than the
signal's close, good for 4 bars (16h); once filled, the stop keeps its
distance from the entry and the target stays on its level:

| 4h entry | win | per trade | Sharpe dev / hold | max DD dev / hold |
|---|---|---|---|---|
| market at the close (live) | 71% | +0.24% | 1.59 / 1.14 | 40% / 19% |
| **resting 0.5 ATR / 4 bars, stop moves with it** | 63% | **+0.66%** | **2.87 / 2.99** | 30% / 20% |
| … price must trade 0.05 ATR through the order | 62% | +0.52% | 2.22 / 2.34 | 32% / 22% |
| resting, same stop and target | 55% | +0.53% | 2.70 / 2.88 | 23% / 15% |
| resting, stop and target both move | 73% | +0.37% | 2.17 / 1.93 | 25% / 31% |

It beats the market entry in all six half-years (+316% against +171% in
total at full exposure; +263% under the strict fill). 30–40% of calls never
fill — the price ran without dipping — and are not traded. The same-stop
version was the first found and **collapsed under a strict fill** (1.54
dev): its edge was catching the exact low. The pick does not depend on that.

**Exits: only one thing helped, and not in combination.** Moving the stop to
entry halfway to the target (Sharpe 1.96 / 2.24 alone) is worse on top of
the resting entry (1.88 / 2.27): a better entry reaches halfway sooner and
scratches trades that would have won. Trailing past the target, half-out
and trail, trailing only, and the second level as target all **lost on the
development windows** even where the holdout looked spectacular (+155%/yr
for trail-past-target) — the protocol rejects them, as it rejected the
1-ATR stop. No time exit and an 8-bar exit were worse.

### Binance's top traders

* **As an overlay on the calls:** calls with top traders net on the same
  side netted more per trade in both periods (+0.55% vs +0.33–0.37%), but
  as a filter or a strength shift it lowers the account Sharpe (fewer
  trades). Context to show, not a rule.
* **As a signal across coins it loses.** Long where top traders are most
  long, short where least: Sharpe −1.19, positive in 1 of 11 half-years.
* **The crowd is contrarian.** All accounts' long/short z-score orders
  3-day relative returns the wrong way in 9 of 11 half-years; long the three
  coins it leans most short against, short the three it leans most long:
  +31.6%/yr market-neutral, Sharpe 1.12, 7 of 11 half-years.
* The **Hyperliquid 24h overlay still holds** on the pooled calls: agree
  +0.74% (80% hit), silent +0.16%, disagree −0.34% (57%) — small samples,
  same order as when it shipped. Kept.

### New strategy families

| strategy | %/yr | Sharpe | since Sep 2023 | positive half-years |
|---|---|---|---|---|
| **30-day momentum rotation**, 3 long / 3 short, weekly | +47.4% | **1.24** | 1.54 | **11/12** |
| … on the 10 coins established by 2021 (survivorship check) | +32.9% | 0.99 | 1.05 | 10/12 |
| … long-only best 3 of those ten, over holding all ten | +29.8% | 0.72 | 1.04 | 9/12 |
| crowd contrarian, market-neutral, every 3 days | +31.6% | 1.12 | 1.54 | 7/11 |
| 7-day momentum | −2.6% | −0.04 | | 9/12 |
| funding extremes (cross-section, or fading \|z\|>2) | ~0 | 0.00 | | 3/8 |

30-day momentum is the strongest new result, and its mirror (30-day
reversal) loses 58%/yr, which is the shape a real effect has. Some of the
15-coin figure is survivorship — the list is today's — which is why the
2021 subset is shown. **As an overlay on the 4h calls it orders per-trade
returns monotonically in both periods** (with +0.66/+0.68%, middle
+0.43/+0.25%, against +0.39/+0.23%) but moves the account by nothing
measurable; it is a strategy of its own, weekly, not a tweak to 4h.

### Closed

Bar-close hour and weekday (flip between periods); Bitcoin's 200-day trend as
a 4h gate (dev Sharpe 2.70 → 1.99; the holdout liked it, the protocol does
not); following Binance's top traders; funding rates.

#### Correction: the resting-entry figures above are optimistic (2026-09-23)

The table above scores every call's order independently, and the account
simulation then picks trades in SIGNAL order among the ones that eventually
filled. A real account cannot do that: when an older order fills later than
a newer one, the newer one is already the position. Replayed with a policy a
person can actually follow — `monitor.resting_orders`: one order resting at
the newest call's price, one position at a time, the same code the server
runs — the gain is real but smaller:

| 4h entry | Sharpe dev / hold | per trade | total, six half-years | beats live |
|---|---|---|---|---|
| market at the close (live) | 1.59 / 1.14 | +0.24% | +171% | — |
| **one resting order, newest price** | **2.22 / 2.22** | **+0.53%** | **+246%** | 5 of 6 |
| … price must trade 0.03 ATR through | 1.65 / 2.01 | +0.40% | +197% | 4 of 6 |
| a ladder (every call's order open, first fill wins) | 2.12 / 2.20 | +0.50% | | |
| only the first order of each run of calls | 2.57 / 1.44 (lab scoring) | | | |

The ladder is no better than one order at the newest price, so the simpler
instruction ships. Re-quoting matters: acting only on a call's first order
gives up most of the holdout gain, which is why the app notifies when the
order price moves.

#### Correction: momentum's figure depended on the rebalance day (2026-09-23)

The rotation above rebalanced every 42 bars from the start of the data — one
arbitrary phase of the week. Rerun at all 42 phases (every 4h slot of the
week), the effect is real but smaller than quoted:

| weekly rotation, 3 long / 3 short | Sharpe across 42 phases |
|---|---|
| 30-day momentum, 15 coins | **median 1.00**, 0.62 – 1.31, positive at every phase |
| 30-day, the 10 coins established by 2021 | median 0.70, 0.41 – 1.10, positive at every phase |
| 30-day, long-only best 3 of those ten, over holding all ten | median 0.42, 0.10 – 1.04 |
| 15-day momentum | median 0.83, 0.33 – 1.59 |
| 60-day momentum | median 0.24, −0.15 – 0.75 |
| 30-day, held two weeks | median 0.71, −0.19 – 1.31 |

The quoted 1.24 was a lucky phase. Monday 00:00 UTC, which the app uses
because it is the natural time for a person, was historically the weakest
(0.62); nothing about a weekday should matter to a 30-day signal, so the
median is the expectation.

### Shipped (2026-09-23): the resting entry and the momentum rotation

* **Resting entry on pooled 4h calls** — `Agent5Config.entry_offset_atr = 0.5`,
  `entry_valid_bars = 4`, written into the installed models by
  `train_pooled_4h.py --update-rules` (no refit; the pool and its score book
  stay). `monitor._resting_decision` replays each side's recent calls through
  `monitor.resting_orders` once per sensitivity level, so the card, the
  payload's `order` block and the calls list speak for the order a person on
  that level actually holds: open (BUY/SELL with the limit and expiry),
  filled (WAIT, in the trade), or ended at this close (a note). The forming
  bar can fill an order or close its trade between closes
  (`service._settle_order`). Notifications: the entry names the order; a
  persisting call that moves the order notifies; an unfilled expiry says so.
* **Momentum rotation** — `api/momentum.py`, `GET /api/momentum`, a Market
  panel, a Monday alert (`kind: momentum`, mutable in Profile).
* **The OOM loop behind yesterday's evening restarts** — the daily trader
  selection ran as a thread in the API and peaked above a gigabyte; it now
  runs as a child process (`smartmoney.run_select`) that asks the kernel to
  kill it first, reading fills slimmed to the eleven fields the stats use.

## Round four: what big players leave behind, the noise floor, and an honest rotation (2026-09-23)

`research/footprints.py` (feature blocks), `research/decision_4h.py` (order
handling), `research/universe.py` + `research/momentum_pit.py` (the rotation
on every perpetual Binance has listed), `improve_4h.served` (any variant's
calls traded exactly as the server trades them -- `monitor.resting_orders` --
which reproduces the shipped figures, 2.22/2.22 and +246%, to the digit).
Same protocol: chosen on the four development half-years, confirmed on the
two holdout ones. Two outside surveys (other bots' measured accuracy; how
big players trade) were checked source by source before use -- figures
marked unverified below were not used in the app.

### First, the noise floor

The shipped 4h model, refit with nothing changed but LightGBM's seed:

| seed | Sharpe dev / hold (served) | six half-years |
|---|---|---|
| 7 (shipped) | 2.22 / 2.22 | +246% |
| 11 | 1.82 / 1.40 | +185% |
| 13 | 1.81 / 2.64 | +235% |
| 17 | 2.33 / 1.59 | +232% |
| 19 | 2.35 / 1.71 | +238% |
| **mean** | **2.11 / 1.91** | **+227%** |

A single fit's Sharpe moves by ±0.3 (dev) and ±0.5 (holdout) on the seed
alone -- the top 3% of a rank is exactly where fit noise lands. Every
single-seed row in this file should be read against that, and several
earlier "wins" of 0.2-0.4 were inside it. From here on, candidates were
compared BAGGED against BAGGED.

### What shipped from this round

* **Seed bagging** (`Agent5Config.bag_seeds`, `agent5.model.SeedBag`): the
  final fit under five seeds, averaged. **2.42 / 1.97, +252%** -- above the
  average seed on both periods and above every seed on the total. It is
  variance reduction, not a new edge; its value is that the live system no
  longer depends on which seed it drew. Five times the trees, so the server
  now loads each distinct model file once and shares it
  (`monitor.load_shared_judge`): 10 pairs cost 19 MB instead of 84 MB.
* **Orders good for 6 bars, not 4.** Re-tuned on the served policy (4 was
  picked on the optimistic replay). Dev improved for all four models tried
  (+0.23 to +0.44), holdout for three of four (−0.03 to +0.18), totals +17
  to +38 points. 8 bars: no better on dev, worse on holdout. The offset
  stays 0.5 ATR: 0.25, 0.75, 1.0 and a limit AT the level were all worse.
  Together: **2.65 / 1.94, +269%**, max drawdown 33% / 15%.
* **The live record** (`api/ledger.py`, `/api/record`, the Market panel):
  every order the server places, per sensitivity, and how each trade ends,
  net of fees -- beside the walk-forward's expectation per level. Orders
  placed before a model's install time are excluded (their ranks rest on
  the back-filled, in-sample score book).
* **Fill and trade-end notifications.** A fill turns BUY into WAIT and a
  trade ending turns WAIT into FLAT; neither is a call's entry or exit, so
  neither ever notified. Now both do.

What a person taking EVERY strong call should expect (per trade, no cap on
open positions, net): win 61%, +0.44% overall; **58%, +0.16% on the last
two half-years**. The account figures above hold at most three positions
and did better per trade (+0.43% holdout) -- a pattern this round could not
pin to crowding (returns by number of open trades flip sign between models).

### Closed this round

| tried | result | why it is closed |
|---|---|---|
| order flow from the klines (net taker share 1-18 bars, divergence from price, trade count, trade size) | AUC ±0.005; served 1.80 / 1.90 | inside the noise floor; the pooled model already reads location, and whole-bar flow adds nothing to it |
| sweeps of the trade's own levels (depth, flow on the sweep bar, reclaim) | 2.42 / 1.81 | inside the noise; Agent 1 already flags sweeps |
| estimated liquidation clusters from open interest | AUC **+0.03-0.04 in every window**, money 0.90 / 1.34 | the geometry leak again: mass "between the close and the target" grows with the target's distance |
| the same, only mass within ±2 ATR (no geometry) | 1.90 / 2.42 | inside the noise |
| higher-timeframe levels (last week's/month's high and low, week/month open, round numbers) | single seed 2.39 / 2.45; **bagged 2.04 / 2.02 vs 2.42 / 1.97** | the single-seed win was the seed |
| Coinbase premium (BTC's, each coin's, relative), bagged | 1.85 / 1.37 | worse; the one outside check of it also found ~55% |
| recency-weighted training (half-life 2 years) | 2.06 / 1.49 | worse, as the learning curve predicted |
| the previous refit's model blended in | 2.09 / 2.08 | an average seed |
| cancel an unfilled order once the target trades | 2.50 / 1.84 | worse |
| cooldown after a stop (6 or 12 bars) | 2.63 / 1.96, 2.47 / 1.83 | null / worse |
| cancel an order when its rank drops below 0.5 / 0.8 | 2.57 / 2.00, 2.49 / 1.89 | null |
| exit a trade on the opposite side's call | bag 2.76 / 2.13; three other models dev −0.08 to +0.02, hold +0.09 to +0.12 | dev flat; not adopted as a rule (the stay/close advice already says it) |
| round-number-aware targets (pull the target in front of a round number) | win rate **+1 to +3 points**, total +250% / +229% vs +269% | buys hit rate with money -- the clearest example that "more accurate" and "pays more" are different things |
| "medium" instead of "strong" in the three-position account | total higher on 5/5 models, Sharpe better on 2, worse on 2 | more trades at the same quality, not better calls |

### The rotation, on an honest universe

`research/universe.py` rebuilt every USDT perpetual Binance has listed (844,
delisted ones included -- LUNA's collapse is in it); each Monday the
universe is what was trading most over the previous 30 days. Binance's
archive is missing five days (Feb and Apr 2022) for many coins; unfilled,
the 60-day history rule dropped SOL, XRP, NEAR and LUNA for two months, so
interior gaps of up to five days are carried (never past a coin's last
close). Median Sharpe over the seven weekday phases:

| rule | dev (2021 - Jun 2024) | hold (Jul 2024 - Aug 2026) |
|---|---|---|
| **the shipped rule: 15 coins, 3/3, 30-day** | **0.39** | **0.32** |
| 30 coins, 5/5, 30-day | 0.33 | 0.89 |
| 50 coins, 5/5, 30-day (before the gap fix) | 0.28 | 1.00 |
| 30 coins, 5/5, 15-day | 0.45 | 0.61 |
| **30 coins, 5/5, net taker flow (7 days, net of 7/30-day returns)** | **0.58** | **0.60**, every phase positive in both |
| **30 coins, 5/5, the two ranks averaged (now served)** | **0.91** | **1.09** |
| the two as separate half-size baskets | 1.01 | 0.84, every phase positive in both |
| distance to the 20-day high | 0.82 | 0.12 |
| risk-adjusted 30-day | 0.31 | 1.16 |
| residual (net of BTC beta), before the gap fix | 0.27 | 1.00 |
| inverse-vol weights / vol-managed book, before the gap fix | 0.40 / 0.05 | 0.68 / 1.06 |
| long-only, over holding the universe, before the gap fix | 0.12-0.30 | 0.35-0.74 |

**Most of the old rotation's 1.00 was survivorship**: on today's fifteen
coins the rule looked strong because they are on today's list partly for
having gone up. Grobys et al. (2025) measured the same design on the top 30
coins by market cap: +1.74%/week before mid-2020, negative and insignificant
after, with one −255% week from a short-leg coin that rose 1,400%.

**Net taker flow is the big-player footprint that paid.** Per coin inside
the 4h model it added nothing (above); across coins over a week it is the
steadiest single signal here, and its weekly returns correlate +0.05 with
momentum's -- so the averaged rank roughly doubles either. The sign was
left free and set by the development years (positive: buyers more
aggressive than the price shows lead), and 7 days was the paper's design,
not a search (14 days did better on the holdout, which cannot choose).
Worst weeks of the served rule: −15% to −18%. Served from Binance's public
daily bars for the 45 most-traded perpetuals; `api.momentum.rotation`
reproduces the research picks exactly (the 2% of weeks that differed were
ties in the averaged rank, now broken by the 15-day return).

### What other bots are measured at (checked against the source)

* Peer-reviewed ML forecasts of bitcoin 1-60 minutes ahead: **50.9-56.0%**
  right; every model negative after 0.30% round-trip costs (Jaquart, Dann &
  Weinhardt 2021). Top-100 coins beating the cross-sectional median next
  day: 52.9-54.2% (Jaquart et al. 2022; from the agent's reading, not
  re-checked).
* Copy trading, 100,236 follower outcomes on Binance/Bybit/MEXC: 97% of
  lead traders in profit themselves, **43.6% made money for followers**
  (YieldFund 2025).
* Telegram pump-and-dumps, 14,499 channels: +10% to the peak, −15% for
  whoever bought it (arXiv 2609.01176).
* 3Commas, Cryptohopper, Pionex, Bitsgap, Coinrule, HaasOnline, Kryll:
  none publishes a share of users in profit; 80-97% win rates found were
  all vendor claims. Grid bots' expected return is ~0 by construction;
  DCA bots' near-100% "success" is losing deals kept open.
* Not verified and not used: the Kaggle G-Research winning score, Token
  Metrics' and IntoTheBlock's accuracy claims, the 73% backtest-to-live
  Sharpe haircut.

Vanth's own numbers sit well above that literature (a 61% win rate on
filled 4h trades, account Sharpe ~2 out of time) -- which is the strongest
reason to watch the live record: the literature's backtest-to-live decay
would put a real Sharpe well below the backtest's.

### Big players: what was tested from the survey, and what is left

Tested and closed above: whole-bar taker flow, sweeps, liquidation maps,
round numbers, the Coinbase premium, distance to the N-day high. Checked
sources for the rest: quarter-hour opening order imbalance predicts 4-12h
returns on Binance perps (arXiv 2607.09426: significant for 4 of 6 coins,
a few basis points, mostly spanned by price-volume state); distance from
the 1-week high predicts large coins' returns (Fičura, t = 4.93); order
flow predicts the weekly cross-section (Anastasopoulos et al., J. Financial
Markets 2026 -- the exact premium not re-checked). The last one is now in
the rotation (above). Left, in order: quarter-hour imbalance on 4h (needs
1m bars or aggTrades), an OI-flush reversal rule, spot-led vs perp-led flow.

### A 70% win rate, and its price (research/win_rate.py, 2026-09-23)

The owner asked for at least 70% wins. The served rule wins 62% / 58%
(development / latest year). Every lever that buys win rate sells money;
the question is which sells least. Bagged model, served policy, every call
(3-at-once account in brackets):

| rule | win, every call | per trade, every call | Sharpe (3 at once) | 3 years |
|---|---|---|---|---|
| served now | 62% / 58% | +0.59% / +0.16% | 2.65 / 1.94 | +269% |
| target at 60% of the way, stop +0.5 ATR | 71% / 68% (71 / 70) | +0.43% / +0.02% | 1.97 / 1.25 | +186% |
| **take a third off halfway, stop to entry on the rest** | **75% / 71%** (76 / 73) | +0.36% / 0.00% | **2.33 / 1.40** | +187% |
| take half off a third of the way | 80% / 76% (81 / 80) | +0.26% / −0.06% | 2.12 / 1.77 | +150% |
| rank ≥ 0.99, half off halfway | 77% / 75% (76 / 76) | **+0.48% / +0.24%** | 1.44 / 1.37 | +90% |
| market entry (the old way) | 69% / 67% | +0.21% / −0.04% | 0.48 / 0.28 | +46% |

Chosen on development with win ≥ 70%: the third-off-halfway rule has the
best development Sharpe, and the latest year confirms 71% (73% at three at
once) -- at the cost of about a third of the three-year return. The
strictest cut with a half off is the only 70%+ rule whose latest-year
return per trade BEATS the served rule (+0.24% vs +0.16%), on 40% as many
trades. The scale-out replay reproduces the served policy exactly when
nothing is taken off.

**Shipped 2026-09-23 (the owner's choice): a third off halfway, stop to the
entry.** `Agent5Config.scale_out_part/at`, `monitor.RestingOrder` (the halfway
point is set at the fill; the stop is checked first in a bar, then halfway,
then the target), written into the installed models by
`train_pooled_4h.py --update-rules`. Re-measured through the server's own code
(`improve_4h.served(..., scale_part=1/3, scale_at=0.5)`): strong calls win
75% / 71% (73% over three years), +0.24% a trade taking every call, account
Sharpe 2.30 / 1.37; medium 73% / 70%; small 71% / 69%. A trade that takes
its third and comes back to the entry is a win and is recorded as "back to
entry", not a stop.

**A bug this surfaced:** the server runs pandas 3, whose DatetimeIndex is
microsecond-resolution, so `ScoreBook.record` stored every live reading in
microseconds (`asi8` returns the index's unit). They sorted below the
laptop's nanosecond back-fill, were trimmed, and `rank` never saw them -- the
live pool was frozen at the install's back-fill and would have emptied ~90
days later, ending all 4h calls. Fixed (`as_unit("ns")`, mixed units repaired
on load) and redeployed the same day.

## More coins: per-coin ranking, parent and child, and what "strong" means (2026-09-24)

The owner plans many more coins, and asked why ten new ones changed the
existing fifteen's results at all. They did not change how any coin is
predicted; they changed which coins got CALLED, through two choices:

* **The pooled rank.** A reading called when it was in the top 3% of every
  coin's readings, so new coins competed for that top 3%. Replaced by a
  per-coin rank (`Agent5Config.rank_scope = "coin"`, `monitor._own_ranks`,
  identical to the lab's `rank_coin` bar for bar): each coin against its own
  last 540 readings. On the fifteen it costs almost nothing -- won 73.4% ->
  72.4%, Sharpe 2.30/1.37 -> 2.19/1.35 (bagged, scale-out) -- and with ten
  more coins served, the fifteen's own trades were **identical to the trade**.
  Shipped.
* **The three-position account** used for scoring let new coins' calls take
  slots from better ones. A scoring choice, not a constraint on anyone.

The ten themselves (AVAX, LINK, LTC, BCH, AAVE, FIL, DOT, WLD, TAO, ONDO;
`research/expand_coins.py`, checked independently): the model wins 68% of
the time on them with the scale-out but loses money per trade (−0.15%,
−0.52% in the latest year). Training on 25 coins did not improve the 15
(results mixed, within the seed noise). Not served.

**Parent and child** (the owner's proposal: the shared 4h model as the
parent, a per-coin model adding each coin's own patterns), per-coin rank,
scale-out on, dev / latest year:

| | AUC by window | Sharpe | 3 years |
|---|---|---|---|
| parent only (live) | 0.60 0.60 0.62 0.57 0.63 0.60 | 2.19 / 1.35 | +180% |
| child only (a model per coin) | 0.53 0.56 0.56 0.55 0.59 0.56 | −0.13 / −0.46 | −19% |
| parent told which coin (coin as an input) | same to 3 decimals | 2.03 / 1.38 | +164% |
| 80% parent + 20% child | ≈ same | 1.38 / 1.00 | +116% |
| 50% / 50% | lower | 1.25 / 0.78 | +98% |

A coin alone has too little history: its own model memorises noise, and the
parent, told the coin, does not use it. The personal part that works is the
per-coin rank.

**"Strong" from the model's own probability** (the owner's framing). The
model's probability is fairly honest out of time (said 0.62 -> got 0.61;
said 0.72 -> got 0.66-0.69), but a fixed line drifts with the market: chance
≥ 70% gave Sharpe 0.88 / 1.43 (+96%), ≥ 72% 1.37 / 1.75 (+99%), against the
per-coin rank's 2.19 / 1.35 (+180%). Rank AND ≥ 70% looked best on the
latest year (2.05) but lost on the years that choose (1.39). So the label
stays the per-coin rank, and the card now LEADS with the model's own chance
that the target comes before the stop.
