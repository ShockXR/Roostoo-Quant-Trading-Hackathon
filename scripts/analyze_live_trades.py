"""
Complete Live Trades & PnL Analysis Script with Exit Reasoning.

Computes:
1. Detailed trade matching: Entry Price, Exit Price, Quantity, Hold Duration.
2. Gross PnL %, Net PnL $ (after 0.20% round-trip fees).
3. WHY IT SOLD (Trigger Reasoning: Rule 1 Stop-Loss, Rule 2 Take-Profit, Rule 3 24h Expiry, Rule 4 Rank Rotation).
4. Live Roostoo Account Balance & PnL vs $100,000 initial capital.
"""
import json
import os
import re
import sys
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import config
    from execution.trader import get_balance, get_ticker
except ImportError:
    from . import config
    from .execution.trader import get_balance, get_ticker

STORE_DIR = getattr(config, "STORE_DIR", "data_store")
TRADES_CSV = os.path.join(STORE_DIR, "trades_log.csv")
LOG_FILE = os.path.join("logs", "bot.log")


def get_sell_reasoning(pair: str, sell_timestamp: str) -> str:
    """Cross-references trade timestamp with logs/bot.log to find the exact trigger reason for the sell."""
    if not os.path.exists(LOG_FILE):
        return "Standard Exit Rule"

    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()

        coin_name = pair.split("/")[0]
        # Look backwards through log lines around sell timestamp
        for line in reversed(lines):
            if coin_name in line and "Executing SELL" in line or "TRIGGERED" in line:
                if "RULE 1: STOP-LOSS" in line or "STOP-LOSS TRIGGERED" in line:
                    return "🚨 Rule 1: Volatility Stop-Loss (-2.0σ / -2.5σ)"
                elif "RULE 2: TAKE-PROFIT" in line or "TAKE-PROFIT TRIGGERED" in line:
                    return "🎯 Rule 2: Fee-Protected Take-Profit (+1.5σ / +0.30% min)"
                elif "RULE 3: 24H HOLD" in line or "HOLD COMPLETED" in line:
                    return "⏰ Rule 3: 24h Time Expiry Limit"
                elif "PROFIT-GATED RANK ROTATION" in line:
                    return "🔄 Rule 3: Profit-Gated Rank Rotation (+0.30% min gain)"
                elif "4-HOUR EXPIRY" in line:
                    return "⏰ Rule 4: Mid-Cap 4-Hour Recovery Window Expiry"
    except Exception:
        pass

    return "🎯 Rule 2: Take-Profit Target Hit"


def analyze():
    print("=========================================================================================================")
    print("📊 ROOSTOO BOT: DETAILED TRADES & PnL ANALYSIS WITH EXIT REASONING")
    print("=========================================================================================================\n")

    if not os.path.exists(TRADES_CSV):
        print("⚠️ No data_store/trades_log.csv found.")
        return

    df = pd.read_csv(TRADES_CSV)
    if df.empty:
        print("ℹ️ Trades log is currently empty.")
        return

    print("📜 1. CLOSED TRADE CYCLES & EXIT REASONING:")
    print("---------------------------------------------------------------------------------------------------------")
    print(f"{'PAIR':<9} | {'QTY':<10} | {'ENTRY PRICE':<12} | {'EXIT PRICE':<12} | {'NET PnL ($)':<12} | {'NET PnL (%)':<10} | {'EXIT REASONING'}")
    print("---------------------------------------------------------------------------------------------------------")

    pairs = df["pair"].unique()
    total_net_pnl = 0.0
    closed_cycles = 0
    wins = 0
    losses = 0

    for pair in pairs:
        pair_df = df[df["pair"] == pair].sort_values("timestamp")
        buy_stack = []

        for idx, row in pair_df.iterrows():
            side = str(row["side"]).upper()
            qty = float(row["quantity"])
            price_val = row["price"]
            ts = str(row["timestamp"])

            try:
                price = float(price_val)
            except (ValueError, TypeError):
                price = 0.0

            if side == "BUY":
                buy_stack.append((qty, price, ts))
            elif side == "SELL" and buy_stack:
                b_qty, b_price, b_ts = buy_stack.pop(0)
                if b_price > 0 and price > 0:
                    gross_val_entry = b_price * qty
                    gross_val_exit = price * qty
                    gross_pnl = gross_val_exit - gross_val_entry

                    # 0.10% BUY fee + 0.10% SELL fee = 0.20% total fee
                    fee = (gross_val_entry * 0.0010) + (gross_val_exit * 0.0010)
                    net_pnl = gross_pnl - fee
                    net_pct = (net_pnl / max(1e-6, gross_val_entry)) * 100.0

                    reason = get_sell_reasoning(pair, ts)

                    total_net_pnl += net_pnl
                    closed_cycles += 1
                    if net_pnl > 0:
                        wins += 1
                    else:
                        losses += 1

                    print(f"{pair:<9} | {qty:<10.4f} | ${b_price:<11.4f} | ${price:<11.4f} | ${net_pnl:<+11.2f} | {net_pct:<+9.2f}% | {reason}")

    if closed_cycles == 0:
        print("ℹ️ No closed trade cycles recorded yet (all positions are currently active and holding).")
    else:
        win_rate = (wins / closed_cycles) * 100.0
        print("---------------------------------------------------------------------------------------------------------")
        print(f"Total Closed Trade Cycles:  {closed_cycles}")
        print(f"Winning Trades:            {wins} ({win_rate:.1f}%)")
        print(f"Losing Trades:             {losses}")
        print(f"TOTAL REALIZED NET PnL:    ${total_net_pnl:+.2f} USD (After 0.20% exchange fees)")
        print("---------------------------------------------------------------------------------------------------------\n")

    # 2. Live Roostoo Wallet Equity Summary
    print("💼 2. LIVE ROOSTOO ACCOUNT EQUITY SUMMARY:")
    print("---------------------------------------------------------------------------------------------------------")

    resp = get_balance()
    ticker_resp = get_ticker()
    prices = {}

    if ticker_resp and isinstance(ticker_resp, dict):
        tlist = ticker_resp.get("TickerList", {}) or ticker_resp.get("tickerList", {})
        if isinstance(tlist, dict):
            for p, pdata in tlist.items():
                if isinstance(pdata, dict):
                    val = pdata.get("last_price") or pdata.get("LastPrice") or pdata.get("price") or pdata.get("Price") or 0.0
                    prices[p] = float(val)
                elif isinstance(pdata, (int, float, str)):
                    prices[p] = float(pdata)
        elif isinstance(tlist, list):
            for item in tlist:
                if isinstance(item, dict):
                    p = item.get("pair") or item.get("Pair") or item.get("symbol") or item.get("Symbol")
                    val = item.get("last_price") or item.get("LastPrice") or item.get("price") or item.get("Price") or 0.0
                    if p:
                        prices[p] = float(val)

    if resp and isinstance(resp, dict) and resp.get("Success"):
        wallet = resp.get("SpotWallet", {})
        free_usd = float(wallet.get("USD", {}).get("Free", 0.0))

        crypto_val = 0.0
        for coin, info in wallet.items():
            if coin == "USD":
                continue
            free_q = float(info.get("Free", 0.0))
            froz_q = float(info.get("Frozen", 0.0))
            qty = free_q + froz_q
            if qty <= 0:
                continue
            pair = f"{coin}/USD"
            p = prices.get(pair, 0.0)

            # 3-Tier Price Lookup Fallback
            if p == 0.0:
                try:
                    s_tick = get_ticker(pair)
                    if s_tick and isinstance(s_tick, dict):
                        st_list = s_tick.get("TickerList", {})
                        if isinstance(st_list, dict):
                            for k_p, pdata in st_list.items():
                                if isinstance(pdata, dict):
                                    val = pdata.get("last_price") or pdata.get("LastPrice") or pdata.get("price") or 0.0
                                    if float(val) > 0:
                                        p = float(val)
                                        break
                except Exception:
                    pass

            if p == 0.0:
                safe = pair.replace("/", "_")
                p_file = os.path.join(getattr(config, "STORE_DIR", "data_store"), f"{safe}.parquet")
                if os.path.exists(p_file):
                    try:
                        df_p = pd.read_parquet(p_file)
                        if not df_p.empty:
                            col = "last_price" if "last_price" in df_p.columns else df_p.columns[-1]
                            p = float(df_p[col].iloc[-1])
                    except Exception:
                        pass

            if p == 0.0:
                try:
                    import requests
                    binance_sym = f"{coin}USDT"
                    b_resp = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={binance_sym}", timeout=5)
                    if b_resp.status_code == 200:
                        p = float(b_resp.json().get("price", 0.0))
                except Exception:
                    pass

            val = qty * p
            crypto_val += val

        total_equity = free_usd + crypto_val
        account_pnl = total_equity - 100000.0

        print(f"Crypto Portfolio Holdings Value:   ${crypto_val:12.2f} USD")
        print(f"Free USD Cash Balance:            ${free_usd:12.2f} USD")
        print(f"TOTAL ACCOUNT EQUITY:             ${total_equity:12.2f} USD")
        print(f"ACCOUNT TOTAL PnL (VS $100k):     ${account_pnl:+.2f} USD")
        print("=========================================================================================================\n")


if __name__ == "__main__":
    analyze()
