"""Insider and whale tracking.

The failure this file mostly guards against is a MISREADING, not a crash.

In a real week of SEC data the two largest transactions on the watchlist
were both code F - shares withheld automatically to pay tax on vesting.
A naive tracker headlines that as "RIOT EXECUTIVE DUMPS $3M". It is not a
sale in any meaningful sense; nobody decided anything. Size is not
conviction, and these tests pin that.

The second guard is the same point-in-time discipline the news feed has: a
Form 4 trade on the 24th filed on the 26th was not knowable on the 24th.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import timedelta

import pandas as pd

from monitor import whale_verdict
from whalefeed import WATCHLIST, WhaleEvent, WhaleStore, entities, parse_form4
from whalefeed.watchlist import BY_TICKER, Entity

FORM4 = """<ownershipDocument>
  <issuer><issuerName>Riot Platforms, Inc.</issuerName></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerName>Doe Jane A</rptOwnerName>
  </reportingOwnerId><reportingOwnerRelationship>
  <officerTitle>Chief Financial Officer</officerTitle>
  </reportingOwnerRelationship></reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-08-24</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def _entity() -> Entity:
    return BY_TICKER["RIOT"]


def _parse(code: str, shares: float, price: float, ad: str):
    xml = FORM4.format(code=code, shares=shares, price=price, ad=ad)
    return parse_form4(xml, _entity(), "2026-08-26T17:03:56.000Z",
                       "https://example.test/form4")


def _event(**kw) -> WhaleEvent:
    base = dict(actor="Doe Jane A", actor_kind="insider", action="SELL",
                asset="RIOT", amount_usd=1_000_000.0, quantity=1000.0,
                price=1000.0, transaction_code="S", source="sec-form4",
                event_time="2026-08-24T00:00:00Z",
                published_at="2026-08-26T17:03:56Z",
                meta={"crypto_proximity": 0.80})
    base.update(kw)
    return WhaleEvent(**base)


# ------------------------------------------------- the misreading
def test_tax_withholding_is_not_a_sale_signal():
    """Code F is automatic. The biggest filing of the week is usually one."""
    huge_mechanical = _event(transaction_code="F", amount_usd=3_000_000.0)
    impact, side = whale_verdict(huge_mechanical)
    assert side == "NO ACTION", f"a $3m tax withholding produced {side!r}"
    assert "MECHANICAL" in impact
    assert huge_mechanical.conviction == 0.0
    return True


def test_option_exercise_is_not_a_view():
    for code in ("M", "A", "G", "C", "X"):
        e = _event(transaction_code=code, amount_usd=5_000_000.0)
        assert e.conviction == 0.0, f"code {code} should carry no conviction"
        assert whale_verdict(e)[1] == "NO ACTION"
    return True


def test_open_market_purchase_is_the_strongest_signal():
    """Code P is someone choosing to buy with their own money."""
    buy = _event(transaction_code="P", action="BUY", amount_usd=1_000_000.0)
    assert buy.conviction == 1.0
    impact, side = whale_verdict(buy)
    assert side == "BUY" and "BULLISH" in impact
    return True


def test_low_crypto_proximity_damps_the_signal():
    """A BlackRock insider selling BLK says nothing about bitcoin."""
    near = _event(transaction_code="P", action="BUY", amount_usd=1_000_000.0,
                  meta={"crypto_proximity": 0.95})
    far = _event(transaction_code="P", action="BUY", amount_usd=1_000_000.0,
                 meta={"crypto_proximity": 0.30})
    assert whale_verdict(near)[0].startswith("STRONG")
    assert not whale_verdict(far)[0].startswith("STRONG"), \
        "a distant entity produced a strong crypto signal"
    return True


def test_small_transactions_are_ignored():
    tiny = _event(transaction_code="P", action="BUY", amount_usd=5_000.0)
    assert whale_verdict(tiny)[1] == "NO ACTION"
    return True


# ------------------------------------------------ point in time
def test_observable_at_uses_the_filing_not_the_trade():
    """A trade on the 24th filed on the 26th was not knowable on the 24th."""
    e = _event()
    assert e.event_time < e.published_at
    assert e.observable_at() == e.published_at, \
        "observable_at drifted toward the trade date - that is two free days"

    lagged = e.observable_at(timedelta(minutes=5))
    assert lagged == e.published_at + timedelta(minutes=5)
    return True


def test_ingested_at_wins_when_we_saw_it_later():
    e = _event(ingested_at="2026-08-27T09:00:00Z")
    assert e.observable_at() == e.ingested_at, \
        "observable_at ignored a later ingestion time"
    return True


def test_store_dedupes_and_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        store = WhaleStore(Path(tmp))
        # set ingested_at explicitly: otherwise the store stamps it as NOW,
        # and observable_at correctly becomes now - which is right behaviour
        # but makes any fixed cutoff in a test meaningless
        events = [_event(ingested_at="2026-08-26T17:30:00Z"),
                  _event(ingested_at="2026-08-26T17:30:00Z",
                         amount_usd=2_000_000.0, quantity=2000.0)]
        assert store.append(events) == 2
        assert store.append(events) == 0, "the same filing was stored twice"

        back = store.load()
        assert len(back) == 2
        assert {e.id for e in back} == {e.id for e in events}
        assert all(e.ingested_at is not None for e in back)

        cutoff = pd.Timestamp("2026-08-26T18:00:00Z").to_pydatetime()
        assert len(store.visible_at(cutoff)) == 2

        early = pd.Timestamp("2026-08-25T00:00:00Z").to_pydatetime()
        assert store.visible_at(early) == [], \
            "an event was visible before it was filed"

        # and the trade date itself must NOT make it visible - the filing is
        # two days later, and that gap is exactly the free lookahead this
        # whole design exists to refuse
        trade_day = pd.Timestamp("2026-08-24T23:59:00Z").to_pydatetime()
        assert store.visible_at(trade_day) == [], \
            "an event was visible on its trade date, before it was filed"
    return True


# --------------------------------------------------- parsing
def test_form4_parsing_extracts_direction_and_amount():
    sells = _parse("S", 1000, 12.5, "D")
    assert len(sells) == 1
    e = sells[0]
    assert e.action == "SELL" and e.quantity == 1000 and e.price == 12.5
    assert abs(e.amount_usd - 12_500) < 1e-6
    assert e.transaction_code == "S"
    assert e.actor == "Doe Jane A" and e.title == "Chief Financial Officer"

    buys = _parse("P", 500, 10.0, "A")
    assert buys[0].action == "BUY", "acquired code did not map to BUY"
    return True


def test_form4_parsing_survives_junk():
    assert parse_form4("<ownershipDocument></ownershipDocument>",
                       _entity(), "2026-08-26T17:03:56.000Z", "u") == []
    assert parse_form4("not xml at all", _entity(),
                       "2026-08-26T17:03:56.000Z", "u") == []
    return True


def test_zero_share_rows_are_skipped():
    assert _parse("S", 0, 12.5, "D") == []
    return True


# -------------------------------------------------- watchlist
def test_watchlist_is_well_formed():
    seen_cik, seen_ticker = set(), set()
    for e in WATCHLIST:
        assert 0 < e.cik < 10 ** 10, f"{e.ticker} has an implausible CIK"
        assert e.cik not in seen_cik, f"duplicate CIK {e.cik}"
        assert e.ticker not in seen_ticker, f"duplicate ticker {e.ticker}"
        assert 0.0 <= e.crypto_proximity <= 1.0
        assert e.category and e.name
        seen_cik.add(e.cik)
        seen_ticker.add(e.ticker)
    return True


def test_watchlist_filtering():
    high = entities(min_proximity=0.7)
    assert all(e.crypto_proximity >= 0.7 for e in high)
    assert len(high) < len(WATCHLIST)
    miners = entities(categories=["miner"])
    assert miners and all(e.category == "miner" for e in miners)
    return True


def test_treasury_holders_rank_above_distant_institutions():
    """MicroStrategy's actions bear on BTC. BlackRock's own equity does not."""
    assert BY_TICKER["MSTR"].crypto_proximity > BY_TICKER["BLK"].crypto_proximity
    assert BY_TICKER["MARA"].crypto_proximity > BY_TICKER["HOOD"].crypto_proximity
    return True


def test_bad_action_is_rejected():
    try:
        _event(action="HOLD")
    except ValueError:
        return True
    raise AssertionError("an invalid action was accepted")


def test_missing_published_at_is_rejected():
    try:
        WhaleEvent(actor="x", actor_kind="insider", action="BUY", asset="BTC",
                   amount_usd=1.0, source="t", published_at=None)
    except ValueError:
        return True
    raise AssertionError("an event with no public timestamp was accepted")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(tests)} whalefeed tests passed.")
