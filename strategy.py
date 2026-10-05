"""Shared research/paper signal-to-target rules; no exchange calls."""
import math
from . import config, costs, sizing


def rule_direction(row, strategy):
    if strategy == "momentum":
        value = row.get("ret_5", float("nan"))
        return 1 if value > 0 else (-1 if value < 0 else 0)
    if strategy == "mean_reversion":
        z = row.get("zscore", float("nan"))
        return 1 if z < -config.MEAN_REVERSION_THRESHOLD else (-1 if z > config.MEAN_REVERSION_THRESHOLD else 0)
    if strategy == "cross_sectional":
        rank = row.get("cs_percentile", float("nan"))
        return 1 if rank > config.CROSS_SECTIONAL_LONG_PERCENTILE else (-1 if rank < config.CROSS_SECTIONAL_SHORT_PERCENTILE else 0)
    raise ValueError("unknown rule strategy")


def rule_target(row, strategy, allow_shorts=False):
    direction = rule_direction(row, strategy)
    if direction < 0 and not allow_shorts:
        direction = 0
    return direction * config.MAX_POSITION_PER_ASSET


def model_target(bundle, frame, spread=0., components=None, allow_shorts=False):
    components = config.COMPONENTS if components is None else components
    if not components.get("ml_model", False):
        return 0.
    p, returns = bundle.predict(frame)
    expected = float(returns[-1])
    if not math.isfinite(expected) or expected == 0:
        return 0.
    direction = 1 if expected > 0 else -1
    if direction < 0 and not allow_shorts:
        return 0.
    if components.get("cost_filter", False) and not costs.passes_cost_filter(abs(expected), spread=spread, short=direction < 0):
        return 0.
    if p is not None:
        confidence = float(p[-1]) if direction > 0 else 1 - float(p[-1])
        if confidence < config.MIN_CONFIDENCE_TO_TRADE:
            return 0.
    else:
        # A return regressor does not produce a calibrated win probability.
        # Use a fixed cap, optionally scaled by volatility, rather than invent one.
        confidence = 1.
    if components.get("position_sizing", False):
        vol = float(frame["realized_vol"].iloc[-1]) if "realized_vol" in frame else None
        size = sizing.confidence_to_size(confidence, vol)
    else:
        size = config.MAX_POSITION_PER_ASSET
    return direction * size
