"""Who counts as important, and why each one is on the list.

Every CIK here was resolved from the SEC's own ticker->CIK mapping
(https://www.sec.gov/files/company_tickers.json), not typed from memory.
A wrong CIK does not error - it just silently returns another company's
filings forever, which is exactly the kind of failure that never surfaces.

WHAT IS AND IS NOT HERE
-----------------------
IN:  companies whose insiders and treasuries genuinely move with crypto -
     the large corporate holders, the miners, the exchanges, the ETF
     issuers. Their Form 4 filings are exact, legally required, and free.

OUT: individual "crypto influencers" and anonymous whale wallets. Not from
     squeamishness - they are simply not tractable:
       * X/Twitter API pricing puts a real feed out of reach, and scraping
         breaches their terms
       * TradingView publishes no API for ideas, and scraping breaches
         their terms
       * anonymous wallets need Arkham/Nansen/Whale Alert, all paid, and
         the labels are guesses that get gamed
     `onchain.py` has the adapter for a paid wallet feed if you buy one.

THE HONEST RANKING
------------------
An exchange or miner insider selling tells you about THEIR equity, not
about bitcoin. Only the treasury holders' 8-K purchases and the miners'
production disclosures are close to a crypto signal. `crypto_proximity`
records that distance so nothing downstream mistakes an MSTR option
exercise for a view on BTC.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Entity:
    name: str
    cik: int                    # verified against the SEC ticker map
    ticker: str
    category: str
    # 0..1: how much this entity's own trading says about CRYPTO rather than
    # just about its own share price
    crypto_proximity: float
    note: str = ""

    @property
    def cik_padded(self) -> str:
        return f"CIK{self.cik:010d}"


# ordered roughly by how much their actions bear on crypto
WATCHLIST: Tuple[Entity, ...] = (
    # --- corporate treasuries: they literally buy and hold BTC -----------
    Entity("Strategy Inc (MicroStrategy)", 1050446, "MSTR", "treasury", 0.95,
           "largest corporate BTC holder; 8-K filings announce purchases"),
    Entity("Tesla, Inc.", 1318605, "TSLA", "treasury", 0.60,
           "holds BTC on the balance sheet; disclosed in 10-Q/10-K"),
    Entity("Block, Inc.", 1512673, "XYZ", "treasury", 0.70,
           "BTC on balance sheet plus bitcoin-native business lines"),

    # --- miners: their selling is real BTC supply hitting the market ------
    Entity("MARA Holdings, Inc.", 1507605, "MARA", "miner", 0.80,
           "large holder; monthly production and sales disclosures"),
    Entity("Riot Platforms, Inc.", 1167419, "RIOT", "miner", 0.80,
           "miner treasury; sells production into the market"),
    Entity("CleanSpark, Inc.", 827876, "CLSK", "miner", 0.75, "miner treasury"),
    Entity("Core Scientific, Inc.", 1839341, "CORZ", "miner", 0.70, "miner"),
    Entity("TeraWulf Inc.", 1083301, "WULF", "miner", 0.65, "miner"),
    Entity("Hut 8 Corp.", 1964789, "HUT", "miner", 0.70, "miner treasury"),
    Entity("Cipher Mining", 1819989, "CIFR", "miner", 0.65, "miner"),
    Entity("Bit Digital, Inc", 1710350, "BTBT", "miner", 0.60, "miner"),
    Entity("IREN Ltd", 1878848, "IREN", "miner", 0.60, "miner"),

    # --- venues and issuers: flow and custody, not directional views ------
    Entity("Coinbase Global, Inc.", 1679788, "COIN", "exchange", 0.55,
           "largest US venue; insider trades are about COIN equity"),
    Entity("Galaxy Digital Inc.", 1859392, "GLXY", "institution", 0.65,
           "crypto-native merchant bank"),
    Entity("Robinhood Markets, Inc.", 1783879, "HOOD", "exchange", 0.35,
           "retail flow venue; only partly crypto"),
    Entity("Grayscale Bitcoin Trust ETF", 1588489, "GBTC", "issuer", 0.50,
           "ETF flows move spot, but filings are fund mechanics"),
    Entity("BlackRock, Inc.", 2012383, "BLK", "institution", 0.30,
           "IBIT issuer; BLK insider trades say nothing about BTC"),
)

BY_TICKER: Dict[str, Entity] = {e.ticker: e for e in WATCHLIST}
BY_CIK: Dict[int, Entity] = {e.cik: e for e in WATCHLIST}


def entities(min_proximity: float = 0.0,
             categories: Optional[List[str]] = None) -> List[Entity]:
    """Filter the list. Default returns everything."""
    out = [e for e in WATCHLIST if e.crypto_proximity >= min_proximity]
    if categories:
        wanted = {c.lower() for c in categories}
        out = [e for e in out if e.category.lower() in wanted]
    return out


def describe() -> str:
    lines = [f"{len(WATCHLIST)} tracked entities "
             f"(CIKs verified against the SEC ticker map)", ""]
    by_cat: Dict[str, List[Entity]] = {}
    for e in WATCHLIST:
        by_cat.setdefault(e.category, []).append(e)
    for cat, items in by_cat.items():
        lines.append(f"  {cat}:")
        for e in sorted(items, key=lambda x: -x.crypto_proximity):
            lines.append(f"    {e.ticker:6} {e.name[:38]:40} "
                         f"crypto-proximity {e.crypto_proximity:.2f}")
    lines.append("")
    lines.append("  crypto-proximity = how much this entity's own trading")
    lines.append("  says about CRYPTO rather than about its own share price.")
    return "\n".join(lines)
