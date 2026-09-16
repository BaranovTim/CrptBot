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
