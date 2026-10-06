"""
Prints active portfolio allocations, entry prices, and sleeve deployment totals.
"""
import json
import os

POS_FILE = os.path.join("data_store", "active_positions.json")

def main():
    if not os.path.exists(POS_FILE):
        print("⚠️ No active_positions.json file found in data_store/")
        return

    try:
        with open(POS_FILE, "r") as f:
            pos = json.load(f)
    except Exception as e:
        print(f"Error loading {POS_FILE}: {e}")
        return

    if not pos:
        print("ℹ️ No active positions currently held.")
        return

    print("=======================================================================")
    print(f"{'PAIR':<12} | {'SLEEVE':<10} | {'ALLOCATED USD':<15} | {'ENTRY PRICE':<12}")
    print("-----------------------------------------------------------------------")

    total_usd = 0.0
    sleeve_totals = {"largecap": 0.0, "midcap": 0.0}

    for pair, v in pos.items():
        qty = float(v.get("quantity", 0.0))
        entry_p = float(v.get("entry_price", 0.0))
        usd_val = qty * entry_p
        sleeve = v.get("sleeve", "largecap")
        
        total_usd += usd_val
        sleeve_totals[sleeve] = sleeve_totals.get(sleeve, 0.0) + usd_val

        print(f"{pair:<12} | {sleeve:<10} | ${usd_val:<14.2f} | ${entry_p:<11.4f}")

    print("=======================================================================")
    print(f"Large-Cap Sleeve Deployed:  ${sleeve_totals.get('largecap', 0.0):10.2f} USD (Max $50,000)")
    print(f"Mid-Cap Sleeve Deployed:    ${sleeve_totals.get('midcap', 0.0):10.2f} USD (Max $20,000)")
    print(f"TOTAL CAPITAL DEPLOYED:     ${total_usd:10.2f} USD")
    print(f"UNALLOCATED CASH BUFFER:    ${100000.0 - total_usd:10.2f} USD (Preserved Buffer)")
    print("=======================================================================\n")

if __name__ == "__main__":
    main()
