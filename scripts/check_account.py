"""
Read-only check of the Roostoo account the live loop will trade: verifies
the API keys, shows the raw /v3/balance layout next to what
execution/rebalance.parse_balance() makes of it, and values the account at
Roostoo prices. Places NO orders.

Needs ROOSTOO_API_KEY / ROOSTOO_API_SECRET (environment or .env). Run from
the project root:

    .venv/bin/python scripts/check_account.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import register_package  # noqa: E402


def main():
    register_package()
    from roostoo import config
    from roostoo.execution.rebalance import parse_balance
    from roostoo.execution.trader import get_balance, get_exchange_info, get_server_time, get_ticker

    if not config.API_KEY or not config.SECRET:
        sys.exit("ROOSTOO_API_KEY / ROOSTOO_API_SECRET are not set")
    print("server time:", get_server_time())
    info = get_exchange_info()
    print("initial wallet:", info.get("InitialWallet"))

    resp = get_balance()
    if not resp:
        sys.exit("balance request failed (see log above)")
    print("\nraw /v3/balance top-level keys:", sorted(resp))
    print(json.dumps(resp, indent=2)[:2000])
    if not resp.get("Success", True):
        sys.exit(f"balance call rejected: {resp.get('ErrMsg')}")

    holdings = parse_balance(resp)
    print("\nparse_balance():", holdings)
    if not holdings:
        sys.exit("parse_balance() found no wallet -- the layout differs from Wallet/SpotWallet; fix it "
                 "before trading (execution/rebalance.py)")

    ticker = get_ticker()
    prices = {p.split("/")[0]: float(v["LastPrice"]) for p, v in (ticker or {}).get("Data", {}).items()}
    value = holdings.get("USD", 0.0)
    for coin, qty in holdings.items():
        if coin != "USD" and qty:
            value += qty * prices.get(coin, 0.0)
            print(f"  {coin}: {qty} x {prices.get(coin)}")
    print(f"\naccount value at Roostoo prices: ${value:,.2f}")
    missing = [p for p in config.PRODUCTION_SYMBOLS.values() if p not in info.get("TradePairs", {})]
    print("production pairs missing on Roostoo:", missing or "none")


if __name__ == "__main__":
    main()
