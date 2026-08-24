"""Collapsing a stream of scored items into one fixed row per bar.

The same reduction problem as Agent 1's order blocks: between two bars there
may be zero news items or forty, and the model takes a fixed-length vector.
Here the reduction is over time rather than over price zones, so the tools are
a lookback window and a decay weight.

Two decisions worth knowing about:

**Per-item decay.** Each item fades on its own scored ``horizon_hours``
rather than a single global half-life. A regulatory decision scored at 720
hours stays in the 24h average almost undiminished; a liquidation headline
scored at 1 hour is gone within the same session. A single half-life would
force those to fade at the same rate, which is wrong in both directions.

**Credibility as weight, not as a feature.** An anonymous rumour and a
regulator's filing contribute to the same average, weighted by how much they
should count. The mean credibility is emitted separately so Agent 5 can still
learn "this window was all rumour".

Everything here reads only items whose ``observable_at`` has passed. That
single comparison is the entire lookahead defence, and
``tests/test_agent3_pit.py`` is what proves it holds.
"""
from __future__ import annotations

import bisect
from datetime import timedelta
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

from newsfeed.events import NewsItem, NewsScore

from .config import Agent3Config
from .schema import COUNT_COLUMNS, FEATURE_COLUMNS, validate_features

HOUR = 3600.0


def _relevant(item: NewsItem, cfg: Agent3Config) -> bool:
    """Does this item bear on the asset we are trading?

    An untagged item is market-wide and kept — "Fed hikes 50bp" carries no
    ticker but moves everything. An item tagged only for other assets is
    dropped, so ETH news does not quietly drive BTC features.
    """
    if not item.assets:
        return cfg.include_untagged
    return cfg.asset.upper() in {a.upper() for a in item.assets}


def _weight(age_h: float, score: NewsScore, cfg: Agent3Config) -> float:
    """Credibility x exponential decay on the item's own horizon."""
    half_life = max(cfg.min_half_life_hours, score.horizon_hours / 2.0)
    return score.credibility * (0.5 ** (age_h / half_life))


def _wmean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = sum(weights)
    if total <= 1e-12:
        return float("nan")
    return float(sum(v * w for v, w in zip(values, weights)) / total)


def compute_news_features(
    bars: pd.DataFrame,
    items: Sequence[NewsItem],
    scores: Dict[str, NewsScore],
    cfg: Agent3Config,
) -> pd.DataFrame:
    """One row per bar, indexed by close_time."""
    idx = bars.index
    n = len(idx)
    lag = timedelta(seconds=cfg.safety_lag_seconds)

    # Sort by the only clock we trust. bisect over this list is what enforces
    # "nothing visible before observable_at".
    usable: List[Tuple[float, NewsItem, NewsScore]] = []
    for item in items:
        if not _relevant(item, cfg):
            continue
        usable.append((item.observable_at(lag).timestamp(), item,
                       scores.get(item.id)))
    usable.sort(key=lambda r: r[0])
    obs_ts = [r[0] for r in usable]

    cols = {c: np.full(n, np.nan) for c in FEATURE_COLUMNS}
    for c in COUNT_COLUMNS:
        cols[c] = np.zeros(n)

    bar_ts = (idx.view("int64") // 1_000_000_000).astype(float) \
        if hasattr(idx, "view") else np.array([t.timestamp() for t in idx])

    last_seen_bar = None
    win_s = cfg.short_window_hours * HOUR
    win_m = cfg.mid_window_hours * HOUR
    win_l = cfg.long_window_hours * HOUR

    for t in range(n):
        now = bar_ts[t]
        # bisect_right => strictly "observable at or before this bar's close".
        hi = bisect.bisect_right(obs_ts, now)
        if hi == 0:
            continue

        lo_l = bisect.bisect_left(obs_ts, now - win_l)
        window_l = usable[lo_l:hi]
        if not window_l and last_seen_bar is not None:
            cols["bars_since_news"][t] = t - last_seen_bar
            continue
        if not window_l:
            continue

        last_seen_bar = t
        cols["bars_since_news"][t] = 0.0

        lo_m = bisect.bisect_left(obs_ts, now - win_m)
        lo_s = bisect.bisect_left(obs_ts, now - win_s)

        def stats(rows):
            vals, wts, dirs = [], [], []
            mags, novs, creds, scored = [], [], [], 0
            for ts, _item, sc in rows:
                if sc is None:
                    continue
                scored += 1
                age_h = (now - ts) / HOUR
                w = _weight(age_h, sc, cfg)
                vals.append(sc.signed_strength())
                dirs.append(sc.direction)
                wts.append(w)
                mags.append(sc.magnitude)
                novs.append(sc.novelty)
                creds.append(sc.credibility)
            return vals, dirs, wts, mags, novs, creds, scored

        v_l, d_l, w_l, mag_l, nov_l, cred_l, scored_l = stats(window_l)
        v_m, d_m, w_m, mag_m, nov_m, _, _ = stats(usable[lo_m:hi])
        v_s, _, w_s, _, _, _, _ = stats(usable[lo_s:hi])

        cols["news_count_6h"][t] = len(usable[lo_m:hi])
        cols["news_count_24h"][t] = len(window_l)
        cols["news_scored_fraction_24h"][t] = scored_l / max(1, len(window_l))

        if v_s:
            cols["news_sentiment_1h"][t] = _wmean(v_s, w_s)
        if v_m:
            cols["news_sentiment_6h"][t] = _wmean(v_m, w_m)
            cols["news_max_magnitude_6h"][t] = max(mag_m)
            cols["news_novelty_max_6h"][t] = max(nov_m)
        if v_l:
            cols["news_sentiment_24h"][t] = _wmean(v_l, w_l)
            cols["news_magnitude_mean_24h"][t] = float(np.mean(mag_l))
            cols["news_credibility_mean_24h"][t] = float(np.mean(cred_l))
            # Spread of opinion, scaled to [0, 1]. Near 0 means every source
            # is saying the same thing — which, given that almost all crypto
            # coverage is bullish, is the common case and therefore weak
            # evidence. Disagreement is the informative state.
            cols["news_sentiment_dispersion_24h"][t] = min(
                1.0, float(np.std(d_l)) if len(d_l) > 1 else 0.0
            )

        # Most recent item, by observation time.
        for _ts, _item, sc in reversed(window_l):
            if sc is not None:
                cols["news_direction_last"][t] = sc.direction
                cols["news_novelty_last"][t] = sc.novelty
                break

        for cat, col in (("regulatory", "news_cat_regulatory_24h"),
                         ("listing", "news_cat_listing_24h"),
                         ("security", "news_cat_security_24h"),
                         ("macro", "news_cat_macro_24h")):
            cols[col][t] = sum(
                1 for _ts, _i, sc in window_l if sc is not None and sc.category == cat
            )

    out = pd.DataFrame(cols, index=idx)[list(FEATURE_COLUMNS)]

    # Unusual news volume, standardised against a ROLLING baseline. A
    # full-sample mean here would leak the whole future into every row —
    # the same normalisation trap as Agent 2, in a different file.
    c24 = out["news_count_24h"]
    w = cfg.baseline_bars
    mu = c24.rolling(w, min_periods=max(24, w // 4)).mean()
    sd = c24.rolling(w, min_periods=max(24, w // 4)).std(ddof=0)
    out["news_count_z_24h"] = ((c24 - mu) / sd.where(sd > 1e-9)).replace(
        [np.inf, -np.inf], np.nan
    )
    return validate_features(out)
