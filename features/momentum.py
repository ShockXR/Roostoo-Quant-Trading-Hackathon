"""
Time-series momentum features.
All computed strictly causally: row i only uses data at or before i.
Answers: "is this asset moving persistently relative to its OWN history?"
"""
import pandas as pd

from .. import config


def add_momentum_features(df: pd.DataFrame, price_col: str = "last_price") -> pd.DataFrame:
    df = df.copy()

    # Multi-horizon returns -- disagreement across horizons is itself
    # informative (noise vs genuine trend), so keep every horizon as its
    # own column rather than collapsing to one number.
    for h in config.MOMENTUM_HORIZONS:
        df[f"ret_{h}"] = df[price_col].pct_change(h)

    # Moving averages and their relationship
    fast_w = config.MA_WINDOWS["fast"]
    slow_w = config.MA_WINDOWS["slow"]
    df["ma_fast"] = df[price_col].rolling(fast_w).mean()
    df["ma_slow"] = df[price_col].rolling(slow_w).mean()
    df["ma_ratio"] = df["ma_fast"] / df["ma_slow"] - 1.0

    # distance of current price from each MA (normalized)
    df["dist_from_ma_fast"] = df[price_col] / df["ma_fast"] - 1.0
    df["dist_from_ma_slow"] = df[price_col] / df["ma_slow"] - 1.0

    # slope of moving averages (rate of change of the MA itself)
    df["ma_fast_slope"] = df["ma_fast"].pct_change(fast_w)
    df["ma_slow_slope"] = df["ma_slow"].pct_change(slow_w)

    # cross-horizon agreement: how many of the horizon returns share the
    # same sign -- a simple, interpretable "is this a real trend" feature
    ret_cols = [f"ret_{h}" for h in config.MOMENTUM_HORIZONS]
    df["momentum_agreement"] = df[ret_cols].apply(
        lambda row: (row.dropna() > 0).mean() if row.dropna().size else float("nan"),
        axis=1,
    )

    return df
