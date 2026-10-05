"""Causal feature groups, with raw optional ticker metadata kept out of ML."""
import numpy as np
import pandas as pd
from .. import config
from ..data.validation import clean_ticks
from .cross_sectional import build_cross_sectional_features
from .mean_reversion import add_mean_reversion_features
from .momentum import add_momentum_features
from .regime import add_regime_features, attach_market_wide_features

NON_MODEL_COLUMNS = {"timestamp", "last_price", "max_bid", "min_ask", "change",
                     "coin_trade_value", "unit_trade_value", "ma_fast", "ma_slow"}


def model_inputs(feature_df):
    """Keep normalized derived features; exclude raw and moving-average levels."""
    return feature_df.drop(columns=[c for c in feature_df if c in NON_MODEL_COLUMNS])


def build_price_panel(raw):
    series = {}
    for pair, frame in raw.items():
        clean = clean_ticks(frame)
        if not clean.empty:
            series[pair] = clean.set_index("timestamp")["last_price"]
    if not series:
        return pd.DataFrame()
    panel = pd.concat(series, axis=1).sort_index()
    for pair in panel:
        original = panel[pair]
        observed_at = pd.Series(np.where(original.notna(), panel.index, np.nan), index=panel.index).ffill()
        age = pd.Series(panel.index, index=panel.index) - observed_at
        panel[pair] = original.ffill().where(age <= config.MAX_QUOTE_AGE_MS)
    return panel


def build_features_for_pair(pair, raw, price_panel, components=None):
    components = config.COMPONENTS if components is None else components
    df = clean_ticks(raw[pair]).set_index("timestamp")
    raw_columns = set(df.columns)
    if components.get("momentum", False):
        df = add_momentum_features(df.reset_index()).set_index("timestamp")
    if components.get("mean_reversion", False):
        df = add_mean_reversion_features(df.reset_index()).set_index("timestamp")
    if components.get("regime", False):
        df = add_regime_features(df.reset_index()).set_index("timestamp")
        if config.MARKET_PROXY in raw and pair != config.MARKET_PROXY:
            market = clean_ticks(raw[config.MARKET_PROXY])
            market = add_regime_features(add_momentum_features(market)).set_index("timestamp")
            df = attach_market_wide_features(df, market)
    if components.get("cross_sectional", False) and price_panel.shape[1] > 1:
        # Compare only within the declared group, including when expanded.
        group = next((members for members in config.UNIVERSE_GROUPS.values() if pair in members), [pair])
        members = [p for p in price_panel if p in group]
        if len(members) > 1:
            cs = build_cross_sectional_features(price_panel[members])
            df = df.join(cs[pair], how="left")
    return model_inputs(df[[c for c in df.columns if c not in raw_columns]]).replace([np.inf, -np.inf], np.nan)


def assert_no_lookahead(feature_df, target_df, available_at=None):
    """Validate explicit availability times, not infer causality from values.

    An arbitrary numeric column cannot reveal where its values came from.
    Unknown provenance is rejected. Prefix-invariance tests independently
    verify the feature builder, while split purging protects future labels.
    """
    if not feature_df.index.is_monotonic_increasing or not feature_df.index.is_unique:
        raise ValueError("feature timestamps must be sorted and unique")
    if not feature_df.index.intersection(target_df.index).size:
        raise ValueError("no overlapping feature/target timestamps")
    if available_at is None:
        raise ValueError("feature availability timestamps required")
    times = available_at.reindex(feature_df.index)
    missing = times.isna().any().any() if isinstance(times, pd.DataFrame) else times.isna().any()
    if missing:
        raise ValueError("missing feature availability times")
    if isinstance(times, pd.DataFrame):
        if list(times.columns) != list(feature_df.columns) or times.gt(feature_df.index, axis=0).any().any():
            raise ValueError("feature unavailable at decision time")
    elif (times > pd.Series(feature_df.index, index=feature_df.index)).any():
        raise ValueError("feature unavailable at decision time")
    return True
