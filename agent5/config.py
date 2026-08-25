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

    # --- regime block -------------------------------------------------------
    vol_window: int = 24            # realized-vol lookback (bars)
    vol_pctile_window: int = 720    # percentile baseline (~30 days on 1h)
    volume_baseline_window: int = 720
    trend_window: int = 48
    funding_z_window: int = 720

    def __post_init__(self) -> None:
        if self.k_up <= 0 or self.k_dn <= 0:
            raise ValueError("barrier multiples must be positive")
        if self.max_hold_bars < 2:
            raise ValueError("max_hold_bars must be at least 2")
        if self.n_splits < 2:
            raise ValueError("n_splits must be at least 2")
        if not 0 < self.kelly_fraction <= 1:
            raise ValueError("kelly_fraction must be in (0, 1]")
        if self.model not in ("auto", "logistic", "lightgbm"):
            raise ValueError("model must be auto, logistic or lightgbm")


DEFAULT_CONFIG = Agent5Config()

# the ablation order from the plan: start from the regime floor, then add
# blocks from cheapest to most expensive. each step answers "does this block
# add anything on top of everything before it?"
ABLATION_ORDER = ("regime", "agent2", "agent1", "agent4", "agent3")
