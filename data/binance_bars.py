"""
Complete hourly bars for the live loop, from Binance's public market-data
API (no key). Same bar convention as strategies/features.hourly_bars: a bar
is labelled by the start of its hour and only finished hours are returned.
The Roostoo mock exchange prices track Binance spot to within a few
dollars on BTC, so these are the same prices the backtest was run on.
"""
import logging
import time

import pandas as pd
import requests

from .. import config

log = logging.getLogger("binance_bars")
HOUR_MS = 3_600_000


def _get(params: dict, retries: int = 3) -> list:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(config.BINANCE_KLINES_URL, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.warning("klines %s attempt %d/%d failed: %s", params.get("symbol"), attempt, retries, e)
            time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"could not fetch klines for {params.get('symbol')}")


def fetch_hourly(symbol: str, days: int, now_ms: int | None = None) -> pd.DataFrame:
    """The last `days` days of finished hourly bars for a Binance symbol."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    end = now_ms - now_ms % HOUR_MS          # start of the current (unfinished) hour
    start = end - days * 24 * HOUR_MS
    rows = []
    while start < end:
        batch = _get({"symbol": symbol, "interval": "1h", "startTime": start, "endTime": end - 1, "limit": 1000})
        if not batch:
            break
        rows += batch
        start = batch[-1][0] + HOUR_MS
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time",
                                     "qv", "n", "tb", "tq", "x"])
    df = df[df.close_time < now_ms]          # finished bars only
    idx = pd.to_datetime(df.open_time, unit="ms", utc=True)
    bars = df[["open", "high", "low", "close", "volume"]].astype(float).set_index(idx)
    bars["vol1m_1d"] = float("nan")          # needs 1m data; not used by the production model
    return bars[~bars.index.duplicated()].sort_index()
