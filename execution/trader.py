"""
Thin, defensive wrappers around the trading endpoints.
Every call returns None on failure (after logging) rather than raising,
so the main loop can decide how to react instead of crashing mid-run.
"""
import logging
import time

import requests

try:
    from .. import config
    from .auth import sign
except ImportError:
    import config
    from execution.auth import sign

log = logging.getLogger("trader")

_SESSION = requests.Session()


def _request(method: str, path: str, signed_payload: dict, params: bool, retries: int = 3):
    url = config.BASE_URL + path
    headers, payload, total_params = sign(signed_payload)
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    for attempt in range(1, retries + 1):
        try:
            if params:
                resp = _SESSION.get(url, headers=headers, params=payload, timeout=10)
            else:
                resp = _SESSION.post(url, headers=headers, data=total_params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("Success", True):
                log.warning("%s failed: %s", path, data.get("ErrMsg"))
            return data
        except requests.exceptions.RequestException as e:
            log.error("%s attempt %d/%d failed: %s", path, attempt, retries, e)
            time.sleep(min(2 ** attempt, 10))
    log.error("%s: giving up after %d attempts", path, retries)
    return None


def get_server_time():
    resp = _SESSION.get(config.BASE_URL + "/v3/serverTime", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_exchange_info():
    resp = _SESSION.get(config.BASE_URL + "/v3/exchangeInfo", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_ticker(pair: str | None = None):
    payload = {"pair": pair} if pair else {}
    return _request("GET", "/v3/ticker", payload, params=True)


def get_balance():
    return _request("GET", "/v3/balance", {}, params=True)


def get_pending_count():
    return _request("GET", "/v3/pending_count", {}, params=True)


import csv
import os
from datetime import datetime

TRADES_CSV_PATH = os.path.join(config.STORE_DIR, "trades_log.csv")


def log_trade_to_csv(pair: str, side: str, quantity: float, execution_type: str = "LIVE", price: float | None = None, order_id: str | None = None):
    """Appends trade records (Live or Paper) to data_store/trades_log.csv automatically."""
    os.makedirs(config.STORE_DIR, exist_ok=True)
    file_exists = os.path.isfile(TRADES_CSV_PATH)
    with open(TRADES_CSV_PATH, mode="a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "pair", "side", "quantity", "price", "execution_type", "order_id"])
        writer.writerow([
            datetime.utcnow().isoformat(),
            pair,
            side.upper(),
            quantity,
            price if price is not None else "MARKET",
            execution_type,
            order_id if order_id is not None else ""
        ])


def place_order(pair: str, side: str, quantity: float, price: float | None = None):
    payload = {
        "pair": pair,
        "side": side.upper(),
        "quantity": str(quantity),
        "type": "LIMIT" if price is not None else "MARKET",
    }
    if price is not None:
        payload["price"] = str(price)
    resp = _request("POST", "/v3/place_order", payload, params=False)
    order_id = resp.get("order_id") if isinstance(resp, dict) else None
    log_trade_to_csv(pair, side, quantity, execution_type="LIVE", price=price, order_id=order_id)
    return resp


def cancel_order(order_id: int | None = None, pair: str | None = None):
    payload = {}
    if order_id is not None:
        payload["order_id"] = str(order_id)
    elif pair is not None:
        payload["pair"] = pair
    return _request("POST", "/v3/cancel_order", payload, params=False)


def query_order(order_id=None, pair=None, pending_only=None):
    payload = {}
    if order_id is not None:
        payload["order_id"] = str(order_id)
    elif pair is not None:
        payload["pair"] = pair
        if pending_only is not None:
            payload["pending_only"] = "TRUE" if pending_only else "FALSE"
    return _request("POST", "/v3/query_order", payload, params=False)


def short_open(pair: str, collateral: float, price: float | None = None):
    payload = {"pair": pair, "collateral": str(collateral)}
    if price is not None:
        payload["order_type"] = "LIMIT"
        payload["price"] = str(price)
    return _request("POST", "/v6/short_open", payload, params=False)


def short_close(pair: str, close_qty: float | None = None, close_pct: float | None = None):
    payload = {"pair": pair}
    if close_qty is not None:
        payload["close_qty"] = str(close_qty)
    elif close_pct is not None:
        payload["close_pct"] = str(close_pct)
    return _request("POST", "/v6/short_close", payload, params=False)


def get_short_positions():
    return _request("GET", "/v6/short_positions", {}, params=True)
