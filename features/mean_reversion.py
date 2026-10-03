"""
Mean-reversion features.

Includes the "falling knife" context features described in the strategy
doc: a large |z| alone does NOT mean reversion is likely. We hand the
model the extra context (momentum direction, vol regime, rate of change
of z) so it can learn when reversion signals historically worked vs when
they were actually the start of a genuine breakdown.
"""
import numpy as np
import pandas as pd

from .. import config


def _rsi(prices: pd.Series, window: int) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window).mean()
    avg_loss = loss.rolling(window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def add_mean_reversion_features(df: pd.DataFrame, price_col: str = "last_price") -> pd.DataFrame:
    df = df.copy()
    w = config.MEAN_REV_WINDOW

    roll_mean = df[price_col].rolling(w).mean()
    roll_std = df[price_col].rolling(w).std()

    # core z-score: distance from rolling mean in standard-deviation units.
    # large positive z -> price stretched above its recent mean.
    # large negative z -> price stretched below its recent mean.
    df["zscore"] = (df[price_col] - roll_mean) / roll_std.replace(0, np.nan)

    # rate of change of the z-score itself: is the stretch accelerating
    # (still moving away, "catching the falling knife" risk) or decelerating
    # (losing momentum, more likely to actually mean-revert)?
    df["zscore_roc"] = df["zscore"].diff()

    df["rsi"] = _rsi(df[price_col], w)

    bb_w = config.BOLLINGER_WINDOW
    bb_std_mult = config.BOLLINGER_STD
    bb_mean = df[price_col].rolling(bb_w).mean()
    bb_std = df[price_col].rolling(bb_w).std()
    upper = bb_mean + bb_std_mult * bb_std
    lower = bb_mean - bb_std_mult * bb_std
    # position within the bands, 0 = lower band, 1 = upper band
    df["bollinger_pct"] = (df[price_col] - lower) / (upper - lower).replace(0, np.nan)

    # short-term reversal return: negative of the most recent short move,
    # a naive "would betting against the last move have worked" feature
    df["short_term_reversal"] = -df[price_col].pct_change(1)

    return df
