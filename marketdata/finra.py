"""Short interest from FINRA.

WHY FINRA AND NOT A DATA VENDOR
    Short interest is a regulatory filing, not a product. FINRA Rule 4560
    requires every member firm to report its short positions twice a month,
    and FINRA publishes the aggregate. It is free, official, and carries no
    licensing restriction — which matters because the vendors that resell it
    forbid redistribution, and this app has paying subscribers.

    It is also the only field in the "Short Plays" preset that cannot be
    computed from prices, so without it that screen has nothing to stand on.

TWICE A MONTH, AND THAT IS THE POINT
    Settlement dates are the 15th and the end of the month, published about
    eight days later. So this number is ALWAYS stale — typically by one to
    three weeks — and there is no version of it that is not. A squeeze that
    started last Tuesday is invisible here.

    `as_of` is returned alongside every value so the app can say how old the
    reading is. Presenting a fortnight-old short interest as a live figure is
    the kind of quiet wrongness that makes someone size a position on it.

FLOAT SHORT NEEDS A FLOAT, WHICH THIS DOES NOT HAVE
    FINRA reports the number of shares short. Turning that into a percentage
    needs the float, and float is not shares outstanding — it excludes insider
    and restricted holdings. Substituting one for the other inflates the
    denominator and understates the percentage, making a crowded short look
    uncrowded. The division happens in the assembly layer, against a real
    float, or not at all.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger(__name__)

API = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
STORE = Path("data_cache") / "finra"

# The API is public but paginates hard; 5,000 is its documented page size.
PAGE = 5000
MAX_PAGES = 12          # ~60k rows, comfortably more than the listed universe


class FinraError(RuntimeError):
    pass


def _post(offset: int, timeout: int = 60) -> list:
    """One page. FINRA's data API takes a JSON body on POST, not query args."""
    body = json.dumps({"limit": PAGE, "offset": offset}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "Vanth/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()) or []
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            pass
        raise FinraError(f"HTTP {e.code} from FINRA: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise FinraError(f"cannot reach FINRA: {e}") from e


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch_short_interest() -> Dict[str, dict]:
    """SYMBOL -> {shares_short, as_of, avg_daily_volume, days_to_cover}.

    Only the most recent settlement date is kept. FINRA returns several
    historical periods in the same feed, and taking whichever row happened to
    arrive last would mix a current reading for one ticker with a two-month-old
    one for the next — inside a single screen, invisibly.
    """
    rows: Dict[str, dict] = {}
    for page in range(MAX_PAGES):
        batch = _post(page * PAGE)
        if not batch:
            break
        for r in batch:
            sym = str(r.get("symbolCode") or r.get("issueSymbolIdentifier")
                      or "").upper().strip()
            if not sym:
                continue
            when = str(r.get("settlementDate") or "")[:10]
            if not when:
                continue
            prev = rows.get(sym)
            if prev and prev["as_of"] >= when:
                continue                    # keep only the newest settlement
            rows[sym] = {
                "shares_short": _num(r.get("currentShortPositionQuantity")),
                "as_of": when,
                "avg_daily_volume": _num(r.get("averageDailyVolumeQuantity")),
                "days_to_cover": _num(r.get("daysToCoverQuantity")),
            }
        if len(batch) < PAGE:
            break
    log.info("finra: short interest for %d symbols", len(rows))
    return rows


def save(rows: Dict[str, dict], path: Optional[Path] = None) -> Path:
    path = Path(path or (STORE / "short_interest.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows))
    tmp.replace(path)
    return path


def load(path: Optional[Path] = None) -> Dict[str, dict]:
    path = Path(path or (STORE / "short_interest.json"))
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as e:
        log.warning("finra store unreadable: %s", e)
        return {}


def float_short_pct(shares_short: Optional[float],
                    float_shares: Optional[float]) -> Optional[float]:
    """Percent of the float that is sold short.

    Returns None rather than a number when the float is missing or absurd.
    A short interest greater than the entire float is possible in reality —
    it is the definition of a squeeze setup — so values above 100 are NOT
    clamped. Clamping them would hide precisely the stocks this screen exists
    to find.
    """
    if not shares_short or not float_shares or float_shares <= 0:
        return None
    return (shares_short / float_shares) * 100.0
