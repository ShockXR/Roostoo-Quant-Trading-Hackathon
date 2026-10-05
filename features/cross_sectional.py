"""
Cross-sectional momentum features.

Answers a different question than time-series momentum:
"is this asset outperforming the OTHER assets right now?"

Requires price histories for the whole universe aligned on timestamp.
"""
import pandas as pd

try:
    from .. import config
except ImportError:
    import config


def build_cross_sectional_features(
    price_panel: pd.DataFrame,
    ret_horizon: int = 5,
) -> dict[str, pd.DataFrame]:
    """
    price_panel: wide DataFrame, index = timestamp, columns = pair, values = last_price.
    Returns dict[pair] -> DataFrame of cross-sectional features aligned to price_panel.index.
    """
    returns = price_panel.pct_change(ret_horizon)

    cs_mean = returns.mean(axis=1)
    market_ret = returns[config.MARKET_PROXY] if config.MARKET_PROXY in returns.columns else cs_mean

    out = {}
    for pair in price_panel.columns:
        r = returns[pair]
        feat = pd.DataFrame(index=price_panel.index)
        feat["cs_rank"] = returns.rank(axis=1, pct=False)[pair]
        feat["cs_percentile"] = returns.rank(axis=1, pct=True)[pair]
        feat["ret_vs_cs_mean"] = r - cs_mean
        feat["ret_vs_market"] = r - market_ret
        # cross-sectional dispersion at this timestamp: how spread out are
        # returns across the universe right now (a regime-ish signal too)
        feat["cs_dispersion"] = returns.std(axis=1)
        out[pair] = feat
    return out
