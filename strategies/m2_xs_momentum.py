"""
MODEL 2 -- cross-sectional dual momentum across the large caps.

Hypothesis: crypto returns show cross-sectional momentum at multi-week
horizons (the assets that led over the past weeks keep leading) and
time-series momentum (an asset that rose keeps rising more often than it
reverses). Model 0 is idle most of the time (~14% average exposure) and
misses sustained rallies; a model that holds the strongest few large caps
while they are trending captures the bull-market drift, and the absolute
filter moves it to cash in a broad bear market.

Once a day, at the close of the 23:00 UTC bar:
  1. score each asset by its `lookback_days` return;
  2. keep assets whose score is positive (absolute momentum);
  3. hold the `top_n` highest at `weight` each (optionally scaled down by
     vol_target / the asset's 30-day annualized vol, in 2.5% steps so small
     vol changes do not trade);
  4. hold until the next daily decision.

Optional market filter (`market_sma_days`): hold nothing unless the market
proxy (`market_symbol`, BTC) closes above its `market_sma_days`-day simple
moving average at the decision.
"""
import numpy as np
import pandas as pd

from .. import config
from .features import annualized_vol, panel

DEFAULTS = {"lookback_days": 28, "top_n": 3, "vol_target": None, "decision_hour": 23,
            "market_sma_days": None, "market_symbol": "BTCUSDT"}


def targets(bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    """`lookback_days` may be a list: the average of one portfolio per lookback."""
    p = {**DEFAULTS, **params}
    if isinstance(p["lookback_days"], list):
        parts = [targets(bars, {**p, "lookback_days": lb}) for lb in p["lookback_days"]]
        return sum(parts) / len(parts)
    lookback, top_n = int(p["lookback_days"]), int(p["top_n"])
    weight = float(p.get("weight", config.MAX_POSITION_PER_ASSET))
    close = panel(bars, "close")

    score = close / close.shift(lookback * 24) - 1
    decide = close.index.hour == int(p["decision_hour"])
    s = score[decide]
    if p.get("_eligible") is not None:          # point-in-time universe: rank members only
        s = s.where(p["_eligible"].reindex_like(score)[decide].fillna(False).astype(bool))
    rank = s.where(s > 0).rank(axis=1, ascending=False, method="first")
    w = (rank <= top_n).astype(float) * weight
    if p["vol_target"]:
        vol = annualized_vol(close, 30)[decide]
        scale = (float(p["vol_target"]) / vol).clip(upper=1.0)
        w = (w * scale.fillna(0.0) / 0.025).round() * 0.025
    if p["market_sma_days"]:
        mkt = close[p["market_symbol"]]
        risk_on = (mkt > mkt.rolling(int(p["market_sma_days"]) * 24).mean())[decide]
        w = w.mul(risk_on.astype(float), axis=0)
    out = w.reindex(close.index).ffill().fillna(0.0)
    return out.where(np.isfinite(out), 0.0)
