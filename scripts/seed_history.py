"""
Pre-fills data_store/ with 1,000+ 1-minute price bars directly from Binance public API
so the bot instantly has a full 24-hour historical window without waiting 24 hours.
"""
import os
import requests
import pandas as pd

STORE_DIR = "data_store"
os.makedirs(STORE_DIR, exist_ok=True)

SYMBOLS = {
    # Sleeve 1: Large-Caps (12 Coins)
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
    "SOL/USD": "SOLUSDT",
    "BNB/USD": "BNBUSDT",
    "XRP/USD": "XRPUSDT",
    "ADA/USD": "ADAUSDT",
    "DOGE/USD": "DOGEUSDT",
    "LINK/USD": "LINKUSDT",
    "AVAX/USD": "AVAXUSDT",
    "DOT/USD": "DOTUSDT",
    "LTC/USD": "LTCUSDT",
    "SUI/USD": "SUIUSDT",

    # Sleeve 2: Mid-Caps (11 Coins)
    "UNI/USD": "UNIUSDT",
    "AAVE/USD": "AAVEUSDT",
    "CRV/USD": "CRVUSDT",
    "PENDLE/USD": "PENDLEUSDT",
    "CAKE/USD": "CAKEUSDT",
    "ONDO/USD": "ONDOUSDT",
    "ENA/USD": "ENAUSDT",
    "ARB/USD": "ARBUSDT",
    "POL/USD": "POLUSDT",
    "SEI/USD": "SEIUSDT",
    "CFX/USD": "CFXUSDT",
}


def fetch_binance_history(symbol: str, target_bars: int = 1500) -> pd.DataFrame:
    """Fetches 1500+ candles (25+ hours) of Binance 1m price data using paginated REST requests."""
    url = "https://api.binance.com/api/v3/klines"
    
    # 1. Fetch latest 1000 candles
    resp1 = requests.get(f"{url}?symbol={symbol}&interval=1m&limit=1000", timeout=10)
    resp1.raise_for_status()
    data1 = resp1.json()
    
    # 2. Fetch preceding 500 candles before data1[0] timestamp
    earliest_ts = data1[0][0]
    resp2 = requests.get(f"{url}?symbol={symbol}&interval=1m&limit=600&endTime={earliest_ts - 1}", timeout=10)
    resp2.raise_for_status()
    data2 = resp2.json()
    
    combined = data2 + data1
    rows = []
    for item in combined:
        rows.append({
            "timestamp": int(item[0]),
            "last_price": float(item[4]),  # Close price as last_price
        })
    return pd.DataFrame(rows)


def seed_data_store():
    for pair, symbol in SYMBOLS.items():
        print(f"Fetching Binance 1m price history for {pair} ({symbol})...")
        df = fetch_binance_history(symbol)
        safe_pair = pair.replace("/", "_")
        filepath = os.path.join(STORE_DIR, f"{safe_pair}.parquet")
        
        # Merge with existing file if present to preserve monotonicity
        if os.path.exists(filepath):
            existing_df = pd.read_parquet(filepath)
            df = pd.concat([df, existing_df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
            
        df.to_parquet(filepath, index=False)
        print(f"✅ Pre-filled {len(df)} bars into {filepath}")


if __name__ == "__main__":
    seed_data_store()
