"""Recent hourly closes from Binance's public klines API for the mid-cap signal.

Roostoo prices stream from Binance (organizer FAQ Q17), and other market-data
APIs are allowed (FAQ Q16). The close of the 1h kline that closes at hour H is
the last trade before H, the same price the backtest's top-of-hour signal uses.
Only fully closed klines are kept.
"""
import logging
import pandas as pd
import requests
from .. import config
from ..backtest.portfolio import HourlyPanel

log = logging.getLogger("binance_live")
HOUR_MS = 3_600_000


def binance_symbol(pair):
    base, quote = pair.split("/")
    if quote != "USD":
        raise ValueError(f"unexpected quote currency in {pair}")
    return base + "USDT"


def fetch_klines(symbol, end_hour_ms, hours, session=requests):
    """Hourly klines whose close time is at or before end_hour_ms, oldest first."""
    params = {"symbol": symbol, "interval": "1h", "endTime": end_hour_ms - 1, "limit": hours}
    last_error = None
    for url in config.BINANCE_KLINE_URLS:
        try:
            response = session.get(url, params=params, timeout=10)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("unexpected klines response")
            return rows
        except (requests.exceptions.RequestException, ValueError) as exc:
            last_error = exc
            log.warning("%s klines from %s failed (%s)", symbol, url, type(exc).__name__)
    raise ValueError(f"no kline source answered for {symbol}") from last_error


def hourly_panel(pairs, end_hour_ms, hours=None, session=requests):
    """HourlyPanel ending at end_hour_ms, columns in Roostoo pair names."""
    if end_hour_ms % HOUR_MS:
        raise ValueError("end_hour_ms must be on the hour")
    hours = config.MIDCAP_HISTORY_HOURS if hours is None else hours
    closes, volumes = {}, {}
    for pair in pairs:
        rows = fetch_klines(binance_symbol(pair), end_hour_ms, hours, session)
        stamps = [int(r[0]) + HOUR_MS for r in rows if int(r[0]) + HOUR_MS <= end_hour_ms]
        kept = rows[:len(stamps)]
        closes[pair] = pd.Series([float(r[4]) for r in kept], index=stamps)
        volumes[pair] = pd.Series([float(r[7]) for r in kept], index=stamps)
    signal = pd.DataFrame(closes).sort_index()
    if signal.empty or int(signal.index[-1]) != end_hour_ms:
        raise ValueError("latest hourly close not yet available")
    if signal.isna().any().any():
        raise ValueError("missing hourly closes for some pairs")
    expected = pd.RangeIndex(int(signal.index[0]), end_hour_ms + 1, HOUR_MS)
    if len(signal) != len(expected) or (signal.index.to_numpy() != expected.to_numpy()).any():
        raise ValueError("gap in hourly closes")
    signal.index = pd.to_datetime(signal.index, unit="ms", utc=True)
    volume = pd.DataFrame(volumes).sort_index()
    volume.index = signal.index
    return HourlyPanel(signal, signal.copy(), volume[signal.columns])
