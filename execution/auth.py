"""
Request signing for RCL_TopLevelCheck endpoints.
"""
import hashlib
import hmac
import time

from .. import config


def get_timestamp() -> str:
    """13-digit millisecond timestamp, as required by the API."""
    return str(int(time.time() * 1000))


def sign(payload: dict, secret: str | None = None) -> tuple[dict, dict, str]:
    """
    Adds a timestamp to payload, builds the sorted query/body string,
    and returns (headers, payload, total_params_string).

    Only include parameters that the endpoint actually signs -- extra
    params are ignored server-side and will make your own signature
    mismatch what the server computes.
    """
    payload = dict(payload)  # don't mutate caller's dict
    secret = config.SECRET if secret is None else secret
    payload["timestamp"] = get_timestamp()

    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)

    signature = hmac.new(
        secret.encode("utf-8"),
        total_params.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "RST-API-KEY": config.API_KEY,
        "MSG-SIGNATURE": signature,
    }
    return headers, payload, total_params
