"""
Live trading loop: polling -> compute quantitative strategy signal -> size -> execute.
Runs pure rule-based strategies (e.g. Mean-Reversion / Reversal) without any ML models.
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import config
    import costs
    import sizing
    import strategy
    from data.collector import poll_once
    from data.store import load_all
    from execution.trader import place_order
except ImportError:
    from . import config, costs, sizing, strategy
    from .data.collector import poll_once
    from .data.store import load_all
    from .execution.trader import place_order

log = logging.getLogger("main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DRY_RUN = True  # Set to False when ready to place live orders on Roostoo
SELECTED_STRATEGY = "reversal"  # Options: 'reversal', 'buy_hold', etc.


def run_once():
    """Single execution step: fetch latest prices, compute strategy signals, execute trades."""
    poll_once()
    raw = load_all(config.UNIVERSE)

    if not raw:
        log.warning("No market data available yet.")
        return

    for pair in config.UNIVERSE:
        if pair not in raw or raw[pair].empty:
            continue

        df = raw[pair]
        min_bars_needed = max(config.REVERSAL_LOOKBACK_BARS, config.REVERSAL_HOLD_BARS)
        if len(df) < min_bars_needed:
            log.info("%s: accumulating price history (%d/%d bars)", pair, len(df), min_bars_needed)
            continue

        # Evaluate selected quantitative strategy
        strat_fn = strategy.STRATEGIES.get(SELECTED_STRATEGY, strategy._reversal)
        signals = strat_fn(pair, raw, None, df, {})

        if signals is None or signals.empty:
            continue

        latest_signal = signals.iloc[-1]
        if latest_signal <= 0:
            log.info("%s: strategy %s signal=%.4f (no action)", pair, SELECTED_STRATEGY, latest_signal)
            continue

        log.info("%s: STRATEGY SIGNAL TRIGGERED (%s) target_weight=%.4f", pair, SELECTED_STRATEGY, latest_signal)

        if not DRY_RUN:
            place_order(pair, "BUY", quantity=latest_signal)
        else:
            log.info("[DRY RUN] Would place BUY order for %s with size=%.4f", pair, latest_signal)


def run_forever(interval: int = config.POLL_INTERVAL_SECONDS):
    log.info("Starting live strategy loop (Strategy: %s, DRY_RUN=%s)", SELECTED_STRATEGY, DRY_RUN)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Error in main strategy loop")
        time.sleep(interval)


if __name__ == "__main__":
    run_forever()
