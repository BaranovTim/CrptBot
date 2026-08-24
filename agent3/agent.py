"""Agent 3 — the news tracker.

The third thermometer, and the one that reads a hostile instrument.

Agents 1 and 2 read candles: a closed, numeric, exchange-timestamped feed
nobody can write to. Agent 3 reads text off the public internet, which
introduces two problems the other agents do not have.

**The timestamp is the weak link.** Price data is microsecond-accurate and
honest. A news timestamp is a claim, sometimes revised after the fact. So
every item is gated on ``observable_at`` — the latest of (published, ingested)
plus a safety margin — never on when the event happened. See
``newsfeed/events.py``; it is the analogue of Agent 1's ``confirmed_at`` and
the single most important thing in this package.

**The input is adversarial.** Someone can publish a headline written to steer
a bot they know is reading. The defence is structural, not persuasive: the
scorer's output schema holds bounded numbers and a closed category enum, so
there is no field in which an injected instruction could express itself. A
fully successful attack moves a number inside its valid range — which is what
an honest mis-score does too, and the calibration layer already assumes those.

Otherwise the contract is the familiar one: pure, untrained, blind to the
future, opinionless. "Untrained" is worth a word here, since this agent calls
a large language model — the model is a fixed pretrained artifact used as a
measuring instrument, and nothing in this package is fitted to trading
outcomes. Change the prompt or the model and you have changed the instrument,
so re-score the corpus rather than mixing old and new scores in one dataset.

Reads news only. Not candles (Agent 1), not indicators (Agent 2), not order
flow (Agent 4) — except for the bar index it aligns onto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from newsfeed.events import NewsItem, NewsScore
from newsfeed.store import JSONLNewsStore

from .config import DEFAULT_CONFIG, Agent3Config
from .features import compute_news_features
from .schema import FEATURE_COLUMNS
from .scorers import CachedScorer, LexiconScorer, NewsScorer


@dataclass
class NewsOutput:
    """Two channels, same split as Agents 1 and 2."""

    timestamp: pd.Timestamp
    features: Dict[str, float]
    trace: List[str] = field(default_factory=list)

    def as_row(self) -> pd.DataFrame:
        return pd.DataFrame([self.features], index=[self.timestamp])[list(FEATURE_COLUMNS)]

    def __str__(self) -> str:
        head = f"[{self.timestamp}] Agent 3"
        lines = [head, "-" * len(head)]
        lines += ["  " + t for t in self.trace] or ["  (no news in window)"]
        return "\n".join(lines)


class NewsAgent:
    def __init__(
        self,
        config: Agent3Config = DEFAULT_CONFIG,
        scorer: Optional[NewsScorer] = None,
        store: Optional[JSONLNewsStore] = None,
    ):
        self.cfg = config
        self.store = store
        # LexiconScorer by default so nothing needs a network or an API key to
        # run. Pass ClaudeScorer() for the real thing.
        self.scorer = scorer if scorer is not None else CachedScorer(LexiconScorer(), store)

    @property
    def warmup_bars(self) -> int:
        """Bars before ``news_count_z_24h`` has a baseline to standardise against.

        Every other column is event-driven and valid from the first bar — a
        headline is either observable or it is not, with no rolling window to
        fill. Only the volume z-score needs history.
        """
        return max(24, self.cfg.baseline_bars // 4)

    def score_items(self, items: Sequence[NewsItem]) -> Dict[str, NewsScore]:
        """Score a corpus once. The slow, offline half of the pipeline.

        Every model call the system makes happens here, at ingest time — never
        between a bar closing and a decision. Inference takes seconds; a
        decision has to be arithmetic.
        """
        if not items:
            return {}
        return {s.item_id: s for s in self.scorer.score(list(items), self.cfg.asset)}

    def compute(
        self,
        bars: pd.DataFrame,
        items: Sequence[NewsItem],
        scores: Optional[Dict[str, NewsScore]] = None,
    ) -> pd.DataFrame:
        """News features for every bar, indexed by close_time."""
        if not isinstance(bars.index, pd.DatetimeIndex):
            raise TypeError("bars must be indexed by close_time as a DatetimeIndex")
        if not bars.index.is_monotonic_increasing:
            raise ValueError("bars index must be sorted ascending")
        if bars.index.tz is None:
            raise ValueError(
                "bars index must be timezone-aware (UTC). News timestamps are "
                "absolute; comparing them against naive local times silently "
                "shifts every item by the UTC offset."
            )
        if scores is None:
            scores = self.score_items(items)
        return compute_news_features(bars, items, scores, self.cfg)

    def latest(
        self,
        bars: pd.DataFrame,
        items: Sequence[NewsItem],
        scores: Optional[Dict[str, NewsScore]] = None,
    ) -> NewsOutput:
        if scores is None:
            scores = self.score_items(items)
        features = self.compute(bars, items, scores)
        row = features.iloc[-1]
        return NewsOutput(
            timestamp=features.index[-1],
            features={k: float(row[k]) for k in FEATURE_COLUMNS},
            trace=self._build_trace(row, bars.index[-1], items, scores),
        )

    # -- trace -------------------------------------------------------------
    def _build_trace(self, row, now, items, scores) -> List[str]:
        out: List[str] = []

        def has(k):
            return not pd.isna(row[k])

        if row["news_count_24h"] == 0:
            return [f"no {self.cfg.asset} news in the last "
                    f"{self.cfg.long_window_hours:.0f}h"]

        out.append(
            f"{int(row['news_count_24h'])} items in 24h "
            f"({int(row['news_count_6h'])} in 6h)"
            + (f", {row['news_count_z_24h']:+.1f} sd vs baseline"
               if has("news_count_z_24h") else "")
        )

        for col, label in (("news_sentiment_1h", "1h"), ("news_sentiment_6h", "6h"),
                           ("news_sentiment_24h", "24h")):
            if has(col):
                v = row[col]
                word = "bullish" if v > 0.05 else "bearish" if v < -0.05 else "neutral"
                out.append(f"sentiment {label}: {v:+.2f} ({word})")

        if has("news_sentiment_dispersion_24h"):
            d = row["news_sentiment_dispersion_24h"]
            out.append(
                f"dispersion {d:.2f} — "
                + ("sources disagree" if d > 0.5 else "sources broadly agree "
                   "(weak evidence; nearly all coverage is bullish)")
            )

        if has("news_novelty_max_6h"):
            out.append(f"peak novelty in 6h: {row['news_novelty_max_6h']:.2f} "
                       f"(1.0 = genuinely new information)")
        if has("news_max_magnitude_6h"):
            out.append(f"peak magnitude in 6h: {row['news_max_magnitude_6h']:.2f}")
        if has("news_credibility_mean_24h"):
            out.append(f"mean source credibility: {row['news_credibility_mean_24h']:.2f}")

        cats = [(c.replace("news_cat_", "").replace("_24h", ""), int(row[c]))
                for c in ("news_cat_regulatory_24h", "news_cat_listing_24h",
                          "news_cat_security_24h", "news_cat_macro_24h")
                if row[c] > 0]
        if cats:
            out.append("categories: " + ", ".join(f"{n} x{k}" for n, k in cats))

        if has("bars_since_news"):
            out.append(f"most recent item {int(row['bars_since_news'])} bars ago")

        if has("news_scored_fraction_24h") and row["news_scored_fraction_24h"] < 1.0:
            out.append(
                f"WARNING: only {row['news_scored_fraction_24h']:.0%} of items "
                f"were scored — the scoring pipeline is degraded, and every "
                f"sentiment number above is computed from a partial sample"
            )

        lag = pd.Timedelta(seconds=self.cfg.safety_lag_seconds)
        visible = [i for i in items if i.observable_at(lag) <= now]
        flagged = sum(1 for i in visible
                      if scores.get(i.id) and scores[i.id].injection_suspected)
        if flagged:
            out.append(f"SECURITY: {flagged} visible item(s) match prompt-injection "
                       f"patterns — scored as data, never executed")

        for item in sorted(visible, key=lambda i: i.observable_at(lag))[-3:][::-1]:
            sc = scores.get(item.id)
            if sc is None:
                continue
            age_h = (now - item.observable_at(lag)).total_seconds() / 3600.0
            out.append(
                f'  "{item.headline[:70]}" ({item.source}, {age_h:.1f}h ago) '
                f"dir {sc.direction:+.2f} mag {sc.magnitude:.2f} nov {sc.novelty:.2f}"
            )
        return out
