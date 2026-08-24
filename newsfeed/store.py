"""Append-only news store with point-in-time queries.

Append-only matters. A store you can overwrite is a store where a later
correction silently rewrites history, and your backtest then trains on a
version of the past that nobody had at the time. Items are keyed by content
hash and written once; a revised republication of the same story is the same
row, not a replacement.

``ingested_at`` is stamped by this store at write time when the source did
not supply one. That stamp is what makes a live-collected corpus honest: it
records when *we* saw the item, which is the clock ``observable_at`` trusts.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .events import NewsItem, NewsScore

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data_cache" / "news"


class JSONLNewsStore:
    def __init__(self, directory: Path = DEFAULT_DIR):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items_path = self.dir / "items.jsonl"
        self.scores_path = self.dir / "scores.jsonl"

    # -- items -------------------------------------------------------------
    def append_items(self, items: Iterable[NewsItem]) -> int:
        """Write new items, skipping ones already stored. Returns count added."""
        known = self.known_ids()
        added = 0
        with self.items_path.open("a", encoding="utf-8") as fh:
            for item in items:
                if item.id in known:
                    continue
                d = item.to_dict()
                if d["ingested_at"] is None:
                    # Stamp arrival time. Without this, a live-collected item
                    # is indistinguishable from a backfilled one and we lose
                    # the only clock that proves when we actually saw it.
                    d["ingested_at"] = datetime.now(timezone.utc).isoformat()
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                known.add(item.id)
                added += 1
        return added

    def load_items(self) -> List[NewsItem]:
        if not self.items_path.exists():
            return []
        out = []
        with self.items_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(NewsItem.from_dict(json.loads(line)))
        return out

    def known_ids(self) -> set:
        if not self.items_path.exists():
            return set()
        ids = set()
        with self.items_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ids.add(json.loads(line).get("id"))
        return ids

    def visible_at(
        self, when: datetime, items: Optional[List[NewsItem]] = None,
        safety_lag: timedelta = timedelta(0),
    ) -> List[NewsItem]:
        """Every item observable at ``when`` — the point-in-time query.

        This is the single gate. Anything that reaches a feature calculation
        goes through here or through the equivalent check in
        ``agent3/features.py``.
        """
        items = self.load_items() if items is None else items
        return [i for i in items if i.observable_at(safety_lag) <= when]

    # -- scores ------------------------------------------------------------
    def save_scores(self, scores: Iterable[NewsScore]) -> int:
        n = 0
        with self.scores_path.open("a", encoding="utf-8") as fh:
            for s in scores:
                fh.write(json.dumps({
                    "item_id": s.item_id, "direction": s.direction,
                    "magnitude": s.magnitude, "novelty": s.novelty,
                    "credibility": s.credibility, "category": s.category,
                    "horizon_hours": s.horizon_hours, "assets": list(s.assets),
                    "rationale": s.rationale, "scorer": s.scorer,
                    "injection_suspected": s.injection_suspected,
                }, ensure_ascii=False) + "\n")
                n += 1
        return n

    def load_scores(self) -> Dict[str, NewsScore]:
        """Latest score per item id."""
        if not self.scores_path.exists():
            return {}
        out: Dict[str, NewsScore] = {}
        with self.scores_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                out[d["item_id"]] = NewsScore(
                    item_id=d["item_id"], direction=d["direction"],
                    magnitude=d["magnitude"], novelty=d["novelty"],
                    credibility=d["credibility"], category=d["category"],
                    horizon_hours=d["horizon_hours"],
                    assets=tuple(d.get("assets", ())),
                    rationale=d.get("rationale", ""), scorer=d.get("scorer", ""),
                    injection_suspected=d.get("injection_suspected", False),
                )
        return out
