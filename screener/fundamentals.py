"""The screener fields that come from company filings.

EVERY NUMBER HERE IS ARITHMETIC OVER A FILED LINE ITEM
    Return on equity is net income over shareholders' equity. Current ratio is
    current assets over current liabilities. A data vendor selling these is
    selling the division, not the numerator — which is why this reads EDGAR
    directly and pays nobody.

THE THREE FIELDS THAT ARE NOT HERE, AND WHY THEY NEVER WILL BE
    eps_growth_next_year, eps_growth_next_5y, peg

    All three depend on what analysts EXPECT, which is an opinion published by
    banks and sold by the firms that aggregate it. Nothing in a 10-K implies
    it. They are returned as None, and the screener reports "needs analyst
    estimates" rather than treating the absence as a failed filter — the
    difference between "this stock does not qualify" and "nobody here knows"
    is the whole reason a screener is trustworthy.

FLOWS ARE SUMMED OVER FOUR QUARTERS. STOCKS ARE READ AT A POINT IN TIME.
    This distinction is the whole correctness of the file and getting it wrong
    is invisible in the output.

    A FLOW covers a period: earnings per share, net income, revenue, dividends
    declared. "The most recent one" is a QUARTER for most filers, so using it
    as an annual figure quadruples P/E and quarters the dividend yield. These
    are summed across the last four quarters — measured, not assumed: KO came
    back at a P/E of 96.9 on EPS of $0.91 when its real trailing EPS is about
    $2.90, and nothing anywhere raised.

    A STOCK is a balance at an instant: equity, current assets, debt, shares
    outstanding. Summing four quarters of those would quadruple the balance
    sheet. They take the latest value.

A QUARTER IS DEFINED BY ITS LENGTH, NOT BY ITS LABEL
    US filers report the SAME quarter twice: once as the three months, and
    once as the year to date. Apple's June 2026 quarter appears as both
    `2026-03-29 -> 2026-06-27, 2.02` and `2025-09-28 -> 2026-06-27, 6.88`,
    and BOTH are tagged `fp=Q3` with the same end date. Nothing in the label
    distinguishes them.

    Deduplicating on (end, fp) therefore kept whichever came first, and four
    such "quarters" could be four year-to-date figures stacked on each other.
    Measured: Apple came back at a P/E of 16.1 and a return on equity of 278%
    against real values near 38 and 150.

    So a flow is accepted only if its period is the right LENGTH — about 90
    days for a quarter, about a year for an annual figure. Only one 90-day
    period can end on a given date, which makes the deduplication exact
    rather than arbitrary.

A DIVIDEND FACT DOES NOT MEAN A DIVIDEND
    GameStop pays nothing and came back with a 2.06% yield, because the tag
    still holds a declaration from years ago and "most recently filed" found
    it. Any flow whose period ended more than fifteen months ago is treated as
    absent, which is what it is.

STALENESS IS REPORTED, NOT HIDDEN
    A company that last filed fourteen months ago still has a return on equity
    in this output, and `days_since_filing` beside it. Dropping stale filers
    silently would quietly delist half of the small caps; presenting a
    two-year-old balance sheet as current would be worse.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, Optional

from marketdata.edgar import TAGS, latest_fact

log = logging.getLogger(__name__)

FIELDS = ("market_cap", "shares_outstanding", "roe", "current_ratio",
          "debt_equity", "pe", "eps_ttm", "dividend_yield", "payout_ratio",
          "eps_growth_this_year", "eps_growth_qtr", "sales_growth_qtr",
          "days_since_filing")

# Returned as None by construction. Listed so the screener can tell a field it
# cannot know from a field that simply failed.
NEEDS_ESTIMATES = ("eps_growth_next_year", "eps_growth_next_5y", "peg")


# Concepts that cover a PERIOD and must be summed across four quarters.
# Everything else is a balance at an instant and must not be.
FLOWS = ("revenue", "net_income", "eps_diluted", "dividends_per_share")

# A flow whose period ended longer ago than this is not a current figure.
# Fifteen months, not twelve: a late annual filer is still a going concern,
# and being too strict here blanks small caps wholesale.
STALE_DAYS = 460


def _ttm(facts: dict, concept: str,
         as_of: Optional[date]) -> Optional[float]:
    """A flow over the trailing twelve months.

    Four quarters summed when four quarters exist, otherwise the most recent
    full fiscal year. Never a single quarter presented as a year.
    """
    rows = _series(facts, concept, as_of)
    if not rows:
        return None

    cutoff = (as_of or date.today())
    def fresh(r):
        try:
            end = datetime.strptime(r["end"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            return False
        return 0 <= (cutoff - end).days <= STALE_DAYS

    quarters, seen = [], set()
    for r in reversed(rows):                 # newest period first
        if not fresh(r) or not _spans(r, 80, 100):
            continue
        end = r.get("end")
        if end in seen:                      # the same quarter, restated
            continue
        seen.add(end)
        v = _val(r)
        if v is None:
            continue
        quarters.append(v)
        if len(quarters) == 4:
            return sum(quarters)

    # Not four clean quarters. An annual filing is the honest fallback; a
    # partial sum of two quarters presented as a year would be worse than
    # saying nothing.
    for r in reversed(rows):
        if fresh(r) and _spans(r, 340, 380):
            return _val(r)
    return None


def _spans(entry: dict, low: int, high: int) -> bool:
    """Does this fact cover a period of roughly the expected length?

    The only reliable way to tell a three-month figure from the year-to-date
    one filed beside it: they share an end date and a fiscal-period label and
    differ only in where they start. An entry with no `start` is a balance,
    not a flow, and is never a candidate here.
    """
    start, end = entry.get("start"), entry.get("end")
    if not start or not end:
        return False
    try:
        a = datetime.strptime(start, "%Y-%m-%d").date()
        b = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return False
    return low <= (b - a).days <= high


def _point(facts: dict, concept: str,
           as_of: Optional[date]) -> Optional[float]:
    """A balance at the most recent instant it was reported."""
    return _val(latest_fact(facts, TAGS[concept], as_of=as_of))


def _val(entry: Optional[dict]) -> Optional[float]:
    if not entry:
        return None
    try:
        return float(entry["val"])
    except (KeyError, TypeError, ValueError):
        return None


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Division that returns None rather than inf or a ZeroDivisionError.

    A company with zero equity is not one with infinite return on equity; it
    is one the ratio does not describe. Returning inf would sort it to the top
    of every quality screen.
    """
    if a is None or b is None or b == 0:
        return None
    return a / b


def _growth(new: Optional[float], old: Optional[float]) -> Optional[float]:
    """Percent change, refusing the cases where the sign makes it meaningless.

    Growth from a LOSS to a profit is not a percentage. -0.50 to 0.50 is not
    "200% growth" in any sense a screen should act on, and the sign of the
    denominator flips the direction of the answer. Finviz shows these blank
    and so does this.
    """
    if new is None or old is None or old <= 0:
        return None
    return (new / old - 1.0) * 100.0


def fundamentals(facts: dict, price: Optional[float] = None,
                 as_of: Optional[date] = None) -> Dict[str, Optional[float]]:
    """Every filing-derived screener field for one company."""
    out: Dict[str, Optional[float]] = {k: None for k in FIELDS}
    for k in NEEDS_ESTIMATES:
        out[k] = None
    if not facts:
        return out

    # STOCKS — balances, read at an instant
    equity = _point(facts, "equity", as_of)
    assets_c = _point(facts, "assets_current", as_of)
    liab_c = _point(facts, "liabilities_current", as_of)
    debt_l = _point(facts, "debt_long", as_of)
    debt_s = _point(facts, "debt_short", as_of)

    # FLOWS — periods, summed to twelve months
    net_income = _ttm(facts, "net_income", as_of)
    eps = _ttm(facts, "eps_diluted", as_of)
    dps = _ttm(facts, "dividends_per_share", as_of)

    # Shares outstanding is a balance, but it is reported on the cover of the
    # filing rather than in the statements, so it is read the same way.
    out["shares_outstanding"] = _point(facts, "shares_outstanding", as_of)
    if price is not None and out["shares_outstanding"]:
        out["market_cap"] = price * out["shares_outstanding"]

    roe = _div(net_income, equity)
    out["roe"] = None if roe is None else roe * 100.0

    out["current_ratio"] = _div(assets_c, liab_c)

    total_debt = None
    for part in (debt_l, debt_s):
        if part is not None:
            total_debt = (total_debt or 0.0) + part
    out["debt_equity"] = _div(total_debt, equity)

    out["eps_ttm"] = eps
    if price is not None and eps and eps > 0:
        # NEGATIVE EPS DELIBERATELY LEAVES P/E EMPTY. A loss-making company
        # has a negative P/E, which sorts below every profitable one and so
        # passes every "P/E under 20" filter ever written.
        out["pe"] = price / eps

    if dps is not None and price:
        out["dividend_yield"] = (dps / price) * 100.0
        if eps and eps > 0:
            out["payout_ratio"] = (dps / eps) * 100.0

    out["eps_growth_this_year"] = _year_growth(facts, "eps_diluted", as_of)
    out["eps_growth_qtr"] = _qtr_growth(facts, "eps_diluted", as_of)
    out["sales_growth_qtr"] = _qtr_growth(facts, "revenue", as_of)

    newest = latest_fact(facts, TAGS["equity"], as_of=as_of) or \
        latest_fact(facts, TAGS["net_income"], as_of=as_of)
    if newest and newest.get("filed"):
        try:
            filed = datetime.strptime(newest["filed"], "%Y-%m-%d").date()
            out["days_since_filing"] = float(((as_of or date.today()) - filed).days)
        except ValueError:
            pass
    return out


def _series(facts: dict, concept: str, as_of: Optional[date]):
    """Every usable entry for a concept, newest filing first."""
    from marketdata.edgar import _usable

    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    dei = (facts.get("facts") or {}).get("dei") or {}

    # EVERY tag is collected, not just the first one that has rows.
    #
    # Breaking on the first non-empty tag looked reasonable and silently lost
    # Coca-Cola's dividend: it has stale rows under
    # `CommonStockDividendsPerShareDeclared` and current ones under
    # `CommonStockDividendsPerShareCashPaid`, so stopping at the first left
    # the yield blank on a company that has paid one for sixty years.
    #
    # Merged on (end, filed) so the same period reported under two tags counts
    # once — otherwise a trailing-twelve-month sum could take the same quarter
    # twice and double it.
    seen, rows = set(), []
    for name in TAGS[concept]:
        block = gaap.get(name) or dei.get(name)
        if not block:
            continue
        for unit_rows in (block.get("units") or {}).values():
            for r in unit_rows:
                if not _usable(r, as_of):
                    continue
                # `start` is part of the key: the 3-month and the
                # year-to-date figure share an end date and a label, and
                # collapsing them here would discard the one we want before
                # `_spans` ever sees it.
                key = (r.get("start"), r.get("end"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(r)
    rows.sort(key=lambda r: (r.get("end", ""), r.get("filed", "")))
    return rows


def _qtr_growth(facts: dict, concept: str,
                as_of: Optional[date]) -> Optional[float]:
    """This quarter against the SAME quarter a year ago.

    Year over year, not against last quarter: retailers make most of their
    money in Q4, so a sequential comparison reports every one of them
    collapsing in January and booming in October.
    """
    rows = [r for r in _series(facts, concept, as_of) if r.get("fp") != "FY"]
    if len(rows) < 5:
        return None
    latest = rows[-1]
    fy, fp = latest.get("fy"), latest.get("fp")
    if fy is None or fp is None:
        return None
    prior = [r for r in rows if r.get("fp") == fp and r.get("fy") == fy - 1]
    if not prior:
        return None
    return _growth(_val(latest), _val(prior[-1]))


def _year_growth(facts: dict, concept: str,
                 as_of: Optional[date]) -> Optional[float]:
    """The latest full fiscal year against the one before it."""
    rows = [r for r in _series(facts, concept, as_of) if r.get("fp") == "FY"]
    if len(rows) < 2:
        return None
    return _growth(_val(rows[-1]), _val(rows[-2]))
