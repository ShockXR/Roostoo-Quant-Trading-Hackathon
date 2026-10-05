"""
Position sizing. Deliberately kept simple and explicit: size scales with
model confidence and shrinks with realized volatility, clipped to hard
risk limits. This layer matters more to overall PnL than small gains in
model accuracy -- a mediocre model with disciplined sizing beats a great
model with none.
"""
try:
    from . import config
except ImportError:
    import config


def confidence_to_size(
    proba: float,
    realized_vol: float,
    min_confidence: float = config.MIN_CONFIDENCE_TO_TRADE,
    max_position: float = config.MAX_POSITION_PER_ASSET,
) -> float:
    """
    Returns a fraction of capital to allocate to this asset, in [0, max_position].
    proba: model's confidence the trade clears the target threshold.
    realized_vol: recent realized volatility (used to shrink size in choppy/violent markets).
    """
    if proba < min_confidence:
        return 0.0

    # confidence above the minimum threshold scales size linearly to max_position
    confidence_scale = (proba - min_confidence) / max(1e-6, 1 - min_confidence)
    raw_size = confidence_scale * max_position

    # volatility-aware shrink: normalize against a reference vol level.
    # if realized_vol is missing/zero, don't scale (avoid div-by-zero blowups).
    if realized_vol and realized_vol > 0:
        vol_scale = min(1.0, config.SIZING_REFERENCE_VOL / realized_vol)
        raw_size *= vol_scale

    return max(0.0, min(raw_size, max_position))


def enforce_portfolio_limits(
    proposed_sizes: dict[str, float],
    max_total_exposure: float = config.MAX_PORTFOLIO_EXPOSURE,
) -> dict[str, float]:
    """Scales all proposed sizes down proportionally if their sum exceeds
    the portfolio-wide exposure cap."""
    total = sum(proposed_sizes.values())
    if total <= max_total_exposure or total == 0:
        return proposed_sizes
    scale = max_total_exposure / total
    return {k: v * scale for k, v in proposed_sizes.items()}
