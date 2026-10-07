"""
Point-in-time liquidity-tier membership, shared by the research builder
(scripts/build_midcap_universe.py) and the live mid-cap book
(data/binance_universe.py), so both apply exactly the same rule.

At the first day of month M, using only daily bars BEFORE M: a USDT pair is
eligible if it was listed at least MIN_AGE_DAYS before M and traded on each
of the last TRADED_DAYS days; eligible pairs are ranked by mean daily USDT
quote volume over the last VOLUME_DAYS days (rank 1 = most liquid).
Ranks 1-20 are the large-cap tier, 21-60 the mid caps, and the mid-cap
production model trades ranks 21-40.
"""
import re

import pandas as pd

MIN_AGE_DAYS = 90
VOLUME_DAYS = 90
TRADED_DAYS = 30
LARGE_RANKS = (1, 20)
MID_RANKS = (21, 60)
MID_TRADED_RANKS = (21, 40)       # the liquid half; ranks 41-60 failed in research

# not tradable "assets": stablecoins, fiat, wrapped / liquid-staking receipts
EXCLUDED_BASES = {
    "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "UST", "USDS", "SUSD", "USDSB", "EUR", "GBP",
    "AUD", "BRL", "TRY", "RUB", "UAH", "NGN", "ZAR", "BIDR", "IDRT", "BVND", "EURI", "AEUR", "XUSD", "USDE",
    "RLUSD", "PYUSD", "BFUSD", "USD1", "USDT", "WBTC", "WBETH", "BETH", "BNSOL", "PAXG", "XAUT", "USDJ",
}
LEVERAGED = re.compile(r".+(UP|DOWN|BULL|BEAR)$")


def is_candidate(symbol: str) -> bool:
    """A spot USDT pair whose base asset is a real, unleveraged crypto asset."""
    if not symbol.endswith("USDT"):
        return False
    base = symbol[:-4]
    return base not in EXCLUDED_BASES and not LEVERAGED.match(base)


def ranks(qvol: pd.DataFrame, close: pd.DataFrame, months: pd.DatetimeIndex) -> pd.DataFrame:
    """Rows: month start (UTC); columns: symbol -> liquidity rank (NaN = not
    eligible). qvol / close: daily bars (UTC midnight index x symbol)."""
    first = close.apply(lambda c: c.first_valid_index())
    rows = {}
    for m in months:
        win = qvol[(qvol.index >= m - pd.Timedelta(days=VOLUME_DAYS)) & (qvol.index < m)]
        traded = (win.tail(TRADED_DAYS) > 0).sum() == TRADED_DAYS
        old = first <= m - pd.Timedelta(days=MIN_AGE_DAYS)
        eligible = traded & old.reindex(traded.index).fillna(False)
        rows[m] = win.mean()[eligible].rank(ascending=False, method="first")
    return pd.DataFrame(rows).T


def members(rank: pd.DataFrame, lo: int, hi: int) -> dict[str, list[str]]:
    """{"YYYY-MM": [symbols with rank in lo..hi]} from ranks()."""
    return {m.strftime("%Y-%m"): sorted(r[(r >= lo) & (r <= hi)].index) for m, r in rank.iterrows()}
