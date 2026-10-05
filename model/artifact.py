"""Stable import path for locally trusted serialized model artifacts."""
from dataclasses import dataclass
import numpy as np


@dataclass
class ModelBundle:
    estimator: object
    columns: tuple
    mode: str
    horizon: int
    trained_until: int
    mean_hit_return: float | None
    mean_miss_return: float | None
    feature_settings: dict
    pair: str | None = None

    def predict(self, frame):
        missing = set(self.columns) - set(frame.columns)
        if missing:
            raise ValueError(f"missing model features: {sorted(missing)}")
        X = frame.loc[:, list(self.columns)]
        if not np.isfinite(X.to_numpy(dtype=float)).all():
            raise ValueError("nonfinite model features; skip prediction, never drop columns")
        if self.mode == "regression":
            return None, np.asarray(self.estimator.predict(X), dtype=float)
        proba = self.estimator.predict_proba(X)
        idx = list(self.estimator.classes_).index(1.)
        p = proba[:, idx]
        expected = p * self.mean_hit_return + (1 - p) * self.mean_miss_return
        return p, expected
