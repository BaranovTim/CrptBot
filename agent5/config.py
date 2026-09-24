"""Configuration for Agent 5.

Two kinds of numbers live here, and the difference matters:

  DEFINITIONS   barrier multiples, holding period, cost assumptions, CV
                geometry. These shape what question the model answers.
                Changing them changes the question, not the fit.

  FIT SETTINGS  regularisation, tree depth, learning rate. These are
                allowed to be tuned - but only against out-of-fold metrics
                inside the harness, never against a backtest P&L by hand.

Unlike Agents 1-4, this config is *expected* to interact with outcomes:
Agent 5 is the one block that is allowed to learn. The discipline is that
every number is evaluated through purged cross-validation, so the tuning
itself cannot quietly overfit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class Agent5Config:
    # --- labels: the triple barrier ----------------------------------------
    # A sample at bar t asks: from t's close, does price travel +k_up ATRs
    # before it travels -k_dn ATRs, within max_hold_bars? The payoff is baked
    # into the label, so the model predicts "would this trade have won",
    # not an abstract direction.
    k_up: float = 2.0               # take-profit distance, in ATRs
    k_dn: float = 1.0               # stop-loss distance, in ATRs
    max_hold_bars: int = 24         # vertical barrier (time-out)
    atr_period: int = 14
    # WHERE THE BARRIERS SIT. "atr": +k_up / -k_dn ATR from the close, the
    # symmetric question every model asked until September 2026. "structure":
    # the nearest confirmed swing ahead and behind (agent5/structure.py),
    # one model per SIDE because a short's target is not a long's mirror.
    # Measured: 4h AUC 0.51 -> 0.58-0.61, and it holds out of time.
    geometry: str = "atr"           # atr | structure
    side: str = "long"              # long | short; only read for structure

    # --- sampling ----------------------------------------------------------
    sample_every: int = 1           # every bar; CUSUM sampling is a later upgrade

    # --- cross-validation ---------------------------------------------------
    n_splits: int = 5
    # embargo after each test block, as a fraction of the sample count.
    # features are autocorrelated, so even non-overlapping neighbours are
    # near-duplicates; the embargo keeps them out of training.
    embargo_pct: float = 0.01

    # --- model --------------------------------------------------------------
    # "auto" = LightGBM if importable, else logistic.
    # "logistic" is the baseline the chat recommends fitting first anyway:
    # its coefficients ARE the N/W/P/I weights, and with this much noise it
    # frequently is not beaten.
    model: str = "auto"             # auto | logistic | lightgbm
    logistic_c: float = 0.1         # inverse L2 strength; small = heavy shrinkage
    max_iter: int = 2000

    # LightGBM: the anti-overfit settings from the plan. Defaults assume real
    # data volumes; tests override them downward.
    lgbm_params: dict = field(default_factory=lambda: dict(
        objective="binary",
        min_data_in_leaf=500,       # the most important knob: leaves must be big
        num_leaves=31,
        max_depth=6,
        learning_rate=0.02,
        n_estimators=2000,          # early stopping decides the real number
        feature_fraction=0.7,
        lambda_l2=5.0,
        verbosity=-1,
        seed=7,
        deterministic=True,
    ))
    early_stopping_rounds: int = 100
    # inner split for early stopping: last fraction of each training fold,
    # separated by a purge gap so the val labels do not overlap train labels
    inner_val_frac: float = 0.15

    # --- decision (stage 4-5: written by hand, never fitted) ---------------
    round_trip_cost_pct: float = 0.10   # fees + slippage, both sides, in %
    ev_threshold_pct: float = 0.05      # trade only if EV clears this after costs
    kelly_fraction: float = 0.25        # quarter Kelly - full Kelly assumes p is exact
    max_position_pct: float = 10.0      # hard cap, % of equity; the agent cannot override
    # Long only, and deliberately so. The triple barrier with k_up=2, k_dn=1
    # asks "did price rise 2 ATR before falling 1 ATR" - the payoff geometry
    # of a long. A low p does NOT imply a profitable short, because for a
    # short those barriers are the wrong way round. Shorting needs its own
    # model fitted on mirrored labels; inverting p would be free money on
    # paper and a loss in the market.
    allow_short: bool = False

    # --- the pooled 4h structure model (research/wf4h.py, 2026-09-22) -------
    # WHERE THE STOP SITS. 0 puts it ON the structural level, which is where
    # every other trader's stop sits too, and tight structural stops were
    # the losing trades from every angle measured: reward:risk >= 1 lost,
    # sizing each trade to equal risk (biggest positions on the tightest
    # stops) turned Sharpe 0.65/1.63 into 0.03/0.48. Moving the stop 0.5 ATR
    # past its level was positive in all six half-year windows. Only a stop
    # that came from a level moves; the one-ATR fallback does not.
    stop_buffer_atr: float = 0.0
    # RANKED AS A POOL. Non-empty means one fit serves every coin and its
    # score means the same thing on each, so a reading is ranked against
    # every coin's recent readings (monitor.SCOREBOOK) rather than only its
    # own coin's. The value names the fit, so two versions never share a
    # ranking.
    rank_pool: str = ""
    # THE SAME SIZE FOR EVERY POOLED CALL, % of equity. Quarter-Kelly grows
    # with reward:risk, so it put the largest positions on the tight-stop
    # trades that lose and floored the near-target, wide-stop ones -- where
    # the edge is -- at 1.25%. Equal size beat it on every rule measured.
    # The level itself is a risk preference, not a measurement.
    equal_size_pct: float = 5.0
    # THE ENTRY: a resting order `entry_offset_atr` ATRs better than the
    # call's close, good for `entry_valid_bars` bars, instead of buying at
    # the close. The stop moves with it (same distance from the entry); the
    # target stays on its level. 0 = the old market entry. research/
    # improve_4h.py + monitor.resting_orders: Sharpe 1.59/1.14 -> 2.22/2.22
    # on the walk-forward, profit per trade doubled -- the resting-order
    # habit that separated the profitable big traders from the rest.
    entry_offset_atr: float = 0.0
    entry_valid_bars: int = 0
    # SEED BAGGING: the final model refit under each of these seeds and
    # averaged (agent5.model.SeedBag). Empty = one fit, as before. On the
    # 4h walk-forward, one reseed of the live configuration moved the
    # served account from Sharpe 2.22/2.22 to 1.82/1.40: the top 3% of a
    # rank is where fit noise lands. Five seeds averaged beat the average
    # single seed on both periods and every single seed on the total
    # (research/QUANT.md, round four).
    bag_seeds: tuple = ()
    # THE SCALE-OUT (research/win_rate.py, the owner's choice for a 70%+
    # win rate): once filled, `scale_out_part` of the position comes off
    # when price has gone `scale_out_at` of the way to the target, and the
    # stop on the rest moves to the entry. A third at halfway: wins 75% /
    # 71% (development / latest year) against 62% / 58%, for about a third
    # less return over three years. 0 = off.
    scale_out_part: float = 0.0
    scale_out_at: float = 0.0
    # WHAT A POOLED MODEL'S READING IS RANKED AGAINST. "pool": every coin's
    # recent readings (a call is the top 3% of all coins). "coin": this
    # coin's own last 90 days only -- so adding or removing a coin cannot
    # change another coin's calls. Measured on the 15 (bagged, scale-out):
    # pool 73.4% won, Sharpe 2.30 / 1.37; coin 72.4%, 2.19 / 1.35 -- and
    # with ten more coins served, the 15's own trades were unchanged to the
    # trade under "coin" (research/QUANT.md, "More coins").
    rank_scope: str = "pool"

    # --- regime block -------------------------------------------------------
    vol_window: int = 24            # realized-vol lookback (bars)
    vol_pctile_window: int = 720    # percentile baseline (~30 days on 1h)
    volume_baseline_window: int = 720
    trend_window: int = 48
    funding_z_window: int = 720

    def __post_init__(self) -> None:
        if self.k_up <= 0 or self.k_dn <= 0:
            raise ValueError("barrier multiples must be positive")
        if self.max_hold_bars < 1:
            raise ValueError("max_hold_bars must be at least 1")
        # 1 is legal and useful: it asks "does the very next bar touch a
        # barrier, and if not, did it close up or down". that is the right
        # question for a window with one bar left to run. most labels then
        # resolve by timeout rather than by touch, because a full ATR inside
        # a single bar is a large move - which is fine, it is still a real
        # forward-looking outcome.
        if self.n_splits < 2:
            raise ValueError("n_splits must be at least 2")
        if not 0 < self.kelly_fraction <= 1:
            raise ValueError("kelly_fraction must be in (0, 1]")
        if self.model not in ("auto", "logistic", "lightgbm"):
            raise ValueError("model must be auto, logistic or lightgbm")
        if self.geometry not in ("atr", "structure"):
            raise ValueError("geometry must be atr or structure")
        if self.side not in ("long", "short"):
            raise ValueError("side must be long or short")
        if self.stop_buffer_atr < 0:
            raise ValueError("stop_buffer_atr cannot be negative")
        # positive only: the monitor caps it at max_position_pct, the same
        # hard cap every other size answers to
        if self.equal_size_pct <= 0:
            raise ValueError("equal_size_pct must be positive")
        if self.entry_offset_atr < 0 or self.entry_valid_bars < 0:
            raise ValueError("the resting entry cannot be negative")
        if self.entry_offset_atr > 0 and self.entry_valid_bars < 1:
            raise ValueError("a resting entry needs at least one bar to fill in")
        if len(set(self.bag_seeds)) != len(tuple(self.bag_seeds)):
            raise ValueError("bag_seeds must be distinct")
        if not 0 <= self.scale_out_part < 1:
            raise ValueError("scale_out_part must be in [0, 1)")
        if self.scale_out_part > 0 and not 0 < self.scale_out_at < 1:
            raise ValueError("a scale-out needs a point between the entry and the target")
        if self.rank_scope not in ("pool", "coin"):
            raise ValueError("rank_scope must be pool or coin")


DEFAULT_CONFIG = Agent5Config()

# the ablation order from the plan: start from the regime floor, then add
# blocks from cheapest to most expensive. each step answers "does this block
# add anything on top of everything before it?"
ABLATION_ORDER = ("regime", "quant", "agent2", "agent1", "agent4", "agent3")
