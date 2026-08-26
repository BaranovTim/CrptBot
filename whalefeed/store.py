"""Append-only store for whale events. Same discipline as the news store."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

from core import utc_now

from .events import WhaleEvent

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data_cache" / "whales"


class WhaleStore:
    def __init__(self, directory: Path = DEFAULT_DIR):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "events.jsonl"

    def append(self, events: Iterable[WhaleEvent]) -> int:
        """Store what is new. Returns how many were added."""
        known = self.known_ids()
        added = 0
        with self.path.open("a", encoding="utf-8") as fh:
            for e in events:
                if e.id in known:
                    continue
                d = e.to_dict()
                if d["ingested_at"] is None:
                    # stamp arrival ourselves - this is the clock that cannot
                    # be revised out from under us later
                    d["ingested_at"] = utc_now().isoformat()
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                known.add(e.id)
                added += 1
        return added

    def known_ids(self) -> set:
        if not self.path.exists():
            return set()
        ids = set()
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ids.add(json.loads(line).get("id"))
        return ids

    def load(self) -> List[WhaleEvent]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(WhaleEvent.from_dict(json.loads(line)))
        return out

    def visible_at(self, when: datetime,
                   safety_lag: timedelta = timedelta(0)) -> List[WhaleEvent]:
        """Point-in-time query - the single gate, same as the news store."""
        return [e for e in self.load() if e.observable_at(safety_lag) <= when]
