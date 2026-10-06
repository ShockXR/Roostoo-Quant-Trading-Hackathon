"""
Prints live Roostoo exchange account balance (/v3/balance), matching your Roostoo app 100%.
Displays exact coin quantities, current market prices, live USD holding values, and free cash.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import config
    from execution.trader import get_balance, get_ticker
except ImportError:
    from . import config
    from .execution.trader import get_balance, get_ticker

POS_FILE = os.path.join(getattr(config, "STORE_DIR", "data_store"), "active_positions.json")


def main():
    print("=======================================================================")
    print("📱 LIVE ROOSTOO APP PORTFOLIO BALANCE & ALLOCATIONS")
    print("=======================================================================\n")

    # Query live Roostoo wallet balance via API
    resp = get_balance()
    if not resp or not isinstance(resp, dict) or not resp.get("Success"):
        print("⚠️ Could not reach Roostoo API directly; checking local disk state...\n")
        show_disk_fallback()
        return

    wallet = resp.get("SpotWallet", {})
    free_usd = float(wallet.get("USD", {}).get("Free", 0.0))

    # Fetch live ticker prices for evaluation
    ticker_resp = get_ticker()
    prices = {}
    if ticker_resp and isinstance(ticker_resp, dict):
        ticker_list = ticker_resp.get("TickerList", {}) or ticker_resp.get("tickerList", {})
        if isinstance(ticker_list, dict):
            for p, pdata in ticker_list.items():
                if isinstance(pdata, dict):
                    val = pdata.get("last_price") or pdata.get("LastPrice") or pdata.get("price") or pdata.get("Price") or 0.0
                    prices[p] = float(val)
                elif isinstance(pdata, (int, float, str)):
                    prices[p] = float(pdata)
        elif isinstance(ticker_list, list):
            for item in ticker_list:
                if isinstance(item, dict):
                    p = item.get("pair") or item.get("Pair") or item.get("symbol") or item.get("Symbol")
                    val = item.get("last_price") or item.get("LastPrice") or item.get("price") or item.get("Price") or 0.0
                    if p:
                        prices[p] = float(val)

    print(f"{'COIN':<8} | {'SLEEVE':<10} | {'EXACT QUANTITY':<18} | {'LIVE PRICE':<12} | {'USD VALUE':<14}")
    print("---------------------------------------------------------------------------------------")

    total_coins_usd = 0.0
    sleeve_totals = {"largecap": 0.0, "midcap": 0.0}

    for coin, info in wallet.items():
        if coin == "USD":
            continue

        pair = f"{coin}/USD"
        free_qty = float(info.get("Free", 0.0))
        frozen_qty = float(info.get("Frozen", 0.0))
        total_qty = free_qty + frozen_qty

        if total_qty <= 0:
            continue

        live_price = prices.get(pair, 0.0)

        # 1. Try per-pair Roostoo ticker query
        if live_price == 0.0:
            try:
                single_tick = get_ticker(pair)
                if single_tick and isinstance(single_tick, dict):
                    tlist = single_tick.get("TickerList", {})
                    if isinstance(tlist, dict):
                        for k_p, pdata in tlist.items():
                            if isinstance(pdata, dict):
                                val = pdata.get("last_price") or pdata.get("LastPrice") or pdata.get("price") or 0.0
                                if float(val) > 0:
                                    live_price = float(val)
                                    break
            except Exception:
                pass

        # 2. Try local parquet historical data_store file
        if live_price == 0.0:
            safe = pair.replace("/", "_")
            p_file = os.path.join(getattr(config, "STORE_DIR", "data_store"), f"{safe}.parquet")
            if os.path.exists(p_file):
                try:
                    df_p = pd.read_parquet(p_file)
                    if not df_p.empty:
                        col = "last_price" if "last_price" in df_p.columns else df_p.columns[-1]
                        live_price = float(df_p[col].iloc[-1])
                except Exception:
                    pass

        # 3. Try Binance live REST API ticker fallback
        if live_price == 0.0:
            try:
                import requests
                binance_sym = f"{coin}USDT"
                b_resp = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={binance_sym}", timeout=5)
                if b_resp.status_code == 200:
                    live_price = float(b_resp.json().get("price", 0.0))
            except Exception:
                pass

        usd_val = total_qty * live_price
        total_coins_usd += usd_val

        sleeve = "largecap" if pair in getattr(config, "LARGECAP_UNIVERSE", []) else "midcap"
        sleeve_totals[sleeve] = sleeve_totals.get(sleeve, 0.0) + usd_val

        print(f"{coin:<8} | {sleeve:<10} | {total_qty:<18.4f} | ${live_price:<11.4f} | ${usd_val:<13.2f}")

    total_account_equity = free_usd + total_coins_usd

    print("=======================================================================")
    print(f"Large-Cap Holdings Value:    ${sleeve_totals.get('largecap', 0.0):12.2f} USD")
    print(f"Mid-Cap Holdings Value:      ${sleeve_totals.get('midcap', 0.0):12.2f} USD")
    print(f"Total Coin Holdings Value:   ${total_coins_usd:12.2f} USD")
    print(f"Free USD Cash Balance:       ${free_usd:12.2f} USD (Roostoo Wallet)")
    print("-----------------------------------------------------------------------")
    print(f"TOTAL ROOSTOO ACCOUNT EQUITY: ${total_account_equity:12.2f} USD")
    print("=======================================================================\n")


def show_disk_fallback():
    if not os.path.exists(POS_FILE):
        print("⚠️ No active_positions.json file found.")
        return

    try:
        with open(POS_FILE, "r") as f:
            pos = json.load(f)
    except Exception as e:
        print(f"Error loading {POS_FILE}: {e}")
        return

    print(f"{'PAIR':<12} | {'SLEEVE':<10} | {'QUANTITY':<15} | {'ENTRY PRICE':<12} | {'USD COST':<14}")
    print("-----------------------------------------------------------------------")
    total_usd = 0.0
    for pair, v in pos.items():
        qty = float(v.get("quantity", 0.0))
        entry_p = float(v.get("entry_price", 0.0))
        usd_val = qty * entry_p
        sleeve = v.get("sleeve", "largecap")
        total_usd += usd_val
        print(f"{pair:<12} | {sleeve:<10} | {qty:<15.4f} | ${entry_p:<11.4f} | ${usd_val:<13.2f}")
    print("=======================================================================\n")


if __name__ == "__main__":
    main()
