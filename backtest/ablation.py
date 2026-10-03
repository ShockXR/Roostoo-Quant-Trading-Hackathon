"""
Runs the sequential baseline comparison (buy/hold -> full cost-aware model)
described in the strategy doc, and reports whether each added component
actually earns its place out-of-sample and after costs.
"""
import pandas as pd

from .. import config, sizing
from ..model.targets import get_target
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
) -> pd.Series:
    target = get_target(price)
    aligned = features.join(target.rename("y")).dropna()
    if aligned.empty:
        return pd.Series(0.0, index=price.index)

    X = aligned.drop(columns=["y"])
    y = aligned["y"]
    folds = train_walk_forward(X, y, model_name=model_name)

    position = pd.Series(0.0, index=price.index)
    for fold in folds:
        proba = fold["val_proba"]
        idx = fold["val_index"]
        for i, p in zip(idx, proba):
            gross_edge_estimate = p - 0.5  # crude proxy without avg_win/avg_loss calibration
            if apply_cost_filter:
                from .. import costs
                if not costs.passes_cost_filter(gross_edge_estimate):
                    continue
            if apply_sizing:
                vol = features["realized_vol"].get(i, 0.0) if "realized_vol" in features.columns else 0.0
                size = sizing.confidence_to_size(p, vol)
            else:
                size = config.MAX_POSITION_PER_ASSET if p >= 0.5 else 0.0
            position.loc[i] = size
    return position


def run_ablation(price: pd.Series, features: pd.DataFrame) -> pd.DataFrame:
    """
    price: single-asset price series.
    features: full feature matrix (all groups already built) for that asset.
    Returns a comparison table across the 8 baselines.
    """
    rows = []

    def _eval(name: str, position: pd.Series):
        sim = simulate(price, position)
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
                                                    apply_cost_filter=True, apply_sizing=True))

    return pd.DataFrame(rows).set_index("baseline")
