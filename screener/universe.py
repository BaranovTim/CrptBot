"""Building the table the screener runs over.

ONE ROW PER TICKER, BUILT ONCE A DAY, SERVED FROM DISK
    Screening 13,000 names means 13,000 rows of about thirty numbers. Computing
    those on request would put minutes of work behind a phone waiting on a
    socket — the same mistake `/api/chart` made when it re-parsed the whole bar
    store per call. So this is a batch job whose output is a file, and the API
    reads the file.

THREE SOURCES, THREE FAILURE MODES, NONE OF THEM FATAL
    prices      Alpaca. Without them there is no row at all.
    filings     SEC EDGAR. Without them the company fields are blank and the
                screener reports them as unjudged rather than as failures.
    short int   FINRA. Without it `float_short` is blank, which costs the
                Short Plays preset one criterion and nothing else.

    A partial table is useful; a missing one is not. So each source is caught
    independently and what arrived is what gets written.

WHY THE BENCHMARK IS FETCHED FIRST AND SEPARATELY
    Beta is measured against SPY, so every row needs the same index series.
    Fetching it once and passing it in makes 13,000 rows cost one extra
    request; fetching it per symbol would cost 13,000.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from marketdata.alpaca import Alpaca, AlpacaError
from screener.technicals import MIN_BARS, technicals

log = logging.getLogger(__name__)

STORE = Path("data_cache") / "screener"
TABLE = STORE / "universe.json"

BENCHMARK = "SPY"

# Enough for SMA200 plus a year of beta, with slack for holidays and halts.
HISTORY_DAYS = 500


# Suffixes that mark an instrument no screen can judge. Warrants, units and
# rights have no earnings, no equity and no float of their own — every company
# field is blank for them by construction, so they arrive in the "unjudged"
# bucket in their thousands and bury the near-misses that are worth reading.
#
# Deliberately NOT a filter on data completeness: a real company that has not
# filed yet must still appear as unjudged. This drops things that can never
# have the data, not things that happen to be missing it.
_UNSCREENABLE_SUFFIXES = (".WS", ".W", ".U", ".R", ".RT", ".P")


def _screenable(symbol: str) -> bool:
    if any(symbol.endswith(sfx) for sfx in _UNSCREENABLE_SUFFIXES):
        return False
    # preferred shares: BRK.A is common, BAC.PRK is preferred
    return ".PR" not in symbol


def build(symbols: Optional[List[str]] = None,
          client: Optional[Alpaca] = None,
          with_filings: bool = True,
          with_short_interest: bool = True,
          chunk: int = 100,
          progress_every: int = 1000) -> Dict[str, dict]:
    """Compute every screener field for every symbol. Minutes, not seconds."""
    client = client or Alpaca()
    if not client.configured:
        raise AlpacaError(
            "no Alpaca credentials — the screener has no price data without "
            "them. See .env.example.")

    if symbols is None:
        symbols = [a["symbol"] for a in client.universe()]
    symbols = sorted({s.upper() for s in symbols if s})
    if len(symbols) > 100:                 # a named handful is never filtered
        before = len(symbols)
        symbols = [s for s in symbols if _screenable(s)]
        log.info("screener: %d symbols, %d dropped as unscreenable",
                 len(symbols), before - len(symbols))
    log.info("screener: building %d symbols", len(symbols))

    start = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)

    # One request, reused by every row. See the module docstring.
    bench = None
    try:
        b = client.bars([BENCHMARK], start=start).get(BENCHMARK)
        if b is not None and not b.empty:
            bench = b["close"]
    except AlpacaError as e:
        log.warning("screener: no benchmark, beta will be blank: %s", e)

    rows: Dict[str, dict] = {}
    t0 = time.time()
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        try:
            bars = client.bars(batch, start=start)
        except AlpacaError as e:
            # One bad chunk must not end a twenty-minute run.
            log.warning("screener: chunk %d failed: %s", i // chunk, e)
            continue
        for sym, df in bars.items():
            if df is None or df.empty:
                continue
            rows[sym] = technicals(df, benchmark=bench)
        if progress_every and (i // chunk) % max(1, progress_every // chunk) == 0:
            log.info("screener: %d/%d symbols, %.0fs elapsed",
                     len(rows), len(symbols), time.time() - t0)

    log.info("screener: %d symbols priced in %.0fs", len(rows), time.time() - t0)

    if with_filings:
        _add_filings(rows)
    if with_short_interest:
        _add_short_interest(rows)
    return rows


def rejoin(rows: Dict[str, dict], with_filings: bool = True,
           with_short_interest: bool = True) -> None:
    """Re-run the joins over an existing table, in place.

    The price fetch is eighteen minutes; the joins are seconds. Correcting a
    failed join should not cost the fetch.
    """
    if with_filings:
        _add_filings(rows)
    if with_short_interest:
        _add_short_interest(rows)


def _add_filings(rows: Dict[str, dict]) -> None:
    """Company fields from EDGAR, for whatever tickers it can be joined to.

    Silent on failure by design: an EDGAR outage should cost the company
    columns, not the whole table. Every field it would have filled is then
    None, which the screener already reports as unjudged.
    """
    try:
        from marketdata.edgar import iter_bulk, ticker_map
        from screener.fundamentals import fundamentals
    except ImportError as e:
        log.warning("screener: fundamentals unavailable: %s", e)
        return

    try:
        tickers = ticker_map()
    except Exception as e:
        log.warning("screener: no ticker->CIK map, company fields blank: %s", e)
        return

    by_cik = {}
    for sym in rows:
        cik = tickers.get(sym)
        if cik is not None:
            by_cik.setdefault(cik, []).append(sym)

    failures: Dict[str, int] = {}

    def apply(cik, facts) -> int:
        n = 0
        for sym in by_cik.get(cik, ()):
            try:
                rows[sym].update(fundamentals(facts,
                                              price=rows[sym].get("price")))
                n += 1
            except Exception as e:          # one malformed filer, not the run
                # COUNTED, not just logged at debug.
                #
                # A rename inside `fundamentals` once made this throw for
                # EVERY company, and the debug-level message meant the table
                # rebuilt cleanly with every company field blank. One filer
                # failing is normal; all of them failing is a bug, and the
                # difference has to be visible without turning on debug logs.
                failures[type(e).__name__] = failures.get(
                    type(e).__name__, 0) + 1
                log.debug("screener: %s fundamentals failed: %s", sym, e)
        return n

    # TWO PATHS, chosen by how many companies are actually wanted.
    #
    # The bulk archive is one request for everything and is the only sane
    # option at full universe scale. But it is hundreds of megabytes, and
    # downloading all of it to fill in three tickers — which is what a
    # `--symbols AAPL,MSFT,NVDA` run does — is absurd. Below the crossover the
    # per-company endpoint is faster even at 10 requests/second.
    # The display name, joined for every symbol whether or not its filings
    # parse. A screener row reading only "CLMT" tells you nothing about what
    # you just matched.
    try:
        from marketdata.edgar import ticker_names

        names = ticker_names()
        for sym in rows:
            n = names.get(sym)
            if n:
                rows[sym]["name"] = n
    except Exception as e:
        log.warning("screener: company names unavailable: %s", e)

    from marketdata.edgar import STORE as EDGAR_STORE
    bulk = EDGAR_STORE / "companyfacts.zip"
    filled = 0

    # SMALL LISTS TAKE THE PER-COMPANY PATH EVEN WHEN THE BULK FILE EXISTS.
    #
    # It used to fall through to the archive as soon as one had been
    # downloaded, so verifying three tickers streamed a 1.4GB zip. On a
    # 967MB box that is not merely slow — it competes for memory with
    # whatever else is running and drops SSH sessions, which is how this was
    # noticed.
    if len(by_cik) <= 300:
        from marketdata.edgar import company_facts
        for cik in by_cik:
            try:
                filled += apply(cik, company_facts(cik))
            except Exception as e:
                log.debug("screener: CIK %s unavailable: %s", cik, e)
    else:
        if not bulk.exists():
            # LOUD, because this is not a degraded corner — it blanks every
            # company field and with them five of the seven presets, and the
            # screener still returns rows so nothing looks broken.
            log.error(
                "screener: %s IS MISSING, so EVERY company field will be "
                "blank — market cap, P/E, ROE, debt/equity, dividends. Five "
                "of the seven presets cannot be judged without it. Run with "
                "--download-filings.", bulk)
            return
        try:
            for cik, facts in iter_bulk(bulk):
                if cik in by_cik:
                    filled += apply(cik, facts)
        except Exception as e:
            log.warning("screener: bulk filings unreadable: %s", e)
    log.info("screener: filings joined for %d of %d symbols", filled, len(rows))
    if failures:
        total = sum(failures.values())
        detail = ", ".join(f"{k} x{v}" for k, v in
                           sorted(failures.items(), key=lambda x: -x[1]))
        # Above a handful it is not bad data, it is broken code.
        speak = log.error if total > max(10, 0.2 * len(by_cik)) else log.info
        speak("screener: %d symbols failed fundamentals (%s)", total, detail)


def _add_short_interest(rows: Dict[str, dict]) -> None:
    """Float short, where both halves of the division exist."""
    try:
        from marketdata import finra
    except ImportError:
        return
    si = finra.load()
    if not si:
        log.info("screener: no short interest on disk, float_short blank")
        return

    filled = 0
    for sym, row in rows.items():
        entry = si.get(sym)
        if not entry:
            continue
        row["shares_short"] = entry.get("shares_short")
        row["short_interest_as_of"] = entry.get("as_of")
        row["days_to_cover"] = entry.get("days_to_cover")
        # Shares outstanding is NOT float — see finra.py. Used only because
        # EDGAR has no float field, and recorded as an approximation so the
        # app can say so rather than implying precision it does not have.
        denom = row.get("float_shares") or row.get("shares_outstanding")
        pct = finra.float_short_pct(entry.get("shares_short"), denom)
        if pct is not None:
            row["float_short"] = pct
            row["float_short_approx"] = 1.0 if not row.get("float_shares") else 0.0
            filled += 1
    log.info("screener: short interest joined for %d symbols", filled)


def save(rows: Dict[str, dict], path: Optional[Path] = None) -> Path:
    path = Path(path or TABLE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"built_at": datetime.now(timezone.utc).isoformat(),
               "symbols": len(rows), "rows": rows}
    tmp = path.with_suffix(".tmp")
    # allow_nan=False: a NaN would serialise as the bare token `NaN`, which is
    # not valid JSON and which Dart's decoder rejects — so one bad number
    # would blank the entire screener page rather than one cell.
    tmp.write_text(json.dumps(_clean(payload), allow_nan=False))
    tmp.replace(path)
    log.info("screener: wrote %d rows to %s", len(rows), path)
    return path


def load(path: Optional[Path] = None) -> dict:
    path = Path(path or TABLE)
    if not path.exists():
        return {"built_at": None, "symbols": 0, "rows": {}}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as e:
        log.warning("screener table unreadable: %s", e)
        return {"built_at": None, "symbols": 0, "rows": {}}


def _clean(obj):
    """NaN and infinity out, None in.

    They arrive from pandas whenever a window is short or a denominator is
    zero, and both mean "no value" here. Keeping them would either break the
    JSON or, worse, let infinity win every `greater than` filter.
    """
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return None if (obj != obj or obj in (float("inf"), float("-inf"))) else obj
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    # ANYTHING ELSE BECOMES None RATHER THAN KILLING THE WRITE.
    #
    # A complex number reached this point once — a negative base raised to a
    # fractional power, which Python returns silently — and `json.dumps` threw
    # at the very end of an eighteen-minute build, discarding all of it. The
    # arithmetic that produced it is fixed, but one unserialisable cell must
    # never again cost the whole table.
    log.warning("screener: dropped unserialisable %s value", type(obj).__name__)
    return None
