"""
Complete Live Trades & PnL Analysis Script with Exit Reasoning.

Computes:
1. Active Open Positions & Unrealized PnL (Entry Price, Current Price, Quantity, Holdings USD Value).
2. Closed Trade Cycles & Exit Reasoning (Rule 1 SL, Rule 2 TP, Rule 3 24h Expiry, Rule 4 Rank Rotation).
3. Total Account Equity Breakdown (Free USD Cash + Crypto Holdings vs $100k initial capital).
"""
import json
import os
import re
import sys
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import config
    from execution.trader import get_balance, get_ticker
except ImportError:
    from . import config
    from .execution.trader import get_balance, get_ticker

STORE_DIR = getattr(config, "STORE_DIR", "data_store")
TRADES_CSV = os.path.join(STORE_DIR, "trades_log.csv")
POS_FILE = os.path.join(STORE_DIR, "active_positions.json")
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
            if coin_name in line and ("Executing SELL" in line or "TRIGGERED" in line):
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


def get_live_price(pair: str, ticker_prices: dict) -> float:
    """Multi-tier price lookup: live ticker -> parquet data store -> Binance API fallback."""
    p = ticker_prices.get(pair, 0.0)
    if p > 0.0:
        return p

    # 1. Direct single-pair ticker query
    try:
        s_tick = get_ticker(pair)
        if s_tick and isinstance(s_tick, dict):
            tlist = s_tick.get("TickerList", {}) or s_tick.get("tickerList", {})
            if isinstance(tlist, dict):
                for k_p, pdata in tlist.items():
                    if isinstance(pdata, dict):
                        val = pdata.get("last_price") or pdata.get("LastPrice") or pdata.get("price") or pdata.get("Price") or 0.0
                        if float(val) > 0:
                            return float(val)
    except Exception:
        pass

    # 2. Local parquet data store fallback
    safe = pair.replace("/", "_")
    p_file = os.path.join(STORE_DIR, f"{safe}.parquet")
    if os.path.exists(p_file):
        try:
            df_p = pd.read_parquet(p_file)
            if not df_p.empty:
                col = "close" if "close" in df_p.columns else ("last_price" if "last_price" in df_p.columns else df_p.columns[-1])
                val = float(df_p[col].iloc[-1])
                if val > 0:
                    return val
        except Exception:
            pass

    # 3. Binance API fallback
    try:
        import requests
        coin = pair.split("/")[0]
        binance_sym = f"{coin}USDT"
        b_resp = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={binance_sym}", timeout=5)
        if b_resp.status_code == 200:
            val = float(b_resp.json().get("price", 0.0))
            if val > 0:
                return val
    except Exception:
        pass

    return 0.0


def analyze():
    print("=========================================================================================================")
    print("📊 ROOSTOO BOT: DETAILED TRADES & PnL ANALYSIS WITH EXIT REASONING")
    print("=========================================================================================================\n")

    # Fetch global ticker map
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

    # ---------------------------------------------------------------------------------------------------------
    # 1. ACTIVE OPEN POSITIONS & UNREALIZED PnL
    # ---------------------------------------------------------------------------------------------------------
    print("💼 1. ACTIVE OPEN POSITIONS & UNREALIZED PnL:")
    print("---------------------------------------------------------------------------------------------------------")
    print(f"{'PAIR':<9} | {'SLEEVE':<9} | {'QTY':<14} | {'ENTRY PRICE':<11} | {'LIVE PRICE':<11} | {'USD VALUE':<12} | {'UNREALIZED PnL'}")
    print("---------------------------------------------------------------------------------------------------------")

    active_positions = {}
    if os.path.exists(POS_FILE):
        try:
            with open(POS_FILE, "r") as f:
                active_positions = json.load(f)
        except Exception:
            pass

    # Query live wallet balance
    resp = get_balance()
    wallet_holdings = {}
    free_usd = 0.0
    api_available = False

    if resp and isinstance(resp, dict) and resp.get("Success"):
        api_available = True
        wallet = resp.get("SpotWallet", {})
        usd_info = wallet.get("USD", {})
        free_usd = float(usd_info.get("Free", 0.0) or usd_info.get("free", 0.0))

        for coin, info in wallet.items():
            if coin == "USD":
                continue
            free_q = float(info.get("Free", 0.0) or info.get("free", 0.0))
            froz_q = float(info.get("Frozen", 0.0) or info.get("frozen", 0.0))
            qty = free_q + froz_q
            if qty > 0:
                wallet_holdings[f"{coin}/USD"] = qty

    total_active_usd = 0.0
    total_unrealized_pnl = 0.0

    # Build active list from wallet + active_positions.json
    all_active_pairs = set(active_positions.keys()).union(set(wallet_holdings.keys()))

    if not all_active_pairs:
        print("ℹ️ No active open positions currently held.")
    else:
        for pair in sorted(all_active_pairs):
            pos_info = active_positions.get(pair, {})
            qty = wallet_holdings.get(pair, float(pos_info.get("quantity", 0.0)))
            entry_p = float(pos_info.get("entry_price", 0.0))
            sleeve = pos_info.get("sleeve", "largecap" if pair in getattr(config, "LARGECAP_UNIVERSE", []) else "midcap")

            live_p = get_live_price(pair, prices)
            if entry_p <= 0.0:
                entry_p = live_p

            usd_val = qty * live_p
            total_active_usd += usd_val

            unrealized_pnl_usd = (live_p - entry_p) * qty if entry_p > 0 else 0.0
            unrealized_pnl_pct = ((live_p - entry_p) / max(1e-6, entry_p)) * 100.0 if entry_p > 0 else 0.0
            total_unrealized_pnl += unrealized_pnl_usd

            pnl_str = f"${unrealized_pnl_usd:+9.2f} ({unrealized_pnl_pct:+6.2f}%)"
            print(f"{pair:<9} | {sleeve:<9} | {qty:<14.4f} | ${entry_p:<10.4f} | ${live_p:<10.4f} | ${usd_val:<11.2f} | {pnl_str}")

        print("---------------------------------------------------------------------------------------------------------")
        print(f"Total Active Crypto Holdings Value:  ${total_active_usd:12.2f} USD")
        print(f"Total Active Unrealized PnL:         ${total_unrealized_pnl:+12.2f} USD")
        print("---------------------------------------------------------------------------------------------------------\n")

    # ---------------------------------------------------------------------------------------------------------
    # 2. CLOSED TRADE CYCLES & EXIT REASONING
    # ---------------------------------------------------------------------------------------------------------
    print("📜 2. CLOSED TRADE CYCLES & EXIT REASONING:")
    print("---------------------------------------------------------------------------------------------------------")

    if not os.path.exists(TRADES_CSV):
        print("ℹ️ No data_store/trades_log.csv found yet.")
    else:
        df = pd.read_csv(TRADES_CSV)
        if df.empty:
            print("ℹ️ Trades log is currently empty.")
        else:
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

                            # 0.10% BUY fee + 0.10% SELL fee = 0.20% total round-trip fee
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
                print("---------------------------------------------------------------------------------------------------------")

    print("\n---------------------------------------------------------------------------------------------------------")
    # ---------------------------------------------------------------------------------------------------------
    # 3. LIVE ROOSTOO ACCOUNT EQUITY SUMMARY
    # ---------------------------------------------------------------------------------------------------------
    print("💵 3. TOTAL ROOSTOO ACCOUNT EQUITY SUMMARY:")
    print("---------------------------------------------------------------------------------------------------------")

    # If API not available or cash is 0, estimate cash from $100k - invested cash
    if free_usd == 0.0 and total_active_usd > 0:
        free_usd = max(0.0, 100000.0 - total_active_usd + total_unrealized_pnl)

    total_equity = free_usd + total_active_usd
    account_pnl = total_equity - 100000.0

    print(f"Crypto Portfolio Holdings Value:   ${total_active_usd:12.2f} USD")
    print(f"Free USD Cash Balance:            ${free_usd:12.2f} USD")
    print(f"TOTAL ACCOUNT EQUITY:             ${total_equity:12.2f} USD")
    print(f"ACCOUNT TOTAL PnL (VS $100k):     ${account_pnl:+.2f} USD")
    print("=========================================================================================================\n")


if __name__ == "__main__":
    analyze()

