"""The screener: filters, the seven presets, and what "no answer" means.

WHAT THESE GUARD, IN ORDER OF HOW BADLY THEY WOULD MISLEAD

  UNKNOWN TREATED AS FAIL   A company that has not filed, or a field needing
                            analyst estimates this stack does not buy, must
                            not be quietly rejected. Doing so drops most small
                            caps and presents the survivors as "the stocks
                            that match" — a screener lying by omission.

  UNKNOWN TREATED AS PASS   The mirror, and worse: it claims a criterion was
                            met that was never checked.

  A PRESET THAT IS NOT ITS FILTERS  Picking a preset must fill the same
                            controls you would set by hand, so every value
                            stays editable. A preset with hidden logic is a
                            recommendation you cannot argue with.

  NEGATIVE P/E PASSING      A loss-making company has a negative P/E, which is
                            below 20, so "P/E under 20" would return every
                            company losing money.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.engine import evaluate, run
from screener.filters import (BY_ID, FAIL, GT, IS_FALSE, IS_TRUE, LT,
                              PASS, PRESETS, PRESETS_BY_ID, UNAVAILABLE,
                              UNKNOWN, Filter, catalogue)


def test_every_preset_the_user_asked_for_exists():
    wanted = {"buy_and_hold", "oversold_bounce", "breakout", "short_plays",
              "ma_bounce", "dividend", "highest_earning"}
    assert wanted <= set(PRESETS_BY_ID), wanted - set(PRESETS_BY_ID)
    assert len(PRESETS) == 7, [p.id for p in PRESETS]
    return True


def test_every_preset_filter_names_a_real_field():
    """A typo here silently drops a criterion instead of raising."""
    for p in PRESETS:
        for f in p.filters:
            assert f.field in BY_ID, f"{p.id} filters on unknown {f.field!r}"
    return True


def test_a_preset_is_nothing_but_editable_filter_rows():
    """The requirement: applying a recommendation fills the controls, so
    every value can then be changed."""
    for p in PRESETS:
        j = p.to_json()
        assert j["filters"], p.id
        for f in j["filters"]:
            assert "field" in f and "op" in f
            # a threshold filter must expose its number, or it cannot be edited
            if f["op"] in ("gt", "gte", "lt", "lte"):
                assert "value" in f, f"{p.id}: {f} has no editable value"
    return True


def test_the_presets_carry_the_thresholds_that_were_specified():
    """Spot-checks against the values as given, so a later refactor cannot
    quietly move someone's screen."""
    bh = {f.field: f for f in PRESETS_BY_ID["buy_and_hold"].filters}
    assert bh["market_cap"].value == 50e6
    assert bh["beta"].value == 1.5
    assert bh["roe"].value == 15
    assert bh["current_ratio"].value == 1.5

    ob = {f.field: f for f in PRESETS_BY_ID["oversold_bounce"].filters}
    assert ob["price"].value == 5
    assert ob["rsi14"].op == LT and ob["rsi14"].value == 30
    assert ob["change_pct"].op == GT and ob["change_pct"].value == 0

    # the one combination easiest to get backwards: above the 20, BELOW the 50
    mab = {f.field: f for f in PRESETS_BY_ID["ma_bounce"].filters}
    assert mab["above_sma20"].op == IS_TRUE
    assert mab["above_sma50"].op == IS_FALSE

    div = {f.field: f for f in PRESETS_BY_ID["dividend"].filters}
    assert div["market_cap"].value == 10e9
    assert div["pe"].op == LT and div["pe"].value == 20
    assert div["payout_ratio"].value == 50
    return True


def test_a_missing_metric_is_unknown_and_never_a_rejection():
    f = Filter("roe", GT, 15)
    assert f.check({"roe": 20}) == PASS
    assert f.check({"roe": 3}) == FAIL
    assert f.check({"roe": None}) == UNKNOWN
    assert f.check({}) == UNKNOWN
    return True


def test_fields_needing_analyst_estimates_are_unknown_even_when_a_value_exists():
    """Belt and braces: if a value ever appears in one of these — from a
    stale cache, a future provider, a test fixture — it must still be
    reported as unjudged rather than silently trusted."""
    for fid in UNAVAILABLE:
        assert Filter(fid, GT, 1).check({fid: 999}) == UNKNOWN, fid
    assert "eps_growth_next_5y" in UNAVAILABLE
    assert "peg" in UNAVAILABLE
    return True


def test_unjudged_rows_are_counted_separately_from_matches():
    """The number that stops the screen lying by omission."""
    universe = {
        "AAA": {"roe": 30, "price": 10},        # passes both
        "BBB": {"roe": 2, "price": 10},         # fails on merit
        "CCC": {"roe": None, "price": 10},      # never filed
    }
    filters = [Filter("roe", GT, 15), Filter("price", GT, 5)]

    out = run(universe, filters)
    assert out["matched"] == 1, out
    assert out["unjudged"] == 1, out
    assert out["scanned"] == 3
    assert [r["symbol"] for r in out["rows"]] == ["AAA"]

    # and they can be asked for explicitly, still labelled
    out2 = run(universe, filters, include_unknown=True)
    assert {r["symbol"] for r in out2["rows"]} == {"AAA", "CCC"}
    ccc = [r for r in out2["rows"] if r["symbol"] == "CCC"][0]
    assert ccc["unknown"] == ["roe"] and ccc["passed"] == ["price"]
    return True


def test_a_row_says_which_filter_rejected_it():
    """So "why isn't NVDA here" is answerable on screen."""
    row = evaluate("NVDA", {"roe": 40, "pe": 60, "price": None},
                   [Filter("roe", GT, 15), Filter("pe", LT, 20),
                    Filter("price", GT, 5)])
    assert row.passed == ["roe"]
    assert row.failed == ["pe"]
    assert row.unknown == ["price"]
    assert not row.matched
    return True


def test_a_loss_making_company_does_not_pass_a_cheap_pe_screen():
    """A negative P/E is below 20. `fundamentals` leaves it None for exactly
    this reason, and the filter must then treat it as unjudged."""
    from screener.fundamentals import fundamentals

    facts = _facts(eps=-3.0, equity=1000.0, net_income=-300.0)
    m = fundamentals(facts, price=50.0)
    assert m["pe"] is None, m["pe"]
    assert Filter("pe", LT, 20).check(m) == UNKNOWN
    return True


def test_booleans_read_the_way_the_preset_reads():
    assert Filter("above_sma20", IS_TRUE).check({"above_sma20": 1.0}) == PASS
    assert Filter("above_sma20", IS_TRUE).check({"above_sma20": 0.0}) == FAIL
    assert Filter("above_sma50", IS_FALSE).check({"above_sma50": 0.0}) == PASS
    assert Filter("above_sma50", IS_FALSE).check({"above_sma50": 1.0}) == FAIL
    return True


def test_sorting_never_puts_a_missing_value_at_the_top():
    """None must not sort as negative infinity — that is how an empty field
    wins an ascending sort and leads the results."""
    universe = {"AAA": {"roe": 30}, "BBB": {"roe": None}, "CCC": {"roe": 10}}
    for desc in (True, False):
        out = run(universe, [], sort_by="roe", descending=desc,
                  include_unknown=True)
        assert out["rows"][-1]["symbol"] == "BBB", (desc, out["rows"])
    return True


def test_the_catalogue_describes_the_screen_without_the_app_hardcoding_it():
    c = catalogue()
    assert len(c["presets"]) == 7
    assert {f["id"] for f in c["fields"]} >= {"rsi14", "roe", "float_short"}
    # the app has to be able to explain the gap, so it has to be told about it
    assert "eps_growth_next_5y" in c["unavailable"]
    dividend = [p for p in c["presets"] if p["id"] == "dividend"][0]
    assert set(dividend["unavailable"]) == {"eps_growth_next_5y", "peg",
                                            "eps_growth_next_year"}
    return True


def test_relative_volume_is_documented_as_a_ratio():
    """The field that arrived specified as `Over 100K`. Whatever the number
    ends up being, the help text has to say what the units are."""
    assert "RATIO" in BY_ID["rel_volume"].help
    for pid in ("oversold_bounce", "short_plays"):
        rv = {f.field: f for f in PRESETS_BY_ID[pid].filters}["rel_volume"]
        assert rv.value is not None and rv.value < 100, (pid, rv.value)
    return True


def _facts(eps=None, equity=None, net_income=None):
    """Minimal EDGAR-shaped fixture."""
    def unit(val):
        return {"units": {"USD": [{"val": val, "end": "2026-06-30",
                                   "filed": "2026-07-30", "fy": 2026,
                                   "fp": "Q2"}]}}
    facts = {"facts": {"us-gaap": {}}}
    if eps is not None:
        facts["facts"]["us-gaap"]["EarningsPerShareDiluted"] = unit(eps)
    if equity is not None:
        facts["facts"]["us-gaap"]["StockholdersEquity"] = unit(equity)
    if net_income is not None:
        facts["facts"]["us-gaap"]["NetIncomeLoss"] = unit(net_income)
    return facts


# ---------------------------------------------- flows versus balances
def _recent_quarter_ends(n=4):
    """The last `n` completed quarters, counted back from today.

    Generated rather than hardcoded: a fixture with fixed 2026 dates has its
    later quarters ending in the FUTURE for most of that year, and the
    freshness guard correctly refuses them — so the test fails for a reason
    that has nothing to do with what it is testing.
    """
    import datetime as _dt

    today = _dt.date.today()
    last_day = {3: 31, 6: 30, 9: 30, 12: 31}
    # Step back a QUARTER at a time. An earlier version moved a cursor to the
    # day before the quarter end it had just rejected, which lands back inside
    # the same quarter and loops forever.
    year, month = today.year, ((today.month - 1) // 3) * 3 + 3
    ends = []
    while len(ends) < n:
        end = _dt.date(year, month, last_day[month])
        if end < today:
            ends.append(end)
        month -= 3
        if month <= 0:
            month += 12
            year -= 1
    return [e.isoformat() for e in reversed(ends)]     # oldest first


def _quarters(values, ends):
    """Three-month facts, with a real `start` — a flow is now identified by
    the LENGTH of its period, because the label cannot distinguish a quarter
    from the year-to-date figure filed beside it."""
    import datetime as _dt

    fps = ("Q1", "Q2", "Q3", "Q4")
    out = []
    for v, e in zip(values, ends):
        end = _dt.date.fromisoformat(e)
        out.append({"val": v, "start": (end - _dt.timedelta(days=90)).isoformat(),
                    "end": e, "filed": e, "fy": int(e[:4]),
                    "fp": fps[(int(e[5:7]) - 1) // 3]})
    return {"units": {"USD": out}}


def _four_quarter_facts(eps_per_qtr, dps_per_qtr=None, equity=None):
    ends = _recent_quarter_ends(4)
    gaap = {"EarningsPerShareDiluted": _quarters(eps_per_qtr, ends)}
    if dps_per_qtr is not None:
        gaap["CommonStockDividendsPerShareDeclared"] = _quarters(
            dps_per_qtr, ends)
    if equity is not None:
        gaap["StockholdersEquity"] = {"units": {"USD": [
            {"val": equity, "end": ends[-1], "filed": ends[-1],
             "fy": int(ends[-1][:4]), "fp": "Q4"}]}}
    return {"facts": {"us-gaap": gaap}}


def test_a_quarterly_eps_is_never_used_as_an_annual_one():
    """THE BUG THIS PINS, measured on real data.

    KO came back at a P/E of 96.9 on EPS of $0.91 — its real trailing EPS is
    about $2.90. `latest_fact` returned the most recently FILED value, which
    for most companies is a quarter, and using it as a year quadruples P/E and
    quarters the dividend yield. Nothing raised; the number simply rendered.
    """
    from screener.fundamentals import fundamentals

    facts = _four_quarter_facts([0.70, 0.72, 0.74, 0.74], equity=1000.0)
    m = fundamentals(facts, price=100.0)

    # 0.70+0.72+0.74+0.74 = 2.90, not the 0.74 of the latest quarter
    assert m["eps_ttm"] is not None
    assert abs(m["eps_ttm"] - 2.90) < 1e-6, m["eps_ttm"]
    assert abs(m["pe"] - (100.0 / 2.90)) < 1e-6, m["pe"]
    return True


def test_the_dividend_is_the_year_not_the_quarter():
    """AAPL came back at a 0.24% yield against a real ~0.4%, for the same
    reason: one quarterly declaration read as the annual payment."""
    from screener.fundamentals import fundamentals

    facts = _four_quarter_facts([1.0, 1.0, 1.0, 1.0],
                                dps_per_qtr=[0.25, 0.25, 0.26, 0.26])
    m = fundamentals(facts, price=100.0)
    assert abs(m["dividend_yield"] - 1.02) < 1e-6, m["dividend_yield"]
    # and the payout ratio uses the same twelve months on both halves
    assert abs(m["payout_ratio"] - 25.5) < 1e-6, m["payout_ratio"]
    return True


def test_a_balance_sheet_item_is_never_summed():
    """Equity is a balance, not a flow. Adding four quarters of it would
    quadruple the denominator of every ratio built on it."""
    from screener.fundamentals import fundamentals

    ends = _recent_quarter_ends(4)
    facts = {"facts": {"us-gaap": {
        # a balance has an instant, not a period — no `start`
        "StockholdersEquity": {"units": {"USD": [
            {"val": 1000.0, "end": e, "filed": e, "fy": int(e[:4]), "fp": "Q4"}
            for e in ends]}},
        "NetIncomeLoss": _quarters([25.0] * 4, ends),
    }}}
    m = fundamentals(facts, price=10.0)
    # ROE = 100 of income over 1000 of equity, not over 4000
    assert abs(m["roe"] - 10.0) < 1e-6, m["roe"]
    return True


def test_a_dividend_declared_years_ago_is_not_a_current_yield():
    """GameStop pays nothing and came back with a 2.06% yield, because the
    tag still held a declaration from years earlier and 'most recently filed'
    found it."""
    from screener.fundamentals import fundamentals

    facts = {"facts": {"us-gaap": {
        "CommonStockDividendsPerShareDeclared": {"units": {"USD": [
            {"val": 0.38, "start": "2018-12-31", "end": "2019-03-31",
             "filed": "2019-04-15", "fy": 2019, "fp": "Q1"}]}}}}}
    m = fundamentals(facts, price=18.45)
    assert m["dividend_yield"] is None, m["dividend_yield"]
    return True


def test_an_annual_filing_is_used_when_four_quarters_are_not_there():
    """Foreign filers and small caps often report annually only. Blanking
    them would be stricter than the data requires."""
    import datetime as _dt

    from screener.fundamentals import fundamentals

    end = _dt.date.today() - _dt.timedelta(days=60)
    facts = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD": [
        {"val": 4.0, "start": (end - _dt.timedelta(days=364)).isoformat(),
         "end": end.isoformat(), "filed": end.isoformat(), "fy": 2026,
         "fp": "FY"}]}}}}}
    m = fundamentals(facts, price=40.0)
    assert m["eps_ttm"] == 4.0
    assert abs(m["pe"] - 10.0) < 1e-9
    return True


def test_the_unjudged_list_leads_with_the_closest_misses():
    """Alphabetical order put a warrant that passed nothing above a company
    that cleared six of seven criteria. The bucket is only useful if what is
    worth a second look is at the top of it."""
    universe = {
        "ZZZZ": {"a": 1, "b": 1, "c": 1, "d": None},   # 3 passed, 1 unknown
        "AAAA": {"a": None, "b": None, "c": None, "d": None},   # 0 passed
        "MMMM": {"a": 1, "b": None, "c": None, "d": None},      # 1 passed
    }
    filters = [Filter(k, GT, 0) for k in ("a", "b", "c", "d")]
    out = run(universe, filters, include_unknown=True)
    assert [r["symbol"] for r in out["rows"]] == ["ZZZZ", "MMMM", "AAAA"], \
        [r["symbol"] for r in out["rows"]]
    assert out["matched"] == 0 and out["unjudged"] == 3
    return True


def test_instruments_that_can_never_be_screened_are_left_out():
    """Warrants, units, rights and preferreds have no earnings or equity of
    their own, so every company field is blank by construction and they
    arrive in the unjudged bucket in their thousands.

    NOT a completeness filter: a real company that simply has not filed yet
    must still appear as unjudged."""
    from screener.universe import _screenable

    for junk in ("AAC.WS", "AAC.U", "SPCE.W", "XYZ.R", "BAC.PRK"):
        assert not _screenable(junk), junk
    for real in ("AAPL", "BRK.A", "MSFT", "GME"):
        assert _screenable(real), real
    return True


def test_a_year_to_date_figure_is_never_counted_as_a_quarter():
    """THE BUG THIS PINS, seen on Apple's real filings.

    US filers report the same quarter twice: once as three months, once as
    the year to date. Apple's June 2026 quarter is BOTH
    `2026-03-29 -> 2026-06-27 = 2.02` and `2025-09-28 -> 2026-06-27 = 6.88`,
    with the same end date and the same `fp=Q3`. Nothing in the label tells
    them apart, so deduplicating on (end, fp) kept an arbitrary one — and four
    year-to-date figures summed gave Apple a P/E of 16.1 and a return on
    equity of 278%, against real values near 38 and 150.
    """
    import datetime as _dt

    from screener.fundamentals import fundamentals

    ends = _recent_quarter_ends(4)
    rows = []
    for e in ends:
        end = _dt.date.fromisoformat(e)
        fp = ("Q1", "Q2", "Q3", "Q4")[(end.month - 1) // 3]
        # the three-month figure
        rows.append({"val": 2.0,
                     "start": (end - _dt.timedelta(days=90)).isoformat(),
                     "end": e, "filed": e, "fy": end.year, "fp": fp})
        # and the year-to-date figure filed beside it, same end, same label
        rows.append({"val": 6.0,
                     "start": (end - _dt.timedelta(days=272)).isoformat(),
                     "end": e, "filed": e, "fy": end.year, "fp": fp})

    facts = {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD": rows}}}}}
    m = fundamentals(facts, price=100.0)

    # four real quarters of 2.0, not four year-to-date figures of 6.0
    assert m["eps_ttm"] is not None
    assert abs(m["eps_ttm"] - 8.0) < 1e-6, m["eps_ttm"]
    assert abs(m["pe"] - 12.5) < 1e-6, m["pe"]
    return True


def test_a_stock_split_does_not_invert_multi_year_eps_growth():
    """THE BUG THIS PINS, found on NVIDIA's real filings.

    As-filed XBRL is not split-adjusted: the original 10-Q for a pre-split
    year reports pre-split EPS forever. Later filings restate the same period
    on a post-split basis. Keeping the FIRST value seen for a period compared
    pre-split cents with post-split cents and produced -6.6% five-year EPS
    growth for a company whose sales grew 67% over the same window.

    The rule is simply that a restatement supersedes the original — the
    latest FILED version of a period wins.
    """
    from screener.fundamentals import fundamentals

    def fy(val, end, filed):
        return {"val": val, "start": f"{int(end[:4]) - 1}{end[4:]}",
                "end": end, "filed": filed, "fy": int(end[:4]), "fp": "FY"}

    import datetime as _dt

    newest = _dt.date.today() - _dt.timedelta(days=40)
    old_end = newest.replace(year=newest.year - 3).isoformat()
    recent = newest.isoformat()

    facts = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {
        "units": {"USD": [
            # the original, pre-split: $20 a share
            fy(20.0, old_end, old_end),
            # the same period restated after a 10-for-1 split: $2 a share
            fy(2.0, old_end, newest.replace(year=newest.year - 1).isoformat()),
            fy(4.0, recent, recent),
        ]}}}}}

    m = fundamentals(facts, price=100.0)
    # 2.00 -> 4.00 over three years is +26% a year. Comparing the pre-split
    # 20.00 against 4.00 would give roughly -38%.
    g = m["eps_growth_3y"]
    assert g is not None and 20 < g < 30, g
    return True


def test_growth_is_annualised_not_cumulative():
    """A screener's "past 5 years" means the compound annual rate. Reporting
    the total would make every threshold about five times too easy."""
    from screener.fundamentals import _annualised

    import datetime as _dt

    # EXACTLY three years apart. The rate is computed over the ACTUAL span
    # between the two filings, not the requested one — a 2.6 year gap
    # annualised as 3 years would understate the rate — so a fixture that is
    # only roughly three years apart tests arithmetic rather than intent.
    newest = _dt.date.today() - _dt.timedelta(days=40)
    older = newest.replace(year=newest.year - 3)

    def fy(val, end):
        return {"val": val,
                "start": end.replace(year=end.year - 1).isoformat(),
                "end": end.isoformat(), "filed": end.isoformat(),
                "fy": end.year, "fp": "FY"}

    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        fy(100.0, older), fy(200.0, newest),
    ]}}}}}
    # 100 -> 200 over 3 years is 26% a year, not 100%
    g = _annualised(facts, "revenue", None, years=3)
    assert g is not None and 25 < g < 27, g

    # and the span really is measured: the same doubling over one year is
    # 100%, not 26%
    facts1 = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        fy(100.0, newest.replace(year=newest.year - 1)), fy(200.0, newest),
    ]}}}}}
    g1 = _annualised(facts1, "revenue", None, years=1)
    assert g1 is not None and 99 < g1 < 101, g1
    return True


def test_growth_from_a_loss_is_blank_rather_than_a_giant_number():
    """A company that went from a loss to a profit has no meaningful growth
    RATE — the percentage change from a negative base has the wrong sign and
    an arbitrary magnitude."""
    from screener.fundamentals import _annualised

    import datetime as _dt
    year = _dt.date.today().year
    recent = (_dt.date.today() - _dt.timedelta(days=40)).isoformat()

    def fy(val, end):
        return {"val": val, "start": f"{int(end[:4]) - 1}{end[4:]}",
                "end": end, "filed": end, "fy": int(end[:4]), "fp": "FY"}

    facts = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {
        "units": {"USD": [fy(-2.0, f"{year - 3}-12-31"), fy(3.0, recent)]}}}}}
    assert _annualised(facts, "eps_diluted", None, years=3) is None
    return True


def test_a_negative_denominator_leaves_the_multiple_blank():
    """Negative equity produces a negative P/B, which sorts below every
    healthy company and so passes every "cheap" filter ever written. Same
    reasoning as the negative P/E rule."""
    from screener.fundamentals import fundamentals

    import datetime as _dt
    ends = _recent_quarter_ends(4)
    facts = {"facts": {"us-gaap": {
        "StockholdersEquity": {"units": {"USD": [
            {"val": -500.0, "end": ends[-1], "filed": ends[-1],
             "fy": int(ends[-1][:4]), "fp": "Q4"}]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 1000.0, "end": ends[-1], "filed": ends[-1],
             "fy": int(ends[-1][:4]), "fp": "Q4"}]}},
    }}}
    m = fundamentals(facts, price=10.0)
    assert m["market_cap"] == 10000.0
    assert m["pb"] is None, m["pb"]
    return True
