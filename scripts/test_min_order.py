"""Read-only order-size inspection. This command never places orders.

Roostoo's official ExchangeInfo documents MiniOrder as notional value:
OrderPrice * OrderAmount > MiniOrder. AmountPrecision controls quantity
rounding. Inspect the current account's public metadata before deployment.
"""
import argparse
from ..execution.trader import get_exchange_info, get_ticker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default="BTC/USD")
    args = parser.parse_args()
    info = get_exchange_info()
    ticker = get_ticker(args.pair)
    if not info or not info.get("IsRunning") or not ticker or not ticker.get("Success"):
        parser.error("exchange metadata/ticker unavailable")
    meta = info["TradePairs"].get(args.pair)
    if not meta:
        parser.error("pair not listed")
    price = float(ticker["Data"][args.pair]["MinAsk"])
    print(f"{args.pair}: AmountPrecision={meta['AmountPrecision']}; notional must exceed ${meta['MiniOrder']}; current ask=${price}")
    print("No orders submitted. Short opens have a separate minimum collateral requirement.")


if __name__ == "__main__":
    main()
