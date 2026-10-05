# Frozen copy of strategy.py as used for experiments E05-E12 (includes the
# rejected `trend` rule and the stop / take_profit / risk_budget options).
# To rerun those experiments, copy this file over strategy.py.
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


def _trend(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    """Long `weight` while close is above its EMA, flat otherwise.

    span:  EMA span in 1m bars (trend lookback)
    band:  hysteresis -- enter above EMA*(1+band), exit below EMA*(1-band)
    every: only re-decide at the close of each `every`-minute block, so a
           price hovering around the EMA can't flip the position every bar
    """
    c = ohlcv["close"]
    span = int(params.get("span", 1440))
    band = float(params.get("band", 0.0))
    every = int(params.get("every", 60))
    weight = float(params.get("weight", config.MAX_POSITION_PER_ASSET))

    dist = c / c.ewm(span=span, adjust=False).mean() - 1
    state = pd.Series(np.where(dist > band, 1.0, np.where(dist < -band, 0.0, np.nan)), index=c.index)
    minute_of_day = c.index.hour * 60 + c.index.minute
    state[(minute_of_day % every) != every - 1] = np.nan
    state = state.ffill().fillna(0.0)
    state.iloc[:span] = 0.0  # EMA not warmed up
    return state * weight


def _reversal(pair: str, raw: dict, panel: pd.DataFrame, ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    """Buy an unusually large drop, hold for `hold` bars.

    lookback: bars over which the drop is measured
    k:        entry when the lookback return < -k * (expected lookback vol),
              expected vol = 1-day rolling std of 1m returns * sqrt(lookback)
    hold:     bars to hold after the LAST qualifying signal (a fresh signal
              extends the hold, as in the original bot)
    every:    only check for entries at the close of each `every`-minute block
    """
    c = ohlcv["close"]
    lookback = int(params.get("lookback", 240))
    k = float(params.get("k", 1.5))
    hold = int(params.get("hold", lookback))
    every = int(params.get("every", 60))
    weight = float(params.get("weight", config.MAX_POSITION_PER_ASSET))

    stop = params.get("stop")                # exit after a further drop of stop * vol below entry
    take_profit = params.get("take_profit")  # exit after a rise of take_profit * vol above entry
    risk_budget = params.get("risk_budget")  # cap weight at risk_budget / daily vol

    one_min_vol = c.pct_change().rolling(1440).std()
    move = c / c.shift(lookback) - 1
    vol = one_min_vol * np.sqrt(lookback)
    minute_of_day = c.index.hour * 60 + c.index.minute
    signal = (move < -k * vol) & ((minute_of_day % every) == every - 1)
    if stop is None and take_profit is None and risk_budget is None:
        return signal.astype(float).rolling(hold, min_periods=1).max() * weight

    daily_vol = (one_min_vol * np.sqrt(1440)).to_numpy()
    sig, px, v = signal.to_numpy(), c.to_numpy(), vol.to_numpy()
    out = np.zeros(len(c))
    left = blocked = 0
    w = ref = floor = ceiling = 0.0
    for t in range(len(c)):
        blocked -= 1
        if sig[t] and blocked <= 0:
            if left <= 0:  # new entry: size and stop are fixed for the trade
                w = weight if risk_budget is None else min(weight, risk_budget / daily_vol[t])
                ref = px[t]
                floor = ref * (1 - stop * v[t]) if stop is not None else -np.inf
                ceiling = ref * (1 + take_profit * v[t]) if take_profit is not None else np.inf
            left = hold
        if left > 0 and px[t] < floor:
            left, blocked = 0, hold  # stopped out: sit out one hold period
        if left > 0 and px[t] > ceiling:
            left = 0
        if left > 0:
            out[t] = w
            left -= 1
    return pd.Series(out, index=c.index)


STRATEGIES = {
    "baseline": _baseline,
    "buy_hold": _buy_hold,
    "trend": _trend,
    "reversal": _reversal,
}


def generate_targets(name: str, pair: str, raw: dict, panel: pd.DataFrame,
                     ohlcv: pd.DataFrame, params: dict) -> pd.Series:
    return STRATEGIES[name](pair, raw, panel, ohlcv, params)
