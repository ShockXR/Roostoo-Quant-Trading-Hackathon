"""
Live point-in-time mid-cap membership for the mid-cap book, computed with
the research rule (strategies/universe.py) from Binance's public API.

Daily candles for every trading spot USDT pair are fetched once per UTC day
and cached in data_store/. Difference from the backtest: pairs delisted
since are no longer in Binance's exchangeInfo, so past months' membership
can miss a few names; the current month is unaffected.
"""
import json
import logging
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from .. import config
from ..strategies.universe import MID_TRADED_RANKS, is_candidate, members, ranks

log = logging.getLogger("binance_universe")
DAY_MS = 86_400_000
API = config.BINANCE_KLINES_URL.rsplit("/", 1)[0]          # .../api/v3


def _get(path: str, params: dict) -> list | dict:
    for attempt in range(1, 4):
        try:
            r = requests.get(f"{API}/{path}", params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            log.warning("%s %s attempt %d failed: %s", path, params.get("symbol", ""), attempt, e)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{path} failed")


def usdt_symbols() -> list[str]:
    info = _get("exchangeInfo", {"permissions": "SPOT"})
    return sorted(s["symbol"] for s in info["symbols"]
                  if s["status"] == "TRADING" and s["quoteAsset"] == "USDT" and is_candidate(s["symbol"]))


def daily_bars(symbols: list[str], days: int, now_ms: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(close, quote volume) of finished daily candles, last `days` days."""
    end = now_ms - now_ms % DAY_MS

    def one(sym):
        k = _get("klines", {"symbol": sym, "interval": "1d", "startTime": end - days * DAY_MS,
                            "endTime": end - 1, "limit": 1000})
        idx = pd.to_datetime([row[0] for row in k], unit="ms", utc=True)
        return sym, pd.Series([float(r[4]) for r in k], index=idx), pd.Series([float(r[7]) for r in k], index=idx)

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, symbols))
    return (pd.DataFrame({s: c for s, c, _ in res}), pd.DataFrame({s: q for s, _, q in res}))


def mid_membership(now_ms: int | None = None, months_back: int = 7,
                   cache: str = "data_store/mid_membership.json") -> dict[str, list[str]]:
    """{"YYYY-MM": members ranked MID_TRADED_RANKS} for the current month and
    the `months_back - 1` before it (enough for the 150-day basket filter)."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    today = pd.Timestamp(now_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    path = pathlib.Path(cache)
    if path.exists():
        cached = json.loads(path.read_text())
        if cached.get("date") == today:
            return cached["members"]
    month = pd.Timestamp(now_ms, unit="ms", tz="UTC").normalize().replace(day=1)
    months = pd.date_range(end=month, periods=months_back, freq="MS")
    syms = usdt_symbols()
    close, qvol = daily_bars(syms, months_back * 31 + 100, now_ms)
    out = members(ranks(qvol, close, months), *MID_TRADED_RANKS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": today, "members": out}, indent=1))
    log.info("mid-cap membership from %d pairs: %s now %s", len(syms), months[-1].strftime("%Y-%m"),
             out[months[-1].strftime("%Y-%m")])
    return out
