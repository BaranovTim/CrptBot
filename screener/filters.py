"""What you can filter on, and the seven preset combinations.

A PRESET IS A STARTING POSITION, NOT A QUERY
    Every preset expands into ordinary filter rows the moment it is applied.
    There is no hidden "buy and hold" logic anywhere — picking one fills the
    same controls you would have set by hand, and every value is then yours to
    change. That was the requirement, and it is also the honest design: a
    recommendation you cannot inspect is a recommendation you cannot disagree
    with.

THREE KINDS OF ANSWER, NOT TWO
    PASS, FAIL, and UNKNOWN. A filter on a company that has not filed, or on a
    field that needs analyst estimates nobody here has, is UNKNOWN — it is not
    a FAIL. Collapsing the two would quietly drop every company with a gap in
    its filings and present the survivors as "the stocks that match", which is
    a screener lying by omission.

    Unknown rows are excluded from results by default and counted separately,
    so the page can say "412 matched, 89 unknown" instead of pretending the 89
    were rejected on the merits.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- fields

NUMBER, PERCENT, CURRENCY, RATIO, BOOL, SHARES = (
    "number", "percent", "currency", "ratio", "bool", "shares")


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    group: str
    kind: str
    # What the value cannot be computed from. Empty means price data alone is
    # enough, which is also the set that works with no paid data at all.
    needs: str = "price"
    help: str = ""


FIELDS: Tuple[Field, ...] = (
    Field("price", "Price", "Price & volume", CURRENCY),
    Field("change_pct", "Change", "Price & volume", PERCENT,
          help="Percent move on the last closed session."),
    Field("above_sma20", "Price vs SMA20", "Price & volume", BOOL),
    Field("above_sma50", "Price vs SMA50", "Price & volume", BOOL),
    Field("above_sma200", "Price vs SMA200", "Price & volume", BOOL),
    Field("rsi14", "RSI(14)", "Price & volume", NUMBER,
          help="Wilder's RSI, the same definition the models use."),
    Field("at_50d_high", "At 50-day high", "Price & volume", BOOL),
    Field("at_50d_low", "At 50-day low", "Price & volume", BOOL),
    Field("avg_volume", "Average volume", "Price & volume", SHARES,
          help="Mean of the previous 50 sessions, excluding today."),
    Field("current_volume", "Current volume", "Price & volume", SHARES),
    Field("rel_volume", "Relative volume", "Price & volume", RATIO,
          help="Today's volume divided by the 50-day average. A RATIO — "
               "2 means twice normal. Not a share count."),
    Field("beta", "Beta", "Price & volume", RATIO,
          help="Against SPY, one year of daily returns."),

    Field("market_cap", "Market cap", "Company", CURRENCY, needs="filings"),
    Field("roe", "Return on equity", "Company", PERCENT, needs="filings"),
    Field("current_ratio", "Current ratio", "Company", RATIO, needs="filings"),
    Field("debt_equity", "Debt / equity", "Company", RATIO, needs="filings"),
    Field("pe", "P/E", "Company", RATIO, needs="filings",
          help="Blank for loss-making companies — a negative P/E would pass "
               "every 'under 20' filter ever written."),
    Field("dividend_yield", "Dividend yield", "Company", PERCENT,
          needs="filings"),
    Field("payout_ratio", "Payout ratio", "Company", PERCENT, needs="filings"),
    Field("eps_growth_this_year", "EPS growth this year", "Growth", PERCENT,
          needs="filings"),
    Field("eps_growth_qtr", "EPS growth qtr/qtr", "Growth", PERCENT,
          needs="filings",
          help="Against the same quarter last year, not the previous one."),
    Field("sales_growth_qtr", "Sales growth qtr/qtr", "Growth", PERCENT,
          needs="filings"),

    Field("float_short", "Float short", "Short interest", PERCENT,
          needs="finra",
          help="FINRA short interest over float. Published twice a month, so "
               "it is days old by construction."),

    Field("gap_pct", "Gap", "Price & volume", PERCENT,
          help="Open against the previous close."),
    Field("change_from_open", "Change from open", "Price & volume", PERCENT),
    Field("atr_pct", "Average true range", "Price & volume", PERCENT,
          help="ATR(14) as a percent of price, so it compares across "
               "stocks at different prices."),
    Field("volatility_w", "Volatility (week)", "Price & volume", PERCENT),
    Field("volatility_m", "Volatility (month)", "Price & volume", PERCENT),
    Field("at_20d_high", "At 20-day high", "Price & volume", BOOL),
    Field("at_20d_low", "At 20-day low", "Price & volume", BOOL),
    Field("at_52w_high", "At 52-week high", "Price & volume", BOOL),
    Field("at_52w_low", "At 52-week low", "Price & volume", BOOL),
    Field("off_52w_high", "Below 52-week high", "Price & volume", PERCENT,
          help="How far under the 52-week high, as a positive percent."),
    Field("off_52w_low", "Above 52-week low", "Price & volume", PERCENT),
    Field("at_all_time_high", "At all-time high", "Price & volume", BOOL,
          help="All-time within the history held — about ten years, not "
               "since listing."),
    Field("perf_week", "Performance (week)", "Performance", PERCENT),
    Field("perf_month", "Performance (month)", "Performance", PERCENT),
    Field("perf_quarter", "Performance (quarter)", "Performance", PERCENT),
    Field("perf_half", "Performance (6 months)", "Performance", PERCENT),
    Field("perf_year", "Performance (year)", "Performance", PERCENT),
    Field("perf_ytd", "Performance (YTD)", "Performance", PERCENT),

    Field("pb", "P/B", "Valuation", RATIO, needs="filings"),
    Field("ps", "P/S", "Valuation", RATIO, needs="filings"),
    Field("price_cash", "Price / cash", "Valuation", RATIO, needs="filings"),
    Field("price_fcf", "Price / free cash flow", "Valuation", RATIO,
          needs="filings"),
    Field("ev_ebitda", "EV / EBITDA", "Valuation", RATIO, needs="filings"),
    Field("ev_sales", "EV / sales", "Valuation", RATIO, needs="filings"),

    Field("roa", "Return on assets", "Quality", PERCENT, needs="filings"),
    Field("roic", "Return on invested capital", "Quality", PERCENT,
          needs="filings"),
    Field("quick_ratio", "Quick ratio", "Quality", RATIO, needs="filings"),
    Field("lt_debt_equity", "LT debt / equity", "Quality", RATIO,
          needs="filings"),
    Field("gross_margin", "Gross margin", "Quality", PERCENT,
          needs="filings"),
    Field("operating_margin", "Operating margin", "Quality", PERCENT,
          needs="filings"),
    Field("net_margin", "Net profit margin", "Quality", PERCENT,
          needs="filings"),

    Field("eps_growth_ttm", "EPS growth TTM", "Growth", PERCENT,
          needs="filings"),
    Field("sales_growth_ttm", "Sales growth TTM", "Growth", PERCENT,
          needs="filings"),
    Field("eps_growth_3y", "EPS growth past 3 years", "Growth", PERCENT,
          needs="filings", help="Annualised."),
    Field("eps_growth_5y", "EPS growth past 5 years", "Growth", PERCENT,
          needs="filings", help="Annualised."),
    Field("sales_growth_3y", "Sales growth past 3 years", "Growth", PERCENT,
          needs="filings", help="Annualised."),
    Field("sales_growth_5y", "Sales growth past 5 years", "Growth", PERCENT,
          needs="filings", help="Annualised."),
    Field("dividend_growth_5y", "Dividend growth", "Growth", PERCENT,
          needs="filings", help="Annualised over five years."),

    Field("shares_outstanding", "Shares outstanding", "Company", SHARES,
          needs="filings"),
    Field("float_shares", "Float", "Company", SHARES, needs="filings"),
    Field("days_to_cover", "Days to cover", "Short interest", RATIO,
          needs="finra"),
    Field("sector_code", "Sector (SIC)", "Company", NUMBER, needs="filings",
          help="The SEC's own SIC classification, as a number. Filterable "
               "by range: 2000-3999 is manufacturing, 6000-6799 finance."),

    # Declared, and permanently blank on this data stack. Kept in the
    # registry so the app can SAY they are unavailable rather than quietly
    # omitting a filter the user came looking for.
    Field("forward_pe", "Forward P/E", "Valuation", RATIO, needs="estimates"),
    Field("target_price", "Target price", "Analyst", CURRENCY,
          needs="estimates"),
    Field("analyst_recom", "Analyst recommendation", "Analyst", RATIO,
          needs="estimates"),
    Field("earnings_date_days", "Days to earnings", "Analyst", NUMBER,
          needs="estimates"),
    Field("eps_surprise", "Earnings surprise", "Analyst", PERCENT,
          needs="estimates"),
    Field("insider_ownership", "Insider ownership", "Ownership", PERCENT,
          needs="estimates"),
    Field("institutional_ownership", "Institutional ownership", "Ownership",
          PERCENT, needs="estimates"),
    Field("eps_growth_next_year", "EPS growth next year", "Growth", PERCENT,
          needs="estimates",
          help="Analyst consensus. NOT AVAILABLE on the free data stack."),
    Field("eps_growth_next_5y", "EPS growth next 5 years", "Growth", PERCENT,
          needs="estimates",
          help="Analyst long-term growth consensus. NOT AVAILABLE on the "
               "free data stack."),
    Field("peg", "PEG", "Growth", RATIO, needs="estimates",
          help="P/E over forward growth, so it inherits the estimate "
               "problem. NOT AVAILABLE on the free data stack."),
)

BY_ID: Dict[str, Field] = {f.id: f for f in FIELDS}

# Fields nothing in the free stack can populate. Kept in the registry rather
# than deleted so a preset can still name them and the page can explain the
# gap instead of silently dropping a criterion the user asked for.
UNAVAILABLE = tuple(f.id for f in FIELDS if f.needs == "estimates")


# --------------------------------------------------------------- filters

GT, GTE, LT, LTE, BETWEEN, IS_TRUE, IS_FALSE = (
    "gt", "gte", "lt", "lte", "between", "is_true", "is_false")

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


@dataclass
class Filter:
    field: str
    op: str
    value: Optional[float] = None
    value2: Optional[float] = None

    def to_json(self) -> dict:
        d = {"field": self.field, "op": self.op}
        if self.value is not None:
            d["value"] = self.value
        if self.value2 is not None:
            d["value2"] = self.value2
        return d

    @staticmethod
    def from_json(d: dict) -> "Filter":
        return Filter(field=str(d["field"]), op=str(d.get("op", GT)),
                      value=_num(d.get("value")),
                      value2=_num(d.get("value2")))

    def check(self, row: Dict[str, Any]) -> str:
        """PASS, FAIL or UNKNOWN for one stock."""
        if self.field in UNAVAILABLE:
            return UNKNOWN
        v = row.get(self.field)
        if v is None:
            return UNKNOWN
        try:
            v = float(v)
        except (TypeError, ValueError):
            return UNKNOWN

        if self.op == IS_TRUE:
            return PASS if v else FAIL
        if self.op == IS_FALSE:
            return FAIL if v else PASS
        if self.value is None:
            return UNKNOWN
        if self.op == GT:
            return PASS if v > self.value else FAIL
        if self.op == GTE:
            return PASS if v >= self.value else FAIL
        if self.op == LT:
            return PASS if v < self.value else FAIL
        if self.op == LTE:
            return PASS if v <= self.value else FAIL
        if self.op == BETWEEN and self.value2 is not None:
            return PASS if self.value <= v <= self.value2 else FAIL
        return UNKNOWN


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- presets

@dataclass
class Preset:
    id: str
    name: str
    note: str
    filters: List[Filter] = dc_field(default_factory=list)

    def to_json(self) -> dict:
        missing = sorted({f.field for f in self.filters
                          if f.field in UNAVAILABLE})
        return {
            "id": self.id,
            "name": self.name,
            "note": self.note,
            # ONLY THE CRITERIA THAT CAN ACTUALLY BE JUDGED.
            #
            # Shipping the unjudgeable ones made every affected preset return
            # "0 matched, 671 unjudged" — technically honest and practically
            # useless, because the screen you asked for produced nothing. A
            # preset is a starting position, and a starting position that
            # matches nothing is not one.
            #
            # The dropped criteria are still named below, so the page can say
            # what it left out instead of pretending the screen was complete.
            "filters": [f.to_json() for f in self.filters
                        if f.field not in UNAVAILABLE],
            "dropped": [f.to_json() for f in self.filters
                        if f.field in UNAVAILABLE],
            "unavailable": missing,
        }


MICRO_CAP = 50e6
SMALL_CAP = 300e6
LARGE_CAP = 10e9

PRESETS: Tuple[Preset, ...] = (
    Preset("buy_and_hold", "Recommended for Buy and Hold",
           "Profitable, solvent, growing, and already trending up.",
           [Filter("market_cap", GTE, MICRO_CAP),
            Filter("above_sma20", IS_TRUE),
            Filter("beta", GT, 1.5),
            Filter("eps_growth_next_5y", GT, 10),
            Filter("roe", GT, 15),
            Filter("current_ratio", GT, 1.5)]),

    Preset("oversold_bounce", "Recommended for oversold bounce plays",
           "Sold off hard, turning up today, on real volume.",
           [Filter("price", GT, 5),
            Filter("rsi14", LT, 30),
            Filter("change_pct", GT, 0),
            # Sent as "Over 100K". Relative volume is a RATIO — volume over
            # its own average — so a share count cannot be compared against
            # it. Implemented as the Finviz-equivalent `Over 2` and flagged;
            # every value here is editable, so correcting it is one tap.
            Filter("rel_volume", GT, 2)]),

    Preset("breakout", "Recommended for a breakout",
           "Above every major average, at a new 50-day high, and solvent.",
           [Filter("above_sma20", IS_TRUE),
            Filter("above_sma50", IS_TRUE),
            Filter("at_50d_high", IS_TRUE),
            Filter("roe", GT, 20),
            Filter("debt_equity", LT, 1),
            Filter("above_sma200", IS_TRUE),
            Filter("avg_volume", GT, 100e3)]),

    Preset("short_plays", "Recommended for Short Plays",
           "Heavily shorted and liquid enough to get out of.",
           [Filter("market_cap", GTE, SMALL_CAP),
            Filter("float_short", GT, 20),
            Filter("avg_volume", GT, 500e3),
            Filter("rel_volume", GT, 2),      # see the note above
            Filter("current_volume", GT, 500e3)]),

    Preset("ma_bounce", "Recommended for Moving average bounce plays",
           "Reclaiming the 20-day while still under the 50-day.",
           [Filter("above_sma20", IS_TRUE),
            Filter("above_sma50", IS_FALSE),
            Filter("avg_volume", GT, 400e3),
            Filter("rel_volume", GT, 1),
            Filter("current_volume", GT, 2e6)]),

    Preset("dividend", "Recommended for Dividend Plays",
           "Large, paying, cheap, and covering the dividend.",
           [Filter("market_cap", GTE, LARGE_CAP),
            Filter("dividend_yield", GT, 0),
            Filter("pe", LT, 20),
            Filter("eps_growth_next_5y", GT, 5),
            Filter("payout_ratio", LT, 50),
            Filter("peg", LT, 1),
            Filter("eps_growth_next_year", GT, 5)]),

    Preset("highest_earning", "Highest Earning Companies",
           "Growing earnings and sales fast, and the market agrees.",
           [Filter("above_sma200", IS_TRUE),
            Filter("avg_volume", GT, 400e3),
            Filter("rsi14", GT, 50),
            Filter("eps_growth_this_year", GT, 25),
            Filter("eps_growth_qtr", GT, 25),
            Filter("eps_growth_next_year", GT, 25),
            Filter("sales_growth_qtr", GT, 25)]),
)

PRESETS_BY_ID: Dict[str, Preset] = {p.id: p for p in PRESETS}


# ------------------------------------------------------------ crypto
#
# A SEPARATE SET, not the equity presets with the company fields removed.
#
# Half the equity criteria are category errors here: a perpetual future has
# no earnings, no equity and no dividend, so "P/E under 20" is not a filter
# that returns nothing — it is a question that cannot be asked. And crypto has
# a criterion equities do not: whether this app has a fitted model for the
# pair, which is the whole point of the screener existing inside this app.

CRYPTO_FIELDS: Tuple[Field, ...] = (
    Field("price", "Price", "Price & volume", CURRENCY),
    Field("change_pct", "24h change", "Price & volume", PERCENT),
    Field("quote_volume", "24h volume", "Price & volume", CURRENCY,
          help="In dollars, not tokens. Base volume ranks a billion-supply "
               "memecoin above Bitcoin."),
    Field("rsi14", "RSI (14)", "Price & volume", NUMBER),
    Field("above_sma20", "Price vs SMA20", "Price & volume", BOOL),
    Field("above_sma50", "Price vs SMA50", "Price & volume", BOOL),
    Field("above_sma200", "Price vs SMA200", "Price & volume", BOOL),
    Field("at_50d_high", "At 50-day high", "Price & volume", BOOL),
    Field("at_52w_high", "At 52-week high", "Price & volume", BOOL),
    Field("off_52w_high", "Below 52-week high", "Price & volume", PERCENT),
    Field("rel_volume", "Relative volume", "Price & volume", RATIO,
          help="Today against its own 50-day average. A RATIO, not a count."),
    Field("atr_pct", "Average true range", "Price & volume", PERCENT),
    Field("volatility_m", "Volatility (month)", "Price & volume", PERCENT),
    Field("beta", "Beta vs BTC", "Price & volume", RATIO,
          help="Against BTC, not an equity index — in this market BTC is "
               "the market."),
    Field("perf_week", "Performance (week)", "Performance", PERCENT),
    Field("perf_month", "Performance (month)", "Performance", PERCENT),
    Field("perf_quarter", "Performance (quarter)", "Performance", PERCENT),
    Field("perf_year", "Performance (year)", "Performance", PERCENT),
    Field("trained", "Bot has a model", "This app", BOOL,
          help="Whether Vanth has a fitted model for this pair on any "
               "timeframe. Nothing else screens on this."),
)

CRYPTO_BY_ID: Dict[str, Field] = {f.id: f for f in CRYPTO_FIELDS}

CRYPTO_PRESETS: Tuple[Preset, ...] = (
    Preset("crypto_oversold", "Oversold bounce",
           "Sold off hard, still liquid, turning up.",
           [Filter("rsi14", LT, 35),
            Filter("quote_volume", GT, 50e6),
            Filter("change_pct", GT, 0),
            Filter("rel_volume", GT, 1.5)]),

    Preset("crypto_breakout", "Breakout momentum",
           "Above every average and at a new high, on real volume.",
           [Filter("above_sma20", IS_TRUE),
            Filter("above_sma50", IS_TRUE),
            Filter("above_sma200", IS_TRUE),
            Filter("at_50d_high", IS_TRUE),
            Filter("quote_volume", GT, 100e6)]),

    Preset("crypto_confluence", "Top confluence",
           "What the bot has a model for AND the chart agrees with.",
           [Filter("trained", IS_TRUE),
            Filter("above_sma200", IS_TRUE),
            Filter("rsi14", LT, 70),
            Filter("quote_volume", GT, 100e6)]),

    Preset("crypto_dip", "Deep discount",
           "Well off the highs but still trading.",
           [Filter("off_52w_high", GT, 50),
            Filter("quote_volume", GT, 50e6),
            Filter("rsi14", LT, 45)]),

    Preset("crypto_momentum", "Strong momentum",
           "Outperforming over a month, not just today.",
           [Filter("perf_month", GT, 20),
            Filter("above_sma20", IS_TRUE),
            Filter("quote_volume", GT, 100e6),
            Filter("rsi14", LT, 75)]),

    Preset("crypto_calm", "Low volatility majors",
           "Large and comparatively quiet.",
           [Filter("quote_volume", GT, 500e6),
            Filter("volatility_m", LT, 4),
            Filter("above_sma50", IS_TRUE)]),
)

CRYPTO_PRESETS_BY_ID: Dict[str, Preset] = {
    p.id: p for p in CRYPTO_PRESETS}


def catalogue(market: str = "stocks") -> dict:
    """Everything the app needs to draw the screener without hardcoding it."""
    crypto = market == "crypto"
    fields = CRYPTO_FIELDS if crypto else FIELDS
    presets = CRYPTO_PRESETS if crypto else PRESETS
    return {
        "market": market,
        "fields": [{"id": f.id, "label": f.label, "group": f.group,
                    "kind": f.kind, "needs": f.needs, "help": f.help}
                   for f in fields],
        "operators": [GT, GTE, LT, LTE, BETWEEN, IS_TRUE, IS_FALSE],
        "presets": [p.to_json() for p in presets],
        # Nothing in the crypto set needs analyst estimates — there are no
        # analysts to estimate a perpetual's next-year earnings.
        "unavailable": [] if crypto else list(UNAVAILABLE),
    }


def presets_for(market: str) -> Dict[str, Preset]:
    return CRYPTO_PRESETS_BY_ID if market == "crypto" else PRESETS_BY_ID
