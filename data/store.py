"""
Minimal local time-series store. One parquet file per pair, appended to
over time. Kept deliberately simple -- swap for a real DB later if needed.
"""
import os
import tempfile

import pandas as pd

from .. import config
from .validation import clean_ticks


def _path(pair: str) -> str:
    safe = pair.replace("/", "_")
    return os.path.join(config.STORE_DIR, f"{safe}.parquet")


def append_tick(pair: str, row: dict):
    """row must include at least: timestamp, last_price, max_bid, min_ask."""
    path = _path(pair)
    os.makedirs(config.STORE_DIR, exist_ok=True)
    df_new = pd.DataFrame([row])
    if os.path.exists(path):
        df_old = pd.read_parquet(path)
        df = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df = df_new
    df = clean_ticks(df)
    fd, temp = tempfile.mkstemp(dir=config.STORE_DIR, suffix=".parquet")
    os.close(fd)
    try:
        df.to_parquet(temp, index=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load(pair: str) -> pd.DataFrame:
    path = _path(pair)
    if not os.path.exists(path):
        return pd.DataFrame(columns=["timestamp", "last_price", "max_bid", "min_ask"])
    return clean_ticks(pd.read_parquet(path))


def load_all(pairs: list[str]) -> dict[str, pd.DataFrame]:
    return {p: load(p) for p in pairs}
