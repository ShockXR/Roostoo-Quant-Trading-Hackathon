"""
MODEL 1 -- Model 0 with a long-term trend filter.

Hypothesis: a large one-day drop overshoots and partly retraces when the
asset is in a long-term uptrend (a liquidation flush), but in a downtrend
it is more often the next leg down (a falling knife). Model 0 was only
ever tested in a bull market. Taking Model 0's entries only while the
asset's close is above its `trend_days`-day simple moving average should
keep the bull-market edge and skip bear-market dips.

Exits are unchanged: a position opened in an uptrend is held for the full
`hold` hours even if the filter turns off meanwhile.
"""
import pandas as pd

from . import m0_reversal
from .features import panel

DEFAULTS = {"trend_days": 50}


def uptrend(bars: dict[str, pd.DataFrame], trend_days: int) -> pd.DataFrame:
    close = panel(bars, "close")
    return close > close.rolling(trend_days * 24).mean()


def targets(bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    p = m0_reversal.resolve(params)
    trend_days = int(params.get("trend_days", DEFAULTS["trend_days"]))
    sig = m0_reversal.signal(bars, p["lookback"], p["k"]) & uptrend(bars, trend_days)
    sig = m0_reversal.eligible_entries(sig, params)
    return m0_reversal.hold_signal(sig, p["hold"]) * p["weight"]
