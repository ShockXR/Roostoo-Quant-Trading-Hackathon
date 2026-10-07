"""
MID-CAP MODEL (frozen) -- momentum with a dual market regime filter,
traded on the more liquid half of the mid-cap tier.

Universe (part of the model): Binance USDT pairs ranked 21-40 by 90-day
mean quote volume at the start of each month (scripts/build_midcap_universe.py;
backtests use --universe mid_caps_pit_21_40).

Once a day at the 23:00 UTC bar close: hold the 3 members with the highest
positive 28-day return at 20% each, but only while BTC is above its 100-day
SMA AND the equal-weight basket of the mid-cap members is above its
150-day SMA; otherwise cash.

Why each piece (experiments/long/mid/RESEARCH_LOG.md):
- momentum core = the large-cap winner (28 days, top 3);
- basket confirmation (G15): mid caps bleed while BTC holds up; the
  dual filter cut drawdowns from ~50% to 25-40% across a 9-cell plateau
  (basket 100-200 x BTC 50-200 days); 150/100 is its centre;
- liquidity half (G18/G20): momentum worked in ranks 21-40 and failed in
  ranks 41-60 (negative train Sharpe at every lookback).
"""
import pandas as pd

from . import mc_momentum

DEFAULTS = {"lookback_days": 28, "top_n": 3, "regime": "both", "regime_sma_days": 150, "btc_sma_days": 100}
UNIVERSE = "mid_caps_pit_21_40"


def targets(bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    return mc_momentum.targets(bars, {**DEFAULTS, **params})
