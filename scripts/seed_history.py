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
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
    "SOL/USD": "SOLUSDT",
    "BNB/USD": "BNBUSDT",
    "XRP/USD": "XRPUSDT",
    "ADA/USD": "ADAUSDT",
}


def fetch_binance_history(symbol: str, limit: int = 1000) -> pd.DataFrame:
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit={limit}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for item in data:
        rows.append({
            "timestamp": int(item[0]),
            "last_price": float(item[4]),  # Close price as last_price
        })
    return pd.DataFrame(rows)


def seed_data_store():
    for pair, symbol in SYMBOLS.items():
        print(f"Fetching Binance 1m price history for {pair} ({symbol})...")
        df = fetch_binance_history(symbol, limit=1000)
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
