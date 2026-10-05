"""
Polls /v3/ticker on a fixed interval and appends each tick to local storage.
Run this standalone (python -m roostoo_bot.data.collector) to build up a
price history before you have anything else to backtest against.
"""
import logging
import time

try:
    from .. import config
    from ..execution.trader import get_ticker
    from .store import append_tick
except ImportError:
    import config
    from execution.trader import get_ticker
    from data.store import append_tick

log = logging.getLogger("collector")
logging.basicConfig(level=logging.INFO)


def poll_once():
    resp = get_ticker()
    if not resp or not resp.get("Success"):
        log.warning("ticker poll failed: %s", resp)
        return
    server_time = resp["ServerTime"]
    data = resp.get("Data", {})
    for pair in config.UNIVERSE:
        info = data.get(pair)
        if info is None:
            continue
        append_tick(pair, {
            "timestamp": server_time,
            "last_price": info["LastPrice"],
            "max_bid": info["MaxBid"],
            "min_ask": info["MinAsk"],
            "change": info.get("Change"),
            "coin_trade_value": info.get("CoinTradeValue"),
            "unit_trade_value": info.get("UnitTradeValue"),
        })


def run_forever(interval: int = config.POLL_INTERVAL_SECONDS):
    log.info("starting collector, polling every %ss for %s", interval, config.UNIVERSE)
    while True:
        try:
            poll_once()
        except Exception:
            log.exception("unexpected error in poll loop")
        time.sleep(interval)


if __name__ == "__main__":
    run_forever()
