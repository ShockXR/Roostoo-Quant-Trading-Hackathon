"""
MODEL 3 -- Model 2 with a market regime filter (research iteration 2).

Weakness addressed: Model 2 lost 35% in the 2022-23 bear market by buying
short-lived rallies that had a positive 28-day return and then faded.
Hypothesis: cross-sectional momentum whipsaws in bear markets; holding
nothing unless BTC closes above its 100-day SMA removes most of those
trades. 100 days is the centre of the 50-200 day range that improved
train Sharpe in every tested cell (experiments/long/G04_m2_market); a fast
20-day filter did not help.

Frozen parameters: 28-day lookback, top 3, 20% each, daily decision at the
23:00 UTC bar close, BTC > 100-day SMA.
"""
import pandas as pd

from . import m2_xs_momentum

DEFAULTS = {"lookback_days": 28, "top_n": 3, "market_sma_days": 100, "market_symbol": "BTCUSDT"}


def targets(bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    return m2_xs_momentum.targets(bars, {**DEFAULTS, **params})
