"""Rate-limited API wrappers; mutating requests are never blindly retried."""
from collections import deque
import logging
import threading
import time
import requests
from .. import config
from .auth import sign, get_timestamp

log = logging.getLogger("trader")
_SESSION = requests.Session()


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.calls = deque()
        self.lock = threading.Lock()

    def reserve(self):
        with self.lock:
            now = self.clock()
            while self.calls and now - self.calls[0] >= 60:
                self.calls.popleft()
            if len(self.calls) >= config.MAX_API_CALLS_PER_MINUTE:
                raise RuntimeError("API request budget exhausted; retry next cycle")
            self.calls.append(now)

    def available(self):
        with self.lock:
            now = self.clock()
            while self.calls and now - self.calls[0] >= 60:
                self.calls.popleft()
            return max(0, config.MAX_API_CALLS_PER_MINUTE - len(self.calls))


_LIMITER = RateLimiter()


def _request(method, path, signed_payload, params, retries=3, signed=True):
    mutation = method == "POST" and path != "/v3/query_order"
    attempts = 1 if mutation else retries
    for attempt in range(attempts):
        _LIMITER.reserve()
        if signed:
            if not config.API_KEY or not config.SECRET:
                raise ValueError("signed endpoint requires configured API credentials")
            headers, payload, body = sign(signed_payload)
        else:
            payload = dict(signed_payload)
            if path == "/v3/ticker":
                payload["timestamp"] = get_timestamp()
            headers, body = {}, ""
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            if params:
                response = _SESSION.get(config.BASE_URL + path, headers=headers, params=payload, timeout=10)
            else:
                response = _SESSION.post(config.BASE_URL + path, headers=headers, data=body, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid API response shape")
            if data.get("Success") is False:
                log.warning("%s rejected: %s", path, data.get("ErrMsg", "unknown error"))
            return data
        except (requests.exceptions.RequestException, ValueError) as exc:
            # No headers, signatures, payloads or credential-bearing request URLs.
            log.error("%s attempt %s/%s failed (%s)", path, attempt + 1, attempts, type(exc).__name__)
            if attempt + 1 < attempts:
                time.sleep(min(2 ** (attempt + 1), 10))
    return None


def get_server_time():
    return _request("GET", "/v3/serverTime", {}, True, signed=False)


def get_exchange_info():
    return _request("GET", "/v3/exchangeInfo", {}, True, signed=False)


def get_ticker(pair=None):
    return _request("GET", "/v3/ticker", {"pair": pair} if pair else {}, True, signed=False)


def get_balance():
    return _request("GET", "/v3/balance", {}, True)


def get_pending_count():
    return _request("GET", "/v3/pending_count", {}, True)


def place_order(pair, side, quantity, price=None):
    payload = {"pair": pair, "side": side.upper(), "quantity": str(quantity), "type": "LIMIT" if price is not None else "MARKET"}
    if price is not None:
        payload["price"] = str(price)
    return _request("POST", "/v3/place_order", payload, False)


def cancel_order(order_id=None, pair=None):
    payload = {"order_id": str(order_id)} if order_id is not None else {"pair": pair} if pair else {}
    return _request("POST", "/v3/cancel_order", payload, False)


def query_order(order_id=None, pair=None, pending_only=None):
    payload = {"order_id": str(order_id)} if order_id is not None else {"pair": pair} if pair else {}
    if order_id is None and pair and pending_only is not None:
        payload["pending_only"] = "TRUE" if pending_only else "FALSE"
    return _request("POST", "/v3/query_order", payload, False)


def short_open(pair, collateral, price=None):
    payload = {"pair": pair, "collateral": str(collateral)}
    if price is not None:
        payload.update(order_type="LIMIT", price=str(price))
    return _request("POST", "/v6/short_open", payload, False)


def short_close(pair, close_qty=None, close_pct=None):
    payload = {"pair": pair}
    if close_qty is not None:
        payload["close_qty"] = str(close_qty)
    elif close_pct is not None:
        payload["close_pct"] = str(close_pct)
    return _request("POST", "/v6/short_close", payload, False)


def get_short_positions():
    return _request("GET", "/v6/short_positions", {}, True)
