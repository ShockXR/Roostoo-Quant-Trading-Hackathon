"""Ticker validation; timestamps are UTC epoch milliseconds."""
import numpy as np
import pandas as pd
from .. import config


def clean_ticks(frame):
    if not {"timestamp", "last_price"}.issubset(frame.columns):
        raise ValueError("missing timestamp/last_price")
    out = frame.copy()
    for col in ("timestamp", "last_price", "max_bid", "min_ask"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="raise")
    if len(out) and (not np.isfinite(out.timestamp).all() or (out.timestamp < 0).any()
                     or (out.timestamp % 1 != 0).any() or not np.isfinite(out.last_price).all()
                     or (out.last_price <= 0).any()):
        raise ValueError("invalid tick timestamp/price")
    out = out.drop_duplicates().sort_values("timestamp")
    if out.timestamp.duplicated().any():
        raise ValueError("conflicting ticks share a timestamp")
    out["timestamp"] = out.timestamp.astype("int64")
    return out.reset_index(drop=True)


def validate_recent_history(frame, now_ms, lookback):
    frame = clean_ticks(frame)
    tail = frame.tail(lookback)
    if len(tail) < lookback:
        raise ValueError("insufficient history")
    age = now_ms - int(tail.timestamp.iloc[-1])
    if age < 0 or age > config.MAX_QUOTE_AGE_MS:
        raise ValueError("stale/future quote")
    if (tail.timestamp.diff().dropna() > config.MAX_HISTORY_GAP_MS).any():
        raise ValueError("gap in required feature history")
    return frame
