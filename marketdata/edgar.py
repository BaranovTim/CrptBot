"""Company fundamentals from SEC EDGAR's XBRL data.

WHY THIS AND NOT A PAID API
    It is the same numbers. Every ratio a commercial provider sells —
    return on equity, current ratio, debt/equity, payout ratio — is arithmetic
    over line items the company filed with the SEC. The provider's value is
    packaging, not information.

    And it is the only source with no licensing problem: EDGAR data is US
    government work, not subject to copyright, and may be redistributed. That
    matters because this app charges a subscription. Yahoo and Finviz both
    forbid exactly that, which is why neither is used here.

WHAT IT CANNOT GIVE YOU
    Anything forward-looking. EPS Growth Next Year and Next 5 Years are
    ANALYST ESTIMATES — opinions collected from banks, not facts filed with a
    regulator. They are not in EDGAR at any price, and no amount of cleverness
    derives them from history. Three of the screener's fields need them; the
    screener says so rather than guessing.

    Float is also absent. EDGAR reports shares OUTSTANDING; float excludes
    insider and restricted holdings and is a different number. Using one for
    the other would overstate float and understate short interest as a
    percentage of it — quietly, in the direction that makes a crowded short
    look uncrowded.

WHY THE BULK FILE
    `companyfacts.zip` is every fact for every filer in one download. The
    per-company endpoint is 10 requests/second, so the same coverage is
    roughly 13,000 requests and half an hour of politeness. The bulk file is
    one request. SEC asks for a User-Agent identifying you; that is the whole
    of their fair-access policy and it is honoured here.

POINT IN TIME, AS EVERYWHERE ELSE IN THIS REPO
    Every fact carries the date it was FILED, not just the period it covers. A
    Q3 balance sheet is not knowable in Q3 — it is knowable when the 10-Q is
    filed, weeks later. Screening on the period date would be looking at
    numbers the market did not have, which is the same lookahead the bar
    indexing in `binance.py` exists to prevent.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# SEC's fair-access policy asks for identification, not a key. Rotating this
# or omitting it is what gets an IP blocked.
UA = "ThusIldy research contact@thusildy.app"

# 10 requests/second is the documented ceiling. This sits under it.
MIN_INTERVAL = 0.12

STORE = Path("data_cache") / "edgar"

# The XBRL tags each metric can appear under, in order of preference.
#
# There are several per concept because filers choose between them and change
# their minds between years: a company reporting `Revenues` in 2021 may report
# `RevenueFromContractWithCustomerExcludingAssessedTax` in 2023. Taking the
# first tag that exists, rather than one hardcoded name, is the difference
# between covering most of the market and covering the third of it that
# happens to share your preferred vocabulary.
TAGS: Dict[str, List[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assets_current": ["AssetsCurrent"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "debt_long": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_short": ["LongTermDebtCurrent", "DebtCurrent"],
    "dividends_per_share": ["CommonStockDividendsPerShareDeclared",
                            "CommonStockDividendsPerShareCashPaid"],
    "shares_outstanding": ["CommonStockSharesOutstanding",
                           "EntityCommonStockSharesOutstanding",
                           "WeightedAverageNumberOfDilutedSharesOutstanding"],
}


class EdgarError(RuntimeError):
    pass


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return raw
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise EdgarError("SEC rate limit — 10 req/s is the ceiling") from e
        raise EdgarError(f"HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, OSError) as e:
        raise EdgarError(f"cannot reach SEC: {e}") from e


def ticker_map(refresh: bool = False) -> Dict[str, int]:
    """TICKER -> CIK. The join key between price data and filings.

    Cached on disk: it changes when a company lists or renames, which is not
    something to re-download per screener run.
    """
    path = STORE / "tickers.json"
    if not refresh and path.exists():
        try:
            return {k: int(v) for k, v in json.loads(path.read_text()).items()}
        except (ValueError, OSError):
            pass                      # corrupt cache re-downloads, not crashes

    raw = json.loads(_get(TICKER_URL))
    # the file is {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}
    out = {}
    for row in raw.values():
        t = str(row.get("ticker", "")).upper().strip()
        if t:
            out[t] = int(row["cik_str"])
    STORE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    log.info("edgar: %d tickers mapped to CIKs", len(out))
    return out


def _usable(entry: dict, as_of: Optional[date]) -> bool:
    """Was this fact public by `as_of`?

    `filed` is the date the document reached the SEC. `end` is the period it
    describes, which is always earlier. Screening on `end` would use numbers
    weeks before anyone could have seen them.
    """
    if as_of is None:
        return True
    try:
        return datetime.strptime(entry["filed"], "%Y-%m-%d").date() <= as_of
    except (KeyError, ValueError):
        return False


def latest_fact(facts: dict, names: Iterable[str],
                as_of: Optional[date] = None,
                quarterly: bool = False) -> Optional[dict]:
    """The most recently FILED value among these tag names.

    Returns the whole entry, not the number, because callers need `end` and
    `filed` to compute growth and to know how stale a figure is.
    """
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    dei = (facts.get("facts") or {}).get("dei") or {}

    best = None
    for name in names:
        block = us_gaap.get(name) or dei.get(name)
        if not block:
            continue
        for unit_rows in (block.get("units") or {}).values():
            for row in unit_rows:
                if not _usable(row, as_of):
                    continue
                # A quarterly figure has a ~90 day span, an annual one ~365.
                # Mixing them silently compares a quarter against a year.
                if quarterly and row.get("fp") == "FY":
                    continue
                if best is None or row["filed"] > best["filed"] or (
                        row["filed"] == best["filed"]
                        and row.get("end", "") > best.get("end", "")):
                    best = row
        if best is not None:
            break                    # first tag that has anything usable wins
    return best


def download_bulk(dest: Optional[Path] = None,
                  timeout: int = 1800) -> Path:
    """The whole of EDGAR's XBRL in one file. Gigabytes.

    STREAMED TO DISK, NEVER BUFFERED.
    ---------------------------------
    The obvious `dest.write_bytes(urlopen(...).read())` holds the entire
    archive in memory first. The API container is capped at 1GB and this file
    is larger than that, so the read is killed partway through by the OOM
    killer — which looks from the outside like the download simply hanging,
    with a partial file and no error. Measured: 452MB resident and climbing
    before this was rewritten.

    Written to a `.part` file and renamed only on success, so an interrupted
    download can never be mistaken for a complete one by the next run.
    """
    dest = Path(dest or (STORE / "companyfacts.zip"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")

    req = urllib.request.Request(BULK_URL, headers={"User-Agent": UA})
    log.info("edgar: downloading bulk companyfacts (gigabytes, streaming)")
    got = 0
    last_log = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, \
                part.open("wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            while True:
                block = r.read(1 << 20)          # 1MB at a time
                if not block:
                    break
                f.write(block)
                got += len(block)
                if got - last_log >= 100 << 20:  # every 100MB
                    last_log = got
                    pct = f" ({100.0 * got / total:.0f}%)" if total else ""
                    log.info("edgar: %.0f MB%s", got / 1e6, pct)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        part.unlink(missing_ok=True)
        raise EdgarError(f"bulk download failed after {got / 1e6:.0f} MB: {e}") from e

    part.replace(dest)
    log.info("edgar: bulk file is %.0f MB", dest.stat().st_size / 1e6)
    return dest


def company_facts(cik: int, timeout: int = 30) -> dict:
    """One company, live from the API. For gap-filling, not for bulk work."""
    time.sleep(MIN_INTERVAL)
    return json.loads(_get(FACTS_URL.format(cik=cik), timeout=timeout))


def iter_bulk(path: Optional[Path] = None):
    """(cik, facts) for every filer in the bulk zip, one at a time.

    A generator because the archive holds tens of thousands of JSON documents
    and materialising them all is gigabytes of dictionaries for no reason.
    """
    path = Path(path or (STORE / "companyfacts.zip"))
    if not path.exists():
        raise EdgarError(f"{path} not downloaded — call download_bulk() first")
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            m = re.match(r"CIK(\d+)\.json", name)
            if not m:
                continue
            try:
                yield int(m.group(1)), json.loads(z.read(name))
            except (ValueError, KeyError, zipfile.BadZipFile) as e:
                log.debug("edgar: %s unreadable: %s", name, e)
