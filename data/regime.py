"""
Macro market regime and multi-day momentum signal engine.
Fetches public Binance hourly closes to compute:
1. BTC 100-day Simple Moving Average (Regime Gate: Risk-On vs Risk-Off)
2. Large-Cap 28-Day Momentum (Top 3 Majors)
3. Small-Cap 7-Day Confirmed Momentum (Top 2 Memes)
Results are cached on disk and in-memory for 1 hour to ensure zero API rate pressure.
"""
import json
import logging
import os
import time
import numpy as np
import pandas as pd
import requests

try:
    import config
except ImportError:
    from .. import config

log = logging.getLogger("regime")

CACHE_DIR = getattr(config, "STORE_DIR", "data_store")
REGIME_CACHE_FILE = os.path.join(CACHE_DIR, "regime_cache.json")
HOUR_MS = 3_600_000

# In-memory cache
_CACHE = {
    "timestamp": 0.0,
    "btc_regime_on": True,
    "btc_price": 0.0,
    "btc_sma100": 0.0,
    "largecap_leaders": [],
    "smallcap_leaders": [],
}


def _binance_symbol(pair: str) -> str:
    base, quote = pair.split("/")
    return f"{base}USDT"


def fetch_binance_klines(symbol: str, limit: int = 1000) -> list:
    """Fetches public hourly klines from Binance."""
    params = {"symbol": symbol, "interval": "1h", "limit": limit}
    urls = getattr(config, "BINANCE_KLINE_URLS", [
        "https://api.binance.com/api/v3/klines",
        "https://data-api.binance.vision/api/v3/klines",
    ])
    for url in urls:
        try:
            resp = requests.get(url, params=params, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data
        except Exception as e:
            log.debug("Binance klines from %s for %s failed: %s", url, symbol, e)
    return []


def update_regime_cache():
    """Refreshes BTC 100d SMA, Large-Cap 28d Momentum, and Small-Cap 7d Momentum once per hour."""
    global _CACHE
    now = time.time()
    
    # Check if cache is fresh (< 60 minutes)
    if (now - _CACHE["timestamp"] < 3600) and _CACHE["btc_price"] > 0:
        return

    # Check disk cache
    if os.path.exists(REGIME_CACHE_FILE) and _CACHE["timestamp"] == 0.0:
        try:
            with open(REGIME_CACHE_FILE, "r") as f:
                disk_data = json.load(f)
                if now - disk_data.get("timestamp", 0) < 3600:
                    _CACHE.update(disk_data)
                    log.info("💾 Loaded fresh macro regime cache from %s (BTC Regime: %s)", REGIME_CACHE_FILE, _CACHE["btc_regime_on"])
                    return
        except Exception:
            pass

    log.info("🌐 Refreshing macro regime (BTC 100d SMA) & multi-day momentum from Binance...")

    try:
        # 1. BTC 100-Day SMA Regime (requires ~2400 hourly bars or 100 daily bars)
        # We can fetch 1d klines for the 100-day moving average cleanly!
        btc_daily_params = {"symbol": "BTCUSDT", "interval": "1d", "limit": 110}
        urls = getattr(config, "BINANCE_KLINE_URLS", ["https://api.binance.com/api/v3/klines"])
        btc_daily = []
        for url in urls:
            try:
                r = requests.get(url, params=btc_daily_params, timeout=8)
                if r.status_code == 200:
                    btc_daily = r.json()
                    if btc_daily:
                        break
            except Exception:
                continue

        if btc_daily and len(btc_daily) >= 100:
            daily_closes = [float(k[4]) for k in btc_daily]
            current_btc_p = daily_closes[-1]
            sma_100 = float(np.mean(daily_closes[-100:]))
            regime_on = current_btc_p >= sma_100
            _CACHE["btc_price"] = current_btc_p
            _CACHE["btc_sma100"] = sma_100
            _CACHE["btc_regime_on"] = regime_on
            log.info("📈 [BTC REGIME UPDATE] BTC: $%.2f | 100d SMA: $%.2f | Regime: %s", current_btc_p, sma_100, "🟢 RISK-ON" if regime_on else "🔴 RISK-OFF (CASH)")
        else:
            log.warning("Could not fetch 100d BTC klines, maintaining previous regime flag: %s", _CACHE["btc_regime_on"])

        # 2. Large-Cap 28-Day Momentum Leaders
        large_pairs = getattr(config, "LARGECAP_UNIVERSE", [])
        large_returns = {}
        for p in large_pairs:
            sym = _binance_symbol(p)
            klines = fetch_binance_klines(sym, limit=672)  # 28 days = 672 hours
            if len(klines) >= 24:
                first_p = float(klines[0][4])
                last_p = float(klines[-1][4])
                ret = (last_p - first_p) / first_p
                large_returns[p] = ret

        # Top 3 with positive 28-day return
        sorted_large = sorted([p for p, r in large_returns.items() if r > 0], key=lambda x: large_returns[x], reverse=True)
        _CACHE["largecap_leaders"] = sorted_large[:getattr(config, "LARGE_TOP_K", 3)]
        log.info("🏆 [LARGECAP 28D MOMENTUM] Top Leaders: %s", [(p, f"{large_returns[p]*100:+.2f}%") for p in _CACHE["largecap_leaders"]])

        # 3. Small-Cap 7-Day Confirmed Momentum Leaders
        small_pairs = getattr(config, "SMALLCAP_UNIVERSE", [])
        small_returns_7d = {}
        small_returns_24h = {}
        for p in small_pairs:
            sym = _binance_symbol(p)
            klines = fetch_binance_klines(sym, limit=168)  # 7 days = 168 hours
            if len(klines) >= 24:
                last_p = float(klines[-1][4])
                p_7d = float(klines[0][4])
                lb_24h_idx = max(0, len(klines) - 24)
                p_24h = float(klines[lb_24h_idx][4])
                ret_7d = (last_p - p_7d) / p_7d
                ret_24h = (last_p - p_24h) / p_24h
                # Must have positive 7d AND positive 24h return
                if ret_7d > 0 and ret_24h > 0:
                    small_returns_7d[p] = ret_7d
                    small_returns_24h[p] = ret_24h

        sorted_small = sorted(small_returns_7d.keys(), key=lambda x: small_returns_7d[x], reverse=True)
        _CACHE["smallcap_leaders"] = sorted_small[:getattr(config, "SMALL_TOP_K", 2)]
        log.info("🚀 [SMALLCAP 7D CONFIRMED MOMENTUM] Top Memes: %s", [(p, f"7d: {small_returns_7d[p]*100:+.2f}%, 24h: {small_returns_24h[p]*100:+.2f}%") for p in _CACHE["smallcap_leaders"]])

        _CACHE["timestamp"] = now

        # Save to disk
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(REGIME_CACHE_FILE, "w") as f:
                json.dump(_CACHE, f, indent=2)
        except Exception:
            pass

    except Exception as e:
        log.error("Failed to update macro regime cache: %s", e)


def is_btc_regime_on() -> bool:
    """Returns True if BTC is above its 100-day moving average."""
    update_regime_cache()
    return _CACHE.get("btc_regime_on", True)


def get_largecap_leaders() -> list[str]:
    """Returns top 3 Large-Cap coins by 28-day momentum."""
    update_regime_cache()
    return _CACHE.get("largecap_leaders", [])


def get_smallcap_leaders() -> list[str]:
    """Returns top 2 Small-Cap meme coins by confirmed 7-day momentum."""
    update_regime_cache()
    return _CACHE.get("smallcap_leaders", [])
