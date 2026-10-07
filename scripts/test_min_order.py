"""
Determines whether "Minimum order size: 1" means $1 notional (price * qty)
or 1 whole unit of the coin, by reading ExchangeInfo and attempting a few
deliberately tiny orders and reading back the exact ErrMsg.

Run with ROOSTOO_API_KEY / ROOSTOO_API_SECRET set (environment or .env),
from the project root:

    .venv/bin/python scripts/test_min_order.py

Safe to run against the mock exchange -- it's not real money, and every
attempt is logged with the raw response so you can see exactly what the
server says either way.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import register_package  # noqa: E402

register_package()
from roostoo.execution.trader import cancel_order, get_exchange_info, get_ticker, place_order  # noqa: E402

# Pick one cheap, high-unit-price coin and one expensive, low-unit-price
# coin -- if the rule were "1 whole unit", these two would behave very
# differently; if it's "$1 notional", both should behave the same way.
TEST_PAIRS = ["DOGE/USD", "BTC/USD"]


def main():
    info = get_exchange_info()
    if not info or not info.get("IsRunning", True):
        print("Could not reach exchange info, aborting.")
        sys.exit(1)

    pairs_info = info.get("TradePairs", {})

    for pair in TEST_PAIRS:
        meta = pairs_info.get(pair)
        if not meta:
            print(f"{pair}: not found in ExchangeInfo, skipping")
            continue

        mini_order = meta["MiniOrder"]
        amount_precision = meta["AmountPrecision"]
        print(f"\n=== {pair} ===")
        print(f"MiniOrder={mini_order}  AmountPrecision={amount_precision}")

        ticker = get_ticker(pair)
        if not ticker or not ticker.get("Success"):
            print(f"  could not fetch ticker for {pair}, skipping")
            continue
        last_price = ticker["Data"][pair]["LastPrice"]
        print(f"  LastPrice={last_price}")

        # Attempt 1: quantity = 1 whole unit (tests the "1 unit" theory)
        print("  -> attempting quantity=1 (whole unit)...")
        resp1 = place_order(pair, "BUY", quantity=1)
        print(f"     response: {resp1}")
        if resp1 and resp1.get("Success"):
            order_id = resp1["OrderDetail"]["OrderID"]
            cancel_order(order_id=order_id)  # clean up if it rested as a limit/pending order

        # Attempt 2: quantity sized so that price * qty ~= $0.50 (tests the
        # "$1 notional" theory -- this SHOULD fail if $1 notional is the rule,
        # and the ErrMsg wording will confirm which rule is actually enforced)
        tiny_qty = round(0.5 / last_price, amount_precision)
        if tiny_qty <= 0:
            tiny_qty = 10 ** (-amount_precision)
        print(f"  -> attempting quantity={tiny_qty} (~$0.50 notional)...")
        resp2 = place_order(pair, "BUY", quantity=tiny_qty)
        print(f"     response: {resp2}")
        if resp2 and resp2.get("Success"):
            order_id = resp2["OrderDetail"]["OrderID"]
            cancel_order(order_id=order_id)

    print(
        "\nRead the ErrMsg text above closely. A message like "
        "'quantity below minimum' or mentioning AmountPrecision/qty points to "
        "a UNIT minimum. A message mentioning order VALUE or notional, or "
        "attempt 2 succeeding while being well under 1 whole unit, confirms "
        "a $ NOTIONAL minimum."
    )


if __name__ == "__main__":
    main()
