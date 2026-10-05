"""
Multi-Factor Volatility-Adjusted Position Sizing Module.

Calculates capital allocation per asset based on 3 quantitative factors:
1. Signal Strength & Dip Depth
2. Inverse Volatility Risk Scale
3. Hard Portfolio Exposure & Cash Reserve Caps
"""
import numpy as np

try:
    import config
except ImportError:
    from . import config


def calculate_position_size(
    signal_strength: float,
    realized_vol: float | None = None,
    max_position: float = config.MAX_POSITION_PER_ASSET,
    ref_vol: float = config.SIZING_REFERENCE_VOL,
) -> float:
    """
    Computes exact portfolio weight fraction [0.0, max_position] allocated to a coin trade.
    
    Factors considered:
    - signal_strength: target weight or confidence from strategy (e.g. 0.20)
    - realized_vol: rolling price volatility of the asset (shrinks size during extreme volatility spikes)
    - max_position: maximum single position cap (20% of capital)
    """
    if signal_strength <= 0:
        return 0.0

    raw_size = min(signal_strength, max_position)

    # Volatility-aware inverse scaling: normalize against reference volatility
    if realized_vol and realized_vol > 0:
        vol_scale = min(1.0, ref_vol / max(1e-6, realized_vol))
        raw_size *= vol_scale

    return float(np.clip(raw_size, 0.0, max_position))


def confidence_to_size(
    proba: float,
    realized_vol: float,
    min_confidence: float = config.MIN_CONFIDENCE_TO_TRADE,
    max_position: float = config.MAX_POSITION_PER_ASSET,
) -> float:
    """Backward-compatible helper function."""
    if proba < min_confidence:
        return 0.0
    confidence_scale = (proba - min_confidence) / max(1e-6, 1.0 - min_confidence)
    raw_size = confidence_scale * max_position
    if realized_vol and realized_vol > 0:
        vol_scale = min(1.0, config.SIZING_REFERENCE_VOL / realized_vol)
        raw_size *= vol_scale
    return float(np.clip(raw_size, 0.0, max_position))


def enforce_portfolio_limits(
    proposed_sizes: dict[str, float],
    max_total_exposure: float = config.MAX_PORTFOLIO_EXPOSURE,
) -> dict[str, float]:
    """
    Scales all proposed asset weights down proportionally if their sum exceeds
    the portfolio-wide exposure cap (70%), preserving the 30% cash buffer.
    """
    total = sum(proposed_sizes.values())
    if total <= max_total_exposure or total == 0:
        return proposed_sizes
    scale = max_total_exposure / total
    return {k: float(v * scale) for k, v in proposed_sizes.items()}
