"""
Hourly bars built from 1-minute candles, shared by every hourly strategy,
the long-history backtest and the live loop.

Bar convention: a bar is labelled by the START of its hour; its close is
the close of the hh:59 minute. A target computed on bar t may use the bar's
close and anything earlier; the simulator fills it at bar t+1's open, which
is the open of the next minute -- the same fill the 1m backtest gives a
signal taken at hh:59.
"""
import numpy as np
import pandas as pd

MINUTES_PER_DAY = 1440


def fill_minute_grid(m1: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Reindex 1m candles onto a gap-free minute grid. Missing minutes (exchange
    outages) become flat candles at the previous close with zero volume, so
    rolling windows measured in bars stay windows in time. Returns the frame
    and the number of minutes filled."""
    grid = pd.date_range(m1.index[0], m1.index[-1], freq="1min")
    out = m1.reindex(grid)
    missing = int(out["close"].isna().sum())
    if missing:
        out["close"] = out["close"].ffill()
        for col in ("open", "high", "low"):
            out[col] = out[col].fillna(out["close"])
        out["volume"] = out["volume"].fillna(0.0)
    return out, missing


def hourly_bars(m1: pd.DataFrame) -> pd.DataFrame:
    """1m OHLCV (UTC index, gap-free) -> hourly OHLCV plus vol1m_1d: the
    standard deviation of the last 1440 one-minute returns at the hour's
    last minute (Model 0's "expected move" scale). Only complete hours are
    kept, so the latest bar never peeks into a partially formed hour."""
    vol = m1["close"].pct_change().rolling(MINUTES_PER_DAY).std()
    g = m1.assign(vol1m_1d=vol).resample("1h", label="left", closed="left")
    bars = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(), "vol1m_1d": g["vol1m_1d"].last(),
    })
    complete = g["close"].count() == 60
    return bars[complete]


def panel(bars: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    """One field of every symbol's bars as a (time x symbol) frame."""
    return pd.DataFrame({s: b[field] for s, b in bars.items()})


def daily_close(close: pd.DataFrame) -> pd.DataFrame:
    """Hourly closes sampled at the 23:00 bar (close of 23:59 UTC), still on
    the hourly index (NaN elsewhere) so callers can ffill without shifting."""
    return close.where(close.index.hour == 23)


def annualized_vol(close: pd.DataFrame, days: int) -> pd.DataFrame:
    """Rolling annualized volatility of daily (24-bar) log returns, sampled
    every hour, from the last `days` days."""
    r = np.log(close).diff(24)
    return r.rolling(days * 24).std() * np.sqrt(365)
