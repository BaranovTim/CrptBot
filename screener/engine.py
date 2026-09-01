"""Running a set of filters over the universe.

WHY THE RESULT CARRIES WHY
    A screener that returns a list of tickers is a black box: when a stock you
    expected is missing, there is nothing to look at. Every row here reports
    which filters it passed, which it failed, and which could not be judged —
    so "why isn't NVDA in this?" has an answer on the screen rather than in a
    debugger.

UNKNOWN IS NOT FAIL, AND IT IS NOT PASS EITHER
    A company that has not filed recently, or a field that needs analyst
    estimates this stack does not buy, produces UNKNOWN. Those rows are held
    back from the results and counted, because both alternatives are lies:
    including them claims a criterion was met that was never checked, and
    excluding them silently claims the stock was rejected on the merits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, Iterable, List, Optional

from screener.filters import FAIL, PASS, UNKNOWN, Filter

log = logging.getLogger(__name__)

# A screen over 13,000 names can match thousands. The app cannot draw that and
# nobody reads past the first screen of it, so the server caps what it sends
# and says how many there were.
DEFAULT_LIMIT = 200


@dataclass
class Row:
    symbol: str
    metrics: Dict[str, Any]
    passed: List[str] = dc_field(default_factory=list)
    failed: List[str] = dc_field(default_factory=list)
    unknown: List[str] = dc_field(default_factory=list)

    @property
    def matched(self) -> bool:
        return not self.failed and not self.unknown

    def to_json(self) -> dict:
        return {"symbol": self.symbol, "metrics": self.metrics,
                "passed": self.passed, "failed": self.failed,
                "unknown": self.unknown}


def evaluate(symbol: str, metrics: Dict[str, Any],
             filters: Iterable[Filter]) -> Row:
    row = Row(symbol=symbol, metrics=metrics)
    for f in filters:
        verdict = f.check(metrics)
        target = (row.passed if verdict == PASS
                  else row.failed if verdict == FAIL else row.unknown)
        target.append(f.field)
    return row


def run(universe: Dict[str, Dict[str, Any]], filters: List[Filter],
        limit: int = DEFAULT_LIMIT,
        sort_by: Optional[str] = None,
        descending: bool = True,
        include_unknown: bool = False) -> dict:
    """Screen every symbol. Returns matches plus what happened to the rest."""
    matched: List[Row] = []
    near: List[Row] = []          # failed only on fields nobody could judge
    for symbol, metrics in universe.items():
        row = evaluate(symbol, metrics, filters)
        if row.failed:
            continue
        if row.unknown:
            near.append(row)
        else:
            matched.append(row)

    # NEAR-MISSES RANKED BY HOW NEAR THEY ARE.
    #
    # Alphabetical order put AAC.WS — a warrant that passed nothing and was
    # unjudged on everything — above a company that cleared six of seven
    # criteria. The unjudged bucket is only useful if the rows worth a second
    # look are at the top of it, so: most criteria passed first, fewest
    # unanswerable next, symbol last as a stable tiebreak.
    near.sort(key=lambda r: (-len(r.passed), len(r.unknown), r.symbol))
    rows = matched + near if include_unknown else matched

    if sort_by:
        # None sorts last in BOTH directions, rather than counting as
        # negative infinity and taking the top of an ascending sort.
        def key(r: Row):
            v = r.metrics.get(sort_by)
            try:
                return (0, float(v))
            except (TypeError, ValueError):
                return (1, 0.0)

        rows = sorted(rows, key=key, reverse=descending)
        rows = [r for r in rows if r.metrics.get(sort_by) is not None] + \
               [r for r in rows if r.metrics.get(sort_by) is None]

    return {
        "matched": len(matched),
        # Named separately so the page can say "89 could not be judged"
        # rather than letting them look like rejections.
        "unjudged": len(near),
        "scanned": len(universe),
        "returned": len(rows[:limit]),
        "rows": [r.to_json() for r in rows[:limit]],
    }
