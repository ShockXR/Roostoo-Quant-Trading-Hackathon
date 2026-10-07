"""
Mid-cap momentum family (mid-cap research, branch midcap-model-research).

Generalises Model 2/3 (left untouched for large-cap reproducibility) with
the options the mid-cap hypotheses need. With the defaults it produces the
same targets as Model 3.

Every `rebalance_days` days, at the close of the `decision_hour` (23:00 UTC) bar:
  1. score each eligible asset on the `lookback_days` window ending
     `skip_days` ago: plain return (`score="return"`) or return divided by
     the window's daily-return volatility (`score="sharpe"`);
  2. keep positive scores (absolute momentum), take the `top_n` best;
  3. weight them equally at `weight`, or (`weighting="inv_vol"`) in inverse
     proportion to 30-day vol with the same total, each capped at
     config.MAX_POSITION_PER_ASSET;
  4. hold nothing if the regime is off: `regime="btc"` -> BTC close below
     its `regime_sma_days` SMA; `regime="basket"` -> the equal-weight index
     of eligible assets below its SMA; `regime="both"` -> either BTC below
     its `btc_sma_days` SMA or the basket below its `regime_sma_days` SMA;
     `regime=None` -> no filter.
Optional `volume_confirm`: only assets whose 14-day mean volume exceeds
their 60-day mean can be selected.
"""
import numpy as np
import pandas as pd

from .. import config
from .features import panel

DEFAULTS = {"lookback_days": 28, "skip_days": 0, "score": "return", "top_n": 3, "weighting": "equal",
            "regime": "btc", "regime_sma_days": 100, "btc_sma_days": 100, "rebalance_days": 1,
            "market_symbol": "BTCUSDT", "volume_confirm": False, "decision_hour": 23}


def _regime_on(close: pd.DataFrame, p: dict, eligible: pd.DataFrame | None) -> pd.Series:
    if p["regime"] is None:
        return pd.Series(True, index=close.index)
    if p["regime"] == "both":                    # BTC trend AND the mid-cap basket's own trend
        btc = _regime_on(close, {**p, "regime": "btc", "regime_sma_days": p["btc_sma_days"]}, eligible)
        return btc & _regime_on(close, {**p, "regime": "basket"}, eligible)
    if p["regime"] == "btc":
        idx = close[p["market_symbol"]]
    else:
        elig = eligible.reindex_like(close).fillna(False).astype(bool) if eligible is not None \
            else pd.DataFrame(True, index=close.index, columns=close.columns)
        r = close.pct_change().where(elig.shift(1, fill_value=False)).mean(axis=1).fillna(0.0)
        idx = (1 + r).cumprod()
    return idx > idx.rolling(int(p["regime_sma_days"]) * 24).mean()


def targets(bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    """`lookback_days` may be a list: the average of one portfolio per lookback."""
    p = {**DEFAULTS, **params}
    if isinstance(p["lookback_days"], list):
        parts = [targets(bars, {**p, "lookback_days": lb}) for lb in p["lookback_days"]]
        return sum(parts) / len(parts)
    eligible = p.get("_eligible")
    close = panel(bars, "close")
    lb, skip, top_n = int(p["lookback_days"]) * 24, int(p["skip_days"]) * 24, int(p["top_n"])
    weight = float(p.get("weight", config.MAX_POSITION_PER_ASSET))

    end = close.shift(skip)
    score = end / close.shift(skip + lb) - 1
    daily_r = close / close.shift(24) - 1
    if p["score"] == "sharpe":
        score = score / daily_r.shift(skip).rolling(lb).std()

    day = (close.index - pd.Timestamp("2020-01-01", tz="UTC")).days
    decide = (close.index.hour == int(p["decision_hour"])) & (day % int(p["rebalance_days"]) == 0)
    s = score[decide]
    if eligible is not None:
        s = s.where(eligible.reindex_like(score)[decide].fillna(False).astype(bool))
    if p["volume_confirm"]:                      # rising participation: 14-day mean volume > 60-day
        qv = panel(bars, "qvol") if "qvol" in next(iter(bars.values())) else panel(bars, "volume")
        s = s.where((qv.rolling(14 * 24).mean() > qv.rolling(60 * 24).mean())[decide])
    picked = s.where(s > 0).rank(axis=1, ascending=False, method="first") <= top_n

    if p["weighting"] == "inv_vol":
        inv = 1 / daily_r.rolling(30 * 24).std()[decide]
        raw = inv.where(picked)
        w = raw.div(raw.sum(axis=1), axis=0) * weight * picked.sum(axis=1).values[:, None]
        w = w.clip(upper=config.MAX_POSITION_PER_ASSET).fillna(0.0)
    else:
        w = picked.astype(float) * weight

    w = w.mul(_regime_on(close, p, eligible)[decide].astype(float), axis=0)
    out = w.reindex(close.index).ffill().fillna(0.0)
    return out.where(np.isfinite(out), 0.0)
