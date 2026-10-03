"""
Live loop: fetch -> feature -> predict -> cost filter -> size -> execute.

Run in DRY_RUN mode first (paper-trade): logs intended trades without
calling place_order, so you can compare live decisions against what the
backtest predicts for the same incoming data before risking anything.
"""
import logging
import time

from . import config, costs, sizing
from .data.collector import poll_once
from .data.store import load_all
from .execution.trader import place_order
from .features.build_features import build_features_for_pair, build_price_panel

log = logging.getLogger("main")
logging.basicConfig(level=logging.INFO)

DRY_RUN = True  # flip only after burn-in paper-trading period looks correct


def run_once(model=None):
    poll_once()
    raw = load_all(config.UNIVERSE)
    panel = build_price_panel(raw)

    for pair in config.UNIVERSE:
        df = raw[pair]
        if len(df) < max(config.MOMENTUM_HORIZONS + [config.MEAN_REV_WINDOW, config.REGIME_VOL_WINDOW]):
            log.info("%s: not enough history yet, skipping", pair)
            continue

        features = build_features_for_pair(pair, raw, panel)
        latest = features.iloc[[-1]].dropna(axis=1)
        if latest.empty or model is None:
            continue

        proba = model.predict_proba(latest)[:, 1][0]
        realized_vol = features["realized_vol"].iloc[-1] if "realized_vol" in features.columns else 0.0

        gross_edge_estimate = proba - 0.5
        if not costs.passes_cost_filter(gross_edge_estimate):
            log.info("%s: signal (p=%.3f) does not clear cost filter, skipping", pair, proba)
            continue

        size = sizing.confidence_to_size(proba, realized_vol)
        if size <= 0:
            continue

        side = "BUY" if proba >= 0.5 else "SELL"
        log.info("%s: %s size=%.4f proba=%.3f", pair, side, size, proba)

        if not DRY_RUN:
            place_order(pair, side, quantity=size)  # NOTE: size here is a capital fraction
            # -- convert to actual quantity using current balance & price before going live.


def run_forever(interval: int = config.POLL_INTERVAL_SECONDS, model=None):
    while True:
        try:
            run_once(model=model)
        except Exception:
            log.exception("error in main loop")
        time.sleep(interval)


if __name__ == "__main__":
    log.info("Starting in DRY_RUN=%s. Train a model first via model/train.py "
              "and pass it into run_forever(model=...) before enabling live trades.", DRY_RUN)
    run_forever()
