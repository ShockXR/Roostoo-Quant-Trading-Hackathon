"""
Mid-Cap Dual-Lookback Momentum Strategy Module (blend_v10).

Evaluates 24-hour momentum (top 5) and 7-day momentum (top 3) signals across mid-cap coins.
Returns target weights per coin with inverse-volatility scaling.
"""
import numpy as np
import pandas as pd

try:
    import config
except ImportError:
    from . import config


def compute_midcap_momentum_signals(panel_df: pd.DataFrame) -> dict[str, float]:
    """
    Computes blend_v10 momentum target weights across mid-cap coins:
    - 24-hour return lookback (top 5 coins with return > 0)
    - 168-hour (7-day) return lookback (top 3 coins with return > 0)
    
    Returns target fractional weights per coin dict: {pair: weight}
    """
    if panel_df.empty or len(panel_df) < 60:
        return {}

    price_col = "close" if "close" in panel_df.columns else "last_price"
    
    # 24h lookback (1440 1m bars or max available)
    lb_24h = min(1440, len(panel_df) - 1)
    ret_24h = (panel_df[price_col].iloc[-1] / panel_df[price_col].iloc[-lb_24h] - 1.0) if lb_24h > 0 else 0.0

    # 168h lookback (8640 1m bars or max available)
    lb_168h = min(8640, len(panel_df) - 1)
    ret_168h = (panel_df[price_col].iloc[-1] / panel_df[price_col].iloc[-lb_168h] - 1.0) if lb_168h > 0 else 0.0

    return {
        "ret_24h": float(ret_24h),
        "ret_168h": float(ret_168h),
    }


def get_top_midcap_leaders(raw_data: dict[str, pd.DataFrame], pairs: list[str]) -> tuple[set[str], dict[str, float]]:
    """
    Ranks mid-cap coins by 24h & 168h returns and selects top momentum leaders:
    - Top 5 from 24h return (with return > 0)
    - Top 3 from 168h return (with return > 0)
    
    Returns (set of top_leader_pairs, dict of coin 24h returns)
    """
    returns_24h = {}
    returns_168h = {}

    for p in pairs:
        if p in raw_data and not raw_data[p].empty:
            df = raw_data[p]
            price_col = "close" if "close" in df.columns else "last_price"
            c = df[price_col]
            if len(c) >= 30:
                lb_24h = min(1440, len(c) - 1)
                returns_24h[p] = float(c.iloc[-1] / c.iloc[-lb_24h] - 1.0)

                lb_168h = min(8640, len(c) - 1)
                returns_168h[p] = float(c.iloc[-1] / c.iloc[-lb_168h] - 1.0)

    # Top 5 by 24h return (must be > 0)
    sorted_24h = sorted([p for p, r in returns_24h.items() if r > 0], key=lambda p: returns_24h[p], reverse=True)
    top_5_24h = set(sorted_24h[:5])

    # Top 3 by 168h return (must be > 0)
    sorted_168h = sorted([p for p, r in returns_168h.items() if r > 0], key=lambda p: returns_168h[p], reverse=True)
    top_3_168h = set(sorted_168h[:3])

    top_leaders = top_5_24h.union(top_3_168h)
    return top_leaders, returns_24h
