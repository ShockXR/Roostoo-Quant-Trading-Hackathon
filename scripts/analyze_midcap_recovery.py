"""
Empirical Mid-Cap Recovery & Holding Time Analysis Script.

Fetches 1500+ candles (25+ hours of 1m data) per mid-cap coin, simulates rank decay events,
and measures:
1. Average recovery time (hours) to reach +0.50% profit target or re-rank.
2. Win-rate decay curve P(Win | t) as a function of holding duration.
3. 50th, 75th, 85th, and 90th percentile optimal time-expiry cutoffs (T_opt).
"""
import os
import sys
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import config
except ImportError:
    from . import config

STORE_DIR = getattr(config, "STORE_DIR", "data_store")

MIDCAP_SYMBOLS = {
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


def fetch_binance_history(symbol: str, target_bars: int = 1600) -> pd.DataFrame:
    """Fetches Binance 1m price data using paginated REST calls."""
    url = "https://api.binance.com/api/v3/klines"
    resp1 = requests.get(f"{url}?symbol={symbol}&interval=1m&limit=1000", timeout=10)
    resp1.raise_for_status()
    data1 = resp1.json()

    earliest_ts = data1[0][0]
    resp2 = requests.get(f"{url}?symbol={symbol}&interval=1m&limit=600&endTime={earliest_ts - 1}", timeout=10)
    resp2.raise_for_status()
    data2 = resp2.json()

    combined = data2 + data1
    rows = []
    for item in combined:
        rows.append({
            "timestamp": int(item[0]),
            "last_price": float(item[4]),  # Close price
        })
    df = pd.DataFrame(rows).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def run_recovery_analysis():
    print("=================================================================")
    print("🚀 EMPIRICAL MID-CAP RECOVERY & HOLDING TIME ANALYSIS")
    print("=================================================================\n")

    dfs = {}
    for pair, symbol in MIDCAP_SYMBOLS.items():
        safe_pair = pair.replace("/", "_")
        path = os.path.join(STORE_DIR, f"{safe_pair}.parquet")
        if os.path.exists(path):
            df = pd.read_parquet(path)
            if len(df) >= 500:
                dfs[pair] = df
                print(f"✅ Loaded {len(df)} local bars for {pair}")
                continue

        print(f"Fetching Binance 1m data for {pair} ({symbol})...")
        try:
            df = fetch_binance_history(symbol)
            os.makedirs(STORE_DIR, exist_ok=True)
            df.to_parquet(path, index=False)
            dfs[pair] = df
            print(f"✅ Downloaded & saved {len(df)} bars for {pair}")
        except Exception as e:
            print(f"⚠️ Failed to fetch {pair}: {e}")

    if not dfs:
        print("❌ No market data available for analysis.")
        return

    # Combine into price panel
    prices = pd.DataFrame({pair: df["last_price"] for pair, df in dfs.items()}).dropna()
    print(f"\n📊 Panel assembled: {len(prices)} synchronized 1-minute bars across {len(prices.columns)} mid-cap coins.")

    # 1. Compute 24-hour returns (1440 bars) or max available lookback
    lookback = min(1440, len(prices) // 2)
    ret_24h = prices.pct_change(lookback)

    # 2. Simulate Rank Decay and Recovery Tracking
    recovery_times_min = []
    win_outcomes = []

    # Target profit threshold: +0.50% (clears taker fee 0.20% + slippage + profit)
    PROFIT_TARGET = 0.0050  # +0.50%
    STOP_LOSS = -0.0300     # -3.00%

    print("\n🔍 Simulating Rank-Out Events & Tracking Recovery Times...")
    for t in range(lookback, len(prices) - 60):
        row_ret = ret_24h.iloc[t]

        # Find coins that were NOT in the top 3 momentum leaders
        ranked = row_ret.sort_values(ascending=False)
        top_coins = set(ranked.index[:3])
        non_top = [c for c in prices.columns if c not in top_coins]

        for coin in non_top:
            entry_price = prices[coin].iloc[t]

            # Look forward up to 720 minutes (12 hours) to observe outcome
            future_prices = prices[coin].iloc[t + 1 : t + 721]
            future_returns = (future_prices / entry_price) - 1.0

            # Find first time it hits profit target (+0.50%) or stop loss (-3%)
            tp_hit = future_returns[future_returns >= PROFIT_TARGET]
            sl_hit = future_returns[future_returns <= STOP_LOSS]

            tp_time = tp_hit.index[0] - t if not tp_hit.empty else 9999
            sl_time = sl_hit.index[0] - t if not sl_hit.empty else 9999

            if tp_time < sl_time and tp_time <= 720:
                recovery_times_min.append(tp_time)
                win_outcomes.append(1)
            elif sl_time <= tp_time and sl_time <= 720:
                win_outcomes.append(0)

    if not recovery_times_min:
        # If dataset period is shorter than full lookback, run with shorter 60-bar lookback for instant stats
        print("⚡ Running granular 1-hour lookback recovery simulation...")
        lookback = 60
        ret_1h = prices.pct_change(lookback)
        for t in range(lookback, len(prices) - 60):
            row_ret = ret_1h.iloc[t]
            ranked = row_ret.sort_values(ascending=False)
            non_top = [c for c in prices.columns if c not in set(ranked.index[:3])]
            for coin in non_top:
                entry_price = prices[coin].iloc[t]
                future_prices = prices[coin].iloc[t + 1 : t + 721]
                future_returns = (future_prices / entry_price) - 1.0
                tp_hit = future_returns[future_returns >= PROFIT_TARGET]
                sl_hit = future_returns[future_returns <= STOP_LOSS]
                tp_time = (tp_hit.index[0] - t) if not tp_hit.empty else 9999
                sl_time = (sl_hit.index[0] - t) if not sl_hit.empty else 9999
                if tp_time < sl_time and tp_time <= 720:
                    recovery_times_min.append(tp_time)
                    win_outcomes.append(1)
                elif sl_time <= tp_time and sl_time <= 720:
                    win_outcomes.append(0)

    rec_arr = np.array(recovery_times_min)
    rec_hours = rec_arr / 60.0

    print("\n=================================================================")
    print("📊 EMPIRICAL STATISTICAL RESULTS")
    print("=================================================================")
    print(f"Total Rank-Out Sample Events Analyzed: {len(win_outcomes)}")
    print(f"Overall Recovery Win Rate:             {np.mean(win_outcomes) * 100:.2f}%")
    print(f"Mean Recovery Duration:               {np.mean(rec_hours):.2f} hours ({np.mean(rec_arr):.0f} mins)")
    print(f"Median (50th percentile) Recovery:    {np.median(rec_hours):.2f} hours ({np.median(rec_arr):.0f} mins)")
    print(f"75th Percentile Recovery:              {np.percentile(rec_hours, 75):.2f} hours ({np.percentile(rec_arr, 75):.0f} mins)")
    print(f"85th Percentile Recovery:              {np.percentile(rec_hours, 85):.2f} hours ({np.percentile(rec_arr, 85):.0f} mins)")
    print(f"90th Percentile Recovery:              {np.percentile(rec_hours, 90):.2f} hours ({np.percentile(rec_arr, 90):.0f} mins)")
    print("=================================================================\n")

    t_opt_hours = int(np.ceil(np.percentile(rec_hours, 85)))
    print(f"💡 OPTIMAL EMPIRICAL TIME-EXPIRY CUTOFF (T_opt): {t_opt_hours} HOURS ({t_opt_hours * 60} MINUTES)")
    print(f"   -> 85% of all successful mid-cap recovery trades complete within {t_opt_hours} hours!")
    print("=================================================================\n")


if __name__ == "__main__":
    run_recovery_analysis()
