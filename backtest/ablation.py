"""
Runs the sequential baseline comparison (buy/hold -> full cost-aware model)
described in the strategy doc, and reports whether each added component
actually earns its place out-of-sample and after costs.
"""
import numpy as np
import pandas as pd

from .. import config, costs, sizing
from ..features.build_features import model_inputs
from ..model.targets import future_return, get_target
from ..model.train import train_walk_forward
from .engine import simulate, summarize


def _signal_buy_hold(price: pd.Series) -> pd.Series:
    return pd.Series(config.MAX_POSITION_PER_ASSET, index=price.index)


def _signal_from_rule(feature: pd.Series, long_thresh: float, short_thresh: float | None = None) -> pd.Series:
    pos = pd.Series(0.0, index=feature.index)
    pos[feature > long_thresh] = config.MAX_POSITION_PER_ASSET
    if short_thresh is not None:
        pos[feature < short_thresh] = -config.MAX_POSITION_PER_ASSET
    return pos


def _signal_from_model(
    features: pd.DataFrame,
    price: pd.Series,
    model_name: str,
    apply_cost_filter: bool,
    apply_sizing: bool,
    taker: bool = True,
) -> pd.Series:
    target = get_target(price)
    fwd_ret = future_return(price)
    aligned = model_inputs(features).join(target.rename("y")).dropna()
    if aligned.empty:
        return pd.Series(0.0, index=price.index)

    X = aligned.drop(columns=["y"])
    y = aligned["y"]
    folds = train_walk_forward(X, y, model_name=model_name)

    position = pd.Series(0.0, index=price.index)
    for fold in folds:
        proba = pd.Series(fold["val_proba"], index=fold["val_index"])

        if apply_cost_filter:
            # Turn P(target hit) into an expected return using the average
            # realized forward return of hits / misses in this fold's TRAIN
            # window only (no val data). The old `p - 0.5` proxy compared a
            # probability with a return, so it passed almost every p > 0.503.
            train_y = y.loc[fold["train_index"]]
            train_fwd = fwd_ret.loc[fold["train_index"]]
            avg_win = train_fwd[train_y == 1].mean()
            avg_loss = -train_fwd[train_y == 0].mean()
            if np.isnan(avg_win) or np.isnan(avg_loss):
                continue  # no hits in this train window -- no basis to trade
            expected = costs.proba_to_expected_return(proba, avg_win, avg_loss)
            proba = proba[costs.passes_cost_filter(expected, taker)]

        if apply_sizing:
            vol = features["realized_vol"].reindex(proba.index).fillna(0.0) \
                if "realized_vol" in features.columns else pd.Series(0.0, index=proba.index)
            size = [sizing.confidence_to_size(p, v) for p, v in zip(proba, vol)]
        else:
            size = (proba >= 0.5) * config.MAX_POSITION_PER_ASSET
        position.loc[proba.index] = size

    # The model predicts the return over the next TARGET_HORIZON bars, so
    # hold each entry that long instead of exiting the moment the signal
    # fades -- otherwise we pay a full round trip to capture one bar of a
    # 10-bar forecast. A fresh signal inside the window extends the hold.
    return position.rolling(config.TARGET_HORIZON, min_periods=1).max()


def run_ablation(price: pd.Series, features: pd.DataFrame, taker: bool = True) -> pd.DataFrame:
    """
    price: single-asset price series.
    features: full feature matrix (all groups already built) for that asset.
    taker: charge taker fees (market orders) vs maker fees (resting limits).
    Maker assumes every order fills at the bar price -- optimistic, so treat
    maker results as an upper bound, not a forecast.
    Returns a comparison table across the 8 baselines.
    """
    rows = []

    def _eval(name: str, position: pd.Series):
        sim = simulate(price, position, taker=taker)
        stats = summarize(sim)
        stats["baseline"] = name
        rows.append(stats)

    # 1. buy/hold
    _eval("1_buy_hold", _signal_buy_hold(price))

    # 2. simple momentum (rule-based): long when short-horizon return positive
    if "ret_5" in features.columns:
        _eval("2_momentum_only", _signal_from_rule(features["ret_5"], long_thresh=0.0))

    # 3. simple mean reversion (rule-based): long when z-score very negative
    if "zscore" in features.columns:
        pos = pd.Series(0.0, index=features.index)
        pos[features["zscore"] < -1.5] = config.MAX_POSITION_PER_ASSET
        pos[features["zscore"] > 1.5] = -config.MAX_POSITION_PER_ASSET
        _eval("3_mean_reversion_only", pos)

    # 4. cross-sectional momentum only
    if "cs_percentile" in features.columns:
        _eval("4_cross_sectional_only", _signal_from_rule(features["cs_percentile"], long_thresh=0.7, short_thresh=0.3))

    # 5. momentum + mean reversion combined (simple average of rule signals)
    if {"ret_5", "zscore"}.issubset(features.columns):
        mom = _signal_from_rule(features["ret_5"], long_thresh=0.0)
        mr = pd.Series(0.0, index=features.index)
        mr[features["zscore"] < -1.5] = config.MAX_POSITION_PER_ASSET
        mr[features["zscore"] > 1.5] = -config.MAX_POSITION_PER_ASSET
        _eval("5_momentum_plus_meanrev", (mom + mr) / 2)

    # 6. features + simple ML (logistic), no cost filter/sizing yet
    _eval("6_ml_logistic_raw", _signal_from_model(features, price, "logistic",
                                                    apply_cost_filter=False, apply_sizing=False))

    # 7. features + ML + regime info (regime cols already in `features` if enabled)
    _eval("7_ml_with_regime", _signal_from_model(features, price, "random_forest",
                                                   apply_cost_filter=False, apply_sizing=False))

    # 8. full transaction-cost-aware strategy
    _eval("8_full_cost_aware", _signal_from_model(features, price, "random_forest",
                                                    apply_cost_filter=True, apply_sizing=True, taker=taker))

    return pd.DataFrame(rows).set_index("baseline")
