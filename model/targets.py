"""
Target construction. This is the ONE place future information is allowed
to enter the pipeline -- by definition, since it's what we predict. It
must never be joined back into the feature matrix.
"""
import pandas as pd

from .. import config


def future_return(price: pd.Series, horizon: int | None = None) -> pd.Series:
    """R(t -> t+h) = P(t+h)/P(t) - 1, aligned to time t (uses future data
    on purpose -- this is the target, not a feature)."""
    horizon = config.TARGET_HORIZON if horizon is None else horizon
    if not isinstance(horizon, int) or horizon <= 0:
        raise ValueError("horizon must be a positive integer")
    result = price.shift(-horizon) / price - 1.0
    result.attrs["available_at"] = {price.index[i]: price.index[i + horizon]
                                   for i in range(max(0, len(price) - horizon)) if pd.notna(result.iloc[i])}
    return result


def build_targets(price: pd.Series, horizon: int | None = None) -> pd.DataFrame:
    r = future_return(price, horizon)
    out = pd.DataFrame(index=price.index)
    out["target_return"] = r                                   # Model A
    out["target_direction"] = (r > 0).astype(float).where(r.notna())
    out["target_threshold"] = (r > config.TARGET_THRESHOLD).astype(float).where(r.notna())
    # rows with NaN target (last `horizon` rows) must be dropped before training
    return out


def get_target(price: pd.Series, mode: str | None = None, horizon: int | None = None) -> pd.Series:
    mode = config.TARGET_MODE if mode is None else mode
    targets = build_targets(price, horizon)
    col = {"regression": "target_return", "direction": "target_direction", "threshold": "target_threshold"}[mode]
    return targets[col]
