"""
Strategy registry: turns market data into target portfolio weights per bar.

generate_targets() returns, for one pair, a Series of target weights
(fraction of equity, long-only here) indexed like the input candles.
target[t] may only use information up to and including bar t's close;
the simulator executes it at the next bar's open.
"""
import numpy as np
import pandas as pd

from . import config
from .features.build_features import build_features_for_pair


def _baseline(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    """The original bot, unchanged: walk-forward random forest on the full
    feature set, cost filter, confidence/vol sizing, hold TARGET_HORIZON bars
    (backtest/ablation.py stage 8_full_cost_aware)."""
    from .backtest.ablation import _signal_from_model
    features = build_features_for_pair(pair, raw, panel)
    features = features.replace([np.inf, -np.inf], np.nan).dropna()
    return _signal_from_model(features, features["last_price"], "random_forest",
                              apply_cost_filter=True, apply_sizing=True)


def _buy_hold(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    return pd.Series(params.get("weight", config.MAX_POSITION_PER_ASSET), index=ohlcv.index)


def _reversal(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    """The final strategy: buy an unusually large drop, hold for a fixed time.

    lookback: bars over which the drop is measured
    k:        entry when the lookback return < -k * (expected lookback move),
              expected move = 1-day rolling std of 1m returns * sqrt(lookback)
    hold:     bars to hold after the LAST qualifying signal (a fresh signal
              extends the hold, as in the original bot)
    every:    only check for entries at the close of each `every`-minute block
    weight:   fraction of equity per position
    Defaults come from config.REVERSAL_*.
    """
    c = ohlcv["close"]
    lookback = int(params.get("lookback", config.REVERSAL_LOOKBACK_BARS))
    k = float(params.get("k", config.REVERSAL_K))
    hold = int(params.get("hold", config.REVERSAL_HOLD_BARS))
    every = int(params.get("every", config.REVERSAL_CHECK_EVERY))
    weight = float(params.get("weight", config.MAX_POSITION_PER_ASSET))

    move = c / c.shift(lookback) - 1
    expected = c.pct_change().rolling(1440).std() * np.sqrt(lookback)
    minute_of_day = c.index.hour * 60 + c.index.minute
    signal = (move < -k * expected) & ((minute_of_day % every) == every - 1)
    return signal.astype(float).rolling(hold, min_periods=1).max() * weight


STRATEGIES = {
    "baseline": _baseline,
    "buy_hold": _buy_hold,
    "reversal": _reversal,
}


def generate_targets(name: str, pair: str, raw: dict, panel: pd.DataFrame,
                     ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    return STRATEGIES[name](pair, raw, panel, ohlcv, params)
