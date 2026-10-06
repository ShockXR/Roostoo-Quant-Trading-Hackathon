"""
Checks data collection status across all 23 coins in data_store/ and trades log.
"""
import glob
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import config
except ImportError:
    from . import config

STORE_DIR = getattr(config, "STORE_DIR", "data_store")
TRADES_CSV = os.path.join(STORE_DIR, "trades_log.csv")


def check_status():
    print("=======================================================================")
    print("📊 ROOSTOO BOT: DATA COLLECTION & EXECUTED TRADES INSPECTOR")
    print("=======================================================================\n")

    # 1. Inspect Executed Trades Log
    print("📜 1. EXECUTED TRADES HISTORY (data_store/trades_log.csv):")
    print("-----------------------------------------------------------------------")
    if os.path.exists(TRADES_CSV):
        try:
            df_trades = pd.read_csv(TRADES_CSV)
            if not df_trades.empty:
                print(df_trades.tail(15).to_string(index=False))
                print(f"\nTotal Executed Trades Recorded: {len(df_trades)}")
            else:
                print("ℹ️ Trades log file is currently empty.")
        except Exception as e:
            print(f"Error reading trades log: {e}")
    else:
        print("ℹ️ No trades_log.csv file created yet.")

    print("\n-----------------------------------------------------------------------")
    # 2. Inspect Historical Data Collection per Coin
    print("📁 2. DATA COLLECTION STATUS PER COIN (data_store/*.parquet):")
    print("-----------------------------------------------------------------------")
    print(f"{'PAIR':<12} | {'SLEEVE':<10} | {'TOTAL BARS':<12} | {'LATEST PRICE':<14} | {'LATEST TIMESTAMP'}")
    print("---------------------------------------------------------------------------------------")

    files = glob.glob(os.path.join(STORE_DIR, "*.parquet"))
    total_bars = 0

    largecap = getattr(config, "LARGECAP_UNIVERSE", [])
    
    for f in sorted(files):
        filename = os.path.basename(f)
        pair = filename.replace("_USD.parquet", "/USD").replace("_", "/")
        try:
            df = pd.read_parquet(f)
            bars = len(df)
            total_bars += bars
            price_col = "close" if "close" in df.columns else "last_price"
            latest_price = float(df[price_col].iloc[-1]) if not df.empty else 0.0
            latest_ts = int(df["timestamp"].iloc[-1]) if "timestamp" in df.columns and not df.empty else 0

            sleeve = "largecap" if pair in largecap else "midcap"
            print(f"{pair:<12} | {sleeve:<10} | {bars:<12} | ${latest_price:<13.4f} | {latest_ts}")
        except Exception as e:
            print(f"{pair:<12} | Error reading file: {e}")

    print("=======================================================================")
    print(f"Total Stored Historical Files:   {len(files)} coins")
    print(f"Total 1-Minute Price Bars:       {total_bars:,} bars")
    print("=======================================================================\n")


if __name__ == "__main__":
    check_status()
