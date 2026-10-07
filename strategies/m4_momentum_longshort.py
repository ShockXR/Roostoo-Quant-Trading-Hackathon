"""
MODEL 4 (research iteration 4) -- Model 3 plus a short leg in risk-off.

Weakness addressed: Model 3 can only sit in cash in a bear market (flat
Oct 2025 - Mar 2026, -14% in the Jan-Feb 2025 roll-over).
Hypothesis: time-series momentum also holds on the way down, so while BTC
is below its 100-day SMA, shorting the `short_n` weakest assets (most
negative `lookback_days` return) earns in persistent declines. Roostoo
exposes short endpoints (/v6/short_open); borrow cost is not modelled.
"""
import pandas as pd

from . import m3_momentum_regime
from .features import panel

DEFAULTS = {"short_n": 3}


def targets(bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    p = {**m3_momentum_regime.DEFAULTS, **DEFAULTS, **params}
    long_leg = m3_momentum_regime.targets(bars, {k: v for k, v in p.items() if k != "short_n"})
    close = panel(bars, "close")
    weight = float(p.get("weight", m3_momentum_regime.m2_xs_momentum.config.MAX_POSITION_PER_ASSET))
    decide = close.index.hour == 23
    score = (close / close.shift(int(p["lookback_days"]) * 24) - 1)[decide]
    mkt = close[p["market_symbol"]]
    risk_off = (mkt <= mkt.rolling(int(p["market_sma_days"]) * 24).mean())[decide]
    rank = score.where(score < 0).rank(axis=1, ascending=True, method="first")
    short = -(rank <= int(p["short_n"])).astype(float).mul(risk_off.astype(float), axis=0) * weight
    short = short.reindex(close.index).ffill().fillna(0.0)
    return long_leg + short
