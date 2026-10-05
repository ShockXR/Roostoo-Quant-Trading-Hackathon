"""Risk sizing in fractions of equity; quantity conversion is separate."""
import math
from decimal import Decimal, ROUND_DOWN
from . import config


def confidence_to_size(proba, realized_vol, min_confidence=None, max_position=None):
    minimum = config.MIN_CONFIDENCE_TO_TRADE if min_confidence is None else min_confidence
    cap = config.MAX_POSITION_PER_ASSET if max_position is None else max_position
    if not 0 <= minimum < 1 or not 0 <= cap <= 1:
        raise ValueError("invalid confidence threshold or position cap")
    if not math.isfinite(proba) or not 0 <= proba <= 1 or proba < minimum:
        return 0.
    if realized_vol is None or not math.isfinite(realized_vol) or realized_vol < 0:
        return 0.
    vol_scale = min(1., config.SIZING_REFERENCE_VOL / realized_vol) if realized_vol > 0 else 1.
    return min(cap, max(0., (proba - minimum) / (1 - minimum) * cap * vol_scale))


def enforce_portfolio_limits(proposed_sizes, max_total_exposure=None):
    cap = config.MAX_PORTFOLIO_EXPOSURE if max_total_exposure is None else max_total_exposure
    if not 0 <= cap <= 1 or any(not math.isfinite(v) for v in proposed_sizes.values()):
        raise ValueError("invalid exposure budget or allocation")
    clipped = {k: max(-config.MAX_POSITION_PER_ASSET, min(v, config.MAX_POSITION_PER_ASSET)) for k, v in proposed_sizes.items()}
    total = sum(abs(v) for v in clipped.values())
    scale = min(1., cap / total) if total else 1.
    return {k: v * scale for k, v in clipped.items()}


def round_quantity(quantity, precision):
    if not math.isfinite(quantity) or quantity < 0 or not isinstance(precision, int) or not 0 <= precision <= 18:
        raise ValueError("invalid quantity or amount precision")
    return float(Decimal(str(quantity)).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_DOWN))


def capital_to_quantity(fraction, equity, price, precision):
    if not all(math.isfinite(v) for v in (fraction, equity, price)) or equity <= 0 or price <= 0:
        raise ValueError("invalid sizing inputs")
    return round_quantity(abs(fraction) * equity / price, precision)
