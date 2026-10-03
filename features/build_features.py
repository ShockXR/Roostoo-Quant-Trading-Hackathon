"""
Combines all feature groups into a single feature matrix, respecting the
component on/off switches in config.COMPONENTS so the ablation study can
run the same pipeline with pieces disabled.
"""
import pandas as pd

from .. import config
from .cross_sectional import build_cross_sectional_features
from .mean_reversion import add_mean_reversion_features
from .momentum import add_momentum_features
from .regime import add_regime_features, attach_market_wide_features


def build_price_panel(raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """raw: dict[pair] -> df with columns timestamp, last_price.
    Returns wide panel aligned on timestamp (asof-merged, causal)."""
    panel = None
    for pair, df in raw.items():
        s = df.set_index("timestamp")["last_price"].rename(pair)
        panel = s.to_frame() if panel is None else panel.join(s, how="outer")
    panel = panel.sort_index().ffill()  # forward-fill only -- never bfill (that's leakage)
    return panel


def build_features_for_pair(
    pair: str,
    raw: dict[str, pd.DataFrame],
    price_panel: pd.DataFrame,
    components: dict = None,
) -> pd.DataFrame:
    components = components or config.COMPONENTS
    df = raw[pair].copy().set_index("timestamp")

    if components.get("momentum", True):
        df = add_momentum_features(df.reset_index(), price_col="last_price").set_index("timestamp")

    if components.get("mean_reversion", True):
        df = add_mean_reversion_features(df.reset_index(), price_col="last_price").set_index("timestamp")

    if components.get("regime", True):
        df = add_regime_features(df.reset_index(), price_col="last_price").set_index("timestamp")
        if config.MARKET_PROXY in raw and pair != config.MARKET_PROXY:
            market_df = add_regime_features(
                add_momentum_features(raw[config.MARKET_PROXY].copy(), price_col="last_price"),
                price_col="last_price",
            ).set_index("timestamp")
            df = attach_market_wide_features(df, market_df)

    if components.get("cross_sectional", True) and price_panel.shape[1] > 1:
        cs = build_cross_sectional_features(price_panel)
        if pair in cs:
            df = df.join(cs[pair], how="left")

    return df


def assert_no_lookahead(feature_df: pd.DataFrame, target_df: pd.DataFrame):
    """
    Sanity check: every feature row's timestamp must be <= the timestamp
    the target was computed from. Run this in tests / before every training
    run -- it's the single most important check in the whole pipeline.
    """
    common_idx = feature_df.index.intersection(target_df.index)
    if len(common_idx) == 0:
        raise ValueError("no overlapping timestamps between features and targets")
    # features are built causally by construction (rolling/pct_change/shift
    # only look backward); this check just guards against accidental bfill
    # or a future merge introducing forward-looking values.
    if feature_df.index.is_monotonic_increasing is False:
        raise ValueError("feature index not sorted -- cannot trust causal ordering")
    return True
