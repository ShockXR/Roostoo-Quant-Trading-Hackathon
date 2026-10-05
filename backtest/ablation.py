"""Paired out-of-sample baselines; each ML addition changes one component."""
import numpy as np
import pandas as pd
from .. import config
from ..model.train import training_frame, train_walk_forward, walk_forward_splits
from ..strategy import model_target, rule_target
from .engine import simulate, summarize

REGIME_COLUMNS = {"realized_vol", "vol_change", "trend_strength", "market_realized_vol", "market_ret_5"}


def _signal_from_model(features, price, model_name="logistic", apply_cost_filter=False, apply_sizing=False,
                       folds=None, allow_shorts=False):
    if folds is None:
        X, y, returns = training_frame(features, price)
        folds = train_walk_forward(X, y, model_name, returns=returns)
    position = pd.Series(0., index=price.index)
    switches = {**config.COMPONENTS, "ml_model": True, "cost_filter": apply_cost_filter, "position_sizing": apply_sizing}
    for fold in folds:
        if fold["fitted_model"] is None:
            continue  # Explicitly skipped folds remain flat on the common dates.
        for timestamp in fold["val_index"]:
            position.loc[timestamp] = model_target(fold["fitted_model"], features.loc[[timestamp]],
                                                   components=switches, allow_shorts=allow_shorts)
    return position


def run_ablation(price, features, allow_shorts=False):
    X, y, returns = training_frame(features, price)
    splits = list(walk_forward_splits(len(X)))
    if not splits:
        raise ValueError("insufficient known-label history for a complete purged out-of-sample fold")
    evaluation_index = X.index[np.concatenate([va for _, va in splits])]
    if not evaluation_index.is_unique:
        raise ValueError("validation periods overlap")
    # Do not compress missing timestamps into imaginary contiguous returns.
    positions_in_price = price.index.get_indexer(evaluation_index)
    if len(positions_in_price) > 1 and not np.all(np.diff(positions_in_price) == 1):
        raise ValueError("noncontiguous validation timestamps; evaluate separated periods independently")
    evaluation_price = price.loc[evaluation_index]
    rows = []

    def evaluate(name, position, folds=None):
        sim = simulate(evaluation_price, position.reindex(evaluation_index))
        stats = summarize(sim)
        stats.update(baseline=name, oos_start=int(evaluation_index[0]), oos_end=int(evaluation_index[-1]), n_oos=len(evaluation_index))
        stats["n_skipped_folds"] = sum(f.get("status") == "skipped" for f in (folds or []))
        rows.append(stats)

    evaluate("1_buy_hold", pd.Series(config.MAX_POSITION_PER_ASSET, index=evaluation_index))
    rule_positions = {}
    for name, strategy, required in [("2_momentum_only", "momentum", "ret_5"), ("3_mean_reversion_only", "mean_reversion", "zscore"),
                                     ("4_cross_sectional_only", "cross_sectional", "cs_percentile")]:
        if required in features:
            positions = features.loc[evaluation_index].apply(lambda row: rule_target(row, strategy, allow_shorts), axis=1)
            rule_positions[strategy] = positions
            evaluate(name, positions)
    if {"momentum", "mean_reversion"}.issubset(rule_positions):
        evaluate("5_momentum_plus_meanrev", (rule_positions["momentum"] + rule_positions["mean_reversion"]) / 2)
    if config.COMPONENTS.get("ml_model", False):
        without_regime = X.drop(columns=[c for c in X if c in REGIME_COLUMNS])
        raw_folds = train_walk_forward(without_regime, y, returns=returns)
        full_folds = train_walk_forward(X, y, returns=returns)
        evaluate("6_ml_logistic_raw", _signal_from_model(without_regime, price, folds=raw_folds, allow_shorts=allow_shorts), raw_folds)
        evaluate("7_ml_with_regime", _signal_from_model(X, price, folds=full_folds, allow_shorts=allow_shorts), full_folds)
        if config.COMPONENTS.get("cost_filter", False):
            evaluate("8_ml_with_cost_filter", _signal_from_model(X, price, apply_cost_filter=True, folds=full_folds, allow_shorts=allow_shorts), full_folds)
        if config.COMPONENTS.get("position_sizing", False):
            evaluate("9_full_sized", _signal_from_model(X, price, apply_cost_filter=config.COMPONENTS.get("cost_filter", False), apply_sizing=True, folds=full_folds, allow_shorts=allow_shorts), full_folds)
    return pd.DataFrame(rows).set_index("baseline")
