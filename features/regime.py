"""
Market / regime features.

Kept as simple, interpretable rule-based features fed directly into the
ML model, rather than a separate classifier stage -- per the strategy
doc, we only add a dedicated regime-classification layer if these
simple features don't already move the needle in the ablation study.
"""
import numpy as np
import pandas as pd

from .. import config


def add_regime_features(df: pd.DataFrame, price_col: str = "last_price") -> pd.DataFrame:
    df = df.copy()
    vol_w = config.REGIME_VOL_WINDOW
    trend_w = config.REGIME_TREND_WINDOW

    rets = df[price_col].pct_change()

    # realized volatility and whether it's expanding or contracting
    df["realized_vol"] = rets.rolling(vol_w).std()
    previous_vol = df["realized_vol"].shift(vol_w)
    df["vol_change"] = (df["realized_vol"] / previous_vol.replace(0, np.nan) - 1).mask((previous_vol == 0) & (df["realized_vol"] == 0), 0.)

    # simple trend-strength proxy: |net move| relative to the sum of
    # absolute step-to-step moves over the window. Close to 1 = smooth
    # persistent trend, close to 0 = choppy/sideways.
    net_move = df[price_col].diff(trend_w).abs()
    path_length = rets.abs().rolling(trend_w).sum() * df[price_col].shift(trend_w)
    df["trend_strength"] = (net_move / path_length.replace(0, np.nan)).clip(0, 1).mask(path_length == 0, 0.)

    return df


def attach_market_wide_features(
    per_asset_df: pd.DataFrame,
    market_df: pd.DataFrame,
) -> pd.DataFrame:
    """Attach market-proxy (e.g. BTC) momentum/vol as context features."""
    df = per_asset_df.copy()
    df["market_realized_vol"] = market_df["realized_vol"].reindex(df.index)
    df["market_ret_5"] = market_df.get("ret_5", pd.Series(index=df.index)).reindex(df.index)
    return df
