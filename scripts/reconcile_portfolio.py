"""
Roostoo Portfolio Reconciliation Script.

Queries your live Roostoo exchange account balance (/v3/balance), compares actual wallet coin holdings
against active bot target positions in data_store/active_positions.json, and liquidates excess coin inventory
back into free USD cash.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import config
    from execution.trader import get_balance, place_order, format_quantity
except ImportError:
    from . import config
    from .execution.trader import get_balance, place_order, format_quantity

POS_FILE = os.path.join(getattr(config, "STORE_DIR", "data_store"), "active_positions.json")


def reconcile():
    print("=======================================================================")
    print("🔄 ROOSTOO EXCHANGE PORTFOLIO RECONCILIATION")
    print("=======================================================================\n")

    # Load active bot positions from disk
    target_pos = {}
    if os.path.exists(POS_FILE):
        try:
            with open(POS_FILE, "r") as f:
                target_pos = json.load(f)
        except Exception as e:
            print(f"Error loading {POS_FILE}: {e}")

    # Query live Roostoo wallet balance
    print("Querying live Roostoo wallet balance via /v3/balance...")
    resp = get_balance()
    if not resp or not isinstance(resp, dict) or not resp.get("Success"):
        print("❌ Failed to fetch wallet balance from Roostoo API.")
        return

    wallet = resp.get("SpotWallet", {})
    free_usd = float(wallet.get("USD", {}).get("Free", 0.0))
    print(f"💰 Current Free USD Cash: ${free_usd:.2f} USD\n")

    print(f"{'COIN':<8} | {'WALLET HOLDING':<16} | {'BOT TARGET':<14} | {'EXCESS TO SELL':<15} | {'ACTION':<10}")
    print("-----------------------------------------------------------------------------------------")

    sold_count = 0
    recovered_usd = 0.0

    # Iterate through all coins in wallet (excluding USD)
    for coin, info in wallet.items():
        if coin == "USD":
            continue

        pair = f"{coin}/USD"
        free_qty = float(info.get("Free", 0.0))
        frozen_qty = float(info.get("Frozen", 0.0))
        total_wallet_qty = free_qty + frozen_qty

        if total_wallet_qty <= 0:
            continue

        # Get target quantity from bot's active_positions.json
        target_qty = 0.0
        if pair in target_pos:
            target_qty = float(target_pos[pair].get("quantity", 0.0))

        excess_qty = max(0.0, total_wallet_qty - target_qty)

        if excess_qty > 0 and free_qty > 0:
            qty_to_sell = min(excess_qty, free_qty)
            formatted_sell_qty = format_quantity(pair, qty_to_sell)
            
            if float(formatted_sell_qty) > 0:
                print(f"{coin:<8} | {total_wallet_qty:<16.4f} | {target_qty:<14.4f} | {qty_to_sell:<15.4f} | SELLING...")
                sell_resp = place_order(pair, "SELL", quantity=qty_to_sell)
                if sell_resp and sell_resp.get("Success"):
                    print(f"✅ Successfully sold {formatted_sell_qty} {coin} back to USD cash!")
                    sold_count += 1
                else:
                    print(f"⚠️ Sell order for {coin} returned: {sell_resp}")
            else:
                print(f"{coin:<8} | {total_wallet_qty:<16.4f} | {target_qty:<14.4f} | {excess_qty:<15.4f} | INTACT")
        else:
            print(f"{coin:<8} | {total_wallet_qty:<16.4f} | {target_qty:<14.4f} | 0.0000          | MATCHED")

    print("\n=======================================================================")
    print(f"✅ Reconciliation Complete! Adjusted {sold_count} excess holdings.")
    
    # Re-query wallet to print updated USD cash
    resp_after = get_balance()
    if resp_after and isinstance(resp_after, dict) and resp_after.get("Success"):
        new_free_usd = float(resp_after.get("SpotWallet", {}).get("USD", {}).get("Free", 0.0))
        print(f"💵 Updated Free USD Cash in Wallet: ${new_free_usd:.2f} USD")
    print("=======================================================================\n")


if __name__ == "__main__":
    reconcile()
