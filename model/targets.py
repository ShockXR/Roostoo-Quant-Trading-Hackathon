"""
Target construction. This is the ONE place future information is allowed
to enter the pipeline -- by definition, since it's what we predict. It
must never be joined back into the feature matrix.
"""
import pandas as pd

from .. import config


def future_return(price: pd.Series, horizon: int = config.TARGET_HORIZON) -> pd.Series:
    """R(t -> t+h) = P(t+h)/P(t) - 1, aligned to time t (uses future data
    on purpose -- this is the target, not a feature)."""
    return price.shift(-horizon) / price - 1.0


def build_targets(price: pd.Series, horizon: int = config.TARGET_HORIZON) -> pd.DataFrame:
    r = future_return(price, horizon)
    out = pd.DataFrame(index=price.index)
    out["target_return"] = r                                   # Model A
    out["target_direction"] = (r > 0).astype(float)             # Model B
    out["target_threshold"] = (r > config.TARGET_THRESHOLD).astype(float)  # Model C
    # rows with NaN target (last `horizon` rows) must be dropped before training
    return out


def get_target(price: pd.Series, mode: str = config.TARGET_MODE, horizon: int = config.TARGET_HORIZON) -> pd.Series:
    targets = build_targets(price, horizon)
    col = {"regression": "target_return", "direction": "target_direction", "threshold": "target_threshold"}[mode]
    return targets[col]
