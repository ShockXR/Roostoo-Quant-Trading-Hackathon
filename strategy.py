"""
Strategy registry: turns market data into target portfolio weights per bar.
Provides pure rule-based quantitative strategies (Reversal, Momentum, Mean Reversion, Buy & Hold).
"""
import numpy as np
import pandas as pd

try:
    import config
except ImportError:
    from . import config


def _reversal(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    """Quantitative Reversal Strategy:
    Buys after an unusually large price drop relative to historical volatility.
    """
    price_col = "close" if "close" in ohlcv.columns else "last_price"
    c = ohlcv[price_col]

    lookback = int(params.get("lookback", getattr(config, "REVERSAL_LOOKBACK_BARS", 60)))
    k = float(params.get("k", getattr(config, "REVERSAL_K", 1.5)))
    hold = int(params.get("hold", getattr(config, "REVERSAL_HOLD_BARS", 1440)))
    every = int(params.get("every", getattr(config, "REVERSAL_CHECK_EVERY", 60)))
    weight = float(params.get("weight", getattr(config, "MAX_POSITION_PER_ASSET", 0.20)))

    if len(c) < max(lookback, 1440):
        # Fallback for shorter warming period
        rolling_std = c.pct_change().rolling(len(c), min_periods=5).std()
    else:
        rolling_std = c.pct_change().rolling(1440).std()

    move = c / c.shift(lookback) - 1
    expected = rolling_std * np.sqrt(lookback)

    if isinstance(c.index, pd.DatetimeIndex):
        minute_of_day = c.index.hour * 60 + c.index.minute
        check_mask = (minute_of_day % every) == (every - 1)
    else:
        check_mask = True

    signal = (move < -k * expected) & check_mask
    return signal.astype(float).rolling(hold, min_periods=1).max() * weight


def _momentum(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    """Quantitative Momentum Strategy:
    Buys when fast moving average crosses above slow moving average with strong momentum.
    """
    price_col = "close" if "close" in ohlcv.columns else "last_price"
    c = ohlcv[price_col]
    fast_window = int(params.get("fast", 15))
    slow_window = int(params.get("slow", 60))
    weight = float(params.get("weight", getattr(config, "MAX_POSITION_PER_ASSET", 0.20)))

    fast_ma = c.rolling(fast_window, min_periods=1).mean()
    slow_ma = c.rolling(slow_window, min_periods=1).mean()
    signal = fast_ma > slow_ma
    return signal.astype(float) * weight


def _mean_reversion(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    """Quantitative RSI / Bollinger Mean Reversion Strategy:
    Buys when RSI drops below oversold threshold (RSI < 30).
    """
    price_col = "close" if "close" in ohlcv.columns else "last_price"
    c = ohlcv[price_col]
    window = int(params.get("window", 14))
    rsi_threshold = float(params.get("rsi_threshold", 30.0))
    weight = float(params.get("weight", getattr(config, "MAX_POSITION_PER_ASSET", 0.20)))

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(window, min_periods=1).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))

    signal = rsi < rsi_threshold
    return signal.astype(float) * weight


def _buy_hold(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    weight = float(params.get("weight", getattr(config, "MAX_POSITION_PER_ASSET", 0.20)))
    return pd.Series(weight, index=ohlcv.index)


STRATEGIES = {
    "reversal": _reversal,
    "momentum": _momentum,
    "mean_reversion": _mean_reversion,
    "buy_hold": _buy_hold,
}


def generate_targets(name: str, pair: str, raw: dict, panel: pd.DataFrame,
                     ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    fn = STRATEGIES.get(name, _reversal)
    return fn(pair, raw, panel, ohlcv, params)
