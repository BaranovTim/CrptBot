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
