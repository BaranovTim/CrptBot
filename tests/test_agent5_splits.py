"""Purged K-fold: the scoreboard must not be able to cheat.

Every evaluation number the project will ever produce flows through this
splitter. If it leaks, every number after it is a lie that says "genius".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from agent5.splits import PurgedKFold

N = 1000
POS = np.arange(N, dtype=float)          # one sample per bar
HOLD = 24
T1 = POS + HOLD                          # every label runs the full window


def test_every_sample_tested_exactly_once():
    seen = np.zeros(N)
    for train, test in PurgedKFold(5, T1, 0.01).split(POS):
        seen[test] += 1
    assert (seen == 1).all(), "test folds must partition the samples"
    return True


def test_no_label_overlap_between_train_and_test():
    """The core guarantee: no training label window touches the test window."""
    for train, test in PurgedKFold(5, T1, 0.0).split(POS):
        test_start, test_end = POS[test[0]], np.nanmax(T1[test])
        for i in train:
            overlaps = (T1[i] >= test_start) and (POS[i] <= test_end)
            assert not overlaps, (
                f"train sample at bar {POS[i]:.0f} (label ends {T1[i]:.0f}) "
                f"overlaps test window [{test_start:.0f}, {test_end:.0f}]"
            )
    return True


def test_purge_width_matches_holding_period():
    """Samples up to max_hold bars before the test must be gone, older kept."""
    folds = list(PurgedKFold(5, T1, 0.0).split(POS))
    train, test = folds[2]                       # a middle fold: purge on both sides
    test_start = POS[test[0]]
    gap = test_start - POS[train][POS[train] < test_start].max()
    assert gap > HOLD, f"gap before test is {gap:.0f} bars, label window is {HOLD}"
    return True


def test_embargo_removes_strip_after_test():
    embargo_pct = 0.02
    folds = list(PurgedKFold(5, T1, embargo_pct).split(POS))
    train, test = folds[1]
    test_end = np.nanmax(T1[test])
    later = POS[train][POS[train] > test_end]
    if len(later):
        assert later.min() > test_end + N * embargo_pct - 1, \
            "training samples found inside the embargo strip"
    return True


def test_leak_detector_end_to_end():
    """The demonstration that purging is load-bearing, not decoration.

    Setup: features are SMOOTHED PURE NOISE - by construction they carry
    zero information about the labels, which come from an entirely separate
    random walk. Any evaluation that scores above chance here is lying.

    The naive baseline is shuffled K-fold - what every sklearn tutorial
    defaults to - with a local model (kNN). For a test sample at bar t, its
    nearest neighbours in feature space are its temporal neighbours t+-1,
    t+-2 (smoothed noise barely moves bar to bar), which sit in the training
    fold and share 23 of 24 label-window bars. The model just copies their
    outcome: high AUC, zero signal.

    PurgedKFold removes exactly those neighbours, and the score collapses
    to the coin flip it always deserved to be.
    """
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import KFold
    from sklearn.neighbors import KNeighborsClassifier

    rng = np.random.default_rng(3)
    n, hold = 1500, 24
    pos = np.arange(n, dtype=float)
    t1 = pos + hold

    # labels: direction of a hidden random walk over the NEXT `hold` bars
    hidden = np.cumsum(rng.normal(0, 1, n + hold))
    y = (hidden[hold:] > hidden[:-hold]).astype(float)

    # features: independent noise, smoothed so neighbours look alike.
    # zero relation to `hidden`, therefore zero real signal - guaranteed
    kernel = np.ones(hold) / hold
    X = np.stack([np.convolve(rng.normal(0, 1, n), kernel, mode="same")
                  for _ in range(4)], axis=1)

    def oof_auc(splits) -> float:
        oof = np.full(n, np.nan)
        for tr, te in splits:
            m = KNeighborsClassifier(n_neighbors=5).fit(X[tr], y[tr])
            oof[te] = m.predict_proba(X[te])[:, 1]
        return roc_auc_score(y, oof)

    leaky = oof_auc(KFold(5, shuffle=True, random_state=0).split(X))
    honest = oof_auc(PurgedKFold(5, t1, 0.01).split(pos))

    assert leaky > 0.75, f"leaky baseline only {leaky:.3f} - probe lost its bite"
    assert 0.42 < honest < 0.58, f"purged AUC {honest:.3f} - should be a coin flip"
    return True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} split tests passed.")
