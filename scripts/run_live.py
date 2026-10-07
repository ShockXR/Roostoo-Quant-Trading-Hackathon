"""
Starts the live loop (main.py) for config.PRODUCTION_MODEL. Paper-trades
unless ROOSTOO_LIVE=1; real orders also need ROOSTOO_API_KEY /
ROOSTOO_API_SECRET.

Run from the project root:

    .venv/bin/python scripts/run_live.py            # hourly loop, DRY_RUN
    .venv/bin/python scripts/run_live.py --once     # one decision, print it, exit
    .venv/bin/python scripts/run_live.py --once --book mid   # the mid-cap book
    ROOSTOO_LIVE=1 .venv/bin/python scripts/run_live.py
"""
import argparse
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import register_package  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one paper decision on a fresh wallet, then exit")
    ap.add_argument("--book", choices=["large", "mid"], default=None,
                    help="large = config.PRODUCTION_MODEL (default), mid = config.MIDCAP_MODEL")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    register_package()
    from roostoo import config
    from roostoo import main as live
    book = args.book or config.LIVE_BOOK
    if not args.once:
        live.run_forever(book)
        return
    from roostoo.execution.trader import get_exchange_info
    info = get_exchange_info()
    broker = live.PaperBroker(float(info["InitialWallet"]["USD"]))
    live.DRY_RUN = True
    state = live.run_once(broker, {}, info["TradePairs"], book=book)
    print(json.dumps(state, indent=2, default=str))


if __name__ == "__main__":
    main()
