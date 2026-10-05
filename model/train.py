"""Purged chronological training, fixed schemas and train-only payoffs."""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from .. import config, costs
from .targets import future_return, get_target
from .artifact import ModelBundle
from ..features.build_features import model_inputs

MODEL_REGISTRY = {
    "logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, random_state=config.RANDOM_SEED)),
    "random_forest": lambda: RandomForestClassifier(n_estimators=200, max_depth=5, random_state=config.RANDOM_SEED),
    "gradient_boosting": lambda: GradientBoostingClassifier(random_state=config.RANDOM_SEED),
}
REGRESSORS = {
    "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.)),
    "random_forest": lambda: RandomForestRegressor(n_estimators=200, max_depth=5, random_state=config.RANDOM_SEED),
}


def feature_config():
    names = ("MOMENTUM_HORIZONS", "MA_WINDOWS", "MEAN_REV_WINDOW", "BOLLINGER_WINDOW", "BOLLINGER_STD",
             "REGIME_VOL_WINDOW", "REGIME_TREND_WINDOW", "CROSS_SECTIONAL_HORIZON", "TARGET_HORIZON", "TARGET_MODE", "TARGET_THRESHOLD", "POLL_INTERVAL_SECONDS", "MARKET_PROXY", "UNIVERSE", "UNIVERSE_GROUPS")
    values = {n: getattr(config, n) for n in names}
    values["feature_components"] = {k: config.COMPONENTS[k] for k in ("momentum", "mean_reversion", "cross_sectional", "regime")}
    root = Path(__file__).resolve().parents[1]
    sources = [root / "features" / name for name in ("build_features.py", "momentum.py", "mean_reversion.py", "cross_sectional.py", "regime.py")]
    sources += [root / "data" / "validation.py", root / "model" / "targets.py"]
    values["feature_source_sha256"] = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    return values




def walk_forward_splits(n_rows, train_bars=None, val_bars=None, step_bars=None, horizon=None):
    train_bars = config.WALKFORWARD_TRAIN_BARS if train_bars is None else train_bars
    val_bars = config.WALKFORWARD_VAL_BARS if val_bars is None else val_bars
    step_bars = config.WALKFORWARD_STEP_BARS if step_bars is None else step_bars
    horizon = config.TARGET_HORIZON if horizon is None else horizon
    if min(train_bars, val_bars, step_bars) <= 0 or horizon <= 0 or step_bars < val_bars:
        raise ValueError("positive windows/horizon and nonoverlapping validation blocks required")
    start = 0
    while start + train_bars + horizon + val_bars <= n_rows:
        train_idx = np.arange(start, start + train_bars)
        val_idx = np.arange(start + train_bars + horizon, start + train_bars + horizon + val_bars)
        yield train_idx, val_idx
        start += step_bars


def fit_bundle(X, y, returns, model_name="logistic", mode=None, horizon=None, pair=None):
    mode = config.TARGET_MODE if mode is None else mode
    horizon = config.TARGET_HORIZON if horizon is None else horizon
    if not X.index.equals(y.index) or not X.index.equals(returns.index):
        raise ValueError("features, labels and realized future returns must align exactly")
    if not X.index.is_unique or not X.index.is_monotonic_increasing or X.shape[1] == 0:
        raise ValueError("unique chronological data and explicit feature columns required")
    if not np.isfinite(X.to_numpy(dtype=float)).all() or not np.isfinite(y).all() or not np.isfinite(returns).all():
        raise ValueError("training data must contain only known, finite values")
    hit = miss = None
    if mode == "regression":
        if model_name not in REGRESSORS:
            raise ValueError("regression requires ridge or random_forest")
        model = REGRESSORS[model_name]()
    else:
        if set(y.unique()) != {0., 1.}:
            raise ValueError("both target classes required")
        if model_name not in MODEL_REGISTRY:
            raise ValueError("unknown classifier")
        calibration_size = max(20, len(X) // 5)
        fit_stop = len(X) - calibration_size - horizon
        cal_start = len(X) - calibration_size
        if fit_stop < 20 or y.iloc[:fit_stop].nunique() != 2 or y.iloc[cal_start:].nunique() != 2:
            raise ValueError("insufficient two-class data for purged temporal probability calibration")
        model = CalibratedClassifierCV(MODEL_REGISTRY[model_name](), method="sigmoid",
                                      cv=[(np.arange(fit_stop), np.arange(cal_start, len(X)))])
        hit, miss = float(returns[y == 1].mean()), float(returns[y == 0].mean())
    availability = returns.attrs.get("available_at")
    if availability is None or any(i not in availability for i in X.index):
        raise ValueError("known label availability timestamps required")
    if mode != "regression" and max(availability[i] for i in X.index[:fit_stop]) >= X.index[cal_start]:
        raise ValueError("training labels cross probability calibration boundary")
    model.fit(X, y)
    known_at = max(availability[i] for i in X.index)
    trained_until = int(known_at.value // 1_000_000) if isinstance(known_at, pd.Timestamp) else int(known_at)
    return ModelBundle(model, tuple(X.columns), mode, horizon, trained_until, hit, miss, feature_config(), pair)


def training_frame(features, price, mode=None, horizon=None):
    """Select derived model columns only; optional raw fields never erase rows."""
    X = model_inputs(features).replace([np.inf, -np.inf], np.nan)
    label = get_target(price, mode=mode, horizon=horizon)
    returns = future_return(price, horizon=horizon)
    joined = X.join(label.rename("__y")).join(returns.rename("__return")).dropna()
    known_returns = joined["__return"].copy()
    known_returns.attrs["available_at"] = {i: returns.attrs["available_at"][i] for i in joined.index}
    return joined.drop(columns=["__y", "__return"]), joined["__y"], known_returns


def train_walk_forward(X, y, model_name="logistic", returns=None, mode=None, **split_kwargs):
    if not X.index.equals(y.index) or not X.index.is_unique or not X.index.is_monotonic_increasing:
        raise ValueError("chronological aligned X/y required")
    mode = config.TARGET_MODE if mode is None else mode
    if model_name not in (REGRESSORS if mode == "regression" else MODEL_REGISTRY):
        raise ValueError("unknown model for target mode")
    if mode != "regression" and not set(y.unique()).issubset({0., 1.}):
        raise ValueError("classification labels must be known binary values")
    if returns is None:
        # Classification-only research may omit payoff data. It must never be
        # used by the trading path; bundles for that path require real returns.
        raise ValueError("future returns required for training-only payoff estimation")
    if not X.index.equals(returns.index):
        raise ValueError("returns index differs from training index")
    if not np.isfinite(X.to_numpy(dtype=float)).all() or not np.isfinite(y).all() or not np.isfinite(returns).all():
        raise ValueError("training data must contain only known, finite values")
    results = []
    for fold, (tr, va) in enumerate(walk_forward_splits(len(X), **split_kwargs)):
        available_at = returns.attrs.get("available_at", {})
        if any(i not in available_at for i in X.index[tr]):
            raise ValueError("known label availability timestamps required")
        if max(available_at[i] for i in X.index[tr]) >= X.index[va[0]]:
            raise ValueError("training label availability crosses validation boundary")
        if mode != "regression":
            labels = y.iloc[tr]
            horizon = split_kwargs.get("horizon", config.TARGET_HORIZON)
            calibration_size = max(20, len(tr) // 5)
            fit_stop = len(tr) - calibration_size - horizon
            cal_start = len(tr) - calibration_size
            if set(labels.unique()) != {0., 1.} or fit_stop < 20 or labels.iloc[:fit_stop].nunique() != 2 or labels.iloc[cal_start:].nunique() != 2:
                results.append({"fold": fold, "model": model_name, "train_index": X.index[tr],
                                "val_index": X.index[va], "fitted_model": None,
                                "status": "skipped", "reason": "insufficient two-class training/calibration data"})
                continue
        bundle = fit_bundle(X.iloc[tr], y.iloc[tr], returns.iloc[tr], model_name, mode,
                            split_kwargs.get("horizon"))
        p, expected = bundle.predict(X.iloc[va])
        results.append({"fold": fold, "model": model_name, "status": "fitted", "train_index": X.index[tr],
                        "val_index": X.index[va], "val_proba": p, "val_expected_return": expected,
                        "val_true": y.iloc[va].to_numpy(), "fitted_model": bundle,
                        "train_score": bundle.estimator.score(X.iloc[tr], y.iloc[tr]),
                        "val_score": bundle.estimator.score(X.iloc[va], y.iloc[va])})
    return results


def save_bundle(bundle, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".json").write_text(json.dumps({"sha256": digest, "pair": bundle.pair, "runtime": runtime_versions(),
        "schema": list(bundle.columns), "trained_until": bundle.trained_until, "feature_settings": bundle.feature_settings}, indent=2) + "\n")


def load_bundle(path):
    """Only load locally generated/trusted joblib artifacts (pickle format)."""
    path = Path(path)
    manifest = json.loads(path.with_suffix(path.suffix + ".json").read_text())
    if manifest.get("runtime") != runtime_versions():
        raise ValueError("model runtime mismatch; train with the deployment runtime or install matching dependencies")
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("model artifact checksum mismatch")
    bundle = joblib.load(path)
    if not isinstance(bundle, ModelBundle) or bundle.feature_settings != feature_config():
        raise ValueError("model/feature configuration mismatch; retraining required")
    return bundle


def runtime_versions():
    return {"python": ".".join(platform.python_version_tuple()[:2]), "numpy": np.__version__,
            "pandas": pd.__version__, "scikit-learn": sklearn.__version__, "joblib": joblib.__version__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--train-end-ms", type=int, required=True, help="Last training observation, strictly before validation/test")
    parser.add_argument("--model", default="logistic")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from ..data.store import load_all
    from ..features.build_features import build_price_panel, build_features_for_pair
    raw = {k: v[v.timestamp <= args.train_end_ms] for k, v in load_all(config.UNIVERSE).items()}
    if args.pair not in raw or raw[args.pair].empty:
        parser.error("no stored training data for pair")
    if (raw[args.pair].timestamp.diff().dropna() > config.MAX_HISTORY_GAP_MS).any():
        parser.error("training history contains gaps; supply a cleaned contiguous research window")
    frame = build_features_for_pair(args.pair, raw, build_price_panel(raw))
    price = raw[args.pair].set_index("timestamp").last_price
    X, y, returns = training_frame(frame, price)
    bundle = fit_bundle(X, y, returns, args.model, pair=args.pair)
    save_bundle(bundle, args.output)
    print(f"Saved {args.pair}: {len(X)} known-label training rows, {len(X.columns)} features. No validation/test score implied.")


if __name__ == "__main__":
    main()
