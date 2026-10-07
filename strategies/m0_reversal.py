"""
MODEL 0 -- the existing strategy (strategy.py `_reversal`), unchanged.

Per asset, once an hour: if the 1-day return is below -k x the expected
1-day move (1-day std of 1m returns x sqrt(1440)), hold `weight` of equity
for `hold` hours after the last such signal.

This is the hourly form of the 1m rule: on a gap-free minute grid the 1m
rule only changes its target at hh:59, so sampling it there gives exactly
these targets (tests/test_strategies.py checks the equivalence on minute
data). Defaults are the frozen config.REVERSAL_* values.
"""
import numpy as np
import pandas as pd

from .. import config
from .features import panel


def signal(bars: dict[str, pd.DataFrame], lookback_h: int, k: float) -> pd.DataFrame:
    """Boolean entry signal per asset and hour (the dip condition)."""
    close = panel(bars, "close")
    move = close / close.shift(lookback_h) - 1
    expected = panel(bars, "vol1m_1d") * np.sqrt(lookback_h * 60)
    return move < -k * expected


def hold_signal(sig: pd.DataFrame, hold_h: int) -> pd.DataFrame:
    """1.0 while within `hold_h` hours of the last True (a fresh signal extends it)."""
    return sig.astype(float).rolling(hold_h, min_periods=1).max()


def resolve(params: dict) -> dict:
    """lookback / hold in hours; defaults from config.REVERSAL_*."""
    return {
        "lookback": int(params.get("lookback", config.REVERSAL_LOOKBACK_BARS // 60)),
        "k": float(params.get("k", config.REVERSAL_K)),
        "hold": int(params.get("hold", config.REVERSAL_HOLD_BARS // 60)),
        "weight": float(params.get("weight", config.MAX_POSITION_PER_ASSET)),
    }


def eligible_entries(sig: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Entries only where params["_eligible"] (point-in-time universe mask) allows."""
    elig = params.get("_eligible")
    return sig if elig is None else sig & elig.reindex_like(sig).fillna(False).astype(bool)


def targets(bars: dict[str, pd.DataFrame], params: dict) -> pd.DataFrame:
    p = resolve(params)
    sig = eligible_entries(signal(bars, p["lookback"], p["k"]), params)
    return hold_signal(sig, p["hold"]) * p["weight"]
