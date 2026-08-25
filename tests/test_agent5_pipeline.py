"""Agent 5 end to end: can it find signal, and does it refuse to invent it?

A harness has to pass BOTH halves of this, and each catches the opposite
failure:

  PLANTED SIGNAL   a column that genuinely predicts the label must show up.
                   a harness so conservative it reports chance on real
                   signal is useless in a quieter way than one that leaks.

  PURE NOISE       features with no relationship to the label must score at
                   chance. this is the half that catches leakage, and it is
                   the reason the whole purging apparatus exists.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from agent5 import (
    Agent5Config,
    Dataset,
    JudgeAgent,
    build_dataset,
    decide,
    fit_calibrator,
    fit_cv,
    shuffle_test,
    triple_barrier,
)
from agent5.calibration import brier_score, calibration_error
from tests.synthetic import make_bars

# small, fast settings - the point is correctness, not tuned performance
FAST = dict(
    n_splits=4,
    max_hold_bars=12,
    lgbm_params=dict(objective="binary", min_data_in_leaf=40, num_leaves=15,
                     max_depth=4, learning_rate=0.05, n_estimators=200,
                     feature_fraction=0.8, lambda_l2=5.0, verbosity=-1,
                     seed=7, deterministic=True),
)


def _synthetic_dataset(n=2400, signal_strength=0.0, seed=0, hold=12):
    """A Dataset with a controllable amount of REAL signal.

    `signal_strength` 0.0 gives pure noise; higher values make one column
    genuinely predictive. Labels are built to overlap like real ones, so the
    purging machinery is exercised either way.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")

    driver = rng.normal(0, 1, n)                        # the "true" cause
    noise = rng.normal(0, 1, (n, 5))
    logit = signal_strength * driver
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(float)

    X = pd.DataFrame(
        {"real_signal": driver, **{f"noise_{i}": noise[:, i] for i in range(5)}},
        index=idx)

    pos = np.arange(n, dtype=float)
    t1 = np.minimum(pos + hold, n - 1)
    # overlapping labels => uniqueness weights well below 1, like real data
    weight = pd.Series(1.0 / hold, index=idx)
    return Dataset(
        X=X, y=pd.Series(y, index=idx), weight=weight, t1=t1, positions=pos,
        blocks={"regime": ["real_signal"],
                "agent1": [f"noise_{i}" for i in range(5)]},
        index=idx)


def test_finds_planted_signal():
    """A genuinely predictive column must be found. Both models."""
    ds = _synthetic_dataset(signal_strength=1.4, seed=1)
    for model in ("logistic", "lightgbm"):
        cfg = Agent5Config(model=model, **FAST)
        fit = fit_cv(ds, cfg)
        assert fit.mean_auc > 0.65, (
            f"{model} scored {fit.mean_auc:.3f} on a strongly planted signal - "
            f"the harness cannot detect real structure")
    return True


def test_reports_chance_on_pure_noise():
    """No relationship in the data must produce no score. This catches leaks."""
    ds = _synthetic_dataset(signal_strength=0.0, seed=2)
    for model in ("logistic", "lightgbm"):
        cfg = Agent5Config(model=model, **FAST)
        fit = fit_cv(ds, cfg)
        assert 0.40 < fit.mean_auc < 0.60, (
            f"{model} scored {fit.mean_auc:.3f} on pure noise - "
            f"information is leaking into the model")
    return True


def test_shuffle_test_returns_chance():
    """Shuffled labels must destroy the score even when signal was real."""
    ds = _synthetic_dataset(signal_strength=1.4, seed=3)
    cfg = Agent5Config(model="logistic", **FAST)
    real = fit_cv(ds, cfg).mean_auc
    shuffled = shuffle_test(ds, cfg)
    assert real > 0.65, "planted signal vanished before the comparison"
    assert 0.40 < shuffled < 0.60, (
        f"shuffle test scored {shuffled:.3f} - labels were shuffled, so any "
        f"score above chance is leakage, not skill")
    return True


def test_calibration_improves_probabilities():
    """Isotonic must make the numbers mean what they say."""
    ds = _synthetic_dataset(signal_strength=1.2, seed=4)
    cfg = Agent5Config(model="logistic", **FAST)
    fit = fit_cv(ds, cfg)
    y = ds.y.to_numpy(float)
    cal = fit_calibrator(fit.oof, y, ds.weight.to_numpy(float))
    assert cal.fitted, "calibrator refused to fit on a healthy dataset"

    p_cal = cal.transform(fit.oof)
    before = calibration_error(fit.oof, y)
    after = calibration_error(p_cal, y)
    assert after <= before + 1e-6, (
        f"calibration made reliability worse: {before:.4f} -> {after:.4f}")
    assert np.nanmin(p_cal) > 0 and np.nanmax(p_cal) < 1, (
        "calibrated probabilities hit 0 or 1 - an indefensible claim, and 0 "
        "breaks Kelly sizing")
    return True


def test_calibration_falls_back_safely():
    """Too little data, or one class - passthrough, never a fake confidence."""
    cal = fit_calibrator(np.array([0.4, 0.6]), np.array([0.0, 1.0]))
    assert not cal.fitted
    p = np.array([0.3, 0.7])
    assert np.allclose(cal.transform(p), p), "passthrough altered the values"

    one_class = fit_calibrator(np.random.rand(200), np.zeros(200))
    assert not one_class.fitted, "fitted a calibrator on a single class"
    return True


def test_decision_refuses_negative_ev():
    """The plan's worked example, as a test.

    Same 61% confidence: profitable with a 2:1 payoff, a losing trade with
    a 0.5:1 one. Probability alone can never be the entry rule.
    """
    cfg = Agent5Config(round_trip_cost_pct=0.1, ev_threshold_pct=0.05)
    good = decide([0.61], [2.0], [1.0], cfg).iloc[0]
    bad = decide([0.61], [0.5], [1.0], cfg).iloc[0]

    assert good["action"] == "long", "positive-EV trade was refused"
    assert abs(good["ev_pct"] - 0.73) < 1e-9, f"EV {good['ev_pct']}, expected 0.73"
    assert abs(good["kelly_full"] - 0.415) < 1e-9
    # quarter Kelly wants 10.375% of equity; the default 10% risk cap trims
    # it. worth asserting explicitly - it shows the guardrail engaging on the
    # plan's own worked example, not just on absurd inputs
    assert abs(good["position_pct"] - cfg.max_position_pct) < 1e-9, (
        f"expected the {cfg.max_position_pct}% cap to bind, got "
        f"{good['position_pct']:.3f}%")
    uncapped = decide([0.61], [2.0], [1.0],
                      Agent5Config(round_trip_cost_pct=0.1, ev_threshold_pct=0.05,
                                   max_position_pct=100.0)).iloc[0]
    assert abs(uncapped["position_pct"] - 10.375) < 1e-6, (
        f"uncapped quarter-Kelly size {uncapped['position_pct']:.4f}%, "
        f"expected 10.375%")

    assert bad["action"] == "flat", "negative-EV trade was taken"
    assert bad["ev_pct"] < 0
    assert bad["position_pct"] == 0.0
    return True


def test_position_cap_is_hard():
    """No probability, however confident, may exceed the size limit."""
    cfg = Agent5Config(max_position_pct=5.0, kelly_fraction=1.0,
                       round_trip_cost_pct=0.0, ev_threshold_pct=0.0)
    d = decide([0.999], [10.0], [1.0], cfg).iloc[0]
    assert d["position_pct"] <= 5.0 + 1e-9, (
        f"size {d['position_pct']:.2f}% exceeded the {cfg.max_position_pct}% cap")
    return True


def test_predict_requires_a_fit():
    """Inference must never trigger training."""
    judge = JudgeAgent(Agent5Config(**FAST))
    try:
        judge.predict_proba(pd.DataFrame({"a": [1.0]}))
    except RuntimeError as e:
        assert "not fitted" in str(e)
        return True
    raise AssertionError("predict_proba ran without a fitted model")


def test_missing_columns_rejected():
    """A shifted column set would silently map values onto wrong features."""
    ds = _synthetic_dataset(signal_strength=1.0, seed=5)
    judge = JudgeAgent(Agent5Config(model="logistic", **FAST))
    judge.fit(ds, with_shuffle=False, with_importance=False)
    try:
        judge.predict_proba(ds.X.drop(columns=["noise_0"]))
    except ValueError as e:
        assert "missing" in str(e)
        return True
    raise AssertionError("a frame missing a fitted column was accepted")


def test_save_load_roundtrip():
    """A frozen model must reload and predict identically."""
    import tempfile

    ds = _synthetic_dataset(signal_strength=1.2, seed=6)
    judge = JudgeAgent(Agent5Config(model="logistic", **FAST))
    judge.fit(ds, with_shuffle=False, with_importance=False)
    before = judge.predict_proba(ds.X)

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "judge.pkl"
        judge.save(path)
        reloaded = JudgeAgent.load(path)
        after = reloaded.predict_proba(ds.X)

    assert np.allclose(before, after, equal_nan=True), \
        "a reloaded model produced different probabilities"
    assert reloaded.columns == judge.columns, "column ORDER changed on reload"
    return True


def test_full_pipeline_on_market_bars():
    """The real path: bars -> detectors -> labels -> fit -> decision.

    On a random walk the honest answer is 'no signal', and that is what is
    asserted. A pass here means the plumbing is sound, NOT that anything is
    predictable.
    """
    from agent1 import PatternAgent
    from agent2 import IndicatorAgent

    bars = make_bars(2500)
    cfg = Agent5Config(**FAST)
    judge = JudgeAgent(cfg)
    ds = judge.build(bars, warmup=300,
                     agent1=PatternAgent().compute(bars),
                     agent2=IndicatorAgent().compute(bars))
    assert len(ds) > 1500, f"only {len(ds)} samples survived assembly"
    assert ds.weight.sum() < len(ds) / 3, "uniqueness weighting did not shrink the sample"

    report = judge.fit(ds, with_shuffle=True, with_importance=False)
    assert 0.35 < report.evaluation.auc < 0.65, (
        f"AUC {report.evaluation.auc:.3f} on a random walk - "
        f"that is leakage, not skill")
    assert 0.40 < report.evaluation.shuffle_auc < 0.60

    decisions = judge.decide(bars, ds.X)
    assert set(decisions["action"].unique()) <= {"long", "flat"}
    assert (decisions["position_pct"] <= cfg.max_position_pct + 1e-9).all()
    assert (decisions.loc[decisions["action"] == "flat", "position_pct"] == 0).all()
    return True


def test_dataset_drops_are_accounted():
    """Every removed row must be explained, not silently vanish."""
    bars = make_bars(1200)
    cfg = Agent5Config(**FAST)
    ds = build_dataset(bars, cfg, warmup=200)
    total = len(ds) + sum(ds.dropped.values())
    assert total == len(bars), (
        f"{len(bars)} bars in, {len(ds)} samples + {sum(ds.dropped.values())} "
        f"dropped = {total} - rows disappeared unaccounted")
    assert ds.dropped.get("warmup") == 200
    assert ds.dropped.get("unlabelled") == cfg.max_hold_bars
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} Agent 5 pipeline tests passed.")
