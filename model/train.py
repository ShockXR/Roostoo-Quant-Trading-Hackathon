"""
Walk-forward training harness. Never use random K-fold here -- shuffling
time lets the model see the future during validation and will make a
useless model look great. Every split is strictly chronological.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .. import config

MODEL_REGISTRY = {
    # features live on very different scales (returns ~1e-3, RSI ~50, ranks
    # 1..N) -- unscaled, lbfgs never converges and the fit is meaningless
    "logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
    "random_forest": lambda: RandomForestClassifier(
        n_estimators=200, max_depth=5, random_state=config.RANDOM_SEED
    ),
    "gradient_boosting": lambda: GradientBoostingClassifier(random_state=config.RANDOM_SEED),
}


def walk_forward_splits(
    n_rows: int,
    train_bars: int = config.WALKFORWARD_TRAIN_BARS,
    val_bars: int = config.WALKFORWARD_VAL_BARS,
    step_bars: int = config.WALKFORWARD_STEP_BARS,
    purge_bars: int = config.TARGET_HORIZON,
):
    """Yields (train_idx, val_idx) index arrays, strictly chronological,
    rolled forward. The last `purge_bars` rows before each val block are
    dropped from training: their targets look `TARGET_HORIZON` bars ahead,
    i.e. into the val block, so keeping them leaks val prices into training."""
    start = 0
    while start + train_bars + val_bars <= n_rows:
        train_idx = np.arange(start, start + train_bars - purge_bars)
        val_idx = np.arange(start + train_bars, start + train_bars + val_bars)
        yield train_idx, val_idx
        start += step_bars


def train_walk_forward(
    X: pd.DataFrame,
    y: pd.Series,
    model_name: str = "logistic",
    **split_kwargs,
) -> list[dict]:
    """
    Trains on each walk-forward window and scores on the following block.
    Returns a list of per-fold results for comparison / overfitting checks.
    """
    results = []
    Xv = X.values
    yv = y.values

    for fold, (train_idx, val_idx) in enumerate(walk_forward_splits(len(X), **split_kwargs)):
        model = MODEL_REGISTRY[model_name]()
        model.fit(Xv[train_idx], yv[train_idx])

        train_score = model.score(Xv[train_idx], yv[train_idx])
        val_score = model.score(Xv[val_idx], yv[val_idx])

        val_proba = (
            model.predict_proba(Xv[val_idx])[:, 1]
            if hasattr(model, "predict_proba")
            else model.predict(Xv[val_idx])
        )

        results.append({
            "fold": fold,
            "model": model_name,
            "train_score": train_score,
            "val_score": val_score,
            "train_val_gap": train_score - val_score,   # overfitting signal
            "train_index": X.index[train_idx],
            "val_index": X.index[val_idx],
            "val_proba": val_proba,
            "val_true": yv[val_idx],
            "fitted_model": model,
        })
    return results
