"""Turning news items into bounded numbers.

Three implementations behind one interface:

  ClaudeScorer   the real one — structured extraction via the Claude API
  LexiconScorer  deterministic keyword fallback; no network, no key, used by
                 the test suite so the pipeline is testable offline
  CachedScorer   wraps either, keyed by content hash

The caching wrapper is not an optimisation, it is an architectural
requirement. LLM inference takes seconds; a trading decision has to be
arithmetic. So scoring happens ONCE, when an item is ingested, and the
per-bar feature computation reads frozen numbers off disk. No model call ever
sits in the path between a bar closing and a decision being made.

Why an LLM at all, when FinBERT and CryptoBERT exist and are free? Because
sentiment is the part of this that does not matter. Those models answer "is
this bullish?", and nearly everything published about a coin is bullish. What
is needed is novelty, magnitude, and which asset — and that needs a model
that can reason about whether the market already knew.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Protocol, Sequence

from newsfeed.events import NewsItem, NewsScore

from .prompt import SCORE_SCHEMA, SYSTEM_PROMPT, build_user_content, validate_score

log = logging.getLogger(__name__)


class NewsScorer(Protocol):
    name: str

    def score(self, items: Sequence[NewsItem], asset_hint: str = "") -> List[NewsScore]:
        ...


# ---------------------------------------------------------------------------
class ClaudeScorer:
    """Structured extraction through the Claude API.

    Notes on the request shape, since several of these are easy to get wrong:

    * ``output_config.format`` constrains generation to the schema. The older
      trick of prefilling the assistant turn with ``{"`` returns a 400 on
      current models, and ``output_format`` as a top-level parameter is
      deprecated in favour of ``output_config.format``.
    * Thinking is left ON at low effort rather than disabled. Disabling it is
      the cheaper-looking option but has a documented failure mode where
      internal ``<thinking>`` tags leak into the visible response — which,
      for a JSON extraction task, is a parse failure. Low effort gets most of
      the cost saving without it.
    * The system prompt is cached. It is long, byte-identical on every call,
      and rendered before the message, so it is the ideal cache prefix. Keep
      it frozen: interpolating a timestamp or an asset name into it would
      invalidate the cache on every single request.
    * ``stop_reason`` is checked before ``content`` is read. A refusal returns
      HTTP 200 with an empty content list, so indexing ``content[0]`` blindly
      raises on the first item that trips a classifier.
    """

    name = "claude"

    def __init__(
        self,
        model: str = "claude-opus-5",
        effort: str = "low",
        max_tokens: int = 1024,
        client: Any = None,
    ):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic  # imported lazily so the package stays optional
            self._client = anthropic.Anthropic()
        return self._client

    def _request_kwargs(self, item: NewsItem, asset_hint: str) -> Dict[str, Any]:
        return dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": SCORE_SCHEMA},
            },
            messages=[{"role": "user", "content": build_user_content(item, asset_hint)}],
        )

    def score(self, items: Sequence[NewsItem], asset_hint: str = "") -> List[NewsScore]:
        import anthropic

        out: List[NewsScore] = []
        for item in items:
            try:
                resp = self.client.messages.create(**self._request_kwargs(item, asset_hint))
            except anthropic.RateLimitError as e:
                retry_after = int(getattr(e, "response", None)
                                  and e.response.headers.get("retry-after", "5") or 5)
                time.sleep(retry_after)
                try:
                    resp = self.client.messages.create(**self._request_kwargs(item, asset_hint))
                except anthropic.APIStatusError:
                    out.append(self._unscored(item, "rate_limited"))
                    continue
            except anthropic.APIStatusError as e:
                log.warning("scoring failed for %s: %s", item.id, e)
                out.append(self._unscored(item, "api_error"))
                continue
            except anthropic.APIConnectionError:
                out.append(self._unscored(item, "connection_error"))
                continue

            # A refusal is HTTP 200 with empty content — check before indexing.
            if resp.stop_reason == "refusal":
                log.info("classifier declined item %s", item.id)
                out.append(self._unscored(item, "refused"))
                continue

            text = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                out.append(self._unscored(item, "unparseable"))
                continue
            out.append(validate_score(raw, item, self.name))
        return out

    def batch_requests(self, items: Sequence[NewsItem], asset_hint: str = "") -> List[Any]:
        """Build Batch API requests for a historical backfill.

        Backfilling years of headlines one synchronous call at a time is the
        expensive way to do it. The Batch API runs the same requests at half
        price, and nothing about a backfill is latency-sensitive.
        """
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        return [
            Request(
                custom_id=item.id,
                params=MessageCreateParamsNonStreaming(
                    **self._request_kwargs(item, asset_hint)
                ),
            )
            for item in items
        ]

    @staticmethod
    def _unscored(item: NewsItem, reason: str) -> NewsScore:
        """A failed score is neutral and flagged, never a guess.

        magnitude 0 means the aggregation weights it to nothing, so a broken
        scorer degrades the features toward "no news" rather than toward a
        fabricated signal. ``news_scored_fraction_24h`` is what surfaces the
        breakage to Agent 5.
        """
        return NewsScore(
            item_id=item.id, direction=0.0, magnitude=0.0, novelty=0.0,
            credibility=0.0, category="other", horizon_hours=0.0,
            rationale=f"unscored: {reason}", scorer=f"failed:{reason}",
        )


# ---------------------------------------------------------------------------
_BULL = ("approval", "approved", "adoption", "partnership", "upgrade", "launch",
         "listing", "listed", "inflow", "record high", "rally", "surge",
         "institutional", "etf", "integration", "funding", "buyback")
_BEAR = ("hack", "hacked", "exploit", "breach", "stolen", "lawsuit", "sue",
         "ban", "banned", "crackdown", "delisting", "delisted", "outflow",
         "insolvency", "bankrupt", "liquidation", "halt", "investigation",
         "fraud", "sec charges")
_HIGH_CRED = ("sec.gov", "cftc", "federalreserve", "binance", "coinbase",
              "reuters", "bloomberg", "announcement")


class LexiconScorer:
    """Deterministic keyword scoring. No network, no key, no variance.

    This exists so the whole pipeline — PIT discipline, aggregation, the
    lookahead test — is testable offline and reproducibly. It is a stand-in,
    not a rival: a keyword list cannot judge novelty, which is the field that
    actually matters. Treat its ``novelty`` as a placeholder.
    """

    name = "lexicon"

    def score(self, items: Sequence[NewsItem], asset_hint: str = "") -> List[NewsScore]:
        out = []
        for item in items:
            blob = f"{item.headline} {item.body}".lower()
            bull = sum(w in blob for w in _BULL)
            bear = sum(w in blob for w in _BEAR)
            total = bull + bear
            direction = 0.0 if total == 0 else (bull - bear) / total
            magnitude = min(1.0, total / 4.0)
            cred = 1.0 if any(s in item.source.lower() for s in _HIGH_CRED) else 0.4

            if any(w in blob for w in ("ban", "lawsuit", "sec", "regulat", "crackdown")):
                category = "regulatory"
            elif any(w in blob for w in ("listing", "delisting", "listed")):
                category = "listing"
            elif any(w in blob for w in ("hack", "exploit", "breach", "stolen")):
                category = "security"
            elif any(w in blob for w in ("fed", "cpi", "rates", "inflation")):
                category = "macro"
            else:
                category = "other"

            out.append(validate_score(
                {
                    "direction": direction,
                    "magnitude": magnitude,
                    # A keyword list has no idea whether the market already
                    # knew. Constant placeholder, honestly labelled.
                    "novelty": 0.5,
                    "credibility": cred,
                    "category": category,
                    "horizon_hours": 24.0 if category == "regulatory" else 6.0,
                    "assets": list(item.assets),
                    "rationale": f"lexicon: {bull} bullish / {bear} bearish terms",
                },
                item, self.name,
            ))
        return out


# ---------------------------------------------------------------------------
class CachedScorer:
    """Score each item once, ever, keyed by content hash."""

    def __init__(self, inner: NewsScorer, store: Any = None):
        self.inner = inner
        self.name = f"cached:{getattr(inner, 'name', 'unknown')}"
        self.store = store
        self._mem: Dict[str, NewsScore] = {}
        if store is not None:
            self._mem.update(store.load_scores())

    def score(self, items: Sequence[NewsItem], asset_hint: str = "") -> List[NewsScore]:
        missing = [i for i in items if i.id not in self._mem]
        if missing:
            fresh = self.inner.score(missing, asset_hint)
            for s in fresh:
                self._mem[s.item_id] = s
            if self.store is not None:
                # Persist failures too, but do not cache them forever — a
                # rate-limited item should be retried, an item the model
                # genuinely scored should not.
                self.store.save_scores([s for s in fresh if not s.scorer.startswith("failed:")])
        return [self._mem[i.id] for i in items if i.id in self._mem]

    def scored_ids(self) -> set:
        return set(self._mem)
